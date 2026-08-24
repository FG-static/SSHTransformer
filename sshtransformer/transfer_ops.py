"""Helpers for single-file and recursive folder transfers."""

from __future__ import annotations

import unicodedata
from pathlib import Path


def normalize_unicode(text: str) -> str:
    """Normalize to NFC so macOS NFD names behave on Linux."""
    return unicodedata.normalize("NFC", text)


def normalize_dest_dir(dest: str, source_name: str) -> Path:
    """Resolve the remote/local directory that should hold a transferred folder."""
    dest = normalize_unicode(dest)
    source_name = normalize_unicode(source_name)
    dest_path = Path(dest).expanduser()
    raw = dest.rstrip()
    if raw.endswith(("/", "\\")):
        return dest_path / source_name
    if dest_path.name != source_name:
        # Treat as parent directory (e.g. /home or /Users).
        return dest_path / source_name
    return dest_path


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
    return {
        "root": str(root),
        "name": normalize_unicode(root.name),
        "files": [rel for _, rel in iter_local_files(root)],
        "dirs": iter_local_dirs(root),
    }
