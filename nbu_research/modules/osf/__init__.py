"""OSF connector: push a project's preregistration package to OSF storage.

Each researcher connects their own OSF personal access token at
/settings/connections (credentials.get_credential("osf")). Without a token
the page still offers the preregistration .zip as a plain download via the
exports module; with a token, a background job uploads the package to an OSF
project through the osfstorage Waterbutler API.

Job payloads carry the token (resolved from the request user BEFORE
enqueueing — jobs have no request context). Payloads are visible in the jobs
table, which is inside the same trust boundary; never log the token.
"""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from flask import Blueprint, redirect, render_template, request, url_for

from ... import db
from ...credentials import get_credential
from ...jobs import get_job, job as job_task, start_job, update_progress
from ..exports import EXPORTERS  # sanctioned: modules talk via the registry

bp = Blueprint("osf", __name__)

WATERBUTLER_URL = "https://files.osf.io/v1/resources/{osf_id}/providers/osfstorage/"

# Python's default SSL store is often empty on macOS/slim containers; pin
# certifi's CA bundle (same approach as the EDGAR client).
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - certifi should always be present
    _SSL_CONTEXT = ssl.create_default_context()


# --- upload job ----------------------------------------------------------------

def _upload(osf_id, name, token, data):
    """PUT the zip to OSF storage; return the Waterbutler file metadata dict.

    Raises ValueError with a user-readable message on auth/notfound errors.
    """
    url = (WATERBUTLER_URL.format(osf_id=osf_id)
           + "?" + urllib.parse.urlencode({"kind": "file", "name": name}))
    req = urllib.request.Request(url, data=data, method="PUT", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/zip",
    })
    try:
        with urllib.request.urlopen(req, timeout=120,
                                    context=_SSL_CONTEXT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError(
                "OSF rejected the access token (HTTP "
                f"{e.code}). Reconnect your OSF account under Settings → "
                "Connections and check the token has write scope.")
        if e.code == 404:
            raise ValueError(
                f"OSF project '{osf_id}' was not found (HTTP 404). Check the "
                "5-character project id (the part after osf.io/) and that "
                "your account can access it.")
        raise ValueError(f"OSF upload failed with HTTP {e.code}.")
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach OSF: {e.reason}")
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


@job_task("osf_push")
def _run_push_job(job_id, project_id=None, osf_id="", token=""):
    update_progress(job_id, 0.1, "Building preregistration package…")
    data, _, _ = EXPORTERS["project_prereg"]["fn"](project_id)
    name = f"prereg-{date.today().isoformat()}.zip"
    update_progress(job_id, 0.4, f"Uploading {name} to OSF project {osf_id}…")
    meta = _upload(osf_id, name, token, data)
    attributes = (meta.get("data") or {}).get("attributes") or {}
    file_name = attributes.get("name") or name
    update_progress(job_id, 1.0, "Uploaded.")
    return {
        "file_name": file_name,
        "osf_id": osf_id,
        "osf_link": f"https://osf.io/{osf_id}/files/",
    }


# --- routes ----------------------------------------------------------------------

def _index_context(**extra):
    ctx = {
        "projects": db.query("projects"),
        "connected": get_credential("osf") is not None,
        "project_id": request.args.get("project", ""),
    }
    ctx.update(extra)
    return ctx


@bp.route("/")
def index():
    return render_template("osf/index.html", **_index_context())


@bp.route("/push", methods=["POST"])
def push():
    cred = get_credential("osf")
    if not cred or not cred.get("token"):
        return render_template("osf/index.html", **_index_context(
            error="Connect your OSF account first (Settings → Connections).",
            connected=False)), 400
    project_id = request.form.get("project_id", "").strip()
    project = db.get("projects", project_id) if project_id else None
    if not project:
        return render_template("osf/index.html", **_index_context(
            error="Pick a project to push.")), 400
    # Accept a bare guid ("ab12c") or a pasted URL ("https://osf.io/ab12c/").
    osf_id = request.form.get("osf_id", "").strip().strip("/").split("/")[-1]
    if not osf_id:
        return render_template("osf/index.html", **_index_context(
            error="Enter the OSF project id (e.g. ab12c).")), 400
    job_id = start_job(
        "osf_push",
        {"project_id": project_id, "osf_id": osf_id, "token": cred["token"]},
        ref_table="projects", ref_id=project_id,
    )
    return redirect(url_for("osf.job_progress", job_id=job_id))


@bp.route("/job/<job_id>")
def job_progress(job_id):
    job = get_job(job_id)
    if not job:
        return "Job not found", 404
    return render_template("osf/job.html", job=job,
                           title="Pushing preregistration to OSF")
