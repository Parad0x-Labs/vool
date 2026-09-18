from __future__ import annotations

"""VoolBook feed layout and post card styles."""

VOOLBOOK_FEED_LAYOUT_STYLES = r"""
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  color: var(--text);
  min-height: 100vh;
  position: relative;
}
.nb-layout {
  width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 26px 0 40px;
  display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 28px;
}
@media (max-width: 840px) { .nb-layout { grid-template-columns: 1fr; } .nb-sidebar { order: -1; } }

.nb-feed { display: flex; flex-direction: column; gap: 14px; }

.nb-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 16px; padding: 20px 22px;
  transition: border-color 0.25s, box-shadow 0.25s;
}
.nb-card:hover { border-color: var(--border-hover); box-shadow: var(--glow); }
.nb-card--ghost { overflow: hidden; position: relative; }
.nb-card--ghost::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.36), transparent);
  transform: translateX(-100%);
  animation: shimmer 1.35s ease-in-out infinite;
}
@keyframes shimmer {
  100% { transform: translateX(100%); }
}

.nb-post-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.nb-avatar {
  width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 16px;
}
.nb-avatar--agent { background: linear-gradient(135deg, #d7ede8, #b9ddd6); color: #0f766e; }
.nb-avatar--research { background: linear-gradient(135deg, #dff2e3, #c5e8cf); color: #15803d; }
.nb-avatar--claim { background: linear-gradient(135deg, #f5e4cf, #efd2ad); color: #b45309; }
.nb-avatar--solve { background: linear-gradient(135deg, #ece0fb, #dcc7fa); color: #7c3aed; }
.nb-post-author { font-weight: 700; font-size: 14px; color: var(--text); }
.nb-post-meta { font-size: 12px; color: var(--text-dim); margin-top: 1px; }
.nb-post-body {
  font-size: 14px; line-height: 1.7; color: var(--text);
  white-space: pre-wrap; word-wrap: break-word;
}
.nb-post-body strong { color: var(--accent); font-weight: 600; }
.nb-card--clickable { cursor: pointer; }
.nb-card-kicker {
  font-size: 11px; font-weight: 800; text-transform: uppercase;
  letter-spacing: 0.9px; color: var(--text-dim); margin-bottom: 10px;
}
.nb-card-title-row {
  display: flex; gap: 10px; justify-content: space-between; align-items: flex-start; flex-wrap: wrap;
}
.nb-card-title {
  font-family: var(--font-display);
  font-size: 22px; line-height: 1.2; color: var(--text);
}
.nb-card-summary {
  margin-top: 12px; font-size: 14px; line-height: 1.65; color: var(--text-muted);
  white-space: pre-wrap; word-wrap: break-word;
}
.nb-card-meta-row {
  display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px;
}
.nb-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 10px; border-radius: 8px; font-size: 11px; font-weight: 700;
  color: var(--text-muted); background: rgba(255,255,255,0.03); border: 1px solid var(--border);
  text-transform: uppercase; letter-spacing: 0.08em; font-family: var(--font-mono);
}
.nb-chip--ok { color: var(--green); border-color: rgba(116,198,157,0.28); background: rgba(116,198,157,0.08); }
.nb-chip--warn { color: var(--orange); border-color: rgba(210,122,61,0.28); background: rgba(210,122,61,0.08); }
.nb-chip--accent { color: var(--accent); border-color: rgba(196,125,66,0.28); background: rgba(196,125,66,0.08); }
.nb-surface-empty-title {
  font-family: var(--font-display);
  font-size: 28px; line-height: 1.1; color: var(--text); margin-bottom: 10px;
}
.nb-surface-empty-copy { font-size: 14px; color: var(--text-muted); line-height: 1.7; max-width: 560px; }
"""
