# finn-price-tracker

A price analysis tool for **Finn.no**, Norway's second-hand marketplace.
Scrapes listings, runs statistical price analysis, tracks price history over
time, and evaluates the most promising listings using **Claude Vision** —
analyzing photos and descriptions together. Comes with both a CLI and a
Streamlit web UI.

## Features

- Async scraping with `playwright` (pagination, rate limiting, retry with backoff)
- Statistical report: mean, median, std dev, min/max, P25/P75
- **Price score** per listing relative to the market median (robust to outliers)
- **Composite deal score** (0–100) combining price, battery health, and listing quality
- Irrelevant listing filter (accessories, empty boxes, buy-wanted ads)
- Claude Vision analysis of photos + description via structured output
  (condition score, battery %, red flags, summary)
- **Price history tracking** — every price change is recorded per search;
  `drops` lists listings whose price went down, `history` shows one listing's
  full price timeline
- Sold-listing tracking — listings that disappear from a scan are marked
  inactive and excluded from deals/drops
- **Result cache** — repeating a search within `SEARCH_CACHE_TTL_HOURS`
  (default 6 h) serves stored results instantly instead of re-scraping;
  override with `--fresh` (CLI) or the *Force fresh scan* toggle (web)
- Queries are normalized (case/spacing) so variants share one cache,
  history, and set of rows
- **Retention** — listings sold/removed for more than `RETENTION_DAYS`
  (default 60) are pruned automatically after each scan; `prune` runs it
  on demand
- CSV/JSON export for spreadsheets and scripts
- SQLite persistence; colorful terminal output with `rich`
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

Without an API key everything except the Claude Vision analysis still works.

## Web UI

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`:

- **Search** any product; results are scored and the best candidates get a
  Claude Vision review with photos, red flags, and a price history chart
- **Price drops** land on the home screen as KPI tiles
- **Saved searches** — browse earlier scan results instantly, no re-scraping
- **Deep scan** toggle: read every listing's detail page for more accurate
  scores (slower), or only the AI candidates' pages (~10x faster)

## CLI Usage

```bash
# Search + price analysis + AI analysis of the best 5 candidates
python main.py search "iPhone 13 Pro Max 256GB"

# Change page count or AI limit
python main.py search "MacBook Pro M1" --pages 5 --ai-limit 3

# Run with visible browser (debug mode)
python main.py search "Sony WH-1000XM5" --show-browser

# Best deals from the local database (optionally scoped to one search)
python main.py deals --limit 20
python main.py deals --query "iPhone 13 Pro Max 256GB"

# Listings whose price dropped since a previous scan
python main.py drops
python main.py drops --query "iPhone 13 Pro Max 256GB"

# Recorded price history of a single listing (by finnkode)
python main.py history 400111222

# Export stored listings of a query as CSV or JSON
python main.py export "iPhone 13 Pro Max 256GB" -o listings.csv
python main.py export "iPhone 13 Pro Max 256GB" --format json

# Delete listings sold/removed longer ago than the retention window
python main.py prune            # uses RETENTION_DAYS (default 60)
python main.py prune --days 30

# Verbose logging
python main.py -v search "Canon EOS R6"
```

## Project Structure

```
finn-price-tracker/
├── main.py                      # CLI entry point
├── app.py                       # Streamlit web UI
├── config.py                    # Settings (model, rate limits, thresholds)
├── .streamlit/config.toml       # UI theme
├── scraper/
│   ├── finn_scraper.py          # Playwright scraper + detail page fetcher
│   └── listing_filter.py        # Irrelevant listing filter
├── analyzer/
│   ├── price_analyzer.py        # Statistical analysis + price score
│   ├── scoring.py               # Composite deal score (0-100)
│   └── ai_analyzer.py           # Claude Vision analysis (structured output)
├── database/db.py               # SQLite CRUD + price history
├── models/listing.py            # Pydantic models
├── tests/                       # pytest suite (run in CI on every push)
├── data/listings.db             # Created on first run (gitignored)
├── requirements.txt             # Runtime dependencies
├── requirements-dev.txt         # + pytest
└── .github/workflows/ci.yml     # CI: tests on Python 3.11 & 3.12
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Price Score

```
price_score = (listing_price - median) / median * 100
```

- `-20%` → 20% below the market median (good deal)
- `+15%` → 15% above the market median (overpriced)

The median is used instead of the mean so a single mislabeled or
outlier listing can't skew every score.

## How AI Analysis Works

1. The scraper visits each candidate listing's detail page and fetches the full description and all gallery images (up to 8).
2. Images are upgraded to 960×720 resolution from the Finn CDN.
3. All images are sent to Claude in a single request alongside the full description.
4. A forced tool call pins the response to a fixed schema: condition score (1–10), battery %, red flags, and a summary.
5. Requests run with bounded concurrency to stay within rate limits.

## Notes

- If Finn.no changes its page structure, CSS selectors in `scraper/finn_scraper.py` may need updating — the fixture test in `tests/test_finn_parser.py` acts as an early-warning canary.
- AI analysis incurs API costs; by default only the 5 best candidates are analyzed (`--ai-limit` to change). The model is set in `config.py`.
- Do not lower the rate limit aggressively to stay within Finn.no's Terms of Service.

## Disclaimer

This project is intended for **personal and educational use only**. It is not
affiliated with or endorsed by Finn.no. Scraping may be subject to Finn.no's
Terms of Service and applicable law — you are responsible for how you use this
tool. The built-in rate limiting exists to keep requests modest and respectful;
please keep it that way. Scraped data is stored only locally and no listing
data is redistributed by this project.
