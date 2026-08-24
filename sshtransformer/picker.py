"""Native file/folder picker for macOS and common Linux desktops."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path


class PickerError(RuntimeError):
    pass


class PickerCancelled(Exception):
    pass


def picker_available() -> bool:
    system = platform.system()
    if system == "Darwin":
        return shutil.which("osascript") is not None
    if system == "Linux":
        return any(shutil.which(command) for command in ("zenity", "kdialog", "yad"))
    return False


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
    if system == "Linux":
        return _linux_pick(kind)
    raise PickerError(f"暂不支持 {system} 系统的文件选择器")


def _linux_pick(kind: str) -> str:
    """Use the first available GTK/KDE desktop picker on Linux."""
    if shutil.which("zenity"):
        command = [
            "zenity",
            "--file-selection",
            "--title=选择要传输的文件" if kind == "file" else "--title=选择文件夹",
        ]
        if kind == "folder":
            command.append("--directory")
    elif shutil.which("kdialog"):
        title = "选择要传输的文件" if kind == "file" else "选择文件夹"
        command = ["kdialog", "--title", title]
        command.extend(
            ["--getopenfilename", str(Path.home()), "*"]
            if kind == "file"
            else ["--getexistingdirectory", str(Path.home())]
        )
    elif shutil.which("yad"):
        title = "选择要传输的文件" if kind == "file" else "选择文件夹"
        command = ["yad", "--file-selection", f"--title={title}"]
        if kind == "folder":
            command.append("--directory")
    else:
        raise PickerError(
            "未找到 Linux 文件选择器，请安装 zenity、kdialog 或 yad。"
        )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise PickerError("选择超时，请重试") from exc
    except OSError as exc:
        raise PickerError(f"无法启动文件选择器: {exc}") from exc

    path = (result.stdout or "").strip()
    if path:
        return path
    if result.returncode in {0, 1}:
        raise PickerCancelled()
    detail = (result.stderr or "").strip() or f"picker exit {result.returncode}"
    raise PickerError(f"Linux 文件选择器失败（{detail}）")


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
