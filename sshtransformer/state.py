"""In-memory session state for host/guest pairing and buffers."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    NONE = "none"
    HOST = "host"
    GUEST = "guest"


class Phase(str, Enum):
    BOOT = "boot"
    ROLE = "role"
    WAITING = "waiting"
    CONNECTING = "connecting"
    READY = "ready"
    ERROR = "error"


@dataclass
class PeerInfo:
    hostname: str = ""
    os: str = ""
    ip: str = ""
    role: str = ""


@dataclass
class TransferTask:
    """Progress state for one local or peer-side transfer."""

    id: str
    direction: str
    kind: str
    source: str
    dest: str
    status: str = "running"
    total_files: int = 0
    completed_files: int = 0
    total_bytes: int = 0
    completed_bytes: int = 0
    current_file: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self) -> dict[str, Any]:
        if self.total_bytes > 0:
            progress = round(self.completed_bytes * 100 / self.total_bytes)
        elif self.total_files > 0:
            progress = round(self.completed_files * 100 / self.total_files)
        else:
            progress = 100 if self.status == "completed" else 0

        return {
            "id": self.id,
            "direction": self.direction,
            "kind": self.kind,
            "source": self.source,
            "dest": self.dest,
            "status": self.status,
            "total_files": self.total_files,
            "completed_files": self.completed_files,
            "total_bytes": self.total_bytes,
            "completed_bytes": self.completed_bytes,
            "current_file": self.current_file,
            "progress": max(0, min(100, progress)),
            "error": self.error,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }


@dataclass
class SessionState:
    role: Role = Role.NONE
    phase: Phase = Phase.ROLE
    pairing_code: str = ""
    session_token: str = ""
    selected_ip: str = ""
    local: PeerInfo = field(default_factory=PeerInfo)
    peer: PeerInfo | None = None
    peer_base_url: str = ""
    clipboard_text: str = ""
    clipboard_updated_at: float | None = None
    last_error: str = ""
    transfer_log: list[dict[str, Any]] = field(default_factory=list)
    transfer_tasks: dict[str, TransferTask] = field(default_factory=dict)
    _transfer_order: list[str] = field(default_factory=list, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "role": self.role.value,
                "phase": self.phase.value,
                "pairing_code": self.pairing_code,
                "selected_ip": self.selected_ip,
                "local": {
                    "hostname": self.local.hostname,
                    "os": self.local.os,
                    "ip": self.selected_ip or self.local.ip,
                    "role": self.role.value if self.role != Role.NONE else "",
                },
                "peer": None
                if self.peer is None
                else {
                    "hostname": self.peer.hostname,
                    "os": self.peer.os,
                    "ip": self.peer.ip,
                    "role": self.peer.role,
                },
                "clipboard_text": self.clipboard_text,
                "clipboard_updated_at": self.clipboard_updated_at,
                "last_error": self.last_error,
                "transfer_log": list(self.transfer_log[-20:]),
                "transfers": self._transfer_snapshots_locked(),
                "connected": self.phase == Phase.READY and self.peer is not None,
            }

    def set_clipboard(self, text: str) -> None:
        with self._lock:
            self.clipboard_text = text
            self.clipboard_updated_at = time.time()

    def add_transfer_log(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entry = {**entry, "at": time.time()}
            self.transfer_log.append(entry)
            self.transfer_log = self.transfer_log[-50:]

    def start_transfer(
        self,
        *,
        direction: str,
        kind: str,
        source: str,
        dest: str,
        total_files: int = 0,
        total_bytes: int = 0,
    ) -> str:
        transfer_id = secrets.token_urlsafe(12)
        with self._lock:
            self.transfer_tasks[transfer_id] = TransferTask(
                id=transfer_id,
                direction=direction,
                kind=kind,
                source=source,
                dest=dest,
                total_files=max(0, total_files),
                total_bytes=max(0, total_bytes),
            )
            self._transfer_order.insert(0, transfer_id)
            self._prune_transfer_tasks_locked()
        return transfer_id

    def ensure_transfer(
        self,
        transfer_id: str,
        *,
        direction: str,
        kind: str,
        source: str,
        dest: str,
        total_files: int = 0,
        total_bytes: int = 0,
    ) -> str:
        """Create a peer-side task or enrich an existing task from headers."""
        with self._lock:
            task = self.transfer_tasks.get(transfer_id)
            if task is None:
                task = TransferTask(
                    id=transfer_id,
                    direction=direction,
                    kind=kind,
                    source=source,
                    dest=dest,
                    total_files=max(0, total_files),
                    total_bytes=max(0, total_bytes),
                )
                self.transfer_tasks[transfer_id] = task
                self._transfer_order.insert(0, transfer_id)
            else:
                if total_files > 0:
                    task.total_files = total_files
                if total_bytes > 0:
                    task.total_bytes = total_bytes
                if source:
                    task.source = source
                if dest:
                    task.dest = dest
                if kind:
                    task.kind = kind
                task.updated_at = time.time()
            self._prune_transfer_tasks_locked()
        return transfer_id

    def update_transfer(
        self,
        transfer_id: str,
        *,
        completed_files: int | None = None,
        completed_bytes: int | None = None,
        total_files: int | None = None,
        total_bytes: int | None = None,
        current_file: str | None = None,
        kind: str | None = None,
    ) -> None:
        with self._lock:
            task = self.transfer_tasks.get(transfer_id)
            if task is None:
                return
            if completed_files is not None:
                task.completed_files = max(0, completed_files)
            if completed_bytes is not None:
                task.completed_bytes = max(0, completed_bytes)
            if total_files is not None:
                task.total_files = max(0, total_files)
            if total_bytes is not None:
                task.total_bytes = max(0, total_bytes)
            if current_file is not None:
                task.current_file = current_file
            if kind:
                task.kind = kind
            task.updated_at = time.time()

    def complete_transfer(self, transfer_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self.transfer_tasks.get(transfer_id)
            if task is None:
                return None
            if task.total_files > 0:
                task.completed_files = task.total_files
            if task.total_bytes > 0:
                task.completed_bytes = task.total_bytes
            task.status = "completed"
            task.current_file = ""
            task.error = ""
            task.updated_at = time.time()
            task.finished_at = task.updated_at
            return task.snapshot()

    def fail_transfer(self, transfer_id: str, error: str) -> dict[str, Any] | None:
        with self._lock:
            task = self.transfer_tasks.get(transfer_id)
            if task is None:
                return None
            task.status = "failed"
            task.error = error
            task.current_file = ""
            task.updated_at = time.time()
            task.finished_at = task.updated_at
            return task.snapshot()

    def transfer_info(self, transfer_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self.transfer_tasks.get(transfer_id)
            return task.snapshot() if task else None

    def transfer_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._transfer_snapshots_locked()

    def _transfer_snapshots_locked(self) -> list[dict[str, Any]]:
        return [
            self.transfer_tasks[transfer_id].snapshot()
            for transfer_id in self._transfer_order
            if transfer_id in self.transfer_tasks
        ]

    def _prune_transfer_tasks_locked(self) -> None:
        max_tasks = 40
        if len(self._transfer_order) <= max_tasks:
            return
        keep: list[str] = []
        for transfer_id in self._transfer_order:
            task = self.transfer_tasks.get(transfer_id)
            if task is None:
                continue
            if len(keep) < max_tasks or task.status == "running":
                keep.append(transfer_id)
            else:
                self.transfer_tasks.pop(transfer_id, None)
        self._transfer_order = keep

    def reset_pairing(self) -> None:
        with self._lock:
            self.peer = None
            self.peer_base_url = ""
            self.session_token = ""
            self.last_error = ""
            if self.role == Role.HOST:
                self.phase = Phase.WAITING
                self.pairing_code = f"{secrets.randbelow(1_000_000):06d}"
                self.session_token = secrets.token_urlsafe(24)
            else:
                self.phase = Phase.ROLE
                self.role = Role.NONE
                self.pairing_code = ""


state = SessionState()
