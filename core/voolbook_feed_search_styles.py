from __future__ import annotations

VOOLBOOK_FEED_SEARCH_STYLES = r"""
.nb-empty { text-align: center; padding: 40px 20px; color: var(--text-dim); font-size: 14px; }
.nb-loader { text-align: center; padding: 30px; color: var(--text-dim); }
.nb-loader::after { content: ''; display: inline-block; width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.7s linear infinite; margin-left: 8px; vertical-align: middle; }
@keyframes spin { to { transform: rotate(360deg); } }

.nb-tab-row { display: flex; gap: 4px; margin-bottom: 16px; }
.nb-tab {
  padding: 8px 16px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 600;
  color: var(--text-muted); background: transparent; border: 1px solid transparent;
  cursor: pointer; transition: all 0.2s;
}
.nb-tab:hover { color: var(--text); background: var(--surface); }
.nb-tab.active { color: var(--text); background: var(--surface2); border-color: var(--border); }

.nb-search-wrap {
  position: relative; margin-bottom: 16px;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: rgba(255,255,255,0.02);
}
.nb-search-input {
  width: 100%; padding: 12px 16px 12px 42px;
  background: var(--surface2); border: 1px solid var(--border); border-radius: 10px;
  color: var(--text); font-size: 14px; outline: none; transition: border-color 0.2s, box-shadow 0.2s;
}
.nb-search-input::placeholder { color: var(--text-dim); }
.nb-search-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(196,125,66,0.12); }
.nb-search-icon {
  position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
  color: var(--text-dim); font-size: 16px; pointer-events: none;
}
.nb-search-filters {
  display: flex; gap: 4px; margin-top: 8px;
}
.nb-search-filter {
  padding: 5px 12px; border-radius: 8px; font-size: 11px; font-weight: 600;
  color: var(--text-dim); background: transparent; border: 1px solid var(--border);
  cursor: pointer; transition: all 0.2s; text-transform: uppercase; letter-spacing: 0.08em; font-family: var(--font-mono);
}
.nb-search-filter:hover { color: var(--text-muted); border-color: var(--border-hover); }
.nb-search-filter.active { color: var(--accent); border-color: var(--accent); background: rgba(196,125,66,0.08); }
.nb-search-results { display: none; flex-direction: column; gap: 10px; }
.nb-search-results.visible { display: flex; }
.nb-search-result-section { margin-bottom: 8px; }
.nb-search-result-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.12em;
  color: var(--text-dim); margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid var(--border); font-family: var(--font-mono);
}
.nb-search-result-item {
  padding: 10px 14px; background: var(--surface2); border-radius: 10px;
  border: 1px solid var(--border); margin-bottom: 6px; transition: border-color 0.2s;
}
.nb-search-result-item:hover { border-color: var(--border-hover); }
.nb-search-result-item .sr-title { font-size: 14px; font-weight: 600; color: var(--text); }
.nb-search-result-item .sr-meta { font-size: 12px; color: var(--text-dim); margin-top: 2px; }
.nb-search-result-item .sr-snippet { font-size: 13px; color: var(--text-muted); margin-top: 4px; line-height: 1.5; }
.nb-twitter-link {
  font-size: 12px; color: var(--blue); font-weight: 400; margin-left: 4px;
  opacity: 0.8; transition: opacity 0.2s;
}
.nb-twitter-link:hover { opacity: 1; color: var(--accent2); }

.nb-card .nb-post-footer span, .nb-card .nb-post-footer a, .nb-card .nb-post-footer button { position: relative; z-index: 2; }
"""
