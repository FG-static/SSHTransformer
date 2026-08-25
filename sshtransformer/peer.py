"""Peer-facing API (LAN). Authenticated after pairing."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import clipboard as clip
from .network import local_identity
from .state import Phase, Role, PeerInfo, state
from .transfer_ops import list_tree, parse_transfer_headers

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


@router.post("/peer-disconnect")
def peer_disconnect(authorization: str | None = Header(default=None)) -> dict:
    """Peer told us it is leaving; drop the link and return to the previous screen."""
    _require_token(authorization)
    import secrets

    with state._lock:
        was_host = state.role == Role.HOST
        state.peer = None
        state.peer_base_url = ""
        state.last_error = ""
        if was_host:
            state.phase = Phase.WAITING
            state.pairing_code = f"{secrets.randbelow(1_000_000):06d}"
            state.session_token = secrets.token_urlsafe(24)
        else:
            state.phase = Phase.ROLE
            state.session_token = ""
    return {"ok": True}


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
    request: Request,
    authorization: str | None = Header(default=None),
    dest: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    _require_token(authorization)
    context = parse_transfer_headers(request.headers)
    if context:
        state.ensure_transfer(
            context["id"],
            direction=context["direction"],
            kind=context["kind"],
            source=context["source"] or (file.filename or dest),
            dest=context["dest"] or dest,
            total_files=context["total_files"],
            total_bytes=context["total_bytes"],
        )
    dest_path = Path(dest).expanduser()
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with dest_path.open("wb") as out:
            if context:
                written = 0
                while True:
                    chunk = file.file.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    state.update_transfer(
                        context["id"],
                        completed_bytes=context["completed_bytes"] + written,
                        completed_files=context["file_index"],
                        current_file=context["current_file"] or file.filename or dest_path.name,
                    )
            else:
                shutil.copyfileobj(file.file, out)
    except OSError as exc:
        if context:
            state.fail_transfer(context["id"], str(exc))
        raise HTTPException(status_code=400, detail=f"Cannot write file: {exc}") from exc

    if context:
        completed_files = context["file_index"] + 1
        state.update_transfer(
            context["id"],
            completed_files=completed_files,
            completed_bytes=context["completed_bytes"] + written,
            current_file="",
        )
        if context["final"] or (
            context["total_files"] > 0 and completed_files >= context["total_files"]
        ):
            _complete_peer_transfer(context["id"], "receive")
    else:
        state.add_transfer_log(
            {
                "action": "receive",
                "dest": str(dest_path),
                "name": file.filename or dest_path.name,
                "ok": True,
            }
        )
    return {"ok": True, "dest": str(dest_path)}


class MkdirBody(BaseModel):
    path: str


@router.post("/mkdir")
def make_dir(
    body: MkdirBody,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_token(authorization)
    context = parse_transfer_headers(request.headers)
    if context:
        state.ensure_transfer(
            context["id"],
            direction=context["direction"],
            kind=context["kind"],
            source=context["source"] or body.path,
            dest=context["dest"] or body.path,
            total_files=context["total_files"],
            total_bytes=context["total_bytes"],
        )
    dest_path = Path(body.path).expanduser()
    try:
        dest_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        if context:
            state.fail_transfer(context["id"], str(exc))
        raise HTTPException(status_code=400, detail=f"Cannot create directory: {exc}") from exc
    if context and context["final"]:
        _complete_peer_transfer(context["id"], "receive")
    return {"ok": True, "path": str(dest_path)}


@router.get("/dir/list")
def list_dir(
    path: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_token(authorization)
    context = parse_transfer_headers(request.headers)
    src = Path(path).expanduser()
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {src}")
    if src.is_file():
        try:
            size = src.stat().st_size
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if context:
            state.ensure_transfer(
                context["id"],
                direction=context["direction"],
                kind="file",
                source=context["source"] or str(src),
                dest=context["dest"],
                total_files=1,
                total_bytes=size,
            )
        return {
            "ok": True,
            "kind": "file",
            "root": str(src),
            "name": src.name,
            "files": [src.name],
            "dirs": [],
            "file_sizes": {src.name: size},
            "size": size,
        }
    if not src.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {src}")
    try:
        tree = list_tree(src)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if context:
        state.ensure_transfer(
            context["id"],
            direction=context["direction"],
            kind="dir",
            source=context["source"] or str(src),
            dest=context["dest"],
            total_files=len(tree["files"]),
            total_bytes=sum(tree.get("file_sizes", {}).values()),
        )
        if not tree["files"]:
            _complete_peer_transfer(context["id"], "send")
    return {"ok": True, "kind": "dir", **tree}


@router.get("/file/fetch", response_model=None)
def fetch_file(
    path: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> FileResponse | StreamingResponse:
    _require_token(authorization)
    src = Path(path).expanduser()
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {src}")
    context = parse_transfer_headers(request.headers)
    if not context:
        return FileResponse(path=src, filename=src.name)

    try:
        size = src.stat().st_size
    except OSError as exc:
        state.fail_transfer(context["id"], str(exc))
        raise HTTPException(status_code=404, detail=f"File not found: {src}") from exc

    state.ensure_transfer(
        context["id"],
        direction=context["direction"],
        kind=context["kind"],
        source=context["source"] or str(src),
        dest=context["dest"],
        total_files=context["total_files"] or 1,
        total_bytes=context["total_bytes"] or size,
    )

    def stream_file():
        sent = 0
        try:
            with src.open("rb") as file_obj:
                while True:
                    chunk = file_obj.read(64 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    state.update_transfer(
                        context["id"],
                        completed_bytes=context["completed_bytes"] + sent,
                        completed_files=context["file_index"],
                        current_file=context["current_file"] or src.name,
                    )
                    yield chunk
            completed_files = context["file_index"] + 1
            state.update_transfer(
                context["id"],
                completed_files=completed_files,
                completed_bytes=context["completed_bytes"] + sent,
                current_file="",
            )
            if context["final"] or (
                context["total_files"] > 0 and completed_files >= context["total_files"]
            ):
                _complete_peer_transfer(context["id"], "send")
        except Exception as exc:  # noqa: BLE001
            state.fail_transfer(context["id"], str(exc))
            raise

    return StreamingResponse(
        stream_file(),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size)},
    )


def _complete_peer_transfer(transfer_id: str, action: str) -> None:
    info = state.complete_transfer(transfer_id)
    if info is None:
        return
    state.add_transfer_log(
        {
            "action": action,
            "source": info["source"],
            "dest": info["dest"],
            "ok": True,
            "files": info["completed_files"],
            "kind": info["kind"],
        }
    )
