(() => {
  const app = document.getElementById("app");
  const strip = document.getElementById("endpoint-strip");
  const toastEl = document.getElementById("toast");

  let status = null;
  let selectedIp = "";
  let transferDirection = "send";
  let savedSrcPath = "";
  let savedDestPath = "";
  let pollTimer = null;
  let toastTimer = null;

  function basename(path) {
    const parts = String(path || "")
      .replaceAll("\\", "/")
      .split("/")
      .filter(Boolean);
    return parts[parts.length - 1] || "file";
  }

  function joinPath(root, name) {
    const r = String(root || "").replace(/\/+$/, "");
    const n = String(name || "").replace(/^\/+/, "");
    return `${r}/${n}`;
  }

  function historyOptions(paths, selected) {
    const list = paths || [];
    if (!list.length) {
      return `<option value="">暂无历史记录</option>`;
    }
    return [
      `<option value="">从历史记录选择…</option>`,
      ...list.map(
        (p) =>
          `<option value="${escapeHtml(p)}" ${p === selected ? "selected" : ""}>${escapeHtml(p)}</option>`
      ),
    ].join("");
  }

  function datalistOptions(paths) {
    return (paths || []).map((p) => `<option value="${escapeHtml(p)}"></option>`).join("");
  }

  async function api(path, options = {}) {
    const res = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let data = null;
    const text = await res.text();
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data?.detail;
      const msg = typeof detail === "string" ? detail : JSON.stringify(detail || data);
      throw new Error(msg || `Request failed (${res.status})`);
    }
    return data;
  }

  function toast(message, isError = false) {
    toastEl.textContent = message;
    toastEl.classList.toggle("error", isError);
    toastEl.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 2800);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderEndpoints(s) {
    if (!s?.connected) {
      strip.classList.add("hidden");
      strip.innerHTML = "";
      return;
    }
    strip.classList.remove("hidden");
    const local = s.local || {};
    const peer = s.peer || {};
    strip.innerHTML = `
      <div class="endpoint-chip">
        <span class="dot"></span>
        <strong>本机</strong>
        <span>${escapeHtml(local.hostname || "—")}</span>
        <span class="meta">${escapeHtml(local.ip || "")}</span>
      </div>
      <div class="endpoint-chip peer">
        <span class="dot"></span>
        <strong>对端</strong>
        <span>${escapeHtml(peer.hostname || "—")}</span>
        <span class="meta">${escapeHtml(peer.ip || "")}</span>
      </div>
    `;
  }

  function renderRole() {
    app.innerHTML = `
      <section class="view hero">
        <div>
          <div class="hero-brand">SSHTransformer</div>
          <p class="hero-lead">同一局域网内，把剪贴板和文件在两台机器之间稳稳递过去。先选你是主机还是副机。</p>
        </div>
        <div class="role-grid">
          <button class="role-card" data-role="host" type="button">
            <div class="role-kicker">Host</div>
            <h2 class="role-title">我是主机</h2>
            <p class="role-desc">自动检测局域网 IP，生成配对码，等待副机连入。</p>
          </button>
          <button class="role-card" data-role="guest" type="button">
            <div class="role-kicker">Guest</div>
            <h2 class="role-title">我是副机</h2>
            <p class="role-desc">输入主机 IP 与配对码，连上后进入互传界面。</p>
          </button>
        </div>
      </section>
    `;
    app.querySelectorAll("[data-role]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const role = btn.getAttribute("data-role");
        try {
          status = await api("/role", {
            method: "POST",
            body: JSON.stringify({ role, ip: selectedIp || null }),
          });
          selectedIp = status.selected_ip || (status.ips && status.ips[0]) || "";
          render();
        } catch (err) {
          toast(err.message, true);
        }
      });
    });
  }

  function renderWaiting(s) {
    const ips = s.ips || [];
    if (!selectedIp && ips.length) selectedIp = s.selected_ip || ips[0];
    const code = s.pairing_code || "------";
    const ip = selectedIp || s.selected_ip || "（未检测到 IP）";

    app.innerHTML = `
      <section class="view waiting-layout">
        <div class="panel">
          <h2>等待副机连接</h2>
          <p class="sub">把下面这串说明发给副机。双方需在同一局域网。</p>
          <div class="code-block">${escapeHtml(code)}</div>
          <div class="waiting-pulse">
            <div class="bars" aria-hidden="true"><span></span><span></span><span></span></div>
            <span>正在监听配对端口 18765…</span>
          </div>
          <div class="btn-row">
            <button class="btn btn-ghost" id="btn-copy-guide" type="button">复制说明给副机</button>
            <button class="btn btn-ghost" id="btn-back" type="button">重选角色</button>
          </div>
        </div>
        <div class="panel">
          <h2>主机地址</h2>
          <p class="sub">若有多个网卡，点选副机实际能访问的那个 IP。</p>
          <div class="ip-list">
            ${
              ips.length
                ? ips
                    .map(
                      (item) => `
              <button type="button" class="ip-pill ${item === selectedIp ? "active" : ""}" data-ip="${escapeHtml(item)}">${escapeHtml(item)}</button>
            `
                    )
                    .join("")
                : "<span>未检测到局域网 IP</span>"
            }
          </div>
          <div class="instruction">
            <strong>请副机这样做</strong>
            <ol>
              <li>启动同一程序，打开本机 WebUI</li>
              <li>选择「我是副机」</li>
              <li>主机地址填 <code>${escapeHtml(ip)}</code></li>
              <li>配对码填 <code>${escapeHtml(code)}</code></li>
            </ol>
          </div>
        </div>
      </section>
    `;

    app.querySelectorAll("[data-ip]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        selectedIp = btn.getAttribute("data-ip");
        try {
          status = await api("/role", {
            method: "POST",
            body: JSON.stringify({ role: "host", ip: selectedIp }),
          });
          render();
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    document.getElementById("btn-copy-guide")?.addEventListener("click", async () => {
      const guide = [
        "请连接 SSHTransformer 主机：",
        `1. 启动程序并打开本机 WebUI`,
        `2. 选择「我是副机」`,
        `3. 主机地址：${ip}`,
        `4. 配对码：${code}`,
      ].join("\n");
      try {
        await navigator.clipboard.writeText(guide);
        toast("说明已复制");
      } catch {
        toast("复制失败，请手动选择文字", true);
      }
    });

    document.getElementById("btn-back")?.addEventListener("click", async () => {
      try {
        status = await api("/reset", { method: "POST" });
        render();
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  function renderGuestForm(s) {
    const hosts = s.host_history || [];
    const lastHost = hosts[0] || "";
    app.innerHTML = `
      <section class="view" style="max-width:520px;margin:2rem auto 0">
        <div class="panel">
          <h2>连接主机</h2>
          <p class="sub">输入主机在等待页展示的局域网 IP 与六位配对码。成功连接过的主机会记在本地，可直接下拉选择。</p>
          <form class="form-grid" id="connect-form">
            <label>
              主机 IP
              <input id="host-input" name="host" list="host-datalist" placeholder="例如 192.168.1.8" required autocomplete="off" value="${escapeHtml(lastHost)}" />
            </label>
            <datalist id="host-datalist">${datalistOptions(hosts)}</datalist>
            <select class="history-select" id="host-history">
              ${
                hosts.length
                  ? [`<option value="">从历史主机选择…</option>`, ...hosts.map((ip) => `<option value="${escapeHtml(ip)}">${escapeHtml(ip)}</option>`)].join("")
                  : `<option value="">暂无历史主机</option>`
              }
            </select>
            <label>
              配对码
              <input name="code" placeholder="六位数字" required maxlength="8" autocomplete="off" />
            </label>
            <div class="btn-row">
              <button class="btn btn-accent" type="submit">连接</button>
              <button class="btn btn-ghost" id="btn-back" type="button">返回</button>
            </div>
            ${s.last_error ? `<div class="error-banner">${escapeHtml(s.last_error)}</div>` : ""}
          </form>
        </div>
      </section>
    `;

    const hostInput = document.getElementById("host-input");
    document.getElementById("host-history")?.addEventListener("change", (e) => {
      if (e.target.value) {
        hostInput.value = e.target.value;
      }
      e.target.selectedIndex = 0;
    });

    document.getElementById("connect-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const host = String(fd.get("host") || "").trim();
      const code = String(fd.get("code") || "").trim();
      const submitBtn = e.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      try {
        status = await api("/connect", {
          method: "POST",
          body: JSON.stringify({ host, code }),
        });
        toast("已连接");
        render();
      } catch (err) {
        toast(err.message, true);
        submitBtn.disabled = false;
      }
    });

    document.getElementById("btn-back")?.addEventListener("click", async () => {
      try {
        status = await api("/reset", { method: "POST" });
        render();
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  function renderReady(s) {
    const logs = (s.transfer_log || []).slice().reverse();
    const hist = s.path_history || { local: [], remote: [], all: [] };
    const remoteRoot = s.remote_default_root || "/tmp";
    const localDir = s.local_default_dir || s.local_home || "";
    const sending = transferDirection === "send";

    const srcIsLocal = sending;
    const destIsLocal = !sending;
    const srcHist = srcIsLocal ? hist.local : hist.remote;
    const destHist = destIsLocal ? hist.local : hist.remote;

    const srcPlaceholder = sending
      ? `${localDir || "/Users/you"}/Desktop/notes.txt`
      : `${remoteRoot}/notes.txt`;
    const destPlaceholder = sending
      ? `${remoteRoot}/notes.txt`
      : `${localDir || "/Users/you"}/notes.txt`;

    const srcActions = srcIsLocal
      ? `<div class="path-actions">
           <button class="btn btn-accent btn-compact" id="src-browse-file" type="button">选择文件…</button>
           <button class="btn btn-ghost btn-compact" id="src-browse-folder" type="button">选择文件夹…</button>
         </div>`
      : `<p class="hint">对端路径请手动填写，或从下方历史记录选择</p>`;

    const destActions = destIsLocal
      ? `<div class="path-actions">
           <button class="btn btn-accent btn-compact" id="dest-browse-folder" type="button">选择文件夹…</button>
         </div>`
      : "";

    app.innerHTML = `
      <section class="view ready-grid">
        <div class="panel">
          <h2>剪贴板暂存</h2>
          <p class="sub">本机复制后点「读入系统剪贴板」，或直接粘贴到下方。再推送到对端。</p>
          <textarea id="clip-text" placeholder="在此暂存文字…">${escapeHtml(s.clipboard_text || "")}</textarea>
          <div class="btn-row">
            <button class="btn btn-ghost" id="clip-from" type="button">读入系统剪贴板</button>
            <button class="btn btn-ghost" id="clip-to" type="button">写入系统剪贴板</button>
            <button class="btn btn-accent" id="clip-push" type="button">推送到对端</button>
            <button class="btn btn-primary" id="clip-pull" type="button">从对端拉取</button>
          </div>
        </div>
        <div class="panel">
          <h2>文件互传</h2>
          <p class="sub">推送时可选择本机文件或文件夹；接收时选择本机保存文件夹。对端路径会按对方系统预填默认根目录。</p>
          <div class="transfer-fields">
            <div class="dir-toggle" role="group" aria-label="传输方向">
              <button type="button" data-dir="send" class="${sending ? "active" : ""}">发送到对端</button>
              <button type="button" data-dir="receive" class="${!sending ? "active" : ""}">从对端拉取</button>
            </div>
            <div class="path-field">
              <span class="label">${sending ? "本机源路径" : "对端源路径"}</span>
              <div class="path-row">
                <input id="src-path" list="src-datalist" placeholder="${escapeHtml(srcPlaceholder)}" autocomplete="off" />
                ${srcActions}
              </div>
              <datalist id="src-datalist">${datalistOptions(srcHist)}</datalist>
              <select class="history-select" id="src-history">${historyOptions(srcHist, "")}</select>
            </div>
            <div class="path-field">
              <span class="label">${sending ? "对端目标路径" : "本机目标路径"}</span>
              <div class="path-row">
                <input id="dest-path" list="dest-datalist" placeholder="${escapeHtml(destPlaceholder)}" autocomplete="off" />
                ${destActions}
              </div>
              <datalist id="dest-datalist">${datalistOptions(destHist)}</datalist>
              <select class="history-select" id="dest-history">${historyOptions(destHist, "")}</select>
              <p class="hint">${
                sending
                  ? `对端路径默认从 <code>${escapeHtml(remoteRoot)}</code> 起写，选完本机文件后会自动带上文件名`
                  : `本机默认保存到 <code>${escapeHtml(localDir)}</code>，选文件夹后会自动带上源文件名`
              }</p>
            </div>
          </div>
          <div class="btn-row">
            <button class="btn btn-accent" id="btn-transfer" type="button">开始传输</button>
            <button class="btn btn-ghost" id="btn-disconnect" type="button">断开</button>
          </div>
          <ul class="log-list">
            ${
              logs.length
                ? logs
                    .map((item) => {
                      const label = item.action === "send" ? "发送" : "接收";
                      return `<li>${escapeHtml(label)} · ${escapeHtml(item.source || item.name || "")} → ${escapeHtml(item.dest || "")}</li>`;
                    })
                    .join("")
                : "<li>暂无传输记录</li>"
            }
          </ul>
        </div>
      </section>
    `;

    const srcEl = document.getElementById("src-path");
    const destEl = document.getElementById("dest-path");
    if (savedSrcPath) srcEl.value = savedSrcPath;
    if (savedDestPath) {
      destEl.value = savedDestPath;
    } else if (sending) {
      destEl.value = remoteRoot.replace(/\/+$/, "") + "/";
    } else {
      destEl.value = localDir.replace(/\/+$/, "") + "/";
    }

    const persistPaths = () => {
      savedSrcPath = srcEl.value;
      savedDestPath = destEl.value;
    };
    srcEl.addEventListener("input", persistPaths);
    destEl.addEventListener("input", persistPaths);

    document.getElementById("src-history")?.addEventListener("change", (e) => {
      if (e.target.value) {
        srcEl.value = e.target.value;
        persistPaths();
        if (sending) {
          destEl.value = joinPath(remoteRoot, basename(srcEl.value));
          persistPaths();
        }
      }
      e.target.selectedIndex = 0;
    });
    document.getElementById("dest-history")?.addEventListener("change", (e) => {
      if (e.target.value) {
        destEl.value = e.target.value;
        persistPaths();
      }
      e.target.selectedIndex = 0;
    });

    async function browse(kind, targetInput, after) {
      try {
        const data = await api("/pick", {
          method: "POST",
          body: JSON.stringify({ kind }),
        });
        if (data.cancelled) {
          toast("已取消选择");
          return;
        }
        let path = data.path || "";
        // Finder folder paths usually end with "/"
        targetInput.value = path;
        persistPaths();
        after?.(path);
        toast("已填入路径");
      } catch (err) {
        toast(err.message, true);
      }
    }

    function applyRemoteDestFromLocal(path) {
      if (!sending) return;
      const name = basename(path);
      if (!name) {
        destEl.value = remoteRoot.replace(/\/+$/, "") + "/";
      } else {
        destEl.value = joinPath(remoteRoot, name);
      }
      persistPaths();
    }

    document.getElementById("src-browse-file")?.addEventListener("click", () => {
      browse("file", srcEl, applyRemoteDestFromLocal);
    });
    document.getElementById("src-browse-folder")?.addEventListener("click", () => {
      browse("folder", srcEl, applyRemoteDestFromLocal);
    });
    document.getElementById("dest-browse-folder")?.addEventListener("click", () => {
      browse("folder", destEl, (folder) => {
        const name = basename(srcEl.value);
        destEl.value = name ? joinPath(folder, name) : folder.replace(/\/?$/, "/");
        persistPaths();
      });
    });

    const clip = document.getElementById("clip-text");
    let saveTimer = null;
    clip.addEventListener("input", () => {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(async () => {
        try {
          await api("/clipboard", {
            method: "POST",
            body: JSON.stringify({ text: clip.value }),
          });
        } catch (err) {
          toast(err.message, true);
        }
      }, 280);
    });

    document.getElementById("clip-from").addEventListener("click", async () => {
      try {
        const data = await api("/clipboard/from-system", { method: "POST", body: "{}" });
        clip.value = data.text || "";
        toast("已读入系统剪贴板");
      } catch (err) {
        toast(err.message, true);
      }
    });

    document.getElementById("clip-to").addEventListener("click", async () => {
      try {
        await api("/clipboard", {
          method: "POST",
          body: JSON.stringify({ text: clip.value }),
        });
        await api("/clipboard/to-system", { method: "POST", body: "{}" });
        toast("已写入系统剪贴板");
      } catch (err) {
        toast(err.message, true);
      }
    });

    document.getElementById("clip-push").addEventListener("click", async () => {
      try {
        await api("/clipboard", {
          method: "POST",
          body: JSON.stringify({ text: clip.value }),
        });
        await api("/clipboard/push", { method: "POST", body: "{}" });
        toast("已推送到对端暂存");
      } catch (err) {
        toast(err.message, true);
      }
    });

    document.getElementById("clip-pull").addEventListener("click", async () => {
      try {
        const data = await api("/clipboard/pull", { method: "POST", body: "{}" });
        clip.value = data.text || "";
        toast("已从对端拉取");
      } catch (err) {
        toast(err.message, true);
      }
    });

    app.querySelectorAll("[data-dir]").forEach((btn) => {
      btn.addEventListener("click", () => {
        persistPaths();
        transferDirection = btn.getAttribute("data-dir");
        // Reset dest suggestion when switching direction.
        savedDestPath = "";
        renderReady(status);
      });
    });

    document.getElementById("btn-transfer").addEventListener("click", async () => {
      persistPaths();
      const source_path = srcEl.value.trim();
      const dest_path = destEl.value.trim();
      if (!source_path || !dest_path) {
        toast("请填写源路径和目标路径", true);
        return;
      }
      const btn = document.getElementById("btn-transfer");
      btn.disabled = true;
      try {
        await api("/transfer", {
          method: "POST",
          body: JSON.stringify({
            source_path,
            dest_path,
            direction: transferDirection,
          }),
        });
        toast("传输完成");
        status = await api("/status");
        renderReady(status);
        renderEndpoints(status);
      } catch (err) {
        toast(err.message, true);
        btn.disabled = false;
      }
    });

    document.getElementById("btn-disconnect").addEventListener("click", async () => {
      try {
        status = await api("/disconnect", { method: "POST" });
        toast("已断开");
        render();
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  function render() {
    if (!status) return;
    renderEndpoints(status);

    if (status.connected || status.phase === "ready") {
      renderReady(status);
      return;
    }
    if (status.role === "host" && (status.phase === "waiting" || status.phase === "connecting")) {
      renderWaiting(status);
      return;
    }
    if (status.role === "guest") {
      renderGuestForm(status);
      return;
    }
    renderRole();
  }

  async function refresh() {
    try {
      const next = await api("/status");
      const prevPhase = status?.phase;
      const prevPeer = status?.peer?.hostname;
      status = next;
      if (
        !document.activeElement ||
        !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)
      ) {
        if (prevPhase !== next.phase || prevPeer !== next.peer?.hostname || !app.innerHTML) {
          render();
        } else if (next.connected) {
          renderEndpoints(next);
        } else if (next.role === "host" && next.phase === "waiting") {
          // keep waiting view; only update if code/ip changed via full render when needed
          renderEndpoints(next);
        }
      } else if (next.connected && prevPhase !== "ready") {
        render();
      }
    } catch {
      /* ignore transient poll errors */
    }
  }

  async function boot() {
    status = await api("/status");
    selectedIp = status.selected_ip || (status.ips && status.ips[0]) || "";
    render();
    pollTimer = setInterval(refresh, 1500);
  }

  boot().catch((err) => {
    app.innerHTML = `<div class="panel"><h2>无法连接本机 Agent</h2><p class="sub">${escapeHtml(err.message)}</p></div>`;
  });
})();
