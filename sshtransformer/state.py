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
