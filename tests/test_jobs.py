"""Job-queue tests using fakeredis + a synchronous RQ queue (no server)."""
import asyncio

from fakeredis import FakeStrictRedis
from rq import Queue

import jobs
import pipeline
from database import Database
from models import Listing
from pipeline import SearchParams


class FakeScraper:
    def __init__(self, listings):
        self._listings = listings
        self.last_search_complete = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def search(self, query, pages):
        return list(self._listings)

    async def enrich_all(self, listings, concurrency=3):
        pass


def _mk(i, price):
    return Listing(id=str(i), query="q", title=f"iPhone {i}", price_nok=price, url=f"u{i}")


def _patch_pipeline(monkeypatch, tmp_path, listings):
    monkeypatch.setattr(pipeline, "FinnScraper", lambda **kw: FakeScraper(listings))

    async def fake_ai(top, limit):
        return top

    monkeypatch.setattr(pipeline, "analyze_top_listings", fake_ai)
    db = Database(path=tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "Database", lambda: db)
    return db


def test_queue_disabled_without_redis_url(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert not jobs.queue_enabled()


def test_queue_enabled_with_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    assert jobs.queue_enabled()


def test_run_search_job_roundtrip(tmp_path, monkeypatch):
    db = _patch_pipeline(monkeypatch, tmp_path, [_mk(1, 5000), _mk(2, 6000)])

    # Synchronous queue executes the job in-process — same code path the
    # real worker runs, minus the network hop.
    q = Queue(is_async=False, connection=FakeStrictRedis())
    job = q.enqueue(jobs.run_search_job, SearchParams(query="q", ai_limit=1))

    assert job.is_finished
    result = jobs.job_result(job)
    assert result.report.count == 2
    assert len(result.top) == 1
    # The worker persisted through the shared pipeline
    assert len(db.get_by_query("q")) == 2


def test_run_search_job_outside_worker(tmp_path, monkeypatch):
    # Direct call (no RQ context) must also work — progress is a no-op then.
    _patch_pipeline(monkeypatch, tmp_path, [_mk(1, 5000)])
    result = jobs.run_search_job(SearchParams(query="q", ai_limit=1))
    assert result.report.count == 1


def test_job_stage_defaults_to_queued():
    class Stub:
        meta = {}

    assert jobs.job_stage(Stub()) == "Queued"
    Stub.meta = {"stage": "Scanning Finn.no"}
    assert jobs.job_stage(Stub()) == "Scanning Finn.no"
