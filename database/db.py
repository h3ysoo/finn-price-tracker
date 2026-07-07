"""SQLite ile ilan kalıcılığı."""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional

from config import DB_PATH
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
    price INTEGER NOT NULL,
    seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id, seen_at);
"""


class Database:
    """İnce SQLite sarmalayıcı."""

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
        """Eski DB dosyalarına sonradan eklenen kolonları tamamla."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
        for col, sql_type in (
            ("composite_score", "REAL"),
            ("battery_pct", "INTEGER"),
            ("is_active", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {sql_type}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_listings(self, listings: Iterable[Listing]) -> int:
        """Upsert — aynı (id, query) kaydı üzerine yazar.

        Fiyat değişimleri ayrıca price_history tablosuna eklenir.
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
            self._mark_missing_inactive(conn, listings)
        return len(rows)

    @staticmethod
    def _mark_missing_inactive(conn: sqlite3.Connection, listings: list[Listing]) -> None:
        """Aynı sorgunun taze taramasında görünmeyen ilanlar satılmış/kalkmış demektir."""
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
        """Fiyatı olan her ilan için, son kayıttan farklıysa yeni geçmiş satırı ekle."""
        for l in listings:
            if not l.price_nok:
                continue
            last = conn.execute(
                "SELECT price FROM price_history WHERE listing_id = ? "
                "ORDER BY seen_at DESC LIMIT 1",
                (l.id,),
            ).fetchone()
            if last is None or last["price"] != l.price_nok:
                conn.execute(
                    "INSERT INTO price_history (listing_id, price, seen_at) VALUES (?,?,?)",
                    (l.id, l.price_nok, l.scraped_at.isoformat()),
                )

    def get_by_query(self, query: str) -> list[Listing]:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT * FROM listings WHERE query = ? ORDER BY price_score ASC",
                (query,),
            )
            return [self._row_to_listing(r) for r in cur.fetchall()]

    def get_best_deals(self, limit: int = 10) -> list[Listing]:
        """En negatif price_score (en ucuz) ilanlar — sadece hâlâ yayında olanlar."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM listings
                WHERE price_score IS NOT NULL AND is_active = 1
                ORDER BY price_score ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [self._row_to_listing(r) for r in cur.fetchall()]

    def get_price_drops(self, limit: int = 10) -> list[tuple[Listing, int]]:
        """Son taramada fiyatı bir önceki kayda göre düşen ilanlar.

        (Listing, önceki_fiyat) çiftleri döner; en büyük yüzde düşüş önce.
        """
        with self.connect() as conn:
            cur = conn.execute(
                """
                WITH ranked AS (
                    SELECT listing_id, price,
                           ROW_NUMBER() OVER (
                               PARTITION BY listing_id ORDER BY seen_at DESC
                           ) AS rn
                    FROM price_history
                ),
                drops AS (
                    SELECT cur.listing_id,
                           cur.price AS current_price,
                           prev.price AS previous_price
                    FROM ranked cur
                    JOIN ranked prev
                      ON prev.listing_id = cur.listing_id AND prev.rn = 2
                    WHERE cur.rn = 1 AND cur.price < prev.price
                )
                SELECT l.*, d.previous_price
                FROM drops d
                JOIN listings l ON l.id = d.listing_id AND l.is_active = 1
                GROUP BY d.listing_id
                ORDER BY (d.previous_price - d.current_price) * 1.0 / d.previous_price DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [
                (self._row_to_listing(r), int(r["previous_price"]))
                for r in cur.fetchall()
            ]

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


# Modül düzeyi kısayollar
_default: Optional[Database] = None


def _get() -> Database:
    global _default
    if _default is None:
        _default = Database()
    return _default


def init_db() -> Database:
    return _get()


def save_listings(listings: Iterable[Listing]) -> int:
    return _get().save_listings(listings)


def get_by_query(query: str) -> list[Listing]:
    return _get().get_by_query(query)


def get_best_deals(limit: int = 10) -> list[Listing]:
    return _get().get_best_deals(limit)


def get_price_drops(limit: int = 10) -> list[tuple[Listing, int]]:
    return _get().get_price_drops(limit)
