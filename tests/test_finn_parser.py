"""Kaydedilmiş HTML fixture'ı üzerinden arama sayfası parser'ını test eder.

Gerçek bir Finn.no sayfası yerine selector'larla uyumlu tutulan bir
snapshot kullanılır; Finn yapı değiştirip selector'lar bozulursa bu
test CI'da erken uyarı verir. Headless Chromium gerektirir
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
    # 3 article var ama biri linksiz reklam — 2 ilan çıkmalı
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
