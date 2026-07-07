"""Finn.no Telefon Fiyat Analizi — Streamlit arayüzü."""
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

# Proje kök dizinini path'e ekle
sys.path.insert(0, str(Path(__file__).parent))

from analyzer import analyze_prices, analyze_top_listings, select_candidates, score_listings
from config import AI_ANALYSIS_LIMIT, LISTING_MIN_PRICE
from database import Database
from models import Listing, PriceReport
from scraper import FinnScraper, filter_listings

# ---------------------------------------------------------------------------
# Yardımcılar
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
# Scraper pipeline (sync wrapper — Streamlit'te asyncio.run ile çalıştır)
# ---------------------------------------------------------------------------

async def _pipeline(
    query: str,
    pages: int,
    ai_limit: int,
    min_price: int,
) -> tuple[Optional[PriceReport], list[Listing]]:
    async with FinnScraper(headless=True) as scraper:
        listings = await scraper.search(query, pages=pages)
        if not listings:
            return None, []

        listings = filter_listings(listings, min_price=min_price)
        if not listings:
            return None, []

        # Tüm ilanların detay sayfasına gir (paralel)
        await scraper.enrich_all(listings, concurrency=3)

        # Fiyat analizi (price_score hesapla)
        report = analyze_prices(listings)

        # Bileşik skor: fiyat + batarya + içerik kalitesi
        score_listings(listings)

        # En yüksek skorlu adayları AI ile analiz et
        top = select_candidates(report, limit=ai_limit)
        if top:
            await analyze_top_listings(top, limit=ai_limit)

        # Sonuçları kalıcılaştır — fiyat geçmişi de burada birikir
        Database().save_listings(report.listings)

        return report, top


def run_pipeline(query, pages, ai_limit, min_price):
    # asyncio.run Windows'ta da varsayılan olarak ProactorEventLoop kullanır (3.8+)
    return asyncio.run(_pipeline(query, pages, ai_limit, min_price))


# ---------------------------------------------------------------------------
# Sayfa yapılandırması
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Finn Telefon Tracker",
    page_icon="📱",
    layout="wide",
)

st.title("📱 Finn.no — İkinci El Telefon Analizi")
st.caption("Finn.no ilanlarını tara, fiyatları karşılaştır, AI ile en iyi fırsatı bul.")

# ---------------------------------------------------------------------------
# Kenar çubuğu (ayarlar)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Arama Ayarları")
    pages = st.slider("Taranacak sayfa sayısı", 1, 10, 3)
    ai_limit = st.slider("AI analiz edilecek ilan sayısı", 1, 10, 5,
                         help="En ucuz N ilan Claude ile detaylı analiz edilir.")
    min_price = st.number_input("Minimum fiyat (kr)", 100, 10000, LISTING_MIN_PRICE, step=100,
                                help="Bu fiyatın altındaki ilanlar (aksesuar, kutu vb.) filtrelenir.")

    st.divider()
    st.markdown("**Nasıl çalışır?**")
    st.markdown("""
1. Finn.no'da arama yapılır
2. Aksesuar/kutu ilanları filtrelenir
3. Piyasa fiyatı hesaplanır
4. En ucuz ilanların detay sayfaları okunur
5. Claude fotoğraf + açıklamayı analiz eder
""")

# ---------------------------------------------------------------------------
# Arama kutusu
# ---------------------------------------------------------------------------

col_q, col_btn = st.columns([5, 1])
with col_q:
    query = st.text_input(
        "Telefon modeli",
        placeholder="iPhone 13 Pro Max 256GB",
        label_visibility="collapsed",
    )
with col_btn:
    search = st.button("🔍 Ara", use_container_width=True, type="primary")

# ---------------------------------------------------------------------------
# Fiyatı düşen ilanlar (arama yapılmadığında ana ekranda göster)
# ---------------------------------------------------------------------------

if not (search and query.strip()):
    drops = Database().get_price_drops(limit=10)
    if drops:
        st.subheader("📉 Fiyatı Düşen İlanlar")
        st.caption("Önceki taramalara göre fiyatı düşen ve hâlâ yayında olan ilanlar.")
        drop_rows = []
        for l, prev_price in drops:
            pct = (prev_price - (l.price_nok or 0)) / prev_price * 100
            drop_rows.append({
                "Başlık": l.title,
                "Eski Fiyat": _fmt_price(prev_price),
                "Yeni Fiyat": _fmt_price(l.price_nok),
                "Düşüş": f"-{pct:.1f}%",
                "Link": l.url,
            })
        st.dataframe(
            pd.DataFrame(drop_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="Aç →"),
            },
        )

# ---------------------------------------------------------------------------
# Arama & sonuçlar
# ---------------------------------------------------------------------------

if search and query.strip():
    with st.spinner(f"'{query}' için Finn.no taranıyor... Tüm ilanların detay sayfaları okunuyor ve AI analizi yapılıyor. Bu 2-4 dakika sürebilir, lütfen bekleyin."):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(run_pipeline, query.strip(), pages, ai_limit, min_price)
            try:
                report, top = future.result()
            except Exception as e:
                st.error(f"Hata oluştu: {e}")
                st.stop()

    if report is None or report.count == 0:
        st.warning("Hiç uygun ilan bulunamadı. Farklı bir model veya daha düşük minimum fiyat deneyin.")
        st.stop()

    # --- İstatistik kartları ---
    st.subheader("📊 Piyasa Özeti")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("İlan", report.count)
    c2.metric("Ortalama", _fmt_price(int(report.mean)))
    c3.metric("Medyan", _fmt_price(int(report.median)))
    c4.metric("En ucuz", _fmt_price(report.min_price))
    c5.metric("En pahalı", _fmt_price(report.max_price))
    c6.metric("P25 / P75", f"{_fmt_price(int(report.p25))} / {_fmt_price(int(report.p75))}")

    st.divider()

    # --- İlanlar tablosu ---
    st.subheader("📋 Tüm İlanlar")

    # Tabloyu composite_score'a göre sırala (yüksekten düşüğe)
    sorted_listings = sorted(
        report.listings,
        key=lambda x: x.composite_score or 0,
        reverse=True,
    )

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
            "Skor": score_label,
            "📍 Şehir": city,
            "Başlık": l.title,
            "Fiyat (kr)": l.price_nok or 0,
            "Piyasa Farkı": f"{l.price_score:+.1f}%" if l.price_score is not None else "—",
            "Depolama": storage or "—",
            "Batarya": f"{battery}%" if battery else "—",
            "AI Durum": f"{l.ai_report.condition_score}/10" if l.ai_report else "—",
            "Link": l.url,
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Link": st.column_config.LinkColumn("Link", display_text="Aç →"),
            "Fiyat (kr)": st.column_config.NumberColumn(format="%d kr"),
            "Skor": st.column_config.TextColumn("⭐ Skor", width="small"),
            "📍 Şehir": st.column_config.TextColumn(width="small"),
        },
        column_order=["Skor", "📍 Şehir", "Başlık", "Fiyat (kr)", "Piyasa Farkı", "Depolama", "Batarya", "AI Durum", "Link"],
    )

    st.divider()

    # --- AI analiz kartları ---
    analyzed = [l for l in top if l.ai_report]
    if analyzed:
        st.subheader("🤖 AI Analiz Detayları")
        st.caption(f"En ucuz {len(analyzed)} ilan Claude Vision ile değerlendirildi.")

        for l in analyzed:
            r = l.ai_report
            score = r.condition_score
            icon = "🟢" if score >= 8 else "🟡" if score >= 5 else "🔴"
            price_tag = _fmt_price(l.price_nok)
            price_score_tag = f"{l.price_score:+.1f}%" if l.price_score else ""

            city = l.location.split(",")[0].strip() if l.location else "Konum bilinmiyor"
            cscore_tag = f"  |  ⭐ {l.composite_score}" if l.composite_score is not None else ""
            with st.expander(f"{icon} **{l.title}** — {price_tag}  |  📍 {city}  |  Durum: {score}/10{cscore_tag}  {price_score_tag}"):
                left, right = st.columns([3, 1])

                with left:
                    st.markdown(f"**Özet:** {r.summary}")

                    if r.red_flags:
                        st.markdown("**⚠️ Uyarılar:**")
                        for flag in r.red_flags:
                            st.markdown(f"- {flag}")
                    else:
                        st.success("Belirgin bir sorun tespit edilmedi.")

                with right:
                    if l.composite_score is not None:
                        st.metric("⭐ Fırsat Skoru", f"{l.composite_score}/100")
                    st.metric("Durum Skoru", f"{score}/10")
                    if r.battery_pct:
                        bat_color = "normal" if r.battery_pct >= 80 else "inverse"
                        st.metric("Batarya", f"%{r.battery_pct}", delta_color=bat_color)
                    if l.price_score is not None:
                        st.metric("Piyasa Farkı", f"{l.price_score:+.1f}%")

                st.markdown(f"🔗 [İlana git →]({l.url})")

                # Görseller (varsa)
                if l.image_urls:
                    img_cols = st.columns(min(len(l.image_urls), 4))
                    for i, img_url in enumerate(l.image_urls[:4]):
                        with img_cols[i]:
                            try:
                                st.image(img_url, use_container_width=True)
                            except Exception:
                                pass
