"""Listing persistence in SQLite."""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from config import DB_PATH, RETENTION_DAYS
from models import AIReport, Listing

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT NOT NULL,
    query TEXT NOT NULL,
    title TEXT NOT NULL,
    price INTEGER,
    url TEXT NOT NULL,
    location TEXT,
    scraped_at TEXT NOT NULL,
    image_urls TEXT,
    description TEXT,
    price_score REAL,
    composite_score REAL,
    condition_score INTEGER,
    battery_pct INTEGER,
    ai_summary TEXT,
    ai_red_flags TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (id, query)
);

CREATE INDEX IF NOT EXISTS idx_listings_query ON listings(query);
CREATE INDEX IF NOT EXISTS idx_listings_price_score ON listings(price_score);

CREATE TABLE IF NOT EXISTS price_history (
    listing_id TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    price INTEGER NOT NULL,
    seen_at TEXT NOT NULL
);
"""


class Database:
    """Thin SQLite wrapper."""

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Backfill columns added after the initial schema on older DB files."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
        for col, sql_type in (
            ("composite_score", "REAL"),
            ("battery_pct", "INTEGER"),
            ("is_active", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {sql_type}")

        # price_history used to be query-agnostic; the same finnkode appearing
        # in two different searches would cross-contaminate histories.
        # Legacy rows keep query=''.
        ph_cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_history)")}
        if "query" not in ph_cols:
            conn.execute("ALTER TABLE price_history ADD COLUMN query TEXT NOT NULL DEFAULT ''")

        # The index uses the 'query' column, so it must be created after the migration
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_history_listing_query "
            "ON price_history(listing_id, query, seen_at)"
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_listings(self, listings: Iterable[Listing], prune_missing: bool = True) -> int:
        """Upsert — overwrites the same (id, query) row.

        Price changes are also appended to the price_history table.
        With prune_missing=False, listings missing from the scan aren't
        marked inactive. Partial scans (that hit the page limit before
        reaching the end of results) should pass False so that listings
        still online aren't mistakenly marked as "sold".
        """
        listings = list(listings)
        rows = []
        for l in listings:
            rows.append(
                (
                    l.id,
                    l.query,
                    l.title,
                    l.price_nok,
                    l.url,
                    l.location,
                    l.scraped_at.isoformat(),
                    json.dumps(l.image_urls, ensure_ascii=False),
                    l.description,
                    l.price_score,
                    l.composite_score,
                    l.ai_report.condition_score if l.ai_report else None,
                    l.ai_report.battery_pct if l.ai_report else None,
                    l.ai_report.summary if l.ai_report else None,
                    json.dumps(l.ai_report.red_flags, ensure_ascii=False) if l.ai_report else None,
                )
            )
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO listings
                    (id, query, title, price, url, location, scraped_at,
                     image_urls, description, price_score, composite_score,
                     condition_score, battery_pct, ai_summary, ai_red_flags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id, query) DO UPDATE SET
                    title=excluded.title,
                    price=excluded.price,
                    url=excluded.url,
                    location=excluded.location,
                    scraped_at=excluded.scraped_at,
                    image_urls=excluded.image_urls,
                    description=excluded.description,
                    price_score=excluded.price_score,
                    is_active=1,
                    composite_score=COALESCE(excluded.composite_score, listings.composite_score),
                    condition_score=COALESCE(excluded.condition_score, listings.condition_score),
                    battery_pct=COALESCE(excluded.battery_pct, listings.battery_pct),
                    ai_summary=COALESCE(excluded.ai_summary, listings.ai_summary),
                    ai_red_flags=COALESCE(excluded.ai_red_flags, listings.ai_red_flags)
                """,
                rows,
            )
            self._record_price_history(conn, listings)
            if prune_missing:
                self._mark_missing_inactive(conn, listings)
        return len(rows)

    @staticmethod
    def _mark_missing_inactive(conn: sqlite3.Connection, listings: list[Listing]) -> None:
        """Listings missing from a fresh scan of the same query are treated as sold/removed."""
        by_query: dict[str, list[str]] = {}
        for l in listings:
            by_query.setdefault(l.query, []).append(l.id)
        for query, ids in by_query.items():
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE listings SET is_active = 0 "
                f"WHERE query = ? AND id NOT IN ({placeholders})",
                (query, *ids),
            )

    @staticmethod
    def _record_price_history(conn: sqlite3.Connection, listings: list[Listing]) -> None:
        """For every priced listing, add a history row if the price changed since the last entry."""
        for l in listings:
            if not l.price_nok:
                continue
            last = conn.execute(
                "SELECT price FROM price_history WHERE listing_id = ? AND query = ? "
                "ORDER BY seen_at DESC LIMIT 1",
                (l.id, l.query),
            ).fetchone()
            if last is None or last["price"] != l.price_nok:
                conn.execute(
                    "INSERT INTO price_history (listing_id, query, price, seen_at) VALUES (?,?,?,?)",
                    (l.id, l.query, l.price_nok, l.scraped_at.isoformat()),
                )

    def prune_stale(self, days: Optional[int] = None) -> tuple[int, int]:
        """Delete listings inactive for longer than `days` plus their history.

        Defaults to config.RETENTION_DAYS; 0 (or negative) is a no-op.
        Returns (listings_deleted, history_rows_deleted).
        """
        days = RETENTION_DAYS if days is None else days
        if days <= 0:
            return (0, 0)
        from datetime import timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.connect() as conn:
            history = conn.execute(
                """
                DELETE FROM price_history
                WHERE EXISTS (
                    SELECT 1 FROM listings l
                    WHERE l.id = price_history.listing_id
                      AND l.query = price_history.query
                      AND l.is_active = 0
                      AND l.scraped_at < ?
                )
                """,
                (cutoff,),
            ).rowcount
            listings = conn.execute(
                "DELETE FROM listings WHERE is_active = 0 AND scraped_at < ?",
                (cutoff,),
            ).rowcount
        if listings or history:
            log.info(
                "Pruned %d stale listings and %d history rows (older than %d days).",
                listings, history, days,
            )
        return (int(listings), int(history))

    def get_by_query(self, query: str, active_only: bool = False) -> list[Listing]:
        sql = "SELECT * FROM listings WHERE query = ?"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY price_score ASC"
        with self.connect() as conn:
            cur = conn.execute(sql, (query,))
            return [self._row_to_listing(r) for r in cur.fetchall()]

    def last_scan_time(self, query: str) -> Optional[datetime]:
        """When this query's active listings were last scraped (None if never)."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(scraped_at) AS m FROM listings "
                "WHERE query = ? AND is_active = 1",
                (query,),
            ).fetchone()
        if row is None or row["m"] is None:
            return None
        return datetime.fromisoformat(row["m"])

    def get_queries(self) -> list[tuple[str, int, datetime]]:
        """Summary of saved searches — (query, active listing count, last scan time)."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT query, COUNT(*) AS n, MAX(scraped_at) AS last_seen
                FROM listings
                WHERE is_active = 1
                GROUP BY query
                ORDER BY last_seen DESC
                """
            )
            return [
                (r["query"], int(r["n"]), datetime.fromisoformat(r["last_seen"]))
                for r in cur.fetchall()
            ]

    def get_best_deals(self, limit: int = 10, query: Optional[str] = None) -> list[Listing]:
        """Listings with the most negative price_score (cheapest) — only still-active ones.

        If query is given, restrict the results to that search.
        """
        sql = (
            "SELECT * FROM listings "
            "WHERE price_score IS NOT NULL AND is_active = 1"
        )
        params: list = []
        if query is not None:
            sql += " AND query = ?"
            params.append(query)
        sql += " ORDER BY price_score ASC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            return [self._row_to_listing(r) for r in cur.fetchall()]

    def get_price_drops(
        self, limit: int = 10, query: Optional[str] = None
    ) -> list[tuple[Listing, int]]:
        """Listings whose latest recorded price dropped versus the previous one.

        Returns (Listing, previous_price) pairs; biggest percentage drop first.
        If query is given, results are restricted to that search.
        """
        with self.connect() as conn:
            cur = conn.execute(
                """
                WITH ranked AS (
                    SELECT listing_id, query, price,
                           ROW_NUMBER() OVER (
                               PARTITION BY listing_id, query ORDER BY seen_at DESC
                           ) AS rn
                    FROM price_history
                    WHERE (:query IS NULL OR query = :query)
                ),
                drops AS (
                    SELECT cur.listing_id,
                           cur.query,
                           cur.price AS current_price,
                           prev.price AS previous_price
                    FROM ranked cur
                    JOIN ranked prev
                      ON prev.listing_id = cur.listing_id
                     AND prev.query = cur.query
                     AND prev.rn = 2
                    WHERE cur.rn = 1 AND cur.price < prev.price
                )
                SELECT l.*, d.previous_price
                FROM drops d
                JOIN listings l ON l.id = d.listing_id AND l.query = d.query
                                AND l.is_active = 1
                ORDER BY (d.previous_price - d.current_price) * 1.0 / d.previous_price DESC
                LIMIT :limit
                """,
                {"query": query, "limit": limit},
            )
            return [
                (self._row_to_listing(r), int(r["previous_price"]))
                for r in cur.fetchall()
            ]

    def get_price_history(self, listing_id: str, query: str) -> list[tuple[datetime, int]]:
        """Price history for a listing — (seen_at, price), oldest first."""
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT seen_at, price FROM price_history "
                "WHERE listing_id = ? AND query = ? ORDER BY seen_at",
                (listing_id, query),
            )
            return [
                (datetime.fromisoformat(r["seen_at"]), int(r["price"]))
                for r in cur.fetchall()
            ]

    def get_listing_histories(
        self, listing_id: str
    ) -> list[tuple[Listing, list[tuple[datetime, int]]]]:
        """For a finnkode, return (listing, price history) for every query it was tracked under.

        The same listing may be tracked across multiple searches; entries without a
        recorded history are skipped.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE id = ? ORDER BY query", (listing_id,)
            ).fetchall()
        out: list[tuple[Listing, list[tuple[datetime, int]]]] = []
        for r in rows:
            listing = self._row_to_listing(r)
            history = self.get_price_history(listing_id, listing.query)
            if history:
                out.append((listing, history))
        return out

    @staticmethod
    def _row_to_listing(row: sqlite3.Row) -> Listing:
        ai_report: Optional[AIReport] = None
        if row["condition_score"] is not None or row["ai_summary"]:
            try:
                red_flags = json.loads(row["ai_red_flags"]) if row["ai_red_flags"] else []
            except json.JSONDecodeError:
                red_flags = []
            ai_report = AIReport(
                condition_score=row["condition_score"] or 5,
                battery_pct=row["battery_pct"],
                red_flags=red_flags,
                summary=row["ai_summary"] or "",
            )
        try:
            image_urls = json.loads(row["image_urls"]) if row["image_urls"] else []
        except json.JSONDecodeError:
            image_urls = []
        return Listing(
            id=row["id"],
            query=row["query"],
            title=row["title"],
            price_nok=row["price"],
            url=row["url"],
            location=row["location"],
            scraped_at=datetime.fromisoformat(row["scraped_at"]),
            image_urls=image_urls,
            description=row["description"] or "",
            price_score=row["price_score"],
            composite_score=row["composite_score"],
            ai_report=ai_report,
        )


# Module-level shortcuts
_default: Optional[Database] = None


def _get() -> Database:
    global _default
    if _default is None:
        _default = Database()
    return _default


def init_db() -> Database:
    return _get()


def save_listings(listings: Iterable[Listing], prune_missing: bool = True) -> int:
    return _get().save_listings(listings, prune_missing=prune_missing)


def get_by_query(query: str, active_only: bool = False) -> list[Listing]:
    return _get().get_by_query(query, active_only=active_only)


def get_queries() -> list[tuple[str, int, datetime]]:
    return _get().get_queries()


def get_best_deals(limit: int = 10, query: Optional[str] = None) -> list[Listing]:
    return _get().get_best_deals(limit, query=query)


def get_price_drops(limit: int = 10, query: Optional[str] = None) -> list[tuple[Listing, int]]:
    return _get().get_price_drops(limit, query=query)


def get_price_history(listing_id: str, query: str) -> list[tuple[datetime, int]]:
    return _get().get_price_history(listing_id, query)


def get_listing_histories(listing_id: str) -> list[tuple[Listing, list[tuple[datetime, int]]]]:
    return _get().get_listing_histories(listing_id)


def prune_stale(days: Optional[int] = None) -> tuple[int, int]:
    return _get().prune_stale(days)
