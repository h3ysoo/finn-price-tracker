from analyzer.scoring import _extract_battery, compute_score
from models import Listing


def _listing(**kw):
    base = dict(id="x", query="q", title="iPhone 13", url="u")
    base.update(kw)
    return Listing(**base)


def test_extract_battery():
    assert _extract_battery("batterikapasitet 87%") == 87
    assert _extract_battery("batteri 100 %") == 100
    assert _extract_battery("30% rabatt") is None  # outside the plausible range
    assert _extract_battery("ingen info") is None


def test_compute_score_rewards_good_listing():
    good = _listing(
        title="iPhone 13 pent brukt",
        description="Som ny, batterikapasitet 95%. Original eske og kvittering. " + "x" * 200,
        price_score=-30.0,
    )
    bad = _listing(
        title="iPhone 13",
        description="Knust skjerm, selges som den er.",
        price_score=30.0,
    )
    assert compute_score(good) > compute_score(bad)
    assert 0 <= compute_score(bad) <= 100
    assert 0 <= compute_score(good) <= 100


def test_compute_score_neutral_without_info():
    score = compute_score(_listing(description=""))
    assert 0 <= score <= 100
