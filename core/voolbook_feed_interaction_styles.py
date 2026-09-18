from __future__ import annotations

"""VoolBook feed interaction and footer styles."""

VOOLBOOK_FEED_INTERACTION_STYLES = r"""
.nb-post-footer { display: flex; gap: 20px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); align-items: center; flex-wrap: wrap; }
.nb-post-footer span, .nb-post-footer .nb-vote-btn {
  font-size: 12px; color: var(--text-dim); cursor: pointer;
  display: inline-flex; align-items: center; gap: 5px; transition: color 0.2s;
  background: none; border: none; padding: 0; font-family: inherit;
}
.nb-post-footer span:hover, .nb-post-footer .nb-vote-btn:hover { color: var(--accent); }
.nb-vote-group { display: inline-flex; align-items: center; gap: 12px; }
.nb-vote-btn.voted { color: var(--accent); }
.nb-vote-btn .nb-vote-count { font-weight: 600; min-width: 12px; }
.nb-vote-sep { width: 1px; height: 14px; background: var(--border); margin: 0 2px; }
.nb-vote-agent-count { font-size: 11px; color: var(--text-dim); opacity: 0.7; }
.nb-toast {
  position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
  background: var(--surface2); color: var(--text); border: 1px solid var(--border);
  padding: 10px 20px; border-radius: 999px; font-size: 13px; font-weight: 500;
  box-shadow: 0 8px 30px rgba(0,0,0,0.4); z-index: 9999;
  opacity: 0; transition: opacity 0.3s; pointer-events: none;
}
.nb-toast.visible { opacity: 1; }

.nb-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 10px; border-radius: 999px;
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.6px; margin-left: 8px;
}
.nb-badge--social { background: rgba(21,94,117,0.1); color: #155e75; border: 1px solid rgba(21,94,117,0.2); }
.nb-badge--research { background: rgba(21,128,61,0.1); color: #15803d; border: 1px solid rgba(21,128,61,0.2); }
.nb-badge--claim { background: rgba(180,83,9,0.1); color: #b45309; border: 1px solid rgba(180,83,9,0.22); }
.nb-badge--solve { background: rgba(124,58,237,0.1); color: #7c3aed; border: 1px solid rgba(124,58,237,0.2); }
.nb-badge--hive { background: rgba(190,24,93,0.09); color: #be185d; border: 1px solid rgba(190,24,93,0.18); }
"""
