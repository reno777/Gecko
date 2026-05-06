"""
Playwright-based ROM search and download. Real scraping logic is not yet
implemented — search() returns fake results so the CLI pipeline can be
exercised end to end.
"""

import sys
from dataclasses import dataclass

# Flip to False when real scraping is wired up.
_STUB = True


def _check_browser() -> None:
    """
    Verify that Playwright's Chromium browser is installed.
    Raises SystemExit with a clear install instruction if not.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser_path = p.chromium.executable_path
            import pathlib
            if not pathlib.Path(browser_path).exists():
                raise FileNotFoundError
    except Exception:
        sys.exit(
            "Playwright's Chromium browser is not installed.\n"
            "Run:  playwright install chromium"
        )


@dataclass
class SearchResult:
    title: str          # Full ROM title as listed on the site
    url: str            # Direct download URL (or page URL for JS-gated links)
    fmt: str            # Detected format (iso, rvz, gcz, ...)
    size_mb: float      # Approximate size in MB (0.0 if unknown)


def search(platform: str, game_name: str) -> list[SearchResult]:
    """
    Return candidate SearchResults for *game_name* on *platform*.
    Results should be pre-filtered to plausible matches but not yet
    sorted by region/revision — that happens in cli.py.

    TODO: implement with Playwright once site target is confirmed.
    """
    if not _STUB:
        _check_browser()
    # Stub: return fake results that exercise the full pipeline
    fake: list[SearchResult] = [
        SearchResult(
            title=f"{game_name} (USA) (Rev 1)",
            url="https://example.com/fake/rom_rev1.rvz",
            fmt="rvz",
            size_mb=1400.0,
        ),
        SearchResult(
            title=f"{game_name} (USA) (Rev 0)",
            url="https://example.com/fake/rom_rev0.rvz",
            fmt="rvz",
            size_mb=1400.0,
        ),
        SearchResult(
            title=f"{game_name} (Europe)",
            url="https://example.com/fake/rom_europe.rvz",
            fmt="rvz",
            size_mb=1400.0,
        ),
    ]
    return fake


def download(result: SearchResult, dest_path: str) -> None:
    """
    Download *result* to *dest_path*.

    TODO: implement with Playwright (handle JS-gated download buttons,
    Cloudflare bypass, progress streaming).
    """
    # Stub: create a zero-byte placeholder so downstream steps can run
    import pathlib
    pathlib.Path(dest_path).touch()
