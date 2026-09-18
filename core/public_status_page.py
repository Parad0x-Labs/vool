from __future__ import annotations

from core.public_site_shell import (
    DOCS_URL,
    PUBLIC_STATUS_PATH,
    STATUS_DOC_URL,
    canonical_public_url,
    public_site_base_styles,
    render_back_to_route_index,
    render_public_breadcrumbs,
    render_public_canonical_meta,
    render_public_route_index,
    render_public_site_footer,
    render_surface_header,
)


def render_public_status_page_html(*, canonical_url: str = "") -> str:
    page_title = "VOOL Status · What is real right now"
    page_description = "One place for what already works, what is still rough, and what is not yet ready on the public VOOL surface."
    canonical_url = canonical_url or canonical_public_url(PUBLIC_STATUS_PATH)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{page_title}</title>
<meta name="description" content="{page_description}"/>
{render_public_canonical_meta(canonical_url=canonical_url, og_title=page_title, og_description=page_description)}
<style>
{public_site_base_styles()}
.ns-status-page {{
  padding: 28px 0 56px;
}}
.ns-status-grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 18px;
}}
.ns-status-card {{
  border: 1px solid var(--border);
  background: var(--surface);
  padding: 20px;
}}
.ns-status-card h2 {{
  margin: 0 0 10px;
  font-size: 18px;
  letter-spacing: -0.03em;
}}
.ns-status-card p {{
  margin: 0 0 12px;
  color: var(--text-muted);
  line-height: 1.7;
}}
.ns-status-card ul {{
  margin: 0;
  padding-left: 18px;
  color: var(--text-muted);
}}
.ns-status-card li + li {{
  margin-top: 6px;
}}
.ns-status-card--good h2 {{
  color: var(--green);
}}
.ns-status-card--warn h2 {{
  color: var(--orange);
}}
.ns-status-card--honest h2 {{
  color: var(--accent);
}}
.ns-status-hero {{
  border: 1px solid var(--border);
  background: var(--surface);
  padding: 24px;
}}
.ns-status-kicker {{
  margin-bottom: 10px;
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}}
.ns-status-hero h1 {{
  margin: 0 0 10px;
  font-size: clamp(30px, 4vw, 52px);
  line-height: 0.98;
  letter-spacing: -0.05em;
}}
.ns-status-hero p {{
  margin: 0;
  max-width: 70ch;
  color: var(--text-muted);
  line-height: 1.75;
}}
.ns-status-links {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 16px;
}}
.ns-status-links a {{
  text-decoration: none;
}}
.ns-status-block + .ns-status-block {{
  margin-top: 18px;
}}
@media (max-width: 980px) {{
  .ns-status-grid {{
    grid-template-columns: 1fr;
  }}
}}
</style>
</head>
<body>
{render_surface_header(active="status")}
<main class="ns-shell ns-status-page">
  {render_public_breadcrumbs(("/", "Home"), (PUBLIC_STATUS_PATH, "Status"))}
  {render_back_to_route_index()}
  <section class="ns-status-hero">
    <div class="ns-status-kicker">Public status</div>
    <h1>What is real. What is rough. What is not ready.</h1>
    <p>Status is a route, not a footnote. This page exists so visitors can see the current public surface honestly instead of inferring readiness from scattered copy, historical nouns, or broken links.</p>
    <div class="ns-status-links">
      <a class="ns-button" href="{STATUS_DOC_URL}" target="_blank" rel="noreferrer noopener">Open status doc</a>
      <a class="ns-button ns-button--secondary" href="{DOCS_URL}" target="_blank" rel="noreferrer noopener">Read docs</a>
    </div>
  </section>

  <section class="ns-status-block">
    {render_public_route_index(current_path=PUBLIC_STATUS_PATH, title="Public routes", dense=True)}
  </section>

  <section class="ns-status-grid">
    <article class="ns-status-card ns-status-card--good">
      <h2>Working now</h2>
      <p>The local-first runtime lane is real enough to inspect and test today.</p>
      <ul>
        <li>Local execution and local access surfaces</li>
        <li>Persistent memory and tool use</li>
        <li>Task flow and public work surfaces</li>
        <li>Proof receipts and readable work state</li>
      </ul>
    </article>
    <article class="ns-status-card ns-status-card--warn">
      <h2>Still hardening</h2>
      <p>The coordination story exists, but the public surface still needs sharper discipline and less internal jargon.</p>
      <ul>
        <li>WAN hardening and multi-node repeatability</li>
        <li>Operator surfaces and task detail density</li>
        <li>Sharper proof presentation</li>
        <li>Packaging and deployment hygiene</li>
      </ul>
    </article>
    <article class="ns-status-card ns-status-card--honest">
      <h2>Not yet proven</h2>
      <p>These claims stay demoted until the system can defend them with better evidence.</p>
      <ul>
        <li>Public multi-node repeatability</li>
        <li>Economic rails beyond local simulation</li>
        <li>Mass-market polish</li>
        <li>Fully mature public coordination layer</li>
      </ul>
    </article>
  </section>
</main>
{render_public_site_footer()}
</body>
</html>"""
