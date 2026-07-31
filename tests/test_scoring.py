from analyzer.scoring import _extract_battery, compute_score
from models import AIReport, Listing


def _listing(**kw):
    base = dict(id="x", query="q", title="iPhone 13", url="u")
    base.update(kw)
    return Listing(**base)


def test_score_prefers_ai_battery_over_text():
    # Text says 100%, but the AI report says 60% — the score must use the AI value
    text_100 = _listing(description="batterikapasitet 100%", price_score=0.0)
    ai_60 = _listing(
        description="batterikapasitet 100%", price_score=0.0,
        ai_report=AIReport(condition_score=8, battery_pct=60),
    )
    assert compute_score(ai_60) < compute_score(text_100)


def test_score_uses_low_ai_battery_below_text_floor():
    # A degraded 40% battery (below the text regex's 50 floor) is honored via AI
    low = _listing(price_score=0.0, ai_report=AIReport(condition_score=5, battery_pct=40))
    none = _listing(price_score=0.0)  # no battery info at all
    # 40 * 0.30 = 12 pts vs the 18-pt "no info" default → lower
    assert compute_score(low) < compute_score(none)


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
