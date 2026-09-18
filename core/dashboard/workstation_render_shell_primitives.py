from __future__ import annotations

"""Theme aliases and base shell primitives for the workstation dashboard styles."""

WORKSTATION_RENDER_SHELL_PRIMITIVES = """
    :root {
      --bg: var(--wk-bg);
      --panel: var(--wk-panel);
      --panel-alt: var(--wk-panel-soft);
      --ink: var(--wk-text);
      --muted: var(--wk-muted);
      --line: var(--wk-line);
      --accent: var(--wk-accent);
      --accent-soft: var(--wk-chip-strong);
      --accent-strong: var(--wk-accent-strong);
      --ok: var(--wk-good);
      --warn: var(--wk-warn);
      --chip: var(--wk-chip);
      --shadow: var(--wk-shadow);
    }
    * { box-sizing: border-box; }
    body {
      font-family: var(--wk-font-ui);
      color: var(--ink);
    }
    .shell {
      max-width: none;
      margin: 0;
      padding: 0;
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.8fr);
      gap: 16px;
      margin-bottom: 18px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 18px;
    }
    .eyebrow {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--muted);
      margin-bottom: 8px;
    }
    h1 {
      margin: 0;
      font-size: clamp(28px, 4vw, 52px);
      line-height: 1.02;
    }
    .lede {
      margin: 12px 0 0;
      max-width: 64ch;
      line-height: 1.5;
      color: var(--muted);
      font-size: 15px;
    }
    .inline-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 8px 11px;
      font-size: 12px;
      background: var(--chip);
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .pill.live {
      background: var(--accent-soft);
      color: var(--accent-strong);
      border-color: #b9e5df;
    }
    .meta-grid {
      display: grid;
      gap: 12px;
    }
    .meta-row {
      display: grid;
      grid-template-columns: 92px 1fr;
      gap: 10px;
      align-items: start;
      font-size: 14px;
    }
    .meta-label {
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 11px;
      margin-top: 3px;
    }
    .small {
      font-size: 12px;
      color: var(--muted);
    }
    .loading-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent, #61dafb);
      animation: pulse-dot 1.2s ease-in-out infinite;
      margin-right: 6px;
      vertical-align: middle;
    }
    @keyframes pulse-dot {
      0%, 100% { opacity: 0.3; transform: scale(0.85); }
      50% { opacity: 1; transform: scale(1.15); }
    }
    .mono {
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      word-break: break-all;
    }
"""
