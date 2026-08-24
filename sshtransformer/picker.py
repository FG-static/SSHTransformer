"""Native file/folder picker. macOS first; Linux later."""

from __future__ import annotations

import platform
import subprocess


class PickerError(RuntimeError):
    pass


class PickerCancelled(Exception):
    pass


def picker_available() -> bool:
    return platform.system() == "Darwin"


def pick_path(kind: str) -> str:
    """Open a native dialog and return a POSIX path.

    kind: "file" | "folder"
    """
    kind = kind.strip().lower()
    if kind not in {"file", "folder"}:
        raise PickerError('kind must be "file" or "folder"')

    system = platform.system()
    if system == "Darwin":
        return _mac_pick(kind)
    raise PickerError("本机文件选择器暂仅支持 macOS，Linux 版稍后提供")


def _mac_pick(kind: str) -> str:
    """Use Finder + osascript via stdin so the dialog can come to front."""
    if kind == "file":
        script = """
tell application "Finder"
    activate
    try
        set chosen to choose file with prompt "选择要传输的文件"
        return POSIX path of (chosen as alias)
    on error number -128
        return ""
    end try
end tell
"""
    else:
        script = """
tell application "Finder"
    activate
    try
        set chosen to choose folder with prompt "选择文件夹"
        return POSIX path of (chosen as alias)
    on error number -128
        return ""
    end try
end tell
"""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "AppleScript"],
            input=script,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise PickerError("选择超时，请重试") from exc
    except OSError as exc:
        raise PickerError(f"无法启动 osascript: {exc}") from exc

    path = (result.stdout or "").strip()
    err = (result.stderr or "").strip()

    if path:
        return path

    # User cancelled, or AppleScript failed without stdout.
    if result.returncode in {0, 1} and not err:
        raise PickerCancelled()
    if "User canceled" in err or "-128" in err:
        raise PickerCancelled()

    detail = err or f"osascript exit {result.returncode}"
    raise PickerError(
        f"无法打开文件选择器（{detail}）。"
        "若系统弹出权限请求，请允许「终端 / Cursor / Python」控制 Finder。"
    )
