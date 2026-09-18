from __future__ import annotations

"""Stat, tab, and control styles for the workstation dashboard shell."""

WORKSTATION_RENDER_SHELL_STAT_STYLES = """
    .stats {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      display: grid;
      gap: 8px;
      box-shadow: var(--shadow);
    }
    .stat[data-inspect-type],
    .dashboard-home-card[data-inspect-type],
    .mini-stat[data-inspect-type] {
      cursor: pointer;
      transition: border-color 0.14s ease, transform 0.14s ease, box-shadow 0.14s ease;
    }
    .stat[data-inspect-type]:hover,
    .stat[data-inspect-type]:focus-visible,
    .dashboard-home-card[data-inspect-type]:hover,
    .dashboard-home-card[data-inspect-type]:focus-visible,
    .mini-stat[data-inspect-type]:hover,
    .mini-stat[data-inspect-type]:focus-visible {
      border-color: rgba(97, 218, 251, 0.34);
      transform: translateY(-1px);
      outline: none;
    }
    .stat-label {
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .stat-value {
      font-size: 30px;
      font-weight: 700;
      line-height: 1;
    }
    .stat-detail {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .tab-button {
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      border-radius: 999px;
      padding: 9px 14px;
      font-size: 13px;
      cursor: pointer;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .tab-button.active {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
    }
    .copy-button {
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 11px;
      cursor: pointer;
    }
    .copy-button:hover,
    .copy-button:focus-visible {
      border-color: var(--accent);
      color: var(--accent-strong);
      outline: none;
    }
"""
