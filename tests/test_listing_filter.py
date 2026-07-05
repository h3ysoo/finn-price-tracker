from models import Listing
from scraper.listing_filter import filter_listings, is_relevant


def _listing(title, price=3000):
    return Listing(id="x", query="q", title=title, price_nok=price, url="u")


def test_accessories_filtered():
    assert not is_relevant(_listing("Deksel til iPhone 13"))
    assert not is_relevant(_listing("Lader til iPhone"))
    assert not is_relevant(_listing("Panzerglass skjermbeskytter"))


def test_buy_wanted_filtered():
    assert not is_relevant(_listing("Kjøper iPhone 13"))
    assert not is_relevant(_listing("Ønsker å kjøpe iPhone"))


def test_common_norwegian_words_not_filtered():
    # 'eller' ve 'salg' meşru ilanlarda geçer — filtrelenmemeli
    assert is_relevant(_listing("iPhone 13 128GB eller 256GB"))
    assert is_relevant(_listing("Salg av iPhone 14 Pro"))
    assert is_relevant(_listing("iPhone 13 Pro Max 256GB"))


def test_min_price():
    assert not is_relevant(_listing("iPhone 13", price=200), min_price=500)
    assert is_relevant(_listing("iPhone 13", price=800), min_price=500)
    # Fiyatsız ilan min_price ile elenmez
    assert is_relevant(_listing("iPhone 13", price=None), min_price=500)


def test_filter_listings_counts():
    items = [_listing("iPhone 13"), _listing("Deksel til iPhone")]
    assert [l.title for l in filter_listings(items)] == ["iPhone 13"]
