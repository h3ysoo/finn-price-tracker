"""Test the search-page parser against a saved HTML fixture.

Instead of hitting a live Finn.no page, we parse a snapshot kept in sync
with the selectors — if Finn changes their markup and breaks the
selectors, this test flags it early in CI. Requires headless Chromium
(`python -m playwright install chromium`).
"""
import asyncio
from pathlib import Path

import pytest

from scraper.finn_scraper import FinnScraper

FIXTURE = Path(__file__).parent / "fixtures" / "search_results.html"


@pytest.fixture(scope="module")
def parsed_listings():
    async def run():
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(FIXTURE.read_text(encoding="utf-8"))
            listings = await FinnScraper()._parse_listings(page, "iphone 13")
            await browser.close()
            return listings

    return asyncio.run(run())


def test_parses_all_valid_cards(parsed_listings):
    # 3 articles, but one is a link-less ad — should yield 2 listings
    assert [l.id for l in parsed_listings] == ["400111222", "400333444"]


def test_card_fields(parsed_listings):
    first = parsed_listings[0]
    assert first.title == "iPhone 13 Pro Max 256GB"
    assert first.price_nok == 5500
    assert first.location == "Oslo"
    assert first.url.startswith("https://www.finn.no/bap/forsale/ad.html?finnkode=400111222")
    assert first.image_urls and "finncdn" in first.image_urls[0]
    assert "batterikapasitet 91%" in first.description
    assert first.query == "iphone 13"


def test_unpriced_card(parsed_listings):
    second = parsed_listings[1]
    assert second.title == "iPhone 13 mini 128GB"
    assert second.price_nok is None
    assert second.location == "Bergen"
