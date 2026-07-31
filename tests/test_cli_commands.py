"""cmd_history URL support and the main() error boundary for DB commands."""
import argparse
from datetime import datetime

import main
from database import Database
from models import Listing


def _seed(tmp_path):
    db = Database(path=tmp_path / "t.db")
    db.save_listings([Listing(
        id="400111222", query="iphone 13", title="iPhone 13",
        price_nok=5000, url="https://www.finn.no/recommerce/forsale/item/400111222",
        scraped_at=datetime(2026, 7, 1, 12, 0), price_score=-5.0,
    )])
    db.save_listings([Listing(
        id="400111222", query="iphone 13", title="iPhone 13",
        price_nok=4500, url="https://www.finn.no/recommerce/forsale/item/400111222",
        scraped_at=datetime(2026, 7, 3, 12, 0), price_score=-5.0,
    )])
    return db


def test_history_accepts_full_url(tmp_path, monkeypatch, capsys):
    db = _seed(tmp_path)
    monkeypatch.setattr(main, "Database", lambda: db)
    url = "https://www.finn.no/recommerce/forsale/item/400111222"
    assert main.cmd_history(argparse.Namespace(id=url)) == 0


def test_history_accepts_bare_finnkode(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    monkeypatch.setattr(main, "Database", lambda: db)
    assert main.cmd_history(argparse.Namespace(id="400111222")) == 0


def test_history_unknown_id_returns_1(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    monkeypatch.setattr(main, "Database", lambda: db)
    assert main.cmd_history(argparse.Namespace(id="999")) == 1


def test_main_reports_db_error_cleanly(monkeypatch, capsys):
    # A DB command that blows up should exit 2 with a message, not a traceback
    def boom(_args):
        raise RuntimeError("database is locked")

    monkeypatch.setitem(main._DB_COMMANDS, "deals", boom)
    rc = main.main(["deals"])
    assert rc == 2
    assert "Error:" in capsys.readouterr().out


def test_main_reraises_with_verbose(monkeypatch):
    def boom(_args):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(main._DB_COMMANDS, "deals", boom)
    try:
        main.main(["-v", "deals"])
    except RuntimeError as e:
        assert "kaboom" in str(e)
    else:
        raise AssertionError("expected the error to propagate with -v")
