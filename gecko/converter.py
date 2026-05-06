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
import subprocess
import sys
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


def needs_conversion(available_fmt: str, desired_fmt: str) -> bool:
    return available_fmt != desired_fmt


def cleanup(path: str) -> None:
    """Delete an intermediate file, ignoring missing-file errors."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
