RUNTIME_TASK_RAIL_TRACE_STYLES = """
    .detail-block {
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
    }
    .detail-main h2 {
      margin: 0 0 8px;
      font-size: 24px;
      letter-spacing: -0.03em;
    }
    .detail-main p {
      margin: 0;
      color: var(--muted);
      max-width: 900px;
      line-height: 1.55;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 16px;
    }
    .stat-card {
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.035);
      border: 1px solid var(--line);
    }
    .stat-card strong {
      display: block;
      font-size: 18px;
      line-height: 1.2;
      margin-bottom: 6px;
      overflow-wrap: anywhere;
    }
    .stat-card span {
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .stat-card code {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--text);
    }
    .trace-strip {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .ops-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .ops-card {
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.035);
      border: 1px solid var(--line);
      min-height: 142px;
    }
    .ops-card.good {
      border-color: rgba(94, 240, 168, 0.26);
      background: rgba(94, 240, 168, 0.08);
    }
    .ops-card.warn {
      border-color: rgba(255, 209, 102, 0.28);
      background: rgba(255, 209, 102, 0.09);
    }
    .ops-card.bad {
      border-color: rgba(255, 107, 122, 0.3);
      background: rgba(255, 107, 122, 0.09);
    }
    .ops-card span {
      display: block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .ops-card strong {
      display: block;
      font-size: 20px;
      line-height: 1.2;
      margin-bottom: 8px;
      overflow-wrap: anywhere;
    }
    .ops-card p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }
    .trace-stage {
      border-radius: 16px;
      border: 1px solid var(--line);
      padding: 14px;
      background: rgba(255,255,255,0.02);
      min-height: 112px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .trace-stage.active {
      border-color: rgba(110, 231, 255, 0.36);
      background: rgba(110, 231, 255, 0.08);
    }
    .trace-stage.done {
      border-color: rgba(94, 240, 168, 0.26);
      background: rgba(94, 240, 168, 0.08);
    }
    .trace-stage.failed {
      border-color: rgba(255, 107, 122, 0.3);
      background: rgba(255, 107, 122, 0.08);
    }
    .trace-stage .stage-label {
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .trace-stage .stage-value {
      font-size: 18px;
      font-weight: 700;
      line-height: 1.25;
    }
    .trace-stage .stage-detail {
      font-size: 13px;
      line-height: 1.45;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .meta-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }
    .meta-chip {
      font-size: 12px;
      color: var(--muted);
      font-family: var(--font-mono);
      padding: 7px 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(154, 171, 212, 0.12);
    }
    .meta-chip.route {
      color: #7fd7ff;
      background: rgba(108, 204, 255, 0.10);
      border-color: rgba(108, 204, 255, 0.45);
      font-weight: 600;
    }
"""
