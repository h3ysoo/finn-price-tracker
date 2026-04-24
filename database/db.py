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
    condition_score INTEGER,
    ai_summary TEXT,
    ai_red_flags TEXT,
    PRIMARY KEY (id, query)
);

CREATE INDEX IF NOT EXISTS idx_listings_query ON listings(query);
CREATE INDEX IF NOT EXISTS idx_listings_price_score ON listings(price_score);
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
        """Upsert — aynı (id, query) kaydı üzerine yazar."""
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
                    l.ai_report.condition_score if l.ai_report else None,
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
                     image_urls, description, price_score,
                     condition_score, ai_summary, ai_red_flags)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id, query) DO UPDATE SET
                    title=excluded.title,
                    price=excluded.price,
                    url=excluded.url,
                    location=excluded.location,
                    scraped_at=excluded.scraped_at,
                    image_urls=excluded.image_urls,
                    description=excluded.description,
                    price_score=excluded.price_score,
                    condition_score=COALESCE(excluded.condition_score, listings.condition_score),
                    ai_summary=COALESCE(excluded.ai_summary, listings.ai_summary),
                    ai_red_flags=COALESCE(excluded.ai_red_flags, listings.ai_red_flags)
                """,
                rows,
            )
        return len(rows)

    def get_by_query(self, query: str) -> list[Listing]:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT * FROM listings WHERE query = ? ORDER BY price_score ASC",
                (query,),
            )
            return [self._row_to_listing(r) for r in cur.fetchall()]

    def get_best_deals(self, limit: int = 10) -> list[Listing]:
        """En negatif price_score (en ucuz) ilanlar."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM listings
                WHERE price_score IS NOT NULL
                ORDER BY price_score ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [self._row_to_listing(r) for r in cur.fetchall()]

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
