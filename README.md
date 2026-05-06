# gecko

Search and download ROMs by game name.

## Installation

```
pip install -e .
```

Then install the Playwright browser (required — gecko uses headless Chromium to scrape ROM sites):

```
playwright install chromium
```

## Usage

### Fetch a single game

```
gecko fetch --platform gamecube --format iso "Super Mario Sunshine"
```

### Fetch from a list

```
gecko fetch --platform gamecube --format rvz --list games.txt
```

`games.txt` — one game name per line, blank lines and `#` comments ignored:

```
Super Mario Sunshine
Metroid Prime
The Legend of Zelda: The Wind Waker
```

### Force a specific revision

```
gecko fetch --platform gamecube --format iso --revision 0 "Metroid Prime"
```

### Save to a specific directory

```
gecko fetch --platform gamecube --format iso --output-dir ~/ROMs/GC "F-Zero GX"
```

## Options

| Flag | Description |
|---|---|
| `--platform` | Target platform (required). Currently: `gamecube` |
| `--format` | Output format. GameCube: `iso`, `rvz`, `gcz`. Defaults to `rvz`. |
| `--list` | Path to a `.txt` file of game names (one per line) |
| `--revision` | Force a specific revision number (default: auto, prefers Rev 1) |
| `--output-dir` | Directory for downloaded files (default: `.`) |

## Region behavior

gecko always prefers USA releases:

1. Exact `(USA)` match
2. Multi-region including USA, e.g. `(USA, Europe)`
3. If no USA release exists at all, falls back to whatever is available and warns you

Non-USA-only releases (Europe, Japan, Korea, Australia, etc.) are filtered out unless
no USA version can be found.

## Revision behavior

When multiple revisions exist, gecko applies this priority by default:

**Rev 1 > Rev 0 > (no tag) > Rev 2+**

Rev 1 is preferred because it typically fixes the most critical bugs without
introducing new issues. Override with `--revision N`.

## Format conversion

If the desired format isn't directly available, gecko downloads a convertible
format and converts it automatically:

| Platform | Desired | Downloaded | Tool |
|---|---|---|---|
| GameCube | `iso` | `rvz` | dolphintool |

The intermediate file is deleted after a successful conversion.

## Bundled DolphinTool

gecko ships DolphinTool binaries for:

- Windows x64 (`gecko/bin/dolphintool-win64.exe`)
- Linux x64 (`gecko/bin/dolphintool-linux64`)
- macOS Apple Silicon (`gecko/bin/dolphintool-macos-arm64`)
- macOS Intel (`gecko/bin/dolphintool-macos-x64`)

The correct binary is selected automatically at runtime. You do not need to
install DolphinTool separately.

**DolphinTool is licensed under the GNU General Public License v2 (GPL-2.0).**
Source: https://github.com/dolphin-emu/dolphin
