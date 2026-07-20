"""Job-queue integration for search runs (ROADMAP Phase 2).

When `REDIS_URL` is configured, searches run in a separate RQ worker
process — the web process never launches a browser. Without `REDIS_URL`
(local development), callers fall back to running the pipeline
in-process, so no Redis is needed to use the app locally.

Worker side:  `rq worker searches --url $REDIS_URL`  executes
`run_search_job`, which reports its current stage via `job.meta["stage"]`.
"""
from __future__ import annotations

import os
from typing import Optional

from pipeline import SearchParams, SearchResult, run_search_sync

QUEUE_NAME = "searches"
# A deep scan of 10 pages can legitimately take a while; kill runaways after this.
JOB_TIMEOUT_S = 30 * 60
RESULT_TTL_S = 6 * 60 * 60


def redis_url() -> str:
    return os.getenv("REDIS_URL", "")


def queue_enabled() -> bool:
    """True when searches should go through the worker instead of in-process."""
    return bool(redis_url())


def _connection():
    from redis import Redis

    return Redis.from_url(redis_url())


def get_queue():
    from rq import Queue

    return Queue(QUEUE_NAME, connection=_connection())


def run_search_job(params: SearchParams) -> SearchResult:
    """Entry point executed by the RQ worker for one search."""
    try:
        from rq import get_current_job

        job = get_current_job()
    except Exception:
        job = None

    def progress(stage: str) -> None:
        if job is not None:
            job.meta["stage"] = stage
            job.save_meta()

    return run_search_sync(params, progress)


def enqueue_search(params: SearchParams):
    """Queue a search; returns the RQ job (its .id is the polling handle)."""
    return get_queue().enqueue(
        run_search_job,
        params,
        job_timeout=JOB_TIMEOUT_S,
        result_ttl=RESULT_TTL_S,
        failure_ttl=RESULT_TTL_S,
    )


def fetch_job(job_id: str):
    """Fetch a job by id; returns None if it expired or never existed."""
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    try:
        return Job.fetch(job_id, connection=_connection())
    except NoSuchJobError:
        return None


def job_result(job) -> Optional[SearchResult]:
    """Version-tolerant accessor for a finished job's return value."""
    if hasattr(job, "return_value"):
        return job.return_value()
    return job.result


def job_stage(job) -> str:
    """Human-readable current stage for the polling UI."""
    return (job.meta or {}).get("stage") or "Queued"
