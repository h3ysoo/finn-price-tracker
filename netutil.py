"""URL safety helpers — keep the scraper and image downloader on Finn.no only.

Listing HTML is untrusted input. Without an allowlist, a crafted listing
could point an <img> at an internal address (cloud metadata, localhost,
a private IP) and we would fetch it and forward the bytes to Claude — an
SSRF plus data-exfiltration path. These helpers restrict both listing
URLs and downloadable images to the Finn.no domain family over http(s).
"""
from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlparse

# Finn listings live on *.finn.no; their images on *.finncdn.no.
_ALLOWED_EXACT_HOSTS = {"finn.no", "finncdn.no"}
_ALLOWED_HOST_SUFFIXES = (".finn.no", ".finncdn.no")


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().strip()
    if not host:
        return False
    # Reject IP literals outright (blocks 169.254.169.254, 127.0.0.1, ::1, …)
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if host in _ALLOWED_EXACT_HOSTS:
        return True
    # Leading dot in the suffix prevents 'finn.no.evil.com' bypasses.
    return any(host.endswith(suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def is_allowed_image_url(url: str) -> bool:
    """True only for https images on the Finn CDN domain family."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme != "https":
        return False
    return _host_allowed(p.hostname or "")


def safe_finn_listing_url(url: str) -> Optional[str]:
    """Return the URL if it is an http(s) Finn.no listing URL, else None.

    Blocks javascript:, data:, file: and any off-domain / IP-literal host.
    """
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.scheme not in ("http", "https"):
        return None
    if not _host_allowed(p.hostname or ""):
        return None
    return url
