# SSHTransformer

局域网双机剪贴板 / 文件互传（macOS / Linux）。

## 功能

- 单一程序：本机 Agent + WebUI
- 主机 / 副机配对（IP + 六位配对码）
- 剪贴板暂存、读写系统剪贴板、推送到对端
- 按路径发送 / 拉取文件

## 要求

- macOS 或 Linux
- Python 3.10+
- Linux 系统剪贴板（任选其一）：
  - Wayland：`wl-clipboard`（`wl-copy` / `wl-paste`）
  - X11：`xclip` 或 `xsel`

## 安装与启动

```bash
cd SSHTransformer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

终端会打印：

```text
Open WebUI →  http://127.0.0.1:8765
```

浏览器打开该地址即可。

## 用法

1. **主机**：选「我是主机」→ 确认局域网 IP → 把说明 / 配对码给副机 → 等待
2. **副机**：选「我是副机」→ 填主机 IP 与配对码 → 连接
3. 两边进入互传界面：剪贴板暂存、文件路径传输

## 端口

| 端口 | 绑定 | 用途 |
|------|------|------|
| `8765` | `127.0.0.1` | 本机 WebUI |
| `18765` | `0.0.0.0` | 局域网 Agent 配对与传输 |

若主机防火墙拦截入站，请允许 `18765/tcp`。

## 说明

- 默认假设两机在同一局域网；跨网请自行用 Tailscale 等组网，主机地址填虚拟网 IP。
- 系统剪贴板会按平台自动选择：macOS 用 `pbcopy`/`pbpaste`；Linux 优先 `wl-clipboard`，否则 `xclip`/`xsel`。
