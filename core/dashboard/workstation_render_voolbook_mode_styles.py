from __future__ import annotations

"""VoolBook mode bridge styles that remap the workstation shell into the feed view."""

WORKSTATION_RENDER_VOOLBOOK_MODE_STYLES = """
    body.voolbook-mode .wk-topbar { display: none; }
    body.voolbook-mode .wk-app-shell { padding-top: 0; }
    body.voolbook-mode .dashboard-workbench { display: block; }
    body.voolbook-mode .wk-panel.dashboard-rail { display: none; }
    body.voolbook-mode .wk-panel.dashboard-inspector { display: none; }
    body.voolbook-mode .hero { display: none; }
    body.voolbook-mode .stats { display: none; }
    body.voolbook-mode .tabs.dashboard-tab-row { display: none; }
    body.voolbook-mode .dashboard-stage-head { display: none; }
    body.voolbook-mode .nb-hide-in-nbmode { display: none; }
    body.voolbook-mode .shell.dashboard-frame { max-width: 960px; margin: 0 auto; padding: 0 16px; }
    body.voolbook-mode .wk-main-column { padding: 0; max-width: 100%; }
    body.voolbook-mode .dashboard-stage { padding: 0; background: transparent; border: none; box-shadow: none; }
    body.voolbook-mode footer { text-align: center; }
    .nb-topbar {
      display: none;
      align-items: center;
      justify-content: space-between;
      padding: 14px 24px;
      background: rgba(10, 15, 26, 0.85);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--line);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    body.voolbook-mode .nb-topbar { display: flex; }
    .nb-topbar-brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 18px;
      font-weight: 800;
      letter-spacing: -0.5px;
      background: linear-gradient(135deg, #61dafb, #a78bfa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .nb-topbar-pulse {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ok);
      animation: nb-pulse 2s ease-in-out infinite;
    }
    @keyframes nb-pulse {
      0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.5); }
      50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(52, 211, 153, 0); }
    }
    .nb-topbar-links {
      display: flex;
      gap: 16px;
      align-items: center;
    }
    .nb-topbar-links a {
      color: var(--muted);
      text-decoration: none;
      font-size: 13px;
      transition: color 0.15s;
    }
    .nb-topbar-links a:hover { color: var(--ink); }
    .nb-topbar-modes {
      display: flex;
      gap: 4px;
      align-items: center;
    }
    .nb-mode-link {
      color: var(--muted);
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 6px;
      transition: color 0.15s, background 0.15s;
    }
    .nb-mode-link:hover { color: var(--ink); background: rgba(255,255,255,0.06); }
    .nb-mode-link.active { color: var(--accent, #61dafb); background: rgba(97,218,251,0.1); }
    @media (max-width: 640px) {
      .nb-topbar { padding: 10px 16px; }
    }
"""
