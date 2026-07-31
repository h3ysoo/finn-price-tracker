"""AI output sanitization: out-of-range numbers don't corrupt or drop reports."""
import asyncio
from types import SimpleNamespace

import analyzer.ai_analyzer as aa
from models import Listing


def _client(tool_input: dict):
    class FakeMessages:
        async def create(self, **kwargs):
            block = SimpleNamespace(type="tool_use", input=tool_input)
            return SimpleNamespace(content=[block], stop_reason="tool_use")

    return SimpleNamespace(messages=FakeMessages())


def _analyze(tool_input, monkeypatch):
    monkeypatch.setattr(aa, "ANTHROPIC_API_KEY", "test")
    listing = Listing(id="1", query="q", title="iPhone 13", url="u")
    return asyncio.run(aa.analyze_listing_ai(listing, client=_client(tool_input)))


def test_out_of_range_battery_becomes_none(monkeypatch):
    r = _analyze(
        {"condition_score": 8, "battery_pct": 150, "red_flags": [], "summary": "s"},
        monkeypatch,
    )
    assert r is not None
    assert r.battery_pct is None  # 150 is implausible → dropped
    assert r.condition_score == 8


def test_out_of_range_condition_is_clamped_not_dropped(monkeypatch):
    # condition_score 15 would fail AIReport's ge/le and previously lose the
    # whole report; it must be clamped to 10 and keep summary + flags.
    r = _analyze(
        {"condition_score": 15, "battery_pct": 90, "red_flags": ["Scratch"], "summary": "ok"},
        monkeypatch,
    )
    assert r is not None
    assert r.condition_score == 10
    assert r.battery_pct == 90
    assert r.red_flags == ["Scratch"]
    assert r.summary == "ok"


def test_valid_values_pass_through(monkeypatch):
    r = _analyze(
        {"condition_score": 7, "battery_pct": 84, "red_flags": ["a", "b"], "summary": "fine"},
        monkeypatch,
    )
    assert (r.condition_score, r.battery_pct, r.red_flags) == (7, 84, ["a", "b"])


def test_zero_battery_becomes_none(monkeypatch):
    r = _analyze(
        {"condition_score": 5, "battery_pct": 0, "red_flags": [], "summary": "s"},
        monkeypatch,
    )
    assert r.battery_pct is None
