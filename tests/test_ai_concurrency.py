import asyncio
from types import SimpleNamespace

import analyzer.ai_analyzer as aa
from models import Listing


class ConcurrencyProbe:
    """Aynı anda kaç create() çağrısı uçtuğunu ölçen sahte client."""

    def __init__(self):
        self.current = 0
        self.peak = 0
        self.total = 0
        self.messages = self

    async def create(self, **kwargs):
        self.current += 1
        self.peak = max(self.peak, self.current)
        self.total += 1
        await asyncio.sleep(0.01)  # istekler örtüşsün
        self.current -= 1
        block = SimpleNamespace(type="tool_use", input={
            "condition_score": 7, "battery_pct": None, "red_flags": [], "summary": "ok",
        })
        return SimpleNamespace(content=[block], stop_reason="tool_use")


def _listings(n):
    return [
        Listing(id=str(i), query="q", title=f"item {i}", url=f"u{i}")
        for i in range(n)
    ]


def test_concurrency_is_bounded(monkeypatch):
    monkeypatch.setattr(aa, "ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(aa, "AI_CONCURRENCY", 3)
    probe = ConcurrencyProbe()

    listings = _listings(10)
    asyncio.run(aa.analyze_top_listings(listings, limit=10, client=probe))

    assert probe.total == 10
    assert probe.peak <= 3
    assert all(l.ai_report is not None for l in listings)
    assert listings[0].ai_report.condition_score == 7


def test_limit_respected(monkeypatch):
    monkeypatch.setattr(aa, "ANTHROPIC_API_KEY", "test")
    probe = ConcurrencyProbe()
    listings = _listings(8)
    asyncio.run(aa.analyze_top_listings(listings, limit=4, client=probe))
    assert probe.total == 4
    assert sum(1 for l in listings if l.ai_report) == 4
