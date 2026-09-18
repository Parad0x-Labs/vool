from __future__ import annotations

"""Task rail trace-stage and body styles."""

RUNTIME_TASK_RAIL_PANEL_TRACE_STYLES = """
    .trace-stage-head {
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      background: linear-gradient(180deg, rgba(255,255,255,0.03), transparent);
    }
    .trace-stage-head h2 {
      margin: 0;
      font-size: 28px;
      letter-spacing: -0.04em;
      line-height: 1.05;
    }
    .trace-stage-copy {
      margin: 8px 0 0;
      color: var(--muted);
      line-height: 1.55;
      max-width: 72ch;
      font-size: 14px;
    }
    .trace-stage-proof {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }
    .trace-stage-proof .wk-proof-chip {
      white-space: nowrap;
    }
    .rail-body {
      min-height: calc(100vh - 56px);
      display: grid;
      grid-template-rows: auto auto auto 1fr;
    }
"""
