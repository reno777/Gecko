import sys

if sys.version_info < (3, 11):
    sys.exit(
        f"gecko requires Python 3.11 or later (you have {sys.version.split()[0]}). "
        "Upgrade with: https://python.org/downloads"
    )

import pathlib

import click
from rich.console import Console
from rich.table import Table

from gecko import converter, scraper
from gecko.platforms import get_platform, revision_priority
from gecko.utils import is_usa, parse_game_list, region_score

console = Console()

_TITLE = """
 ██████╗ ███████╗ ██████╗██╗  ██╗ ██████╗
██╔════╝ ██╔════╝██╔════╝██║ ██╔╝██╔═══██╗
██║  ███╗█████╗  ██║     █████╔╝ ██║   ██║
██║   ██║██╔══╝  ██║     ██╔═██╗ ██║   ██║
╚██████╔╝███████╗╚██████╗██║  ██╗╚██████╔╝
 ╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝"""


def _print_banner() -> None:
    console.print(_TITLE, style="bold green")
    console.print(" [dim]ROM Downloader  •  v0.1.0  •  by Reno[/]\n")


@click.group()
def cli() -> None:
    """gecko — ROM search and download tool."""


@cli.command()
@click.option("--platform", "platform_name", required=True, help="Target platform (e.g. gamecube)")
@click.option("--format", "fmt", default=None, help="Desired output format (e.g. iso, rvz, gcz)")
@click.option("--list", "game_list", default=None, type=click.Path(exists=True), help="Path to .txt file of game names")
@click.option("--revision", default=None, type=int, help="Force a specific revision number (e.g. 0, 1)")
@click.option("--output-dir", default=".", show_default=True, type=click.Path(), help="Directory to save downloaded files")
@click.option("--debug", is_flag=True, default=False, help="Run browser in headed mode for troubleshooting")
@click.argument("games", nargs=-1)
def fetch(
    platform_name: str,
    fmt: str | None,
    game_list: str | None,
    revision: int | None,
    output_dir: str,
    debug: bool,
    games: tuple[str, ...],
) -> None:
    """Search and download ROMs for one or more games."""

    _print_banner()

    try:
        platform = get_platform(platform_name)
    except ValueError as e:
        raise click.BadParameter(str(e), param_hint="--platform")

    # Resolve output format
    resolved_fmt = fmt or platform.default_format
    if resolved_fmt not in platform.native_formats and resolved_fmt not in platform.conversions:
        raise click.BadParameter(
            f"Format '{resolved_fmt}' is not supported for {platform.name}. "
            f"Supported: {', '.join(platform.native_formats)}",
            param_hint="--format",
        )

    # Collect game names from inline args and/or --list file
    game_names: list[str] = list(games)
    if game_list:
        game_names.extend(parse_game_list(game_list))
    if not game_names:
        raise click.UsageError("Provide at least one game name or use --list.")

    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for game_name in game_names:
        _fetch_one(game_name, platform_name, resolved_fmt, revision, out_dir, debug=debug)


def _fetch_one(
    game_name: str,
    platform_name: str,
    desired_fmt: str,
    revision_override: int | None,
    out_dir: pathlib.Path,
    debug: bool = False,
) -> None:
    platform = get_platform(platform_name)

    console.rule(f"[bold]{game_name}")

    # 1. Search
    with console.status(f"Searching for [bold]{game_name}[/]..."):
        results = scraper.search(platform_name, game_name)

    if not results:
        console.print(f"[red]No results found for '{game_name}'.[/]")
        return

    # 2. Region filter — prefer USA, warn if falling back
    usa_results = [r for r in results if is_usa(r.title)]
    if not usa_results:
        console.print(
            f"[yellow]Warning:[/] No USA release found for '{game_name}'. "
            "Falling back to first available region."
        )
        usa_results = results
    elif len(usa_results) < len(results):
        skipped = len(results) - len(usa_results)
        console.print(f"[dim]Filtered out {skipped} non-USA result(s).[/]")

    # 3. Format filter — find results in desired_fmt or a convertible source
    source_fmt = desired_fmt
    format_results = [r for r in usa_results if r.fmt == desired_fmt]
    if not format_results and desired_fmt in platform.conversions:
        source_fmt = platform.conversions[desired_fmt]
        format_results = [r for r in usa_results if r.fmt == source_fmt]
        if format_results:
            console.print(
                f"[yellow]'{desired_fmt}' not directly available.[/] "
                f"Will download [bold]{source_fmt}[/] and convert."
            )
    if not format_results:
        console.print(
            f"[red]No '{desired_fmt}' (or convertible) results found for '{game_name}'.[/]"
        )
        return

    # 4. Revision selection
    if revision_override is not None:
        rev_tag = f"(Rev {revision_override})"
        rev_results = [r for r in format_results if rev_tag in r.title]
        if not rev_results:
            console.print(
                f"[yellow]Revision {revision_override} not found; "
                "using best available revision.[/]"
            )
            rev_results = format_results
    else:
        rev_results = format_results

    # Sort by region score first, then revision priority
    rev_results.sort(key=lambda r: (region_score(r.title), revision_priority(r.title)))
    best = rev_results[0]

    # Log if a non-default revision was automatically chosen
    import re
    rev_match = re.search(r"\(Rev (\d+)\)", best.title, re.IGNORECASE)
    if rev_match and rev_match.group(1) != "1":
        console.print(
            f"[dim]Auto-selected revision {rev_match.group(1)} "
            f"(Rev 1 not available).[/]"
        )

    # 5. Show selection summary
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("[dim]Selected[/]", best.title)
    table.add_row("[dim]Format[/]", source_fmt)
    table.add_row("[dim]Size[/]", f"{best.size_mb:.0f} MB" if best.size_mb else "unknown")
    console.print(table)

    # 6. Download — scraper renders its own progress bar
    stem = best.title.replace("/", "-")
    dl_path = out_dir / f"{stem}.{source_fmt}"
    actual_dl_path = pathlib.Path(scraper.download(best, str(dl_path), headless=not debug))
    console.print(f"[green]Downloaded:[/] {actual_dl_path}")

    # 7. Extract archive if needed (zip/7z → actual ROM file)
    if converter.is_archive(str(actual_dl_path)):
        extracted_path = pathlib.Path(converter.extract_archive(str(actual_dl_path), str(out_dir)))
        converter.cleanup(str(actual_dl_path))
        console.print(f"[green]Extracted:[/] {extracted_path}")
        actual_dl_path = extracted_path

    # 8. Convert if needed (e.g. rvz → iso via dolphintool)
    actual_source_fmt = actual_dl_path.suffix.lstrip(".")
    if actual_source_fmt != desired_fmt:
        final_path = out_dir / f"{stem}.{desired_fmt}"
        with console.status(f"Converting to [bold]{desired_fmt}[/]..."):
            converter.convert(str(actual_dl_path), str(final_path), desired_fmt)
        converter.cleanup(str(actual_dl_path))
        console.print(f"[green]Converted:[/] {final_path}")
