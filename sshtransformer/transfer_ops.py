"""Helpers for single-file and recursive folder transfers."""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, unquote


def normalize_unicode(text: str) -> str:
    """Normalize to NFC so macOS NFD names behave on Linux."""
    return unicodedata.normalize("NFC", text)


def normalize_dest_dir(dest: str, source_name: str) -> Path:
    """Resolve the remote/local directory that should hold a transferred folder."""
    dest = normalize_unicode(dest)
    source_name = normalize_unicode(source_name)
    source_component = PurePosixPath(source_name)
    if (
        not source_name
        or len(source_component.parts) != 1
        or source_component.parts[0] in {".", ".."}
    ):
        raise ValueError(f"Invalid transfer folder name: {source_name!r}")
    dest_path = Path(dest).expanduser()
    raw = dest.rstrip()
    if raw.endswith(("/", "\\")):
        return dest_path / source_name
    if dest_path.name != source_name:
        # Treat as parent directory (e.g. /home or /Users).
        return dest_path / source_name
    return dest_path


def safe_relative_path(value: str) -> Path:
    """Convert a peer-provided POSIX relative path without allowing traversal."""
    raw = normalize_unicode(str(value or ""))
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or not candidate.parts
        or any(part in {".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"Invalid relative transfer path: {value!r}")
    return Path(*candidate.parts)


def iter_local_files(root: Path) -> list[tuple[Path, str]]:
    """Return (absolute file path, posix relative path) under root. Skips symlinks."""
    root = root.resolve()
    files: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_file():
            rel = normalize_unicode(path.relative_to(root).as_posix())
            files.append((path, rel))
    return files


def iter_local_dirs(root: Path) -> list[str]:
    """Return relative directory paths (posix), including empty dirs."""
    root = root.resolve()
    dirs: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir():
            dirs.append(normalize_unicode(path.relative_to(root).as_posix()))
    return dirs


def list_tree(root: Path) -> dict:
    root = Path(root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")
    files = iter_local_files(root)
    return {
        "root": str(root),
        "name": normalize_unicode(root.name),
        "files": [rel for _, rel in files],
        "dirs": iter_local_dirs(root),
        "file_sizes": {rel: path.stat().st_size for path, rel in files},
    }


class ProgressReader:
    """File-like wrapper that reports bytes consumed by httpx multipart upload."""

    def __init__(self, file_obj: Any, on_read: Callable[[int], None]) -> None:
        self._file = file_obj
        self._on_read = on_read

    @property
    def name(self) -> str:
        return str(getattr(self._file, "name", "upload"))

    def read(self, size: int = -1) -> bytes:
        data = self._file.read(size)
        if data:
            self._on_read(len(data))
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()


def make_transfer_headers(
    *,
    transfer_id: str,
    direction: str,
    kind: str,
    source: str = "",
    dest: str = "",
    total_files: int = 0,
    total_bytes: int = 0,
    file_index: int | None = None,
    completed_bytes: int = 0,
    current_file: str = "",
    final: bool = False,
) -> dict[str, str]:
    """Build ASCII-only headers shared by both peers for progress reporting."""
    headers = {
        "X-Transfer-Id": transfer_id,
        "X-Transfer-Direction": direction,
        "X-Transfer-Kind": kind,
        "X-Transfer-Total-Files": str(max(0, total_files)),
        "X-Transfer-Total-Bytes": str(max(0, total_bytes)),
        "X-Transfer-Completed-Bytes": str(max(0, completed_bytes)),
    }
    if source:
        headers["X-Transfer-Source"] = quote(source, safe="")
    if dest:
        headers["X-Transfer-Dest"] = quote(dest, safe="")
    if current_file:
        headers["X-Transfer-File"] = quote(current_file, safe="")
    if file_index is not None:
        headers["X-Transfer-File-Index"] = str(max(0, file_index))
    if final:
        headers["X-Transfer-Final"] = "1"
    return headers


def parse_transfer_headers(headers: Any) -> dict[str, Any] | None:
    """Decode transfer metadata from HTTP headers; absent metadata means legacy peer."""

    def get(name: str) -> str:
        return str(headers.get(name, "") or "")

    transfer_id = get("X-Transfer-Id")
    if not transfer_id:
        return None

    def integer(name: str) -> int:
        try:
            return max(0, int(get(name) or 0))
        except ValueError:
            return 0

    def text(name: str) -> str:
        value = get(name)
        try:
            return unquote(value) if value else ""
        except (UnicodeError, ValueError):
            return value

    return {
        "id": transfer_id,
        "direction": get("X-Transfer-Direction") or "receive",
        "kind": get("X-Transfer-Kind") or "file",
        "source": text("X-Transfer-Source"),
        "dest": text("X-Transfer-Dest"),
        "total_files": integer("X-Transfer-Total-Files"),
        "total_bytes": integer("X-Transfer-Total-Bytes"),
        "completed_bytes": integer("X-Transfer-Completed-Bytes"),
        "file_index": integer("X-Transfer-File-Index"),
        "current_file": text("X-Transfer-File"),
        "final": get("X-Transfer-Final") in {"1", "true", "yes"},
    }
