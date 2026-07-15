"""Finn.no bap/forsale ilanlarını playwright ile çeker."""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Optional
from urllib.parse import urlencode, urljoin

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from config import (
    DEFAULT_PAGES,
    FINN_BASE_URL,
    FINN_SEARCH_PATH,
    REQUEST_DELAY_MAX,
    REQUEST_DELAY_MIN,
    USER_AGENT,
)
from models import Listing

log = logging.getLogger(__name__)

# Finn fiyatları Norveç formatında: "1 200 kr", "kr 1.200" vs.
_PRICE_RE = re.compile(r"(\d[\d\s\.]*)")

# Finn CDN boyut segmentleri — küçükten büyüğe öncelik sırası
_FINN_SIZE_RE = re.compile(r"/dynamic/\d+x\d+[a-z]*/")


def _upgrade_finn_image_url(url: str) -> str:
    """Finn CDN URL'sindeki küçük boyutu 960x720 ile değiştir."""
    return _FINN_SIZE_RE.sub("/dynamic/960x720c/", url, count=1)


def _parse_price(text: Optional[str]) -> Optional[int]:
    """'1 299 kr' gibi bir metinden int NOK üret."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace("\u00a0", "").replace(".", "")
    try:
        return int(raw)
    except ValueError:
        return None


def _extract_id_from_url(url: str) -> str:
    """Finn URL'sinden finnkode / id çek."""
    m = re.search(r"finnkode=(\d+)", url) or re.search(r"/(\d{6,})(?:[/?]|$)", url)
    return m.group(1) if m else url


class FinnScraper:
    """Asenkron Finn.no scraper."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._browser: Optional[Browser] = None
        self._ctx: Optional[BrowserContext] = None
        self._pw = None
        # Son arama sonuçların SONUNA ulaştı mı? (sayfa limiti dolmadan
        # yeni ilan gelmeyen bir sayfa görüldüyse True). Kısmi taramalarda
        # DB'deki ilanları yanlışlıkla "satıldı" işaretlememek için kullanılır.
        self.last_search_complete: bool = False

    async def __aenter__(self) -> "FinnScraper":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._ctx = await self._browser.new_context(
            user_agent=USER_AGENT,
            locale="nb-NO",
            viewport={"width": 1366, "height": 900},
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ctx:
            await self._ctx.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def _new_page(self) -> Page:
        assert self._ctx is not None
        return await self._ctx.new_page()

    @staticmethod
    async def _goto_with_retry(page: Page, url: str, attempts: int = 3) -> bool:
        """Sayfayı yükle; geçici hata/timeout'ta üstel bekleme ile tekrar dene."""
        for attempt in range(1, attempts + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return True
            except Exception as e:
                if attempt == attempts:
                    log.warning("Sayfa %d denemede yüklenemedi (%s): %s", attempts, url, e)
                    return False
                delay = 2 ** (attempt - 1) + random.uniform(0, 0.5)
                log.debug("goto başarısız (%d/%d), %.1fs sonra tekrar: %s",
                          attempt, attempts, delay, e)
                await asyncio.sleep(delay)
        return False

    @staticmethod
    def _build_url(query: str, page: int) -> str:
        params = {"q": query, "sort": "PUBLISHED_DESC"}
        if page > 1:
            params["page"] = str(page)
        return f"{FINN_BASE_URL}{FINN_SEARCH_PATH}?{urlencode(params)}"

    async def _parse_listings(self, page: Page, query: str) -> list[Listing]:
        """Arama sayfasındaki ilan kartlarını ayıkla."""
        # Cookie popup'ı varsa kapatmaya çalış (bloke etmesin diye best-effort)
        try:
            btn = await page.query_selector("button:has-text('Godta')")
            if btn:
                await btn.click(timeout=2000)
        except Exception:
            pass

        # Finn'in arama sonuçları article veya a[data-testid=...] olarak
        # render edilebiliyor. İkisini de dene.
        cards = await page.query_selector_all("article")
        if not cards:
            cards = await page.query_selector_all("a[id^='bap-']")

        out: list[Listing] = []
        for card in cards:
            try:
                listing = await self._extract_card(card, query)
                if listing:
                    out.append(listing)
            except Exception as e:
                log.warning("Kart parse edilemedi: %s", e)
                continue
        return out

    async def _extract_card(self, card, query: str) -> Optional[Listing]:
        """Tek bir ilan kartından Listing oluştur."""
        # Başlık + URL
        link = await card.query_selector("a[href*='/bap/'], a[href*='finnkode=']")
        if not link:
            link = await card.query_selector("a")
        if not link:
            return None

        href = await link.get_attribute("href") or ""
        if not href:
            return None
        url = urljoin(FINN_BASE_URL, href)
        listing_id = _extract_id_from_url(url)

        # Başlık
        title_el = await card.query_selector("h2, h3, [data-testid='ad-title']")
        title = (await title_el.inner_text()).strip() if title_el else ""
        if not title:
            title = (await link.inner_text()).strip()

        # Fiyat
        price_el = await card.query_selector(
            "[data-testid*='price'], .t3, span:has-text('kr')"
        )
        price_text = await price_el.inner_text() if price_el else None
        price = _parse_price(price_text)

        # Konum — birden fazla selector dene
        location: Optional[str] = None
        for loc_sel in [
            "[data-testid='location']",
            "[class*='location']",
            "[class*='address']",
            "span[class*='text-caption']",
            "div[class*='between'] span:last-child",
        ]:
            loc_el = await card.query_selector(loc_sel)
            if loc_el:
                txt = (await loc_el.inner_text()).strip()
                # Fiyat veya tarih değil, gerçek konum mu?
                if txt and not any(c.isdigit() for c in txt[:3]) and len(txt) > 2:
                    location = txt.split("\n")[0].strip()
                    break

        # Görsel
        img_el = await card.query_selector("img")
        image_urls: list[str] = []
        if img_el:
            src = (
                await img_el.get_attribute("src")
                or await img_el.get_attribute("data-src")
                or ""
            )
            if src:
                image_urls.append(src)

        # Açıklama snippet'i (bazen kartta olur)
        desc_el = await card.query_selector(
            "[data-testid='description'], p"
        )
        description = (await desc_el.inner_text()).strip() if desc_el else ""

        if not listing_id or not title:
            return None

        return Listing(
            id=listing_id,
            query=query,
            title=title,
            price_nok=price,
            url=url,
            location=location,
            image_urls=image_urls,
            description=description,
        )

    async def fetch_detail(self, listing: Listing) -> None:
        """İlan detay sayfasına gir, tam açıklamayı ve ek bilgileri çek."""
        page = await self._new_page()
        try:
            if not await self._goto_with_retry(page, listing.url):
                return
            await page.wait_for_timeout(1000)

            # --- Tam açıklama (beskrivelse) ---
            description = ""
            # Finn.no farklı sayfa yapılarında farklı selector kullanıyor
            for sel in [
                "[data-testid='description']",
                "[data-testid='ad-description-text']",
                "section:has(h2) div.whitespace-pre-wrap",
                ".whitespace-pre-wrap",
                "div[class*='description']",
            ]:
                el = await page.query_selector(sel)
                if el:
                    text = (await el.inner_text()).strip()
                    if len(text) > 20:
                        description = text
                        break

            # "Beskrivelse" başlığının altındaki metni de dene
            if not description:
                try:
                    heading = await page.query_selector("h2:text('Beskrivelse'), h3:text('Beskrivelse')")
                    if heading:
                        # Başlığın hemen arkasındaki sibling/parent içeriği al
                        description = await page.evaluate(
                            """el => {
                                const parent = el.closest('section') || el.parentElement;
                                return parent ? parent.innerText.replace(el.innerText, '').trim() : '';
                            }""",
                            heading,
                        )
                except Exception:
                    pass

            if description:
                listing.description = description

            # --- Konum (detay sayfasından daha güvenilir) ---
            if not listing.location:
                try:
                    for loc_sel in [
                        "[data-testid='object-address']",
                        "span[data-testid='location']",
                        # "Sted" (yer) etiketi — Finn yapılandırılmış veri satırı
                        "dt:text('Sted') + dd",
                        "th:text('Sted') ~ td",
                    ]:
                        el = await page.query_selector(loc_sel)
                        if el:
                            txt = (await el.inner_text()).strip()
                            if txt:
                                listing.location = txt.split("\n")[0].strip()
                                break

                    # Hâlâ bulunamadıysa sayfanın tamamından "Sted" satırını ara
                    if not listing.location:
                        listing.location = await page.evaluate("""() => {
                            const labels = [...document.querySelectorAll('dt, th, span, div')];
                            for (const el of labels) {
                                if (el.innerText && el.innerText.trim() === 'Sted') {
                                    const next = el.nextElementSibling;
                                    if (next) return next.innerText.trim().split('\\n')[0];
                                }
                            }
                            return null;
                        }""")
                except Exception:
                    pass

            # --- Galeri görselleri (yüksek çözünürlük, tümü) ---
            try:
                seen_urls: set[str] = set(listing.image_urls)
                gallery_urls: list[str] = []

                # Önce thumbnail'leri tıkla / lazy-load tetikle
                await page.evaluate(
                    "window.scrollBy(0, 400)"
                )
                await page.wait_for_timeout(600)

                # Finn CDN görselleri — birden fazla selector dene
                img_els = await page.query_selector_all(
                    "img[src*='finncdn.no'], img[src*='finn-images'], "
                    "img[data-src*='finncdn.no'], img[data-src*='finn-images']"
                )
                for img_el in img_els:
                    src = (
                        await img_el.get_attribute("src")
                        or await img_el.get_attribute("data-src")
                        or ""
                    )
                    if not src or src in seen_urls:
                        continue
                    # Küçük thumbnail ise (w<200) atla
                    try:
                        w = await img_el.evaluate("el => el.naturalWidth || el.width || 0")
                        if w and int(w) < 100:
                            continue
                    except Exception:
                        pass
                    # Finn CDN boyut parametresini yüksek çözünürlüğe yükselt
                    src = _upgrade_finn_image_url(src)
                    seen_urls.add(src)
                    gallery_urls.append(src)

                if gallery_urls:
                    # Mevcut listenin başına ekle (liste sayfasındaki küçük görseli sona at)
                    listing.image_urls = gallery_urls[:8]

            except Exception as e:
                log.debug("Görsel çekme hatası: %s", e)

        except Exception as e:
            log.warning("Detay sayfası çekilemedi (%s): %s", listing.id, e)
        finally:
            await page.close()

    async def enrich_listings(self, listings: list[Listing], limit: int) -> None:
        """İlk `limit` ilanın detay sayfasını çek (sıralı, rate-limit'li)."""
        targets = listings[:limit]
        log.info("Detay sayfaları çekiliyor (%d ilan)...", len(targets))
        for listing in targets:
            await self.fetch_detail(listing)
            await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

    async def enrich_all(self, listings: list[Listing], concurrency: int = 3) -> None:
        """Tüm ilanların detay sayfasını paralel çek.

        `concurrency` adet sayfa aynı anda açılır; bu sayede hız/礼儀 dengesi korunur.
        """
        log.info("Tüm ilanların detay sayfaları çekiliyor (%d ilan, %d paralel)...",
                 len(listings), concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        done = 0
        total = len(listings)

        async def _fetch_one(listing: Listing) -> None:
            nonlocal done
            async with semaphore:
                await self.fetch_detail(listing)
                done += 1
                log.info("Detay: %d/%d — %s", done, total, listing.title[:50])
                await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

        await asyncio.gather(*(_fetch_one(l) for l in listings))

    async def search(self, query: str, pages: int = DEFAULT_PAGES) -> list[Listing]:
        """Verilen arama terimi için birden fazla sayfa tara."""
        if self._browser is None:
            raise RuntimeError("FinnScraper 'async with' ile kullanılmalı.")

        all_listings: list[Listing] = []
        seen_ids: set[str] = set()
        self.last_search_complete = False
        page = await self._new_page()

        try:
            for page_num in range(1, pages + 1):
                url = self._build_url(query, page_num)
                log.info("Sayfa %d: %s", page_num, url)
                if not await self._goto_with_retry(page, url):
                    continue
                await page.wait_for_timeout(1500)  # lazy render için

                items = await self._parse_listings(page, query)
                new_count = 0
                for item in items:
                    if item.id in seen_ids:
                        continue
                    seen_ids.add(item.id)
                    all_listings.append(item)
                    new_count += 1

                log.info("Sayfa %d → %d yeni ilan", page_num, new_count)

                if new_count == 0 and page_num > 1:
                    # yeni bir şey yoksa sonuçların sonuna gelinmiştir
                    self.last_search_complete = True
                    break

                # Rate limit
                delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
                await asyncio.sleep(delay)
        finally:
            await page.close()

        return all_listings


async def scrape_finn(
    query: str,
    pages: int = DEFAULT_PAGES,
    headless: bool = True,
    enrich_limit: int = 0,
) -> list[Listing]:
    """Kısayol: scraper'ı tek seferlik kullan.

    enrich_limit > 0 ise ilk N ilanın detay sayfası da çekilir.
    """
    async with FinnScraper(headless=headless) as s:
        listings = await s.search(query, pages=pages)
        if enrich_limit > 0 and listings:
            await s.enrich_listings(listings, limit=enrich_limit)
        return listings
