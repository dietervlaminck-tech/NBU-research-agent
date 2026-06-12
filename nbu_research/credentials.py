"""Per-user service credentials (Zotero, Qualtrics, OSF, Mendeley).

Each researcher connects their OWN accounts after Entra login — credentials
are keyed by the Entra user id (or the synthetic "dev" user in dev mode) and
never shared between users. Connectors read them via get_credential() and must
degrade to a "not connected" UI state when it returns None.

Storage note: payloads live in the SQLite database alongside the research
data. The database file is the trust boundary (same as study data); at-rest
encryption is an infrastructure concern (Azure disk encryption). Documented
in docs/ROADMAP.md hardening notes.
"""
from . import db
from .auth import current_user

SERVICES = {
    "zotero": {
        "label": "Zotero",
        "fields": [
            ("api_key", "API key (zotero.org → Settings → Security → Create key)"),
            ("user_id", "Zotero user ID (numeric, shown on the same page)"),
        ],
        "help": "Enables pushing review sources to your Zotero library and "
                "importing Zotero collections as sources.",
    },
    "qualtrics": {
        "label": "Qualtrics",
        "fields": [
            ("api_token", "API token (Qualtrics → Account settings → Qualtrics IDs)"),
            ("datacenter", "Datacenter ID (e.g. fra1, eu — also on the Qualtrics IDs page)"),
        ],
        "help": "Enables pushing surveys to Qualtrics and pulling responses "
                "back into the platform.",
    },
    "osf": {
        "label": "OSF (Open Science Framework)",
        "fields": [
            ("token", "Personal access token (osf.io → Settings → Personal access tokens)"),
        ],
        "help": "Enables pushing preregistration packages to an OSF project.",
    },
    "mendeley": {
        "label": "Mendeley",
        "fields": [],
        "help": "Mendeley's API requires an OAuth application approved by "
                "Elsevier; new registrations are restricted. This connector "
                "activates once the institution obtains an app registration "
                "(client id/secret) — ask IT to request one if needed.",
        "dormant": True,
    },
}


def get_credential(service, user_id=None):
    """The payload dict for this user's `service` connection, or None."""
    if user_id is None:
        user = current_user()
        if not user:
            return None
        user_id = user["user_id"]
    rows = db.query("user_credentials", "user_id = ? AND service = ?",
                    (user_id, service), order="updated_at DESC")
    return rows[0]["payload"] if rows else None


def set_credential(service, payload, user_id=None):
    """Create or replace this user's `service` connection."""
    if user_id is None:
        user_id = current_user()["user_id"]
    existing = db.query("user_credentials", "user_id = ? AND service = ?",
                        (user_id, service), order="")
    if existing:
        db.update("user_credentials", existing[0]["id"], {"payload": payload})
        return existing[0]["id"]
    return db.insert("user_credentials", {
        "user_id": user_id, "service": service, "payload": payload,
    })


def delete_credential(service, user_id=None):
    if user_id is None:
        user_id = current_user()["user_id"]
    for row in db.query("user_credentials", "user_id = ? AND service = ?",
                        (user_id, service), order=""):
        db.delete("user_credentials", row["id"])


def connection_status(user_id=None):
    """{service: bool} — which services this user has connected."""
    if user_id is None:
        user = current_user()
        user_id = user["user_id"] if user else "?"
    connected = {r["service"] for r in db.query(
        "user_credentials", "user_id = ?", (user_id,), order="")}
    return {key: key in connected for key in SERVICES}
