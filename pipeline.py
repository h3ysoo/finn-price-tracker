"""Canonical search pipeline shared by the web UI, CLI, and future job worker.

This module has no dependency on Streamlit or the CLI renderer — it only
scrapes, scores, runs AI analysis, and persists. Callers pass a
`SearchParams` and get back a `SearchResult`; an optional `progress`
callback reports stage transitions so a UI or worker can surface status.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from analyzer import (
    analyze_prices,
    analyze_top_listings,
    score_listings,
    select_candidates,
)
from config import AI_ANALYSIS_LIMIT, DEFAULT_PAGES, LISTING_MIN_PRICE
from database import Database
from models import Listing, PriceReport
from scraper import FinnScraper, filter_listings

# Called with a short human-readable stage label; return value ignored.
ProgressCallback = Callable[[str], None]


@dataclass
class SearchParams:
    query: str
    pages: int = DEFAULT_PAGES
    ai_limit: int = AI_ANALYSIS_LIMIT
    min_price: int = LISTING_MIN_PRICE
    deep_scan: bool = False


@dataclass
class SearchResult:
    report: Optional[PriceReport]
    top: list[Listing]

    @property
    def is_empty(self) -> bool:
        return self.report is None or self.report.count == 0


def _noop(_: str) -> None:
    pass


async def run_search(
    params: SearchParams,
    progress: Optional[ProgressCallback] = None,
    *,
    headless: bool = True,
    persist: bool = True,
) -> SearchResult:
    """Run the full search → score → AI → persist pipeline.

    `progress` receives stage labels; `persist` can be disabled for dry runs.
    """
    report_stage = progress or _noop

    async with FinnScraper(headless=headless) as scraper:
        report_stage("Scanning Finn.no")
        listings = await scraper.search(params.query, pages=params.pages)
        if not listings:
            return SearchResult(report=None, top=[])

        listings = filter_listings(listings, min_price=params.min_price)
        if not listings:
            return SearchResult(report=None, top=[])

        # Deep scan reads every listing's detail page (accurate, slow);
        # fast mode only enriches the AI candidates below (~10x faster).
        if params.deep_scan:
            report_stage(f"Reading {len(listings)} detail pages")
            await scraper.enrich_all(listings, concurrency=3)

        report_stage("Analyzing prices")
        report = analyze_prices(listings)
        score_listings(listings)

        top = select_candidates(report, limit=params.ai_limit)
        if top:
            if not params.deep_scan:
                report_stage(f"Reading {len(top)} candidate detail pages")
                await scraper.enrich_all(top, concurrency=3)
                score_listings(listings)  # refresh with full descriptions
            report_stage(f"AI-analyzing {len(top)} listings")
            await analyze_top_listings(top, limit=params.ai_limit)

        if persist:
            report_stage("Saving results")
            # A partial scan (didn't reach the end) must not prune listings.
            Database().save_listings(
                report.listings, prune_missing=scraper.last_search_complete
            )

    return SearchResult(report=report, top=top)


def run_search_sync(
    params: SearchParams,
    progress: Optional[ProgressCallback] = None,
    **kwargs,
) -> SearchResult:
    """Blocking wrapper around `run_search` for non-async callers."""
    return asyncio.run(run_search(params, progress, **kwargs))
