"""macOS clipboard helpers via pbcopy / pbpaste."""

from __future__ import annotations

import subprocess


def read_clipboard() -> str:
    result = subprocess.run(
        ["pbpaste"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def write_clipboard(text: str) -> None:
    subprocess.run(
        ["pbcopy"],
        input=text,
        check=True,
        text=True,
    )
