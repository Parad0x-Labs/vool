from __future__ import annotations

"""Inspector and drawer styles for the workstation dashboard shell."""

WORKSTATION_RENDER_SHELL_INSPECTOR_STYLES = """
    .dashboard-inspector-title {
      margin: 0 0 10px;
      font-size: 20px;
      letter-spacing: -0.03em;
    }
    .dashboard-inspector-body {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    .dashboard-inspector-meta {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .dashboard-inspector-truth-note {
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(97, 218, 251, 0.06);
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }
    .dashboard-inspector-row {
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      overflow-wrap: anywhere;
      font-size: 12px;
      line-height: 1.5;
    }
    .inspector-view-toggle {
      display: flex;
      gap: 4px;
      margin: 8px 0 4px;
    }
    .inspector-view-btn {
      border: 1px solid var(--line);
      background: transparent;
      color: var(--muted);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 11px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .inspector-view-btn.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    .dashboard-inspector-raw {
      display: none;
      margin-top: 12px;
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.26);
      font-family: var(--wk-font-mono);
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow: auto;
      max-height: 48vh;
      color: var(--wk-text);
    }
    .dashboard-inspector[data-inspector-mode="raw"] .dashboard-inspector-raw {
      display: block;
    }
    .dashboard-inspector[data-inspector-mode="raw"] .dashboard-inspector-human,
    .dashboard-inspector[data-inspector-mode="raw"] .dashboard-inspector-agent {
      display: none;
    }
    .dashboard-inspector[data-inspector-mode="agent"] .dashboard-inspector-human[data-human-optional="1"] {
      display: none;
    }
    .dashboard-inspector[data-inspector-mode="human"] .dashboard-inspector-agent[data-agent-optional="1"] {
      display: none;
    }
    .dashboard-drawer {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.03);
      overflow: hidden;
    }
    .dashboard-drawer summary {
      list-style: none;
      cursor: pointer;
      padding: 12px 14px;
      color: var(--ink);
      font-size: 12px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      background: rgba(255, 255, 255, 0.02);
    }
    .dashboard-drawer summary::-webkit-details-marker {
      display: none;
    }
    .dashboard-drawer-body {
      padding: 14px;
      border-top: 1px solid var(--line);
    }
    .inspect-button {
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      cursor: pointer;
    }
    .inspect-button:hover,
    .inspect-button:focus-visible {
      border-color: var(--accent);
      color: var(--accent);
      outline: none;
    }
"""
