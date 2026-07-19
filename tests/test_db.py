from datetime import datetime, timedelta

from database import Database
from models import AIReport, Listing

T0 = datetime(2026, 7, 1, 12, 0)
T1 = T0 + timedelta(days=2)


def _listing(id_, price, at=T0, **kw):
    base = dict(
        id=id_, query="iphone 13", title=f"iPhone 13 ({id_})",
        price_nok=price, url=f"https://finn.no/{id_}", scraped_at=at,
        price_score=-5.0,
    )
    base.update(kw)
    return Listing(**base)


def test_price_history_records_only_changes(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000), _listing("222", 7000)])
    db.save_listings([_listing("111", 4200, at=T1), _listing("222", 7000, at=T1)])

    with db.connect() as conn:
        rows = [
            (r["listing_id"], r["price"])
            for r in conn.execute(
                "SELECT listing_id, price FROM price_history ORDER BY listing_id, seen_at"
            )
        ]
    assert rows == [("111", 5000), ("111", 4200), ("222", 7000)]


def test_get_price_drops(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000), _listing("222", 7000)])
    db.save_listings([_listing("111", 4000, at=T1), _listing("222", 7500, at=T1)])

    drops = db.get_price_drops()
    assert len(drops) == 1
    listing, prev = drops[0]
    assert (listing.id, prev, listing.price_nok) == ("111", 5000, 4000)


def test_get_price_history(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000)])
    db.save_listings([_listing("111", 4200, at=T1)])

    assert db.get_price_history("111", "iphone 13") == [(T0, 5000), (T1, 4200)]
    # A different query's history is empty
    assert db.get_price_history("111", "another search") == []


def test_get_queries_and_active_only(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000), _listing("222", 7000)])
    db.save_listings([_listing("333", 8000, at=T1, query="macbook air")])
    # 222 is missing from the fresh iphone scan → gets marked inactive
    db.save_listings([_listing("111", 5000, at=T1)])

    queries = db.get_queries()
    assert [(q, n) for q, n, _ in queries] == [("iphone 13", 1), ("macbook air", 1)]

    # active_only hides inactive listings; default returns all
    assert {l.id for l in db.get_by_query("iphone 13")} == {"111", "222"}
    assert [l.id for l in db.get_by_query("iphone 13", active_only=True)] == ["111"]


def test_deals_and_drops_query_filter(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000)])
    db.save_listings([_listing("222", 8000, query="macbook air")])
    db.save_listings([_listing("111", 4500, at=T1)])
    db.save_listings([_listing("222", 7000, at=T1, query="macbook air")])

    # Unfiltered: both queries contribute
    assert {l.id for l in db.get_best_deals()} == {"111", "222"}
    assert {l.id for l, _ in db.get_price_drops()} == {"111", "222"}

    # Filtered: only the specified query
    assert [l.id for l in db.get_best_deals(query="macbook air")] == ["222"]
    assert [l.id for l, _ in db.get_price_drops(query="iphone 13")] == ["111"]
    assert db.get_price_drops(query="missing search") == []


def test_get_listing_histories(tmp_path):
    db = Database(path=tmp_path / "t.db")
    # Same finnkode tracked across two queries; a third listing is unrelated
    db.save_listings([_listing("111", 5000), _listing("777", 9000)])
    db.save_listings([_listing("111", 4200, at=T1)])
    db.save_listings([_listing("111", 4300, at=T1, query="iphone 13 pro")])

    entries = db.get_listing_histories("111")
    assert [(l.query, hist) for l, hist in entries] == [
        ("iphone 13", [(T0, 5000), (T1, 4200)]),
        ("iphone 13 pro", [(T1, 4300)]),
    ]
    assert entries[0][0].title == "iPhone 13 (111)"
    assert db.get_listing_histories("no-such-listing") == []


def test_price_history_is_per_query(tmp_path):
    db = Database(path=tmp_path / "t.db")
    # Same finnkode appears at different prices in two different searches —
    # histories must not mix and no bogus "price drop" should be recorded
    db.save_listings([_listing("111", 5000)])
    db.save_listings([_listing("111", 4000, at=T1, query="iphone 13 pro")])

    assert db.get_price_drops() == []
    with db.connect() as conn:
        rows = [
            (r["query"], r["price"])
            for r in conn.execute(
                "SELECT query, price FROM price_history ORDER BY query"
            )
        ]
    assert rows == [("iphone 13", 5000), ("iphone 13 pro", 4000)]


def test_unpriced_listing_not_in_history(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("333", None)])
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM price_history").fetchone()["c"]
    assert n == 0


def test_ai_fields_round_trip(tmp_path):
    db = Database(path=tmp_path / "t.db")
    l = _listing(
        "444", 6000, composite_score=77.5,
        ai_report=AIReport(condition_score=9, battery_pct=88, red_flags=["No box"], summary="s"),
    )
    db.save_listings([l])
    back = db.get_by_query("iphone 13")[0]
    assert back.composite_score == 77.5
    assert back.ai_report.battery_pct == 88
    assert back.ai_report.red_flags == ["No box"]


def test_missing_listings_marked_inactive(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000), _listing("222", 7000)])
    # 222 is missing from the fresh scan → treat as sold, hide from deals
    db.save_listings([_listing("111", 5000, at=T1)])

    deals = db.get_best_deals()
    assert [l.id for l in deals] == ["111"]

    # If 222 reappears, it should be reactivated
    db.save_listings([_listing("111", 5000, at=T1), _listing("222", 6800, at=T1)])
    assert {l.id for l in db.get_best_deals()} == {"111", "222"}


def test_partial_scan_does_not_prune(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000), _listing("222", 7000)])
    # Partial scan (didn't reach the end of results): 222 wasn't seen but
    # is probably just on an unscanned page — don't mark it inactive
    db.save_listings([_listing("111", 5000, at=T1)], prune_missing=False)
    assert {l.id for l in db.get_best_deals()} == {"111", "222"}


def test_drops_exclude_inactive(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000)])
    db.save_listings([_listing("111", 4000, at=T1)])  # price dropped
    assert len(db.get_price_drops()) == 1
    # The listing is gone in the next scan → shouldn't appear in drops either
    db.save_listings([_listing("999", 9000, at=T1)])
    assert db.get_price_drops() == []


def test_upsert_preserves_ai_fields(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([
        _listing("555", 5000, ai_report=AIReport(condition_score=8, summary="analysis"))
    ])
    # Re-save without an AI report — the old AI data must be preserved
    db.save_listings([_listing("555", 4800, at=T1)])
    back = db.get_by_query("iphone 13")[0]
    assert back.price_nok == 4800
    assert back.ai_report is not None
    assert back.ai_report.condition_score == 8
