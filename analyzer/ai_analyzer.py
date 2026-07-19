"""Listing assessment via Claude Vision + text analysis."""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

import aiohttp
from anthropic import AsyncAnthropic

from config import (
    AI_ANALYSIS_LIMIT,
    AI_CONCURRENCY,
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    USER_AGENT,
)
from models import AIReport, Listing

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert who evaluates Finn.no (Norwegian second-hand marketplace) listings.
You will be given several photos of the listing and its full description.

Your task:
1. Study ALL the photos carefully. Write what you actually see — do not mark something as "missing" if the photo shows it.
2. If the item is a phone/tablet/laptop, focus on:
   - SCREEN: any scratches, cracks, burn-in, dead pixels?
   - BODY/BACK COVER: any damage, scratches, bending?
   - CAMERA: is the lens scratched or cracked?
   - BATTERY: is a battery % shown in the description or in a screen photo? How much?
   - ACCESSORIES: is the box, charger, or cable included?
   - LOCK: any risk of iCloud/Google account lock?
   If the item is in a different category (headphones, camera, console, etc.), assess it
   against the wear, damage, and missing-part criteria appropriate for that category;
   leave battery info null if it isn't meaningful.
3. Cross-check facts stated in the description (battery %, warranty, damage). Flag contradictions.
4. List red flags — ONLY the genuinely concerning ones, don't overdo it.

Report the result via the report_listing_assessment tool.
"""

# Forced tool call — pins Claude's output to the schema and eliminates JSON parse errors
AI_REPORT_TOOL = {
    "name": "report_listing_assessment",
    "description": "Report the structured evaluation of a second-hand listing.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "condition_score": {
                "type": "integer",
                "enum": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                "description": "Overall item condition, 1 (very poor) - 10 (like new)",
            },
            "battery_pct": {
                "type": ["integer", "null"],
                "description": "Battery health percentage; null if unknown or not meaningful",
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Genuinely concerning points (in English)",
            },
            "summary": {
                "type": "string",
                "description": "2-3 sentence summary in English",
            },
        },
        "required": ["condition_score", "battery_pct", "red_flags", "summary"],
        "additionalProperties": False,
    },
}


async def _download_image(url: str) -> Optional[tuple[bytes, str]]:
    """Download an image and return (bytes, media_type)."""
    try:
        async with aiohttp.ClientSession(
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as s:
            async with s.get(url) as resp:
                if resp.status != 200:
                    log.warning("Image download failed (%s): %s", resp.status, url)
                    return None
                data = await resp.read()
                ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                if ct not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                    ct = "image/jpeg"
                return data, ct
    except Exception as e:
        log.warning("Image download error: %s", e)
        return None


async def analyze_listing_ai(
    listing: Listing,
    client: Optional[AsyncAnthropic] = None,
) -> Optional[AIReport]:
    """Run a Claude Vision analysis for a single listing."""
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY missing, AI analysis skipped.")
        return None

    client = client or AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    content: list[dict] = []

    # Download all images in parallel (max 8 for API limits)
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
            # Tell Claude how many images were provided
            content.append({
                "type": "text",
                "text": f"(Above are {downloaded} different photos of the listing. Take all of them into account when evaluating.)"
            })

    user_text = (
        f"Listing title: {listing.title}\n"
        f"Price: {listing.price_nok} NOK\n"
        f"Location: {listing.location or '-'}\n"
        f"Description: {listing.description or '(no description)'}\n"
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
        log.error("Claude API error (%s): %s", listing.id, e)
        return None

    block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    if block is None:
        log.error("No tool call returned (%s): stop_reason=%s", listing.id, msg.stop_reason)
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
        log.error("AI report validation failed (%s): %s | input=%s", listing.id, e, str(parsed)[:200])
        return None


async def analyze_top_listings(
    listings: list[Listing],
    limit: int = AI_ANALYSIS_LIMIT,
    client: Optional[AsyncAnthropic] = None,
) -> list[Listing]:
    """Run AI analysis on the first `limit` listings with bounded concurrency.

    At most AI_CONCURRENCY requests fly at once; sending them all at once can
    hit rate limits on image-heavy requests.
    """
    if not listings:
        return listings
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY missing — skipping AI analysis.")
        return listings

    client = client or AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    targets = listings[:limit]
    semaphore = asyncio.Semaphore(AI_CONCURRENCY)

    async def _run(l: Listing) -> None:
        async with semaphore:
            l.ai_report = await analyze_listing_ai(l, client=client)

    await asyncio.gather(*(_run(l) for l in targets), return_exceptions=True)
    return listings
