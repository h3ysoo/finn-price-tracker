"""Retention pruning: long-inactive listings and their history are deleted."""
from datetime import datetime, timedelta, timezone

from database import Database
from models import Listing

NOW = datetime.now(timezone.utc)
OLD = NOW - timedelta(days=90)     # older than the default 60-day window
RECENT = NOW - timedelta(days=10)  # inside the window


def _listing(id_, price, at, query="iphone 13"):
    return Listing(
        id=id_, query=query, title=f"iPhone 13 ({id_})", price_nok=price,
        url=f"https://finn.no/{id_}", scraped_at=at, price_score=-5.0,
    )


def _history_count(db) -> int:
    with db.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM price_history"
        ).fetchone()["c"]


def test_prune_deletes_old_inactive_with_history(tmp_path):
    db = Database(path=tmp_path / "t.db")
    # 111 goes stale: seen old, then absent from a fresh scan → inactive + old
    db.save_listings([_listing("111", 5000, OLD), _listing("222", 6000, OLD)])
    db.save_listings([_listing("222", 6000, RECENT)])  # 111 now inactive

    # Sanity: 111 is inactive and has history rows
    assert _history_count(db) == 2  # one per listing's first sighting

    listings_deleted, history_deleted = db.prune_stale(days=60)
    assert listings_deleted == 1
    assert history_deleted == 1

    remaining = {l.id for l in db.get_by_query("iphone 13")}
    assert remaining == {"222"}
    assert _history_count(db) == 1  # only 222's history left


def test_prune_keeps_recent_inactive(tmp_path):
    db = Database(path=tmp_path / "t.db")
    # 111 is inactive but only 10 days old → within retention, must survive
    db.save_listings([_listing("111", 5000, RECENT), _listing("222", 6000, RECENT)])
    db.save_listings([_listing("222", 6000, NOW)])  # 111 inactive but recent

    assert db.prune_stale(days=60) == (0, 0)
    assert {l.id for l in db.get_by_query("iphone 13")} == {"111", "222"}


def test_prune_never_touches_active_listings(tmp_path):
    db = Database(path=tmp_path / "t.db")
    # Active but with an old scraped_at (e.g. cached) — must not be deleted
    db.save_listings([_listing("111", 5000, OLD)])
    assert db.prune_stale(days=60) == (0, 0)
    assert [l.id for l in db.get_by_query("iphone 13")] == ["111"]


def test_prune_disabled_with_zero_days(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([_listing("111", 5000, OLD), _listing("222", 6000, OLD)])
    db.save_listings([_listing("222", 6000, RECENT)])
    assert db.prune_stale(days=0) == (0, 0)
    assert {l.id for l in db.get_by_query("iphone 13")} == {"111", "222"}
