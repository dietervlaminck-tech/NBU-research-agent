"""Microsoft Entra ID (Azure AD) authentication.

OAuth2 authorization-code flow via msal. Entra is the sole researcher login;
respondent-facing routes (interview chat, survey runner) stay public because
respondents are external and must never need an institutional account.

Dev mode: when AZURE_CLIENT_ID is unset, authentication is DISABLED — every
request runs as a synthetic "dev" user so local development and the test suite
work without an Azure app registration. The base template shows a visible
"auth disabled" hint so this state is never mistaken for production.
"""
from functools import wraps

from flask import (
    Blueprint, abort, current_app, redirect, request, session, url_for,
)

from . import config, db
from .config import auth_configured

bp = Blueprint("auth", __name__)

# Endpoints reachable without login. Respondents land on these from share
# links; everything else on the platform is researcher-facing.
PUBLIC_ENDPOINTS = {
    "auth.login", "auth.callback", "auth.logout",
    "interviews.run_page", "interviews.api_create_session",
    "interviews.api_first_message", "interviews.api_send_message",
    "surveys.run", "surveys.api_respond",
    "static",
}

DEV_USER = {
    "user_id": "dev",
    "display_name": "Dev user (auth disabled)",
    "email": "dev@localhost",
    "roles": ["researcher"],
}

_SCOPES = []  # empty -> openid/profile only; we need identity, not Graph data.


def _msal_app():
    import msal
    return msal.ConfidentialClientApplication(
        config.AZURE_CLIENT_ID,
        client_credential=config.AZURE_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{config.AZURE_TENANT_ID}",
    )


def current_user():
    """The logged-in user dict, the dev user when auth is disabled, or None."""
    if not auth_configured():
        return DEV_USER
    return session.get("user")


def login_required(fn):
    """Route decorator: require an authenticated researcher."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def init_app(app):
    """Register the auth blueprint and a global login guard.

    The guard enforces login on every endpoint not in PUBLIC_ENDPOINTS, which
    protects all existing blueprints without decorating each route by hand
    (login_required stays available for explicit use).
    """
    app.register_blueprint(bp, url_prefix="/auth")

    @app.before_request
    def _require_login():
        if not auth_configured():
            return None  # dev mode
        endpoint = request.endpoint or ""
        if endpoint in PUBLIC_ENDPOINTS:
            return None
        if session.get("user"):
            return None
        if request.method in ("POST", "PUT", "DELETE", "PATCH") or \
                request.path.startswith("/api/"):
            abort(401)
        return redirect(url_for("auth.login", next=request.full_path))

    @app.context_processor
    def _inject_user():
        return {"auth_user": current_user(), "auth_enabled": auth_configured()}


@bp.route("/login")
def login():
    if not auth_configured():
        return redirect(url_for("projects.index"))
    flow = _msal_app().initiate_auth_code_flow(
        _SCOPES, redirect_uri=config.AZURE_REDIRECT_URI)
    session["auth_flow"] = flow
    session["auth_next"] = request.args.get("next", "/")
    return redirect(flow["auth_uri"])


@bp.route("/callback")
def callback():
    flow = session.pop("auth_flow", None)
    if not flow:
        return redirect(url_for("auth.login"))
    try:
        result = _msal_app().acquire_token_by_auth_code_flow(
            flow, request.args.to_dict())
    except ValueError:
        return "Authentication failed (state mismatch). Try again.", 400
    if "error" in result:
        return f"Authentication failed: {result.get('error_description', result['error'])}", 401

    claims = result.get("id_token_claims") or {}
    if claims.get("tid") != config.AZURE_TENANT_ID:
        current_app.logger.warning("Login rejected: wrong tenant %s", claims.get("tid"))
        return "This account belongs to a different organization.", 403

    user = {
        "user_id": claims.get("oid"),
        "display_name": claims.get("name") or claims.get("preferred_username", ""),
        "email": (claims.get("preferred_username") or claims.get("email") or "").lower(),
        "roles": claims.get("roles") or ["researcher"],
    }
    if not user["user_id"]:
        return "Authentication failed: no object id in token.", 401
    session["user"] = user
    _upsert_user(user)
    next_url = session.pop("auth_next", "/") or "/"
    return redirect(next_url if next_url.startswith("/") else "/")


@bp.route("/logout")
def logout():
    session.clear()
    if not auth_configured():
        return redirect("/")
    return redirect(
        f"https://login.microsoftonline.com/{config.AZURE_TENANT_ID}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri={request.url_root.rstrip('/')}"
    )


# --- per-project roles (viewer < collaborator < owner) -------------------------

ROLE_ORDER = {"viewer": 1, "collaborator": 2, "owner": 3}


def project_role(project_id, user=None):
    """The user's role on a project, or None.

    Dev mode (auth disabled) → 'owner' everywhere. Projects created before the
    roles model (no member rows at all) are grandfathered: any authenticated
    researcher acts as owner, so existing data never locks itself away.
    """
    user = user or current_user()
    if user is None:
        return None
    if not auth_configured():
        return "owner"
    members = db.query("project_members", "project_id = ?", (project_id,), order="")
    if not members:
        return "owner"  # legacy project, no membership rows yet
    for m in members:
        if m["user_id"] == user["user_id"]:
            return m["role"]
    return None


def check_project_role(project_id, min_role):
    """Abort 403 unless the current user has at least min_role on the project.
    A None/empty project_id (standalone studies/datasets) only requires login,
    which the global guard already enforces."""
    if not project_id:
        return
    role = project_role(project_id)
    if role is None or ROLE_ORDER[role] < ROLE_ORDER[min_role]:
        abort(403)


def require_project_role(min_role):
    """Decorator for routes with a `project_id` path argument."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            check_project_role(kwargs.get("project_id"), min_role)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def _upsert_user(user):
    """First-login upsert into users; convert pending email invites into
    memberships (project_members) — see the roles model in db.py."""
    existing = db.get("users", user["user_id"])
    if existing:
        db.update("users", user["user_id"], {
            "display_name": user["display_name"], "email": user["email"],
        })
    else:
        db.insert("users", {
            "id": user["user_id"],
            "display_name": user["display_name"],
            "email": user["email"],
        })
    if user["email"]:
        for inv in db.query("project_invites", "email = ?", (user["email"],),
                            order="invited_at ASC"):
            already = db.query(
                "project_members", "project_id = ? AND user_id = ?",
                (inv["project_id"], user["user_id"]), order="")
            if not already:
                db.insert("project_members", {
                    "project_id": inv["project_id"],
                    "user_id": user["user_id"],
                    "role": inv["role"],
                    "added_at": db.now(),
                })
            db.delete("project_invites", inv["id"])
