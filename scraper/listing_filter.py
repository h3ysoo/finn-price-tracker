"""Alakasız ilanları filtrele (aksesuar, kutu, satın alma isteği vb.)."""
from __future__ import annotations

import re
import logging
from typing import Optional

from models import Listing

log = logging.getLogger(__name__)

# Norveçce + İngilizce — telefon ilanı OLMAYAN kategorileri işaret eden kelimeler
_BLACKLIST_PATTERNS: list[str] = [
    # Kutular / ambalaj
    r"\beske[rn]?\b",
    r"\bboks\b",
    r"\bomvisning\b",
    # Kılıf / ekran koruyucu / aksesuar
    r"\bdeksel\b",
    r"\betui\b",
    r"\bcover\b",
    r"\bpanzerglass\b",
    r"\bskjermfilm\b",
    r"\bskjermbeskytter\b",
    r"\bpopgrip\b",
    r"\bpopsocket\b",
    r"\bstativ\b",
    # Şarj / kablo
    r"\blader[en]?\b",
    r"\bladekabel\b",
    r"\bcharger\b",
    r"\bkabel\b",
    # Satın alma isteği (alıcı ilanı)
    r"\bkjøper\b",
    r"\bønsker å kjøpe\b",
    r"\bønsker å kjøp\b",
    r"\bsøker\b",
    r"\bwanted\b",
    r"\bvil kjøpe\b",
    r"\bhenter på dagen\b",
    # Parça / tamir
    r"\bdeler\b",
    r"\breparasjon\b",
    r"\brepair\b",
    r"\bknust\b",  # kırık (ekran kırığı genellikle "knust skjerm" olarak geçer ama başlık bazında çok geniş)
    # Genel aksesuar
    r"\btilbehør\b",
    r"\baccessories\b",
    # Ticari / toplu satış sinyalleri
    r"\boppover\b",            # "13 & oppover" = model aralığı
]

_BLACKLIST_RE = re.compile("|".join(_BLACKLIST_PATTERNS), re.IGNORECASE)


def is_relevant(listing: Listing, min_price: Optional[int] = None) -> bool:
    """True → ilan muhtemelen gerçek bir cihaz satışı."""
    # Kara liste kontrolü — bilerek sadece başlık: açıklamada "lader følger med"
    # (şarj aleti dahil) gibi meşru ifadeler yanlış pozitif üretir
    if _BLACKLIST_RE.search(listing.title):
        log.debug("Filtrelendi (kara liste): %s", listing.title)
        return False

    # Minimum fiyat kontrolü
    if min_price is not None and listing.price_nok is not None:
        if listing.price_nok < min_price:
            log.debug("Filtrelendi (düşük fiyat %d kr): %s", listing.price_nok, listing.title)
            return False

    return True


def filter_listings(
    listings: list[Listing],
    min_price: Optional[int] = None,
) -> list[Listing]:
    """Listeyi filtrele, kaç ilan atlandığını logla."""
    before = len(listings)
    filtered = [l for l in listings if is_relevant(l, min_price=min_price)]
    removed = before - len(filtered)
    if removed:
        log.info(
            "Filtre: %d alakasız ilan çıkarıldı (%d → %d kaldı).",
            removed,
            before,
            len(filtered),
        )
    return filtered
