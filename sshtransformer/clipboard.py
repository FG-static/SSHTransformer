"""Cross-platform clipboard helpers (macOS + Linux)."""

from __future__ import annotations

import platform
import shutil
import subprocess
import threading

# Clipboard tools that keep running in the background to own the selection.
_BACKGROUND: list[subprocess.Popen] = []
_BACKGROUND_LOCK = threading.Lock()


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


_LINUX_READ_COMMANDS = (
    ["wl-paste", "--no-newline"],
    ["xclip", "-selection", "clipboard", "-o"],
    ["xsel", "--clipboard", "--output"],
)

_LINUX_WRITE_COMMANDS = (
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
)

_NO_TOOL_HINT = (
    "No clipboard tool found. Install wl-clipboard (Wayland) or xclip/xsel (X11)."
)


def _linux_read() -> str:
    # Try every installed tool; the first one that works wins, so a Wayland
    # tool present in an X11 session (or vice versa) does not break the read.
    errors: list[str] = []
    for cmd in _LINUX_READ_COMMANDS:
        if not shutil.which(cmd[0]):
            continue
        try:
            return _run_out(cmd)
        except ClipboardError as exc:
            errors.append(f"{cmd[0]}: {exc}")
    raise ClipboardError("; ".join(errors) if errors else _NO_TOOL_HINT)


def _linux_write(text: str) -> None:
    errors: list[str] = []
    for cmd in _LINUX_WRITE_COMMANDS:
        if not shutil.which(cmd[0]):
            continue
        try:
            _spawn_selection_owner(cmd, text)
            return
        except ClipboardError as exc:
            errors.append(f"{cmd[0]}: {exc}")
    raise ClipboardError("; ".join(errors) if errors else _NO_TOOL_HINT)


def _spawn_selection_owner(cmd: list[str], text: str) -> None:
    """Feed text to a clipboard tool without waiting on its background owner.

    wl-copy / xclip fork a child that keeps serving the clipboard and
    inherits our stdio pipes; waiting for those pipes to close would block
    until something else takes the clipboard. So write stdin, close it, and
    wait only briefly for the direct child: a quick nonzero exit means the
    tool failed (try the next one), a lingering process means the clipboard
    is now being served.
    """
    _prune_background()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ClipboardError(f"{cmd[0]}: {exc}") from exc

    try:
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
    except BrokenPipeError:
        pass  # Tool bailed out early; the exit status below reports it.
    except OSError as exc:
        proc.kill()
        proc.wait()
        proc.stderr.close()
        raise ClipboardError(f"{cmd[0]}: {exc}") from exc

    try:
        code = proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        # Still running: it owns the clipboard now. Leave it be.
        with _BACKGROUND_LOCK:
            _BACKGROUND.append(proc)
        proc.stderr.close()
        return

    if code != 0:
        # The tool failed, so it forked no background child and stderr EOF
        # is already here — safe to read. Never read it on success: the
        # forked owner inherits the pipe and read() would block forever.
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.stderr.close()
        raise ClipboardError(err or f"{cmd[0]} exited with {code}")
    proc.stderr.close()


def _prune_background() -> None:
    with _BACKGROUND_LOCK:
        _BACKGROUND[:] = [proc for proc in _BACKGROUND if proc.poll() is None]


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
