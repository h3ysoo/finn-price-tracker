"""Finn Price Tracker — CLI girişi.

Kullanım:
    python main.py search "iPhone 13 Pro Max 256GB"
    python main.py deals
    python main.py deals --limit 20
    python main.py drops
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from analyzer import analyze_prices, analyze_top_listings, select_candidates
from config import AI_ANALYSIS_LIMIT, DEFAULT_PAGES, LISTING_MIN_PRICE
from database import Database
from models import Listing, PriceReport
from scraper import FinnScraper, scrape_finn, filter_listings

console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


def _format_price(v: Optional[int | float]) -> str:
    if v is None or v == 0:
        return "—"
    return f"{int(v):,} kr".replace(",", " ")


def _score_text(score: Optional[float]) -> Text:
    if score is None:
        return Text("—", style="dim")
    if score < -15:
        return Text(f"{score:+.1f}%", style="bold green")
    if score < -5:
        return Text(f"{score:+.1f}%", style="green")
    if score > 15:
        return Text(f"{score:+.1f}%", style="bold red")
    if score > 5:
        return Text(f"{score:+.1f}%", style="red")
    return Text(f"{score:+.1f}%", style="yellow")


def _render_report(report: PriceReport) -> None:
    """Fiyat raporunu rich panel + tablo olarak yazdır."""
    stats = Table.grid(padding=(0, 2))
    stats.add_column(style="cyan", justify="right")
    stats.add_column()
    stats.add_row("İlan sayısı:", str(report.count))
    stats.add_row("Ortalama:", _format_price(report.mean))
    stats.add_row("Medyan:", _format_price(report.median))
    stats.add_row("Std sapma:", _format_price(report.std))
    stats.add_row("Min / Max:", f"{_format_price(report.min_price)} / {_format_price(report.max_price)}")
    stats.add_row("25% / 75%:", f"{_format_price(report.p25)} / {_format_price(report.p75)}")

    console.print(
        Panel(
            stats,
            title=f"[bold]Fiyat Analizi — '{report.query}'[/bold]",
            border_style="cyan",
        )
    )


def _render_listings(listings: list[Listing], title: str) -> None:
    tbl = Table(title=title, show_lines=False, header_style="bold magenta")
    tbl.add_column("#", justify="right", style="dim", width=3)
    tbl.add_column("Başlık", overflow="fold", max_width=42)
    tbl.add_column("Fiyat", justify="right")
    tbl.add_column("Skor", justify="right")
    tbl.add_column("Konum", overflow="fold", max_width=20)
    tbl.add_column("Durum", justify="center")
    tbl.add_column("URL", overflow="fold", max_width=40)

    for i, l in enumerate(listings, 1):
        cond = "—"
        if l.ai_report:
            cs = l.ai_report.condition_score
            color = "green" if cs >= 8 else "yellow" if cs >= 5 else "red"
            cond = f"[{color}]{cs}/10[/{color}]"
        tbl.add_row(
            str(i),
            l.title,
            _format_price(l.price_nok),
            _score_text(l.price_score),
            l.location or "—",
            cond,
            l.url,
        )
    console.print(tbl)


def _render_ai_details(listings: list[Listing]) -> None:
    """AI analizi yapılmış ilanlar için detay panelleri."""
    analyzed = [l for l in listings if l.ai_report]
    if not analyzed:
        return

    console.print("\n[bold]AI Analiz Detayları[/bold]")
    for l in analyzed:
        r = l.ai_report
        assert r is not None
        flags = "\n".join(f"  ⚠ {f}" for f in r.red_flags) or "  (yok)"
        body = (
            f"[bold]Başlık:[/bold] {l.title}\n"
            f"[bold]Fiyat:[/bold] {_format_price(l.price_nok)}  "
            f"[bold]Skor:[/bold] {r.condition_score}/10\n"
            f"[bold]Özet:[/bold] {r.summary}\n"
            f"[bold]Kırmızı bayraklar:[/bold]\n{flags}\n"
            f"[dim]{l.url}[/dim]"
        )
        color = "green" if r.condition_score >= 8 else "yellow" if r.condition_score >= 5 else "red"
        console.print(Panel(body, border_style=color))


# --- Komutlar -----------------------------------------------------------------

async def cmd_search(args: argparse.Namespace) -> int:
    query: str = args.query
    pages: int = args.pages
    ai_limit: int = args.ai_limit

    console.rule(f"[bold]Finn.no araması: '{query}'[/bold]")

    report = None
    top: list = []

    try:
        async with FinnScraper(headless=not args.show_browser) as scraper:
            # 1. Arama sayfalarını tara
            with console.status("[cyan]Finn.no taranıyor..."):
                listings = await scraper.search(query, pages=pages)

            if not listings:
                console.print("[yellow]Hiç ilan bulunamadı.[/yellow]")
                return 1

            console.print(f"[green]✓[/green] {len(listings)} ilan çekildi.")

            # 2. Alakasız ilanları filtrele
            listings = filter_listings(listings, min_price=LISTING_MIN_PRICE)
            if not listings:
                console.print("[yellow]Filtre sonrası ilan kalmadı.[/yellow]")
                return 1

            # 3. Tüm ilanların detay sayfasına gir (paralel)
            with console.status(f"[cyan]Tüm {len(listings)} ilanın detay sayfası okunuyor..."):
                await scraper.enrich_all(listings, concurrency=3)

            # 4. Detaylı bilgilerle fiyat analizi
            report = analyze_prices(listings)

            # 5. Gerçek fırsat adaylarını seç (enrich zaten yapıldı)
            top = select_candidates(report, limit=ai_limit)

    except Exception as e:
        console.print(f"[red]Scraper hatası:[/red] {e}")
        return 2

    if report is None:
        return 1

    _render_report(report)

    # 5. AI analizi (detayı çekilmiş en ucuz N)
    if ai_limit > 0 and top:
        with console.status(f"[cyan]{len(top)} ilan AI ile analiz ediliyor..."):
            await analyze_top_listings(top, limit=ai_limit)

    _render_listings(report.listings[: max(20, ai_limit)], title="İlanlar (ucuzdan pahalıya)")
    _render_ai_details(top)

    # DB'ye yaz
    db = Database()
    saved = db.save_listings(report.listings)
    console.print(f"[green]✓[/green] {saved} kayıt DB'ye yazıldı: {db.path}")
    return 0


def cmd_deals(args: argparse.Namespace) -> int:
    db = Database()
    deals = db.get_best_deals(limit=args.limit)
    if not deals:
        console.print("[yellow]DB'de kayıt yok. Önce 'search' çalıştır.[/yellow]")
        return 1
    _render_listings(deals, title=f"En iyi {len(deals)} fırsat (tüm sorgulardan)")
    return 0


def cmd_drops(args: argparse.Namespace) -> int:
    """Fiyat geçmişine göre son taramada fiyatı düşen ilanları göster."""
    db = Database()
    drops = db.get_price_drops(limit=args.limit)
    if not drops:
        console.print(
            "[yellow]Fiyatı düşen ilan yok. Aynı aramayı zaman içinde tekrar "
            "çalıştırdıkça fiyat geçmişi birikir.[/yellow]"
        )
        return 1

    tbl = Table(title=f"Fiyatı düşen {len(drops)} ilan", header_style="bold magenta")
    tbl.add_column("#", justify="right", style="dim", width=3)
    tbl.add_column("Başlık", overflow="fold", max_width=42)
    tbl.add_column("Eski", justify="right", style="dim")
    tbl.add_column("Yeni", justify="right")
    tbl.add_column("Düşüş", justify="right")
    tbl.add_column("URL", overflow="fold", max_width=40)

    for i, (l, prev_price) in enumerate(drops, 1):
        drop_pct = (prev_price - (l.price_nok or 0)) / prev_price * 100
        tbl.add_row(
            str(i),
            l.title,
            _format_price(prev_price),
            _format_price(l.price_nok),
            Text(f"-{drop_pct:.1f}%", style="bold green"),
            l.url,
        )
    console.print(tbl)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="finn-price-tracker",
        description="Finn.no 2. el fiyat analiz aracı.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="Finn.no'da arama yap ve analiz et")
    sp.add_argument("query", help="Arama terimi, ör: 'iPhone 13 Pro Max 256GB'")
    sp.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    sp.add_argument("--ai-limit", type=int, default=AI_ANALYSIS_LIMIT)
    sp.add_argument("--show-browser", action="store_true", help="Headless olmadan çalıştır")

    dp = sub.add_parser("deals", help="DB'deki en iyi fırsatları listele")
    dp.add_argument("--limit", type=int, default=10)

    rp = sub.add_parser("drops", help="Fiyatı düşen ilanları listele")
    rp.add_argument("--limit", type=int, default=10)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    if args.cmd == "search":
        return asyncio.run(cmd_search(args))
    if args.cmd == "deals":
        return cmd_deals(args)
    if args.cmd == "drops":
        return cmd_drops(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
