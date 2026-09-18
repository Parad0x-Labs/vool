from __future__ import annotations

WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_TOPIC_STYLES = """
    .nb-timeline {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .nb-tl-topic {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
    }
    .nb-tl-topic-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }
    .nb-tl-topic-title {
      font-weight: 700;
      font-size: 15px;
      color: var(--ink);
    }
    .nb-tl-badge {
      display: inline-block;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 3px 8px;
      border-radius: 999px;
    }
    .nb-tl-badge--solved { background: rgba(52, 211, 153, 0.15); color: #34d399; }
    .nb-tl-badge--open { background: rgba(97, 218, 251, 0.15); color: #61dafb; }
    .nb-tl-badge--researching { background: rgba(251, 191, 36, 0.15); color: #fbbf24; }
    .nb-tl-badge--disputed { background: rgba(244, 114, 182, 0.15); color: #f472b6; }
"""
