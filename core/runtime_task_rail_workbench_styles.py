RUNTIME_TASK_RAIL_WORKBENCH_STYLES = """
    .trace-workbench {
      display: grid;
      grid-template-columns: minmax(280px, 320px) minmax(0, 1.45fr) minmax(300px, 360px);
      gap: 16px;
      align-items: start;
    }
    .trace-rail-shell,
    .trace-summary-shell {
      position: sticky;
      top: 18px;
      align-self: start;
      min-height: calc(100vh - 36px);
      background:
        linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.01)),
        var(--panel-2);
    }
    .trace-center-shell {
      background:
        linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01)),
        rgba(9, 15, 28, 0.96);
    }
    .trace-rail-shell,
    .trace-summary-shell {
      padding: 16px;
    }
    .trace-rail-stack,
    .trace-summary-stack {
      display: grid;
      gap: 14px;
    }
    .trace-rail-section,
    .trace-summary-section {
      display: grid;
      gap: 10px;
    }
    .trace-selected-step {
      display: grid;
      gap: 10px;
    }
    .trace-selected-step h3 {
      margin: 0;
      font-size: 18px;
      letter-spacing: -0.02em;
    }
    .trace-selected-step p {
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }
    .trace-selected-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .trace-feed-grid {
      display: block;
      min-height: 0;
    }
    .trace-feed-grid .event-feed {
      min-height: 0;
    }
    .trace-raw-panel {
      display: none;
      padding: 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.24);
      color: var(--text);
      font-family: var(--font-mono);
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      overflow: auto;
      max-height: 42vh;
    }
    body[data-view-mode="raw"] .trace-raw-panel {
      display: block;
    }
    body[data-view-mode="raw"] .trace-human,
    body[data-view-mode="raw"] .trace-agent {
      display: none;
    }
    body[data-view-mode="agent"] .trace-human[data-human-optional="1"] {
      display: none;
    }
    body[data-view-mode="human"] .trace-agent[data-agent-optional="1"] {
      display: none;
    }
    @media (max-width: 1200px) {
      .trace-workbench {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 980px) {
      .session-list {
        max-height: 320px;
      }
      .rail-body {
        min-height: unset;
      }
    }
    @media (max-width: 640px) {
      .summary-grid,
      .trace-strip,
      .ops-grid {
        grid-template-columns: 1fr;
      }
    }
"""
