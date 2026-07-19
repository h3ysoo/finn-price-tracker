from scraper.finn_scraper import _extract_id_from_url, _parse_price, _upgrade_finn_image_url


def test_parse_price_norwegian_formats():
    assert _parse_price("1 299 kr") == 1299
    assert _parse_price("kr 1.200") == 1200
    assert _parse_price("5 500 kr") == 5500
    assert _parse_price("Gis bort") is None
    assert _parse_price(None) is None
    assert _parse_price("") is None


def test_extract_id_from_url():
    assert _extract_id_from_url("https://www.finn.no/bap/forsale/ad.html?finnkode=123456789") == "123456789"
    assert _extract_id_from_url("https://www.finn.no/recommerce/forsale/item/987654321") == "987654321"
    # If no ID is found, the URL itself is returned (fallback)
    assert _extract_id_from_url("https://www.finn.no/foo") == "https://www.finn.no/foo"


def test_upgrade_finn_image_url():
    url = "https://images.finncdn.no/dynamic/220x220c/2026/1/foo.jpg"
    assert _upgrade_finn_image_url(url) == "https://images.finncdn.no/dynamic/960x720c/2026/1/foo.jpg"
    # If the size segment is absent, the URL is returned unchanged
    plain = "https://images.finncdn.no/static/foo.jpg"
    assert _upgrade_finn_image_url(plain) == plain
