"""Minimal background job runner.

Agent pipelines (literature reviews, thematic coding, article generation) take
minutes. Routes start a job, return its id, and the UI polls
GET /api/jobs/<id> until status is done|error.
"""
import threading
import traceback

from . import db


def start_job(kind, fn, ref_table="", ref_id=""):
    """Run fn(job_id) in a daemon thread; returns the job id immediately.

    fn may call update_progress(job_id, ...) along the way and should return a
    JSON-serializable result dict.
    """
    job_id = db.insert("jobs", {
        "kind": kind, "ref_table": ref_table, "ref_id": ref_id,
        "status": "running", "message": "Started",
    })

    def runner():
        try:
            result = fn(job_id) or {}
            db.update("jobs", job_id, {
                "status": "done", "progress": 1.0, "message": "Completed", "result": result,
            })
        except Exception as e:
            db.update("jobs", job_id, {
                "status": "error",
                "message": f"{type(e).__name__}: {e}",
                "result": {"traceback": traceback.format_exc()},
            })

    threading.Thread(target=runner, daemon=True).start()
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
