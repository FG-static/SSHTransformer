"""Local WebUI control API (localhost only)."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import clipboard as clip
from .network import list_lan_ips, local_identity
from .state import Phase, Role, PeerInfo, state

router = APIRouter(prefix="/api")

PEER_PORT = 18765


class RoleBody(BaseModel):
    role: str
    ip: str | None = None


class ConnectBody(BaseModel):
    host: str
    code: str


class ClipboardBody(BaseModel):
    text: str = ""


class TransferBody(BaseModel):
    source_path: str = Field(..., min_length=1)
    dest_path: str = Field(..., min_length=1)
    direction: str = Field(description="send = local→peer, receive = peer→local")


def _peer_headers() -> dict[str, str]:
    with state._lock:
        token = state.session_token
    if not token:
        raise HTTPException(status_code=400, detail="Not paired")
    return {"Authorization": f"Bearer {token}"}


def enriched_status() -> dict:
    snap = state.snapshot()
    snap["ips"] = list_lan_ips()
    return snap


@router.get("/status")
def status() -> dict:
    return enriched_status()


@router.post("/role")
def choose_role(body: RoleBody) -> dict:
    identity = local_identity()
    ips = list_lan_ips()

    if body.role == "host":
        import secrets

        selected = body.ip or (ips[0] if ips else "")
        with state._lock:
            state.role = Role.HOST
            state.phase = Phase.WAITING
            state.pairing_code = f"{secrets.randbelow(1_000_000):06d}"
            state.session_token = secrets.token_urlsafe(24)
            state.selected_ip = selected
            state.local = PeerInfo(
                hostname=identity["hostname"],
                os=identity["os"],
                ip=selected,
                role=Role.HOST.value,
            )
            state.peer = None
            state.last_error = ""
        return enriched_status()

    if body.role == "guest":
        with state._lock:
            state.role = Role.GUEST
            state.phase = Phase.ROLE
            state.pairing_code = ""
            state.session_token = ""
            state.selected_ip = ""
            state.local = PeerInfo(
                hostname=identity["hostname"],
                os=identity["os"],
                ip=ips[0] if ips else "",
                role=Role.GUEST.value,
            )
            state.peer = None
            state.last_error = ""
        return enriched_status()

    raise HTTPException(status_code=400, detail="role must be host or guest")


@router.post("/connect")
def connect_guest(body: ConnectBody) -> dict:
    identity = local_identity()
    host = body.host.strip().split("://")[-1].split("/")[0].split(":")[0]
    if not host:
        raise HTTPException(status_code=400, detail="Host IP required")

    guest_ips = list_lan_ips()
    guest_ip = guest_ips[0] if guest_ips else ""

    with state._lock:
        state.role = Role.GUEST
        state.phase = Phase.CONNECTING
        state.last_error = ""
        state.local = PeerInfo(
            hostname=identity["hostname"],
            os=identity["os"],
            ip=guest_ip,
            role=Role.GUEST.value,
        )

    url = f"http://{host}:{PEER_PORT}/pair"
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(
                url,
                json={
                    "code": body.code.strip(),
                    "hostname": identity["hostname"],
                    "os": identity["os"],
                    "ip": guest_ip,
                },
            )
            if resp.status_code >= 400:
                detail = resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
                raise HTTPException(status_code=400, detail=str(detail) or "Pairing failed")
            data = resp.json()
    except HTTPException:
        with state._lock:
            state.phase = Phase.ERROR
            state.last_error = "Pairing failed"
        raise
    except httpx.HTTPError as exc:
        with state._lock:
            state.phase = Phase.ERROR
            state.last_error = f"Cannot reach host: {exc}"
        raise HTTPException(status_code=400, detail=f"Cannot reach host at {host}:{PEER_PORT}") from exc

    host_info = data.get("host") or {}
    with state._lock:
        state.session_token = data["token"]
        state.peer_base_url = f"http://{host}:{PEER_PORT}"
        state.peer = PeerInfo(
            hostname=host_info.get("hostname", "host"),
            os=host_info.get("os", ""),
            ip=host_info.get("ip") or host,
            role=Role.HOST.value,
        )
        state.phase = Phase.READY
        state.last_error = ""

    return enriched_status()


@router.post("/disconnect")
def disconnect() -> dict:
    """Drop peer link. Host returns to waiting with a fresh code; guest returns to connect form."""
    with state._lock:
        was_host = state.role == Role.HOST
        state.peer = None
        state.peer_base_url = ""
        state.last_error = ""
        if was_host:
            import secrets

            state.phase = Phase.WAITING
            state.pairing_code = f"{secrets.randbelow(1_000_000):06d}"
            state.session_token = secrets.token_urlsafe(24)
        else:
            state.phase = Phase.ROLE
            state.session_token = ""
    return enriched_status()


@router.post("/reset")
def reset_session() -> dict:
    """Return to role selection."""
    with state._lock:
        state.role = Role.NONE
        state.phase = Phase.ROLE
        state.pairing_code = ""
        state.session_token = ""
        state.selected_ip = ""
        state.peer = None
        state.peer_base_url = ""
        state.clipboard_text = ""
        state.clipboard_updated_at = None
        state.transfer_log.clear()
        state.last_error = ""
    return enriched_status()


@router.get("/clipboard")
def get_clipboard() -> dict:
    with state._lock:
        return {"text": state.clipboard_text, "updated_at": state.clipboard_updated_at}


@router.post("/clipboard")
def set_clipboard(body: ClipboardBody) -> dict:
    state.set_clipboard(body.text)
    return {"ok": True, "updated_at": state.clipboard_updated_at}


@router.post("/clipboard/from-system")
def clipboard_from_system() -> dict:
    try:
        text = clip.read_clipboard()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Read clipboard failed: {exc}") from exc
    state.set_clipboard(text)
    return {"ok": True, "text": text, "updated_at": state.clipboard_updated_at}


@router.post("/clipboard/to-system")
def clipboard_to_system() -> dict:
    with state._lock:
        text = state.clipboard_text
    try:
        clip.write_clipboard(text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Write clipboard failed: {exc}") from exc
    return {"ok": True}


@router.post("/clipboard/push")
def clipboard_push() -> dict:
    """Push local staging buffer to peer's staging buffer."""
    with state._lock:
        if state.phase != Phase.READY or not state.peer_base_url:
            raise HTTPException(status_code=400, detail="Not connected")
        text = state.clipboard_text
        target = state.peer_base_url

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.put(f"{target}/clipboard", json={"text": text}, headers=_peer_headers())
            if resp.status_code >= 400:
                raise HTTPException(status_code=400, detail=resp.text)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Push failed: {exc}") from exc
    return {"ok": True}


@router.post("/clipboard/pull")
def clipboard_pull() -> dict:
    with state._lock:
        if state.phase != Phase.READY or not state.peer_base_url:
            raise HTTPException(status_code=400, detail="Not connected")
        target = state.peer_base_url

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{target}/clipboard", headers=_peer_headers())
            if resp.status_code >= 400:
                raise HTTPException(status_code=400, detail=resp.text)
            data = resp.json()
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Pull failed: {exc}") from exc

    text = data.get("text", "")
    state.set_clipboard(text)
    return {"ok": True, "text": text, "updated_at": state.clipboard_updated_at}


@router.post("/transfer")
def transfer_file(body: TransferBody) -> dict:
    direction = body.direction.strip().lower()
    if direction not in {"send", "receive"}:
        raise HTTPException(status_code=400, detail="direction must be send or receive")

    with state._lock:
        if state.phase != Phase.READY or not state.peer_base_url:
            raise HTTPException(status_code=400, detail="Not connected")
        target = state.peer_base_url

    src = Path(body.source_path).expanduser()
    dest = body.dest_path

    if direction == "send":
        if not src.is_file():
            raise HTTPException(status_code=400, detail=f"Source not found: {src}")
        try:
            with httpx.Client(timeout=None) as client:
                with src.open("rb") as f:
                    resp = client.post(
                        f"{target}/file",
                        headers=_peer_headers(),
                        files={"file": (src.name, f)},
                        data={"dest": dest},
                    )
                if resp.status_code >= 400:
                    raise HTTPException(status_code=400, detail=resp.text)
        except HTTPException:
            raise
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=400, detail=f"Transfer failed: {exc}") from exc

        state.add_transfer_log(
            {"action": "send", "source": str(src), "dest": dest, "ok": True}
        )
        return {"ok": True, "direction": "send", "source": str(src), "dest": dest}

    try:
        with httpx.Client(timeout=None) as client:
            resp = client.get(
                f"{target}/file/fetch",
                params={"path": body.source_path},
                headers=_peer_headers(),
            )
            if resp.status_code >= 400:
                raise HTTPException(status_code=400, detail=resp.text)
            dest_path = Path(dest).expanduser()
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(resp.content)
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"Transfer failed: {exc}") from exc

    state.add_transfer_log(
        {
            "action": "receive",
            "source": body.source_path,
            "dest": str(dest_path),
            "ok": True,
        }
    )
    return {"ok": True, "direction": "receive", "source": body.source_path, "dest": str(dest_path)}
