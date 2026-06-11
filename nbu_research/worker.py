"""Celery worker for durable background jobs.

Start alongside the web process (same image, same code):

    celery -A nbu_research.worker worker --loglevel=info --concurrency=2

Configuration (see .env.example):
    CELERY_BROKER_URL      e.g. redis://localhost:6379/0  (required for Celery)
    CELERY_RESULT_BACKEND  defaults to the broker URL
    USE_EAGER_TASKS=true   dev flag: task_always_eager — tasks run inline in
                           the calling process, no broker/worker needed

Without CELERY_BROKER_URL the web app never touches Celery at all (jobs fall
back to in-process threads — see nbu_research/jobs.py).
"""
import os

from dotenv import load_dotenv

load_dotenv(override=True)

from celery import Celery  # noqa: E402

BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", BROKER_URL)

celery_app = Celery("nbu_research", broker=BROKER_URL, backend=RESULT_BACKEND)
celery_app.conf.update(
    task_always_eager=os.environ.get("USE_EAGER_TASKS", "").lower()
    in ("1", "true", "yes"),
    # acks_late + the done-check in jobs.execute() make a retry after a worker
    # crash safe: redelivered jobs that already completed are skipped.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

# Import the app package so every module registers its jobs in jobs.REGISTRY.
from . import create_app  # noqa: E402

_flask_app = create_app()

from . import jobs  # noqa: E402


@celery_app.task(
    name="nbu_research.execute_job",
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError, OSError),
    retry_kwargs={"max_retries": 1, "countdown": 5},
)
def execute_job(self, job_id, kind, payload):
    """Run one registered platform job inside the worker.

    Pipeline errors are caught by jobs.execute() and recorded on the job row
    as status=error (the task itself succeeds — no broker-level retry storm);
    only transient infrastructure errors trigger the single automatic retry.
    """
    with _flask_app.app_context():
        return jobs.execute(job_id, kind, payload)
