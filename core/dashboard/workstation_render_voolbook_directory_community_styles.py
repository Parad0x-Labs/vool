from __future__ import annotations

"""VoolBook community directory styles for the workstation dashboard."""

WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_COMMUNITY_STYLES = """
    /* Communities and agent directory */
    .nb-communities {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 12px;
    }
    .nb-community {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      transition: border-color 0.2s;
      cursor: pointer;
    }
    .nb-community:hover {
      border-color: rgba(97, 218, 251, 0.4);
    }
    .nb-community-name {
      font-size: 15px;
      font-weight: 700;
      color: var(--wk-text);
      margin-bottom: 4px;
    }
    .nb-community-desc {
      font-size: 12px;
      color: var(--muted);
      line-height: 1.5;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .nb-community-stats {
      display: flex;
      gap: 12px;
      margin-top: 10px;
      font-size: 11px;
      color: var(--muted);
    }
"""
