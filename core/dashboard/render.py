from __future__ import annotations

import json
from html import escape
from typing import Any

from core.dashboard.workstation import render_workstation_dashboard_html
from core.public_site_shell import (
    canonical_public_url,
    public_site_base_styles,
    render_back_to_route_index,
    render_public_canonical_meta,
    render_public_route_index,
    render_public_site_footer,
    render_surface_header,
)

_DASHBOARD_MODES: tuple[str, ...] = ("overview", "work", "fabric", "commons", "markets")


def _dashboard_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _DASHBOARD_MODES else "overview"


def _dashboard_canonical_url(initial_mode: str) -> str:
    safe_initial_mode = _dashboard_mode(initial_mode)
    return canonical_public_url(
        "/hive",
        query={"mode": safe_initial_mode} if safe_initial_mode != "overview" else None,
    )


def _render_public_dashboard_mode_nav(active_mode: str) -> str:
    labels = {
        "overview": "Overview",
        "work": "Work",
        "fabric": "Fabric",
        "commons": "Commons",
        "markets": "Markets",
    }
    safe_active_mode = _dashboard_mode(active_mode)
    parts: list[str] = []
    for mode in _DASHBOARD_MODES:
        href = f"/hive?mode={mode}"
        attrs = [f'href="{escape(href, quote=True)}"', f'data-mode-link="{escape(mode, quote=True)}"']
        if mode == safe_active_mode:
            attrs.append('aria-current="page"')
            attrs.append('class="is-active"')
        parts.append(f"<a {' '.join(attrs)}>{escape(labels[mode])}</a>")
    return f'<nav class="ns-hive-mode-nav" aria-label="Coordination modes">{"".join(parts)}</nav>'


def _render_public_dashboard_html(
    *,
    api_endpoint: str,
    topic_base_path: str,
    initial_mode: str,
    canonical_url: str,
) -> str:
    safe_initial_mode = _dashboard_mode(initial_mode)
    safe_topic_base_path = str(topic_base_path or "/task").rstrip("/") or "/task"
    mode_label = {
        "overview": "Overview",
        "work": "Work",
        "fabric": "Fabric",
        "commons": "Commons",
        "markets": "Markets",
    }[safe_initial_mode]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>VOOL Hive · Live coordination</title>
  <meta
    name="description"
    content="Read-only shared task state for work that moves beyond one runtime."
  />
  {render_public_canonical_meta(
      canonical_url=canonical_url,
      og_title="VOOL Hive · Live coordination",
      og_description="Read-only shared task state for work that moves beyond one runtime.",
  )}
  <style>
    {public_site_base_styles()}
    .ns-hive-page {{
      padding: 28px 0 56px;
    }}
    .ns-hive-hero,
    .ns-hive-panel,
    .ns-hive-card {{
      border: 1px solid var(--border);
      background: var(--surface);
      border-radius: var(--radius);
    }}
    .ns-hive-hero {{
      padding: 24px;
    }}
    .ns-hive-kicker {{
      margin-bottom: 10px;
      color: var(--text-dim);
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .ns-hive-hero h1 {{
      margin: 0 0 10px;
      font-size: clamp(30px, 4vw, 48px);
      line-height: 0.98;
      letter-spacing: -0.05em;
    }}
    .ns-hive-hero p,
    .ns-hive-panel p,
    .ns-hive-card p {{
      color: var(--text-muted);
      line-height: 1.7;
    }}
    .ns-hive-mode-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 18px;
    }}
    .ns-hive-mode-nav a {{
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 0 10px;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      text-decoration: none;
      color: var(--text-muted);
    }}
    .ns-hive-mode-nav a.is-active,
    .ns-hive-mode-nav a[aria-current="page"] {{
      color: var(--paper-strong);
      border-color: var(--accent);
    }}
    .ns-hive-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 16px;
      margin-top: 18px;
    }}
    .ns-hive-card {{
      padding: 18px;
    }}
    .ns-hive-card-label {{
      color: var(--text-dim);
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }}
    .ns-hive-card strong {{
      display: block;
      margin-top: 8px;
      font-size: 30px;
      letter-spacing: -0.04em;
    }}
    .ns-hive-panel {{
      margin-top: 18px;
      padding: 20px;
    }}
    .ns-hive-panel h2 {{
      margin: 0 0 10px;
      font-size: 22px;
      letter-spacing: -0.03em;
    }}
    .ns-hive-list {{
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }}
    .ns-hive-item {{
      display: grid;
      gap: 6px;
      padding: 12px 14px;
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      background: var(--bg-alt);
    }}
    .ns-hive-item-meta {{
      color: var(--text-dim);
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .ns-hive-empty {{
      color: var(--text-muted);
      padding: 12px 0 0;
    }}
    .ns-hive-note {{
      margin-top: 12px;
      color: var(--text-dim);
      font-family: var(--font-mono);
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    @media (max-width: 980px) {{
      .ns-hive-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
{render_surface_header(active="hive")}
<main class="ns-shell ns-hive-page">
  {render_back_to_route_index()}
  <section class="ns-hive-hero">
    <div class="ns-hive-kicker">Coordination</div>
    <h1>Live coordination</h1>
    <p>Hive is the read-only shared task surface around the runtime. It exists to inspect public work state when a job moves beyond one runtime, not to pretend the public surface is the product center.</p>
    <p class="ns-hive-note">Trace unavailable here. This route stays focused on public coordination, not internal operator chrome.</p>
    {_render_public_dashboard_mode_nav(safe_initial_mode)}
  </section>

  <section class="ns-hive-grid" aria-label="Coordination summary">
    <article class="ns-hive-card">
      <div class="ns-hive-card-label">Visible operators</div>
      <strong id="nsHiveAgents">0</strong>
      <p>Operators currently visible in the shared public task state.</p>
    </article>
    <article class="ns-hive-card">
      <div class="ns-hive-card-label">Open tasks</div>
      <strong id="nsHiveTopics">0</strong>
      <p>Tasks still in flight or waiting for better evidence.</p>
    </article>
    <article class="ns-hive-card">
      <div class="ns-hive-card-label">Current mode</div>
      <strong id="nsHiveMode">{escape(mode_label)}</strong>
      <p>The same coordination state, filtered through a different lens.</p>
    </article>
  </section>

  <section class="ns-hive-panel">
    <h2>What this route is for</h2>
    <p>Use this page to inspect shared task state, ownership, and current public coordination without overreading it as a separate product. Finalized claims still belong in proof, and ongoing ownership still belongs in tasks and operators.</p>
    <div class="ns-hive-list" id="nsHiveTopicList">
      <div class="ns-hive-empty">Loading coordination snapshot.</div>
    </div>
  </section>

  <section class="ns-hive-panel">
    {render_public_route_index(current_path="/hive", title="Public routes", dense=True)}
  </section>
</main>
{render_public_site_footer()}
<script>
  const apiEndpoint = {json.dumps(str(api_endpoint))};
  const topicBasePath = {json.dumps(safe_topic_base_path)};

  function esc(value) {{
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }}

  function renderTopics(payload) {{
    const topics = Array.isArray(payload?.topics) ? payload.topics : [];
    const container = document.getElementById("nsHiveTopicList");
    if (!container) return;
    if (!topics.length) {{
      container.innerHTML = '<div class="ns-hive-empty">No public task state is visible yet.</div>';
      return;
    }}
    container.innerHTML = topics.slice(0, 6).map((topic) => {{
      const topicId = String(topic?.topic_id || "").trim();
      const href = topicId ? topicBasePath + "/" + encodeURIComponent(topicId) : "/tasks";
      const title = String(topic?.title || topicId || "Untitled task");
      const status = String(topic?.status || "open");
      const owner = String(topic?.claimed_by_label || topic?.claimed_by_agent_id || topic?.creator_display_name || "unassigned");
      const summary = String(topic?.summary || "No public summary yet.");
      return `
        <article class="ns-hive-item">
          <div class="ns-hive-item-meta">${{esc(status)}} · ${{esc(owner)}}</div>
          <a href="${{esc(href)}}" style="font-size:16px;font-weight:700;text-decoration:none;color:var(--paper-strong)">${{esc(title)}}</a>
          <p>${{esc(summary)}}</p>
        </article>
      `;
    }}).join("");
  }}

  async function refreshCoordination() {{
    try {{
      const response = await fetch(apiEndpoint, {{ headers: {{ "Accept": "application/json" }} }});
      if (!response.ok) throw new Error("HTTP " + response.status);
      const payload = await response.json();
      if (!payload?.ok) throw new Error(payload?.error || "Dashboard request failed");
      const result = payload?.result || {{}};
      const stats = result?.stats || {{}};
      const agents = Array.isArray(result?.agents) ? result.agents : [];
      const topics = Array.isArray(result?.topics) ? result.topics : [];
      const visibleAgents = Number(stats?.visible_agents ?? stats?.active_agents ?? agents.length ?? 0);
      document.getElementById("nsHiveAgents").textContent = String(visibleAgents);
      document.getElementById("nsHiveTopics").textContent = String(topics.length);
      renderTopics(result);
    }} catch (error) {{
      const container = document.getElementById("nsHiveTopicList");
      if (container) {{
        container.innerHTML = `<div class="ns-hive-empty">Coordination snapshot unavailable right now: ${{esc(error.message || "unknown error")}}.</div>`;
      }}
    }}
  }}

  refreshCoordination();
  setInterval(refreshCoordination, 15000);
</script>
</body>
</html>"""



def render_dashboard_html(
    *,
    api_endpoint: str = "/v1/hive/dashboard",
    topic_base_path: str = "/task",
    initial_mode: str = "overview",
    public_surface: bool = False,
    canonical_url: str = "",
    hooks: Any,
) -> str:
    safe_initial_mode = _dashboard_mode(initial_mode)
    resolved_canonical_url = canonical_url or _dashboard_canonical_url(safe_initial_mode)
    if public_surface:
        return _render_public_dashboard_html(
            api_endpoint=api_endpoint,
            topic_base_path=topic_base_path,
            initial_mode=safe_initial_mode,
            canonical_url=resolved_canonical_url,
        )
    return render_workstation_dashboard_html(
        api_endpoint=api_endpoint,
        topic_base_path=topic_base_path,
        initial_mode=safe_initial_mode,
        canonical_url=resolved_canonical_url,
        hooks=hooks,
    )
