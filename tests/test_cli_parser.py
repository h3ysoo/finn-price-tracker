"""CLI argument parsing — flags flow through to the search parameters."""
import argparse
import asyncio

import pytest

import main
from config import AI_ANALYSIS_LIMIT, DEFAULT_PAGES, LISTING_MIN_PRICE
from main import _build_parser


def _parse(argv):
    return _build_parser().parse_args(argv)


def test_search_defaults():
    args = _parse(["search", "iphone 13"])
    assert args.query == "iphone 13"
    assert args.pages == DEFAULT_PAGES
    assert args.ai_limit == AI_ANALYSIS_LIMIT
    assert args.min_price == LISTING_MIN_PRICE
    assert args.deep_scan is False
    assert args.fresh is False


def test_search_min_price_override():
    args = _parse(["search", "macbook", "--min-price", "3000", "--pages", "5"])
    assert args.min_price == 3000
    assert args.pages == 5


def test_search_flags():
    args = _parse(["search", "sony", "--deep-scan", "--fresh"])
    assert args.deep_scan is True
    assert args.fresh is True


def test_prune_days():
    assert _parse(["prune"]).days is None
    assert _parse(["prune", "--days", "30"]).days == 30


@pytest.mark.parametrize("argv", [
    ["search", "x", "--pages", "0"],       # pages must be >= 1
    ["search", "x", "--ai-limit", "-1"],   # ai-limit must be >= 0
    ["search", "x", "--min-price", "-5"],  # min-price must be >= 0
    ["deals", "--limit", "0"],             # limit must be >= 1
    ["drops", "--limit", "-3"],
    ["prune", "--days", "-1"],
    ["search", "x", "--pages", "abc"],     # non-integer
])
def test_rejects_out_of_range_numbers(argv):
    with pytest.raises(SystemExit):  # argparse exits on a bad type
        _parse(argv)


def test_accepts_valid_boundaries():
    assert _parse(["search", "x", "--ai-limit", "0"]).ai_limit == 0
    assert _parse(["search", "x", "--min-price", "0"]).min_price == 0
    assert _parse(["prune", "--days", "0"]).days == 0
    assert _parse(["deals", "--limit", "1"]).limit == 1


@pytest.mark.parametrize("query", ["", "   ", "\t "])
def test_search_rejects_empty_query_without_scraping(query, monkeypatch):
    # If the guard fails, cmd_search would construct FinnScraper; make that blow up
    monkeypatch.setattr(main, "run_search", None)
    args = argparse.Namespace(query=query, pages=3, ai_limit=5, min_price=500,
                              deep_scan=False, fresh=False, show_browser=False)
    assert asyncio.run(main.cmd_search(args)) == 1
