"""Cross-platform clipboard helpers (macOS + Linux)."""

from __future__ import annotations

import platform
import shutil
import subprocess


class ClipboardError(RuntimeError):
    pass


def read_clipboard() -> str:
    system = platform.system()
    if system == "Darwin":
        return _run_out(["pbpaste"])
    if system == "Linux":
        return _linux_read()
    raise ClipboardError(f"Unsupported system for clipboard: {system}")


def write_clipboard(text: str) -> None:
    system = platform.system()
    if system == "Darwin":
        _run_in(["pbcopy"], text)
        return
    if system == "Linux":
        _linux_write(text)
        return
    raise ClipboardError(f"Unsupported system for clipboard: {system}")


def _linux_read() -> str:
    # Wayland first, then X11 tools.
    for cmd in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ):
        if shutil.which(cmd[0]):
            return _run_out(cmd)
    raise ClipboardError(
        "No clipboard tool found. Install wl-clipboard (Wayland) or xclip/xsel (X11)."
    )


def _linux_write(text: str) -> None:
    for cmd in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        if shutil.which(cmd[0]):
            _run_in(cmd, text)
            return
    raise ClipboardError(
        "No clipboard tool found. Install wl-clipboard (Wayland) or xclip/xsel (X11)."
    )


def _run_out(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ClipboardError(f"Clipboard command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ClipboardError(err or f"Clipboard read failed: {cmd[0]}") from exc
    return result.stdout


def _run_in(cmd: list[str], text: str) -> None:
    try:
        subprocess.run(cmd, input=text, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise ClipboardError(f"Clipboard command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        raise ClipboardError(err or f"Clipboard write failed: {cmd[0]}") from exc
