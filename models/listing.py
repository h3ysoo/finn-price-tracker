"""Shared data models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class AIReport(BaseModel):
    """Claude Vision analysis output."""

    condition_score: int = Field(..., ge=1, le=10)
    battery_pct: Optional[int] = None
    red_flags: list[str] = Field(default_factory=list)
    summary: str = ""


class Listing(BaseModel):
    """A single listing scraped from Finn.no."""

    id: str
    query: str
    title: str
    price_nok: Optional[int] = None
    url: str
    location: Optional[str] = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    image_urls: list[str] = Field(default_factory=list)
    description: str = ""
    price_score: Optional[float] = None      # % relative to market median (negative = cheap)
    composite_score: Optional[float] = None  # composite deal score 0-100 (higher = better)
    ai_report: Optional[AIReport] = None


class PriceReport(BaseModel):
    """Statistical summary of a single search's results."""

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
