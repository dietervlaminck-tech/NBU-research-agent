"""Durable background jobs.

Pipelines register themselves in a registry of serializable tasks:

    @jobs.job("edgar_panel")
    def _run_panel_job(job_id, project_id=None, tickers=None, ...): ...

    job_id = jobs.start_job("edgar_panel", {"tickers": [...]}, ref_table=...)

Dispatch is pluggable:
- **Celery** (production): when CELERY_BROKER_URL is set and USE_EAGER_TASKS is
  not enabled, jobs are queued to Redis and executed by a separate worker
  process (`celery -A nbu_research.worker worker`), so Azure Web App restarts
  no longer kill running pipelines. See nbu_research/worker.py.
- **Thread** (dev fallback): without a broker — or with USE_EAGER_TASKS=true —
  jobs run in an in-process daemon thread, preserving the original single-
  process dev flow (no Redis needed, requests return immediately).

Either way the jobs table carries progress and the UI keeps polling
GET /api/jobs/<id> unchanged.
"""
import contextvars
import os
import threading
import traceback

from . import db

# The job currently executing in this thread/context; llm.py reads it so every
# AI call made inside a background pipeline is attributed in ai_usage_log.
current_job_id = contextvars.ContextVar("current_job_id", default=None)

# kind -> fn(job_id, **payload). Payloads must be JSON-serializable.
REGISTRY = {}


def job(kind):
    """Decorator: register a function as a runnable background job."""
    def deco(fn):
        REGISTRY[kind] = fn
        return fn
    return deco


def _use_celery():
    return bool(os.environ.get("CELERY_BROKER_URL")) and \
        os.environ.get("USE_EAGER_TASKS", "").lower() not in ("1", "true", "yes")


def execute(job_id, kind, payload):
    """Run one registered job and record its outcome in the jobs table.

    Idempotent on retry: a job that already completed is skipped, so a Celery
    redelivery after a worker crash (acks_late) cannot run a pipeline twice.
    """
    row = db.get("jobs", job_id)
    if row and row.get("status") == "done":
        return row.get("result") or {}
    db.update("jobs", job_id, {"status": "running"})
    current_job_id.set(job_id)
    try:
        fn = REGISTRY[kind]
        result = fn(job_id, **(payload or {})) or {}
        db.update("jobs", job_id, {
            "status": "done", "progress": 1.0, "message": "Completed",
            "result": result,
        })
        return result
    except Exception as e:
        db.update("jobs", job_id, {
            "status": "error",
            "message": f"{type(e).__name__}: {e}",
            "result": {"traceback": traceback.format_exc()},
        })
        return None
    finally:
        current_job_id.set(None)


def start_job(kind, payload=None, ref_table="", ref_id=""):
    """Create a job row and dispatch it; returns the job id immediately.

    `kind` must be registered via @jobs.job. Payload values must be
    JSON-serializable (they cross the Celery broker in production).
    """
    if kind not in REGISTRY:
        raise ValueError(f"Unknown job kind: {kind} (no registered handler)")
    job_id = db.insert("jobs", {
        "kind": kind, "ref_table": ref_table, "ref_id": ref_id,
        "status": "pending", "message": "Queued",
    })
    if _use_celery():
        from .worker import execute_job
        execute_job.delay(job_id, kind, payload or {})
    else:
        threading.Thread(
            target=execute, args=(job_id, kind, payload or {}), daemon=True,
        ).start()
    return job_id


def update_progress(job_id, progress=None, message=None):
    values = {}
    if progress is not None:
        values["progress"] = progress
    if message is not None:
        values["message"] = message
    if values:
        db.update("jobs", job_id, values)


def get_job(job_id):
    return db.get("jobs", job_id)
