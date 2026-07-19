"""Filter out irrelevant listings (accessories, boxes, buy-wanted ads, etc.)."""
from __future__ import annotations

import re
import logging
from typing import Optional

from models import Listing

log = logging.getLogger(__name__)

# Norwegian + English — keywords marking categories that are NOT actual phone listings
_BLACKLIST_PATTERNS: list[str] = [
    # Boxes / packaging
    r"\beske[rn]?\b",
    r"\bboks\b",
    r"\bomvisning\b",
    # Cases / screen protectors / accessories
    r"\bdeksel\b",
    r"\betui\b",
    r"\bcover\b",
    r"\bpanzerglass\b",
    r"\bskjermfilm\b",
    r"\bskjermbeskytter\b",
    r"\bpopgrip\b",
    r"\bpopsocket\b",
    r"\bstativ\b",
    # Chargers / cables
    r"\blader[en]?\b",
    r"\bladekabel\b",
    r"\bcharger\b",
    r"\bkabel\b",
    # Buy-wanted ads (buyer, not seller)
    r"\bkjøper\b",
    r"\bønsker å kjøpe\b",
    r"\bønsker å kjøp\b",
    r"\bsøker\b",
    r"\bwanted\b",
    r"\bvil kjøpe\b",
    r"\bhenter på dagen\b",
    # Parts / repair
    r"\bdeler\b",
    r"\breparasjon\b",
    r"\brepair\b",
    r"\bknust\b",  # broken (screen cracks usually say "knust skjerm"; too broad at title level)
    # Generic accessory terms
    r"\btilbehør\b",
    r"\baccessories\b",
    # Commercial / bulk-sale signals
    r"\boppover\b",            # "13 & oppover" = model range
]

_BLACKLIST_RE = re.compile("|".join(_BLACKLIST_PATTERNS), re.IGNORECASE)


def is_relevant(listing: Listing, min_price: Optional[int] = None) -> bool:
    """True → the listing is likely a real device for sale."""
    # Blacklist check — title only, on purpose: descriptions often say
    # "lader følger med" (charger included) and other legit phrases that
    # would produce false positives.
    if _BLACKLIST_RE.search(listing.title):
        log.debug("Filtered (blacklist): %s", listing.title)
        return False

    # Minimum price check
    if min_price is not None and listing.price_nok is not None:
        if listing.price_nok < min_price:
            log.debug("Filtered (low price %d kr): %s", listing.price_nok, listing.title)
            return False

    return True


def filter_listings(
    listings: list[Listing],
    min_price: Optional[int] = None,
) -> list[Listing]:
    """Filter the list, log how many were dropped."""
    before = len(listings)
    filtered = [l for l in listings if is_relevant(l, min_price=min_price)]
    removed = before - len(filtered)
    if removed:
        log.info(
            "Filter: removed %d irrelevant listings (%d → %d remaining).",
            removed,
            before,
            len(filtered),
        )
    return filtered
