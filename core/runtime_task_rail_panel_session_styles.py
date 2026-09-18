from __future__ import annotations

"""Task rail session list and badge styles."""

RUNTIME_TASK_RAIL_PANEL_SESSION_STYLES = """
    .session-list {
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-height: calc(100vh - 180px);
      overflow: auto;
    }
    .session-card {
      border: 1px solid rgba(154, 171, 212, 0.12);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.025);
      cursor: pointer;
      transition: transform 0.14s ease, border-color 0.14s ease, background 0.14s ease;
    }
    .session-card:hover,
    .session-card.active {
      transform: translateY(-1px);
      border-color: rgba(110, 231, 255, 0.42);
      background: rgba(110, 231, 255, 0.08);
    }
    .session-top {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
    }
    .session-id {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--muted);
      word-break: break-all;
    }
    .badge {
      padding: 5px 9px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      border: 1px solid transparent;
      white-space: nowrap;
    }
    .badge.running { color: var(--accent); background: rgba(110,231,255,0.12); border-color: rgba(110,231,255,0.2); }
    .badge.completed { color: var(--good); background: rgba(94,240,168,0.12); border-color: rgba(94,240,168,0.2); }
    .badge.request_done { color: var(--warn); background: rgba(255,209,102,0.12); border-color: rgba(255,209,102,0.2); }
    .badge.researching { color: var(--accent-2); background: rgba(255,148,102,0.12); border-color: rgba(255,148,102,0.22); }
    .badge.solved { color: var(--good); background: rgba(94,240,168,0.12); border-color: rgba(94,240,168,0.2); }
    .badge.failed { color: var(--bad); background: rgba(255,107,122,0.12); border-color: rgba(255,107,122,0.2); }
    .badge.pending_approval { color: var(--warn); background: rgba(255,209,102,0.12); border-color: rgba(255,209,102,0.2); }
    .badge.interrupted { color: #ffb15c; background: rgba(255,177,92,0.12); border-color: rgba(255,177,92,0.22); }
    .session-preview {
      font-size: 15px;
      line-height: 1.45;
      margin-bottom: 10px;
    }
    .session-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
"""
