"""
Playwright-based ROM search and download.

Currently implements romsgames.net. romsfun.com (Cloudflare-protected) is a
planned second source once the core flow is validated.
"""

import difflib
import pathlib
import sys
import urllib.request
from dataclasses import dataclass

_ROMSGAMES_BASE = "https://www.romsgames.net"

# Maps platform CLI names to the URL slug used by romsgames.net
_PLATFORM_SLUG: dict[str, str] = {
    "gamecube": "gamecube",
}

# File extensions that indicate a direct ROM download response
_ROM_EXTENSIONS = (".iso", ".rvz", ".gcz", ".zip", ".7z", ".rar")


@dataclass
class SearchResult:
    title: str       # Game title as listed on the site
    url: str         # Game detail page URL (Playwright navigates here to download)
    fmt: str         # Detected or inferred format (iso, rvz, gcz, ...)
    size_mb: float   # Approximate size in MB (0.0 if unknown)


def _check_browser() -> None:
    """Exit with a clear message if Playwright's Chromium browser isn't installed."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            if not pathlib.Path(p.chromium.executable_path).exists():
                raise FileNotFoundError
    except Exception:
        sys.exit(
            "Playwright's Chromium browser is not installed.\n"
            "Run:  playwright install chromium"
        )


def _first_alpha(name: str) -> str:
    """Return the first alphabetic character of a string, lowercased."""
    for ch in name:
        if ch.isalpha():
            return ch.lower()
    return "a"


def search(platform: str, game_name: str) -> list[SearchResult]:
    """
    Return candidate SearchResults for *game_name* on *platform*,
    sorted by fuzzy match score. Region/revision sorting happens in cli.py.
    """
    _check_browser()
    slug = _PLATFORM_SLUG.get(platform.lower())
    if slug is None:
        raise ValueError(f"romsgames.net scraper does not support platform '{platform}'")
    return _romsgames_search(slug, game_name)


def _romsgames_search(platform_slug: str, game_name: str) -> list[SearchResult]:
    from playwright.sync_api import sync_playwright

    first_letter = _first_alpha(game_name)
    candidates: list[tuple[str, str]] = []  # (title, href)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page_num = 1
            while True:
                url = (
                    f"{_ROMSGAMES_BASE}/roms/{platform_slug}/"
                    f"?letter={first_letter}&page={page_num}&sort=alphabetical"
                )
                page.goto(url, wait_until="domcontentloaded")

                links = page.query_selector_all(f"a[href*='/{platform_slug}-rom-']")
                if not links:
                    break

                page_hits: list[tuple[str, str]] = []
                for link in links:
                    title = link.inner_text().strip()
                    href = link.get_attribute("href")
                    if title and href:
                        page_hits.append((title, href))

                if not page_hits:
                    break
                candidates.extend(page_hits)

                # Advance only if a next-page control exists
                has_next = page.query_selector(
                    "a.next, a[rel='next'], .pagination a:has-text('Next')"
                )
                if not has_next:
                    break
                page_num += 1
        finally:
            browser.close()

    if not candidates:
        return []

    titles = [t for t, _ in candidates]
    close = difflib.get_close_matches(game_name, titles, n=5, cutoff=0.4)

    seen: set[str] = set()
    results: list[SearchResult] = []
    for match_title in close:
        for title, href in candidates:
            if title == match_title and href not in seen:
                seen.add(href)
                results.append(SearchResult(
                    title=title,
                    url=f"{_ROMSGAMES_BASE}{href}",
                    # romsgames.net doesn't expose format in listings;
                    # GameCube ROMs there are consistently .iso
                    fmt="iso",
                    size_mb=0.0,
                ))
                break

    return results


def download(result: SearchResult, dest_path: str) -> None:
    """Navigate to result.url with Playwright and save the ROM to dest_path."""
    _check_browser()
    _romsgames_download(result, dest_path)


def _romsgames_download(result: SearchResult, dest_path: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            # Watch responses for a direct file URL before we touch any buttons
            direct_url: list[str] = []

            def on_response(response) -> None:
                url = response.url
                if (
                    any(url.lower().endswith(ext) for ext in _ROM_EXTENSIONS)
                    and response.status == 200
                ):
                    direct_url.append(url)

            page.on("response", on_response)
            page.goto(result.url, wait_until="networkidle")

            if direct_url:
                urllib.request.urlretrieve(direct_url[0], dest_path)
                return

            # Try common download button patterns in priority order
            download_selectors = [
                "a:has-text('Download')",
                "button:has-text('Download')",
                ".download-btn",
                "a.download",
                "[data-action='download']",
                "a[href*='download']",
            ]

            btn = None
            for selector in download_selectors:
                candidate = page.query_selector(selector)
                if candidate:
                    btn = candidate
                    break

            if btn is None:
                raise RuntimeError(
                    f"Could not locate a download button on {result.url}. "
                    "The site layout may have changed."
                )

            with context.expect_page() as new_page_info:
                btn.click()

            # Some sites open a new tab that triggers the download
            new_page = new_page_info.value
            new_page.wait_for_load_state("domcontentloaded")

            # Re-check for a direct URL that appeared after the click
            if direct_url:
                urllib.request.urlretrieve(direct_url[0], dest_path)
                return

            # Last resort: wait for Playwright's built-in download event
            with new_page.expect_download(timeout=60_000) as dl_info:
                pass
            dl_info.value.save_as(dest_path)

        finally:
            browser.close()
