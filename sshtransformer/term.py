"""Interactive terminal front-end for SSHTransformer (macOS + Linux).

The terminal client drives the same local agent HTTP API as the WebUI, so
pairing, clipboard staging and file transfers behave identically on both
front-ends. It needs no extra dependencies: plain input() for menus and
select() on stdin for polling while the host waits for a guest.
"""

from __future__ import annotations

import select
import sys
import threading
import time
from pathlib import PurePosixPath

import httpx

POLL_INTERVAL = 1.0


class Quit(Exception):
    """User (or EOF on stdin) asked to leave the program."""


class ApiError(RuntimeError):
    pass


def _fmt_bytes(num: float) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _basename(path: str) -> str:
    return PurePosixPath(str(path).replace("\\", "/")).name


def _preview(text: str, limit: int = 48) -> str:
    flat = (text or "").replace("\n", "⏎")
    if not flat:
        return "（空）"
    return flat if len(flat) <= limit else flat[:limit] + "…"


class TermUI:
    def __init__(self, base_url: str, peer_port: int) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=30.0, trust_env=False)
        self._peer_port = peer_port
        self._tty = sys.stdout.isatty()
        self._poll_prompted = False

    # ------------------------------------------------------------- plumbing

    def api(
        self,
        path: str,
        *,
        method: str = "GET",
        json: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        try:
            resp = self._client.request(method, f"/api{path}", json=json, timeout=timeout)
        except httpx.HTTPError as exc:
            raise ApiError(f"本机服务不可用: {exc}") from exc
        if resp.status_code >= 400:
            try:
                data = resp.json()
                detail = str(data.get("detail") or resp.text)
            except Exception:  # noqa: BLE001
                detail = resp.text or f"HTTP {resp.status_code}"
            raise ApiError(detail)
        return resp.json()

    def status(self) -> dict:
        return self.api("/status")

    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        if prompt.endswith(">"):
            line = f"{prompt} {suffix}".rstrip() + " "
        else:
            line = f"{prompt}{suffix}: "
        try:
            raw = input(line).strip()
        except EOFError:
            raise Quit() from None
        return raw or default

    def pause(self) -> None:
        try:
            input("  按回车继续… ")
        except EOFError:
            raise Quit() from None

    def poll_line(self, prompt: str, timeout: float) -> str | None:
        """Wait up to `timeout` for one stdin line. None on timeout, Quit on EOF."""
        if self._tty:
            sys.stdout.write("\r" + prompt)
            sys.stdout.flush()
        elif not self._poll_prompted:
            print(prompt, flush=True)
            self._poll_prompted = True
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        line = sys.stdin.readline()
        self._poll_prompted = False
        if not line:
            raise Quit()
        if self._tty:
            sys.stdout.write("\n")
        return line.strip()

    # ----------------------------------------------------------- main loop

    def run(self) -> None:
        while True:
            s = self.status()
            phase = s.get("phase")
            if phase == "ready":
                self.screen_ready()
            elif phase == "waiting":
                self.screen_waiting()
            elif phase == "connecting":
                time.sleep(0.5)
            elif phase == "error":
                print(f"\n  ✗ {s.get('last_error') or '发生错误'}")
                self.api("/reset", method="POST")
            else:  # role / boot
                self.screen_role()

    # -------------------------------------------------------------- screens

    def screen_role(self) -> None:
        s = self.status()
        local = s.get("local") or {}
        ips = ", ".join(s.get("ips") or []) or "（未检测到）"
        print()
        print("  " + "═" * 46)
        print(f"  SSHTransformer 终端版 · {local.get('hostname', '')} ({local.get('os', '')})")
        print(f"  局域网 IP: {ips}")
        print("  " + "─" * 46)
        print("   1) 我是主机（生成配对码，等待副机连接）")
        print("   2) 我是副机（连接到主机）")
        print("   q) 退出")
        choice = self._menu({"1", "2", "q"})
        if choice == "1":
            self.api("/role", method="POST", json={"role": "host"})
        elif choice == "2":
            self.api("/role", method="POST", json={"role": "guest"})
            self.screen_connect()
        else:
            raise Quit()

    def screen_waiting(self) -> None:
        while True:
            s = self.status()
            if s.get("phase") == "ready":
                peer = s.get("peer") or {}
                print(
                    f"\n  ✓ 副机已连接: {peer.get('hostname', '?')}"
                    f" ({peer.get('os', '?')}) @ {peer.get('ip', '')}"
                )
                return
            if s.get("phase") != "waiting":
                return
            ip = s.get("selected_ip") or "（未检测到局域网 IP）"
            code = s.get("pairing_code") or ""
            prompt = (
                f"  【主机等待中】副机填 IP {ip}:{self._peer_port} 配对码 {code}"
                f"   c=取消  "
            )
            key = self.poll_line(prompt, POLL_INTERVAL)
            if key is not None and key.lower() in {"c", "q"}:
                self.api("/reset", method="POST")
                return

    def screen_connect(self) -> None:
        while True:
            s = self.status()
            hosts = (s.get("host_history") or [])[:5]
            if hosts:
                print("  历史主机:")
                for i, ip in enumerate(hosts, 1):
                    print(f"    {i}) {ip}")
            raw = self.ask("  主机 IP（可带端口如 1.2.3.4:18765；序号；空=返回）")
            if not raw:
                self.api("/reset", method="POST")
                return
            host = hosts[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(hosts) else raw
            code = self.ask("  六位配对码")
            if not code:
                continue
            print("  连接中…")
            try:
                self.api("/connect", method="POST", json={"host": host, "code": code}, timeout=15.0)
            except ApiError as exc:
                print(f"  ✗ 连接失败: {exc}")
                self.pause()
            return

    def screen_ready(self) -> None:
        while True:
            s = self.status()
            if s.get("phase") != "ready":
                return
            peer = s.get("peer") or {}
            local = s.get("local") or {}
            stamp = ""
            if s.get("clipboard_updated_at"):
                stamp = time.strftime(" %H:%M", time.localtime(s["clipboard_updated_at"]))
            print()
            print("  " + "═" * 46)
            print(
                f"  已连接 · 本机 {local.get('hostname', '')} ({local.get('os', '')})"
                f" ↔ 对端 {peer.get('hostname', '?')} ({peer.get('os', '?')}) @ {peer.get('ip', '')}"
            )
            print(f"  暂存: {_preview(s.get('clipboard_text') or '')}{stamp}")
            print("  " + "─" * 46)
            print("   1) 读入系统剪贴板 → 暂存    2) 暂存 → 写入系统剪贴板")
            print("   3) 编辑暂存文字             4) 推送暂存 → 对端")
            print("   5) 从对端拉取暂存")
            print("   6) 发送文件/文件夹 → 对端   7) 从对端拉取 → 本机")
            print("   8) 传输进度 / 记录")
            print("   9) 断开连接                 q) 退出程序")
            choice = self._menu({"1", "2", "3", "4", "5", "6", "7", "8", "9", "q"})
            if choice == "1":
                self._clip_from_system()
            elif choice == "2":
                self._clip_to_system()
            elif choice == "3":
                self._clip_edit()
            elif choice == "4":
                self._clip_push()
            elif choice == "5":
                self._clip_pull()
            elif choice == "6":
                self.do_transfer("send")
            elif choice == "7":
                self.do_transfer("receive")
            elif choice == "8":
                self.screen_transfers()
            elif choice == "9":
                self.api("/disconnect", method="POST")
                print("  已断开连接")
            else:
                raise Quit()

    # ----------------------------------------------------------- clipboard

    def _clip_from_system(self) -> None:
        try:
            data = self.api("/clipboard/from-system", method="POST")
        except ApiError as exc:
            print(f"  ✗ 读入失败: {exc}")
            return
        print(f"  ✓ 已读入系统剪贴板: {_preview(data.get('text') or '')}")

    def _clip_to_system(self) -> None:
        try:
            self.api("/clipboard/to-system", method="POST")
        except ApiError as exc:
            print(f"  ✗ 写入失败: {exc}")
            return
        print("  ✓ 已写入系统剪贴板")

    def _clip_edit(self) -> None:
        print("  输入暂存文字，单独一行 . 结束（直接回车取消）:")
        lines: list[str] = []
        while True:
            try:
                line = input("  | ")
            except EOFError:
                break
            if line.strip() == ".":
                break
            lines.append(line)
        if not lines:
            print("  已取消")
            return
        text = "\n".join(lines)
        self.api("/clipboard", method="POST", json={"text": text})
        print(f"  ✓ 暂存已更新: {_preview(text)}")

    def _clip_push(self) -> None:
        try:
            self.api("/clipboard/push", method="POST")
        except ApiError as exc:
            print(f"  ✗ 推送失败: {exc}")
            return
        print("  ✓ 已推送到对端暂存")

    def _clip_pull(self) -> None:
        try:
            data = self.api("/clipboard/pull", method="POST")
        except ApiError as exc:
            print(f"  ✗ 拉取失败: {exc}")
            return
        print(f"  ✓ 已从对端拉取: {_preview(data.get('text') or '')}")

    # ------------------------------------------------------------ transfers

    def do_transfer(self, direction: str) -> None:
        s = self.status()
        if direction == "send":
            print("  发送: 本机 → 对端")
            src = self.ask_path("源路径（本机）", s.get("path_history", {}).get("local") or [], True)
            if not src:
                return
            name = _basename(src)
            root = (s.get("remote_default_root") or "/tmp").rstrip("/")
            default_dest = f"{root}/{name}" if name else root + "/"
            dest = self.ask("  目标路径（对端）", default_dest)
        else:
            print("  拉取: 对端 → 本机")
            src = self.ask_path("源路径（对端）", s.get("path_history", {}).get("remote") or [], False)
            if not src:
                return
            dest = self.ask("  保存到本机目录", s.get("local_default_dir") or "")
        if not dest:
            print("  ✗ 需要目标路径")
            return

        body = {"source_path": src, "dest_path": dest, "direction": direction}
        try:
            result = self.run_with_progress(body)
        except ApiError as exc:
            print(f"\n  ✗ 传输失败: {exc}")
            return
        if result.get("kind") == "dir":
            print(
                f"\n  ✓ 文件夹传输完成（{result.get('files', 0)} 个文件）→ {result.get('dest', dest)}"
            )
        else:
            print(f"\n  ✓ 传输完成 → {result.get('dest', dest)}")

    def ask_path(self, title: str, history: list[str], allow_pick: bool) -> str:
        hist = [h for h in history if h][:5]
        if hist:
            print(f"  {title} 历史:")
            for i, item in enumerate(hist, 1):
                print(f"    {i}) {item}")
        if allow_pick:
            print(f"  {title}（输入路径；b=选择文件；B=选择文件夹）")
        else:
            print(f"  {title}（输入路径）")
        raw = self.ask("  > ")
        if allow_pick and raw in {"b", "B"}:
            picked = self.pick("file" if raw == "b" else "folder")
            return picked
        if raw.isdigit() and hist and 1 <= int(raw) <= len(hist):
            return hist[int(raw) - 1]
        return raw

    def pick(self, kind: str) -> str:
        print("  打开系统文件选择器…（在弹窗中取消则返回）")
        try:
            data = self.api("/pick", method="POST", json={"kind": kind}, timeout=660.0)
        except ApiError as exc:
            print(f"  ✗ 选择器不可用: {exc}")
            return ""
        if data.get("cancelled"):
            print("  已取消选择")
            return ""
        return data.get("path") or ""

    def run_with_progress(self, body: dict) -> dict:
        before = {t["id"] for t in self.api("/transfers")["transfers"]}
        outcome: dict = {}
        errors: list[str] = []

        def work() -> None:
            try:
                outcome.update(
                    self.api("/transfer", method="POST", json=body, timeout=None)
                )
            except ApiError as exc:
                errors.append(str(exc))

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.4)
            self.render_progress(before)
        if self._tty:
            sys.stdout.write("\n")
            sys.stdout.flush()
        if errors:
            raise ApiError(errors[0])
        return outcome

    def render_progress(self, before: set[str]) -> None:
        try:
            tasks = self.api("/transfers")["transfers"]
        except ApiError:
            return
        current = next(
            (t for t in tasks if t["id"] not in before and t["status"] == "running"),
            None,
        )
        if current is None:
            return
        arrow = "→ 对端" if current["direction"] == "send" else "← 对端"
        done = _fmt_bytes(current["completed_bytes"])
        total = _fmt_bytes(current["total_bytes"]) if current["total_bytes"] else "?"
        filled = max(0, min(20, current["progress"] * 20 // 100))
        bar = "#" * filled + "-" * (20 - filled)
        label = current["current_file"] or _basename(current["source"]) or current["source"]
        line = f"  {arrow} [{bar}] {current['progress']:3d}%  {done}/{total}  {label}"[:96]
        if self._tty:
            sys.stdout.write("\r" + line)
            sys.stdout.flush()
        else:
            print(line, flush=True)

    def screen_transfers(self) -> None:
        while True:
            tasks = self._print_transfers()
            if any(t["status"] == "running" for t in tasks):
                while True:
                    time.sleep(0.5)
                    tasks = self.api("/transfers")["transfers"]
                    if not any(t["status"] == "running" for t in tasks):
                        break
                    self.render_progress(set())
                if self._tty:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                tasks = self._print_transfers()
            key = self.ask("  [回车=刷新  q=返回]").lower()
            if key == "q":
                return

    def _print_transfers(self) -> list[dict]:
        data = self.api("/transfers")
        tasks = data["transfers"][:12]
        log = data["transfer_log"][-10:]
        print()
        print("  ── 传输任务（最新在前）──")
        if not tasks:
            print("  （暂无）")
        for t in tasks:
            mark = {"running": "●", "completed": "✓", "failed": "✗"}.get(t["status"], "·")
            size = ""
            if t["total_bytes"]:
                size = f" {_fmt_bytes(t['completed_bytes'])}/{_fmt_bytes(t['total_bytes'])}"
            tail = f"  {t['error']}" if t["status"] == "failed" and t["error"] else ""
            print(
                f"  {mark} {t['status']:9s} {t['direction']:7s} {t['kind']:4s}"
                f" {t['source']} → {t['dest']}  {t['progress']}%{size}{tail}"[:120]
            )
        print("  ── 记录 ──")
        if not log:
            print("  （暂无）")
        for entry in log:
            when = time.strftime("%H:%M:%S", time.localtime(entry.get("at") or 0))
            action = "发送" if entry.get("action") == "send" else "接收"
            files = entry.get("files")
            extra = f"（{files} 个文件）" if isinstance(files, int) and entry.get("kind") == "dir" else ""
            print(f"  {when} {action} {entry.get('source', '')} → {entry.get('dest', '')}{extra}"[:120])
        return tasks

    # --------------------------------------------------------------- misc

    def _menu(self, valid: set[str]) -> str:
        while True:
            raw = self.ask("  请选择").lower()
            if raw in valid:
                return raw
            print(f"  请输入 {'/'.join(sorted(valid))}")
