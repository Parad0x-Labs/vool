RUNTIME_TASK_RAIL_EVENT_FEED_STYLES = """
    .feed-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      min-height: 0;
    }
    .event-feed,
    .inspector {
      min-height: 0;
      overflow: auto;
    }
    .event-feed {
      padding: 18px 20px 24px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .event-card {
      border: 1px solid rgba(154, 171, 212, 0.14);
      border-radius: 16px;
      background:
        linear-gradient(135deg, rgba(255,255,255,0.03), transparent 55%),
        rgba(255,255,255,0.02);
      padding: 16px 18px;
      position: relative;
      overflow: hidden;
      cursor: pointer;
      transition: transform 0.14s ease, border-color 0.14s ease, background 0.14s ease;
    }
    .event-card::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--accent);
      opacity: 0.9;
    }
    .event-card.tool_failed::before,
    .event-card.task_failed::before { background: var(--bad); }
    .event-card.tool_preview::before,
    .event-card.task_pending_approval::before { background: var(--warn); }
    .event-card.task_interrupted::before { background: #ffb15c; }
    .event-card.task_completed::before,
    .event-card.tool_loop_completed::before { background: var(--good); }
    .event-card.tool_started::before { background: var(--accent-2); }
    .event-card:hover,
    .event-card.is-active {
      transform: translateY(-1px);
      border-color: rgba(110, 231, 255, 0.34);
      background:
        linear-gradient(135deg, rgba(97, 218, 251, 0.06), transparent 55%),
        rgba(255,255,255,0.03);
    }
    .event-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 10px;
      flex-wrap: wrap;
    }
    .event-type {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--accent);
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .event-time {
      font-size: 12px;
      color: var(--muted);
      font-family: var(--font-mono);
    }
    .event-message {
      font-size: 15px;
      line-height: 1.55;
      margin-bottom: 12px;
      white-space: pre-wrap;
    }
    .event-meta {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .inspector {
      border-left: 1px solid var(--line);
      padding: 18px 20px 24px;
      background: rgba(255,255,255,0.02);
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .inspector-card {
      border-radius: 16px;
      border: 1px solid var(--line-strong);
      background: rgba(255,255,255,0.03);
      padding: 14px;
    }
    .inspector-card h3 {
      margin: 0 0 10px;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--accent);
    }
    .inspector-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .inspector-item {
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(154, 171, 212, 0.12);
      font-size: 13px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .inspector-item code {
      font-family: var(--font-mono);
      color: var(--text);
      font-size: 12px;
    }
    .empty-state {
      padding: 40px 24px;
      color: var(--muted);
      text-align: center;
      line-height: 1.6;
    }
    .link-line {
      margin-top: 12px;
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--muted);
    }
"""
