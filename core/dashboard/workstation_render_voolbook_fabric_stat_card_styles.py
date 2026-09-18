from __future__ import annotations

WORKSTATION_RENDER_VOOLBOOK_FABRIC_STAT_CARD_STYLES = """
    .nb-fabric-cards {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }
    .nb-fabric-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 18px;
    }
    .nb-fabric-card-title {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .nb-fabric-card-value {
      font-size: 24px;
      font-weight: 800;
      color: var(--ink);
      letter-spacing: -0.5px;
    }
    .nb-fabric-card-detail {
      font-size: 12px;
      color: var(--muted);
      margin-top: 6px;
      line-height: 1.5;
    }
"""
