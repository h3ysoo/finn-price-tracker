"""URL allowlist: only Finn.no over http(s); block SSRF/malicious targets."""
import asyncio
from types import SimpleNamespace

import pytest

import analyzer.ai_analyzer as aa
from netutil import is_allowed_image_url, safe_finn_listing_url


# --- image URL allowlist (SSRF guard) -----------------------------------------

@pytest.mark.parametrize("url", [
    "https://images.finncdn.no/dynamic/960x720c/2026/1/foo.jpg",
    "https://finncdn.no/x.jpg",
    "https://www.finn.no/img/y.png",
])
def test_allowed_images(url):
    assert is_allowed_image_url(url) is True


@pytest.mark.parametrize("url", [
    "http://images.finncdn.no/x.jpg",              # not https
    "https://169.254.169.254/latest/meta-data/",   # cloud metadata (IP)
    "https://127.0.0.1/x",                          # localhost IP
    "https://localhost/x",                          # localhost name
    "https://evil.com/x.jpg",                       # off-domain
    "https://finn.no.evil.com/x.jpg",               # suffix-bypass attempt
    "https://notfinn.no/x.jpg",                     # near-miss host
    "file:///etc/passwd",                           # file scheme
    "data:image/png;base64,AAAA",                   # data URI
    "javascript:alert(1)",
    "",
])
def test_blocked_images(url):
    assert is_allowed_image_url(url) is False


# --- listing URL validation ---------------------------------------------------

def test_valid_listing_urls():
    u = "https://www.finn.no/recommerce/forsale/item/400111222"
    assert safe_finn_listing_url(u) == u
    assert safe_finn_listing_url("http://finn.no/x") == "http://finn.no/x"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "data:text/html,<script>",
    "https://evil.com/phish",
    "https://finn.no.evil.com/x",
    "ftp://finn.no/x",
])
def test_rejected_listing_urls(url):
    assert safe_finn_listing_url(url) is None


# --- the downloader actually enforces the allowlist ---------------------------

def test_download_image_blocks_ssrf(monkeypatch):
    monkeypatch.setattr(aa, "ANTHROPIC_API_KEY", "test")

    # If the guard fails, aiohttp would be used; make it explode to prove
    # no network call happens for a blocked URL.
    def boom(*a, **k):
        raise AssertionError("network call attempted for a blocked URL")

    monkeypatch.setattr(aa.aiohttp, "ClientSession", boom)
    result = asyncio.run(aa._download_image("https://169.254.169.254/meta"))
    assert result is None
