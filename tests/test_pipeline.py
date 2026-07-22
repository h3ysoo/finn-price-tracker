import asyncio

import pipeline
from database import Database
from models import Listing
from pipeline import SearchParams, normalize_query, run_search


def test_normalize_query():
    assert normalize_query("iPhone  13 Pro ") == "iphone 13 pro"
    assert normalize_query("  MacBook\tAir M1") == "macbook air m1"
    assert normalize_query("brukt SYKKEL") == "brukt sykkel"
    assert normalize_query("iphone 13") == "iphone 13"  # already canonical


class FakeScraper:
    """Async-context-manager stand-in for FinnScraper (no network)."""

    def __init__(self, listings, complete=True):
        self._listings = listings
        self.last_search_complete = complete
        self.enriched = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def search(self, query, pages):
        return list(self._listings)

    async def enrich_all(self, listings, concurrency=3):
        self.enriched.append(len(listings))


def _mk(i, price):
    return Listing(id=str(i), query="q", title=f"iPhone {i}", price_nok=price, url=f"u{i}")


def _patch(monkeypatch, tmp_path, scraper):
    monkeypatch.setattr(pipeline, "FinnScraper", lambda **kw: scraper)

    async def fake_ai(top, limit):  # hermetic — no real API call
        return top

    monkeypatch.setattr(pipeline, "analyze_top_listings", fake_ai)
    db = Database(path=tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "Database", lambda: db)
    return db


def test_run_search_scores_persists_and_reports(tmp_path, monkeypatch):
    fake = FakeScraper([_mk(1, 5000), _mk(2, 6000), _mk(3, 7000)])
    db = _patch(monkeypatch, tmp_path, fake)

    stages = []
    result = asyncio.run(run_search(SearchParams(query="q", ai_limit=2), stages.append))

    assert not result.is_empty
    assert result.report.count == 3
    assert len(result.top) == 2
    # Fast mode: only the 2 candidates get enriched, not all 3
    assert fake.enriched == [2]
    assert any("Scanning" in s for s in stages)
    assert any("Saving" in s for s in stages)
    # Persisted with computed price scores
    saved = db.get_by_query("q")
    assert len(saved) == 3
    assert all(l.price_score is not None for l in saved)


def test_run_search_deep_scan_enriches_all(tmp_path, monkeypatch):
    fake = FakeScraper([_mk(1, 5000), _mk(2, 6000)])
    _patch(monkeypatch, tmp_path, fake)

    asyncio.run(run_search(SearchParams(query="q", ai_limit=1, deep_scan=True)))
    # Deep scan enriches all listings up front (2), not just the candidate
    assert fake.enriched[0] == 2


def test_run_search_empty(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, FakeScraper([]))
    result = asyncio.run(run_search(SearchParams(query="q")))
    assert result.is_empty
    assert result.report is None


def test_run_search_no_persist(tmp_path, monkeypatch):
    db = _patch(monkeypatch, tmp_path, FakeScraper([_mk(1, 5000)]))
    asyncio.run(run_search(SearchParams(query="q", ai_limit=1), persist=False))
    assert db.get_by_query("q") == []
