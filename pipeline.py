"""Canonical search pipeline shared by the web UI, CLI, and future job worker.

This module has no dependency on Streamlit or the CLI renderer — it only
scrapes, scores, runs AI analysis, and persists. Callers pass a
`SearchParams` and get back a `SearchResult`; an optional `progress`
callback reports stage transitions so a UI or worker can surface status.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from analyzer import (
    analyze_prices,
    analyze_top_listings,
    score_listings,
    select_candidates,
)
from config import (
    AI_ANALYSIS_LIMIT,
    DEFAULT_PAGES,
    LISTING_MIN_PRICE,
    SEARCH_CACHE_TTL_HOURS,
)
from database import Database
from models import Listing, PriceReport
from scraper import FinnScraper, filter_listings

# Called with a short human-readable stage label; return value ignored.
ProgressCallback = Callable[[str], None]


def normalize_query(query: str) -> str:
    """Canonical form of a search query: trimmed, single-spaced, casefolded.

    'iPhone  13 ' and 'iphone 13' would otherwise create separate DB rows,
    separate price histories, and miss each other's cache.
    """
    return " ".join(query.split()).casefold()


@dataclass
class SearchParams:
    query: str
    pages: int = DEFAULT_PAGES
    ai_limit: int = AI_ANALYSIS_LIMIT
    min_price: int = LISTING_MIN_PRICE
    deep_scan: bool = False
    # False forces a fresh scrape even when cached results are still valid
    use_cache: bool = True


@dataclass
class SearchResult:
    report: Optional[PriceReport]
    top: list[Listing]
    from_cache: bool = False
    scanned_at: Optional[datetime] = None

    @property
    def is_empty(self) -> bool:
        return self.report is None or self.report.count == 0


def _noop(_: str) -> None:
    pass


def _load_cached(params: SearchParams) -> Optional[SearchResult]:
    """Return stored results if this query was scanned within the cache TTL."""
    if SEARCH_CACHE_TTL_HOURS <= 0:
        return None
    db = Database()
    last = db.last_scan_time(params.query)
    if last is None:
        return None
    if last.tzinfo is None:  # rows written before tz-aware timestamps
        last = last.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - last > timedelta(hours=SEARCH_CACHE_TTL_HOURS):
        return None
    listings = db.get_by_query(params.query, active_only=True)
    if not listings:
        return None
    # Stats are recomputed from the stored listings; composite scores and AI
    # reports come back exactly as persisted.
    report = analyze_prices(listings)
    top = sorted(
        (l for l in listings if l.ai_report),
        key=lambda l: l.composite_score or 0,
        reverse=True,
    )[: params.ai_limit]
    return SearchResult(report=report, top=top, from_cache=True, scanned_at=last)


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
    params = replace(params, query=normalize_query(params.query))

    if params.use_cache:
        cached = _load_cached(params)
        if cached is not None:
            report_stage("Serving cached results")
            return cached

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
            db = Database()
            # A partial scan (didn't reach the end) must not prune listings.
            db.save_listings(
                report.listings, prune_missing=scraper.last_search_complete
            )
            # Opportunistic cleanup: drop listings that have been sold/removed
            # for longer than the retention window (config.RETENTION_DAYS).
            db.prune_stale()

    return SearchResult(report=report, top=top)


def run_search_sync(
    params: SearchParams,
    progress: Optional[ProgressCallback] = None,
    **kwargs,
) -> SearchResult:
    """Blocking wrapper around `run_search` for non-async callers."""
    return asyncio.run(run_search(params, progress, **kwargs))
