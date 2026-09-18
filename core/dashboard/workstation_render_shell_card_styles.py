from __future__ import annotations

"""Card, list, chip, and table styles for the workstation dashboard shell."""

WORKSTATION_RENDER_SHELL_CARD_STYLES = """
    .tab-panel {
      display: none;
      gap: 16px;
    }
    .tab-panel.active {
      display: grid;
    }
    .cols-2 {
      grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
    }
    .subgrid {
      display: grid;
      gap: 14px;
    }
    .section-title {
      margin: 0 0 10px;
      font-size: 20px;
    }
    .list {
      display: grid;
      gap: 10px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      padding: 14px;
    }
    .card-link {
      display: block;
      color: inherit;
      text-decoration: none;
    }
    .card-link:hover h3,
    .card-link:focus-visible h3 {
      color: var(--accent-strong);
    }
    .card h3 {
      margin: 0 0 6px;
      font-size: 17px;
    }
    .card p {
      margin: 0;
      line-height: 1.45;
      color: var(--muted);
      font-size: 14px;
    }
    .row-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
      font-size: 12px;
      color: var(--muted);
    }
    .chip {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 8px;
      background: var(--chip);
      border: 1px solid var(--line);
      font-size: 11px;
    }
    .chip.ok {
      background: rgba(95, 229, 166, 0.12);
      color: var(--ok);
      border-color: rgba(95, 229, 166, 0.24);
    }
    .chip.warn {
      background: rgba(245, 178, 92, 0.12);
      color: var(--warn);
      border-color: rgba(245, 178, 92, 0.26);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.1em;
      font-weight: 600;
    }
"""
