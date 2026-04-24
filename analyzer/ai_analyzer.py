"""Claude Vision + metin analizi ile ilan değerlendirmesi."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Optional

import aiohttp
from anthropic import AsyncAnthropic

from config import AI_ANALYSIS_LIMIT, ANTHROPIC_API_KEY, CLAUDE_MODEL, USER_AGENT
from models import AIReport, Listing

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen, Finn.no (Norveç 2. el) ilanlarını değerlendiren bir uzmansın.
Sana ilanın birden fazla fotoğrafı (ön yüz, arka yüz, kamera, ekran, aksesuar vb.) ve tam açıklaması verilecek.

Görevin:
1. TÜM fotoğrafları dikkatlice incele. Bir fotoğrafta görünen hasarı veya durumu başka fotoğraf çürütüyorsa bunu belirt.
2. Fotoğraflarda gördüğün gerçek durumu yaz — fotoğrafta olmayan bir şeyi "eksik" olarak işaretleme.
   Örneğin arka kapak fotoğrafı varsa "arka kapak fotoğrafı yok" deme.
3. Açıklamada belirtilen bilgileri (batarya %, garanti, kutu, hasar) doğrula veya çelişkileri işaretle.
4. Fotoğraflarda görünen somut hasar/çizik/kırık varsa belirt.
5. Kuşkulu noktaları (red flag) listele — SADECE gerçekten sorunlu olan şeyleri.

Sadece geçerli JSON dön, başka metin verme. Şema:
{
  "condition_score": <1-10 arası integer>,
  "red_flags": ["...","..."],
  "summary": "<2-3 cümlelik türkçe özet>"
}
"""


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


def _extract_json(text: str) -> dict:
    """Claude cevabındaki JSON bloğunu ayıkla."""
    # Saf JSON veya ```json ... ``` bloğu
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return json.loads(fence.group(1))
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        return json.loads(brace.group(0))
    raise ValueError(f"JSON bulunamadı: {text[:200]}")


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
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        log.error("Claude API hatası (%s): %s", listing.id, e)
        return None

    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        parsed = _extract_json(text)
        return AIReport(
            condition_score=int(parsed.get("condition_score", 5)),
            red_flags=list(parsed.get("red_flags", [])),
            summary=str(parsed.get("summary", "")),
        )
    except Exception as e:
        log.error("JSON parse edilemedi (%s): %s | raw=%s", listing.id, e, text[:200])
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
