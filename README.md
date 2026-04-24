# finn-price-tracker

Norveç 2. el pazarı **Finn.no** için fiyat analiz aracı.
İlanları scrape eder, istatistiksel fiyat analizi yapar ve en ucuz ilanları
**Claude Vision** ile fotoğraf + açıklama üzerinden değerlendirir.

## Özellikler

- `playwright` ile async scraping (pagination + rate limiting)
- İstatistiksel rapor: ortalama, medyan, std, min/max, P25/P75
- Her ilana piyasa ortalamasına göre yüzdelik **fiyat skoru**
- Claude `claude-sonnet-4-20250514` ile fotoğraf + açıklama analizi
- SQLite ile kalıcılık; `deals` komutuyla geçmişten en iyi fırsatlar
- `rich` ile renkli terminal çıktısı
- UTF-8 + Norveç karakterleri (æ ø å) uyumlu

## Kurulum

```bash
# 1. Sanal ortam
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Bağımlılıklar
pip install -r requirements.txt

# 3. Playwright tarayıcıları
playwright install chromium

# 4. API anahtarı
cp .env.example .env
# .env içine ANTHROPIC_API_KEY= anahtarını yaz
```

## Kullanım

```bash
# Arama + fiyat analizi + ilk 5'in AI analizi
python main.py search "iPhone 13 Pro Max 256GB"

# Sayfa sayısı / AI limiti değiştir
python main.py search "MacBook Pro M1" --pages 5 --ai-limit 3

# Tarayıcıyı görünür çalıştır (debug)
python main.py search "Sony WH-1000XM5" --show-browser

# DB'deki tüm kayıtlardan en iyi fırsatlar
python main.py deals --limit 20

# Verbose log
python main.py -v search "Canon EOS R6"
```

## Klasör Yapısı

```
finn-price-tracker/
├── main.py                  # CLI
├── config.py                # Ayarlar
├── scraper/finn_scraper.py  # Playwright scraper
├── analyzer/
│   ├── price_analyzer.py    # İstatistiksel analiz
│   └── ai_analyzer.py       # Claude Vision analizi
├── database/db.py           # SQLite CRUD
├── models/listing.py        # Pydantic modeller
├── data/listings.db         # (ilk çalıştırmada üretilir)
├── requirements.txt
├── .env.example
└── README.md
```

## Fiyat Skoru

```
price_score = (ilan_fiyatı - ortalama) / ortalama * 100
```

- `-20%` → piyasa ortalamasından %20 ucuz (iyi fırsat)
- `+15%` → piyasa ortalamasından %15 pahalı

## Notlar

- Finn.no yapısı değiştirirse `scraper/finn_scraper.py` içindeki CSS seçicileri
  güncellemek gerekebilir.
- AI analizi API maliyeti yaratır; varsayılan olarak sadece en ucuz 5 ilan için
  çalışır (`--ai-limit` ile değiştirilebilir).
- Finn.no Terms of Service'i ihlal etmemek için rate-limit çok düşürülmemeli.
