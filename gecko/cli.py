import queue
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# Allow both -h and --help everywhere
CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

_TITLE = """
 ██████╗ ███████╗ ██████╗██╗  ██╗ ██████╗
██╔════╝ ██╔════╝██╔════╝██║ ██╔╝██╔═══██╗
██║  ███╗█████╗  ██║     █████╔╝ ██║   ██║
██║   ██║██╔══╝  ██║     ██╔═██╗ ██║   ██║
╚██████╔╝███████╗╚██████╗██║  ██╗╚██████╔╝
 ╚═════╝ ╚══════╝ ╚═════╝╚═╝  ╚═╝ ╚═════╝"""


def _print_banner() -> None:
    console.print(_TITLE, style="bold green")
    console.print(" [dim]ROM Downloader  •  v0.3.0  •  by Reno[/]\n")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _sanitize_stem(title: str) -> str:
    """Strip characters that are illegal in filenames on Windows/macOS (e.g. colons)."""
    return re.sub(r'[\\/:*?"<>|]', "-", title).strip()


def _already_downloaded(out_dir: pathlib.Path, title: str, fmt: str) -> pathlib.Path | None:
    """
    Return the first file in out_dir that looks like this game in the given format, or None.

    Uses alphanumeric-only comparison so that differences in punctuation, region
    tags, or double extensions (e.g. .nkit.iso) don't cause a false miss.
    """
    def _alnum(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    norm = _alnum(_sanitize_stem(title))
    for f in out_dir.glob(f"*.{fmt}"):
        # Compare only the part before the first dot to handle .nkit.iso etc.
        if norm in _alnum(f.name.split(".")[0]):
            return f
    return None


def _find_best(
    game_name: str,
    platform_name: str,
    desired_fmt: str,
    revision_override: int | None,
) -> tuple[scraper.SearchResult, str] | None:
    """
    Silent search + selection — safe to call from multiple threads simultaneously.

    Runs the full region/format/revision filter pipeline and returns
    (best_result, source_fmt) or None if no suitable ROM was found.
    Intentionally produces no console output so concurrent calls don't interleave.
    """
    platform = get_platform(platform_name)
    results = scraper.search(platform_name, game_name)
    if not results:
        return None

    # Prefer USA releases; fall back to any region if none exist
    usa_results = [r for r in results if is_usa(r.title)]
    if not usa_results:
        usa_results = results

    # Find results in the desired format, or fall back to a convertible source
    source_fmt = desired_fmt
    format_results = [r for r in usa_results if r.fmt == desired_fmt]
    if not format_results and desired_fmt in platform.conversions:
        source_fmt = platform.conversions[desired_fmt]
        format_results = [r for r in usa_results if r.fmt == source_fmt]
    if not format_results:
        return None

    # Pin to a specific revision if requested; fall back to best available if not found
    if revision_override is not None:
        rev_tag = f"(Rev {revision_override})"
        rev_results = [r for r in format_results if rev_tag in r.title]
        rev_results = rev_results or format_results
    else:
        rev_results = format_results

    # Sort by region score first (exact USA wins), then revision priority
    rev_results.sort(key=lambda r: (region_score(r.title), revision_priority(r.title)))
    return rev_results[0], source_fmt


def _download_one(
    game_name: str,
    best: scraper.SearchResult,
    source_fmt: str,
    desired_fmt: str,
    out_dir: pathlib.Path,
    debug: bool = False,
) -> None:
    """
    Download, extract, and convert a single resolved ROM.
    Prints a progress header and status updates. Called sequentially by the queue worker.
    """
    # Sanitize the filename — colons and other special chars break paths on some systems
    stem = _sanitize_stem(best.title)

    # Check before printing anything — skip immediately if already downloaded
    existing = _already_downloaded(out_dir, best.title, desired_fmt)
    if existing:
        console.print(
            f"[yellow]⊘  Already downloaded:[/] [bold]{game_name}[/]\n"
            f"   [dim]{existing.name}[/]"
        )
        return

    console.rule(f"[bold]{game_name}")

    # Inform the user when a conversion will happen after download
    if source_fmt != desired_fmt:
        console.print(
            f"[yellow]'{desired_fmt}' not directly available.[/] "
            f"Will download [bold]{source_fmt}[/] and convert."
        )

    # Inform the user when a non-Rev-1 revision was automatically chosen
    rev_match = re.search(r"\(Rev (\d+)\)", best.title, re.IGNORECASE)
    if rev_match and rev_match.group(1) != "1":
        console.print(
            f"[dim]Auto-selected revision {rev_match.group(1)} (Rev 1 not available).[/]"
        )

    # Show a summary of what was selected before downloading
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("[dim]Selected[/]", best.title)
    table.add_row("[dim]Format[/]", source_fmt)
    table.add_row("[dim]Size[/]", f"{best.size_mb:.0f} MB" if best.size_mb else "unknown")
    console.print(table)

    dl_path = out_dir / f"{stem}.{source_fmt}"

    console.print("[dim]Starting download (site uses a countdown — may take up to 60 s before transfer begins)...[/]")
    actual_dl_path = pathlib.Path(scraper.download(best, str(dl_path), headless=not debug))
    console.print(f"[green]Downloaded:[/] {actual_dl_path}")

    # Extract archive if needed — ROM sites often wrap files in zip or 7z
    if converter.is_archive(str(actual_dl_path)):
        extracted_path = pathlib.Path(converter.extract_archive(str(actual_dl_path), str(out_dir)))
        converter.cleanup(str(actual_dl_path))
        console.print(f"[green]Extracted:[/] {extracted_path}")
        actual_dl_path = extracted_path

    # Convert to the desired format if the downloaded file differs (e.g. rvz → iso)
    actual_source_fmt = actual_dl_path.suffix.lstrip(".")
    if actual_source_fmt != desired_fmt:
        final_path = out_dir / f"{stem}.{desired_fmt}"
        with console.status(f"Converting to [bold]{desired_fmt}[/]..."):
            converter.convert(str(actual_dl_path), str(final_path), desired_fmt)
        converter.cleanup(str(actual_dl_path))
        console.print(f"[green]Converted:[/] {final_path}")


def _fetch_one(
    game_name: str,
    platform_name: str,
    desired_fmt: str,
    revision_override: int | None,
    out_dir: pathlib.Path,
    debug: bool = False,
) -> None:
    """Single-game pipeline: search with a spinner, then download."""
    console.rule(f"[bold]{game_name}")

    with console.status(f"Searching for [bold]{game_name}[/]..."):
        match = _find_best(game_name, platform_name, desired_fmt, revision_override)

    if match is None:
        console.print(f"[red]No results found for '{game_name}'.[/]")
        return

    best, source_fmt = match
    _download_one(game_name, best, source_fmt, desired_fmt, out_dir, debug=debug)


# ─── CLI ──────────────────────────────────────────────────────────────────────

@click.group(context_settings=CONTEXT_SETTINGS)
def cli() -> None:
    """gecko — search and download ROMs by game name.

    \b
    gecko scrapes supported ROM sites, filters results to USA releases,
    selects the best available revision, and saves the file in your chosen
    format — converting automatically when needed.

    \b
    Commands:
      fetch    Search and download one or more ROMs

    \b
    Run 'gecko COMMAND -h' for detailed help on any command.
    """


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--platform", "platform_name",
    required=True,
    metavar="PLATFORM",
    help=(
        "The target platform to search ROMs for. "
        "This determines which site catalogue is searched and which formats are available. "
        "Supported: gamecube. "
        "Example: --platform gamecube"
    ),
)
@click.option(
    "--format", "fmt",
    default=None,
    metavar="FORMAT",
    help=(
        "The desired file format for the final ROM. "
        "If omitted, the platform default is used (gamecube → rvz). "
        "If the requested format is not directly available on the site, gecko will "
        "download a compatible source format and convert it automatically using DolphinTool. "
        "Supported formats — gamecube: iso, rvz, gcz."
    ),
)
@click.option(
    "--list", "game_list",
    default=None,
    type=click.Path(exists=True),
    metavar="FILE",
    help=(
        "Path to a plain-text file containing game names, one per line. "
        "Lines beginning with '#' are treated as comments and ignored. "
        "Can be combined with inline GAMES arguments — both sources are merged into one run."
    ),
)
@click.option(
    "--revision",
    default=None,
    type=int,
    metavar="N",
    help=(
        "Pin the download to a specific revision number (e.g. 0, 1, 2). "
        "ROM releases often ship in multiple revisions that address bugs or regional differences. "
        "If the requested revision is not found, gecko warns you and falls back to the best available. "
        "Omit this flag to let gecko auto-select using the priority order: "
        "Rev 1 > Rev 0 > untagged > Rev 2+."
    ),
)
@click.option(
    "--output-dir",
    default=".",
    show_default=True,
    type=click.Path(),
    metavar="DIR",
    help=(
        "Directory where downloaded and converted files are saved. "
        "Created automatically if it does not already exist. "
        "Defaults to the current working directory."
    ),
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help=(
        "Launch the browser in headed (visible) mode instead of running headless. "
        "Useful for diagnosing download failures — you can watch the browser navigate "
        "the page in real time and see exactly where it gets stuck or clicks the wrong element."
    ),
)
@click.argument("games", nargs=-1, metavar="GAMES...")
def fetch(
    platform_name: str,
    fmt: str | None,
    game_list: str | None,
    revision: int | None,
    output_dir: str,
    debug: bool,
    games: tuple[str, ...],
) -> None:
    """Search and download ROMs for one or more games.

    GAMES are fuzzy-matched against the ROM site catalogue — partial names and
    minor misspellings are handled automatically. Quote multi-word titles to
    prevent the shell from splitting them.

    \b
    The full download pipeline for each game:
      1. Search the ROM site catalogue for the closest match
      2. Filter results to USA region (falls back to any region if none found)
      3. Match the requested format, or find a convertible source
      4. Select the best revision (or the one you pinned with --revision)
      5. Download the file with a live progress bar
      6. Extract the ROM if it arrived inside a zip or 7z archive
      7. Convert to your desired format via DolphinTool if needed

    \b
    Examples:
      gecko fetch --platform gamecube "Mario Party 6"
      gecko fetch --platform gamecube --format iso "Metroid Prime"
      gecko fetch --platform gamecube --revision 1 "Super Mario Sunshine"
      gecko fetch --platform gamecube --list my_games.txt --output-dir ~/roms
      gecko fetch --platform gamecube --debug "Paper Mario"
    """

    _print_banner()

    try:
        platform = get_platform(platform_name)
    except ValueError as e:
        raise click.BadParameter(str(e), param_hint="--platform")

    # Resolve output format — fall back to the platform default when --format is omitted
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

    # Single game: run the pipeline directly with no queue overhead
    if len(game_names) == 1:
        _fetch_one(game_names[0], platform_name, resolved_fmt, revision, out_dir, debug=debug)
        return

    # ── Multi-game: concurrent search → queued downloads ──────────────────────
    #
    # Phase 1: search for all games at the same time using a thread pool.
    #   Each search runs in its own thread so the catalogue lookups overlap.
    #   No console output is produced during this phase to avoid interleaving.
    #
    # Phase 2: feed every resolved result into a queue and process downloads
    #   one at a time with a single worker thread.

    console.print(f"[bold]Searching for {len(game_names)} games...[/]\n")

    download_queue: queue.Queue[tuple[str, scraper.SearchResult, str]] = queue.Queue()
    search_failed: list[str] = []

    # Cap concurrency at 6 — each search launches a headless browser
    with ThreadPoolExecutor(max_workers=min(len(game_names), 6)) as pool:
        future_to_name = {
            pool.submit(_find_best, name, platform_name, resolved_fmt, revision): name
            for name in game_names
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                result = future.result()
            except Exception as exc:
                console.print(f"  [red]✗[/] {name} — {exc}")
                search_failed.append(name)
                continue

            if result is None:
                console.print(f"  [red]✗[/] {name} — not found")
                search_failed.append(name)
            else:
                best, source_fmt = result
                console.print(f"  [green]✓[/] {name} → {best.title}")
                download_queue.put((name, best, source_fmt))

    if download_queue.empty():
        console.print("\n[red]No games found to download.[/]")
        return

    queued = download_queue.qsize()
    console.print(f"\n[bold]Starting download queue ({queued} game(s))...[/]\n")

    completed: list[str] = []
    download_failed: list[str] = []

    def _worker() -> None:
        while True:
            try:
                game_name, best, source_fmt = download_queue.get(timeout=0.1)
            except queue.Empty:
                break
            try:
                _download_one(game_name, best, source_fmt, resolved_fmt, out_dir, debug=debug)
                completed.append(game_name)
            except Exception as exc:
                console.print(f"[red]Failed:[/] {game_name} — {exc}")
                download_failed.append(game_name)
            finally:
                download_queue.task_done()

    worker_thread = threading.Thread(target=_worker, daemon=True)
    worker_thread.start()
    worker_thread.join()

    # Final summary
    total = len(game_names)
    all_failed = search_failed + download_failed
    console.rule()
    console.print(f"[bold]Done.[/] {len(completed)}/{total} succeeded.")
    if all_failed:
        console.print("[red]Failed:[/]")
        for name in all_failed:
            console.print(f"  [red]✗[/] {name}")
