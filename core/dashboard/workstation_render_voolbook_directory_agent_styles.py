from __future__ import annotations

"""VoolBook agent directory styles for the workstation dashboard."""

WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_AGENT_STYLES = """
    .nb-agent-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 12px;
    }
    .nb-agent-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      text-align: center;
    }
    .nb-agent-avatar {
      width: 48px;
      height: 48px;
      border-radius: 50%;
      background: linear-gradient(135deg, rgba(97, 218, 251, 0.3), rgba(244, 114, 182, 0.3));
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 8px;
      position: relative;
    }
    .nb-agent-avatar::after {
      content: "\\1F98B";
      position: absolute;
      bottom: -2px;
      right: -6px;
      font-size: 14px;
    }
    .nb-agent-name {
      font-weight: 700;
      font-size: 15px;
      color: var(--wk-text);
    }
    .nb-agent-tier {
      font-size: 11px;
      color: var(--accent);
      margin-top: 2px;
    }
    .nb-agent-stats {
      display: flex;
      justify-content: center;
      gap: 16px;
      margin-top: 10px;
      font-size: 11px;
      color: var(--muted);
    }
    .nb-agent-caps {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      justify-content: center;
      margin-top: 8px;
    }
    .nb-cap-tag {
      padding: 2px 7px;
      border-radius: 999px;
      background: rgba(97, 218, 251, 0.08);
      border: 1px solid rgba(97, 218, 251, 0.15);
      font-size: 10px;
      color: var(--muted);
    }
"""
