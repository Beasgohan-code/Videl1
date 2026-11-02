"""
Reusable SaaS Design System Stylesheet (`VidelBot/webapp/ui_styles.py`).

Clean, high-contrast Light & Dark semantic token architecture with crisp spacing,
accessible focus states, responsive desktop sidebar + mobile navigation, and zero
gimmicky glassmorphism or random neon gradients.
"""

STYLES_CSS = """
:root, [data-theme="dark"] {
  color-scheme: dark;
  --bg-app: #0b0d12;
  --bg-surface: #12161f;
  --bg-elevated: #181d29;
  --bg-subtle: #1e2534;
  --bg-hover: #232b3d;
  --border: #262f42;
  --border-strong: #35415b;
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --accent: #3b82f6;
  --accent-hover: #2563eb;
  --accent-soft: rgba(59, 130, 246, 0.14);
  --success: #10b981;
  --success-soft: rgba(16, 185, 129, 0.14);
  --warning: #f59e0b;
  --warning-soft: rgba(245, 158, 11, 0.14);
  --danger: #ef4444;
  --danger-soft: rgba(239, 68, 68, 0.14);
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.35);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.42);
  --shadow-lg: 0 20px 48px rgba(0, 0, 0, 0.55);
}

[data-theme="light"] {
  color-scheme: light;
  --bg-app: #f8fafc;
  --bg-surface: #ffffff;
  --bg-elevated: #ffffff;
  --bg-subtle: #f1f5f9;
  --bg-hover: #e2e8f0;
  --border: #e2e8f0;
  --border-strong: #cbd5e1;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-muted: #64748b;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --accent-soft: rgba(37, 99, 235, 0.10);
  --success: #059669;
  --success-soft: rgba(5, 150, 105, 0.11);
  --warning: #d97706;
  --warning-soft: rgba(217, 119, 6, 0.12);
  --danger: #dc2626;
  --danger-soft: rgba(220, 38, 38, 0.11);
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.05);
  --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.08);
  --shadow-lg: 0 20px 48px rgba(15, 23, 42, 0.14);
}

*, *::before, *::after { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  height: 100%;
  background: var(--bg-app);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

code, pre, .mono {
  font-family: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace;
  font-size: 12.5px;
}

button, input, select, textarea {
  font: inherit;
  color: inherit;
}

:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

[hidden] { display: none !important; }

/* ── Auth Screen ── */
.auth-screen {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.auth-card {
  width: 100%;
  max-width: 460px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 32px;
  box-shadow: var(--shadow-md);
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 17px;
  letter-spacing: -0.02em;
}

.brand-badge {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
}

/* ── App Layout Shell ── */
.app-shell {
  display: grid;
  grid-template-columns: 252px 1fr;
  min-height: 100vh;
  transition: grid-template-columns 0.18s ease;
}

.app-shell.sidebar-collapsed {
  grid-template-columns: 68px 1fr;
}

.sidebar {
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  z-index: 30;
}

.sidebar-header {
  height: 58px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sidebar-nav {
  padding: 12px 10px 24px;
  flex: 1;
  overflow-y: auto;
}

.nav-group-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  padding: 12px 10px 5px;
}

.app-shell.sidebar-collapsed .nav-group-label,
.app-shell.sidebar-collapsed .nav-label,
.app-shell.sidebar-collapsed .brand-text {
  display: none;
}

.nav-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  margin-bottom: 2px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  text-align: left;
  transition: background 0.12s ease, color 0.12s ease;
}

.nav-item:hover {
  background: var(--bg-subtle);
  color: var(--text-primary);
}

.nav-item.active {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.nav-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* ── Topbar ── */
.main-column {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 58px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  position: sticky;
  top: 0;
  z-index: 25;
}

.topbar-left, .topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.selector-pill {
  height: 34px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  max-width: 195px;
}

.cmdk-trigger {
  height: 34px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  display: inline-flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  min-width: 210px;
  justify-content: space-between;
}

.cmdk-trigger:hover { border-color: var(--border-strong); color: var(--text-primary); }

.kbd {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--border-strong);
  background: var(--bg-surface);
  color: var(--text-muted);
}

.demo-banner {
  background: var(--warning-soft);
  border-bottom: 1px solid rgba(245, 158, 11, 0.35);
  color: var(--warning);
  padding: 7px 20px;
  font-size: 12.5px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

/* ── Main Content & Reusable Components ── */
.workspace-canvas {
  padding: 24px 28px 80px;
  max-width: 1440px;
  width: 100%;
  margin: 0 auto;
}

.breadcrumb-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 22px;
  flex-wrap: wrap;
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.page-subtitle {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 13.5px;
}

.grid { display: grid; gap: 16px; }
.grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }

.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.card-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.stat-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
}

.stat-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}

.stat-meta {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

/* ── Buttons & Inputs ── */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: 34px;
  padding: 0 13px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.12s ease, border-color 0.12s ease;
  white-space: nowrap;
}

.btn:hover:not(:disabled) { background: var(--bg-hover); border-color: var(--border-strong); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn-primary:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }
.btn-danger { background: var(--danger-soft); border-color: rgba(239, 68, 68, 0.35); color: var(--danger); }
.btn-sm { height: 28px; padding: 0 10px; font-size: 12px; border-radius: 6px; }
.btn-icon { width: 34px; padding: 0; }

.field { margin-bottom: 14px; }
.field-label { display: block; font-size: 12.5px; font-weight: 600; margin-bottom: 5px; color: var(--text-primary); }
.field-hint { font-size: 12px; color: var(--text-muted); margin-top: 4px; }

.input, .select, .textarea {
  width: 100%;
  padding: 8px 11px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  color: var(--text-primary);
  font-size: 13.5px;
}

.input:focus, .select:focus, .textarea:focus {
  border-color: var(--accent);
  outline: none;
}

.textarea { min-height: 82px; resize: vertical; }

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.toolbar-group { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* ── Badges & Status Indicators ── */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
  border: 1px solid transparent;
}

.badge-success { background: var(--success-soft); color: var(--success); }
.badge-warning { background: var(--warning-soft); color: var(--warning); }
.badge-danger  { background: var(--danger-soft); color: var(--danger); }
.badge-info    { background: var(--accent-soft); color: var(--accent); }
.badge-neutral { background: var(--bg-subtle); color: var(--text-secondary); border-color: var(--border); }

.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot-ok { background: var(--success); }
.dot-warn { background: var(--warning); }
.dot-err { background: var(--danger); }

/* ── Tables ── */
.table-wrap {
  width: 100%;
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
}

table.data-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  font-size: 13px;
}

.data-table th {
  padding: 10px 14px;
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 12px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.data-table td {
  padding: 11px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: middle;
}

.data-table tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover { background: var(--bg-subtle); }

/* ── Empty, Loading & Alert States ── */
.empty-state {
  text-align: center;
  padding: 40px 20px;
  border: 1px dashed var(--border-strong);
  border-radius: 12px;
  color: var(--text-secondary);
}

.empty-state h4 { margin: 8px 0 4px; color: var(--text-primary); font-size: 15px; }
.empty-state p { margin: 0 0 14px; font-size: 13px; }

.callout {
  padding: 12px 14px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--bg-subtle);
  font-size: 13px;
  margin-bottom: 14px;
}

.callout-warn { background: var(--warning-soft); border-color: rgba(245, 158, 11, 0.35); color: var(--warning); }
.callout-info { background: var(--accent-soft); border-color: rgba(59, 130, 246, 0.35); }

.progress-track {
  width: 100%;
  height: 7px;
  background: var(--bg-subtle);
  border-radius: 99px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--accent);
  transition: width 0.25s ease;
}

/* ── Tabs & Steppers ── */
.subtabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  background: var(--bg-subtle);
  border: 1px solid var(--border);
  border-radius: 9px;
  margin-bottom: 18px;
  flex-wrap: wrap;
}

.subtab {
  padding: 6px 12px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}

.subtab.active {
  background: var(--bg-surface);
  color: var(--text-primary);
  box-shadow: var(--shadow-sm);
}

.stepper {
  display: flex;
  gap: 8px;
  margin-bottom: 18px;
  flex-wrap: wrap;
}

.step-pill {
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid var(--border);
  color: var(--text-muted);
}

.step-pill.active { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.step-pill.done { border-color: var(--success); color: var(--success); }

/* ── Modals & Command Palette ── */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(5, 8, 15, 0.68);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 64px 16px 24px;
  z-index: 90;
  overflow-y: auto;
}

.modal-box {
  width: 100%;
  max-width: 580px;
  background: var(--bg-surface);
  border: 1px solid var(--border-strong);
  border-radius: 14px;
  padding: 22px 24px;
  box-shadow: var(--shadow-lg);
}

.toast-stack {
  position: fixed;
  right: 20px;
  bottom: 24px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 120;
  max-width: 360px;
}

.toast {
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  border-left: 4px solid var(--accent);
  padding: 11px 14px;
  border-radius: 9px;
  box-shadow: var(--shadow-md);
  font-size: 13px;
}

.toast.err { border-left-color: var(--danger); }
.toast.ok  { border-left-color: var(--success); }

/* ── Mobile Navigation ── */
.mobile-bottom-nav { display: none; }
.mobile-menu-btn { display: none; }

@media (max-width: 1024px) {
  .grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 780px) {
  .app-shell, .app-shell.sidebar-collapsed { grid-template-columns: 1fr; }
  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    width: 268px;
    transform: translateX(-104%);
    transition: transform 0.2s ease;
    box-shadow: var(--shadow-lg);
  }
  .sidebar.mobile-open { transform: translateX(0); }
  .mobile-menu-btn { display: inline-flex; }
  .cmdk-trigger { min-width: auto; }
  .cmdk-trigger span:first-child { display: none; }
  .workspace-canvas { padding: 16px 14px 88px; }
  .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
  .mobile-bottom-nav {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    height: 60px;
    background: var(--bg-surface);
    border-top: 1px solid var(--border);
    z-index: 40;
  }
  .mobile-bottom-nav button {
    border: none;
    background: transparent;
    color: var(--text-secondary);
    font-size: 11px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    cursor: pointer;
  }
  .mobile-bottom-nav button.active { color: var(--accent); font-weight: 600; }
}
"""
