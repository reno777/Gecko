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
from typing import Any

_ROMSGAMES_BASE = "https://www.romsgames.net"

# Maps platform CLI names to the URL slug used by romsgames.net
_PLATFORM_SLUG: dict[str, str] = {
    "gamecube": "gamecube",
}

# File extensions that indicate a direct ROM download response
_ROM_EXTENSIONS = (".iso", ".rvz", ".gcz", ".zip", ".7z", ".rar")

_DOWNLOAD_SELECTORS = [
    "a:has-text('Save Game')",
    "button:has-text('Save Game')",
    "a:has-text('Download ROM')",
    "a:has-text('Download Now')",
    "a:has-text('Download'):not([href*='emulator'])",
    "button:has-text('Download')",
    ".download-btn",
    "a.download",
    "[data-action='download']",
]


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
                    fmt="iso",
                    size_mb=0.0,
                ))
                break

    return results


def download(result: SearchResult, dest_path: str, headless: bool = True) -> None:
    """Navigate to result.url with Playwright and save the ROM to dest_path."""
    _check_browser()
    _romsgames_download(result, dest_path, headless=headless)


def _attach_response_watcher(page, bucket: list[str]) -> None:
    """Append any ROM file URLs seen in network responses to *bucket*."""
    def on_response(response) -> None:
        url = response.url
        if any(url.lower().endswith(ext) for ext in _ROM_EXTENSIONS):
            bucket.append(url)
    page.on("response", on_response)


def _find_download_locator(page) -> tuple[Any, str] | tuple[None, None]:
    """Return (Locator, matched_selector) for the first matching download button.

    Locators re-query the DOM on every interaction, so they never go stale
    after a page re-render (unlike ElementHandle from query_selector).
    Returns (None, None) if no button is found.
    """
    for sel in _DOWNLOAD_SELECTORS:
        loc = page.locator(sel).first
        if loc.count() > 0:
            return loc, sel
    return None, None


def _save_from_url(url: str, dest_path: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)


def _romsgames_download(result: SearchResult, dest_path: str, headless: bool = True) -> None:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(accept_downloads=True)
            direct_urls: list[str] = []

            page = context.new_page()
            _attach_response_watcher(page, direct_urls)

            page.goto(result.url, wait_until="networkidle")

            if direct_urls:
                _save_from_url(direct_urls[0], dest_path)
                return

            loc, matched_sel = _find_download_locator(page)
            if loc is None:
                raise RuntimeError(
                    f"No download button found on {result.url}. "
                    "The site layout may have changed."
                )

            if not headless:
                print(f"[debug] selector matched: {matched_sel!r}")
                print(f"[debug] button text:      {loc.inner_text()[:80]!r}")

            # Observed flow on romsgames.net:
            #   click 1 → 1 ad tab opens
            #   click 2 → 2 ad tabs open simultaneously
            #   close all ad tabs → countdown appears on the main page
            #   countdown expires → download fires on the main page
            #
            # We keep expect_download open across both clicks so it catches
            # the download event whenever the countdown finishes.
            try:
                with page.expect_download(timeout=90_000) as dl_info:
                    for click_num in range(2):
                        loc.click()
                        # Wait for ad tabs to fully open before closing them
                        page.wait_for_timeout(2_000)
                        for pg in context.pages[1:]:
                            pg.close()
                        page.wait_for_timeout(500)
                    # Countdown is now running on the main page.
                    # expect_download will block here until the file arrives.
                dl_info.value.save_as(dest_path)
                return
            except PWTimeout:
                pass

            if direct_urls:
                _save_from_url(direct_urls[0], dest_path)
                return

            raise RuntimeError(
                f"Could not download from {result.url}. "
                "Try --debug to watch the browser and verify the right button is being clicked."
            )
        finally:
            browser.close()
