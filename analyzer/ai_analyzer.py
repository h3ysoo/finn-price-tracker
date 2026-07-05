"""Claude Vision + metin analizi ile ilan değerlendirmesi."""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

import aiohttp
from anthropic import AsyncAnthropic

from config import AI_ANALYSIS_LIMIT, ANTHROPIC_API_KEY, CLAUDE_MODEL, USER_AGENT
from models import AIReport, Listing

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen, Finn.no (Norveç 2. el pazarı) ilanlarını değerlendiren bir uzmansın.
Sana ilanın birden fazla fotoğrafı ve tam açıklaması verilecek.

Görevin:
1. TÜM fotoğrafları dikkatlice incele. Gördüğün gerçek durumu yaz — fotoğrafta olan bir şeyi "eksik" olarak işaretleme.
2. Ürün bir telefon/tablet/laptop ise şu noktalara odaklan:
   - EKRAN: çizik, kırık, yanma (burn-in), dead pixel var mı?
   - KASA/ARKA KAPAK: hasar, çizik, bükülme var mı?
   - KAMERA: lens çizik/kırık mı?
   - BATARYA: açıklamada veya ekran fotoğrafında batarya % yazıyor mu? Kaç?
   - AKSESUAR: kutu, şarj aleti, kablo var mı?
   - KİLİT: iCloud/Google hesabı kilidi riski var mı?
   Başka bir ürün kategorisiyse (kulaklık, kamera, konsol vb.) o kategoriye uygun
   aşınma, hasar ve eksik parça kriterlerine göre değerlendir; batarya bilgisi
   anlamlı değilse null bırak.
3. Açıklamada belirtilen bilgileri (batarya %, garanti, hasar) doğrula. Çelişki varsa belirt.
4. Red flag listele — SADECE gerçekten sorunlu olanları, fazla abartma.

Sonucu report_listing_assessment aracıyla raporla.
"""

# Zorunlu tool çağrısı — Claude'un çıktısını şemaya kilitler, JSON parse hatasını yok eder
AI_REPORT_TOOL = {
    "name": "report_listing_assessment",
    "description": "İkinci el ilan değerlendirme sonucunu yapılandırılmış olarak raporla.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "condition_score": {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                "description": "Ürünün genel durumu, 1 (çok kötü) - 10 (yeni gibi)",
            },
            "battery_pct": {
                "type": ["integer", "null"],
                "description": "Batarya sağlığı yüzdesi; bilinmiyorsa veya anlamsızsa null",
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Gerçekten sorunlu noktalar (Türkçe)",
            },
            "summary": {
                "type": "string",
                "description": "2-3 cümlelik Türkçe özet",
            },
        },
        "required": ["condition_score", "battery_pct", "red_flags", "summary"],
        "additionalProperties": False,
    },
}


async def _download_image(url: str) -> Optional[tuple[bytes, str]]:
    """Görseli indir, (bytes, media_type) döndür."""
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    log.warning("Görsel indirilemedi (%s): %s", resp.status, url)
                    return None
                data = await resp.read()
                ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                if ct not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                    ct = "image/jpeg"
                return data, ct
    except Exception as e:
        log.warning("Görsel indirme hatası: %s", e)
        return None


async def analyze_listing_ai(
    listing: Listing,
    client: Optional[AsyncAnthropic] = None,
) -> Optional[AIReport]:
    """Tek bir ilan için Claude Vision analizi yap."""
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY yok, AI analizi atlandı.")
        return None

    client = client or AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    content: list[dict] = []

    # Tüm görselleri paralel indir (max 8, API limiti için)
    if listing.image_urls:
        download_tasks = [_download_image(u) for u in listing.image_urls[:8]]
        results = await asyncio.gather(*download_tasks, return_exceptions=True)

        downloaded = 0
        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            data, media_type = result
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(data).decode("utf-8"),
                    },
                }
            )
            downloaded += 1

        if downloaded > 1:
            # Kaç görsel gönderildiğini Claude'a bildir
            content.append({
                "type": "text",
                "text": f"(Yukarıda ilanın {downloaded} farklı fotoğrafı verildi. Hepsini dikkate alarak değerlendir.)"
            })

    user_text = (
        f"İlan başlığı: {listing.title}\n"
        f"Fiyat: {listing.price_nok} NOK\n"
        f"Konum: {listing.location or '-'}\n"
        f"Açıklama: {listing.description or '(açıklama yok)'}\n"
    )
    content.append({"type": "text", "text": user_text})

    try:
        msg = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=[AI_REPORT_TOOL],
            tool_choice={"type": "tool", "name": AI_REPORT_TOOL["name"]},
        )
    except Exception as e:
        log.error("Claude API hatası (%s): %s", listing.id, e)
        return None

    block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    if block is None:
        log.error("Tool çağrısı dönmedi (%s): stop_reason=%s", listing.id, msg.stop_reason)
        return None

    parsed = block.input
    try:
        battery = parsed.get("battery_pct")
        return AIReport(
            condition_score=int(parsed["condition_score"]),
            battery_pct=int(battery) if battery is not None else None,
            red_flags=list(parsed.get("red_flags", [])),
            summary=str(parsed.get("summary", "")),
        )
    except Exception as e:
        log.error("AI raporu doğrulanamadı (%s): %s | input=%s", listing.id, e, str(parsed)[:200])
        return None


async def analyze_top_listings(
    listings: list[Listing],
    limit: int = AI_ANALYSIS_LIMIT,
) -> list[Listing]:
    """İlk `limit` ilan için AI analizini paralel çalıştır ve listingleri güncelle."""
    if not listings:
        return listings
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY yok — AI analizi atlanıyor.")
        return listings

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    targets = listings[:limit]

    async def _run(l: Listing) -> None:
        l.ai_report = await analyze_listing_ai(l, client=client)

    await asyncio.gather(*(_run(l) for l in targets), return_exceptions=True)
    return listings
