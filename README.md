# SSHTransformer

局域网双机剪贴板 / 文件互传（macOS / Linux），提供 WebUI 与终端两种界面。

## 功能

- 单一程序：本机 Agent + 界面（WebUI 或终端）
- 主机 / 副机配对（IP + 六位配对码）
- 剪贴板暂存、读写系统剪贴板、推送到对端
- 按路径发送 / 拉取文件和文件夹（macOS / Linux 可用系统文件选择器；路径历史会自动记住）
- 文件和文件夹传输显示实时进度，传输记录会自动更新

## 要求

- macOS 或 Linux
- Python 3.10+
- Linux 系统剪贴板（任选其一）：
  - Wayland：`wl-clipboard`（`wl-copy` / `wl-paste`）
  - X11：`xclip` 或 `xsel`
- Linux 文件选择器（任选其一，可选）：`zenity`、`kdialog` 或 `yad`

没有安装 Linux 文件选择器时，仍可直接手动填写路径。

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

## 终端版

不想开浏览器时可以使用纯终端界面（功能与 WebUI 一致，macOS / Linux 均可运行）：

```bash
python run.py --tui
```

- `1` 我是主机：显示本机 IP 与配对码，等待副机连接；`c` 取消
- `2` 我是副机：输入主机 IP（可选历史记录，支持 `IP:端口`）与配对码
- 连接后：读入/写入系统剪贴板、编辑暂存、推送/拉取暂存、按路径发送/拉取文件与文件夹（`b` / `B` 调用系统文件选择器），传输显示实时进度与记录
- 终端模式下 WebUI 仍然可用（默认 `http://127.0.0.1:8765`，端口被占用时自动换空闲端口）

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
- 系统剪贴板会按平台自动选择：macOS 用 `pbcopy`/`pbpaste`；Linux 依次尝试 `wl-clipboard` 与 `xclip`/`xsel`，某个工具不可用（如会话类型不匹配）时自动回退，不会阻塞或误报。
- 本机「选择文件/文件夹」：macOS 通过 Finder；Linux 优先使用 `zenity`、其次 `kdialog` / `yad`。路径历史保存在 `~/.sshtransformer/path_history.json`。
- 推送：本机可选文件或文件夹；接收：本机只选保存文件夹。对端路径按系统预填默认根目录（macOS → `/Users`，Linux → `/home`）。
- 文件传输进度和记录默认每秒自动刷新，不需要手动刷新页面。文件夹传输需要两端都运行当前版本，并在更新代码后重启程序。
- 副机连接非默认端口的主机时可用 `IP:端口` 格式（主机用 `--peer-port` 自定义了监听端口）。
