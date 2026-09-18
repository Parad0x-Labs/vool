from __future__ import annotations

VOOLBOOK_FEED_OVERLAY_STYLES = r"""
.nb-overlay {
  position: fixed; inset: 0; z-index: 500;
  background: rgba(0,0,0,0.7); backdrop-filter: blur(6px);
  display: flex; justify-content: center; align-items: flex-start;
  padding: 48px 20px; overflow-y: auto;
  animation: fadeIn 0.15s ease;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.nb-overlay-inner {
  width: 100%; max-width: 680px; position: relative;
}
.nb-overlay-close {
  position: absolute; top: -36px; right: 0;
  font-size: 14px; color: var(--text-muted); cursor: pointer; background: none; border: none;
  padding: 4px 10px; border-radius: 6px; transition: color 0.2s;
}
.nb-overlay-close:hover { color: var(--text); }
.nb-detail-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 24px 28px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.5);
}
.nb-detail-card .nb-post-body { font-size: 16px; line-height: 1.8; }
.nb-detail-card .nb-post-author { font-size: 16px; }
.nb-detail-card .nb-avatar { width: 48px; height: 48px; font-size: 20px; }
.nb-replies-section {
  margin-top: 16px; border-top: 1px solid var(--border); padding-top: 16px;
}
.nb-replies-title { font-size: 13px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 12px; }
.nb-reply-card {
  background: var(--surface2); border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 14px 16px; margin-bottom: 10px;
}
.nb-reply-card .nb-post-body { font-size: 13px; line-height: 1.6; }
.nb-reply-card .nb-post-author { font-size: 13px; }
.nb-reply-card .nb-avatar { width: 28px; height: 28px; font-size: 12px; }
.nb-no-replies { font-size: 13px; color: var(--text-dim); text-align: center; padding: 20px 0; }
"""
