"""Per-project role enforcement (Feature 2).

Auth is simulated by patching config attributes (the real OAuth round-trip
needs Azure) and injecting users into the Flask session. Config is restored
after each test so later test files keep running in dev mode.
"""
import os
import tempfile
from contextlib import contextmanager

os.environ.setdefault("NBU_DATA_DIR", tempfile.mkdtemp())
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db, config, create_app  # noqa: E402

db.init_db()

APP = create_app()
APP.config["TESTING"] = True

ALICE = {"user_id": "oid-alice", "display_name": "Alice", "email": "alice@nyenrode.nl",
         "roles": ["researcher"]}
BOB = {"user_id": "oid-bob", "display_name": "Bob", "email": "bob@nyenrode.nl",
       "roles": ["researcher"]}


@contextmanager
def auth_on():
    saved = (config.AZURE_CLIENT_ID, config.AZURE_CLIENT_SECRET,
             config.AZURE_TENANT_ID, config.AZURE_REDIRECT_URI)
    config.AZURE_CLIENT_ID = "test-client"
    config.AZURE_CLIENT_SECRET = "s"
    config.AZURE_TENANT_ID = "tenant"
    config.AZURE_REDIRECT_URI = "http://localhost/auth/callback"
    try:
        yield
    finally:
        (config.AZURE_CLIENT_ID, config.AZURE_CLIENT_SECRET,
         config.AZURE_TENANT_ID, config.AZURE_REDIRECT_URI) = saved


def client_as(user):
    c = APP.test_client()
    with c.session_transaction() as s:
        s["user"] = user
    return c


def _ensure_user(user):
    if not db.get("users", user["user_id"]):
        db.insert("users", {"id": user["user_id"],
                            "display_name": user["display_name"],
                            "email": user["email"]})


def _new_project(owner):
    _ensure_user(owner)
    _ensure_user(BOB)
    pid = db.insert("projects", {"title": "Roles fixture"})
    db.insert("project_members", {"project_id": pid, "user_id": owner["user_id"],
                                  "role": "owner", "added_at": db.now()})
    return pid


def test_owner_can_view_nonmember_cannot():
    with auth_on():
        pid = _new_project(ALICE)
        assert client_as(ALICE).get(f"/projects/{pid}").status_code == 200
        assert client_as(BOB).get(f"/projects/{pid}").status_code == 403


def test_viewer_reads_but_cannot_manage():
    with auth_on():
        pid = _new_project(ALICE)
        db.insert("project_members", {"project_id": pid, "user_id": BOB["user_id"],
                                      "role": "viewer", "added_at": db.now()})
        bob = client_as(BOB)
        assert bob.get(f"/projects/{pid}").status_code == 200
        # viewer may not invite members (owner-only)
        assert bob.post(f"/projects/{pid}/members",
                        data={"email": "x@y.z", "role": "viewer"}).status_code == 403
        # viewer may not delete the project
        assert bob.delete(f"/api/projects/{pid}").status_code == 403


def test_owner_invites_existing_user_by_email():
    with auth_on():
        pid = _new_project(ALICE)
        r = client_as(ALICE).post(f"/projects/{pid}/members",
                                  data={"email": BOB["email"], "role": "collaborator"})
        assert r.status_code in (302, 303)
        rows = db.query("project_members", "project_id = ? AND user_id = ?",
                        (pid, BOB["user_id"]), order="")
        assert rows and rows[0]["role"] == "collaborator"


def test_unknown_email_becomes_pending_invite():
    with auth_on():
        pid = _new_project(ALICE)
        client_as(ALICE).post(f"/projects/{pid}/members",
                              data={"email": "new@nyenrode.nl", "role": "viewer"})
        inv = db.query("project_invites", "project_id = ?", (pid,), order="")
        assert inv and inv[0]["email"] == "new@nyenrode.nl"


def test_legacy_project_without_members_is_grandfathered():
    with auth_on():
        pid = db.insert("projects", {"title": "Legacy, no members"})
        assert client_as(BOB).get(f"/projects/{pid}").status_code == 200


def test_dev_mode_allows_everything():
    pid = db.insert("projects", {"title": "Dev mode"})
    c = APP.test_client()  # no session user; auth disabled
    assert c.get(f"/projects/{pid}").status_code == 200


def test_collaborator_can_run_analysis_viewer_cannot():
    with auth_on():
        pid = _new_project(ALICE)
        study_id = db.insert("studies", {
            "project_id": pid, "study_type": "survey", "title": "S",
            "config": {"questions": [{"id": "q1", "type": "numeric",
                                      "text": "n", "required": True,
                                      "scale": {"min": 0, "max": 9}}]},
        })
        db.insert("project_members", {"project_id": pid, "user_id": BOB["user_id"],
                                      "role": "viewer", "added_at": db.now()})
        bob = client_as(BOB)
        assert bob.get(f"/analysis/study/{study_id}").status_code == 200
        assert bob.post(f"/analysis/study/{study_id}/run",
                        data={"kind": "descriptives"}).status_code == 403


if __name__ == "__main__":
    for name in sorted(k for k in dir() if k.startswith("test_")):
        globals()[name]()
        print(name, "OK")
    print("all role tests passed")
