"""Durable job queue (Feature 4): registry, thread fallback, idempotency."""
import os
import tempfile
import time

os.environ.setdefault("NBU_DATA_DIR", tempfile.mkdtemp())
os.environ.pop("CELERY_BROKER_URL", None)  # force thread fallback

from nbu_research import db, jobs  # noqa: E402

db.init_db()

CALLS = []


@jobs.job("test_dummy")
def _dummy(job_id, value=0):
    CALLS.append(job_id)
    jobs.update_progress(job_id, 0.5, "halfway")
    return {"doubled": value * 2}


@jobs.job("test_boom")
def _boom(job_id):
    raise ValueError("intentional kaboom")


def _wait(job_id, timeout=10):
    for _ in range(int(timeout * 20)):
        row = jobs.get_job(job_id)
        if row and row["status"] in ("done", "error"):
            return row
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


def test_thread_fallback_runs_registered_job():
    job_id = jobs.start_job("test_dummy", {"value": 21})
    row = _wait(job_id)
    assert row["status"] == "done"
    assert row["result"]["doubled"] == 42
    assert row["progress"] == 1.0


def test_failed_job_marked_with_error_message():
    job_id = jobs.start_job("test_boom")
    row = _wait(job_id)
    assert row["status"] == "error"
    assert "kaboom" in row["message"]
    assert "traceback" in (row["result"] or {})


def test_unknown_kind_raises_immediately():
    try:
        jobs.start_job("never_registered")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "never_registered" in str(e)


def test_execute_is_idempotent_on_retry():
    """A completed job re-delivered by the broker must not run again."""
    job_id = jobs.start_job("test_dummy", {"value": 5})
    _wait(job_id)
    n_calls = CALLS.count(job_id)
    result = jobs.execute(job_id, "test_dummy", {"value": 5})  # simulate redelivery
    assert CALLS.count(job_id) == n_calls  # not executed again
    assert result["doubled"] == 10  # prior result returned


def test_all_pipeline_kinds_are_registered():
    # Importing the app registers every module's jobs.
    from nbu_research import create_app
    create_app()
    for kind in ("thematic", "literature_review", "article_generation",
                 "article_revision", "edgar_panel", "edgar_filing_analysis",
                 "refinitiv_panel"):
        assert kind in jobs.REGISTRY, kind


def test_worker_module_importable_in_eager_mode():
    os.environ["USE_EAGER_TASKS"] = "true"
    try:
        from nbu_research import worker
        assert worker.celery_app.conf.task_always_eager is True
        assert "nbu_research.execute_job" in worker.celery_app.tasks
    finally:
        os.environ.pop("USE_EAGER_TASKS", None)
        # worker.py load_dotenv()s at import (it runs standalone in prod);
        # in the test process that re-injects the developer's real .env keys.
        # Scrub them so later tests stay offline and cost nothing.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("CELERY_BROKER_URL", None)


if __name__ == "__main__":
    for name in sorted(k for k in dir() if k.startswith("test_")):
        globals()[name]()
        print(name, "OK")
    print("all job tests passed")
