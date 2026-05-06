"""
DolphinTool wrapper and binary resolution.

Binaries are bundled under gecko/bin/ and named:
  dolphintool-win64.exe
  dolphintool-linux64
  dolphintool-macos-arm64
  dolphintool-macos-x64
"""

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


_BIN_DIR = Path(__file__).parent / "bin"

_BINARY_MAP: dict[tuple[str, str], str] = {
    ("win32",  "amd64"):   "dolphintool-win64.exe",
    ("linux",  "x86_64"):  "dolphintool-linux64",
    ("darwin", "arm64"):   "dolphintool-macos-arm64",
    ("darwin", "x86_64"):  "dolphintool-macos-x64",
}


def get_dolphintool_path() -> Path:
    """Resolve the correct bundled dolphintool binary for the current OS/arch."""
    sys_key = sys.platform          # win32 | linux | darwin
    arch_key = platform.machine().lower()

    binary_name = _BINARY_MAP.get((sys_key, arch_key))
    if binary_name is None:
        raise RuntimeError(
            f"No bundled dolphintool for platform '{sys_key}' / arch '{arch_key}'. "
            "Supported: win64, linux64, macos-arm64, macos-x64."
        )

    path = _BIN_DIR / binary_name
    if not path.exists():
        raise FileNotFoundError(
            f"dolphintool binary not found at {path}. "
            "Re-install gecko or manually place the binary there."
        )

    # Ensure executable bit is set (no-op on Windows)
    if sys_key != "win32":
        path.chmod(path.stat().st_mode | 0o111)

    return path


def convert(input_path: str, output_path: str, output_fmt: str) -> None:
    """
    Convert *input_path* to *output_fmt* at *output_path* using dolphintool.
    Raises subprocess.CalledProcessError on non-zero exit.
    """
    tool = get_dolphintool_path()
    cmd = [str(tool), "convert", "-i", input_path, "-o", output_path, "-f", output_fmt]
    subprocess.run(cmd, check=True)


_ARCHIVE_EXTS = {".zip", ".7z", ".rar"}
_ROM_EXTS = {".iso", ".rvz", ".gcz", ".nds", ".gba", ".sfc", ".smc", ".n64", ".z64"}

# Magic byte signatures → format string
_MAGIC: list[tuple[bytes, str]] = [
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),   # empty zip
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
    (b"RVZ\x01", "rvz"),
]


def detect_format(path: str) -> str | None:
    """Return the actual file format detected from magic bytes, or None if unknown."""
    with open(path, "rb") as f:
        header = f.read(8)
    for magic, fmt in _MAGIC:
        if header[: len(magic)] == magic:
            return fmt
    return None


def is_archive(path: str) -> bool:
    """Return True if the file is a known archive format (by content, not extension)."""
    return detect_format(path) in {"zip", "7z", "rar"}


def extract_archive(archive_path: str, dest_dir: str) -> str:
    """
    Extract the ROM file from a zip or 7z archive into dest_dir.
    Format is detected from file content (magic bytes), not the extension.
    Returns the path of the extracted ROM file.

    For zip: uses stdlib zipfile.
    For 7z: requires py7zr (pip install py7zr).
    """
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn

    archive = Path(archive_path)
    dest = Path(dest_dir)
    fmt = detect_format(archive_path)

    def _pick_rom(names: list[str]) -> str:
        rom_names = [n for n in names if Path(n).suffix.lower() in _ROM_EXTS]
        return (rom_names or names)[0]

    if fmt == "zip":
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            target_name = _pick_rom([m.filename for m in members])
            target = next(m for m in members if m.filename == target_name)
            out_path = dest / Path(target.filename).name
            with Progress(
                TextColumn("[cyan]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task(f"Extracting {out_path.name}", total=target.file_size)
                with zf.open(target) as src, open(out_path, "wb") as dst:
                    while chunk := src.read(65536):
                        dst.write(chunk)
                        progress.advance(task, len(chunk))
        return str(out_path)

    if fmt == "7z":
        try:
            import py7zr
        except ImportError:
            raise RuntimeError(
                "py7zr is required to extract .7z archives.\n"
                "Run:  pip install py7zr"
            )
        with py7zr.SevenZipFile(archive, mode="r") as zf:
            names = zf.getnames()
            target_name = _pick_rom(names)
            out_path = dest / Path(target_name).name
            zf.extract(dest, targets=[target_name])
            extracted = dest / target_name
            if extracted != out_path:
                shutil.move(str(extracted), str(out_path))
        return str(out_path)

    raise NotImplementedError(
        f"Unrecognised archive format (magic: {detect_format(archive_path)!r}). "
        "Supported: .zip, .7z"
    )


def needs_conversion(available_fmt: str, desired_fmt: str) -> bool:
    return available_fmt != desired_fmt


def cleanup(path: str) -> None:
    """Delete an intermediate file, ignoring missing-file errors."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
