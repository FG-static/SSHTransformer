"""Persistent path history and default roots for transfers."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_MAX_ENTRIES = 40
_LOCK = threading.RLock()


def _history_file() -> Path:
    return Path.home() / ".sshtransformer" / "path_history.json"


def load_history() -> list[dict[str, Any]]:
    path = _history_file()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        p = str(item.get("path") or "").strip()
        if not p:
            continue
        cleaned.append(
            {
                "path": p,
                "side": item.get("side") if item.get("side") in {"local", "remote"} else "local",
                "at": item.get("at") or 0,
            }
        )
    return cleaned


def save_history(entries: list[dict[str, Any]]) -> None:
    path = _history_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": entries[:_MAX_ENTRIES]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_paths(*items: tuple[str, str]) -> list[dict[str, Any]]:
    """Remember (path, side) pairs; newest first. side is local|remote."""
    import time

    with _LOCK:
        entries = load_history()
        now = time.time()
        for raw_path, side in reversed(items):
            path = str(raw_path or "").strip()
            if not path:
                continue
            side_norm = side if side in {"local", "remote"} else "local"
            entries = [e for e in entries if e.get("path") != path]
            entries.insert(0, {"path": path, "side": side_norm, "at": now})
        entries = entries[:_MAX_ENTRIES]
        save_history(entries)
        return entries


def history_paths(side: str | None = None) -> list[str]:
    entries = load_history()
    out: list[str] = []
    seen: set[str] = set()
    for item in entries:
        if side and item.get("side") != side:
            continue
        p = item["path"]
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def default_root_for_os(os_str: str | None) -> str:
    """Guess a sensible remote root from peer OS banner."""
    s = (os_str or "").lower()
    if "darwin" in s or "macos" in s or "mac os" in s:
        return "/Users"
    if "linux" in s or "ubuntu" in s or "debian" in s or "fedora" in s:
        return "/home"
    if "windows" in s or "win32" in s or "win64" in s:
        return "C:/Users"
    return "/tmp"


def local_home() -> str:
    return str(Path.home())


def local_default_dir() -> str:
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return str(downloads)
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return str(desktop)
    return str(Path.home())


def suggest_remote_dest(peer_os: str | None, source_path: str) -> str:
    root = default_root_for_os(peer_os).rstrip("/")
    name = Path(source_path).name if source_path else "file"
    # Prefer a shared drop folder under the OS root.
    return f"{root}/{name}"


def suggest_local_dest(source_path: str) -> str:
    name = Path(source_path).name if source_path else "file"
    return str(Path(local_default_dir()) / name)


# --- Host IP history (guest side) ---

_MAX_HOSTS = 20
_HOST_LOCK = threading.RLock()


def _host_history_file() -> Path:
    return Path.home() / ".sshtransformer" / "host_history.json"


def load_host_history() -> list[dict[str, Any]]:
    path = _host_history_file()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("hosts") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in entries:
        if isinstance(item, str):
            ip = item.strip()
            if ip:
                cleaned.append({"ip": ip, "at": 0})
            continue
        if not isinstance(item, dict):
            continue
        ip = str(item.get("ip") or "").strip()
        if not ip:
            continue
        cleaned.append({"ip": ip, "at": item.get("at") or 0})
    return cleaned


def save_host_history(entries: list[dict[str, Any]]) -> None:
    path = _host_history_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"hosts": entries[:_MAX_HOSTS]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remember_host(ip: str) -> list[str]:
    """Remember a successfully connected host IP; newest first."""
    import time

    ip = str(ip or "").strip()
    if not ip:
        return host_ips()
    with _HOST_LOCK:
        entries = [e for e in load_host_history() if e.get("ip") != ip]
        entries.insert(0, {"ip": ip, "at": time.time()})
        entries = entries[:_MAX_HOSTS]
        save_host_history(entries)
        return [e["ip"] for e in entries]


def host_ips() -> list[str]:
    return [e["ip"] for e in load_host_history()]
