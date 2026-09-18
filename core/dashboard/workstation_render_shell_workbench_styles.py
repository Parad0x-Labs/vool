from __future__ import annotations

"""Workbench and stage layout styles for the workstation dashboard shell."""

WORKSTATION_RENDER_SHELL_WORKBENCH_STYLES = """
    .dashboard-frame {
      display: grid;
      gap: 16px;
    }
    .dashboard-workbench {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr) 340px;
      gap: 16px;
      align-items: stretch;
    }
    .dashboard-rail,
    .dashboard-inspector {
      padding: 16px;
      position: sticky;
      top: 18px;
      align-self: start;
      min-height: calc(100vh - 36px);
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.01)),
        var(--wk-panel-strong);
    }
    .dashboard-rail::before,
    .dashboard-inspector::before {
      content: "";
      display: block;
      width: 44px;
      height: 3px;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), transparent);
      margin-bottom: 14px;
    }
    .dashboard-rail .tab-button,
    .dashboard-rail .copy-button {
      width: 100%;
      justify-content: flex-start;
      text-align: left;
    }
    .dashboard-rail .wk-chip-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .dashboard-rail-group + .dashboard-rail-group,
    .dashboard-inspector-group + .dashboard-inspector-group {
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid var(--line);
    }
    .dashboard-rail-label,
    .dashboard-inspector-label {
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }
    .dashboard-home-board {
      margin-bottom: 16px;
    }
    .dashboard-home-board .section-title {
      margin-bottom: 12px;
    }
    .dashboard-stage {
      padding: 18px;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01)),
        rgba(9, 15, 28, 0.96);
      display: grid;
      gap: 18px;
    }
    .dashboard-stage-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }
    .dashboard-stage-head h2 {
      margin: 0;
      font-size: 30px;
      letter-spacing: -0.04em;
      line-height: 1.05;
    }
    .dashboard-stage-copy {
      margin: 8px 0 0;
      max-width: 72ch;
      color: var(--muted);
      line-height: 1.6;
      font-size: 14px;
    }
    .dashboard-stage-proof {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }
    .dashboard-stage-proof .wk-proof-chip {
      white-space: nowrap;
    }
    .dashboard-overview-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.14fr) minmax(320px, 0.86fr);
      gap: 16px;
      align-items: start;
    }
    .dashboard-overview-primary,
    .dashboard-overview-secondary {
      display: grid;
      gap: 16px;
      align-content: start;
    }
    .dashboard-home-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .dashboard-home-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      background:
        linear-gradient(180deg, rgba(97, 218, 251, 0.08), rgba(255, 255, 255, 0.02)),
        rgba(255, 255, 255, 0.03);
      padding: 16px;
      display: grid;
      gap: 8px;
      min-height: 148px;
    }
    .dashboard-home-card strong {
      display: block;
      font-size: 24px;
      line-height: 1.1;
    }
    .dashboard-home-card span {
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
    }
    .dashboard-home-card p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .dashboard-tab-row {
      display: flex;
      flex-wrap: nowrap;
      gap: 8px;
      margin: 0;
      padding: 10px 12px;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: thin;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.03);
    }
"""
