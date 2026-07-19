"""Assign each listing a composite score (0-100, higher = better deal)."""
from __future__ import annotations

import re
from typing import Optional

from models import Listing

_BATTERY_RE = re.compile(r"(\d{2,3})\s*%")

# Norwegian/English positive-condition keywords
_POSITIVE = [
    "pent brukt",       # lightly used
    "god stand",        # in good condition
    "meget pent",       # very nice
    "som ny",           # like new
    "perfekt",
    "flott",            # great
    "original eske",    # original box
    "med eske",         # with box
    "kvittering",       # receipt included
    "garanti",          # warranty
    "komplett",         # complete
    "ubrukt",           # unused
    "ny ",              # new (with space — avoid "ny batteri" etc.)
    "batteri 1",        # battery 1xx% (Apple's high-capacity battery)
]

# Negative-condition keywords
_NEGATIVE = [
    "knust",            # broken
    "skadet",           # damaged
    "riper",            # scratches
    "bulk",             # dent
    "defekt",           # defective
    "feil",             # fault/issue
    "selges som den er",# sold as-is
    "ikke funksjonell", # not working
    "krever reparasjon",# needs repair
    "icloud",           # iCloud lock risk
    "activation lock",
    "brukt mye",        # heavily used
    "sprekk",           # crack
    "flekk",            # stain
]


def _extract_battery(text: str) -> Optional[int]:
    m = _BATTERY_RE.search(text)
    if m:
        v = int(m.group(1))
        if 50 <= v <= 100:   # plausible battery range
            return v
    return None


def compute_score(listing: Listing) -> float:
    """Return a composite score in [0, 100] for a single listing."""
    text = (listing.title + " " + listing.description).lower()
    score = 0.0

    # ── 1. Price component (max 40 points) ────────────────────────────────
    # price_score: negative = cheap = good
    # below -40% → 40 pts, 0% → 20 pts, above +40% → 0 pts
    if listing.price_score is not None:
        # linear interpolation: [-60, +60] → [40, 0]
        raw = 20.0 - listing.price_score * (20.0 / 40.0)
        score += max(0.0, min(40.0, raw))
    else:
        score += 20.0  # no price → neutral

    # ── 2. Battery component (max 30 points) ──────────────────────────────
    battery = _extract_battery(text)
    if battery is not None:
        # 100% → 30, 80% → 24, 70% → 21, 50% → 15
        score += battery * 0.30
    else:
        score += 18.0  # no info → middling score

    # ── 3. Content quality (max 30 points) ────────────────────────────────
    content = 15.0  # starting score

    for word in _POSITIVE:
        if word in text:
            content += 2.5

    for word in _NEGATIVE:
        if word in text:
            content -= 6.0

    # Description length: fuller descriptions are more trustworthy
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
    """Assign composite_score to every listing, return them in descending order."""
    for l in listings:
        l.composite_score = compute_score(l)
    listings.sort(key=lambda x: x.composite_score or 0, reverse=True)
    return listings
