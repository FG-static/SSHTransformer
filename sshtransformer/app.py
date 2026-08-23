"""FastAPI applications for local UI and peer LAN API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .local_api import router as local_router
from .network import local_identity
from .peer import router as peer_router
from .state import PeerInfo, state

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_local_app() -> FastAPI:
    app = FastAPI(title="SSHTransformer", docs_url=None, redoc_url=None)
    identity = local_identity()
    with state._lock:
        if not state.local.hostname:
            state.local = PeerInfo(
                hostname=identity["hostname"],
                os=identity["os"],
                ip="",
                role="",
            )

    app.include_router(local_router)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def create_peer_app() -> FastAPI:
    app = FastAPI(title="SSHTransformer Peer", docs_url=None, redoc_url=None)
    app.include_router(peer_router)
    return app
