import queue
import re
import sys
import threading

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
    console.print(" [dim]ROM Downloader  •  v0.1.0  •  by Reno[/]\n")


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

    # Single game: run directly with no queue overhead
    if len(game_names) == 1:
        _fetch_one(game_names[0], platform_name, resolved_fmt, revision, out_dir, debug=debug)
        return

    # Multiple games: print the queue up front, then process one at a time
    # in a worker thread so all jobs are committed before any download starts.
    console.print(f"[bold]Queued {len(game_names)} games:[/]")
    for i, name in enumerate(game_names, 1):
        console.print(f"  [dim]{i}.[/] {name}")
    console.print()

    job_queue: queue.Queue[str] = queue.Queue()
    for name in game_names:
        job_queue.put(name)

    completed: list[str] = []
    failed: list[str] = []

    def _worker() -> None:
        while True:
            try:
                game_name = job_queue.get(timeout=0.1)
            except queue.Empty:
                break
            try:
                _fetch_one(game_name, platform_name, resolved_fmt, revision, out_dir, debug=debug)
                completed.append(game_name)
            except Exception as exc:
                console.print(f"[red]Failed:[/] {game_name} — {exc}")
                failed.append(game_name)
            finally:
                job_queue.task_done()

    worker_thread = threading.Thread(target=_worker, daemon=True)
    worker_thread.start()
    worker_thread.join()

    # Summary after all jobs finish
    total = len(game_names)
    console.rule()
    console.print(f"[bold]Queue complete.[/] {len(completed)}/{total} succeeded.")
    if failed:
        console.print("[red]Failed:[/]")
        for name in failed:
            console.print(f"  [red]✗[/] {name}")


def _fetch_one(
    game_name: str,
    platform_name: str,
    desired_fmt: str,
    revision_override: int | None,
    out_dir: pathlib.Path,
    debug: bool = False,
) -> None:
    """Run the full search → download → extract → convert pipeline for a single game."""
    platform = get_platform(platform_name)

    console.rule(f"[bold]{game_name}")

    # 1. Search the ROM site for candidate matches
    with console.status(f"Searching for [bold]{game_name}[/]..."):
        results = scraper.search(platform_name, game_name)

    if not results:
        console.print(f"[red]No results found for '{game_name}'.[/]")
        return

    # 2. Region filter — prefer USA releases; warn and fall back if none found
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

    # 3. Format filter — find results in the desired format, or a convertible source
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

    # 4. Revision selection — pin to requested revision or sort by priority
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

    # Sort by region score first (exact USA wins), then revision priority
    rev_results.sort(key=lambda r: (region_score(r.title), revision_priority(r.title)))
    best = rev_results[0]

    # Inform the user if a non-Rev-1 revision was automatically chosen
    rev_match = re.search(r"\(Rev (\d+)\)", best.title, re.IGNORECASE)
    if rev_match and rev_match.group(1) != "1":
        console.print(
            f"[dim]Auto-selected revision {rev_match.group(1)} "
            f"(Rev 1 not available).[/]"
        )

    # 5. Show a summary of the selected ROM before downloading
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("[dim]Selected[/]", best.title)
    table.add_row("[dim]Format[/]", source_fmt)
    table.add_row("[dim]Size[/]", f"{best.size_mb:.0f} MB" if best.size_mb else "unknown")
    console.print(table)

    # 6. Download — the scraper streams the file and renders its own progress bar
    stem = best.title.replace("/", "-")
    dl_path = out_dir / f"{stem}.{source_fmt}"
    actual_dl_path = pathlib.Path(scraper.download(best, str(dl_path), headless=not debug))
    console.print(f"[green]Downloaded:[/] {actual_dl_path}")

    # 7. Extract archive if needed — ROM sites often wrap files in zip or 7z
    if converter.is_archive(str(actual_dl_path)):
        extracted_path = pathlib.Path(converter.extract_archive(str(actual_dl_path), str(out_dir)))
        converter.cleanup(str(actual_dl_path))
        console.print(f"[green]Extracted:[/] {extracted_path}")
        actual_dl_path = extracted_path

    # 8. Convert to the desired format if the downloaded file differs (e.g. rvz → iso)
    actual_source_fmt = actual_dl_path.suffix.lstrip(".")
    if actual_source_fmt != desired_fmt:
        final_path = out_dir / f"{stem}.{desired_fmt}"
        with console.status(f"Converting to [bold]{desired_fmt}[/]..."):
            converter.convert(str(actual_dl_path), str(final_path), desired_fmt)
        converter.cleanup(str(actual_dl_path))
        console.print(f"[green]Converted:[/] {final_path}")
