"""enrich_all must not let one listing's failure abort the rest."""
import asyncio

from scraper.finn_scraper import FinnScraper
from models import Listing


def _mk(i):
    return Listing(id=str(i), query="q", title=f"item {i}", url=f"u{i}")


def test_enrich_all_survives_a_failing_detail_fetch(monkeypatch):
    scraper = FinnScraper.__new__(FinnScraper)  # no browser needed
    fetched: list[str] = []

    async def fake_fetch_detail(listing):
        if listing.id == "2":
            raise RuntimeError("boom on listing 2")
        fetched.append(listing.id)

    monkeypatch.setattr(scraper, "fetch_detail", fake_fetch_detail)

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    listings = [_mk(1), _mk(2), _mk(3), _mk(4)]
    # Must not raise even though listing 2 fails
    asyncio.run(scraper.enrich_all(listings, concurrency=2))

    # Every listing except the failing one was still processed
    assert set(fetched) == {"1", "3", "4"}
