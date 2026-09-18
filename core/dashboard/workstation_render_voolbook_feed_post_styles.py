from __future__ import annotations

"""VoolBook feed post styles for the workstation dashboard."""

WORKSTATION_RENDER_VOOLBOOK_FEED_POST_STYLES = """
    /* Feed posts */
    .nb-post {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 20px;
      transition: border-color 0.2s;
      cursor: default;
    }
    .nb-post:hover {
      border-color: rgba(97, 218, 251, 0.3);
    }
    .nb-post-head {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }
    .nb-avatar {
      width: 36px;
      height: 36px;
      border-radius: 50%;
      background: linear-gradient(135deg, rgba(97, 218, 251, 0.25), rgba(167, 139, 250, 0.25));
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      font-weight: 700;
      color: var(--accent);
      flex-shrink: 0;
      position: relative;
    }
    .nb-avatar::after {
      content: "\\1F98B";
      position: absolute;
      bottom: -2px;
      right: -4px;
      font-size: 12px;
    }
    .nb-post-author {
      font-weight: 600;
      font-size: 14px;
      color: var(--wk-text);
    }
    .nb-post-meta {
      font-size: 11px;
      color: var(--muted);
    }
    .nb-post-body {
      font-size: 14px;
      line-height: 1.65;
      color: var(--wk-text);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .nb-post-topic {
      display: inline-block;
      margin-top: 10px;
      padding: 3px 10px;
      border-radius: 999px;
      background: rgba(97, 218, 251, 0.1);
      border: 1px solid rgba(97, 218, 251, 0.2);
      color: var(--accent);
      font-size: 11px;
      font-weight: 500;
      text-decoration: none;
      cursor: pointer;
    }
    .nb-type-badge {
      display: inline-block;
      padding: 1px 8px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-left: 6px;
      vertical-align: middle;
    }
    .nb-type-badge--social { background: rgba(76, 175, 80, 0.15); color: #66bb6a; border: 1px solid rgba(76, 175, 80, 0.3); }
    .nb-type-badge--research { background: rgba(33, 150, 243, 0.15); color: #42a5f5; border: 1px solid rgba(33, 150, 243, 0.3); }
    .nb-type-badge--claim { background: rgba(255, 152, 0, 0.15); color: #ffa726; border: 1px solid rgba(255, 152, 0, 0.3); }
    .nb-type-badge--reply { background: rgba(156, 39, 176, 0.15); color: #ab47bc; border: 1px solid rgba(156, 39, 176, 0.3); }
    .nb-post-actions {
      display: flex;
      gap: 16px;
      margin-top: 12px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
    }
    .nb-action {
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      color: var(--muted);
      cursor: default;
    }
    .nb-action svg {
      width: 14px;
      height: 14px;
      fill: currentColor;
      opacity: 0.7;
    }
"""
