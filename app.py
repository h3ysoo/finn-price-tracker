"""Finn.no phone price analysis — Streamlit UI."""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from analyzer import analyze_prices, analyze_top_listings, select_candidates, score_listings
from config import AI_ANALYSIS_LIMIT, LISTING_MIN_PRICE
from database import Database
from models import Listing, PriceReport
from scraper import FinnScraper, filter_listings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BATTERY_RE = re.compile(r"(\d{2,3})\s*%")
_STORAGE_RE = re.compile(r"\b(\d+)\s*[Gg][Bb]\b")


def _extract_battery(text: str) -> Optional[int]:
    m = _BATTERY_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_storage(text: str) -> Optional[str]:
    m = _STORAGE_RE.search(text)
    return f"{m.group(1)} GB" if m else None


def _fmt_price(v: Optional[int]) -> str:
    if not v:
        return "—"
    return f"{v:,} kr".replace(",", " ")


def _render_listings_table(listings: list[Listing]) -> None:
    """Render listings as a dataframe sorted by composite score."""
    sorted_listings = sorted(listings, key=lambda x: x.composite_score or 0, reverse=True)

    rows = []
    for l in sorted_listings:
        battery = None
        if l.ai_report and l.ai_report.battery_pct:
            battery = l.ai_report.battery_pct
        else:
            battery = _extract_battery(l.title + " " + l.description)

        storage = _extract_storage(l.title)

        city = "—"
        if l.location:
            city = l.location.split(",")[0].strip()

        cscore = l.composite_score
        if cscore is not None:
            if cscore >= 70:
                score_label = f"🟢 {cscore}"
            elif cscore >= 50:
                score_label = f"🟡 {cscore}"
            else:
                score_label = f"🔴 {cscore}"
        else:
            score_label = "—"

        rows.append({
            "Score": score_label,
            "📍 City": city,
            "Title": l.title,
            "Price (kr)": l.price_nok or 0,
            "Market Diff": f"{l.price_score:+.1f}%" if l.price_score is not None else "—",
            "Storage": storage or "—",
            "Battery": f"{battery}%" if battery else "—",
            "AI Condition": f"{l.ai_report.condition_score}/10" if l.ai_report else "—",
            "Link": l.url,
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="Open →"),
            "Price (kr)": st.column_config.NumberColumn(format="%d kr"),
            "Score": st.column_config.TextColumn("⭐ Score", width="small"),
            "📍 City": st.column_config.TextColumn(width="small"),
        },
        column_order=["Score", "📍 City", "Title", "Price (kr)", "Market Diff",
                      "Storage", "Battery", "AI Condition", "Link"],
    )


def _score_color(score: Optional[float]) -> str:
    if score is None:
        return "gray"
    if score < -15:
        return "green"
    if score < -5:
        return "lightgreen"
    if score > 15:
        return "red"
    if score > 5:
        return "orange"
    return "gray"


# ---------------------------------------------------------------------------
# Scraper pipeline (sync wrapper — run inside Streamlit with asyncio.run)
# ---------------------------------------------------------------------------

async def _pipeline(
    query: str,
    pages: int,
    ai_limit: int,
    min_price: int,
    deep_scan: bool,
) -> tuple[Optional[PriceReport], list[Listing]]:
    async with FinnScraper(headless=True) as scraper:
        listings = await scraper.search(query, pages=pages)
        if not listings:
            return None, []

        listings = filter_listings(listings, min_price=min_price)
        if not listings:
            return None, []

        # Deep scan visits every listing's detail page (in parallel).
        # Fast mode only fetches detail pages for AI candidates — ~10x faster.
        if deep_scan:
            await scraper.enrich_all(listings, concurrency=3)

        # Price analysis (compute price_score)
        report = analyze_prices(listings)

        # Composite score: price + battery + content quality
        score_listings(listings)

        # Pick the highest-scoring candidates for AI analysis
        top = select_candidates(report, limit=ai_limit)
        if top:
            if not deep_scan:
                await scraper.enrich_all(top, concurrency=3)
                # Candidates now have full descriptions — refresh scores
                score_listings(listings)
            await analyze_top_listings(top, limit=ai_limit)

        # Persist results — price history accumulates here too.
        # Listings absent from a partial scan are NOT marked as sold.
        Database().save_listings(
            report.listings, prune_missing=scraper.last_search_complete
        )

        return report, top


def run_pipeline(query, pages, ai_limit, min_price, deep_scan):
    # asyncio.run already defaults to ProactorEventLoop on Windows (3.8+)
    return asyncio.run(_pipeline(query, pages, ai_limit, min_price, deep_scan))


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Finn Phone Tracker",
    page_icon="📱",
    layout="wide",
)

st.title("📱 Finn.no — Second-Hand Phone Analysis")
st.caption("Scan Finn.no listings, compare prices, and find the best deal with AI.")

# ---------------------------------------------------------------------------
# Sidebar (settings)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Search Settings")
    pages = st.slider("Pages to scan", 1, 10, 3)
    ai_limit = st.slider("Listings to analyze with AI", 1, 10, 5,
                         help="The N cheapest listings get a detailed Claude analysis.")
    min_price = st.number_input("Minimum price (kr)", 100, 10000, LISTING_MIN_PRICE, step=100,
                                help="Listings below this price (accessories, empty boxes, etc.) are filtered out.")
    deep_scan = st.checkbox(
        "Deep scan",
        value=False,
        help="Reads every listing's detail page — scores are more accurate but it "
             "takes 2-4 minutes. When off, only AI candidates are fetched (~30-60s).",
    )

    st.divider()
    st.markdown("**How it works**")
    st.markdown("""
1. Search Finn.no
2. Filter out accessories / empty-box ads
3. Compute market price
4. Read detail pages for the cheapest listings
5. Claude analyzes photos + description
""")

# ---------------------------------------------------------------------------
# Search box
# ---------------------------------------------------------------------------

col_q, col_btn = st.columns([5, 1])
with col_q:
    query = st.text_input(
        "Phone model",
        placeholder="iPhone 13 Pro Max 256GB",
        label_visibility="collapsed",
    )
with col_btn:
    search = st.button("🔍 Search", use_container_width=True, type="primary")

# ---------------------------------------------------------------------------
# Search pipeline — results live in session_state so a rerun triggered by a
# slider or checkbox does not throw away the 2-4 min scan result
# ---------------------------------------------------------------------------

if search and query.strip():
    duration = "2-4 minutes" if deep_scan else "30-60 seconds"
    with st.spinner(f"Scanning Finn.no for '{query}' and running AI analysis... This takes about {duration}, please wait."):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(run_pipeline, query.strip(), pages, ai_limit, min_price, deep_scan)
            try:
                report, top = future.result()
            except Exception as e:
                st.error(f"An error occurred: {e}")
                st.stop()
    st.session_state["results"] = {"query": query.strip(), "report": report, "top": top}

results = st.session_state.get("results")

# ---------------------------------------------------------------------------
# Price drops (shown on the landing screen when no search results are loaded)
# ---------------------------------------------------------------------------

if results is None:
    drops = Database().get_price_drops(limit=10)
    if drops:
        st.subheader("📉 Price Drops")
        st.caption("Listings still online whose price dropped since the previous scan.")
        drop_rows = []
        for l, prev_price in drops:
            pct = (prev_price - (l.price_nok or 0)) / prev_price * 100
            drop_rows.append({
                "Title": l.title,
                "Old Price": _fmt_price(prev_price),
                "New Price": _fmt_price(l.price_nok),
                "Drop": f"-{pct:.1f}%",
                "Link": l.url,
            })
        st.dataframe(
            pd.DataFrame(drop_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="Open →"),
            },
        )

    # --- Saved searches: view previous scans without re-scraping ---
    saved_queries = Database().get_queries()
    if saved_queries:
        st.subheader("🗂 Saved Searches")
        st.caption("View earlier scan results without triggering a new scan.")
        options = {
            f"{q}  —  {n} listings, last scan {dt:%Y-%m-%d %H:%M}": q
            for q, n, dt in saved_queries
        }
        col_sel, col_show = st.columns([5, 1])
        with col_sel:
            picked = st.selectbox(
                "Saved search", list(options.keys()), label_visibility="collapsed"
            )
        with col_show:
            show_saved = st.button("📂 Show", use_container_width=True)
        if show_saved and picked:
            st.session_state["saved_view"] = options[picked]

        saved_query = st.session_state.get("saved_view")
        if saved_query:
            listings = Database().get_by_query(saved_query, active_only=True)
            if listings:
                st.subheader(f"📋 Saved Results — {saved_query}")
                _render_listings_table(listings)
            else:
                st.info("No active listings remain for this search.")

# ---------------------------------------------------------------------------
# Results (from session_state — persist across reruns)
# ---------------------------------------------------------------------------

if results is not None:
    report = results["report"]
    top = results["top"]

    if report is None or report.count == 0:
        st.warning("No matching listings found. Try a different model or a lower minimum price.")
        st.stop()

    # --- Stat cards ---
    st.subheader(f"📊 Market Summary — {results['query']}")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Listings", report.count)
    c2.metric("Mean", _fmt_price(int(report.mean)))
    c3.metric("Median", _fmt_price(int(report.median)))
    c4.metric("Cheapest", _fmt_price(report.min_price))
    c5.metric("Most expensive", _fmt_price(report.max_price))
    c6.metric("P25 / P75", f"{_fmt_price(int(report.p25))} / {_fmt_price(int(report.p75))}")

    st.divider()

    # --- Listings table ---
    st.subheader("📋 All Listings")
    _render_listings_table(report.listings)

    st.divider()

    # --- AI analysis cards ---
    analyzed = [l for l in top if l.ai_report]
    if analyzed:
        st.subheader("🤖 AI Analysis Details")
        st.caption(f"The {len(analyzed)} cheapest listings were evaluated with Claude Vision.")

        db = Database()
        for l in analyzed:
            r = l.ai_report
            score = r.condition_score
            icon = "🟢" if score >= 8 else "🟡" if score >= 5 else "🔴"
            price_tag = _fmt_price(l.price_nok)
            price_score_tag = f"{l.price_score:+.1f}%" if l.price_score else ""

            city = l.location.split(",")[0].strip() if l.location else "Location unknown"
            cscore_tag = f"  |  ⭐ {l.composite_score}" if l.composite_score is not None else ""
            with st.expander(f"{icon} **{l.title}** — {price_tag}  |  📍 {city}  |  Condition: {score}/10{cscore_tag}  {price_score_tag}"):
                left, right = st.columns([3, 1])

                with left:
                    st.markdown(f"**Summary:** {r.summary}")

                    if r.red_flags:
                        st.markdown("**⚠️ Red Flags:**")
                        for flag in r.red_flags:
                            st.markdown(f"- {flag}")
                    else:
                        st.success("No clear issues detected.")

                with right:
                    if l.composite_score is not None:
                        st.metric("⭐ Deal Score", f"{l.composite_score}/100")
                    st.metric("Condition Score", f"{score}/10")
                    if r.battery_pct:
                        bat_color = "normal" if r.battery_pct >= 80 else "inverse"
                        st.metric("Battery", f"{r.battery_pct}%", delta_color=bat_color)
                    if l.price_score is not None:
                        st.metric("Market Diff", f"{l.price_score:+.1f}%")

                st.markdown(f"🔗 [Open listing →]({l.url})")

                # Price history — accumulates as the same query is re-scanned
                history = db.get_price_history(l.id, l.query)
                if len(history) >= 2:
                    st.markdown("**📈 Price History**")
                    hist_df = pd.DataFrame(history, columns=["Date", "Price (kr)"])
                    st.line_chart(hist_df.set_index("Date"))

                # Images (if any)
                if l.image_urls:
                    img_cols = st.columns(min(len(l.image_urls), 4))
                    for i, img_url in enumerate(l.image_urls[:4]):
                        with img_cols[i]:
                            try:
                                st.image(img_url, use_container_width=True)
                            except Exception:
                                pass
