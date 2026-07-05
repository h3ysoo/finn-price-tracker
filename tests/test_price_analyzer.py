from analyzer.price_analyzer import _percentile, analyze_prices, select_candidates
from models import Listing


def _listing(id_, price):
    return Listing(id=id_, query="q", title=f"item {id_}", price_nok=price, url=f"u{id_}")


def test_percentile():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(vals, 0) == 1.0
    assert _percentile(vals, 50) == 3.0
    assert _percentile(vals, 100) == 5.0
    assert _percentile([], 50) == 0.0
    assert _percentile([7.0], 25) == 7.0


def test_analyze_prices_scores_and_sorts():
    listings = [_listing("a", 100), _listing("b", 200), _listing("c", 300), _listing("d", None)]
    report = analyze_prices(listings)
    assert report.count == 3
    assert report.mean == 200
    assert report.median == 200
    # En ucuz önce, fiyatsız en sona
    assert [l.id for l in report.listings] == ["a", "b", "c", "d"]
    assert report.listings[0].price_score == -50.0
    assert report.listings[2].price_score == 50.0
    assert report.listings[3].price_score is None


def test_analyze_prices_empty():
    report = analyze_prices([_listing("a", None)])
    assert report.count == 0
    assert report.mean == 0.0


def test_select_candidates_skips_suspiciously_cheap():
    # Ortalamanın %60'ından fazla altı şüpheli sayılır ve atlanır
    listings = [_listing("cheap", 10), _listing("a", 100), _listing("b", 110), _listing("c", 120)]
    report = analyze_prices(listings)
    top = select_candidates(report, limit=2)
    assert "cheap" not in [l.id for l in top]
    assert len(top) == 2
