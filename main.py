"""Finn Price Tracker — CLI entry point.

Usage:
    python main.py search "iPhone 13 Pro Max 256GB"
    python main.py deals
    python main.py deals --limit 20
    python main.py drops
    python main.py history 400111222
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import AI_ANALYSIS_LIMIT, DEFAULT_PAGES
from database import Database
from models import Listing, PriceReport
from pipeline import SearchParams, run_search

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
    """Print the price report as a rich panel + table."""
    stats = Table.grid(padding=(0, 2))
    stats.add_column(style="cyan", justify="right")
    stats.add_column()
    stats.add_row("Listings:", str(report.count))
    stats.add_row("Mean:", _format_price(report.mean))
    stats.add_row("Median:", _format_price(report.median))
    stats.add_row("Std dev:", _format_price(report.std))
    stats.add_row("Min / Max:", f"{_format_price(report.min_price)} / {_format_price(report.max_price)}")
    stats.add_row("25% / 75%:", f"{_format_price(report.p25)} / {_format_price(report.p75)}")

    console.print(
        Panel(
            stats,
            title=f"[bold]Price Analysis — '{report.query}'[/bold]",
            border_style="cyan",
        )
    )


def _render_listings(listings: list[Listing], title: str) -> None:
    tbl = Table(title=title, show_lines=False, header_style="bold magenta")
    tbl.add_column("#", justify="right", style="dim", width=3)
    tbl.add_column("Title", overflow="fold", max_width=42)
    tbl.add_column("Price", justify="right")
    tbl.add_column("Score", justify="right")
    tbl.add_column("Location", overflow="fold", max_width=20)
    tbl.add_column("Condition", justify="center")
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
    """Detail panels for listings that received an AI analysis."""
    analyzed = [l for l in listings if l.ai_report]
    if not analyzed:
        return

    console.print("\n[bold]AI Analysis Details[/bold]")
    for l in analyzed:
        r = l.ai_report
        assert r is not None
        flags = "\n".join(f"  ⚠ {f}" for f in r.red_flags) or "  (none)"
        body = (
            f"[bold]Title:[/bold] {l.title}\n"
            f"[bold]Price:[/bold] {_format_price(l.price_nok)}  "
            f"[bold]Score:[/bold] {r.condition_score}/10\n"
            f"[bold]Summary:[/bold] {r.summary}\n"
            f"[bold]Red flags:[/bold]\n{flags}\n"
            f"[dim]{l.url}[/dim]"
        )
        color = "green" if r.condition_score >= 8 else "yellow" if r.condition_score >= 5 else "red"
        console.print(Panel(body, border_style=color))


# --- Commands ----------------------------------------------------------------

async def cmd_search(args: argparse.Namespace) -> int:
    ai_limit: int = args.ai_limit
    console.rule(f"[bold]Finn.no search: '{args.query}'[/bold]")

    params = SearchParams(
        query=args.query,
        pages=args.pages,
        ai_limit=ai_limit,
        deep_scan=args.deep_scan,
    )

    def progress(stage: str) -> None:
        console.print(f"[cyan]›[/cyan] {stage}...")

    try:
        # Shared pipeline: scrape → filter → score → AI → persist (pipeline.py)
        result = await run_search(params, progress, headless=not args.show_browser)
    except Exception as e:
        console.print(f"[red]Scraper error:[/red] {e}")
        return 2

    if result.is_empty:
        console.print("[yellow]No listings found (or none left after filtering).[/yellow]")
        return 1

    report = result.report
    top = result.top

    _render_report(report)
    _render_listings(report.listings[: max(20, ai_limit)], title="Listings (cheapest first)")
    _render_ai_details(top)

    console.print(f"[green]✓[/green] {report.count} listings saved to DB: {Database().path}")
    return 0


def cmd_deals(args: argparse.Namespace) -> int:
    db = Database()
    deals = db.get_best_deals(limit=args.limit, query=args.query)
    if not deals:
        console.print("[yellow]No records in DB. Run 'search' first.[/yellow]")
        return 1
    scope = f"'{args.query}' search" if args.query else "all queries"
    _render_listings(deals, title=f"Top {len(deals)} deals ({scope})")
    return 0


def cmd_drops(args: argparse.Namespace) -> int:
    """Show listings whose price dropped in the latest scan versus history."""
    db = Database()
    drops = db.get_price_drops(limit=args.limit, query=args.query)
    if not drops:
        console.print(
            "[yellow]No price drops. Price history accumulates as you re-run the "
            "same search over time.[/yellow]"
        )
        return 1

    tbl = Table(title=f"{len(drops)} price drops", header_style="bold magenta")
    tbl.add_column("#", justify="right", style="dim", width=3)
    tbl.add_column("Title", overflow="fold", max_width=42)
    tbl.add_column("Old", justify="right", style="dim")
    tbl.add_column("New", justify="right")
    tbl.add_column("Drop", justify="right")
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


def cmd_history(args: argparse.Namespace) -> int:
    """Show recorded price points for a listing, grouped by query."""
    db = Database()
    entries = db.get_listing_histories(args.id)
    if not entries:
        console.print(
            f"[yellow]No price history for '{args.id}'. Double-check the finnkode "
            "or run 'search' first.[/yellow]"
        )
        return 1

    for listing, history in entries:
        tbl = Table(header_style="bold magenta")
        tbl.add_column("Date")
        tbl.add_column("Price", justify="right")
        tbl.add_column("Change", justify="right")

        prev: Optional[int] = None
        for seen_at, price in history:
            if prev is None:
                change = Text("—", style="dim")
            else:
                pct = (price - prev) / prev * 100
                style = "bold green" if pct < 0 else "bold red" if pct > 0 else "dim"
                change = Text(f"{pct:+.1f}%", style=style)
            tbl.add_row(seen_at.strftime("%Y-%m-%d %H:%M"), _format_price(price), change)
            prev = price

        console.print(
            Panel(
                tbl,
                title=f"[bold]{listing.title}[/bold] — query: '{listing.query}'",
                subtitle=f"[dim]{listing.url}[/dim]",
                border_style="cyan",
            )
        )
    return 0


_EXPORT_FIELDS = [
    "id", "query", "title", "price_nok", "price_score", "composite_score",
    "condition_score", "battery_pct", "location", "url", "scraped_at",
]


def _listing_to_export_row(l: Listing) -> dict:
    return {
        "id": l.id,
        "query": l.query,
        "title": l.title,
        "price_nok": l.price_nok,
        "price_score": l.price_score,
        "composite_score": l.composite_score,
        "condition_score": l.ai_report.condition_score if l.ai_report else None,
        "battery_pct": l.ai_report.battery_pct if l.ai_report else None,
        "location": l.location,
        "url": l.url,
        "scraped_at": l.scraped_at.isoformat(),
    }


def cmd_export(args: argparse.Namespace) -> int:
    """Export stored listings for a query as CSV or JSON."""
    db = Database()
    listings = db.get_by_query(args.query)
    if not listings:
        console.print(f"[yellow]No records for '{args.query}'. Run 'search' first.[/yellow]")
        return 1

    rows = [_listing_to_export_row(l) for l in listings]

    if args.output:
        out_path = Path(args.output)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            _write_export(f, rows, args.format)
        console.print(f"[green]✓[/green] {len(rows)} listings written to: {out_path}")
    else:
        _write_export(sys.stdout, rows, args.format)
    return 0


def _write_export(f, rows: list[dict], fmt: str) -> None:
    if fmt == "json":
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")
    else:
        writer = csv.DictWriter(f, fieldnames=_EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="finn-price-tracker",
        description="Finn.no second-hand price analysis tool.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="Search Finn.no and analyze results")
    sp.add_argument("query", help="Search term, e.g. 'iPhone 13 Pro Max 256GB'")
    sp.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    sp.add_argument("--ai-limit", type=int, default=AI_ANALYSIS_LIMIT)
    sp.add_argument("--show-browser", action="store_true", help="Run without headless mode")
    sp.add_argument("--deep-scan", action="store_true",
                    help="Read every listing's detail page (more accurate scores, slower)")

    dp = sub.add_parser("deals", help="List the best deals from the DB")
    dp.add_argument("--limit", type=int, default=10)
    dp.add_argument("--query", help="Restrict results to a single search")

    rp = sub.add_parser("drops", help="List listings whose price dropped")
    rp.add_argument("--limit", type=int, default=10)
    rp.add_argument("--query", help="Restrict results to a single search")

    hp = sub.add_parser("history", help="Show recorded price history for a listing")
    hp.add_argument("id", help="Finn listing code (finnkode), e.g. 400111222")

    ep = sub.add_parser("export", help="Export stored listings for a query")
    ep.add_argument("query", help="Search to export, e.g. 'iPhone 13 Pro Max 256GB'")
    ep.add_argument("--format", choices=["csv", "json"], default="csv")
    ep.add_argument("-o", "--output", help="Output file (defaults to stdout)")

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
    if args.cmd == "history":
        return cmd_history(args)
    if args.cmd == "export":
        return cmd_export(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
