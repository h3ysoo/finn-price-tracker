"""CLI argument parsing — flags flow through to the search parameters."""
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
