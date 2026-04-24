"""Ortak veri modelleri."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AIReport(BaseModel):
    """Claude Vision analizi çıktısı."""

    condition_score: int = Field(..., ge=1, le=10)
    red_flags: list[str] = Field(default_factory=list)
    summary: str = ""


class Listing(BaseModel):
    """Finn.no'dan çekilen tek bir ilan."""

    id: str
    query: str
    title: str
    price_nok: Optional[int] = None
    url: str
    location: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    image_urls: list[str] = Field(default_factory=list)
    description: str = ""
    price_score: Optional[float] = None  # piyasa ortalamasına göre % (negatif = ucuz)
    ai_report: Optional[AIReport] = None


class PriceReport(BaseModel):
    """Bir arama sonucuna ait istatistiksel özet."""

    query: str
    count: int
    mean: float
    median: float
    std: float
    min_price: int
    max_price: int
    p25: float
    p75: float
    listings: list[Listing] = Field(default_factory=list)
