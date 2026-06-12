"""Per-user settings: external service connections.

Each researcher manages their own Zotero / Qualtrics / OSF connections here,
after Entra login. Connectors elsewhere read these via credentials.get_credential
and show a "not connected" state pointing at this page.
"""
from flask import Blueprint, redirect, render_template, request, url_for

from .. import credentials

bp = Blueprint("settings", __name__)


@bp.route("/")
def index():
    return redirect(url_for("settings.connections"))


@bp.route("/connections")
def connections():
    status = credentials.connection_status()
    return render_template(
        "settings/connections.html",
        services=credentials.SERVICES, status=status,
        saved=request.args.get("saved", ""),
    )


@bp.route("/connections/<service>", methods=["POST"])
def save_connection(service):
    spec = credentials.SERVICES.get(service)
    if not spec or spec.get("dormant"):
        return "Unknown or unavailable service", 404
    if request.form.get("action") == "disconnect":
        credentials.delete_credential(service)
        return redirect(url_for("settings.connections"))
    payload = {}
    for field, _label in spec["fields"]:
        value = request.form.get(field, "").strip()
        if not value:
            status = credentials.connection_status()
            return render_template(
                "settings/connections.html",
                services=credentials.SERVICES, status=status,
                error=f"{spec['label']}: all fields are required.", saved="",
            ), 400
        payload[field] = value
    credentials.set_credential(service, payload)
    return redirect(url_for("settings.connections", saved=service))
