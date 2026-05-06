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


_DOWNLOAD_SELECTORS = [
    "a:has-text('Download')",
    "button:has-text('Download')",
    ".download-btn",
    "a.download",
    "[data-action='download']",
    "a[href*='download']",
]


def _attach_response_watcher(page, bucket: list[str]) -> None:
    """Append any ROM file URLs seen in network responses to *bucket*."""
    def on_response(response) -> None:
        url = response.url
        if any(url.lower().endswith(ext) for ext in _ROM_EXTENSIONS):
            bucket.append(url)
    page.on("response", on_response)


def _find_download_btn(page):
    for sel in _DOWNLOAD_SELECTORS:
        btn = page.query_selector(sel)
        if btn:
            return btn
    return None


def _save_from_url(url: str, dest_path: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(dest_path, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)


def _romsgames_download(result: SearchResult, dest_path: str) -> None:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(accept_downloads=True)
            direct_urls: list[str] = []

            page = context.new_page()
            _attach_response_watcher(page, direct_urls)

            # Every new tab that opens is an ad popup — close it immediately.
            context.on("page", lambda pg: pg.close())

            page.goto(result.url, wait_until="networkidle")

            if direct_urls:
                _save_from_url(direct_urls[0], dest_path)
                return

            btn = _find_download_btn(page)
            if btn is None:
                raise RuntimeError(
                    f"No download button found on {result.url}. "
                    "The site layout may have changed."
                )

            # Each click opens an ad tab (auto-closed above). After several
            # clicks a countdown appears on the main page and the download
            # fires automatically when it expires. Keep expect_download open
            # for the whole sequence so it catches the event whenever it fires.
            try:
                with page.expect_download(timeout=90_000) as dl_info:
                    for _ in range(3):
                        btn = _find_download_btn(page) or btn
                        btn.click()
                        page.wait_for_timeout(1_500)
                dl_info.value.save_as(dest_path)
                return
            except PWTimeout:
                pass

            if direct_urls:
                _save_from_url(direct_urls[0], dest_path)
                return

            raise RuntimeError(
                f"Could not download from {result.url}. "
                "The site's download flow may have changed."
            )
        finally:
            browser.close()
