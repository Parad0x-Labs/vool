from __future__ import annotations

WORKSTATION_RENDER_VOOLBOOK_FABRIC_TICKER_STYLES = """
    .nb-ticker-wrap {
      margin-top: 16px;
      overflow: hidden;
      border-radius: 8px;
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 10px 0;
    }
    .nb-ticker {
      display: flex;
      gap: 32px;
      animation: nb-scroll 30s linear infinite;
      white-space: nowrap;
      padding: 0 16px;
    }
    @keyframes nb-scroll {
      0% { transform: translateX(0); }
      100% { transform: translateX(-50%); }
    }
    .nb-ticker-item {
      font-size: 13px;
      color: var(--muted);
      flex-shrink: 0;
    }
    .nb-ticker-dot {
      display: inline-block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
    }
    .nb-ticker-dot--claim { background: #61dafb; }
    .nb-ticker-dot--post { background: #a78bfa; }
    .nb-ticker-dot--solve { background: #34d399; }
    .nb-ticker-dot--default { background: var(--muted); }
"""
