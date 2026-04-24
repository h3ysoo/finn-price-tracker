"""İstatistiksel fiyat analizi: ortalama, medyan, std, percentile."""
from __future__ import annotations

import logging
import statistics
from typing import Iterable

from models import Listing, PriceReport

log = logging.getLogger(__name__)


def _percentile(sorted_values: list[float], p: float) -> float:
    """Basit lineer interpolasyonlu percentile (0<=p<=100)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return float(sorted_values[f])
    d0 = sorted_values[f] * (c - k)
    d1 = sorted_values[c] * (k - f)
    return float(d0 + d1)


def analyze_prices(listings: Iterable[Listing]) -> PriceReport:
    """
    Listing'lerin fiyatlarını özetle ve her ilana piyasa ortalamasına
    göre 'price_score' ata.

    price_score = (price - mean) / mean * 100
    → negatif = ortalamadan ucuz, pozitif = pahalı.
    """
    listings = list(listings)
    priced = [l for l in listings if l.price_nok and l.price_nok > 0]

    if not priced:
        log.warning("Fiyatlı ilan yok, boş rapor dönüyor.")
        query = listings[0].query if listings else ""
        return PriceReport(
            query=query,
            count=0,
            mean=0.0,
            median=0.0,
            std=0.0,
            min_price=0,
            max_price=0,
            p25=0.0,
            p75=0.0,
            listings=listings,
        )

    prices = sorted(float(l.price_nok) for l in priced)  # type: ignore[arg-type]
    mean = statistics.fmean(prices)
    median = statistics.median(prices)
    std = statistics.pstdev(prices) if len(prices) > 1 else 0.0

    report = PriceReport(
        query=priced[0].query,
        count=len(priced),
        mean=mean,
        median=median,
        std=std,
        min_price=int(prices[0]),
        max_price=int(prices[-1]),
        p25=_percentile(prices, 25),
        p75=_percentile(prices, 75),
        listings=listings,
    )

    # Her ilana score ver (fiyatsızlar None kalır)
    for l in listings:
        if l.price_nok and mean > 0:
            l.price_score = round((l.price_nok - mean) / mean * 100, 2)

    # Ucuzdan pahalıya sırala (None'ları sona at)
    report.listings = sorted(
        listings,
        key=lambda x: (x.price_score is None, x.price_score if x.price_score is not None else 0),
    )
    return report
