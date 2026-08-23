"""Peer-facing API (LAN). Authenticated after pairing."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import clipboard as clip
from .network import local_identity
from .state import Phase, Role, PeerInfo, state

router = APIRouter()


class PairRequest(BaseModel):
    code: str
    hostname: str = ""
    os: str = ""
    ip: str = ""


class PairResponse(BaseModel):
    ok: bool = True
    token: str
    host: dict
    peer_port: int = 18765


class ClipboardBody(BaseModel):
    text: str = ""


class TextBody(BaseModel):
    text: str = Field(default="")


def _require_token(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.removeprefix("Bearer ").strip()
    with state._lock:
        expected = state.session_token
        ready = state.phase == Phase.READY
    if not expected or token != expected or not ready:
        raise HTTPException(status_code=401, detail="Invalid or inactive session")


@router.post("/pair", response_model=PairResponse)
def pair(body: PairRequest, request: Request) -> PairResponse:
    with state._lock:
        if state.role != Role.HOST or state.phase not in {Phase.WAITING, Phase.READY}:
            raise HTTPException(status_code=400, detail="Host is not accepting pairs")
        if body.code.strip() != state.pairing_code:
            raise HTTPException(status_code=403, detail="Invalid pairing code")

        client_ip = (body.ip or "").strip() or (request.client.host if request.client else "")
        state.peer = PeerInfo(
            hostname=body.hostname or "guest",
            os=body.os or "unknown",
            ip=client_ip,
            role=Role.GUEST.value,
        )
        state.peer_base_url = f"http://{client_ip}:{18765}" if client_ip else ""
        state.phase = Phase.READY
        state.last_error = ""
        token = state.session_token
        selected_ip = state.selected_ip
        local = state.local

    identity = local_identity()
    return PairResponse(
        token=token,
        host={
            "hostname": local.hostname or identity["hostname"],
            "os": local.os or identity["os"],
            "ip": selected_ip,
            "role": Role.HOST.value,
        },
    )


@router.get("/info")
def peer_info(authorization: str | None = Header(default=None)) -> dict:
    _require_token(authorization)
    return state.snapshot()


@router.get("/clipboard")
def get_clipboard(authorization: str | None = Header(default=None)) -> dict:
    _require_token(authorization)
    with state._lock:
        return {
            "text": state.clipboard_text,
            "updated_at": state.clipboard_updated_at,
        }


@router.put("/clipboard")
def put_clipboard(body: ClipboardBody, authorization: str | None = Header(default=None)) -> dict:
    _require_token(authorization)
    state.set_clipboard(body.text)
    return {"ok": True, "updated_at": state.clipboard_updated_at}


@router.post("/system-clipboard")
def set_system_clipboard(body: TextBody, authorization: str | None = Header(default=None)) -> dict:
    """Write text into this machine's OS clipboard (used by peer push)."""
    _require_token(authorization)
    clip.write_clipboard(body.text)
    return {"ok": True}


@router.post("/file")
async def receive_file(
    authorization: str | None = Header(default=None),
    dest: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    _require_token(authorization)
    dest_path = Path(dest).expanduser()
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with dest_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot write file: {exc}") from exc

    state.add_transfer_log(
        {
            "action": "receive",
            "dest": str(dest_path),
            "name": file.filename or dest_path.name,
            "ok": True,
        }
    )
    return {"ok": True, "dest": str(dest_path)}


@router.get("/file/fetch")
def fetch_file(path: str, authorization: str | None = Header(default=None)) -> FileResponse:
    _require_token(authorization)
    src = Path(path).expanduser()
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {src}")
    return FileResponse(path=src, filename=src.name)
