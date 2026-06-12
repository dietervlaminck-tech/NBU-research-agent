"""Thin Qualtrics REST API v3 client (per-user credentials, v0.3).

Two operations only:

- push_survey(study, token, datacenter): import a survey study into the
  researcher's Qualtrics account via POST /surveys (multipart upload of the
  QSF produced by the exports module's builder).
- pull_responses(qualtrics_survey_id, token, datacenter): the documented
  3-step response export — start the CSV export, poll until complete, download
  the ZIP and extract the single CSV.

Credentials are the researcher's own (credentials.get_credential("qualtrics")
→ {"api_token", "datacenter"}); never platform-wide keys. All HTTP errors are
converted to plain ValueErrors the routes/jobs can surface to users.

API docs: https://api.qualtrics.com/ (Survey Definitions, Response Exports).
"""
import io
import json
import ssl
import time
import urllib.error
import urllib.request
import uuid
import zipfile

# Sanctioned cross-module import: surveys reuses the exports module's QSF
# builder so the survey pushed to Qualtrics is byte-identical to the .qsf a
# researcher would download from /exports (one-way import, see task brief).
from ..exports.qsf import study_qsf

# Python's default SSL store is often empty on macOS/slim containers (same
# issue as modules/edgar/client.py). Pin certifi's CA bundle.
try:
    import certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover - certifi should always be present
    _SSL_CONTEXT = ssl.create_default_context()

# Response-export polling: Qualtrics exports are usually ready in seconds for
# survey-sized data; cap the wait so a stuck export fails loudly, not forever.
POLL_INTERVAL_SECONDS = 1.0
POLL_MAX_ATTEMPTS = 120

TIMEOUT_SECONDS = 60


def _base_url(datacenter):
    dc = str(datacenter or "").strip().strip("/")
    if not dc:
        raise ValueError("Qualtrics datacenter is missing — reconnect your "
                         "account at /settings/connections.")
    return f"https://{dc}.qualtrics.com/API/v3"


def _error_from_http(e):
    """Map an HTTPError to a clear ValueError (never leak the token)."""
    if e.code == 401:
        return ValueError(
            "Qualtrics rejected the token (401 Unauthorized). Check your API "
            "token and datacenter at /settings/connections.")
    detail = ""
    try:
        body = json.loads(e.read())
        detail = (body.get("meta", {}).get("error", {})
                  .get("errorMessage", "")) or ""
    except Exception:
        pass
    if e.code == 404:
        return ValueError("Qualtrics returned 404 (not found) — the survey "
                          "may have been deleted, or the datacenter is wrong."
                          + (f" Detail: {detail}" if detail else ""))
    return ValueError(f"Qualtrics request failed ({e.code})."
                      + (f" Detail: {detail}" if detail else ""))


def _http(method, url, token, body=None, content_type=None):
    """One Qualtrics API request; returns raw response bytes.

    Raises ValueError on HTTP/auth/network errors with a user-facing message.
    """
    headers = {"X-API-TOKEN": token}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS,
                                    context=_SSL_CONTEXT) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise _error_from_http(e)
    except urllib.error.URLError as e:
        raise ValueError(f"Could not reach Qualtrics ({e.reason}). Check the "
                         "datacenter ID and network access.")


def _http_json(method, url, token, payload=None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    raw = _http(method, url, token, body=body,
                content_type="application/json" if body else None)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError("Qualtrics returned non-JSON content for " + url)


def _multipart(fields, file_field, filename, file_bytes, file_content_type):
    """Encode a multipart/form-data body; returns (body_bytes, content_type)."""
    boundary = "----nbu-" + uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in (fields or {}).items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        out.write(str(value).encode("utf-8") + b"\r\n")
    out.write(f"--{boundary}\r\n".encode())
    out.write((f'Content-Disposition: form-data; name="{file_field}"; '
               f'filename="{filename}"\r\n').encode())
    out.write(f"Content-Type: {file_content_type}\r\n\r\n".encode())
    out.write(file_bytes + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def push_survey(study, token, datacenter):
    """Import `study` (a studies row) as a new Qualtrics survey.

    Uploads the QSF produced by exports.qsf.study_qsf via POST /surveys and
    returns the new Qualtrics surveyId (e.g. "SV_abc123")."""
    qsf_bytes, filename, _mimetype = study_qsf(study["id"])
    body, content_type = _multipart(
        fields={"name": study.get("title") or "Survey"},
        file_field="file", filename=filename, file_bytes=qsf_bytes,
        file_content_type="application/vnd.qualtrics.survey.qsf",
    )
    raw = _http("POST", _base_url(datacenter) + "/surveys", token,
                body=body, content_type=content_type)
    try:
        result = json.loads(raw).get("result") or {}
    except (ValueError, TypeError):
        raise ValueError("Qualtrics returned an unreadable response to the "
                         "survey import.")
    survey_id = result.get("id")
    if not survey_id:
        raise ValueError("Qualtrics accepted the import but returned no "
                         "survey id.")
    return survey_id


def pull_responses(qualtrics_survey_id, token, datacenter):
    """Export a survey's responses as CSV text (Qualtrics 3-step export).

    1. POST /surveys/{id}/export-responses {"format": "csv"} → progressId
    2. GET  /surveys/{id}/export-responses/{progressId} until status complete
    3. GET  /surveys/{id}/export-responses/{fileId}/file → ZIP with one CSV
    """
    base = f"{_base_url(datacenter)}/surveys/{qualtrics_survey_id}/export-responses"
    start = _http_json("POST", base, token, payload={"format": "csv"})
    progress_id = (start.get("result") or {}).get("progressId")
    if not progress_id:
        raise ValueError("Qualtrics did not start the response export "
                         "(no progressId returned).")

    file_id = None
    for _ in range(POLL_MAX_ATTEMPTS):
        check = _http_json("GET", f"{base}/{progress_id}", token)
        result = check.get("result") or {}
        status = (result.get("status") or "").lower()
        if status == "complete":
            file_id = result.get("fileId")
            break
        if status == "failed":
            raise ValueError("Qualtrics reported the response export failed. "
                             "Try again, or export manually from Qualtrics.")
        time.sleep(POLL_INTERVAL_SECONDS)
    if not file_id:
        raise ValueError("Timed out waiting for the Qualtrics response "
                         "export to complete.")

    zip_bytes = _http("GET", f"{base}/{file_id}/file", token)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise KeyError("no csv")
            return zf.read(names[0]).decode("utf-8-sig", errors="replace")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("Qualtrics returned an export file with no readable "
                         "CSV inside.")
