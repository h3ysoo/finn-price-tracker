"""Her ilana bileşik skor ata (0-100, yüksek = iyi fırsat)."""
from __future__ import annotations

import re
from typing import Optional

from models import Listing

_BATTERY_RE = re.compile(r"(\d{2,3})\s*%")

# Norveçce/İngilizce pozitif durum kelimeleri
_POSITIVE = [
    "pent brukt",       # hafif kullanılmış
    "god stand",        # iyi durumda
    "meget pent",       # çok güzel
    "som ny",           # yeni gibi
    "perfekt",
    "flott",            # harika
    "original eske",    # orijinal kutu
    "med eske",         # kutu var
    "kvittering",       # fiş/makbuz var
    "garanti",          # garanti var
    "komplett",         # eksiksiz
    "ubrukt",           # kullanılmamış
    "ny ",              # yeni (boşlukla — "ny batteri" vs)
    "batteri 1",        # batarya 1xx% (apple'ın yüksek kapasiteli batarya)
]

# Negatif durum kelimeleri
_NEGATIVE = [
    "knust",            # kırık
    "skadet",           # hasarlı
    "riper",            # çizikler
    "bulk",             # ezik
    "defekt",           # arızalı
    "feil",             # hata/sorun
    "selges som den er",# olduğu gibi satılıyor
    "ikke funksjonell", # çalışmıyor
    "krever reparasjon",# tamir gerekiyor
    "icloud",           # iCloud lock riski
    "activation lock",
    "brukt mye",        # çok kullanılmış
    "sprekk",           # çatlak
    "flekk",            # leke
]


def _extract_battery(text: str) -> Optional[int]:
    m = _BATTERY_RE.search(text)
    if m:
        v = int(m.group(1))
        if 50 <= v <= 100:   # mantıklı batarya aralığı
            return v
    return None


def compute_score(listing: Listing) -> float:
    """Tek bir ilan için 0-100 arası bileşik skor döndür."""
    text = (listing.title + " " + listing.description).lower()
    score = 0.0

    # ── 1. Fiyat bileşeni (max 40 puan) ────────────────────────────────
    # price_score: negatif = ucuz = iyi
    # -40% altı → 40 puan, 0% → 20 puan, +40% üstü → 0 puan
    if listing.price_score is not None:
        # doğrusal interpolasyon: [-60, +60] → [40, 0]
        raw = 20.0 - listing.price_score * (20.0 / 40.0)
        score += max(0.0, min(40.0, raw))
    else:
        score += 20.0  # fiyat yoksa nötr

    # ── 2. Batarya bileşeni (max 30 puan) ───────────────────────────────
    battery = _extract_battery(text)
    if battery is not None:
        # 100% → 30, 80% → 24, 70% → 21, 50% → 15
        score += battery * 0.30
    else:
        score += 18.0  # bilgi yoksa orta puan

    # ── 3. İçerik kalitesi (max 30 puan) ────────────────────────────────
    content = 15.0  # başlangıç puanı

    for word in _POSITIVE:
        if word in text:
            content += 2.5

    for word in _NEGATIVE:
        if word in text:
            content -= 6.0

    # Açıklama uzunluğu: dolu açıklama daha güvenilir
    desc_len = len(listing.description.strip())
    if desc_len > 200:
        content += 3.0
    elif desc_len > 80:
        content += 1.5
    elif desc_len == 0:
        content -= 4.0

    score += max(0.0, min(30.0, content))

    return round(min(100.0, score), 1)


def score_listings(listings: list[Listing]) -> list[Listing]:
    """Tüm ilanlara composite_score ata, skorla azalan sırada döndür."""
    for l in listings:
        l.composite_score = compute_score(l)
    listings.sort(key=lambda x: x.composite_score or 0, reverse=True)
    return listings
