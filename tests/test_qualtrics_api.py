"""Qualtrics connector (v0.3): push survey, pull responses, dormant state.

Points NBU_DATA_DIR at a temp dir BEFORE importing nbu_research; auth env vars
are stripped so the app runs in dev mode (synthetic "dev" user, no login
guard). All HTTP is mocked — no network, no API key needed.
"""
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="nbu_test_qualtrics_")
os.environ["NBU_DATA_DIR"] = _TMP
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("CELERY_BROKER_URL", None)  # force in-process thread jobs
for _var in ("AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID",
             "AZURE_REDIRECT_URI"):
    os.environ.pop(_var, None)

from nbu_research import create_app, db, jobs  # noqa: E402
from nbu_research.credentials import delete_credential, set_credential  # noqa: E402
from nbu_research.modules.surveys import qualtrics_api  # noqa: E402

db.init_db()

APP = create_app()
APP.config["TESTING"] = True

CRED = {"api_token": "tok-secret-123", "datacenter": "fra1"}

# A minimal Qualtrics legacy export: 3 header rows (ids / question text /
# ImportId JSON), one metadata column, two data rows. Fed through the REAL
# datasets/qualtrics.py parser in the pull-job test.
QUALTRICS_CSV = (
    'Q1,Q2,ResponseId\n'
    '"How satisfied are you?","What is your age?","Response ID"\n'
    '"{""ImportId"":""QID1""}","{""ImportId"":""QID2""}","{""ImportId"":""_recordId""}"\n'
    '4,29,R_001\n'
    '5,31,R_002\n'
)


def _make_survey(title="Engagement survey"):
    project_id = db.insert("projects", {"title": "P", "description": ""})
    study_id = db.insert("studies", {
        "project_id": project_id,
        "study_type": "survey",
        "title": title,
        "research_question": "RQ",
        "config": {
            "questions": [
                {"id": "q1", "type": "likert", "text": "How satisfied are you?",
                 "required": True,
                 "scale": {"min": 1, "max": 5, "min_label": "Low", "max_label": "High"}},
                {"id": "q2", "type": "numeric", "text": "What is your age?",
                 "required": False},
            ],
            "welcome_text": "", "thankyou_text": "",
        },
    })
    return project_id, study_id


def _wait_for_job(job_id, timeout=10):
    for _ in range(int(timeout * 20)):
        row = jobs.get_job(job_id)
        if row and row["status"] in ("done", "error"):
            return row
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


# --- push ---------------------------------------------------------------------

def test_push_stores_survey_id_in_config(monkeypatch):
    set_credential("qualtrics", CRED, user_id="dev")
    _, study_id = _make_survey()

    seen = {}

    def fake_push(study, token, datacenter):
        seen.update(study_id=study["id"], token=token, datacenter=datacenter)
        return "SV_fake123"

    monkeypatch.setattr(qualtrics_api, "push_survey", fake_push)

    client = APP.test_client()
    resp = client.post(f"/surveys/{study_id}/qualtrics/push")
    assert resp.status_code == 302
    assert f"/surveys/dashboard/{study_id}" in resp.headers["Location"]
    assert "qualtrics_notice=pushed" in resp.headers["Location"]

    assert seen == {"study_id": study_id, "token": "tok-secret-123",
                    "datacenter": "fra1"}
    qx = (db.get("studies", study_id)["config"] or {}).get("qualtrics") or {}
    assert qx["qualtrics_survey_id"] == "SV_fake123"
    assert qx["pushed_at"]

    # The dashboard renders the pushed state without crashing.
    page = client.get(f"/surveys/dashboard/{study_id}")
    assert page.status_code == 200
    assert b"SV_fake123" in page.data


def test_push_api_error_surfaces_as_notice_not_crash(monkeypatch):
    set_credential("qualtrics", CRED, user_id="dev")
    _, study_id = _make_survey()

    def boom(study, token, datacenter):
        raise ValueError("Qualtrics rejected the token (401 Unauthorized). "
                         "Check your API token and datacenter at "
                         "/settings/connections.")

    monkeypatch.setattr(qualtrics_api, "push_survey", boom)
    client = APP.test_client()
    resp = client.post(f"/surveys/{study_id}/qualtrics/push")
    assert resp.status_code == 302
    assert "qualtrics_error=" in resp.headers["Location"]
    # No qualtrics config stored on failure.
    assert "qualtrics" not in (db.get("studies", study_id)["config"] or {})


# --- pull (background job through the real parser) -----------------------------

def test_pull_job_creates_qualtrics_dataset(monkeypatch):
    set_credential("qualtrics", CRED, user_id="dev")
    project_id, study_id = _make_survey(title="Pull me")
    study = db.get("studies", study_id)
    study["config"]["qualtrics"] = {"qualtrics_survey_id": "SV_remote9",
                                    "pushed_at": db.now()}
    db.update("studies", study_id, {"config": study["config"]})

    def fake_pull(qualtrics_survey_id, token, datacenter):
        assert qualtrics_survey_id == "SV_remote9"
        assert token == "tok-secret-123" and datacenter == "fra1"
        return QUALTRICS_CSV

    monkeypatch.setattr(qualtrics_api, "pull_responses", fake_pull)

    client = APP.test_client()
    resp = client.post(f"/surveys/{study_id}/qualtrics/pull")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert f"/surveys/{study_id}/qualtrics/job/" in location
    job_id = location.rstrip("/").split("/")[-1]

    # Polling page renders while the job runs/finishes.
    page = client.get(location)
    assert page.status_code == 200

    row = _wait_for_job(job_id)
    assert row["status"] == "done", row.get("message")
    dataset_id = row["result"]["dataset_id"]

    ds = db.get("datasets", dataset_id)
    assert ds["source"] == "qualtrics"
    assert ds["project_id"] == project_id
    assert ds["name"] == "Qualtrics responses: Pull me"
    assert ds["n_rows"] == 2
    meta = ds["source_meta"]
    assert meta["study_id"] == study_id
    assert meta["qualtrics_survey_id"] == "SV_remote9"
    assert "token" not in meta and "tok-secret-123" not in json.dumps(meta)
    # Real parser behavior: metadata column dropped, row-2 text kept as labels,
    # both remaining columns numeric.
    cols = {c["id"]: c for c in ds["columns"]}
    assert set(cols) == {"q1", "q2"}
    assert cols["q1"]["label"] == "How satisfied are you?"
    assert cols["q2"]["label"] == "What is your age?"
    assert all(c["kind"] == "numeric" for c in cols.values())


def test_pull_without_push_redirects_with_message():
    set_credential("qualtrics", CRED, user_id="dev")
    _, study_id = _make_survey()
    client = APP.test_client()
    resp = client.post(f"/surveys/{study_id}/qualtrics/pull")
    assert resp.status_code == 302
    assert "qualtrics_error=" in resp.headers["Location"]


# --- dormant (not-connected) state ---------------------------------------------

def test_not_connected_redirects_with_notice_not_crash():
    delete_credential("qualtrics", user_id="dev")
    _, study_id = _make_survey()
    client = APP.test_client()

    for action in ("push", "pull"):
        resp = client.post(f"/surveys/{study_id}/qualtrics/{action}")
        assert resp.status_code == 302
        assert "qualtrics_error=not_connected" in resp.headers["Location"]

    # Dashboard shows the not-connected card linking to /settings/connections.
    page = client.get(f"/surveys/dashboard/{study_id}",
                      query_string={"qualtrics_error": "not_connected"})
    assert page.status_code == 200
    assert b"/settings/connections" in page.data


# --- thin client: HTTP layer mocked --------------------------------------------

def test_client_pull_responses_three_step_flow(monkeypatch):
    """Exercise the real polling + ZIP-extraction logic with mocked HTTP."""
    calls = []
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("Pull me.csv", QUALTRICS_CSV)

    def fake_http(method, url, token, body=None, content_type=None):
        calls.append((method, url))
        assert token == "tok"
        if method == "POST" and url.endswith("/export-responses"):
            return json.dumps({"result": {"progressId": "PG_1"}}).encode()
        if method == "GET" and url.endswith("/export-responses/PG_1"):
            # First poll: in progress; second poll: complete.
            n = sum(1 for m, u in calls if u.endswith("/PG_1"))
            if n == 1:
                return json.dumps(
                    {"result": {"status": "inProgress", "percentComplete": 40}}
                ).encode()
            return json.dumps(
                {"result": {"status": "complete", "fileId": "FILE_9"}}
            ).encode()
        if method == "GET" and url.endswith("/export-responses/FILE_9/file"):
            return zip_buf.getvalue()
        raise AssertionError(f"unexpected request {method} {url}")

    monkeypatch.setattr(qualtrics_api, "_http", fake_http)
    monkeypatch.setattr(qualtrics_api, "POLL_INTERVAL_SECONDS", 0)

    text = qualtrics_api.pull_responses("SV_x", "tok", "fra1")
    assert text == QUALTRICS_CSV
    assert calls[0][1] == \
        "https://fra1.qualtrics.com/API/v3/surveys/SV_x/export-responses"


def test_client_401_maps_to_clear_value_error(monkeypatch):
    def fake_urlopen(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized",
                                     {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(qualtrics_api.urllib.request, "urlopen", fake_urlopen)
    try:
        qualtrics_api.pull_responses("SV_x", "bad-token", "fra1")
        assert False, "expected ValueError"
    except ValueError as e:
        msg = str(e)
        assert "Qualtrics rejected the token" in msg
        assert "bad-token" not in msg  # never leak the token


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
