"""Projeye ait ayarlar. .env dosyasından okunur."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Yollar
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "listings.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-5"

# Finn.no
FINN_BASE_URL = "https://www.finn.no"
FINN_SEARCH_PATH = "/bap/forsale/search.html"
DEFAULT_PAGES = 3
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.0

# AI analizi — maliyet için ilk N ilan
AI_ANALYSIS_LIMIT = 5
# Aynı anda en fazla kaç Claude isteği (görsel-ağır istekler rate limit'e takılmasın)
AI_CONCURRENCY = 3

# Filtreleme — bu fiyatın altındaki ilanlar aksesuar/kutu olarak sayılır ve çıkarılır
LISTING_MIN_PRICE = 500

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
