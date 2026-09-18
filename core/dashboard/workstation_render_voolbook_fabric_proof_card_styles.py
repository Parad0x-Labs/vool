from __future__ import annotations

WORKSTATION_RENDER_VOOLBOOK_FABRIC_PROOF_CARD_STYLES = """
    .nb-proof-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 20px;
    }
    .nb-proof-card p {
      margin: 0 0 12px;
      font-size: 14px;
      line-height: 1.6;
      color: var(--muted);
    }
    .nb-proof-card p:last-child { margin-bottom: 0; }
    .nb-proof-factors {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 8px;
      margin-top: 12px;
    }
    .nb-proof-factor {
      background: rgba(97, 218, 251, 0.05);
      border: 1px solid rgba(97, 218, 251, 0.12);
      border-radius: 8px;
      padding: 10px 12px;
      font-size: 12px;
      color: var(--ink);
    }
    .nb-proof-factor-label { font-weight: 700; display: block; margin-bottom: 2px; }
"""
