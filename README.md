# finn-price-tracker

A price analysis tool for **Finn.no**, Norway's second-hand marketplace.
Scrapes listings, runs statistical price analysis, and evaluates the cheapest listings
using **Claude Vision** — analyzing photos and descriptions together.

## Features

- Async scraping with `playwright` (pagination + rate limiting)
- Statistical report: mean, median, std dev, min/max, P25/P75
- **Price score** per listing relative to market average
- Irrelevant listing filter (accessories, empty boxes, buy-wanted ads)
- Full listing detail fetching — reads complete descriptions from each listing page
- Multi-image analysis: sends up to 8 gallery photos to Claude per listing
- Claude Vision analysis of photos + description (condition score, red flags, summary)
- SQLite persistence; `deals` command to surface best historic deals
- Colorful terminal output with `rich`
- UTF-8 + Norwegian character support (æ ø å)

## Setup

```bash
# 1. Virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Dependencies
pip install -r requirements.txt

# 3. Playwright browser
python -m playwright install chromium

# 4. API key
cp .env.example .env
# Add your Anthropic API key to .env:
# ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
# Search + price analysis + AI analysis of the cheapest 5 listings
python main.py search "iPhone 13 Pro Max 256GB"

# Change page count or AI limit
python main.py search "MacBook Pro M1" --pages 5 --ai-limit 3

# Run with visible browser (debug mode)
python main.py search "Sony WH-1000XM5" --show-browser

# Show best deals from the local database
python main.py deals --limit 20

# Verbose logging
python main.py -v search "Canon EOS R6"
```

## Project Structure

```
finn-price-tracker/
├── main.py                      # CLI entry point
├── config.py                    # Settings
├── scraper/
│   ├── finn_scraper.py          # Playwright scraper + detail page fetcher
│   └── listing_filter.py        # Irrelevant listing filter
├── analyzer/
│   ├── price_analyzer.py        # Statistical analysis
│   └── ai_analyzer.py           # Claude Vision analysis
├── database/db.py               # SQLite CRUD
├── models/listing.py            # Pydantic models
├── data/listings.db             # Created on first run
├── requirements.txt
├── .env.example
└── README.md
```

## Price Score

```
price_score = (listing_price - mean) / mean * 100
```

- `-20%` → 20% below market average (good deal)
- `+15%` → 15% above market average (overpriced)

## How AI Analysis Works

1. The scraper visits each candidate listing's detail page and fetches the full description and all gallery images (up to 8).
2. Images are upgraded to 960×720 resolution from the Finn CDN.
3. All images are sent to Claude in a single request alongside the full description.
4. Claude returns a condition score (1–10), red flags, and a summary in Turkish.

## Notes

- If Finn.no changes its page structure, CSS selectors in `scraper/finn_scraper.py` may need updating.
- AI analysis incurs API costs; by default only the 5 cheapest listings are analyzed (`--ai-limit` to change).
- Do not lower the rate limit aggressively to stay within Finn.no's Terms of Service.
