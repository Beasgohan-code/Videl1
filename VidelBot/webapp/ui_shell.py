"""
Semantic HTML5 Shell for Videl1 Multi-Bot SaaS Platform (`VidelBot/webapp/ui_shell.py`).
"""
from __future__ import annotations

import html

import config


def render_app_html() -> str:
    bot_name = html.escape(str(getattr(config, "BOT_NAME", "Videl") or "Videl"))
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{bot_name} Studio · Multi-Bot SaaS Platform</title>
  <link rel="stylesheet" href="/app/styles.css">
  <script src="https://telegram.org/js/telegram-web-app.js" async></script>
</head>
<body>
  <!-- Auth Gate -->
  <main id="auth-screen" class="auth-screen">
    <div class="auth-card" role="region" aria-label="Sign in to {bot_name} Studio">
      <div class="brand-mark" style="margin-bottom:18px">
        <span class="brand-badge">V</span>
        <span>{bot_name} Studio</span>
        <span class="badge badge-info">Multi-Bot SaaS</span>
      </div>
      <h1 style="margin:0 0 6px;font-size:20px">Sign in to your workspace</h1>
      <p style="margin:0 0 20px;color:var(--text-secondary);font-size:13.5px">
        Create, connect, clone, configure, and operate multiple Telegram bots and File Stores from one platform.
      </p>

      <div id="auth-error" class="callout callout-warn" hidden></div>

      <div class="field">
        <label class="field-label" for="auth-token-input">One-Time Telegram Link or Admin Token</label>
        <input id="auth-token-input" class="input" type="password" autocomplete="off"
               placeholder="Paste link key from /web or /dashboard, or Admin Token">
        <div class="field-hint">Send <code>/web</code> or <code>/dashboard</code> to the bot in Telegram for a 10-minute single-use sign-in link.</div>
      </div>

      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px">
        <button id="btn-login-submit" class="btn btn-primary" style="flex:1" type="button">Sign In</button>
        <button id="btn-login-demo" class="btn" style="flex:1" type="button" title="Launch isolated Demo Workspace">
          Launch Demo Workspace
        </button>
      </div>

      <div style="margin-top:20px;padding-top:14px;border-top:1px solid var(--border);display:flex;justify-content:space-between;font-size:12px;color:var(--text-muted)">
        <span>WzGram MTProto · Bot API 10.3 · MongoDB</span>
        <a href="/admin" style="color:var(--accent);text-decoration:none">Classic Ops Console →</a>
      </div>
    </div>
  </main>

  <!-- Main SaaS Shell -->
  <div id="app-shell" class="app-shell" hidden>
    <aside id="sidebar" class="sidebar" aria-label="Primary Studio Navigation">
      <div class="sidebar-header">
        <div class="brand-mark">
          <span class="brand-badge">V</span>
          <span class="brand-text">{bot_name} Studio</span>
        </div>
        <button id="btn-collapse-sidebar" class="btn btn-sm btn-icon" type="button" title="Collapse or expand sidebar" aria-label="Toggle sidebar">⇄</button>
      </div>
      <nav id="sidebar-nav" class="sidebar-nav"></nav>
    </aside>

    <div class="main-column">
      <header class="topbar">
        <div class="topbar-left">
          <button id="btn-mobile-menu" class="btn btn-sm btn-icon mobile-menu-btn" type="button" aria-label="Open navigation">☰</button>
          <label for="ws-selector" class="mono" style="font-size:11px;color:var(--text-muted)">WS</label>
          <select id="ws-selector" class="selector-pill" aria-label="Active Workspace"></select>
          <label for="bot-selector" class="mono" style="font-size:11px;color:var(--text-muted)">BOT</label>
          <select id="bot-selector" class="selector-pill" aria-label="Active Bot"></select>
        </div>

        <div class="topbar-right">
          <button id="btn-open-cmdk" class="cmdk-trigger" type="button" aria-label="Search and command palette">
            <span>Search bots, files, commands…</span>
            <span class="kbd">⌘K</span>
          </button>
          <button id="btn-quick-create" class="btn btn-primary btn-sm" type="button">+ New Bot</button>
          <button id="btn-notifications" class="btn btn-sm" type="button" aria-label="Notifications">
            🔔 <span id="ntf-badge" class="badge badge-info" hidden>0</span>
          </button>
          <button id="btn-theme-toggle" class="btn btn-sm btn-icon" type="button" title="Switch Light / Dark theme" aria-label="Switch theme">◐</button>
          <button id="btn-logout" class="btn btn-sm" type="button" title="Sign out">Sign out</button>
        </div>
      </header>

      <div id="demo-banner" class="demo-banner" hidden>
        <span>🧪 DEMO SANDBOX WORKSPACE — Sample records are isolated to this temporary session and never touch production bots.</span>
        <span class="badge badge-warning">DEMO MODE</span>
      </div>

      <main class="workspace-canvas" id="main-canvas" tabindex="-1">
        <div id="breadcrumbs" class="breadcrumb-bar"></div>
        <div id="studio-viewport"></div>
      </main>
    </div>
  </div>

  <!-- Mobile Bottom Navigation -->
  <nav id="mobile-bottom-nav" class="mobile-bottom-nav" aria-label="Mobile quick navigation" hidden>
    <button type="button" data-nav="overview"><span>⌂</span><span>Overview</span></button>
    <button type="button" data-nav="bots"><span>🤖</span><span>Bots</span></button>
    <button type="button" data-nav="filestore"><span>📦</span><span>Files</span></button>
    <button type="button" data-nav="downloads"><span>⬇</span><span>Downloads</span></button>
    <button type="button" data-nav="analytics"><span>📈</span><span>Analytics</span></button>
  </nav>

  <!-- Global Search & Command Palette (⌘K) -->
  <div id="cmdk-modal" class="modal-backdrop" hidden role="dialog" aria-modal="true" aria-label="Command Palette">
    <div class="modal-box">
      <div class="field" style="margin-bottom:10px">
        <input id="cmdk-input" class="input" type="search" placeholder="Search bots, files, commands, templates, or jump to a studio…">
      </div>
      <div id="cmdk-results" style="max-height:340px;overflow-y:auto"></div>
      <div style="margin-top:10px;display:flex;justify-content:space-between;font-size:11.5px;color:var(--text-muted)">
        <span>Click an item to navigate</span>
        <button id="btn-close-cmdk" class="btn btn-sm" type="button">Esc · Close</button>
      </div>
    </div>
  </div>

  <!-- Generic Confirmation / Reveal Modal -->
  <div id="app-modal" class="modal-backdrop" hidden role="dialog" aria-modal="true">
    <div class="modal-box">
      <h3 id="modal-title" style="margin:0 0 8px;font-size:17px">Confirm Action</h3>
      <div id="modal-body" style="color:var(--text-secondary);font-size:13.5px;margin-bottom:18px"></div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button id="modal-cancel" class="btn" type="button">Cancel</button>
        <button id="modal-confirm" class="btn btn-primary" type="button">Confirm</button>
      </div>
    </div>
  </div>

  <div id="toast-stack" class="toast-stack" aria-live="polite"></div>
  <script src="/app/app.js"></script>
</body>
</html>"""
