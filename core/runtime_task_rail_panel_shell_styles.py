from __future__ import annotations

"""Task rail shell and title styles."""

RUNTIME_TASK_RAIL_PANEL_SHELL_STYLES = """
    :root {
      --bg: var(--wk-bg);
      --bg-2: var(--wk-bg-alt);
      --panel: var(--wk-panel);
      --panel-2: var(--wk-panel-strong);
      --line: var(--wk-line);
      --line-strong: var(--wk-line-strong);
      --text: var(--wk-text);
      --muted: var(--wk-muted);
      --accent: var(--wk-accent);
      --accent-2: var(--wk-warn);
      --good: var(--wk-good);
      --warn: var(--wk-warn);
      --bad: var(--wk-bad);
      --shadow: var(--wk-shadow);
      --radius: var(--wk-radius);
      --font-ui: var(--wk-font-ui);
      --font-mono: var(--wk-font-mono);
    }
    * { box-sizing: border-box; }
    body {
      font-family: var(--font-ui);
      color: var(--text);
    }
    .panel {
      background: linear-gradient(180deg, rgba(18, 29, 54, 0.96), rgba(9, 14, 27, 0.98));
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .panel-header {
      padding: 18px 20px 14px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent);
    }
    .eyebrow {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--accent);
      margin-bottom: 8px;
    }
    .title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .title {
      font-size: 28px;
      font-weight: 700;
      letter-spacing: -0.04em;
    }
    .status-pill {
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 12px;
      background: rgba(110, 231, 255, 0.12);
      color: var(--accent);
      border: 1px solid rgba(110, 231, 255, 0.2);
    }
    .subtitle {
      color: var(--muted);
      margin-top: 10px;
      line-height: 1.5;
    }
"""
