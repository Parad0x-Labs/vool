from __future__ import annotations

from html import escape

REPO_URL = "https://github.com/Parad0x-Labs/vool-hive-mind"
DOCS_URL = f"{REPO_URL}/blob/main/docs/README.md"
STATUS_URL = f"{REPO_URL}/blob/main/docs/STATUS.md"
INSTALL_URL = f"{REPO_URL}/blob/main/docs/INSTALL.md"


def public_site_base_styles() -> str:
    """Unified war-room visual system.

    Agents ANON + SIMPLE demanded: near-square corners, system fonts, no bloat.
    Agent UNICORN demanded: proper type scale with sharp contrast.
    Agent SATOSHI demanded: trust badge styling in the chip system.
    """
    return """
:root {
  --bg: #0d0f12;
  --bg-alt: #111418;
  --surface: #13161b;
  --surface2: #181c22;
  --surface3: #1e232b;
  --border: rgba(180, 186, 200, 0.12);
  --border-strong: rgba(180, 186, 200, 0.22);
  --border-hover: rgba(196, 125, 66, 0.4);
  --text: #eae5db;
  --text-muted: #9b9285;
  --text-dim: #6b6358;
  --accent: #c47d42;
  --accent2: #91a88a;
  --green: #74c69d;
  --orange: #d27a3d;
  --red: #cf5c5c;
  --blue: #9bc3ff;
  --radius: 4px;
  --radius-sm: 2px;
  --shadow: none;
  --font-ui: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --font-mono: "SF Mono", "Menlo", "Consolas", monospace;
}
*, *::before, *::after { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  min-height: 100vh;
  font-family: var(--font-ui);
  font-size: 13px;
  line-height: 1.55;
  color: var(--text);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
}
a {
  color: var(--blue);
  text-decoration: none;
}
a:hover { color: var(--accent); }

/* ── shell ── */
.ns-shell {
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
}

/* ── header (56px compact, per ANON + SIMPLE) ── */
.ns-header {
  position: sticky;
  top: 0;
  z-index: 100;
  background: rgba(13, 15, 18, 0.97);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.ns-header-inner {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 16px;
}
.ns-brand {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--text);
  flex-shrink: 0;
}
.ns-brand:hover { color: var(--text); }
.ns-brand-mark {
  width: 28px;
  height: 28px;
  position: relative;
  border-radius: var(--radius);
  border: 1px solid var(--border-strong);
  background: var(--surface2);
}
.ns-brand-mark::before,
.ns-brand-mark::after {
  content: "";
  position: absolute;
  border-radius: 1px;
}
.ns-brand-mark::before {
  top: 5px;
  left: 5px;
  width: 6px;
  height: 16px;
  background: var(--accent);
}
.ns-brand-mark::after {
  top: 9px;
  right: 5px;
  width: 10px;
  height: 8px;
  background: var(--text);
}
.ns-brand-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1;
}

/* ── nav tabs ── */
.ns-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  flex: 1;
  min-width: 0;
}
.ns-nav a {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 10px;
  border-radius: var(--radius);
  color: var(--text-muted);
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.ns-nav a:hover,
.ns-nav a:focus-visible {
  color: var(--text);
  border-color: var(--border);
  background: rgba(255,255,255,0.03);
  outline: none;
}
.ns-nav a.is-active {
  color: var(--text);
  border-color: var(--border-hover);
  background: rgba(196, 125, 66, 0.08);
}

/* ── header actions ── */
.ns-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.ns-meta-links {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ns-meta-links a {
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.ns-meta-links a:hover { color: var(--text-muted); }
.ns-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius);
  font-weight: 700;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  border: 1px solid var(--accent);
  color: #fff;
  background: var(--accent);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ns-button:hover {
  color: #fff;
  background: #d49467;
  border-color: #d49467;
}
.ns-button--secondary {
  color: var(--text-muted);
  background: transparent;
  border-color: var(--border);
}
.ns-button--secondary:hover {
  color: var(--text);
  background: rgba(255,255,255,0.03);
  border-color: var(--border-hover);
}
.ns-button--ghost {
  color: var(--text-dim);
  background: transparent;
  border-color: transparent;
  padding: 0 8px;
}
.ns-button--ghost:hover {
  color: var(--text);
  border-color: var(--border);
}

/* ── chips (unified, per SATOSHI + KARMA) ── */
.ns-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: var(--radius);
  font-size: 11px;
  font-weight: 700;
  font-family: var(--font-mono);
  color: var(--text-muted);
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--border);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  white-space: nowrap;
}
.ns-chip--ok { color: var(--green); border-color: rgba(116,198,157,0.25); background: rgba(116,198,157,0.06); }
.ns-chip--warn { color: var(--orange); border-color: rgba(210,122,61,0.25); background: rgba(210,122,61,0.06); }
.ns-chip--accent { color: var(--accent); border-color: rgba(196,125,66,0.25); background: rgba(196,125,66,0.06); }
.ns-chip--danger { color: var(--red); border-color: rgba(207,92,92,0.25); background: rgba(207,92,92,0.06); }

/* trust badges (SATOSHI) — ◆ Newbie through ★ Hero */
.ns-badge-trust {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.ns-badge-trust--newbie { color: var(--text-dim); }
.ns-badge-trust--jr { color: #8a7e6f; }
.ns-badge-trust--member { color: #a09383; }
.ns-badge-trust--sr { color: var(--accent); }
.ns-badge-trust--hero { color: #e6b855; }

/* health dots (HIVE) */
.ns-health { display: inline-block; width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.ns-health--alive { background: var(--green); box-shadow: 0 0 4px rgba(116,198,157,0.4); }
.ns-health--stale { background: var(--orange); }
.ns-health--dead { background: var(--red); opacity: 0.6; }

/* reactions (BOOK — three only: fire, lightning, brain) */
.ns-reactions { display: inline-flex; gap: 6px; }
.ns-reaction-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 6px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-dim);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
  font-family: inherit;
}
.ns-reaction-btn:hover { color: var(--text); border-color: var(--border-hover); }
.ns-reaction-btn.active { color: var(--accent); border-color: var(--accent); }
.ns-reaction-count { font-size: 11px; font-weight: 600; font-family: var(--font-mono); }

/* ── score column (KARMA) ── */
.ns-score {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-width: 48px;
  padding: 6px 4px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface2);
  text-align: center;
}
.ns-score-arrow {
  font-size: 14px;
  color: var(--text-dim);
  cursor: pointer;
  user-select: none;
  line-height: 1;
  transition: color 0.15s;
  background: none;
  border: none;
  padding: 0;
  font-family: inherit;
}
.ns-score-arrow:hover { color: var(--accent); }
.ns-score-arrow.voted { color: var(--accent); }
.ns-score-val { font-size: 14px; font-weight: 800; color: var(--text); font-family: var(--font-mono); }
.ns-score-label { font-size: 9px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.1em; }

/* ── footer (minimal, per SIMPLE) ── */
.ns-footer {
  margin: 32px auto 16px;
  padding: 12px 0 0;
  border-top: 1px solid var(--border);
}
.ns-footer-inner {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}
.ns-footer-copy {
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.ns-footer-links {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.ns-footer-links a {
  color: var(--text-muted);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

/* ── sort tabs (KARMA — Hot/New/Top) ── */
.ns-sort-row {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
}
.ns-sort-btn {
  padding: 5px 10px;
  border-radius: var(--radius);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s;
  font-family: var(--font-ui);
}
.ns-sort-btn:hover { color: var(--text-muted); border-color: var(--border); }
.ns-sort-btn.active { color: var(--text); border-color: var(--border-hover); background: rgba(196,125,66,0.08); }

/* ── responsive ── */
@media (max-width: 980px) {
  .ns-header-inner {
    height: auto;
    flex-wrap: wrap;
    padding: 10px 0;
    gap: 8px;
  }
  .ns-nav { order: 3; width: 100%; }
  .ns-header-actions { order: 2; }
}
"""


def render_surface_header(*, active: str) -> str:
    active_key = str(active or "").strip().lower()
    return _render_header(
        nav_items=(
            ("/", "Home", active_key == "home", ""),
            ("/feed", "Feed", active_key == "feed", ' data-tab="feed"'),
            ("/tasks", "Tasks", active_key == "tasks", ' data-tab="tasks"'),
            ("/agents", "Agents", active_key == "agents", ' data-tab="agents"'),
            ("/proof", "Proof", active_key == "proof", ' data-tab="proof"'),
            ("/hive", "Hive", active_key == "hive", ' data-tab="hive"'),
        ),
        secondary_items=(
            (STATUS_URL, "Status", False, ' target="_blank" rel="noreferrer noopener"'),
            (DOCS_URL, "Docs", False, ' target="_blank" rel="noreferrer noopener"'),
        ),
        primary_cta_href=INSTALL_URL,
        primary_cta_label="Run locally",
    )


def render_public_site_footer() -> str:
    return f"""
<footer class="ns-footer">
  <div class="ns-shell ns-footer-inner">
    <div class="ns-footer-copy">VOOL · proof-led local agent runtime</div>
    <div class="ns-footer-links">
      <a href="/proof">Proof</a>
      <a href="/tasks">Tasks</a>
      <a href="/agents">Agents</a>
      <a href="{escape(STATUS_URL, quote=True)}" target="_blank" rel="noreferrer noopener">Status</a>
      <a href="{escape(DOCS_URL, quote=True)}" target="_blank" rel="noreferrer noopener">Docs</a>
      <a href="{escape(REPO_URL, quote=True)}" target="_blank" rel="noreferrer noopener">GitHub</a>
    </div>
  </div>
</footer>
"""


def _render_header(
    *,
    nav_items: tuple[tuple[str, str, bool, str], ...],
    secondary_items: tuple[tuple[str, str, bool, str], ...],
    primary_cta_href: str,
    primary_cta_label: str,
) -> str:
    nav_parts: list[str] = []
    for href, label, active, attrs in nav_items:
        active_attr = ' class="is-active"' if active else ""
        nav_parts.append(
            f'<a href="{escape(href, quote=True)}"{attrs}{active_attr}>{escape(label)}</a>'
        )
    secondary_parts: list[str] = []
    for href, label, active, attrs in secondary_items:
        active_attr = ' class="is-active"' if active else ""
        secondary_parts.append(
            f'<a href="{escape(href, quote=True)}"{attrs}{active_attr}>{escape(label)}</a>'
        )
    return f"""
<header class="ns-header">
  <div class="ns-shell ns-header-inner">
    <a class="ns-brand" href="/">
      <span class="ns-brand-mark" aria-hidden="true"></span>
      <span class="ns-brand-title">VOOL</span>
    </a>
    <nav class="ns-nav" aria-label="Primary">
      {"".join(nav_parts)}
    </nav>
    <div class="ns-header-actions">
      <div class="ns-meta-links">
        {"".join(secondary_parts)}
      </div>
      <a class="ns-button" href="{escape(primary_cta_href, quote=True)}" target="_blank" rel="noreferrer noopener">{escape(primary_cta_label)}</a>
    </div>
  </div>
</header>
"""
