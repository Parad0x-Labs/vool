from __future__ import annotations

"""Learning-program and fold-card styles for the workstation dashboard shell."""

WORKSTATION_RENDER_SHELL_LEARNING_STYLES = """
    .mini-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .learning-program {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .learning-program summary {
      list-style: none;
      cursor: pointer;
      padding: 18px;
      display: grid;
      gap: 12px;
      background: var(--panel);
    }
    .learning-program summary::-webkit-details-marker {
      display: none;
    }
    .learning-program summary:hover,
    .learning-program[open] summary {
      background: var(--panel-alt);
    }
    .learning-program-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    .learning-program-title {
      margin: 0;
      font-size: 19px;
    }
    .learning-program-body {
      border-top: 1px solid var(--line);
      padding: 18px;
      display: grid;
      gap: 16px;
      background: var(--panel);
    }
    .learning-program-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }
    .learning-program-grid.wide {
      grid-template-columns: 1fr;
    }
    .mini-stat {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      background: var(--panel);
    }
    .mini-stat strong {
      display: block;
      font-size: 24px;
      margin-bottom: 4px;
    }
    .fold-card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      overflow: hidden;
    }
    .fold-card summary {
      list-style: none;
      cursor: pointer;
      padding: 12px 14px;
      display: grid;
      gap: 8px;
      background: var(--panel);
    }
    .fold-card summary::-webkit-details-marker {
      display: none;
    }
    .fold-card summary:hover,
    .fold-card[open] summary {
      background: var(--panel-alt);
    }
    .fold-title-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    .fold-title {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      line-height: 1.35;
      color: var(--ink);
    }
    .fold-stamp {
      flex: 0 0 auto;
      font-size: 11px;
      color: var(--muted);
      text-align: right;
      white-space: nowrap;
    }
    .fold-preview {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .fold-body {
      border-top: 1px solid var(--line);
      padding: 12px 14px;
      display: grid;
      gap: 10px;
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
    }
    .body-pre {
      margin: 0;
      white-space: pre-wrap;
      line-height: 1.55;
      color: var(--muted);
      font-size: 13px;
    }
    .list-note {
      color: var(--muted);
      font-size: 12px;
      margin: 0 0 2px;
    }
    .empty {
      color: var(--muted);
      font-style: italic;
    }
"""
