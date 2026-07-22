"""Query-result cache: a recent scan of the same query short-circuits scraping."""
import asyncio
from datetime import datetime

import pipeline
from database import Database
from models import AIReport, Listing
from pipeline import SearchParams, run_search


class CountingScraper:
    """Fake FinnScraper that counts how many times search() runs."""

    def __init__(self, listings):
        self._listings = listings
        self.search_calls = 0
        self.last_search_complete = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def search(self, query, pages):
        self.search_calls += 1
        return [l.model_copy(deep=True) for l in self._listings]

    async def enrich_all(self, listings, concurrency=3):
        pass


def _mk(i, price):
    return Listing(id=str(i), query="q", title=f"iPhone {i}", price_nok=price, url=f"u{i}")


def _patch(monkeypatch, tmp_path, scraper):
    monkeypatch.setattr(pipeline, "FinnScraper", lambda **kw: scraper)

    async def fake_ai(top, limit):
        for l in top[:limit]:
            l.ai_report = AIReport(condition_score=7)
        return top

    monkeypatch.setattr(pipeline, "analyze_top_listings", fake_ai)
    db = Database(path=tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "Database", lambda: db)
    return db


def test_second_search_within_ttl_is_served_from_cache(tmp_path, monkeypatch):
    fake = CountingScraper([_mk(1, 5000), _mk(2, 6000)])
    _patch(monkeypatch, tmp_path, fake)

    stages = []
    r1 = asyncio.run(run_search(SearchParams(query="q", ai_limit=1)))
    r2 = asyncio.run(run_search(SearchParams(query="q", ai_limit=1), stages.append))

    assert not r1.from_cache
    assert r2.from_cache
    assert fake.search_calls == 1  # second run never scraped
    assert r2.report.count == r1.report.count == 2
    assert r2.scanned_at is not None
    # The AI-analyzed candidate is preserved through the cache
    assert len(r2.top) == 1 and r2.top[0].ai_report is not None
    assert stages == ["Serving cached results"]


def test_use_cache_false_forces_fresh_scan(tmp_path, monkeypatch):
    fake = CountingScraper([_mk(1, 5000)])
    _patch(monkeypatch, tmp_path, fake)

    asyncio.run(run_search(SearchParams(query="q", ai_limit=1)))
    r2 = asyncio.run(run_search(SearchParams(query="q", ai_limit=1, use_cache=False)))

    assert fake.search_calls == 2
    assert not r2.from_cache


def test_ttl_zero_disables_cache(tmp_path, monkeypatch):
    fake = CountingScraper([_mk(1, 5000)])
    _patch(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(pipeline, "SEARCH_CACHE_TTL_HOURS", 0)

    asyncio.run(run_search(SearchParams(query="q", ai_limit=1)))
    asyncio.run(run_search(SearchParams(query="q", ai_limit=1)))
    assert fake.search_calls == 2


def test_stale_results_trigger_a_new_scan(tmp_path, monkeypatch):
    fake = CountingScraper([_mk(1, 5000)])
    db = _patch(monkeypatch, tmp_path, fake)

    # Seed a scan far older than the TTL (naive timestamp = pre-tz rows)
    old = _mk(1, 4800)
    old.scraped_at = datetime(2026, 1, 1, 12, 0)
    db.save_listings([old])

    result = asyncio.run(run_search(SearchParams(query="q", ai_limit=1)))
    assert fake.search_calls == 1
    assert not result.from_cache


class StampingScraper(CountingScraper):
    """Like the real scraper: stamps listings with the query it was given."""

    async def search(self, query, pages):
        self.search_calls += 1
        out = [l.model_copy(deep=True) for l in self._listings]
        for l in out:
            l.query = query
        return out


def test_query_case_and_spacing_variants_share_the_cache(tmp_path, monkeypatch):
    fake = StampingScraper([_mk(1, 5000)])
    _patch(monkeypatch, tmp_path, fake)

    r1 = asyncio.run(run_search(SearchParams(query="iPhone  13 ", ai_limit=1)))
    r2 = asyncio.run(run_search(SearchParams(query="iphone 13", ai_limit=1)))

    assert fake.search_calls == 1  # the variant hit the first scan's cache
    assert r2.from_cache
    # Rows are stored under the canonical form
    assert r1.report.listings[0].query == "iphone 13"


def test_different_query_is_not_cached(tmp_path, monkeypatch):
    fake = CountingScraper([_mk(1, 5000)])
    _patch(monkeypatch, tmp_path, fake)

    asyncio.run(run_search(SearchParams(query="q", ai_limit=1)))
    fake._listings = [Listing(id="9", query="other", title="x", price_nok=100, url="u9")]
    asyncio.run(run_search(SearchParams(query="other", ai_limit=1, min_price=0)))
    assert fake.search_calls == 2
