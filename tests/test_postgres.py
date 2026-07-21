"""Postgres integration tests for the data layer.

These run only when TEST_DATABASE_URL points at a Postgres server —
locally they skip; CI provides a postgres service container. They cover
the dialect-sensitive pieces: the ON CONFLICT upsert with COALESCE
preservation, window-function price drops, the expanding IN prune, and
the boolean query-filter parameter.
"""
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from database import Database
from models import AIReport, Listing

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (needs a Postgres server; runs in CI)",
)

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


@pytest.fixture
def db():
    database = Database(url=TEST_DATABASE_URL)
    with database.connect() as conn:
        conn.execute(text("DELETE FROM price_history"))
        conn.execute(text("DELETE FROM listings"))
    return database


def test_upsert_round_trip_preserves_ai_fields(db):
    db.save_listings([
        _listing(
            "111", 5000, composite_score=77.5,
            ai_report=AIReport(condition_score=9, battery_pct=88,
                               red_flags=["No box, æøå"], summary="s"),
        )
    ])
    # Re-save without AI data — COALESCE in the upsert must keep the old report
    db.save_listings([_listing("111", 4800, at=T1)])

    back = db.get_by_query("iphone 13")[0]
    assert back.price_nok == 4800
    assert back.composite_score == 77.5
    assert back.ai_report.battery_pct == 88
    assert back.ai_report.red_flags == ["No box, æøå"]  # UTF-8 survives


def test_price_history_and_drops(db):
    db.save_listings([_listing("111", 5000), _listing("222", 7000)])
    db.save_listings([_listing("111", 4000, at=T1), _listing("222", 7500, at=T1)])

    assert db.get_price_history("111", "iphone 13") == [(T0, 5000), (T1, 4000)]

    drops = db.get_price_drops()
    assert len(drops) == 1
    listing, prev = drops[0]
    assert (listing.id, prev, listing.price_nok) == ("111", 5000, 4000)


def test_missing_listings_marked_inactive(db):
    db.save_listings([_listing("111", 5000), _listing("222", 7000)])
    db.save_listings([_listing("111", 5000, at=T1)])  # 222 disappeared

    assert [l.id for l in db.get_best_deals()] == ["111"]
    assert [l.id for l in db.get_by_query("iphone 13", active_only=True)] == ["111"]


def test_query_filter_and_summaries(db):
    db.save_listings([_listing("111", 5000)])
    db.save_listings([_listing("222", 8000, query="macbook air")])
    db.save_listings([_listing("111", 4500, at=T1)])
    db.save_listings([_listing("222", 7000, at=T1, query="macbook air")])

    # Unfiltered (no_filter=True path) and filtered (query = :query path)
    assert {l.id for l, _ in db.get_price_drops()} == {"111", "222"}
    assert [l.id for l, _ in db.get_price_drops(query="iphone 13")] == ["111"]
    assert [l.id for l in db.get_best_deals(query="macbook air")] == ["222"]

    queries = dict((q, n) for q, n, _ in db.get_queries())
    assert queries == {"iphone 13": 1, "macbook air": 1}
    assert db.last_scan_time("iphone 13") == T1
    assert db.last_scan_time("never searched") is None
