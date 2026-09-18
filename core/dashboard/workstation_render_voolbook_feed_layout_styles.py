from __future__ import annotations

"""VoolBook feed layout styles for the workstation dashboard."""

WORKSTATION_RENDER_VOOLBOOK_FEED_LAYOUT_STYLES = """
    /* Feed layout */
    .nb-hero {
      text-align: center;
      padding: 32px 16px 24px;
    }
    .nb-hero-title {
      font-size: 38px;
      font-weight: 800;
      letter-spacing: -0.04em;
      background: linear-gradient(135deg, var(--accent, #61dafb), #a78bfa, #f472b6);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .nb-hero-sub {
      color: var(--muted);
      font-size: 14px;
      margin-top: 6px;
    }
    .nb-hero-stats {
      display: flex;
      justify-content: center;
      gap: 24px;
      margin-top: 16px;
      flex-wrap: wrap;
    }
    .nb-hero-stat {
      text-align: center;
    }
    .nb-hero-stat-value {
      font-size: 24px;
      font-weight: 700;
      color: var(--wk-text);
    }
    .nb-hero-stat-label {
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .nb-feed {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
"""
