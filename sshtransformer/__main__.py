"""CLI entry: start local WebUI agent + LAN peer listener."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
from typing import Sequence

# Ensure banner lines show immediately in terminals.
os.environ.setdefault("PYTHONUNBUFFERED", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

import uvicorn

from . import __version__
from .app import create_local_app, create_peer_app
from .network import list_lan_ips, local_identity

UI_PORT = 8765
PEER_PORT = 18765


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SSHTransformer — LAN clipboard & file bridge")
    parser.add_argument("--ui-port", type=int, default=UI_PORT)
    parser.add_argument("--peer-port", type=int, default=PEER_PORT)
    parser.add_argument("--no-open", action="store_true", help="Do not print open hint only")
    args = parser.parse_args(list(argv) if argv is not None else None)

    identity = local_identity()
    ips = list_lan_ips()

    if _port_open("127.0.0.1", args.ui_port):
        raise SystemExit(f"UI port {args.ui_port} already in use")

    peer_app = create_peer_app()
    local_app = create_local_app()

    peer_config = uvicorn.Config(
        peer_app,
        host="0.0.0.0",
        port=args.peer_port,
        log_level="warning",
        access_log=False,
    )
    local_config = uvicorn.Config(
        local_app,
        host="127.0.0.1",
        port=args.ui_port,
        log_level="warning",
        access_log=False,
    )

    peer_server = uvicorn.Server(peer_config)
    local_server = uvicorn.Server(local_config)

    thread = threading.Thread(target=peer_server.run, name="peer-server", daemon=True)
    thread.start()

    print()
    print(f"  SSHTransformer v{__version__}")
    print(f"  Machine   {identity['hostname']}  ·  {identity['os']}")
    if ips:
        print(f"  LAN IPs   {', '.join(ips)}")
    else:
        print("  LAN IPs   (none detected)")
    print()
    print("  Local agent running.")
    print(f"  Open WebUI →  http://127.0.0.1:{args.ui_port}")
    print(f"  Peer port  →  {args.peer_port}  (LAN)")
    print()
    print("  Press Ctrl+C to stop.")
    print(flush=True)
    try:
        local_server.run()
    except KeyboardInterrupt:
        pass
    finally:
        peer_server.should_exit = True


if __name__ == "__main__":
    main()
