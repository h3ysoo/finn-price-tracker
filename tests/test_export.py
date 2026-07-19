import argparse
import csv
import json
from datetime import datetime

import main
from database import Database
from models import AIReport, Listing

T0 = datetime(2026, 7, 1, 12, 0)


def _seed(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([
        Listing(
            id="111", query="iphone 13", title="iPhone 13, æøå test",
            price_nok=5000, url="https://finn.no/111", location="Oslo",
            scraped_at=T0, price_score=-12.5, composite_score=80.0,
            ai_report=AIReport(condition_score=9, battery_pct=88, summary="s"),
        ),
        Listing(
            id="222", query="iphone 13", title="iPhone 13 mini",
            price_nok=None, url="https://finn.no/222", scraped_at=T0,
        ),
    ])
    return db


def _run_export(db, tmp_path, monkeypatch, fmt):
    monkeypatch.setattr(main, "Database", lambda: db)
    out = tmp_path / f"out.{fmt}"
    rc = main.cmd_export(argparse.Namespace(query="iphone 13", format=fmt, output=str(out)))
    assert rc == 0
    return out


def test_export_csv(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    out = _run_export(db, tmp_path, monkeypatch, "csv")
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 2
    first = {r["id"]: r for r in rows}["111"]
    assert first["title"] == "iPhone 13, æøå test"  # comma + Norwegian characters preserved
    assert first["price_nok"] == "5000"
    assert first["condition_score"] == "9"
    assert first["battery_pct"] == "88"


def test_export_json(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    out = _run_export(db, tmp_path, monkeypatch, "json")
    data = json.loads(out.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data}
    assert by_id["111"]["composite_score"] == 80.0
    assert by_id["222"]["price_nok"] is None
    assert by_id["222"]["condition_score"] is None
    assert by_id["111"]["scraped_at"] == T0.isoformat()


def test_export_unknown_query(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    monkeypatch.setattr(main, "Database", lambda: db)
    rc = main.cmd_export(argparse.Namespace(query="missing", format="csv", output=None))
    assert rc == 1
