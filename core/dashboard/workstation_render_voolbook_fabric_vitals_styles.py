from __future__ import annotations

WORKSTATION_RENDER_VOOLBOOK_FABRIC_VITALS_STYLES = """
    .nb-vitals {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 12px;
      margin-top: 20px;
    }
    .nb-vital {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px 14px;
      text-align: center;
      position: relative;
      overflow: hidden;
    }
    .nb-vital-value {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -1px;
      color: var(--ink);
    }
    .nb-vital-label {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--muted);
      margin-top: 4px;
    }
    .nb-vital-fresh {
      font-size: 10px;
      color: var(--accent);
      margin-top: 4px;
    }
    .nb-vital--live .nb-vital-value { color: var(--ok); }
    .nb-vital--live::before {
      content: '';
      position: absolute;
      top: 8px;
      right: 10px;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--ok);
      animation: nb-pulse 2s ease-in-out infinite;
    }
"""
