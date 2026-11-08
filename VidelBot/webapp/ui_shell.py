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
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{bot_name} Studio · Multi-Bot SaaS Platform</title>
  <link rel="stylesheet" href="/app/styles.css">
  <script id="telegram-web-app" src="https://telegram.org/js/telegram-web-app.js" async></script>
</head>
<body>
  <a class="skip-link" href="#auth-screen">Skip to sign in</a>
  <!-- Auth Gate -->
  <main id="auth-screen" class="auth-screen" tabindex="-1" aria-label="Workspace access">
    <div class="entry-layout">
      <section class="entry-intro" aria-labelledby="entry-title">
        <div class="brand-mark entry-brand"><span class="brand-badge">V</span><span>{bot_name} <span class="entry-muted">/ Studio</span></span></div>
        <div class="entry-intro-main">
          <p class="entry-kicker">YOUR TELEGRAM WORKSPACE</p>
          <h1 id="entry-title">Your bots.<br><span>Your space.</span></h1>
          <p class="entry-description">One place to manage the bots, files and tools your workspace can access.</p>
          <div class="entry-features" aria-label="Studio areas">
            <div><span>01</span><strong>Workspace</strong><small>Your own space, ready on your first visit</small></div>
            <div><span>02</span><strong>Bots</strong><small>Manage the bot records you have access to</small></div>
            <div><span>03</span><strong>Tools</strong><small>Files and media, where permitted</small></div>
          </div>
        </div>
        <p class="entry-foot">Built around your Telegram bot <span aria-hidden="true">↗</span></p>
      </section>
      <section class="auth-card entry-card" role="region" aria-labelledby="entry-card-title">
        <div class="entry-top"><span>VIDEL STUDIO</span><span>WORKSPACE ACCESS</span></div>
        <div class="entry-heading">
          <p class="entry-kicker">NO SEPARATE SIGN-UP</p>
          <h2 id="entry-card-title">Welcome to Studio</h2>
          <p>Open Studio from your bot in Telegram. We verify your account and create your workspace on your first visit.</p>
        </div>
        <div class="entry-steps" aria-label="How to get started">
          <div><span>01</span><p>Send <strong>/web</strong> to your bot</p></div>
          <div><span>02</span><p>Tap <strong>Open Studio in Telegram</strong></p></div>
          <div><span>03</span><p>Your workspace is ready</p></div>
        </div>
        <div id="auth-error" class="callout callout-warn" role="alert" hidden></div>
        <div id="auth-status" class="field-hint" role="status" hidden></div>
        <button id="btn-telegram-login" class="btn btn-primary entry-telegram" type="button" hidden>Continue with Telegram <span aria-hidden="true">→</span></button>
        <form id="auth-form" class="entry-form">
          <div class="entry-divider"><span>Opening in a browser?</span></div>
          <div class="field">
            <label class="field-label" for="auth-token-input">One-time key or admin token</label>
            <input id="auth-token-input" class="input" type="password" autocomplete="off" spellcheck="false" aria-describedby="entry-key-hint" placeholder="Paste your key here">
            <div id="entry-key-hint" class="field-hint">Request a one-time key from <code>/web</code>. Keys expire after 10 minutes.</div>
          </div>
          <button id="btn-login-submit" class="btn btn-primary entry-submit" type="submit">Continue to workspace <span aria-hidden="true">→</span></button>
        </form>
        <button id="btn-login-demo" class="btn entry-demo" type="button" title="Launch isolated Demo Workspace" hidden>Launch Demo Workspace</button>
        <div class="entry-footer"><a href="/">← Back to website</a><a href="/admin">Classic console ↗</a></div>
      </section>
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
      <nav id="sidebar-nav" class="sidebar-nav" aria-label="Studio sections"></nav>
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

      <main class="workspace-canvas" id="main-canvas" tabindex="-1" aria-label="Workspace content">
        <nav id="breadcrumbs" class="breadcrumb-bar" aria-label="Breadcrumb"></nav>
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
  <div id="cmdk-modal" class="modal-backdrop" hidden role="dialog" aria-modal="true" aria-labelledby="cmdk-title">
    <div class="modal-box">
      <h2 id="cmdk-title" style="margin:0 0 10px;font-size:16px">Search and jump to</h2>
      <div class="field" style="margin-bottom:10px">
        <input id="cmdk-input" class="input" type="search" role="combobox" aria-autocomplete="list" aria-controls="cmdk-results" aria-expanded="true" aria-label="Search bots, files, commands and studios" placeholder="Search bots, files, commands, templates, or jump to a studio…">
      </div>
      <div id="cmdk-results" role="listbox" aria-label="Search results" style="max-height:340px;overflow-y:auto"></div>
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
