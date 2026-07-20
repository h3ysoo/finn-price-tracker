"""Project settings.

Loaded from `.env` / environment variables. Every deployment-relevant
setting can be overridden via an environment variable (used by the Docker
setup); the defaults below keep local development behavior unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


# Paths
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "listings.db")))

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")

# Finn.no
FINN_BASE_URL = "https://www.finn.no"
FINN_SEARCH_PATH = "/recommerce/forsale/search"
DEFAULT_PAGES = _env_int("DEFAULT_PAGES", 3)
REQUEST_DELAY_MIN = _env_float("REQUEST_DELAY_MIN", 1.0)
REQUEST_DELAY_MAX = _env_float("REQUEST_DELAY_MAX", 2.0)

# AI analysis — cheapest N listings only (to control cost)
AI_ANALYSIS_LIMIT = _env_int("AI_ANALYSIS_LIMIT", 5)
# Max concurrent Claude requests (image-heavy calls trip rate limits otherwise)
AI_CONCURRENCY = _env_int("AI_CONCURRENCY", 3)

# Filter — listings below this price are treated as accessories/boxes and dropped
LISTING_MIN_PRICE = _env_int("LISTING_MIN_PRICE", 500)

# Serve stored results when the same query was scanned less than this many
# hours ago instead of scraping again (0 disables the cache). The biggest
# cost / IP-risk reducer once several people share one deployment.
SEARCH_CACHE_TTL_HOURS = _env_float("SEARCH_CACHE_TTL_HOURS", 6.0)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
