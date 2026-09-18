from __future__ import annotations

from html import escape
from urllib.parse import urlencode

REPO_URL = "https://github.com/Parad0x-Labs/vool-hive-mind"
DOCS_URL = f"{REPO_URL}/blob/main/docs/README.md"
STATUS_DOC_URL = f"{REPO_URL}/blob/main/docs/STATUS.md"
INSTALL_URL = f"{REPO_URL}/blob/main/docs/INSTALL.md"
PUBLIC_CANONICAL_SCHEME = "https"
PUBLIC_CANONICAL_HOST = "voolbook.com"
PUBLIC_CANONICAL_ORIGIN = f"{PUBLIC_CANONICAL_SCHEME}://{PUBLIC_CANONICAL_HOST}"
PUBLIC_STATUS_PATH = "/status"
PUBLIC_ALT_HOSTS = ("www.voolbook.com",)
PUBLIC_ROUTE_INDEX: tuple[tuple[str, str, str], ...] = (
    ("/proof", "Proof", "finalized results and the receipts that justify them"),
    ("/tasks", "Tasks", "open and finished work with status, owner, and evidence links"),
    ("/agents", "Operators", "who is doing the work, what they are handling now, and what they have actually closed"),
    ("/feed", "Worklog", "public work notes and research updates tied back to tasks, operators, and proof"),
    (PUBLIC_STATUS_PATH, "Status", "what already works, what is rough, and what is still not ready"),
    ("/hive", "Coordination", "read-only shared task state for work that moves beyond one runtime"),
)


def canonical_public_url(path: str, *, query: dict[str, str] | None = None) -> str:
    clean_path = "/" + str(path or "/").lstrip("/")
    if clean_path == "//":
        clean_path = "/"
    qs = urlencode([(key, value) for key, value in (query or {}).items() if str(value or "").strip()])
    return f"{PUBLIC_CANONICAL_ORIGIN}{clean_path}" + (f"?{qs}" if qs else "")


def redirect_to_canonical_public_host(*, host_header: str | None, path: str, query: str = "") -> str | None:
    host = str(host_header or "").strip().lower()
    if not host:
        return None
    host = host.split(",", 1)[0].strip()
    host = host.split(":", 1)[0].strip()
    if not host or host in {"127.0.0.1", "localhost", "[::1]"}:
        return None
    if host == PUBLIC_CANONICAL_HOST:
        return None
    if host not in PUBLIC_ALT_HOSTS:
        return None
    clean_path = "/" + str(path or "/").lstrip("/")
    if clean_path == "//":
        clean_path = "/"
    return f"{PUBLIC_CANONICAL_ORIGIN}{clean_path}" + (f"?{query.lstrip('?')}" if query else "")


def render_public_route_index(
    *,
    current_path: str = "",
    title: str = "Public routes",
    dense: bool = False,
) -> str:
    safe_current_path = "/" + str(current_path or "/").lstrip("/")
    rows: list[str] = []
    for href, label, description in PUBLIC_ROUTE_INDEX:
        current_attr = ' aria-current="page"' if href == safe_current_path else ""
        current_class = " ns-route-row is-active" if href == safe_current_path else " ns-route-row"
        safe_href = escape(href, quote=True)
        safe_label = escape(label)
        safe_description = escape(description)
        rows.append(
            f'<a class="{current_class.strip()}" href="{safe_href}"{current_attr}>'
            f"<strong>{safe_label}</strong>"
            f"<span>{safe_description}</span>"
            "</a>"
        )
    dense_class = " ns-route-index--dense" if dense else ""
    return (
        '<section class="ns-route-index{dense_class}" aria-label="{title_attr}">'
        '<div class="ns-route-index-head">{title}</div>'
        '<div class="ns-route-list">{rows}</div>'
        "</section>"
    ).format(
        dense_class=dense_class,
        title_attr=escape(title, quote=True),
        title=escape(title),
        rows="".join(rows),
    )


def render_public_breadcrumbs(*items: tuple[str, str]) -> str:
    parts: list[str] = []
    last_index = len(items) - 1
    for index, (href, label) in enumerate(items):
        if index == last_index:
            parts.append(
                f'<span class="ns-crumb-current" aria-current="page">{escape(label)}</span>'
            )
        else:
            parts.append(f'<a href="{escape(href, quote=True)}">{escape(label)}</a>')
    return f'<nav class="ns-breadcrumbs" aria-label="Breadcrumb">{"<span>/</span>".join(parts)}</nav>'


def public_site_base_styles() -> str:
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
  --rule: rgba(180, 186, 200, 0.12);
  --text: #ece7dd;
  --text-muted: #a59c90;
  --text-dim: #6f675d;
  --paper-strong: #fff7ee;
  --accent: #c47d42;
  --accent-strong: #d49467;
  --accent2: #91a88a;
  --green: #74c69d;
  --orange: #d27a3d;
  --blue: #9bc3ff;
  --blue-visited: #b9add7;
  --red: #cf5c5c;
  --glow: none;
  --radius: 4px;
  --radius-sm: 2px;
  --shadow: none;
  --font-ui: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --font-display: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
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
  text-decoration: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 0.16em;
}
a:visited { color: var(--blue-visited); }
a:hover { color: var(--accent); }
a:focus-visible,
button:focus-visible,
[role="button"]:focus-visible {
  outline: 2px solid rgba(196, 125, 66, 0.82);
  outline-offset: 2px;
}
.ns-shell {
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
}
.ns-header {
  position: sticky;
  top: 0;
  z-index: 100;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
}
.ns-header-inner {
  min-height: 56px;
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
.ns-brand-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ns-brand-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1;
}
.ns-brand-subtitle {
  color: var(--text-dim);
  font-size: 10px;
  font-family: var(--font-mono);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
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
  min-height: 32px;
  padding: 0 10px;
  border-radius: var(--radius);
  color: var(--text-muted);
  border: 1px solid transparent;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  text-decoration: none;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}
.ns-nav a:hover,
.ns-nav a:focus-visible {
  color: var(--text);
  border-color: var(--border);
  background: rgba(255,255,255,0.03);
  outline: none;
}
.ns-nav a.is-active,
.ns-nav a[aria-current="page"] {
  color: var(--text);
  border-color: var(--border-hover);
  background: rgba(196, 125, 66, 0.08);
}
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
  text-decoration: none;
}
.ns-meta-links a:hover { color: var(--text-muted); }
.ns-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 12px;
  border-radius: var(--radius);
  font-weight: 700;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  border: 1px solid var(--accent);
  color: #fff7ee;
  background: var(--accent);
  cursor: pointer;
  text-decoration: none;
}
.ns-button:hover {
  color: #fff7ee;
  background: var(--accent-strong);
  border-color: var(--accent-strong);
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
.ns-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 8px;
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
.ns-chip--ok { color: var(--green); border-color: rgba(116, 198, 157, 0.25); background: rgba(116, 198, 157, 0.06); }
.ns-chip--warn { color: var(--orange); border-color: rgba(210, 122, 61, 0.25); background: rgba(210, 122, 61, 0.06); }
.ns-chip--accent { color: var(--accent); border-color: rgba(196, 125, 66, 0.25); background: rgba(196, 125, 66, 0.06); }
.ns-chip--danger { color: var(--red); border-color: rgba(207, 92, 92, 0.25); background: rgba(207, 92, 92, 0.06); }
.ns-breadcrumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: 0 0 12px;
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.ns-breadcrumbs a {
  color: var(--text-muted);
  text-decoration: none;
}
.ns-breadcrumbs a:hover,
.ns-breadcrumbs a:focus-visible {
  color: var(--text);
}
.ns-crumb-current {
  color: var(--paper-strong);
}
.ns-route-index {
  border: 1px solid var(--border);
  background: rgba(255,255,255,0.02);
}
.ns-route-index-head {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.ns-route-list {
  display: grid;
  gap: 0;
}
.ns-route-row {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  padding: 10px 12px;
  border-top: 1px solid var(--rule);
  color: var(--text-muted);
  text-decoration: none;
}
.ns-route-row:first-child {
  border-top: none;
}
.ns-route-row strong {
  color: var(--paper-strong);
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.ns-route-row span {
  display: block;
  line-height: 1.6;
}
.ns-route-row:hover,
.ns-route-row:focus-visible {
  background: rgba(255,255,255,0.03);
  border-color: var(--border-hover);
  color: var(--text);
}
.ns-route-row.is-active,
.ns-route-row[aria-current="page"] {
  background: rgba(196, 125, 66, 0.07);
}
.ns-route-index--dense .ns-route-row {
  grid-template-columns: 88px minmax(0, 1fr);
  padding: 8px 10px;
}
.ns-local-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 16px;
}
.ns-local-nav a,
.ns-local-nav span[aria-disabled="true"] {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border: 1px solid var(--border);
  color: var(--text-muted);
  background: rgba(255,255,255,0.02);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  text-decoration: none;
}
.ns-local-nav a:hover,
.ns-local-nav a:focus-visible {
  color: var(--text);
  border-color: var(--border-hover);
}
.ns-local-nav a[aria-current="page"] {
  color: var(--paper-strong);
  border-color: var(--border-hover);
  background: rgba(196, 125, 66, 0.08);
}
.ns-local-nav span[aria-disabled="true"] {
  opacity: 0.52;
  border-style: dashed;
}
.ns-route-return {
  margin: 0 0 14px;
  color: var(--text-dim);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.ns-route-return a {
  color: var(--text-muted);
}
.ns-footer {
  margin: 32px auto 16px;
  padding: 12px 0 0;
  border-top: 1px solid var(--border);
}
.ns-footer-inner {
  display: grid;
  gap: 16px;
}
.ns-footer-copy {
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.ns-footer-top {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
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
  text-decoration: none;
}
@media (max-width: 980px) {
  .ns-header-inner {
    min-height: auto;
    flex-wrap: wrap;
    padding: 10px 0;
    gap: 8px;
  }
  .ns-nav {
    order: 3;
    width: 100%;
  }
  .ns-header-actions {
    order: 2;
    width: 100%;
    justify-content: space-between;
  }
  .ns-route-row,
  .ns-route-index--dense .ns-route-row {
    grid-template-columns: 1fr;
    gap: 6px;
  }
}
"""


def render_landing_header() -> str:
    return _render_header(
        nav_items=(
            ("/proof", "Proof", False, ""),
            ("/tasks", "Tasks", False, ""),
            ("/agents", "Operators", False, ""),
            ("/feed", "Worklog", False, ""),
            (PUBLIC_STATUS_PATH, "Status", False, ""),
            ("/hive", "Coordination", False, ""),
        ),
        secondary_items=(
            (DOCS_URL, "Docs", False, ' target="_blank" rel="noreferrer noopener"'),
            (REPO_URL, "GitHub", False, ' target="_blank" rel="noreferrer noopener"'),
        ),
        primary_cta_href=INSTALL_URL,
        primary_cta_label="Get VOOL",
    )


def render_surface_header(*, active: str) -> str:
    active_key = str(active or "").strip().lower()
    return _render_header(
        nav_items=(
            ("/proof", "Proof", active_key == "proof", ' data-tab="proof"'),
            ("/tasks", "Tasks", active_key == "tasks", ' data-tab="tasks"'),
            ("/agents", "Operators", active_key == "agents", ' data-tab="agents"'),
            ("/feed", "Worklog", active_key == "feed", ' data-tab="feed"'),
            (PUBLIC_STATUS_PATH, "Status", active_key == "status", ""),
            ("/hive", "Coordination", active_key == "hive", ' data-tab="hive"'),
        ),
        secondary_items=(
            (DOCS_URL, "Docs", False, ' target="_blank" rel="noreferrer noopener"'),
            (REPO_URL, "GitHub", False, ' target="_blank" rel="noreferrer noopener"'),
        ),
        primary_cta_href=INSTALL_URL,
        primary_cta_label="Get VOOL",
    )


def render_public_site_footer() -> str:
    return f"""
<footer class="ns-footer">
  <div class="ns-shell ns-footer-inner">
    <div class="ns-footer-top">
      <div class="ns-footer-copy">VOOL · local-first runtime with visible proof</div>
      <div class="ns-footer-links">
        <a href="{escape(DOCS_URL, quote=True)}" target="_blank" rel="noreferrer noopener">Docs</a>
        <a href="{escape(STATUS_DOC_URL, quote=True)}" target="_blank" rel="noreferrer noopener">Status doc</a>
        <a href="{escape(REPO_URL, quote=True)}" target="_blank" rel="noreferrer noopener">GitHub</a>
      </div>
    </div>
    {render_public_route_index(current_path="", title="Public routes", dense=True)}
  </div>
</footer>
"""


def render_public_view_nav(
    *,
    base_path: str,
    items: tuple[tuple[str, str, bool], ...],
    active_key: str,
) -> str:
    parts: list[str] = []
    for key, label, enabled in items:
        if enabled:
            href = base_path if key == "all" else f"{base_path}?view={escape(key, quote=True)}"
            current_attr = ' aria-current="page"' if key == active_key else ""
            parts.append(f'<a href="{href}"{current_attr}>{escape(label)}</a>')
        else:
            parts.append(f'<span aria-disabled="true">{escape(label)}</span>')
    return f'<nav class="ns-local-nav" aria-label="Route filters">{"".join(parts)}</nav>'


def render_back_to_route_index() -> str:
    return '<div class="ns-route-return"><a href="/#public-routes">Back to route index</a></div>'


def render_route_meta_block(*, current_path: str, breadcrumbs: tuple[tuple[str, str], ...]) -> str:
    return (
        render_public_breadcrumbs(*breadcrumbs)
        + render_back_to_route_index()
        + render_public_route_index(current_path=current_path, title="Public routes", dense=True)
    )


def render_public_canonical_meta(*, canonical_url: str, og_title: str, og_description: str, og_type: str = "website") -> str:
    return (
        f'<link rel="canonical" href="{escape(canonical_url, quote=True)}"/>\n'
        f'<meta property="og:title" content="{escape(og_title, quote=True)}"/>\n'
        f'<meta property="og:description" content="{escape(og_description[:300], quote=True)}"/>\n'
        f'<meta property="og:url" content="{escape(canonical_url, quote=True)}"/>\n'
        f'<meta property="og:type" content="{escape(og_type, quote=True)}"/>\n'
        f'<meta name="twitter:card" content="summary"/>\n'
        f'<meta name="twitter:site" content="@vool_ai"/>\n'
        f'<meta name="twitter:title" content="{escape(og_title, quote=True)}"/>\n'
        f'<meta name="twitter:description" content="{escape(og_description[:200], quote=True)}"/>\n'
    )


def _render_header(
    *,
    nav_items: tuple[tuple[str, str, bool, str], ...],
    secondary_items: tuple[tuple[str, str, bool, str], ...],
    primary_cta_href: str,
    primary_cta_label: str,
) -> str:
    nav_parts: list[str] = []
    for href, label, active, attrs in nav_items:
        active_attr = ' class="is-active" aria-current="page"' if active else ""
        nav_parts.append(
            f'<a href="{escape(href, quote=True)}"{attrs}{active_attr}>{escape(label)}</a>'
        )
    secondary_parts: list[str] = []
    for href, label, active, attrs in secondary_items:
        active_attr = ' class="is-active" aria-current="page"' if active else ""
        secondary_parts.append(
            f'<a href="{escape(href, quote=True)}"{attrs}{active_attr}>{escape(label)}</a>'
        )
    return f"""
<header class="ns-header">
  <div class="ns-shell ns-header-inner">
    <a class="ns-brand" href="/">
      <span class="ns-brand-mark" aria-hidden="true"></span>
      <span class="ns-brand-copy">
        <span class="ns-brand-title">VOOL</span>
        <span class="ns-brand-subtitle">Local-first runtime</span>
      </span>
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
