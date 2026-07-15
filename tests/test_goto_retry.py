import asyncio

from scraper.finn_scraper import FinnScraper


class FlakyPage:
    """İlk `fail_times` çağrıda hata fırlatan sahte Playwright sayfası."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def goto(self, url, wait_until=None, timeout=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TimeoutError(f"simulated timeout #{self.calls}")


def test_retry_succeeds_after_transient_failures(monkeypatch):
    _no_sleep(monkeypatch)
    page = FlakyPage(fail_times=2)
    assert asyncio.run(FinnScraper._goto_with_retry(page, "https://x", attempts=3))
    assert page.calls == 3


def test_retry_gives_up_after_max_attempts(monkeypatch):
    _no_sleep(monkeypatch)
    page = FlakyPage(fail_times=99)
    assert not asyncio.run(FinnScraper._goto_with_retry(page, "https://x", attempts=3))
    assert page.calls == 3


def test_no_retry_needed():
    page = FlakyPage(fail_times=0)
    assert asyncio.run(FinnScraper._goto_with_retry(page, "https://x"))
    assert page.calls == 1


def _no_sleep(monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)
