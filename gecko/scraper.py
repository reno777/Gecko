"""
Playwright-based ROM search and download.

Currently implements romsgames.net. romsfun.com (Cloudflare-protected) is a
planned second source once the core flow is validated.
"""

import difflib
import pathlib
import re
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


def _search_letters(game_name: str) -> list[str]:
    """
    Return the letter(s) to query when browsing the site's alphabetical listing.

    Titles starting with a digit (e.g. '007:', '1080') are filed under '0' on
    romsgames.net rather than the first alpha character.

    Many ROM sites sort titles by ignoring leading articles (The, A, An), so
    'The Legend of Zelda' is filed under 'L' not 'T'.  When the title starts
    with an article we search both letters so neither possibility is missed.
    """
    if game_name and game_name[0].isdigit():
        return ["0"]
    first = _first_alpha(game_name)
    words = game_name.split()
    if words and words[0].lower() in ("the", "a", "an") and len(words) > 1:
        alt = _first_alpha(words[1])
        return [first, alt] if alt != first else [first]
    return [first]


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

    letters = _search_letters(game_name)
    candidates: list[tuple[str, str]] = []  # (title, href)
    seen_hrefs: set[str] = set()             # dedup across multiple letter pages

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            for letter in letters:
                page_num = 1
                while True:
                    url = (
                        f"{_ROMSGAMES_BASE}/roms/{platform_slug}/"
                        f"?letter={letter}&page={page_num}&sort=alphabetical"
                    )
                    page.goto(url, wait_until="domcontentloaded")

                    links = page.query_selector_all(f"a[href*='/{platform_slug}-rom-']")
                    if not links:
                        break

                    page_hits: list[tuple[str, str]] = []
                    for link in links:
                        title = link.inner_text().strip()
                        href = link.get_attribute("href")
                        if title and href and href not in seen_hrefs:
                            page_hits.append((title, href))
                            seen_hrefs.add(href)

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

    # Normalize titles before fuzzy-matching so that punctuation differences
    # (site uses " - " where queries use ":") and region tags like "(USA)" don't
    # prevent a good match or cause false positives.
    def _norm(s: str) -> str:
        s = re.sub(r"\s*\([^)]*\)", "", s)         # strip (USA), (Rev 1), etc.
        s = re.sub(r"[:\-_'\"/\\,]", " ", s)       # punctuation → space (incl. comma)
        s = re.sub(r"\s*&\s*", " and ", s)          # & → and
        s = re.sub(r"\s+", " ", s).strip().lower()
        # Strip leading/trailing articles so "The X" and "X, The" normalize identically
        words = s.split()
        if words and words[0] in ("the", "a", "an"):
            words = words[1:]
        if words and words[-1] in ("the", "a", "an"):
            words = words[:-1]
        return " ".join(words)

    norm_query = _norm(game_name)
    # Map normalised form → first original title that produces it
    norm_map: dict[str, str] = {}
    for title, _ in candidates:
        n = _norm(title)
        if n not in norm_map:
            norm_map[n] = title

    close_norms = difflib.get_close_matches(norm_query, list(norm_map), n=5, cutoff=0.6)
    close = [norm_map[n] for n in close_norms]

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


def download(result: SearchResult, dest_path: str, headless: bool = True) -> str:
    """
    Download result to disk. Returns the actual path saved, which may have a
    different extension than dest_path if the site serves a zip/7z archive.
    """
    _check_browser()
    return _romsgames_download(result, dest_path, headless=headless)


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


def _stream_download(url: str, dest_path: str, extra_headers: dict | None = None) -> str:
    """Download *url* to *dest_path* with a Rich progress bar. Returns dest_path."""
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        total = int(resp.headers.get("Content-Length", 0)) or None
        with Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(pathlib.Path(dest_path).name, total=total)
            with open(dest_path, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
                    progress.advance(task, len(chunk))

    return dest_path


def _romsgames_download(result: SearchResult, dest_path: str, headless: bool = True) -> str:
    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(accept_downloads=True)
            direct_urls: list[str] = []

            page = context.new_page()
            _attach_response_watcher(page, direct_urls)

            page.goto(result.url, wait_until="domcontentloaded")
            page.wait_for_timeout(2_000)  # let JS render the download button

            if direct_urls:
                return _stream_download(direct_urls[0], dest_path)

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
            try:
                with page.expect_download(timeout=90_000) as dl_info:
                    for _ in range(2):
                        loc.click()
                        page.wait_for_timeout(2_000)
                        for pg in context.pages[1:]:
                            pg.close()
                        page.wait_for_timeout(500)

                dl = dl_info.value

                # Use suggested_filename to get the real extension (may be .zip, .7z, etc.)
                suggested = dl.suggested_filename
                if suggested:
                    actual_path = str(pathlib.Path(dest_path).with_suffix(pathlib.Path(suggested).suffix))
                else:
                    actual_path = dest_path

                # Try streaming directly for speed and a live progress bar.
                # If the server rejects the out-of-browser request (e.g. HTTP 400)
                # fall back to letting Playwright save the captured download instead.
                file_url = direct_urls[0] if direct_urls else dl.url
                if file_url and not file_url.startswith("blob:"):
                    try:
                        cookies = context.cookies()
                        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                        return _stream_download(
                            file_url,
                            actual_path,
                            extra_headers={"Referer": result.url, "Cookie": cookie_str},
                        )
                    except Exception:
                        pass  # stream failed — let Playwright save it below

                # Fallback: Playwright writes the already-captured download to disk
                dl.save_as(actual_path)
                return actual_path

            except PWTimeout:
                pass

            raise RuntimeError(
                f"Could not download from {result.url}. "
                "Try --debug to watch the browser and verify the right button is being clicked."
            )
        finally:
            browser.close()
