"""
Client-Side Multi-Studio SaaS Application (`VidelBot/webapp/ui_client.py`).

Zero fake metrics, full CSRF + Bearer session handling, unsaved-changes guard,
keyboard shortcuts (`⌘K` / `Ctrl+K`), responsive Light/Dark theme persistence,
and complete integration with `/api/v1/*`.
"""

CLIENT_JS = r"""
(() => {
  "use strict";

  const STUDIOS = [
    { group: "Core Workspace", items: [
      { id: "overview", label: "Workspace Overview", icon: "⌂" },
      { id: "bots", label: "Bot Management", icon: "🤖" },
      { id: "bot_create", label: "Bot Creation Studio", icon: "✨" },
      { id: "fleet", label: "Bot Fleet & Health", icon: "🛰" },
    ]},
    { group: "Configuration & Clone", items: [
      { id: "bot_config", label: "Configuration & Envs", icon: "⚙" },
      { id: "commands", label: "Command Studio", icon: "⌘" },
      { id: "clone_studio", label: "Clone & Migration", icon: "🧬" },
      { id: "templates", label: "Template Library", icon: "📐" },
    ]},
    { group: "Files, Media & Data", items: [
      { id: "filestore", label: "File Store Studio", icon: "📦" },
      { id: "file_intel", label: "File Intelligence", icon: "🔎" },
      { id: "downloads", label: "Downloads & Media", icon: "⬇" },
      { id: "analytics", label: "Analytics Studio", icon: "📈" },
    ]},
    { group: "Automation & Dev", items: [
      { id: "automation", label: "Automation & Schedules", icon: "⚡" },
      { id: "integrations", label: "Integrations & Plugins", icon: "🔌" },
      { id: "developer", label: "Developer & Backups", icon: "{}" },
      { id: "team", label: "Team, Settings & Help", icon: "👥" },
    ]},
    { group: "Platform Admin", adminOnly: true, items: [
      { id: "admin", label: "Administration Center", icon: "🛡" },
    ]},
  ];

  const S = {
    token: sessionStorage.getItem("videl_saas_tok") || "",
    csrf: sessionStorage.getItem("videl_saas_csrf") || "",
    user: null,
    workspaces: [],
    bots: [],
    catalogs: {},
    settings: {},
    preferences: {},
    wsId: localStorage.getItem("videl_ws_id") || "",
    botId: localStorage.getItem("videl_bot_id") || "",
    studio: "overview",
    unsaved: false,
    botViewMode: "grid",
    fsViewMode: "table",
    wizard: { step: 1, display_name: "", username: "", description: "", template: "filestore", bot_token: "", tokenDiag: null, features: {}, tags: "" },
    selectionSession: null,
  };
  let cmdkItems = [];
  let cmdkActiveIndex = -1;
  let cmdkRequestId = 0;
  let cmdkReturnFocus = null;

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function fmtBytes(n) {
    const b = Number(n || 0);
    if (b <= 0) return "0 B";
    const u = ["B", "KiB", "MiB", "GiB", "TiB"];
    const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
    return (b / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + " " + u[i];
  }

  function toast(msg, kind = "info") {
    const stack = $("toast-stack");
    if (!stack) return;
    const el = document.createElement("div");
    el.className = "toast " + (kind === "error" ? "err" : kind === "success" ? "ok" : "");
    el.textContent = msg;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function confirmModal(title, htmlBody, confirmLabel = "Confirm") {
    return new Promise((resolve) => {
      const m = $("app-modal");
      $("modal-title").textContent = title;
      $("modal-body").innerHTML = htmlBody;
      const btnOk = $("modal-confirm");
      const btnCancel = $("modal-cancel");
      btnOk.textContent = confirmLabel;
      m.hidden = false;
      const cleanup = (val) => {
        m.hidden = true;
        btnOk.onclick = null;
        btnCancel.onclick = null;
        resolve(val);
      };
      btnOk.onclick = () => cleanup(true);
      btnCancel.onclick = () => cleanup(false);
    });
  }

  async function api(path, opts = {}) {
    const method = opts.method || "GET";
    const headers = Object.assign({}, opts.headers || {});
    if (S.token) headers["Authorization"] = "Bearer " + S.token;
    if (S.csrf && method !== "GET") headers["X-CSRF-Token"] = S.csrf;
    let body = opts.body;
    if (body && !(body instanceof FormData) && typeof body === "object") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const resp = await fetch(path, { method, headers, body });
    const ct = resp.headers.get("Content-Type") || "";
    const payload = ct.includes("application/json") ? await resp.json() : await resp.text();
    if (!resp.ok) {
      const err = new Error((payload && payload.error) ? payload.error : ("HTTP " + resp.status));
      err.status = resp.status;
      if (resp.status === 401 && path !== "/api/v1/auth/login") {
        clearSession();
      }
      throw err;
    }
    return payload;
  }

  function saveSession(tok, csrf) {
    S.token = tok;
    S.csrf = csrf;
    sessionStorage.setItem("videl_saas_tok", tok);
    sessionStorage.setItem("videl_saas_csrf", csrf);
  }

  function clearSession() {
    S.token = "";
    S.csrf = "";
    S.user = null;
    sessionStorage.removeItem("videl_saas_tok");
    sessionStorage.removeItem("videl_saas_csrf");
    $("app-shell").hidden = true;
    $("mobile-bottom-nav").hidden = true;
    $("auth-screen").hidden = false;
  }

  function applyTheme(mode) {
    const resolved = mode === "system"
      ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")
      : (mode === "light" ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", resolved);
    localStorage.setItem("videl_theme", mode);
  }

  /* ── Boot & Authentication ── */
  async function bootstrapSession() {
    const data = await api("/api/v1/auth/me");
    S.user = data.user;
    if (data.user.csrf_token) S.csrf = data.user.csrf_token;
    S.workspaces = data.workspaces || [];
    S.settings = data.settings || {};
    S.preferences = data.preferences || {};
    S.catalogs = data.catalogs || {};

    if (!S.workspaces.find((w) => w.workspace_id === S.wsId)) {
      S.wsId = S.workspaces[0] ? S.workspaces[0].workspace_id : "";
    }
    localStorage.setItem("videl_ws_id", S.wsId);

    $("auth-screen").hidden = true;
    $("app-shell").hidden = false;
    $("mobile-bottom-nav").hidden = false;
    const skipLink = document.querySelector(".skip-link");
    if (skipLink) { skipLink.href = "#main-canvas"; skipLink.textContent = "Skip to workspace"; }
    $("demo-banner").hidden = !S.user.is_demo;

    const themePref = (S.settings.appearance && S.settings.appearance.theme) || localStorage.getItem("videl_theme") || "dark";
    applyTheme(themePref);

    renderSidebar();
    await refreshBotsAndSelectors();
    await navigate(S.studio, true);
  }

  function renderSidebar() {
    const nav = $("sidebar-nav");
    let html = "";
    for (const grp of STUDIOS) {
      if (grp.adminOnly && (!S.user || !S.user.is_platform_admin)) continue;
      html += `<div class="nav-group-label">${esc(grp.group)}</div>`;
      for (const item of grp.items) {
        const active = S.studio === item.id ? "active" : "";
        html += `<button type="button" class="nav-item ${active}" data-studio="${esc(item.id)}">
          <span class="nav-icon">${esc(item.icon)}</span>
          <span class="nav-label">${esc(item.label)}</span>
        </button>`;
      }
    }
    nav.innerHTML = html;
    nav.querySelectorAll("[data-studio]").forEach((btn) => {
      btn.onclick = () => {
        $("sidebar").classList.remove("mobile-open");
        navigate(btn.dataset.studio);
      };
    });
    document.querySelectorAll("#mobile-bottom-nav button").forEach((b) => {
      b.classList.toggle("active", b.dataset.nav === S.studio);
      b.onclick = () => navigate(b.dataset.nav);
    });
  }

  async function refreshBotsAndSelectors() {
    const wsSel = $("ws-selector");
    wsSel.innerHTML = S.workspaces.map((w) =>
      `<option value="${esc(w.workspace_id)}" ${w.workspace_id === S.wsId ? "selected" : ""}>${esc(w.icon || "🚀")} ${esc(w.name)}</option>`
    ).join("");

    if (S.wsId) {
      const res = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots?include_archived=1`);
      S.bots = res.bots || [];
      if (!S.bots.find((b) => b.bot_id === S.botId)) {
        S.botId = S.bots[0] ? S.bots[0].bot_id : "";
      }
      localStorage.setItem("videl_bot_id", S.botId);

      // Update notification badge
      try {
        const wsInfo = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}`);
        const unread = (wsInfo.usage && wsInfo.usage.unread_notifications) || 0;
        const badge = $("ntf-badge");
        badge.textContent = String(unread);
        badge.hidden = unread <= 0;
      } catch (_) {}
    }

    const botSel = $("bot-selector");
    botSel.innerHTML = S.bots.length
      ? S.bots.map((b) => `<option value="${esc(b.bot_id)}" ${b.bot_id === S.botId ? "selected" : ""}>${esc(b.display_name)} (@${esc(b.username)})</option>`).join("")
      : `<option value="">No bots yet</option>`;
  }

  async function navigate(studioId, skipGuard = false) {
    if (!skipGuard && S.unsaved) {
      const ok = await confirmModal("Discard unsaved changes?", "You have unsaved configuration edits. Leave this studio anyway?", "Leave Studio");
      if (!ok) return;
      S.unsaved = false;
    }
    S.studio = studioId;
    renderSidebar();
    const wsObj = S.workspaces.find((w) => w.workspace_id === S.wsId);
    const studioMeta = STUDIOS.flatMap((g) => g.items).find((i) => i.id === studioId) || { label: studioId };
    $("breadcrumbs").innerHTML = `<span>${esc(wsObj ? wsObj.name : "Workspace")}</span> <span>/</span> <strong style="color:var(--text-primary)">${esc(studioMeta.label)}</strong>`;

    const vp = $("studio-viewport");
    vp.innerHTML = `<div class="card"><span style="color:var(--text-secondary)">Loading ${esc(studioMeta.label)}…</span></div>`;
    try {
      await RENDERERS[studioId](vp);
    } catch (err) {
      vp.innerHTML = `<div class="callout callout-warn">Failed to load studio: ${esc(err.message)}</div>`;
    }
  }

  function connBadge(state) {
    if (state === "running") return `<span class="badge badge-success"><span class="dot dot-ok"></span>Running Live</span>`;
    if (state === "credentials_verified_idle") return `<span class="badge badge-info"><span class="dot dot-ok"></span>Credentials Verified · Idle</span>`;
    if (state === "archived") return `<span class="badge badge-neutral">Archived</span>`;
    return `<span class="badge badge-warning"><span class="dot dot-warn"></span>No Token Connected</span>`;
  }

  function renderSvgBarChart(labels, values, color = "#3b82f6") {
    if (!labels || !labels.length) return `<div class="empty-state"><p>No time-series points recorded in this range.</p></div>`;
    const maxV = Math.max(1, ...values.map((v) => Number(v || 0)));
    const w = 560, h = 150, pad = 22;
    const barW = Math.max(4, Math.floor((w - pad * 2) / labels.length) - 3);
    const bars = values.map((v, idx) => {
      const val = Number(v || 0);
      const bh = Math.round(((h - pad * 2) * val) / maxV);
      const x = pad + idx * ((w - pad * 2) / labels.length);
      const y = h - pad - bh;
      return `<rect x="${x.toFixed(1)}" y="${y}" width="${barW}" height="${Math.max(2, bh)}" rx="3" fill="${color}" opacity="0.88">
        <title>${esc(labels[idx])}: ${val}</title>
      </rect>`;
    }).join("");
    return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:155px;display:block">
      <line x1="${pad}" y1="${h - pad}" x2="${w - pad}" y2="${h - pad}" stroke="var(--border)" />
      ${bars}
      <text x="${pad}" y="${h - 5}" fill="var(--text-muted)" font-size="10">${esc(labels[0])}</text>
      <text x="${w - pad}" y="${h - 5}" fill="var(--text-muted)" font-size="10" text-anchor="end">${esc(labels[labels.length - 1])}</text>
    </svg>`;
  }

  /* ── Studio Renderers ── */
  const RENDERERS = {
    /* 1. Workspace Overview & Custom Dashboard Builder */
    async overview(vp) {
      const [wsData, layoutData] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/dashboard-layout`),
      ]);
      const u = wsData.usage || {};
      const runningBots = S.bots.filter((b) => b.connection_state === "running").length;
      const verifiedBots = S.bots.filter((b) => b.has_credentials).length;
      const checklistDone = [
        S.bots.length > 0,
        u.files_count > 0,
      ];

      const widgetsHtml = (layoutData.widgets || []).map((wid, idx) => {
        if (wid === "bot_fleet") {
          return `<div class="card">
            <div class="card-header"><h3 class="card-title">🤖 Bot Fleet Summary</h3><button class="btn btn-sm" data-goto="bots">Manage Bots</button></div>
            <div style="font-size:26px;font-weight:700">${S.bots.length}</div>
            <div class="stat-meta">${S.bots.filter((b) => b.connection_state === "running").length} live workers · ${S.bots.filter((b) => b.has_credentials).length} with verified credentials</div>
          </div>`;
        }
        if (wid === "filestore_usage") {
          return `<div class="card">
            <div class="card-header"><h3 class="card-title">📦 File Store Usage</h3>${u.files_count == null ? "" : '<button class="btn btn-sm" data-goto="filestore">Open Vault</button>'}</div>
            <div style="font-size:26px;font-weight:700">${u.files_count == null ? "Unavailable" : `${u.files_count} files`}</div>
            <div class="stat-meta">${u.storage_bytes == null ? "File Store permission required." : `${fmtBytes(u.storage_bytes)} indexed${u.files_count >= u.filestore_sample_limit ? ` (sample of up to ${u.filestore_sample_limit} records)` : " across workspace"}`}</div>
          </div>`;
        }
        if (wid === "active_tasks") {
          return `<div class="card">
            <div class="card-header"><h3 class="card-title">⬇ Active Transfer Queue</h3></div>
            <div class="stat-meta">Workspace-attributed transfers are not available yet.</div>
          </div>`;
        }
        if (wid === "notifications") {
          const ntfs = (wsData.recent_notifications || []).slice(0, 4);
          return `<div class="card">
            <div class="card-header"><h3 class="card-title">🔔 Recent Alerts</h3><span class="badge badge-neutral">${u.unread_notifications == null ? "Permission required" : `${u.unread_notifications} unread`}</span></div>
            ${ntfs.length ? ntfs.map((n) => `<div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:12.5px"><strong>${esc(n.title)}</strong> — <span style="color:var(--text-secondary)">${esc(n.message)}</span></div>`).join("") : `<div class="stat-meta">${u.unread_notifications == null ? "Logs permission required." : "No alerts recorded yet."}</div>`}
          </div>`;
        }
        if (wid === "scheduled_jobs") {
          return `<div class="card">
            <div class="card-header"><h3 class="card-title">⏱ Scheduled Jobs</h3></div>
            <div class="stat-meta">Workspace-attributed schedules are not available yet.</div>
          </div>`;
        }
        return `<div class="card">
          <div class="card-header"><h3 class="card-title">${esc(wid.replace(/_/g, " ").toUpperCase())}</h3>
          <button class="btn btn-sm" data-remove-widget="${esc(wid)}" title="Remove widget">✕</button></div>
          <div class="stat-meta">This widget's data is not available yet.</div>
        </div>`;
      }).join("");

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <p class="eyebrow">VIDEL STUDIO <span aria-hidden="true">/</span> WORKSPACE</p>
            <h1 class="page-title">${esc(wsData.workspace.icon || "🚀")} ${esc(wsData.workspace.name)}</h1>
            <p class="page-subtitle">${esc(wsData.workspace.description || "Your bots, projects and files in one place")} · <span class="badge badge-info">${esc(wsData.my_role)}</span></p>
          </div>
          <div class="toolbar-group">
            <button id="btn-new-ws" class="btn">+ New Workspace</button>
            <button id="btn-rename-ws" class="btn">Rename Workspace</button>
            <button id="btn-customize-dash" class="btn">Customize Widgets</button>
            <button class="btn btn-primary" data-goto="bot_create">+ Connect a bot</button>
          </div>
        </div>

        <section class="overview-hero" aria-labelledby="overview-hero-title">
          <div class="overview-hero-copy">
            <span class="overview-kicker"><span class="overview-spark" aria-hidden="true">✳</span> WORKSPACE COMMAND CENTER</span>
            <h2 id="overview-hero-title">Build what's next.<br><span>Your workspace is ready.</span></h2>
            <p>Connect a Telegram bot, organize your files, and keep the work that matters in view. This snapshot uses data available to your current workspace role.</p>
            <div class="overview-hero-actions">
              <button class="btn btn-primary" data-goto="bot_create">Connect a bot <span aria-hidden="true">→</span></button>
              <button class="btn overview-secondary-action" data-goto="bots">Explore bot studio</button>
            </div>
          </div>
          <div class="overview-snapshot" aria-label="Current workspace snapshot">
            <div class="snapshot-heading"><span>WORKSPACE SNAPSHOT</span><span class="snapshot-live">From current records</span></div>
            <div class="snapshot-stat"><span class="snapshot-icon" aria-hidden="true">◈</span><div><strong>${S.bots.length.toLocaleString()}</strong><span>registered bots</span></div></div>
            <div class="snapshot-stat"><span class="snapshot-icon is-live" aria-hidden="true">↗</span><div><strong>${runningBots.toLocaleString()}</strong><span>bots marked running</span></div></div>
            <div class="snapshot-footer">${verifiedBots.toLocaleString()} bot credentials verified${u.files_count == null ? " · File Store count restricted or unavailable" : ` · ${Number(u.files_count).toLocaleString()} files indexed`}</div>
          </div>
          <span class="overview-orbit orbit-one" aria-hidden="true"></span><span class="overview-orbit orbit-two" aria-hidden="true"></span>
        </section>

        <div class="card" style="margin-bottom:18px">
          <div class="card-header">
            <h3 class="card-title">Getting Started Checklist</h3>
            <span class="badge badge-success">${checklistDone.filter(Boolean).length} / 2 Completed${u.files_count == null ? " · File status unavailable" : ""}</span>
          </div>
          <div class="grid grid-2">
            <div>${checklistDone[0] ? "✅" : "⬜"} <strong>1. Register or connect a Telegram bot</strong><div class="stat-meta">Use Bot Creation Studio; legacy bots require migration.</div></div>
            <div>${u.files_count == null ? "—" : (checklistDone[1] ? "✅" : "⬜")} <strong>2. Upload File Store items</strong><div class="stat-meta">${u.files_count == null ? "File Store permission required." : "Organize files & inspect duplicate candidates."}</div></div>
          </div>
        </div>

        <div id="dash-customizer" class="card" style="margin-bottom:18px" hidden>
          <div class="card-header"><h3 class="card-title">Custom Dashboard Builder</h3><button id="btn-reset-dash" class="btn btn-sm">Restore Default Layout</button></div>
          <div class="toolbar-group">
            ${(layoutData.catalog || []).map((w) => {
              const on = (layoutData.widgets || []).includes(w.id);
              return `<button class="btn btn-sm ${on ? "btn-primary" : ""}" data-toggle-widget="${esc(w.id)}">${on ? "✓ " : "+ "}${esc(w.title)}</button>`;
            }).join("")}
          </div>
        </div>

        <div class="grid grid-3">${widgetsHtml}</div>
      `;

      vp.querySelectorAll("[data-goto]").forEach((b) => { b.onclick = () => navigate(b.dataset.goto); });
      $("btn-customize-dash").onclick = () => { $("dash-customizer").hidden = !$("dash-customizer").hidden; };
      $("btn-reset-dash").onclick = async () => {
        await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/dashboard-layout`, { method: "POST", body: { reset: true } });
        toast("Dashboard restored to default layout", "success");
        navigate("overview", true);
      };
      vp.querySelectorAll("[data-toggle-widget]").forEach((b) => {
        b.onclick = async () => {
          const wid = b.dataset.toggleWidget;
          const cur = layoutData.widgets || [];
          const next = cur.includes(wid) ? cur.filter((x) => x !== wid) : [...cur, wid];
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/dashboard-layout`, { method: "POST", body: { widgets: next } });
          navigate("overview", true);
        };
      });
      $("btn-new-ws").onclick = async () => {
        const name = prompt("New workspace name:");
        if (!name) return;
        const res = await api("/api/v1/workspaces", { method: "POST", body: { name, icon: "⚡" } });
        S.wsId = res.workspace.workspace_id;
        localStorage.setItem("videl_ws_id", S.wsId);
        const me = await api("/api/v1/auth/me");
        S.workspaces = me.workspaces;
        await refreshBotsAndSelectors();
        toast("Workspace created", "success");
        navigate("overview", true);
      };
      $("btn-rename-ws").onclick = async () => {
        const name = prompt("Rename current workspace:", wsData.workspace.name);
        if (!name) return;
        await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}`, { method: "PATCH", body: { name } });
        const me = await api("/api/v1/auth/me");
        S.workspaces = me.workspaces;
        await refreshBotsAndSelectors();
        navigate("overview", true);
      };
    },

    /* 2. Bot Management Center */
    async bots(vp) {
      await refreshBotsAndSelectors();
      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Bot Management Studio</h1>
            <p class="page-subtitle">Manage profiles, credentials, commands, configuration revisions, and lifecycle across your bots.</p>
          </div>
          <div class="toolbar-group">
            <button id="btn-view-grid" class="btn btn-sm ${S.botViewMode === "grid" ? "btn-primary" : ""}">Grid</button>
            <button id="btn-view-list" class="btn btn-sm ${S.botViewMode === "list" ? "btn-primary" : ""}">List</button>
            <button class="btn btn-primary" id="btn-add-bot">+ Register / Connect Bot</button>
          </div>
        </div>

        <div class="toolbar">
          <div class="toolbar-group">
            <input id="bot-search" class="input" style="width:240px" placeholder="Search name, @username, tag…">
            <select id="bot-status-filter" class="select" style="width:185px">
              <option value="all">All Connection States</option>
              <option value="running">Running Live</option>
              <option value="credentials_verified_idle">Credentials Verified</option>
              <option value="disconnected">No Token Connected</option>
              <option value="archived">Archived</option>
            </select>
          </div>
          <span class="stat-meta">${S.bots.length} bot(s) in workspace</span>
        </div>

        <div id="bots-container"></div>
      `;

      const renderList = () => {
        const q = ($("bot-search").value || "").toLowerCase();
        const st = $("bot-status-filter").value;
        const filtered = S.bots.filter((b) => {
          if (st !== "all" && b.connection_state !== st) return false;
          if (q && !`${b.display_name} ${b.username} ${(b.tags || []).join(" ")}`.toLowerCase().includes(q)) return false;
          return true;
        });
        const box = $("bots-container");
        if (!filtered.length) {
          box.innerHTML = `<div class="empty-state"><h4>No matching bots in this workspace</h4><p>Connect an existing @BotFather token or create a configuration blueprint.</p><button class="btn btn-primary" id="empty-create-bot">+ Open Bot Creation Studio</button></div>`;
          const eb = $("empty-create-bot");
          if (eb) eb.onclick = () => navigate("bot_create");
          return;
        }
        if (S.botViewMode === "list") {
          box.innerHTML = `<div class="table-wrap"><table class="data-table">
            <thead><tr><th>Bot</th><th>Category</th><th>Connection</th><th>Version</th><th>Actions</th></tr></thead>
            <tbody>${filtered.map((b) => `<tr>
              <td><strong>${esc(b.avatar_emoji || "🤖")} ${esc(b.display_name)}</strong><div class="mono" style="color:var(--text-muted)">@${esc(b.username)}</div></td>
              <td>${esc(b.category)}</td>
              <td>${connBadge(b.connection_state)}</td>
              <td class="mono">v${b.config_version || 1}</td>
              <td><div class="toolbar-group">
                <button class="btn btn-sm" data-act="cfg" data-id="${esc(b.bot_id)}">Configure</button>
                <button class="btn btn-sm" data-act="cmd" data-id="${esc(b.bot_id)}">Commands</button>
                <button class="btn btn-sm" data-act="cln" data-id="${esc(b.bot_id)}">Clone</button>
                <button class="btn btn-sm btn-danger" data-act="del" data-id="${esc(b.bot_id)}">Delete</button>
              </div></td>
            </tr>`).join("")}</tbody></table></div>`;
        } else {
          box.innerHTML = `<div class="grid grid-3">${filtered.map((b) => `
            <div class="card">
              <div class="card-header">
                <div>
                  <div style="font-weight:700;font-size:15.5px">${esc(b.avatar_emoji || "🤖")} ${esc(b.display_name)}</div>
                  <div class="mono" style="color:var(--text-muted)">@${esc(b.username)} · v${b.config_version || 1}</div>
                </div>
                ${connBadge(b.connection_state)}
              </div>
              <p style="color:var(--text-secondary);font-size:13px;min-height:38px;margin:0 0 10px">${esc(b.description || "No description")}</p>
              <div style="margin-bottom:12px;display:flex;gap:5px;flex-wrap:wrap">
                <span class="badge badge-neutral">${esc(b.category)}</span>
                ${(b.tags || []).map((t) => `<span class="badge badge-neutral">#${esc(t)}</span>`).join("")}
              </div>
              <div class="toolbar-group">
                <button class="btn btn-sm btn-primary" data-act="cfg" data-id="${esc(b.bot_id)}">Configure</button>
                <button class="btn btn-sm" data-act="cmd" data-id="${esc(b.bot_id)}">Commands</button>
                <button class="btn btn-sm" data-act="cln" data-id="${esc(b.bot_id)}">Clone</button>
                <button class="btn btn-sm" data-act="arc" data-id="${esc(b.bot_id)}">${b.archived ? "Unarchive" : "Archive"}</button>
                <button class="btn btn-sm btn-danger" data-act="del" data-id="${esc(b.bot_id)}">Delete</button>
              </div>
            </div>
          `).join("")}</div>`;
        }

        box.querySelectorAll("[data-act]").forEach((btn) => {
          btn.onclick = async () => {
            const bid = btn.dataset.id;
            const act = btn.dataset.act;
            S.botId = bid;
            localStorage.setItem("videl_bot_id", bid);
            $("bot-selector").value = bid;
            if (act === "cfg") return navigate("bot_config");
            if (act === "cmd") return navigate("commands");
            if (act === "cln") return navigate("clone_studio");
            if (act === "arc") {
              const cur = S.bots.find((x) => x.bot_id === bid);
              await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(bid)}`, { method: "PATCH", body: { archived: !cur.archived } });
              toast("Bot archive status updated", "success");
              return navigate("bots", true);
            }
            if (act === "del") {
              const ok = await confirmModal("Delete Bot?", "This removes the bot from your workspace and scrubs stored credentials. Continue?", "Delete Bot");
              if (!ok) return;
              await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(bid)}`, { method: "DELETE" });
              toast("Bot deleted", "success");
              return navigate("bots", true);
            }
          };
        });
      };

      $("btn-add-bot").onclick = () => navigate("bot_create");
      $("btn-view-grid").onclick = () => { S.botViewMode = "grid"; navigate("bots", true); };
      $("btn-view-list").onclick = () => { S.botViewMode = "list"; navigate("bots", true); };
      $("bot-search").oninput = renderList;
      $("bot-status-filter").onchange = renderList;
      renderList();
    },

    /* 3. Guided Bot Creation Studio */
    async bot_create(vp) {
      const w = S.wizard;
      const templates = S.catalogs.bot_templates || {};
      const deps = S.catalogs.feature_dependencies || {};
      const curTpl = templates[w.template] || { name: "File Store", features: {} };
      if (!Object.keys(w.features).length) w.features = Object.assign({}, curTpl.features || {});

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Bot Creation Studio</h1>
            <p class="page-subtitle">Guided 4-step wizard to connect a @BotFather token or design a reusable bot blueprint.</p>
          </div>
        </div>

        <div class="stepper">
          <span class="step-pill ${w.step === 1 ? "active" : w.step > 1 ? "done" : ""}">1. Identity</span>
          <span class="step-pill ${w.step === 2 ? "active" : w.step > 2 ? "done" : ""}">2. Telegram Connection</span>
          <span class="step-pill ${w.step === 3 ? "active" : w.step > 3 ? "done" : ""}">3. Template & Features</span>
          <span class="step-pill ${w.step === 4 ? "active" : ""}">4. Review & Register</span>
        </div>

        <div class="card">
          ${w.step === 1 ? `
            <div class="grid grid-2">
              <div class="field">
                <label class="field-label">Bot Display Name *</label>
                <input id="wiz-name" class="input" value="${esc(w.display_name)}" placeholder="e.g. Acme File Vault">
              </div>
              <div class="field">
                <label class="field-label">Bot Username (without @)</label>
                <input id="wiz-user" class="input" value="${esc(w.username)}" placeholder="acme_vault_bot">
              </div>
            </div>
            <div class="field">
              <label class="field-label">Description</label>
              <textarea id="wiz-desc" class="textarea" placeholder="Purpose of this bot…">${esc(w.description)}</textarea>
            </div>
            <div class="field">
              <label class="field-label">Tags (comma-separated)</label>
              <input id="wiz-tags" class="input" value="${esc(w.tags)}" placeholder="production, filestore, media">
            </div>
          ` : ""}

          ${w.step === 2 ? `
            <div class="callout callout-info">
              <strong>How Telegram Bot Creation Works:</strong> Telegram requires interactive owner confirmation inside <code>@BotFather</code> (<code>/newbot</code>) to mint a brand-new bot username. Create or copy your token from <code>@BotFather</code>, then verify it below — or leave blank to save a draft blueprint first.
            </div>
            <div class="field">
              <label class="field-label">Telegram Bot Token (Encrypted at rest with Fernet, never shown again)</label>
              <div style="display:flex;gap:8px">
                <input id="wiz-token" class="input mono" type="password" value="${esc(w.bot_token)}" placeholder="123456789:AAH...">
                <button id="btn-verify-tok" class="btn btn-primary" type="button">Verify via getMe</button>
              </div>
            </div>
            <div id="wiz-tok-diag">${w.tokenDiag ? `<div class="callout">${esc(w.tokenDiag.diagnostic)} ${w.tokenDiag.username ? `(@${esc(w.tokenDiag.username)})` : ""}</div>` : ""}</div>
          ` : ""}

          ${w.step === 3 ? `
            <div class="field">
              <label class="field-label">Select Starter Template</label>
              <div class="grid grid-3">
                ${Object.entries(templates).map(([k, t]) => `
                  <div class="card" style="cursor:pointer;border-color:${w.template === k ? "var(--accent)" : "var(--border)"}" data-tpl="${esc(k)}">
                    <strong>${esc(t.name)}</strong>
                    <div class="stat-meta">${esc(t.description)}</div>
                  </div>
                `).join("")}
              </div>
            </div>
            <div class="field">
              <label class="field-label">Enabled Capabilities (Dependencies Checked Automatically)</label>
              <div class="grid grid-2">
                ${Object.keys(w.features).map((fk) => `
                  <label style="display:flex;align-items:flex-start;gap:8px;padding:8px;border:1px solid var(--border);border-radius:8px">
                    <input type="checkbox" data-feat="${esc(fk)}" ${w.features[fk] ? "checked" : ""}>
                    <div>
                      <div style="font-weight:600">${esc(fk.replace(/_/g, " "))}</div>
                      ${deps[fk] ? `<div class="field-hint">Requires: <code>${esc(deps[fk].requires)}</code></div>` : ""}
                    </div>
                  </label>
                `).join("")}
              </div>
            </div>
          ` : ""}

          ${w.step === 4 ? `
            <h3 class="card-title" style="margin-bottom:12px">Configuration Summary</h3>
            <div class="table-wrap" style="margin-bottom:14px"><table class="data-table"><tbody>
              <tr><th>Display Name</th><td>${esc(w.display_name || "(Required)")}</td></tr>
              <tr><th>Username</th><td class="mono">@${esc(w.username || "auto_assigned_bot")}</td></tr>
              <tr><th>Template</th><td>${esc(curTpl.name)}</td></tr>
              <tr><th>Token Attached</th><td>${w.bot_token ? "Yes (will be encrypted at rest)" : "No (Draft Blueprint mode)"}</td></tr>
              <tr><th>Enabled Features</th><td>${Object.entries(w.features).filter((x) => x[1]).map((x) => `<code>${esc(x[0])}</code>`).join(" ") || "Minimal"}</td></tr>
            </tbody></table></div>
          ` : ""}

          <div style="display:flex;justify-content:space-between;margin-top:18px">
            <button id="wiz-prev" class="btn" ${w.step === 1 ? "disabled" : ""}>← Previous</button>
            <div class="toolbar-group">
              ${w.step < 4
                ? `<button id="wiz-next" class="btn btn-primary">Continue →</button>`
                : `<button id="wiz-submit" class="btn btn-primary">Register Bot in Workspace</button>`}
            </div>
          </div>
        </div>
      `;

      const saveCurrentInputs = () => {
        if ($("wiz-name")) w.display_name = $("wiz-name").value.trim();
        if ($("wiz-user")) w.username = $("wiz-user").value.trim().replace(/^@/, "");
        if ($("wiz-desc")) w.description = $("wiz-desc").value.trim();
        if ($("wiz-tags")) w.tags = $("wiz-tags").value;
        if ($("wiz-token")) w.bot_token = $("wiz-token").value.trim();
      };

      if ($("btn-verify-tok")) {
        $("btn-verify-tok").onclick = async () => {
          saveCurrentInputs();
          try {
            const diag = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/verify-token`, { method: "POST", body: { bot_token: w.bot_token } });
            w.tokenDiag = diag;
            if (diag.username) w.username = diag.username;
            if (diag.first_name && !w.display_name) w.display_name = diag.first_name;
            navigate("bot_create", true);
          } catch (e) { toast(e.message, "error"); }
        };
      }

      vp.querySelectorAll("[data-tpl]").forEach((el) => {
        el.onclick = () => {
          w.template = el.dataset.tpl;
          w.features = Object.assign({}, (templates[w.template] || {}).features || {});
          navigate("bot_create", true);
        };
      });

      vp.querySelectorAll("[data-feat]").forEach((cb) => {
        cb.onchange = () => { w.features[cb.dataset.feat] = cb.checked; };
      });

      $("wiz-prev").onclick = () => { saveCurrentInputs(); w.step = Math.max(1, w.step - 1); navigate("bot_create", true); };
      if ($("wiz-next")) {
        $("wiz-next").onclick = () => {
          saveCurrentInputs();
          if (w.step === 1 && w.display_name.length < 2) return toast("Enter a display name (2+ characters).", "error");
          w.step = Math.min(4, w.step + 1);
          navigate("bot_create", true);
        };
      }
      if ($("wiz-submit")) {
        $("wiz-submit").onclick = async () => {
          try {
            const created = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots`, {
              method: "POST",
              body: {
                display_name: w.display_name,
                username: w.username,
                description: w.description,
                template: w.template,
                bot_token: w.bot_token,
                features: w.features,
                tags: w.tags.split(",").map((s) => s.trim()).filter(Boolean),
              },
            });
            S.wizard = { step: 1, display_name: "", username: "", description: "", template: "filestore", bot_token: "", tokenDiag: null, features: {}, tags: "" };
            S.botId = created.bot.bot_id;
            await refreshBotsAndSelectors();
            toast(`Bot ${created.bot.display_name} created!`, "success");
            navigate("bots", true);
          } catch (e) { toast(e.message, "error"); }
        };
      }
    },

    /* 4. Bot Fleet & Operations Health + Webhooks */
    async fleet(vp) {
      const [fleet, ops] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/fleet`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/health`),
      ]);
      const sc = fleet.state_counts || {};
      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Bot Fleet & Operations Center</h1>
            <p class="page-subtitle">Cross-bot health, credential vs runtime status, config drift detection, bulk operations, and webhook diagnostics.</p>
          </div>
        </div>

        <div class="grid grid-4" style="margin-bottom:18px">
          <div class="stat-card"><div class="stat-label">Total Workspace Bots</div><div class="stat-value">${fleet.total_bots || 0}</div></div>
          <div class="stat-card"><div class="stat-label">Live Worker Processes</div><div class="stat-value">${sc.running || 0}</div><div class="stat-meta">Active MTProto workers</div></div>
          <div class="stat-card"><div class="stat-label">Credentials Valid · Idle</div><div class="stat-value">${sc.credentials_verified_idle || 0}</div><div class="stat-meta">Token verified, process stopped</div></div>
          <div class="stat-card"><div class="stat-label">Config Drift Fields</div><div class="stat-value">${fleet.config_drift_fields == null ? "—" : fleet.config_drift_fields.length}</div><div class="stat-meta">${fleet.config_drift_fields == null ? "Bot settings permission required" : (fleet.config_drift_fields.join(", ") || "All bots aligned")}</div></div>
        </div>

        <div class="grid grid-2" style="margin-bottom:18px">
          <div class="card">
            <div class="card-header"><h3 class="card-title">Bulk Fleet Action (Preview Required)</h3></div>
            <div class="field">
              <label class="field-label">Operation across all active workspace bots</label>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <select id="bulk-op" class="select" style="width:190px">
                  <option value="add_label">Apply Fleet Label</option>
                  <option value="set_fleet_group">Assign Fleet Group</option>
                  <option value="notify_on_error">Enable Error Alerts</option>
                </select>
                <input id="bulk-val" class="input" style="width:180px" placeholder="Label or group name" value="fleet-v2">
                <button id="btn-bulk-preview" class="btn btn-primary">Preview & Run</button>
              </div>
            </div>
            <div class="stat-meta">${esc(ops.health.architecture_note)}</div>
          </div>

          <div class="card">
            <div class="card-header"><h3 class="card-title">Outbound Integration Webhooks</h3></div>
            ${ops.webhooks.availability ? `<div class="stat-meta">Integration permission required.</div>` : `
            <div style="display:flex;gap:8px;margin-bottom:10px">
              <input id="wh-url" class="input" placeholder="https://example.com/webhook" aria-label="Public webhook URL without credentials">
              <button id="btn-wh-add" class="btn btn-primary">Add</button>
            </div>
            <div class="stat-meta">Use a public endpoint without credentials in the URL. Queries, fragments and redirects are not supported. Saved URLs are not displayed.</div>
            ${(ops.webhooks.webhooks || []).map((w) => `
              <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);font-size:12.5px">
                <span>${esc(w.name)} (${esc(w.last_status)})</span>
                <button class="btn btn-sm" data-ping-wh="${esc(w.webhook_id)}">Ping</button>
              </div>
            `).join("") || `<div class="stat-meta">No outbound webhooks configured.</div>`}
            `}
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h3 class="card-title">Active Health & Security Alerts</h3></div>
          ${(ops.health.alerts || []).length
            ? ops.health.alerts.map((a) => `<div class="callout">${esc(a.severity.toUpperCase())}: ${esc(a.message)}</div>`).join("")
            : `<div class="stat-meta">All workspace bots and storage integrations report healthy status.</div>`}
        </div>
      `;

      $("btn-bulk-preview").onclick = async () => {
        const ids = (fleet.bots || []).filter((b) => !b.archived).map((b) => b.bot_id);
        if (!ids.length) return toast("No active bots in workspace.", "error");
        const op = $("bulk-op").value;
        const val = op === "notify_on_error" ? true : $("bulk-val").value;
        const prev = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/fleet`, {
          method: "POST", body: { bot_ids: ids, operation: op, value: val, confirmed: false },
        });
        const ok = await confirmModal(
          "Confirm Bulk Fleet Operation",
          `Operation <code>${esc(op)}</code> will update <strong>${prev.affected_count}</strong> bot(s):<br>` +
          prev.affected_bots.map((b) => `• ${esc(b.display_name)} (@${esc(b.username)})`).join("<br>"),
          "Execute Bulk Update"
        );
        if (!ok) return;
        await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/fleet`, {
          method: "POST", body: { bot_ids: ids, operation: op, value: val, confirmed: true },
        });
        toast("Bulk fleet operation applied", "success");
        navigate("fleet", true);
      };

      if ($("btn-wh-add")) $("btn-wh-add").onclick = async () => {
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/health`, { method: "POST", body: { url: $("wh-url").value } });
          toast("Webhook registered", "success");
          navigate("fleet", true);
        } catch (e) { toast(e.message, "error"); }
      };

      vp.querySelectorAll("[data-ping-wh]").forEach((b) => {
        b.onclick = async () => {
          try {
            const r = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/health`, { method: "POST", body: { action: "test", webhook_id: b.dataset.pingWh } });
            toast(`Webhook ping status: ${r.event.status} (HTTP ${r.event.http_code})`, r.event.status === "delivered" ? "success" : "error");
            navigate("fleet", true);
          } catch (e) { toast(e.message, "error"); }
        };
      });
    },

    /* 5. Bot Configuration Studio & Environment Manager */
    async bot_config(vp) {
      if (!S.botId) {
        vp.innerHTML = `<div class="empty-state"><h4>Select or create a bot first</h4><button class="btn btn-primary" id="cfg-go-create">+ Create Bot</button></div>`;
        $("cfg-go-create").onclick = () => navigate("bot_create");
        return;
      }
      const [botRes, revRes, envRes] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/config`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/environments`),
      ]);
      const bot = botRes.bot;
      const deps = S.catalogs.feature_dependencies || {};

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Bot Configuration Studio · ${esc(bot.display_name)}</h1>
            <p class="page-subtitle">Active Revision: <span class="mono">v${bot.config_version || 1}</span> · Feature dependency validation, drafts, history, and Dev/Test/Prod promotion.</p>
          </div>
          <div class="toolbar-group">
            <button id="cfg-preview" class="btn">Preview Diff</button>
            <button id="cfg-draft" class="btn">Save Draft</button>
            <button id="cfg-reset" class="btn btn-danger">Reset Defaults</button>
            <button id="cfg-apply" class="btn btn-primary">Apply Changes</button>
          </div>
        </div>

        <div id="cfg-diff-box" hidden class="card" style="margin-bottom:16px"></div>

        <div class="grid grid-2" style="margin-bottom:18px">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:12px">Feature Toggles & Dependency Rules</h3>
            ${Object.entries(bot.features || {}).map(([k, v]) => `
              <label style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
                <span><strong>${esc(k.replace(/_/g, " "))}</strong>${deps[k] ? `<div class="field-hint">Requires <code>${esc(deps[k].requires)}</code></div>` : ""}</span>
                <input type="checkbox" data-cfg-feat="${esc(k)}" ${v ? "checked" : ""}>
              </label>
            `).join("")}
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:12px">Runtime & Message Parameters</h3>
            <div class="field">
              <label class="field-label">Welcome Message</label>
              <textarea id="cfg-welcome" class="textarea">${esc((bot.settings || {}).welcome_message || "")}</textarea>
            </div>
            <div class="field">
              <label class="field-label">Custom File Caption Template</label>
              <input id="cfg-caption" class="input" value="${esc((bot.settings || {}).custom_caption || "")}">
            </div>
            <div class="grid grid-2">
              <div class="field">
                <label class="field-label">Auto-Delete Seconds (0 = off)</label>
                <input id="cfg-autodel" class="input" type="number" min="0" value="${Number((bot.settings || {}).auto_delete_seconds || 0)}">
              </div>
              <div class="field">
                <label class="field-label">Revision Change Note</label>
                <input id="cfg-note" class="input" placeholder="Why did this change?">
              </div>
            </div>
          </div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <div class="card-header"><h3 class="card-title">Bot Environment Manager</h3></div>
            <p class="field-hint" style="margin-bottom:10px">Promote validated configuration safely across isolated stages (never overwrites Production without confirmation).</p>
            <div class="table-wrap"><table class="data-table">
              <thead><tr><th>Environment</th><th>Enabled Features</th><th>Action</th></tr></thead>
              <tbody>${(envRes.environments || []).map((e) => `
                <tr>
                  <td><strong>${esc(e.label)}</strong></td>
                  <td>${Object.values(e.features || {}).filter(Boolean).length} active</td>
                  <td>${e.env !== "production" ? `<button class="btn btn-sm" data-promote="${esc(e.env)}">Promote → ${e.env === "development" ? "Testing" : "Production"}</button>` : `<span class="badge badge-success">Live Stage</span>`}</td>
                </tr>
              `).join("")}</tbody>
            </table></div>
          </div>

          <div class="card">
            <div class="card-header"><h3 class="card-title">Version History</h3></div>
            <div class="table-wrap"><table class="data-table">
              <thead><tr><th>Ver</th><th>Status</th><th>Note</th><th>Restore</th></tr></thead>
              <tbody>${(revRes.revisions || []).slice(0, 8).map((r) => `
                <tr>
                  <td class="mono">v${r.version}</td>
                  <td><span class="badge ${r.status === "applied" ? "badge-success" : "badge-neutral"}">${esc(r.status)}</span></td>
                  <td>${esc(r.note)}</td>
                  <td><button class="btn btn-sm" data-restore="${r.version}">Restore</button></td>
                </tr>
              `).join("")}</tbody>
            </table></div>
          </div>
        </div>
      `;

      const collectPayload = (mode) => {
        const features = {};
        vp.querySelectorAll("[data-cfg-feat]").forEach((cb) => { features[cb.dataset.cfgFeat] = cb.checked; });
        return {
          mode,
          note: $("cfg-note").value,
          features,
          settings: {
            welcome_message: $("cfg-welcome").value,
            custom_caption: $("cfg-caption").value,
            auto_delete_seconds: Number($("cfg-autodel").value || 0),
          },
        };
      };

      vp.querySelectorAll("input, textarea").forEach((el) => { el.oninput = () => { S.unsaved = true; }; });

      const sendConfig = async (mode) => {
        try {
          const res = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/config`, {
            method: "POST", body: collectPayload(mode),
          });
          if (mode === "preview") {
            const box = $("cfg-diff-box");
            box.hidden = false;
            box.innerHTML = `<h4 style="margin:0 0 8px">Configuration Diff (${res.diffs.length} change(s))</h4>` +
              (res.diffs.length ? res.diffs.map((d) => `<div class="mono">${esc(d.field)}: ${esc(JSON.stringify(d.before))} → <strong>${esc(JSON.stringify(d.after))}</strong></div>`).join("") : "<div>No differences from active configuration.</div>");
            return;
          }
          S.unsaved = false;
          toast(`Configuration ${mode} succeeded`, "success");
          navigate("bot_config", true);
        } catch (e) { toast(e.message, "error"); }
      };

      $("cfg-preview").onclick = () => sendConfig("preview");
      $("cfg-draft").onclick = () => sendConfig("draft");
      $("cfg-apply").onclick = () => sendConfig("apply");
      $("cfg-reset").onclick = async () => {
        if (await confirmModal("Reset Bot Configuration?", "Restore all features and settings to template defaults?", "Reset")) {
          sendConfig("reset");
        }
      };

      vp.querySelectorAll("[data-restore]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/config`, {
            method: "POST", body: { restore_version: Number(b.dataset.restore) },
          });
          S.unsaved = false;
          toast(`Restored v${b.dataset.restore}`, "success");
          navigate("bot_config", true);
        };
      });

      vp.querySelectorAll("[data-promote]").forEach((b) => {
        b.onclick = async () => {
          const src = b.dataset.promote;
          const tgt = src === "development" ? "testing" : "production";
          const prev = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/environments`, {
            method: "POST", body: { source_env: src, target_env: tgt, confirmed: false },
          });
          const ok = await confirmModal(
            `Promote ${src} → ${tgt}?`,
            `${prev.diffs.length} field difference(s) will be promoted to <strong>${esc(tgt)}</strong>.`,
            "Promote Environment"
          );
          if (!ok) return;
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/environments`, {
            method: "POST", body: { source_env: src, target_env: tgt, confirmed: true },
          });
          toast(`Promoted ${src} → ${tgt}`, "success");
          navigate("bot_config", true);
        };
      });
    },

    /* 6. Command Management Studio */
    async commands(vp) {
      if (!S.botId) {
        vp.innerHTML = `<div class="empty-state"><h4>Select a bot first</h4></div>`;
        return;
      }
      const res = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/commands`);
      const cmds = res.commands || [];
      const cats = S.catalogs.command_categories || ["General", "Custom"];
      const perms = S.catalogs.command_permissions || ["everyone", "owner"];

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Command Management Studio</h1>
            <p class="page-subtitle">Configure command metadata, permissions, categories, and BotFather menu presentation. Built-in handlers are clearly distinguished from static metadata entries.</p>
          </div>
        </div>

        <div class="grid grid-3" style="margin-bottom:18px">
          <div class="card" style="grid-column: span 2">
            <h3 class="card-title" style="margin-bottom:10px">Add or Update Command</h3>
            <div class="grid grid-2">
              <div class="field"><label class="field-label">Command (no slash)</label><input id="cmd-name" class="input mono" placeholder="faq"></div>
              <div class="field"><label class="field-label">Category</label>
                <select id="cmd-cat" class="select">${cats.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}</select>
              </div>
            </div>
            <div class="grid grid-2">
              <div class="field"><label class="field-label">Description (2–256 chars)</label><input id="cmd-desc" class="input" placeholder="Frequently asked questions"></div>
              <div class="field"><label class="field-label">Permission Scope</label>
                <select id="cmd-perm" class="select">${perms.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("")}</select>
              </div>
            </div>
            <button id="btn-save-cmd" class="btn btn-primary">Save Command</button>
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:8px">@BotFather Menu Preview</h3>
            <pre class="mono" style="background:var(--bg-subtle);padding:10px;border-radius:8px;max-height:175px;overflow:auto;margin:0">${esc(cmds.filter((c) => c.enabled && c.permission === "everyone").map((c) => `${c.command} - ${c.description}`).join("\n") || "No public commands enabled")}</pre>
          </div>
        </div>

        <div class="table-wrap"><table class="data-table">
          <thead><tr><th>Command</th><th>Description</th><th>Category</th><th>Permission</th><th>Binding</th><th>State</th><th></th></tr></thead>
          <tbody>${cmds.map((c) => `
            <tr>
              <td class="mono"><strong>/${esc(c.command)}</strong></td>
              <td>${esc(c.description)}</td>
              <td><span class="badge badge-neutral">${esc(c.category)}</span></td>
              <td class="mono">${esc(c.permission)}</td>
              <td>${c.handler_bound ? `<span class="badge badge-success">Runtime Handler</span>` : `<span class="badge badge-info" title="Menu / static text metadata only">Metadata / Alias</span>`}</td>
              <td><button class="btn btn-sm" data-toggle-cmd="${esc(c.command)}" data-en="${c.enabled ? "1" : "0"}">${c.enabled ? "Enabled" : "Disabled"}</button></td>
              <td><button class="btn btn-sm btn-danger" data-del-cmd="${esc(c.command)}">Delete</button></td>
            </tr>
          `).join("")}</tbody>
        </table></div>
      `;

      $("btn-save-cmd").onclick = async () => {
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/commands`, {
            method: "POST",
            body: {
              command: $("cmd-name").value,
              description: $("cmd-desc").value,
              category: $("cmd-cat").value,
              permission: $("cmd-perm").value,
              enabled: true,
            },
          });
          toast("Command saved", "success");
          navigate("commands", true);
        } catch (e) { toast(e.message, "error"); }
      };

      vp.querySelectorAll("[data-toggle-cmd]").forEach((b) => {
        b.onclick = async () => {
          const c = cmds.find((x) => x.command === b.dataset.toggleCmd);
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/commands`, {
            method: "POST", body: Object.assign({}, c, { enabled: b.dataset.en !== "1" }),
          });
          navigate("commands", true);
        };
      });

      vp.querySelectorAll("[data-del-cmd]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bots/${encodeURIComponent(S.botId)}/commands`, {
            method: "DELETE", body: { command: b.dataset.delCmd },
          });
          toast("Command deleted", "success");
          navigate("commands", true);
        };
      });
    },

    /* 7. Bot Clone Studio & Configuration Migration Center */
    async clone_studio(vp) {
      await refreshBotsAndSelectors();
      const histRes = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/clones`);
      const secs = S.catalogs.cloneable_sections || [];

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Bot Clone Studio & Migration Center</h1>
            <p class="page-subtitle">Preview metadata changes between bots in this workspace. Credentials are excluded by field name; review free-text content before copying. Runtime changes and complete rollback are not yet supported.</p>
          </div>
        </div>

        ${S.bots.length < 2 ? `<div class="callout callout-info">Create at least 2 bots in this workspace to compare, clone, or migrate configurations between them. <button class="btn btn-sm btn-primary" id="cln-add-bot" style="margin-left:8px">+ Create Second Bot</button></div>` : ""}

        <div class="card" style="margin-bottom:18px">
          <div class="grid grid-2">
            <div class="field">
              <label class="field-label">1. Source Bot</label>
              <select id="cln-src" class="select">${S.bots.map((b, i) => `<option value="${esc(b.bot_id)}" ${i === 0 ? "selected" : ""}>${esc(b.display_name)} (@${esc(b.username)})</option>`).join("")}</select>
            </div>
            <div class="field">
              <label class="field-label">2. Destination Bot</label>
              <select id="cln-tgt" class="select">${S.bots.map((b, i) => `<option value="${esc(b.bot_id)}" ${i === 1 ? "selected" : ""}>${esc(b.display_name)} (@${esc(b.username)})</option>`).join("")}</select>
            </div>
          </div>

          <div class="field">
            <label class="field-label">3. Select Configuration Sections to Copy</label>
            <div class="toolbar-group">
              ${secs.map((s) => `<label class="badge badge-neutral" style="padding:5px 10px;cursor:pointer"><input type="checkbox" data-cln-sec="${esc(s)}" checked> ${esc(s)}</label>`).join("")}
            </div>
          </div>

          <div class="toolbar-group">
            <button id="btn-cln-preview" class="btn">Preview Diff & Conflicts</button>
            <button id="btn-cln-exec" class="btn btn-primary" ${S.bots.length < 2 ? "disabled" : ""}>Execute Clone</button>
            <button id="btn-mig-exec" class="btn" ${S.bots.length < 2 ? "disabled" : ""}>Run as Tracked Migration</button>
          </div>
          <div id="cln-preview-out" style="margin-top:14px"></div>
        </div>

        <div class="card">
          <h3 class="card-title" style="margin-bottom:12px">Clone & Migration History</h3>
          <div class="table-wrap"><table class="data-table">
            <thead><tr><th>ID</th><th>Kind</th><th>Source → Target</th><th>Sections</th><th>Status</th><th>Rollback</th></tr></thead>
            <tbody>${(histRes.history || []).map((h) => `
              <tr>
                <td class="mono">${esc(h.clone_id)}</td>
                <td>${esc(h.kind)}</td>
                <td class="mono">${esc(h.source_bot_id)} → ${esc(h.target_bot_id)}</td>
                <td>${(h.sections || []).length} section(s)</td>
                <td>${h.rolled_back ? `<span class="badge badge-warning">Rolled Back</span>` : `<span class="badge badge-success">Completed</span>`}</td>
                <td>${!h.rolled_back && h.rollback_supported ? `<button class="btn btn-sm" data-rb="${esc(h.clone_id)}">Rollback</button>` : h.rolled_back ? "—" : "Unavailable"}</td>
              </tr>
            `).join("") || `<tr><td colspan="6" style="color:var(--text-muted)">No clone or migration runs yet.</td></tr>`}</tbody>
          </table></div>
        </div>
      `;

      if ($("cln-add-bot")) $("cln-add-bot").onclick = () => navigate("bot_create");
      const chosenSecs = () => Array.from(vp.querySelectorAll("[data-cln-sec]:checked")).map((c) => c.dataset.clnSec);

      $("btn-cln-preview").onclick = async () => {
        try {
          const p = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/clones`, {
            method: "POST",
            body: { action: "preview", source_bot_id: $("cln-src").value, target_bot_id: $("cln-tgt").value, sections: chosenSecs() },
          });
          $("cln-preview-out").innerHTML = `<div class="callout callout-info">
            <strong>Validation Passed:</strong> ${p.conflicts_count} overwrite conflict(s) detected. Review free-text for secrets and private data before copying.
            ${p.compatibility_warnings.length ? `<div style="margin-top:4px">${esc(p.compatibility_warnings.join(" "))}</div>` : ""}
            <pre class="mono" style="white-space:pre-wrap;overflow:auto">${esc(JSON.stringify(p.diffs, null, 2))}</pre>
          </div>`;
        } catch (e) { toast(e.message, "error"); }
      };

      const runClone = async (actionName) => {
        try {
          const selection = { source_bot_id: $("cln-src").value, target_bot_id: $("cln-tgt").value, sections: chosenSecs() };
          const preview = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/clones`, {
            method: "POST", body: { ...selection, action: "preview" },
          });
          const ok = await confirmModal("Confirm configuration copy",
            `Copy ${preview.selected_sections.length} section(s) from <strong>${esc(preview.source_bot.display_name)}</strong> to <strong>${esc(preview.target_bot.display_name)}</strong>? ${preview.conflicts_count} potential conflict(s). Review free-text for secrets and private data. Cross-collection changes are not atomic; rollback is unavailable for commands, File Store settings, plugins and automations.<pre class="mono" style="white-space:pre-wrap;max-height:220px;overflow:auto">${esc(JSON.stringify(preview.diffs, null, 2))}</pre>`, "Copy configuration");
          if (!ok) return;
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/clones`, {
            method: "POST", body: { ...selection, action: actionName, confirmed: true },
          });
          toast("Configuration metadata copied", "success");
          navigate("clone_studio", true);
        } catch (e) { toast(e.message, "error"); }
      };

      $("btn-cln-exec").onclick = () => runClone("execute");
      $("btn-mig-exec").onclick = () => runClone("migrate");

      vp.querySelectorAll("[data-rb]").forEach((b) => {
        b.onclick = async () => {
          try {
            if (!await confirmModal("Confirm rollback", "Restore the selected bot-record fields if they have not changed since this clone?", "Rollback")) return;
            await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/clones`, {
              method: "POST", body: { action: "rollback", clone_id: b.dataset.rb, confirmed: true },
            });
            toast("Selected bot settings restored", "success");
            navigate("clone_studio", true);
          } catch (e) { toast(e.message, "error"); }
        };
      });
    },

    /* 8. File Store Studio */
    async filestore(vp) {
      const [filesRes, fsCfg] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/filestore?include_deleted=1`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/filestore/config?bot_id=${encodeURIComponent(S.botId || "default")}`),
      ]);
      const items = filesRes.items || [];
      const activeItems = items.filter((f) => !f.deleted);
      const trashItems = items.filter((f) => f.deleted);

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">File Store Studio</h1>
            <p class="page-subtitle">File vault with table/grid views, metadata, trash recovery, and storage policies. Web share links are not yet supported.</p>
          </div>
          <div class="toolbar-group">
            <label class="btn btn-primary" style="cursor:pointer">
              ⬆ Upload File
              <input id="fs-upload-input" type="file" hidden multiple>
            </label>
            <button id="fs-toggle-view" class="btn">${S.fsViewMode === "table" ? "Grid View" : "Table View"}</button>
          </div>
        </div>

        <div class="grid grid-4" style="margin-bottom:18px">
          <div class="stat-card"><div class="stat-label">Active Stored Files</div><div class="stat-value">${activeItems.length}</div></div>
          <div class="stat-card"><div class="stat-label">Total Storage</div><div class="stat-value">${fmtBytes(filesRes.total_bytes || 0)}</div></div>
          <div class="stat-card"><div class="stat-label">Trash / Recoverable</div><div class="stat-value">${trashItems.length}</div></div>
          <div class="stat-card"><div class="stat-label">Duplicate Policy</div><div class="stat-value" style="font-size:18px">${esc(fsCfg.duplicate_policy)}</div><div class="stat-meta">Max ${fsCfg.max_file_mb} MiB/file</div></div>
        </div>

        <div class="card" style="margin-bottom:18px">
          <div class="toolbar">
            <input id="fs-search" class="input" style="width:260px" placeholder="Filter by filename, file ID, tag…">
            <button id="btn-export-meta" class="btn btn-sm">Export Metadata JSON</button>
          </div>
          <div id="fs-items-box"></div>
        </div>

        <div class="card">
          <h3 class="card-title" style="margin-bottom:10px">File Store Policy Configuration</h3>
          <div class="grid grid-3">
            <div class="field"><label class="field-label">Max File Size (MiB)</label><input id="fsc-max" class="input" type="number" value="${fsCfg.max_file_mb}"></div>
            <div class="field"><label class="field-label">Duplicate Handling</label>
              <select id="fsc-dup" class="select">
                ${["warn", "allow", "block"].map((m) => `<option value="${m}" ${fsCfg.duplicate_policy === m ? "selected" : ""}>${m}</option>`).join("")}
              </select>
            </div>
            <div class="field"><label class="field-label">Caption Template</label><input id="fsc-cap" class="input" value="${esc(fsCfg.caption_template)}"></div>
          </div>
          <button id="btn-save-fsc" class="btn btn-primary">Save File Store Policy</button>
        </div>
      `;

      const renderFiles = () => {
        const q = ($("fs-search").value || "").toLowerCase();
        const rows = items.filter((f) => !q || `${f.filename} ${f.file_uid} ${(f.tags || []).join(" ")}`.toLowerCase().includes(q));
        const box = $("fs-items-box");
        if (!rows.length) {
          box.innerHTML = `<div class="empty-state"><h4>No files in this File Store yet</h4><p>Upload files above. Downloads are managed separately.</p></div>`;
          return;
        }
        if (S.fsViewMode === "grid") {
          box.innerHTML = `<div class="grid grid-3">${rows.map((f) => `
            <div class="card">
              <div style="font-weight:600">${esc(f.filename)}</div>
              <div class="stat-meta">${fmtBytes(f.size_bytes)} · ${esc(f.category)} · ${f.deleted ? "TRASH" : "Active"}</div>
              <div class="toolbar-group" style="margin-top:10px">
                ${!f.deleted ? `<button class="btn btn-sm" data-fs-act="rename" data-id="${esc(f.file_uid)}">Rename</button>
                <button class="btn btn-sm btn-danger" data-fs-act="soft_delete" data-id="${esc(f.file_uid)}">Trash</button>`
                : `<button class="btn btn-sm btn-primary" data-fs-act="restore" data-id="${esc(f.file_uid)}">Restore</button>`}
              </div>
            </div>
          `).join("")}</div>`;
        } else {
          box.innerHTML = `<div class="table-wrap"><table class="data-table">
            <thead><tr><th>Filename</th><th>Size</th><th>Type</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>${rows.map((f) => `
              <tr>
                <td><strong>${esc(f.filename)}</strong><div class="mono" style="color:var(--text-muted)">${esc(f.file_uid)}</div></td>
                <td>${fmtBytes(f.size_bytes)}</td>
                <td><span class="badge badge-neutral">${esc(f.category)}</span></td>
                <td>${f.deleted ? `<span class="badge badge-warning">In Trash</span>` : `<span class="badge badge-success">Stored</span>`}</td>
                <td><div class="toolbar-group">
                  ${!f.deleted ? `
                    <button class="btn btn-sm" data-fs-act="rename" data-id="${esc(f.file_uid)}">Rename</button>
                    <button class="btn btn-sm btn-danger" data-fs-act="soft_delete" data-id="${esc(f.file_uid)}">Delete</button>
                  ` : `
                    <button class="btn btn-sm btn-primary" data-fs-act="restore" data-id="${esc(f.file_uid)}">Restore</button>
                    <button class="btn btn-sm btn-danger" data-fs-act="purge" data-id="${esc(f.file_uid)}">Purge</button>
                  `}
                </div></td>
              </tr>
            `).join("")}</tbody>
          </table></div>`;
        }

        box.querySelectorAll("[data-fs-act]").forEach((b) => {
          b.onclick = async () => {
            const act = b.dataset.fsAct;
            const id = b.dataset.id;
            const payload = { action: act };
            if (act === "rename") {
              const nn = prompt("New filename:");
              if (!nn) return;
              payload.new_name = nn;
            }
            if (act === "soft_delete" || act === "purge") {
              const ok = await confirmModal("Confirm File Deletion", act === "purge" ? "Permanently delete this file from storage?" : "Move this file to recoverable Trash?", "Confirm");
              if (!ok) return;
              payload.confirmed = true;
            }
            await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/filestore/${encodeURIComponent(id)}`, { method: "POST", body: payload });
            toast(`File action '${act}' completed`, "success");
            navigate("filestore", true);
          };
        });
      };

      $("fs-upload-input").onchange = async (ev) => {
        const files = ev.target.files;
        if (!files || !files.length) return;
        const fd = new FormData();
        for (const f of files) fd.append("file", f, f.name);
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/filestore?bot_id=${encodeURIComponent(S.botId || "default")}`, { method: "POST", body: fd });
          toast("Uploaded " + files.length + " file(s)", "success");
          navigate("filestore", true);
        } catch (e) { toast(e.message, "error"); }
      };

      $("fs-toggle-view").onclick = () => { S.fsViewMode = S.fsViewMode === "table" ? "grid" : "table"; navigate("filestore", true); };
      $("fs-search").oninput = renderFiles;
      $("btn-export-meta").onclick = () => {
        const blob = new Blob([JSON.stringify(activeItems, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "filestore_metadata.json";
        a.click();
      };
      $("btn-save-fsc").onclick = async () => {
        await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/filestore/config`, {
          method: "POST",
          body: {
            bot_id: S.botId || "default",
            max_file_mb: Number($("fsc-max").value || 2000),
            duplicate_policy: $("fsc-dup").value,
            caption_template: $("fsc-cap").value,
          },
        });
        toast("File Store policy updated", "success");
      };
      renderFiles();
    },

    /* 9. File Intelligence Center */
    async file_intel(vp) {
      const intel = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/file-intelligence`);
      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">File Intelligence Center</h1>
            <p class="page-subtitle">${esc(intel.auto_cleanup_note)} ${intel.sample_truncated ? "Results use a sample of 5,000 active records." : ""}</p>
          </div>
        </div>

        <div class="grid grid-4" style="margin-bottom:18px">
          <div class="stat-card"><div class="stat-label">Analyzed Files</div><div class="stat-value">${intel.total_files}</div></div>
          <div class="stat-card"><div class="stat-label">Estimated Duplicate Space</div><div class="stat-value">${fmtBytes(intel.duplicate_reclaimable_bytes)}</div><div class="stat-meta">${intel.duplicate_candidates.length} duplicate group(s)</div></div>
          <div class="stat-card"><div class="stat-label">Missing Tags / Desc</div><div class="stat-value">${intel.missing_metadata_count}</div></div>
          <div class="stat-card"><div class="stat-label">Observed 30d Projection</div><div class="stat-value">${fmtBytes(intel.projected_30d_growth_bytes)}</div><div class="stat-meta">Based on ${fmtBytes(intel.bytes_added_7d)} in last 7d</div></div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Largest Stored Files</h3>
            ${(intel.largest_files || []).map((f) => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)"><span>${esc(f.filename)}</span><strong class="mono">${fmtBytes(f.size_bytes)}</strong></div>`).join("") || `<div class="stat-meta">No files indexed yet.</div>`}
          </div>
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Duplicate Candidates (Advisory Only)</h3>
            ${(intel.duplicate_candidates || []).map((g) => `<div style="padding:6px 0;border-bottom:1px solid var(--border)"><strong>${g.count} candidates (${esc(g.match_basis)})</strong> · Estimated: ${fmtBytes(g.reclaimable_bytes)}</div>`).join("") || `<div class="stat-meta">Zero duplicate candidates detected.</div>`}
          </div>
        </div>
      `;
    },

    /* 10. Downloads & Media Processing Studio */
    async downloads(vp) {
      const [dl, studio] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/downloads`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/studio`),
      ]);
      const files = studio.workspace_files || [];
      const ops = Object.entries(studio.catalog || {}).flatMap(([cat, list]) => list.map((o) => ({ cat, ...o })));

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Advanced Downloads & Media Processing Studio</h1>
            <p class="page-subtitle">Media files are isolated per user and workspace. The download queue is still account-scoped, not workspace-isolated; avoid using it for separate tenants until migration is complete.</p>
          </div>
        </div>

        <div class="grid grid-2" style="margin-bottom:18px">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Enqueue Lawful Remote Source</h3>
            <div class="field">
              <label class="field-label">Direct HTTP(S), GitHub Release, RSS/Atom Feed, Google Drive, Dropbox, OneDrive, or Public Mega URL</label>
              <input id="dl-url" class="input" placeholder="https://github.com/owner/repo/releases/latest">
            </div>
            <div class="grid grid-2">
              <div class="field"><label class="field-label">Destination</label>
                <select id="dl-dest" class="select">${(S.catalogs.destinations || []).map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")}</select>
              </div>
              <div class="field"><label class="field-label">Priority</label>
                <select id="dl-prio" class="select"><option value="normal">Normal</option><option value="high">High</option><option value="low">Low</option></select>
              </div>
            </div>
            <div class="toolbar-group">
              <button id="btn-dl-inspect" class="btn">Inspect Multi-File Source</button>
              <button id="btn-dl-start" class="btn btn-primary">Download Now</button>
            </div>
            <div id="dl-selection-box" style="margin-top:12px"></div>
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Media, PDF & Archive Studio</h3>
            <p class="stat-meta">Private media storage for this user and workspace. Legacy downloads and File Store uploads are separate and are not automatically imported.</p>
            <div class="field"><label class="field-label">Upload to Media Studio (up to 512 MiB)</label><input id="st-upload" class="input" type="file"></div>
            <div class="field"><label class="field-label">Media Studio Input File</label>
              <select id="st-file" class="select">${files.length ? files.map((f) => `<option value="${esc(f.path)}">${esc(f.name)} (${fmtBytes(f.size_bytes)})</option>`).join("") : `<option value="">Upload a file to Media Studio first</option>`}</select>
            </div>
            <div class="field"><label class="field-label">Operation</label>
              <select id="st-op" class="select">${ops.map((o) => `<option value="${esc(o.op)}">[${esc(o.cat.toUpperCase())}] ${esc(o.label)}</option>`).join("")}</select>
            </div>
            <div class="field"><label class="field-label">Target Format / Parameter (optional)</label>
              <input id="st-fmt" class="input" placeholder="e.g. webp, mp3, mp4, vtt">
            </div>
            <button id="btn-st-run" class="btn btn-primary" ${!files.length ? "disabled" : ""}>Run Studio Tool</button>
            <div id="st-result" style="margin-top:10px"></div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <h3 class="card-title">Transfer Queue (${(dl.tasks || []).length})</h3>
            <button id="btn-dl-clear" class="btn btn-sm">Clear Completed</button>
          </div>
          <div class="table-wrap"><table class="data-table">
            <thead><tr><th>File</th><th>Provider</th><th>Progress</th><th>Status</th><th>Controls</th></tr></thead>
            <tbody>${(dl.tasks || []).map((t) => `
              <tr>
                <td><strong>${esc(t.filename)}</strong><div class="mono" style="color:var(--text-muted)">${esc(t.sha256 ? t.sha256.slice(0, 16) + "…" : "")}</div></td>
                <td><span class="badge badge-neutral">${esc(t.provider)}</span></td>
                <td style="min-width:140px"><div class="progress-track"><div class="progress-fill" style="width:${t.percent || 0}%"></div></div><div class="stat-meta">${t.percent || 0}% · ${fmtBytes(t.done_bytes)}</div></td>
                <td>${esc(t.stage || t.status)}</td>
                <td><div class="toolbar-group">
                  <button class="btn btn-sm" data-tk-act="pause" data-id="${esc(t.task_id)}">Pause</button>
                  <button class="btn btn-sm" data-tk-act="resume" data-id="${esc(t.task_id)}">Resume</button>
                  <button class="btn btn-sm" data-tk-act="retry" data-id="${esc(t.task_id)}">Retry</button>
                  <button class="btn btn-sm btn-danger" data-tk-act="cancel" data-id="${esc(t.task_id)}">Cancel</button>
                </div></td>
              </tr>
            `).join("") || `<tr><td colspan="5" style="color:var(--text-muted)">Queue is idle.</td></tr>`}</tbody>
          </table></div>
        </div>
      `;

      $("btn-dl-inspect").onclick = async () => {
        try {
          const r = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/downloads`, {
            method: "POST", body: { action: "inspect", url: $("dl-url").value, destination: $("dl-dest").value },
          });
          const sel = r.selection;
          $("dl-selection-box").innerHTML = `<div class="callout">
            <strong>${esc(sel.title)}</strong> (${sel.selected_count}/${sel.total_files} selected · ${fmtBytes(sel.selected_bytes)})
            <div style="max-height:140px;overflow:auto;margin:8px 0">${sel.items.map((it) => `<label style="display:block"><input type="checkbox" data-sel-item="${esc(it.item_id)}" ${it.selected ? "checked" : ""}> ${esc(it.filename)} (${fmtBytes(it.size_bytes)})</label>`).join("")}</div>
            <button id="btn-sel-confirm" class="btn btn-sm btn-primary">Confirm Selected Downloads</button>
          </div>`;
          $("btn-sel-confirm").onclick = async () => {
            const ids = Array.from(vp.querySelectorAll("[data-sel-item]:checked")).map((c) => c.dataset.selItem);
            await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/downloads`, {
              method: "POST", body: { action: "selection_update", session_id: sel.session_id, selected_ids: ids },
            });
            await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/downloads`, {
              method: "POST", body: { action: "selection_confirm", session_id: sel.session_id, priority: $("dl-prio").value },
            });
            toast("Selected files enqueued", "success");
            navigate("downloads", true);
          };
        } catch (e) { toast(e.message, "error"); }
      };

      $("btn-dl-start").onclick = async () => {
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/downloads`, {
            method: "POST",
            body: { action: "enqueue", url: $("dl-url").value, destination: $("dl-dest").value, priority: $("dl-prio").value, auto_select_all: true },
          });
          toast("Download added to queue", "success");
          navigate("downloads", true);
        } catch (e) { toast(e.message, "error"); }
      };

      $("st-upload").onchange = async (event) => {
        const file = event.target.files[0];
        if (!file) return;
        try {
          const form = new FormData();
          form.append("file", file);
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/studio`, { method: "POST", body: form });
          toast("Media Studio file uploaded", "success");
          navigate("downloads", true);
        } catch (e) { toast(e.message, "error"); }
      };

      $("btn-st-run").onclick = async () => {
        try {
          const res = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/studio`, {
            method: "POST",
            body: { op: $("st-op").value, file: $("st-file").value, files: [$("st-file").value], format: $("st-fmt").value || undefined },
          });
          $("st-result").innerHTML = `<pre class="mono" style="background:var(--bg-subtle);padding:10px;border-radius:8px;overflow:auto">${esc(JSON.stringify(res, null, 2))}</pre>`;
          toast("Media Studio operation succeeded", "success");
        } catch (e) { toast(e.message, "error"); }
      };

      $("btn-dl-clear").onclick = async () => {
        await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/downloads`, { method: "POST", body: { action: "clear_finished" } });
        navigate("downloads", true);
      };

      vp.querySelectorAll("[data-tk-act]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/downloads`, {
            method: "POST", body: { action: b.dataset.tkAct, task_id: b.dataset.id },
          });
          navigate("downloads", true);
        };
      });
    },

    /* 11. Bot & File Store Owner Analytics Studio */
    async analytics(vp) {
      const days = Number(sessionStorage.getItem("videl_anl_days") || 30);
      const data = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/analytics?days=${days}&bot_id=${encodeURIComponent(S.botId || "all")}`);
      const t = data.totals || {};
      const cmp = data.comparison_previous_period || {};

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Bot & File Store Owner Analytics</h1>
            <p class="page-subtitle">Active indexed File Store records (${days}-day window); deleted files and legacy transfers are excluded. ${data.file_sample_truncated ? "Storage and period values use a sample of 5,000 records." : ""}</p>
          </div>
          <div class="toolbar-group">
            ${[1, 7, 30, 90].map((d) => `<button class="btn btn-sm ${d === days ? "btn-primary" : ""}" data-days="${d}">${d === 1 ? "Today" : d + "d"}</button>`).join("")}
            <button id="btn-anl-csv" class="btn btn-sm">Export CSV</button>
          </div>
        </div>

        <div class="grid grid-4" style="margin-bottom:18px">
          <div class="stat-card"><div class="stat-label">Stored Files</div><div class="stat-value">${t.stored_files}</div><div class="stat-meta">Δ vs prev period: ${cmp.files_delta >= 0 ? "+" : ""}${cmp.files_delta}</div></div>
          <div class="stat-card"><div class="stat-label">Storage Consumed</div><div class="stat-value">${fmtBytes(t.storage_bytes)}</div></div>
          <div class="stat-card"><div class="stat-label">Transfer History</div><div class="stat-value" style="font-size:16px;color:var(--text-muted)">Unavailable</div><div class="stat-meta">Legacy transfers lack workspace attribution</div></div>
          <div class="stat-card"><div class="stat-label">Passive TG Impressions</div><div class="stat-value" style="font-size:16px;color:var(--text-muted)">Unavailable</div><div class="stat-meta">Not exposed by Bot API</div></div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:8px">Daily Added Active Files</h3>
            ${renderSvgBarChart(data.series.days, data.series.uploads, "#3b82f6")}
          </div>
          <div class="card">
            <h3 class="card-title" style="margin-bottom:8px">Daily Download Activity</h3>
            <div class="stat-meta">Unavailable: download history is not attributed to a workspace.</div>
          </div>
        </div>
      `;

      vp.querySelectorAll("[data-days]").forEach((b) => {
        b.onclick = () => {
          sessionStorage.setItem("videl_anl_days", b.dataset.days);
          navigate("analytics", true);
        };
      });

      $("btn-anl-csv").onclick = async () => {
        const csv = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/analytics.csv?days=${days}`);
        const blob = new Blob([csv], { type: "text/csv" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `videl_analytics_${days}d.csv`;
        a.click();
      };
    },

    /* 12. Automation Studio & Scheduled Tasks */
    async automation(vp) {
      const [autoRes, schRes] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/automations`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/schedules`),
      ]);
      const triggers = S.catalogs.automation_triggers || [];
      const actions = S.catalogs.automation_actions || [];

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Automation Studio & Scheduled Task Center</h1>
            <p class="page-subtitle">Manual runs are available for supported actions. File insights and reports require their corresponding permissions. Automatic triggers, durable idempotency and workspace-isolated schedules are not yet complete; legacy retry and template-application actions cannot run.</p>
          </div>
        </div>

        <div class="grid grid-2" style="margin-bottom:18px">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Build Automation Workflow</h3>
            <div class="field"><label class="field-label">Workflow Name</label><input id="wf-name" class="input" placeholder="Notify on completed download"></div>
            <div class="grid grid-2">
              <div class="field"><label class="field-label">Trigger</label><select id="wf-trig" class="select">${triggers.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("")}</select></div>
              <div class="field"><label class="field-label">Action</label><select id="wf-act" class="select">${actions.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("")}</select></div>
            </div>
            <button id="btn-wf-save" class="btn btn-primary">Save Workflow</button>
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Create Scheduled Task</h3>
            <div class="field"><label class="field-label">Task Label</label><input id="sch-title" class="input" placeholder="Nightly Dataset Mirror"></div>
            <div class="field"><label class="field-label">Target URL</label><input id="sch-url" class="input" placeholder="https://example.com/archive.zip"></div>
            <div class="grid grid-2">
              <div class="field"><label class="field-label">Start Delay (minutes)</label><input id="sch-delay" class="input" type="number" value="60"></div>
              <div class="field"><label class="field-label">Repeat Interval (min, 0=once)</label><input id="sch-iv" class="input" type="number" value="0"></div>
            </div>
            <button id="btn-sch-save" class="btn btn-primary">Schedule Job</button>
          </div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Workflows (${(autoRes.workflows || []).length})</h3>
            ${(autoRes.workflows || []).map((w) => `
              <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
                <div><strong>${esc(w.name)}</strong><div class="stat-meta"><code>${esc(w.trigger)}</code> → <code>${esc((w.actions[0] || {}).type)}</code> · Runs: ${w.run_count || 0}</div></div>
                <div class="toolbar-group">
                  <button class="btn btn-sm" data-wf-run="${esc(w.workflow_id)}">Test Run</button>
                  <button class="btn btn-sm btn-danger" data-wf-del="${esc(w.workflow_id)}">Delete</button>
                </div>
              </div>
            `).join("") || `<div class="stat-meta">No workflows yet.</div>`}
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Scheduled Tasks (${(schRes.schedules || []).length})</h3>
            ${(schRes.schedules || []).map((j) => `
              <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)">
                <div><strong>${esc(j.title)}</strong><div class="stat-meta">${esc(j.last_status)} · ${j.enabled ? "Active" : "Paused"}</div></div>
                <div class="toolbar-group">
                  <button class="btn btn-sm" data-sch-tog="${esc(j.job_id)}" data-en="${j.enabled ? "1" : "0"}">${j.enabled ? "Pause" : "Resume"}</button>
                  <button class="btn btn-sm btn-danger" data-sch-del="${esc(j.job_id)}">Delete</button>
                </div>
              </div>
            `).join("") || `<div class="stat-meta">No scheduled tasks yet.</div>`}
          </div>
        </div>
      `;

      $("btn-wf-save").onclick = async () => {
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/automations`, {
            method: "POST",
            body: { name: $("wf-name").value, trigger: $("wf-trig").value, conditions: [], actions: [{ type: $("wf-act").value }] },
          });
          toast("Automation workflow created", "success");
          navigate("automation", true);
        } catch (e) { toast(e.message, "error"); }
      };

      vp.querySelectorAll("[data-wf-run]").forEach((b) => {
        b.onclick = async () => {
          const r = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/automations`, {
            method: "POST", body: { action: "run", workflow_id: b.dataset.wfRun, event_data: { size_mb: 25 } },
          });
          toast(`Workflow run status: ${r.run.status}`, "success");
          navigate("automation", true);
        };
      });

      vp.querySelectorAll("[data-wf-del]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/automations`, { method: "POST", body: { action: "delete", workflow_id: b.dataset.wfDel } });
          navigate("automation", true);
        };
      });

      $("btn-sch-save").onclick = async () => {
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/schedules`, {
            method: "POST",
            body: { title: $("sch-title").value, target: $("sch-url").value, delay_minutes: Number($("sch-delay").value || 60), interval_minutes: Number($("sch-iv").value || 0) },
          });
          toast("Job scheduled", "success");
          navigate("automation", true);
        } catch (e) { toast(e.message, "error"); }
      };

      vp.querySelectorAll("[data-sch-tog]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/schedules`, {
            method: "POST", body: { action: "toggle", job_id: b.dataset.schTog, enabled: b.dataset.en !== "1" },
          });
          navigate("automation", true);
        };
      });

      vp.querySelectorAll("[data-sch-del]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/schedules`, { method: "POST", body: { action: "delete", job_id: b.dataset.schDel } });
          navigate("automation", true);
        };
      });
    },

    /* 13. Template Library & Marketplace */
    async templates(vp) {
      const tpls = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/templates`);
      const cats = S.catalogs.template_categories || [];
      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Template Studio & Marketplace</h1>
            <p class="page-subtitle">Declarative JSON templates for bot presets, File Store rules, welcome copy, and dashboard layouts (never executes untrusted code).</p>
          </div>
        </div>

        <div class="card" style="margin-bottom:18px">
          <h3 class="card-title" style="margin-bottom:10px">Create Custom Template</h3>
          <div class="grid grid-3">
            <div class="field"><label class="field-label">Template Name</label><input id="tpl-name" class="input" placeholder="Standard Support Bot"></div>
            <div class="field"><label class="field-label">Category</label><select id="tpl-cat" class="select">${cats.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}</select></div>
            <div class="field"><label class="field-label">Description</label><input id="tpl-desc" class="input" placeholder="Reusable team configuration"></div>
          </div>
          <div class="field"><label class="field-label">Declarative JSON Payload</label><textarea id="tpl-json" class="textarea mono">{"welcome_message": "Hello from Videl Studio!"}</textarea></div>
          <button id="btn-tpl-save" class="btn btn-primary">Save Template</button>
        </div>

        <div class="grid grid-2">
          ${[...(tpls.official || []), ...(tpls.workspace_templates || [])].map((t) => `
            <div class="card">
              <div class="card-header">
                <strong>${esc(t.name)}</strong>
                <span class="badge ${t.official ? "badge-info" : "badge-neutral"}">${t.official ? "Official" : "Workspace"} · ${esc(t.category)}</span>
              </div>
              <p style="color:var(--text-secondary);font-size:13px">${esc(t.description)}</p>
              <pre class="mono" style="background:var(--bg-subtle);padding:8px;border-radius:6px;overflow:auto;max-height:95px">${esc(JSON.stringify(t.payload, null, 2))}</pre>
            </div>
          `).join("")}
        </div>
      `;

      $("btn-tpl-save").onclick = async () => {
        try {
          const parsed = JSON.parse($("tpl-json").value);
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/templates`, {
            method: "POST", body: { name: $("tpl-name").value, category: $("tpl-cat").value, description: $("tpl-desc").value, payload: parsed },
          });
          toast("Template saved", "success");
          navigate("templates", true);
        } catch (e) { toast(e.message, "error"); }
      };
    },

    /* 14. Integrations & Plugin Center */
    async integrations(vp) {
      const integ = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/integrations`);
      const conns = integ.connections || {};
      const providersList = ["dropbox", "onedrive", "gdrive", "rclone", "gofile"];

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Cloud Integrations & Storage Vault</h1>
            <p class="page-subtitle">Per-user cloud credentials are sealed at rest with Fernet encryption and never sent back to the browser.</p>
          </div>
        </div>

        <div class="grid grid-2" style="margin-bottom:18px">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Connect Cloud Provider</h3>
            <div class="field"><label class="field-label">Provider</label>
              <select id="int-prov" class="select">${providersList.map((p) => `<option value="${p}">${p.toUpperCase()}</option>`).join("")}</select>
            </div>
            <div class="field"><label class="field-label">OAuth / API Access Token</label><input id="int-tok" class="input mono" type="password" placeholder="sl.B... or Graph Bearer token"></div>
            <div class="field"><label class="field-label">Default Root Folder</label><input id="int-folder" class="input" value="/VidelUploads"></div>
            <button id="btn-int-save" class="btn btn-primary">Save Encrypted Connection</button>
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Connected Storage Accounts</h3>
            ${providersList.map((p) => {
              const c = conns[p];
              return `<div style="display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid var(--border)">
                <div><strong>${p.toUpperCase()}</strong><div class="stat-meta">${c && c.authenticated ? `Connected · Folder: ${esc(c.root_folder || "/")}` : "Not connected"}</div></div>
                ${c && c.authenticated ? `<button class="btn btn-sm btn-danger" data-int-disc="${p}">Disconnect</button>` : `<span class="badge badge-neutral">Optional</span>`}
              </div>`;
            }).join("")}
          </div>
        </div>
      `;

      $("btn-int-save").onclick = async () => {
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/integrations`, {
            method: "POST", body: { provider: $("int-prov").value, access_token: $("int-tok").value, root_folder: $("int-folder").value },
          });
          toast("Cloud connection saved securely", "success");
          navigate("integrations", true);
        } catch (e) { toast(e.message, "error"); }
      };

      vp.querySelectorAll("[data-int-disc]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/integrations`, { method: "POST", body: { action: "disconnect", provider: b.dataset.intDisc } });
          toast("Provider disconnected", "success");
          navigate("integrations", true);
        };
      });
    },

    /* 15. Developer Center & Export/Import Backup */
    async developer(vp) {
      const [keysRes, bundleRes] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/api-keys`),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bundle`),
      ]);
      const scopes = S.catalogs.permission_scopes || [];

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Developer Center & Configuration Backup</h1>
            <p class="page-subtitle">Scoped SHA-256 hashed API keys (shown once on creation), REST API reference, and secret-free workspace export/import.</p>
          </div>
        </div>

        <div class="grid grid-2" style="margin-bottom:18px">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Generate Scoped API Key</h3>
            <div class="field"><label class="field-label">Key Label</label><input id="dev-kname" class="input" placeholder="CI Automation Runner"></div>
            <div class="field"><label class="field-label">Permitted Scopes</label>
              <div class="toolbar-group">${scopes.map((s) => `<label class="badge badge-neutral" style="cursor:pointer"><input type="checkbox" data-kscope="${esc(s)}" ${s === "analytics" ? "checked" : ""}> ${esc(s)}</label>`).join("")}</div>
            </div>
            <button id="btn-dev-kcreate" class="btn btn-primary">Mint Scoped Key</button>
            <div id="dev-key-once" style="margin-top:12px"></div>
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Export / Import Workspace Bundle</h3>
            <p class="field-hint">Format: <code>videl-saas-bundle/1</code> · SHA-256: <code>${esc((bundleRes.bundle.checksum_sha256 || "").slice(0, 18))}…</code> · Zero tokens included.</p>
            <div class="toolbar-group" style="margin-bottom:10px">
              <button id="btn-export-bundle" class="btn btn-primary">Download Backup JSON</button>
            </div>
            <div class="field"><label class="field-label">Import Bundle JSON</label><textarea id="imp-json" class="textarea mono" placeholder='{"format":"videl-saas-bundle/1", ...}'></textarea></div>
            <button id="btn-import-bundle" class="btn">Preview & Import</button>
          </div>
        </div>

        <div class="card">
          <h3 class="card-title" style="margin-bottom:10px">Issued Workspace API Keys</h3>
          <div class="table-wrap"><table class="data-table">
            <thead><tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Uses</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>${(keysRes.keys || []).map((k) => `
              <tr>
                <td><strong>${esc(k.name)}</strong></td>
                <td class="mono">${esc(k.key_prefix)}</td>
                <td>${(k.scopes || []).join(", ")}</td>
                <td>${k.usage_count || 0}</td>
                <td>${k.revoked ? `<span class="badge badge-danger">Revoked</span>` : `<span class="badge badge-success">Active</span>`}</td>
                <td>${!k.revoked ? `<div class="toolbar-group">
                  <button class="btn btn-sm" data-krot="${esc(k.key_id)}">Rotate</button>
                  <button class="btn btn-sm btn-danger" data-krev="${esc(k.key_id)}">Revoke</button>
                </div>` : "—"}</td>
              </tr>
            `).join("") || `<tr><td colspan="6" style="color:var(--text-muted)">No API keys minted yet.</td></tr>`}</tbody>
          </table></div>
        </div>
      `;

      $("btn-dev-kcreate").onclick = async () => {
        try {
          const sc = Array.from(vp.querySelectorAll("[data-kscope]:checked")).map((c) => c.dataset.kscope);
          const created = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/api-keys`, {
            method: "POST", body: { name: $("dev-kname").value, scopes: sc },
          });
          $("dev-key-once").innerHTML = `<div class="callout callout-warn">
            <strong>Copy now (shown once):</strong><br><code style="word-break:break-all">${esc(created.raw_key_once)}</code>
          </div>`;
        } catch (e) { toast(e.message, "error"); }
      };

      vp.querySelectorAll("[data-krev]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/api-keys`, { method: "POST", body: { action: "revoke", key_id: b.dataset.krev } });
          navigate("developer", true);
        };
      });

      vp.querySelectorAll("[data-krot]").forEach((b) => {
        b.onclick = async () => {
          const rot = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/api-keys`, { method: "POST", body: { action: "rotate", key_id: b.dataset.krot } });
          await confirmModal("Rotated API Key (Copy Now)", `<code style="word-break:break-all">${esc(rot.raw_key_once)}</code>`, "Done");
          navigate("developer", true);
        };
      });

      $("btn-export-bundle").onclick = () => {
        const blob = new Blob([JSON.stringify(bundleRes.bundle, null, 2)], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `videl_workspace_${S.wsId}.json`;
        a.click();
      };

      $("btn-import-bundle").onclick = async () => {
        try {
          const parsed = JSON.parse($("imp-json").value);
          const prev = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bundle`, { method: "POST", body: { bundle: parsed, confirmed: false } });
          const ok = await confirmModal("Confirm Bundle Import", `Import ${prev.templates_count} template(s) and ${prev.automations_count} workflow(s)?`, "Import");
          if (!ok) return;
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/bundle`, { method: "POST", body: { bundle: parsed, confirmed: true } });
          toast("Workspace bundle imported", "success");
          navigate("developer", true);
        } catch (e) { toast(e.message, "error"); }
      };
    },

    /* 16. Team Management, 11-Category Settings, Notifications & Help */
    async team(vp) {
      const [teamRes, setRes, ntfRes] = await Promise.all([
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/team`),
        api("/api/v1/settings"),
        api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/notifications`),
      ]);
      const roles = (S.catalogs.workspace_roles || []).filter((r) => r !== "owner");
      const settingsObj = setRes.settings || {};

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Team RBAC, Platform Settings & Activity Center</h1>
            <p class="page-subtitle">Role-based access control with anti-escalation protection, 11 validated preference categories, audit history, and documentation.</p>
          </div>
        </div>

        <div class="grid grid-2" style="margin-bottom:18px">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Workspace Team Members</h3>
            <div style="display:flex;gap:8px;margin-bottom:12px">
              <input id="tm-uid" class="input" type="number" placeholder="Telegram User ID">
              <select id="tm-role" class="select">${roles.map((r) => `<option value="${esc(r)}">${esc(r)}</option>`).join("")}</select>
              <button id="btn-tm-add" class="btn btn-primary">Invite</button>
            </div>
            ${(teamRes.members || []).map((m) => `
              <div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)">
                <span>User <code>${m.user_id}</code> · <span class="badge badge-info">${esc(m.role)}</span></span>
                ${m.role !== "owner" ? `<button class="btn btn-sm btn-danger" data-tm-rem="${m.user_id}">Revoke</button>` : ""}
              </div>
            `).join("")}
          </div>

          <div class="card">
            <div class="card-header">
              <h3 class="card-title">Notifications & Audit Trail</h3>
              <button id="btn-ntf-read" class="btn btn-sm">Mark All Read</button>
            </div>
            <div style="max-height:220px;overflow:auto">
              ${(ntfRes.activity_logs || []).slice(0, 12).map((a) => `
                <div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:12px">
                  <code>${esc(a.action)}</code> · ${esc(a.target || "—")} · <span style="color:var(--text-muted)">${esc(a.created_at)}</span>
                </div>
              `).join("") || `<div class="stat-meta">${esc(ntfRes.activity_logs_availability || "No audit entries yet.")}</div>`}
            </div>
          </div>
        </div>

        <div class="card" style="margin-bottom:18px">
          <div class="card-header">
            <h3 class="card-title">11-Category Account & Studio Settings</h3>
            <button id="btn-reset-settings" class="btn btn-sm">Reset All to Defaults</button>
          </div>
          <div class="grid grid-3">
            ${Object.entries(settingsObj).map(([sec, vals]) => `
              <div class="card" style="padding:12px">
                <strong style="text-transform:capitalize">${esc(sec)}</strong>
                <div class="stat-meta">${Object.entries(vals).map(([k, v]) => `${esc(k)}: <code>${esc(JSON.stringify(v))}</code>`).join(" · ")}</div>
              </div>
            `).join("")}
          </div>
        </div>

        <div class="card">
          <h3 class="card-title" style="margin-bottom:8px">Help Center & Keyboard Shortcuts</h3>
          <div class="grid grid-3">
            <div><span class="kbd">⌘K</span> / <span class="kbd">Ctrl+K</span> — Global Search & Studio Jump</div>
            <div><code>/web</code> in Telegram — Generate single-use 10-minute SaaS sign-in link</div>
            <div><strong>Architecture</strong> — WzGram MTProto + aiogram Bot API 10.3 + MongoDB</div>
          </div>
        </div>
      `;

      $("btn-tm-add").onclick = async () => {
        try {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/team`, {
            method: "POST", body: { user_id: Number($("tm-uid").value), role: $("tm-role").value },
          });
          toast("Member added to workspace", "success");
          navigate("team", true);
        } catch (e) { toast(e.message, "error"); }
      };

      vp.querySelectorAll("[data-tm-rem]").forEach((b) => {
        b.onclick = async () => {
          await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/team/${encodeURIComponent(b.dataset.tmRem)}`, { method: "DELETE" });
          navigate("team", true);
        };
      });

      $("btn-ntf-read").onclick = async () => {
        await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/notifications`, { method: "POST", body: { mark_all_read: true } });
        await refreshBotsAndSelectors();
        navigate("team", true);
      };

      $("btn-reset-settings").onclick = async () => {
        await api("/api/v1/settings", { method: "POST", body: { reset: true } });
        toast("Settings reset to defaults", "success");
        navigate("team", true);
      };
    },

    /* 17. Platform Administration Center (Admin-Only) */
    async admin(vp) {
      const [adm, plg] = await Promise.all([
        api("/api/v1/admin/overview"),
        api("/api/v1/admin/plugins"),
      ]);
      const c = adm.counts || {};
      const res = (adm.diagnostics && adm.diagnostics.resources) || {};

      vp.innerHTML = `
        <div class="page-header">
          <div>
            <h1 class="page-title">Platform Administration Center</h1>
            <p class="page-subtitle">Restricted to platform administrators. System-wide metrics, maintenance controls, plugin manager, and global audit trail.</p>
          </div>
          <button id="btn-adm-maint" class="btn ${adm.maintenance_mode ? "btn-danger" : ""}">
            Maintenance Mode: ${adm.maintenance_mode ? "ON" : "OFF"}
          </button>
        </div>

        <div class="grid grid-4" style="margin-bottom:18px">
          <div class="stat-card"><div class="stat-label">Workspaces</div><div class="stat-value">${c.workspaces || 0}</div></div>
          <div class="stat-card"><div class="stat-label">Registered Bots</div><div class="stat-value">${c.registered_bots || 0}</div></div>
          <div class="stat-card"><div class="stat-label">Container CPU / RAM</div><div class="stat-value">${Number(res.cpu_percent || 0).toFixed(1)}% / ${Number(res.mem_percent || 0).toFixed(1)}%</div></div>
          <div class="stat-card"><div class="stat-label">Database Engine</div><div class="stat-value" style="font-size:18px">${adm.database_connected ? "MongoDB Live" : "Standalone Mem"}</div></div>
        </div>

        <div class="grid grid-2">
          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Modular Plugin Health</h3>
            ${Object.entries(plg.plugins || {}).map(([name, info]) => `
              <div style="display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)">
                <div><strong>${esc(name)}</strong> <span class="mono">v${esc(info.version || "1.0")}</span></div>
                <button class="btn btn-sm" data-plg-tog="${esc(name)}" data-en="${info.enabled ? "1" : "0"}">${info.enabled ? "Disable" : "Enable"}</button>
              </div>
            `).join("") || `<div class="stat-meta">No YAML plugins loaded in standalone web process.</div>`}
          </div>

          <div class="card">
            <h3 class="card-title" style="margin-bottom:10px">Global Security Audit Log</h3>
            <div style="max-height:240px;overflow:auto">
              ${(adm.audit_logs || []).slice(0, 20).map((l) => `
                <div style="padding:5px 0;border-bottom:1px solid var(--border);font-size:12px">
                  <strong>${esc(l.action)}</strong> by <code>${l.actor_id}</code> → ${esc(l.target || "—")} <span style="color:var(--text-muted)">(${esc(l.created_at)})</span>
                </div>
              `).join("")}
            </div>
          </div>
        </div>
      `;

      $("btn-adm-maint").onclick = async () => {
        await api("/api/v1/admin/control", { method: "POST", body: { action: "maintenance", enabled: !adm.maintenance_mode } });
        toast("Maintenance mode toggled", "success");
        navigate("admin", true);
      };

      vp.querySelectorAll("[data-plg-tog]").forEach((b) => {
        b.onclick = async () => {
          await api("/api/v1/admin/plugins", { method: "POST", body: { plugin: b.dataset.plgTog, enabled: b.dataset.en !== "1" } });
          navigate("admin", true);
        };
      });
    },
  };

  /* ── Command Palette (⌘K) & Event Wiring ── */
  function openCommandPalette() {
    const m = $("cmdk-modal");
    cmdkReturnFocus = document.activeElement;
    m.hidden = false;
    const inp = $("cmdk-input");
    inp.value = "";
    inp.focus();
    renderCmdkResults("");
  }

  function closeCommandPalette() {
    const wasOpen = !$("cmdk-modal").hidden;
    $("cmdk-modal").hidden = true;
    if (wasOpen && cmdkReturnFocus && typeof cmdkReturnFocus.focus === "function") cmdkReturnFocus.focus();
  }

  function selectCommandResult(index) {
    const item = cmdkItems[index];
    if (!item) return;
    closeCommandPalette();
    if (item.type === "bot") {
      S.botId = item.id;
      localStorage.setItem("videl_bot_id", S.botId);
    }
    if (item.studio) navigate(item.studio);
  }

  function setCommandSelection(index) {
    const nodes = $("cmdk-results").querySelectorAll("[data-cmdk-idx]");
    if (!nodes.length) { cmdkActiveIndex = -1; return; }
    cmdkActiveIndex = (index + nodes.length) % nodes.length;
    nodes.forEach((node, i) => {
      const selected = i === cmdkActiveIndex;
      node.setAttribute("aria-selected", selected ? "true" : "false");
      if (selected) node.scrollIntoView({ block: "nearest" });
    });
    $("cmdk-input").setAttribute("aria-activedescendant", "cmdk-option-" + cmdkActiveIndex);
  }

  async function renderCmdkResults(q) {
    const box = $("cmdk-results");
    const requestId = ++cmdkRequestId;
    const studioMatches = STUDIOS.flatMap((g) => (g.adminOnly && (!S.user || !S.user.is_platform_admin)) ? [] : g.items)
      .filter((s) => !q || s.label.toLowerCase().includes(q.toLowerCase()))
      .map((s) => ({ type: "studio", studio: s.id, title: s.label, subtitle: "Jump to Studio" }));

    let remote = [];
    if (q && q.length >= 2 && S.wsId) {
      try {
        const res = await api(`/api/v1/workspaces/${encodeURIComponent(S.wsId)}/search?q=${encodeURIComponent(q)}`);
        remote = res.results || [];
      } catch (_) {}
    }
    if (requestId !== cmdkRequestId || $("cmdk-modal").hidden) return;
    const combined = [...remote, ...studioMatches].slice(0, 18);
    cmdkItems = combined;
    box.innerHTML = combined.map((r, idx) => `
      <button type="button" id="cmdk-option-${idx}" role="option" aria-selected="false" class="nav-item cmdk-result" data-cmdk-idx="${idx}" style="justify-content:space-between">
        <span><strong>${esc(r.title)}</strong> <span style="color:var(--text-muted);font-size:12px">· ${esc(r.subtitle)}</span></span>
        <span class="badge badge-neutral">${esc(r.type)}</span>
      </button>
    `).join("") || `<div class="stat-meta" style="padding:12px">No matching resources.</div>`;

    box.querySelectorAll("[data-cmdk-idx]").forEach((b) => {
      b.onclick = () => selectCommandResult(Number(b.dataset.cmdkIdx));
    });
    if (combined.length) setCommandSelection(0);
    else $("cmdk-input").removeAttribute("aria-activedescendant");
  }

  window.addEventListener("beforeunload", (e) => {
    if (S.unsaved) { e.preventDefault(); e.returnValue = ""; }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (S.user) openCommandPalette();
    } else if (e.key === "Escape") {
      const wasPaletteOpen = !$("cmdk-modal").hidden;
      closeCommandPalette();
      $("app-modal").hidden = true;
      if (wasPaletteOpen) e.stopPropagation();
    }
  });

  let authPending = false;
  function setAuthPending(pending) {
    authPending = pending;
    $("btn-login-submit").disabled = pending;
    $("btn-login-demo").disabled = pending;
    $("auth-screen").setAttribute("aria-busy", pending ? "true" : "false");
  }

  $("btn-login-submit").onclick = async () => {
    if (authPending) return;
    const raw = $("auth-token-input").value.trim();
    if (!raw) return;
    $("auth-error").hidden = true;
    setAuthPending(true);
    try {
      const body = raw.startsWith("l.") || raw.length === 32 ? { link: raw } : { admin_token: raw };
      const res = await api("/api/v1/auth/login", { method: "POST", body });
      sessionStorage.removeItem("videl_telegram_signed_out");
      saveSession(res.session_token, res.csrf_token);
      await bootstrapSession();
    } catch (e) {
      $("auth-error").hidden = false;
      $("auth-error").textContent = e.message;
    } finally { setAuthPending(false); }
  };

  $("btn-login-demo").onclick = async () => {
    if (authPending) return;
    setAuthPending(true);
    $("auth-error").hidden = true;
    try {
      const res = await api("/api/v1/auth/demo", { method: "POST", body: {} });
      saveSession(res.session_token, res.csrf_token);
      await bootstrapSession();
      toast("Launched isolated Demo Sandbox Workspace", "success");
    } catch (e) {
      $("auth-error").hidden = false;
      $("auth-error").textContent = e.message;
    } finally { setAuthPending(false); }
  };

  $("btn-collapse-sidebar").onclick = () => $("app-shell").classList.toggle("sidebar-collapsed");
  $("btn-mobile-menu").onclick = () => $("sidebar").classList.toggle("mobile-open");
  $("btn-quick-create").onclick = () => navigate("bot_create");
  $("btn-notifications").onclick = () => navigate("team");
  $("btn-open-cmdk").onclick = openCommandPalette;
  $("btn-close-cmdk").onclick = closeCommandPalette;
  $("cmdk-input").oninput = (e) => renderCmdkResults(e.target.value);
  $("cmdk-input").onkeydown = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setCommandSelection(cmdkActiveIndex + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCommandSelection(cmdkActiveIndex - 1); }
    else if (e.key === "Enter" && cmdkActiveIndex >= 0) { e.preventDefault(); selectCommandResult(cmdkActiveIndex); }
  };

  $("btn-theme-toggle").onclick = () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
  };

  $("btn-logout").onclick = async () => {
    rememberTelegramSignOut();
    try { await api("/api/v1/auth/logout", { method: "POST", body: {} }); } catch (_) {}
    clearSession();
    showTelegramButton();
  };

  $("ws-selector").onchange = async (e) => {
    S.wsId = e.target.value;
    localStorage.setItem("videl_ws_id", S.wsId);
    await refreshBotsAndSelectors();
    navigate(S.studio, true);
  };

  $("bot-selector").onchange = (e) => {
    S.botId = e.target.value;
    localStorage.setItem("videl_bot_id", S.botId);
    if (["bot_config", "commands", "filestore", "analytics"].includes(S.studio)) {
      navigate(S.studio, true);
    }
  };

  /* Telegram launch data is verified by the server; never use initDataUnsafe. */
  // The timestamp is only a local sign-out hint, not proof of identity. If the
  // SDK cannot provide one, stay signed out until an explicit retry.
  function telegramLaunchDate(initData) {
    if (typeof initData !== "string" || initData.length > 8192) return "";
    const date = new URLSearchParams(initData).get("auth_date") || "";
    return /^\d{1,12}$/.test(date) && date !== "1" ? date : "";
  }
  function rememberTelegramSignOut() {
    sessionStorage.setItem("videl_telegram_signed_out", telegramLaunchDate(window.Telegram?.WebApp?.initData) || "1");
  }
  function telegramAutoAllowed(initData) {
    const signedOut = sessionStorage.getItem("videl_telegram_signed_out");
    if (!signedOut) return true;
    const date = telegramLaunchDate(initData);
    return signedOut !== "1" && !!date && date !== signedOut;
  }
  function waitForTelegramSdk() {
    if (window.Telegram?.WebApp) return Promise.resolve();
    const script = $("telegram-web-app");
    if (!script) return Promise.resolve();
    return new Promise(resolve => {
      let done = false;
      const finish = () => { if (done) return; done = true; clearTimeout(timer); script.removeEventListener("load", finish); script.removeEventListener("error", finish); resolve(); };
      const timer = setTimeout(finish, 5000);
      script.addEventListener("load", finish, {once:true});
      script.addEventListener("error", finish, {once:true});
      if (window.Telegram?.WebApp) finish();
    });
  }
  function showTelegramButton() {
    $("btn-telegram-login").hidden = !!S.token || !(typeof window.Telegram?.WebApp?.initData === "string" && window.Telegram.WebApp.initData);
  }
  async function signInTelegram() {
    if (authPending) return;
    const initData = window.Telegram?.WebApp?.initData;
    if (typeof initData !== "string" || !initData) return;
    setAuthPending(true); $("btn-telegram-login").disabled = true;
    $("auth-status").hidden = false; $("auth-status").textContent = "Connecting securely with Telegram…";
    $("auth-error").hidden = true;
    let saved = false;
    try {
      const res = await api("/api/v1/auth/login", {method:"POST", body:{init_data:initData}});
      saveSession(res.session_token, res.csrf_token);
      saved = true;
      await bootstrapSession();
      sessionStorage.removeItem("videl_telegram_signed_out");
    } catch (_) {
      if (saved) { rememberTelegramSignOut(); clearSession(); }
      $("auth-error").hidden = false;
      $("auth-error").textContent = "Telegram could not sign you in. Close and reopen Studio from the bot, or use a one-time key.";
    } finally {
      $("auth-status").hidden = true; setAuthPending(false); $("btn-telegram-login").disabled = false; showTelegramButton();
    }
  }
  $("btn-telegram-login").onclick = signInTelegram;
  const initialHash = (location.hash || "").slice(1);
  const allowLateTelegram = !initialHash || Array.from(new URLSearchParams(initialHash).keys()).some(key => key.startsWith("tgWebApp"));
  $("telegram-web-app")?.addEventListener("load", () => {
    showTelegramButton();
    // Some Telegram clients restore launch data from SDK storage rather than a
    // visible fragment. Do not interrupt manual entry or an explicit sign-out.
    if (allowLateTelegram && !authPending && !S.token && !$("auth-token-input").value.trim()
        && telegramAutoAllowed(window.Telegram?.WebApp?.initData)) void signInTelegram();
  });
  (async () => {
    setAuthPending(true);
    let telegramExchange = false;
    try {
      applyTheme(localStorage.getItem("videl_theme") || "dark");
      const hash = (location.hash || "").slice(1);
      // Telegram owns its launch fragment; it is not an admin token.
      const telegramLaunch = Array.from(new URLSearchParams(hash).keys()).some(key => key.startsWith("tgWebApp"));
      if (hash && !telegramLaunch) {
        history.replaceState(null, "", location.pathname + location.search);
        try {
          const payload = hash.startsWith("l.") ? { link: hash } : { admin_token: hash };
          const res = await api("/api/v1/auth/login", { method: "POST", body: payload });
          sessionStorage.removeItem("videl_telegram_signed_out");
          saveSession(res.session_token, res.csrf_token);
        } catch (e) {
          $("auth-error").hidden = false;
          $("auth-error").textContent = e.message;
        }
      } else if (!S.token) {
        if (telegramLaunch) {
          $("auth-status").hidden = false;
          $("auth-status").textContent = "Connecting securely with Telegram…";
          await waitForTelegramSdk();
        }
        const initData = window.Telegram?.WebApp?.initData;
        if (typeof initData === "string" && initData && telegramAutoAllowed(initData)) {
          try {
            const res = await api("/api/v1/auth/login", { method: "POST", body: { init_data: initData } });
            saveSession(res.session_token, res.csrf_token);
            telegramExchange = true;
          } catch (_) {
            $("auth-error").hidden = false;
            $("auth-error").textContent = "Telegram could not sign you in. Close and reopen Studio from the bot, or use a one-time key.";
          }
        } else if (telegramLaunch && !initData) {
          $("auth-error").hidden = false;
          $("auth-error").textContent = "Telegram sign-in could not load. Reopen Studio from the bot, or use a one-time key.";
        }
      }
      if (S.token) {
        try {
          await bootstrapSession();
          if (telegramExchange) sessionStorage.removeItem("videl_telegram_signed_out");
        } catch (_) {
          if (telegramExchange) rememberTelegramSignOut();
          clearSession();
          $("auth-error").hidden = false;
          $("auth-error").textContent = "Could not load your workspace. Reopen Studio from the bot or use a one-time key.";
        }
      }
    } finally {
      $("auth-status").hidden = true;
      setAuthPending(false);
      showTelegramButton();
    }
  })();
})();

"""
