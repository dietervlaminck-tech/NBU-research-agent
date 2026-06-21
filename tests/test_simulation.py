"""Tests for the interview simulation engine (LLM calls mocked)."""
import os
import tempfile

os.environ["NBU_DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("ANTHROPIC_API_KEY", None)

from nbu_research import db  # noqa: E402
db.init_db()

from nbu_research import jobs  # noqa: E402
from nbu_research.modules.interviews import simulation  # noqa: E402
from nbu_research.modules.interviews import bot  # noqa: E402
from nbu_research.modules.analysis import qualitative  # noqa: E402


def _patch(monkeypatch, n=3):
    # Outline generation
    monkeypatch.setattr(simulation, "_generate_outline",
                        lambda rq, model: "1. Warm-up\n2. Core\n3. Closing")
    # Personas
    monkeypatch.setattr(simulation, "_generate_personas",
                        lambda rq, k, g, model: [
                            {"name": f"P{i}", "role": "Role", "description": "desc"}
                            for i in range(k)])

    # Interviewer: ask one question, then finish on the 2nd turn.
    calls = {"turns": 0}

    def fake_stream(study, messages):
        calls["turns"] += 1
        if calls["turns"] % 2 == 0:  # second interviewer turn ends it
            yield {"type": "text", "content": "Any final thoughts?"}
            yield {"type": "done", "code": "complete", "closing": "bye"}
        else:
            yield {"type": "text", "content": "How do you experience this?"}
            yield {"type": "done", "code": None, "closing": None}

    monkeypatch.setattr(bot, "stream_interview_reply", fake_stream)
    monkeypatch.setattr(simulation, "_persona_reply",
                        lambda persona, rq, transcript, q, model: "I think it is fine.")


def _run(payload):
    """Run the simulation job synchronously (no thread) and return the job id."""
    job_id = db.insert("jobs", {"kind": "interview_simulation",
                                "status": "pending", "message": ""})
    jobs.execute(job_id, "interview_simulation", payload)
    return job_id


def test_simulation_creates_flagged_study_and_sessions(monkeypatch):
    _patch(monkeypatch, n=3)
    monkeypatch.setattr(simulation.qualitative, "run_thematic_analysis",
                        lambda study_id, job_id: {"analysis_id": "fake-a"})

    job_id = _run({"research_question": "How is AI used in teaching?",
                   "n": 3, "auto_analyze": True})
    job = jobs.get_job(job_id)
    assert job["status"] == "done", job.get("message")
    result = job["result"]
    study = db.get("studies", result["study_id"])
    assert study["config"]["simulated"] is True
    assert study["config"]["n_simulated"] == 3
    assert len(study["config"]["personas"]) == 3
    sessions = db.query("sessions", "study_id = ?", (result["study_id"],),
                        order="started_at ASC")
    assert len(sessions) == 3
    assert all(s["status"] == "completed" for s in sessions)
    # Each session has alternating interviewer/respondent turns ending complete.
    msgs = sessions[0]["messages"]
    assert msgs[0]["role"] == "user" and msgs[-1]["role"] == "assistant"
    assert result["analysis_id"] == "fake-a"


def test_simulation_without_autoanalyze(monkeypatch):
    _patch(monkeypatch, n=2)
    job_id = _run({"research_question": "RQ?", "n": 2, "auto_analyze": False})
    job = jobs.get_job(job_id)
    assert job["status"] == "done"
    assert "analysis_id" not in job["result"]
    assert job["result"]["n"] == 2


def test_n_capped_at_max(monkeypatch):
    _patch(monkeypatch, n=999)
    job_id = _run({"research_question": "RQ?", "n": 999, "auto_analyze": False})
    job = jobs.get_job(job_id)
    assert job["result"]["n"] == simulation.MAX_N


if __name__ == "__main__":
    import types
    class MP:
        def setattr(self, obj, name, val): setattr(obj, name, val)
    for fn in (test_simulation_creates_flagged_study_and_sessions,
               test_simulation_without_autoanalyze, test_n_capped_at_max):
        fn(MP())
        print(fn.__name__, "OK")
    print("all simulation tests passed")
