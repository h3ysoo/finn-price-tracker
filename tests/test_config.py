import importlib
from pathlib import Path


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "d" / "custom.db"))
    monkeypatch.setenv("CLAUDE_MODEL", "claude-test-model")
    monkeypatch.setenv("AI_ANALYSIS_LIMIT", "9")
    monkeypatch.setenv("REQUEST_DELAY_MIN", "0.25")
    monkeypatch.setenv("AI_CONCURRENCY", "not-a-number")  # invalid → default

    import config
    try:
        importlib.reload(config)
        assert config.DATA_DIR == tmp_path / "d"
        assert config.DB_PATH == tmp_path / "d" / "custom.db"
        assert config.CLAUDE_MODEL == "claude-test-model"
        assert config.AI_ANALYSIS_LIMIT == 9
        assert config.REQUEST_DELAY_MIN == 0.25
        assert config.AI_CONCURRENCY == 3  # invalid value falls back
        assert (tmp_path / "d").exists()  # DATA_DIR is created
    finally:
        monkeypatch.undo()
        importlib.reload(config)  # restore defaults for the rest of the suite


def test_defaults_without_env(monkeypatch):
    for var in ("DATA_DIR", "DB_PATH", "CLAUDE_MODEL", "AI_ANALYSIS_LIMIT",
                "REQUEST_DELAY_MIN", "AI_CONCURRENCY", "LISTING_MIN_PRICE"):
        monkeypatch.delenv(var, raising=False)
    import config
    try:
        importlib.reload(config)
        assert config.AI_ANALYSIS_LIMIT == 5
        assert config.AI_CONCURRENCY == 3
        assert config.LISTING_MIN_PRICE == 500
        assert config.DB_PATH == Path(config.DATA_DIR) / "listings.db"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
