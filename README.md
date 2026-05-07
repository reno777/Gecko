# gecko

Search and download ROMs by game name.

gecko scrapes romsgames.net, filters results to USA releases, selects the best
available revision, and saves the file in your chosen format — converting
automatically when needed.

## Requirements

- Python 3.11+
- Playwright Chromium (used for scraping — see installation below)
- DolphinTool (bundled, no separate install needed)

## Installation

```
pip install -e .
```

Then install the Playwright browser:

```
playwright install chromium
```

## Usage

### Single game

```
gecko fetch --platform gamecube "Super Mario Sunshine"
```

### Specific format

```
gecko fetch --platform gamecube --format iso "Metroid Prime"
```

### From a list file

```
gecko fetch --platform gamecube --list games.txt --output-dir ~/ROMs/GC
```

`games.txt` — one game per line, blank lines and `#` comments ignored:

```
Super Mario Sunshine
Metroid Prime
# skipping this one for now
# The Legend of Zelda: The Wind Waker
F-Zero GX
```

### Force a specific revision

```
gecko fetch --platform gamecube --revision 1 "Mario Party 4"
```

### Debug mode (headed browser)

```
gecko fetch --platform gamecube --debug "Paper Mario: The Thousand-Year Door"
```

## Options

| Flag | Default | Description |
|---|---|---|
| `--platform` | *(required)* | Target platform. Currently: `gamecube` |
| `--format` | `rvz` | Output format. GameCube: `iso`, `rvz`, `gcz` |
| `--list` | — | Path to a `.txt` file of game names, one per line |
| `--revision` | auto | Pin to a specific revision number (e.g. `0`, `1`, `2`) |
| `--output-dir` | `.` | Directory where files are saved (created if missing) |
| `--debug` | off | Launch the browser in headed (visible) mode for diagnosing failures |

## Download pipeline

For each game:

1. Search the romsgames.net catalogue for the closest match
2. Filter to USA region (falls back to any region if no USA release exists)
3. Match the requested format, or find a convertible source format
4. Select the best revision (or the one you pinned with `--revision`)
5. Open the game page and trigger the download (the site uses a countdown — allow up to 60 s before transfer begins)
6. Stream the file to disk with a live progress bar
7. Extract if the file arrived in a zip or 7z archive
8. Convert to your desired format via DolphinTool if needed

## Multi-game behavior

When more than one game is specified (inline or via `--list`), gecko runs two phases:

1. **Concurrent search** — all games are searched in parallel (up to 6 at once) so catalogue lookups overlap.
2. **Queued download** — downloads run one at a time to avoid hammering the server.

Already-downloaded files are detected and skipped automatically. Detection uses
alphanumeric-only name comparison so differences in punctuation, region tags, or
double extensions (e.g. `.nkit.iso`) don't cause a false re-download.

## Region behavior

gecko always prefers USA releases:

1. Exact `(USA)` match
2. Multi-region including USA, e.g. `(USA, Europe)`
3. If no USA release exists at all, falls back to any available region

Non-USA-only releases (Europe, Japan, Korea, etc.) are filtered out unless no
USA version can be found.

## Revision behavior

When multiple revisions exist, gecko applies this priority by default:

**Rev 1 > Rev 0 > (no tag) > Rev 2+**

Rev 1 is preferred because it typically fixes the most critical bugs without
introducing new issues. Override with `--revision N`.

## Format conversion

If the desired format isn't directly available, gecko downloads a convertible
source and converts it automatically:

| Platform | Desired | Downloaded | Tool |
|---|---|---|---|
| GameCube | `iso` | `rvz` | DolphinTool |

The source file is deleted after a successful conversion.

## Bundled DolphinTool

gecko ships DolphinTool binaries for:

- Windows x64 (`gecko/bin/dolphintool-win64.exe`)
- Linux x64 (`gecko/bin/dolphintool-linux64`)
- macOS Apple Silicon (`gecko/bin/dolphintool-macos-arm64`)
- macOS Intel (`gecko/bin/dolphintool-macos-x64`)

The correct binary is selected automatically at runtime.

**DolphinTool is licensed under the GNU General Public License v2 (GPL-2.0).**
Source: https://github.com/dolphin-emu/dolphin
