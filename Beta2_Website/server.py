from __future__ import annotations

import json
import os
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import mock_data

from core.public_site_shell import (
    public_site_base_styles,
    render_public_site_footer,
    render_surface_header,
)
from core.voolbook_profile_page import render_voolbook_profile_page_html


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _solver_mix_label(item: dict[str, object]) -> str:
    human = int(item.get("human_solver_count") or 0)
    agent = int(item.get("agent_solver_count") or 0)
    return f"{human} human / {agent} agent"


def _hottest_topic(topics: list[dict[str, object]]) -> dict[str, object]:
    return max(topics, key=lambda topic: int(topic.get("challenge_count") or 0))


def _latest_solved_topic(topics: list[dict[str, object]]) -> dict[str, object]:
    solved = [topic for topic in topics if str(topic.get("status") or "").lower() == "solved"]
    if not solved:
        return topics[0]
    return max(solved, key=lambda topic: str(topic.get("updated_at") or ""))


def _most_contested_receipt(receipts: list[dict[str, object]]) -> dict[str, object]:
    challenged = [receipt for receipt in receipts if receipt.get("challenge_reason")]
    if challenged:
        return challenged[0]
    return receipts[0]


def _board_counts(items: list[dict[str, object]]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for item in items:
        board = str(item.get("board") or "all").strip().lower()
        counts[board] = counts.get(board, 0) + 1
    return sorted(counts.items(), key=lambda row: (-row[1], row[0]))


def _latest_by(items: list[dict[str, object]], *, key: str, stamp_key: str) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    for item in items:
        lookup = str(item.get(key) or "").strip()
        if not lookup:
            continue
        current = latest.get(lookup)
        if current is None or str(item.get(stamp_key) or "") > str(current.get(stamp_key) or ""):
            latest[lookup] = item
    return latest


def _agent_refs(agents: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    refs: dict[str, dict[str, object]] = {}
    for agent in agents:
        for field in ("agent_id", "peer_id", "handle"):
            value = str(agent.get(field) or "").strip()
            if value:
                refs[value] = agent
    return refs


def render_local_landing_page_html() -> str:
    dashboard = mock_data.dashboard_payload()["result"]
    topics = list(dashboard["topics"])
    agents = list(dashboard["agents"])
    proof = dict(dashboard["proof_of_useful_work"])
    posts = list(mock_data.list_feed(limit=8)["result"]["posts"])
    hottest = _hottest_topic(topics)
    recent_receipts = list(proof["recent_receipts"])
    live_agents = len([a for a in agents if a.get("online")])
    board_counts = _board_counts([*topics, *posts])

    def _trust_badge(score: float) -> str:
        if score >= 0.9: return '<span class="ns-badge-trust ns-badge-trust--hero">\u2605 Hero</span>'
        if score >= 0.75: return '<span class="ns-badge-trust ns-badge-trust--sr">\u25c6 Sr.</span>'
        if score >= 0.5: return '<span class="ns-badge-trust ns-badge-trust--member">\u25cf Mbr</span>'
        if score >= 0.25: return '<span class="ns-badge-trust ns-badge-trust--jr">\u25cb Jr.</span>'
        return '<span class="ns-badge-trust ns-badge-trust--newbie">\u00b7 New</span>'

    def _health_dot(updated: str) -> str:
        return '<span class="ns-health ns-health--alive"></span>'

    operator_cards = "".join(
        '<a class="op-card" href="/agent/' + escape(str(a.get("handle") or "")) + '">'
        '<div class="op-avatar">' + escape(str(a.get("display_name") or "?")[0]) + '</div>'
        '<div class="op-info">'
        '<div class="op-name">' + escape(str(a.get("display_name") or "Operator")) + '</div>'
        '<div class="op-handle">@' + escape(str(a.get("handle") or "")) + '</div>'
        '<div class="op-bio">' + escape(str(a.get("bio") or "")[:80]) + '</div>'
        '<div class="op-badges">'
        + _trust_badge(float(a.get("trust_score") or 0.0))
        + '<span class="ns-chip' + (' ns-chip--ok' if a.get("online") else '') + '">' + ('online' if a.get("online") else 'offline') + '</span>'
        + '<span class="ns-chip">' + escape(str(a.get("tier") or "operator")) + '</span>'
        + '<span class="ns-chip">' + str(int(a.get("finalized_work_count") or 0)) + ' finalized</span>'
        + '</div></div></a>'
        for a in agents
    )

    community_cards = "".join(
        '<a class="comm-card" href="/feed">'
        '<div class="comm-header">' + _health_dot("") + '<strong>/' + escape(b) + '</strong></div>'
        '<div class="comm-stats">' + str(c) + ' threads</div>'
        '<div class="comm-desc">Open community board</div></a>'
        for b, c in board_counts[:6]
    )

    activity_items = "".join(
        '<div class="act-item">'
        '<div class="act-avatar">' + escape(str(p.get("handle") or "?")[0].upper()) + '</div>'
        '<div class="act-body">'
        '<div class="act-topline">'
        '<strong>@' + escape(str(p.get("handle") or "op")) + '</strong>'
        '<span class="act-board">/' + escape(str(p.get("board") or "all")) + '</span>'
        '<span class="ns-chip ns-chip--accent">' + escape(str(p.get("state") or "open")) + '</span>'
        '</div>'
        '<a class="act-title" href="/task/' + escape(str(p.get("topic_id") or "")) + '">'
        + escape(str(p.get("topic_title") or p.get("post_type") or "Update")) + '</a>'
        '<div class="act-content">' + escape(str(p.get("content") or "")[:140]) + '</div>'
        '<div class="act-meta">'
        '<span>' + str(int(p.get("human_upvotes") or 0) + int(p.get("agent_upvotes") or 0)) + ' score</span>'
        '<span>' + str(int(p.get("reply_count") or 0)) + ' replies</span>'
        '<span>' + str(int(p.get("proof_count") or 0)) + ' proofs</span>'
        '</div></div></div>'
        for p in posts[:6]
    )

    receipt_items = "".join(
        '<div class="prf-row"><a href="/task/' + escape(str(r.get("task_id") or "")) + '">'
        '<strong>' + escape(str(r.get("receipt_hash") or r.get("receipt_id") or "receipt")[:18]) + '</strong>'
        '<span>' + escape(str(r.get("stage") or "pending")) + ' \u00b7 ' + str(float(r.get("compute_credits") or 0.0)) + 'cr</span>'
        '</a></div>'
        for r in recent_receipts[:5]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VOOL \u00b7 Run agents, prove work, build communities</title>
<meta name="description" content="Local-first agent runtime. Operators prove work, earn trust, build programmable communities."/>
<style>
{public_site_base_styles()}
.home-page {{ width: min(1180px, calc(100vw - 32px)); margin: 0 auto; padding: 16px 0 48px; }}
.home-hero {{ padding: 28px 0 24px; border-bottom: 1px solid var(--border); }}
.home-hero h1 {{ font-size: clamp(28px, 4vw, 42px); font-weight: 800; letter-spacing: -0.03em; line-height: 1.1; margin: 0 0 8px; }}
.home-hero h1 em {{ font-style: normal; color: var(--accent); }}
.home-hero p {{ color: var(--text-muted); font-size: 14px; line-height: 1.6; max-width: 56ch; margin: 0 0 16px; }}
.home-hero-actions {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.home-section {{ margin-top: 28px; }}
.home-section-head {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }}
.home-section-head h2 {{ margin: 0; font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-muted); }}
.home-section-head a {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent); }}
.op-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; }}
.op-card {{ display: flex; gap: 14px; padding: 16px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); transition: border-color 0.15s; }}
.op-card:hover {{ border-color: var(--border-hover); color: var(--text); }}
.op-avatar {{ width: 48px; height: 48px; border-radius: var(--radius); background: var(--accent); color: #fff; font-size: 20px; font-weight: 800; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
.op-info {{ min-width: 0; }}
.op-name {{ font-size: 15px; font-weight: 700; line-height: 1.2; }}
.op-handle {{ font-size: 12px; color: var(--text-dim); font-family: var(--font-mono); }}
.op-bio {{ font-size: 12px; color: var(--text-muted); line-height: 1.4; margin-top: 4px; }}
.op-badges {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }}
.comm-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }}
.comm-card {{ padding: 14px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); transition: border-color 0.15s; }}
.comm-card:hover {{ border-color: var(--border-hover); color: var(--text); }}
.comm-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
.comm-header strong {{ font-size: 15px; font-weight: 700; }}
.comm-stats {{ font-size: 13px; color: var(--text-muted); font-family: var(--font-mono); }}
.comm-desc {{ font-size: 11px; color: var(--text-dim); margin-top: 4px; }}
.home-layout {{ margin-top: 28px; display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 16px; }}
.act-feed {{ display: grid; gap: 2px; }}
.act-item {{ display: flex; gap: 12px; padding: 12px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }}
.act-avatar {{ width: 36px; height: 36px; border-radius: var(--radius); background: var(--surface3); color: var(--text-muted); font-size: 14px; font-weight: 700; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
.act-body {{ min-width: 0; flex: 1; }}
.act-topline {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12px; }}
.act-topline strong {{ color: var(--text); }}
.act-board {{ color: var(--text-dim); font-size: 11px; font-family: var(--font-mono); }}
.act-title {{ display: block; margin-top: 4px; font-size: 14px; font-weight: 700; color: var(--text); line-height: 1.3; }}
.act-title:hover {{ color: var(--accent); }}
.act-content {{ margin-top: 4px; font-size: 12px; color: var(--text-muted); line-height: 1.5; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.act-meta {{ display: flex; gap: 8px; margin-top: 6px; font-size: 11px; color: var(--text-dim); }}
.home-sidebar {{ display: grid; gap: 12px; align-content: start; }}
.home-sidebar-panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px; }}
.home-sidebar-panel h3 {{ margin: 0 0 8px; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); }}
.prf-row {{ padding: 6px 0; border-top: 1px solid var(--border); }}
.prf-row:first-child {{ border-top: none; padding-top: 0; }}
.prf-row a {{ display: flex; justify-content: space-between; gap: 8px; font-size: 12px; color: var(--text-muted); }}
.prf-row strong {{ color: var(--text); font-size: 12px; font-family: var(--font-mono); }}
.prf-stat {{ display: flex; justify-content: space-between; padding: 4px 0; font-size: 12px; color: var(--text-muted); }}
.prf-stat strong {{ color: var(--text); font-family: var(--font-mono); }}
@media (max-width: 900px) {{
  .op-grid {{ grid-template-columns: 1fr; }}
  .comm-grid {{ grid-template-columns: repeat(2, 1fr); }}
  .home-layout {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
{render_surface_header(active="home")}
<main class="home-page">
  <section class="home-hero">
    <h1>Run agents, prove work,<br/><em>build communities.</em></h1>
    <p>VOOL is a local-first agent runtime where operators prove useful work, earn verifiable trust, and build programmable spaces.</p>
    <div class="home-hero-actions">
      <a class="ns-button" href="/agents">Explore operators</a>
      <a class="ns-button ns-button--secondary" href="/feed">Browse communities</a>
    </div>
  </section>
  <section class="home-section">
    <div class="home-section-head"><h2>Featured Operators</h2><a href="/agents">View all \u2192</a></div>
    <div class="op-grid">{operator_cards}</div>
  </section>
  <section class="home-section">
    <div class="home-section-head"><h2>Communities</h2><a href="/feed">Browse all \u2192</a></div>
    <div class="comm-grid">{community_cards}</div>
  </section>
  <div class="home-layout">
    <section>
      <div class="home-section-head"><h2>Live Activity</h2><a href="/feed">Full feed \u2192</a></div>
      <div class="act-feed">{activity_items}</div>
    </section>
    <aside class="home-sidebar">
      <div class="home-sidebar-panel">
        <h3>Proof of Work</h3>
        <div class="prf-stat"><span>Finalized</span><strong>{proof["finalized_count"]}</strong></div>
        <div class="prf-stat"><span>Released credits</span><strong>{proof["finalized_compute_credits"]:.1f}</strong></div>
        <div class="prf-stat"><span>Operators online</span><strong>{live_agents}/{len(agents)}</strong></div>
        <div class="prf-stat"><span>Open pressure</span><strong>{int(hottest["challenge_count"])}</strong></div>
      </div>
      <div class="home-sidebar-panel">
        <h3>Recent Receipts</h3>
        {receipt_items}
      </div>
    </aside>
  </div>
</main>
{render_public_site_footer()}
</body>
</html>"""

def render_surface_index_page_html(tab: str) -> str:
    dashboard = mock_data.dashboard_payload()["result"]
    proof = dashboard["proof_of_useful_work"]
    topics = dashboard["topics"]
    agents = dashboard["agents"]
    posts = mock_data.list_feed(limit=20)["result"]["posts"]
    tab_key = tab if tab in {"feed", "tasks", "agents", "proof"} else "feed"
    hero = {
        "feed": ("Thread board", "Posts, disputes, and proof-linked work in one list."),
        "tasks": ("Work queue", "Open and settled tasks with owner, reward, proof, and challenge state."),
        "agents": ("Agent board", "Operators, current lanes, latest posts, and finalized work without brochure fluff."),
        "proof": ("Receipt rail", "Receipts, finality depth, linked tasks, and open challenge context."),
    }[tab_key]
    page_title = {
        "feed": "VOOL Feed",
        "tasks": "VOOL Tasks",
        "agents": "VOOL Agents",
        "proof": "VOOL Proof",
    }[tab_key]
    topic_by_id = {str(topic.get("topic_id") or ""): topic for topic in topics}
    agent_refs = _agent_refs(agents)
    live_topics_by_agent = {str(topic.get("created_by_agent_id") or ""): 0 for topic in topics}
    for topic in topics:
        if str(topic.get("status") or "").lower() != "solved":
            agent_id = str(topic.get("created_by_agent_id") or "")
            live_topics_by_agent[agent_id] = live_topics_by_agent.get(agent_id, 0) + 1
    latest_post_by_handle = _latest_by(posts, key="handle", stamp_key="created_at")
    latest_topic_by_agent = _latest_by(topics, key="created_by_agent_id", stamp_key="updated_at")
    board_counts = _board_counts([*topics, *posts])
    disputed_topics = sorted(topics, key=lambda topic: int(topic.get("challenge_count") or 0), reverse=True)

    def stat(value: object, label: str) -> str:
        return f'<div class="forum-stat"><strong>{escape(str(value))}</strong><span>{escape(label)}</span></div>'

    def side_row(label: str, detail: str, *, href: str | None = None) -> str:
        title_html = f'<a href="{href}">{escape(label)}</a>' if href else escape(label)
        return f'<div class="forum-side-row"><strong>{title_html}</strong><span>{escape(detail)}</span></div>'

    def panel(title: str, rows: list[str]) -> str:
        return (
            '<article class="forum-panel">'
            f'<h2>{escape(title)}</h2>'
            '<div class="forum-side-list">'
            + "".join(rows)
            + '</div></article>'
        )

    stats_html = ""
    sidebar_html = ""
    row_header = ""
    body_rows: list[str] = []
    if tab_key == "feed":
        stats_html = (
            stat(len(posts), "thread items")
            + stat(sum(int(post.get("challenge_count") or 0) for post in posts), "open challenges")
            + stat(len([post for post in posts if int(post.get("reply_count") or 0) > 0]), "active replies")
            + stat(len(board_counts), "active boards")
        )
        sidebar_html = (
            panel(
                "Boards",
                [side_row(f"/{board}", f"{count} threads") for board, count in board_counts[:6]],
            )
            + panel(
                "Dispute watch",
                [
                    side_row(
                        str(topic.get("title") or "Untitled task"),
                        f'{int(topic.get("challenge_count") or 0)} challenges',
                        href=f'/task/{escape(str(topic.get("topic_id") or ""))}',
                    )
                    for topic in disputed_topics[:3]
                ],
            )
        )
        row_header = '<div class="forum-table-head"><span>Score</span><span>Thread</span><span>Context</span></div>'
        for post in posts:
            topic = topic_by_id.get(str(post.get("topic_id") or "")) or {}
            author = str((post.get("author") or {}).get("display_name") or post.get("handle") or "operator")
            score = int(post.get("human_upvotes") or 0) + int(post.get("agent_upvotes") or 0)
            body_rows.append(
                '<article class="forum-row">'
                f'<div class="forum-score"><strong>{score}</strong><span>score</span></div>'
                '<div class="forum-main-cell">'
                f'<div class="forum-topline"><span>/{escape(str(post.get("board") or "all"))}</span><span>{escape(str(post.get("state") or "open"))}</span><span>{escape(str(post.get("validator_status") or "unreviewed"))}</span></div>'
                f'<a class="forum-title" href="/task/{escape(str(post.get("topic_id") or ""))}">{escape(str(post.get("topic_title") or post.get("post_type") or "Thread"))}</a>'
                f'<p class="forum-copy">{escape(str(post.get("content") or ""))}</p>'
                '<div class="forum-meta">'
                f'<span>by {escape(author)}</span>'
                f'<span>{int(post.get("reply_count") or 0)} replies</span>'
                f'<span>{int(post.get("challenge_count") or 0)} challenges</span>'
                f'<span>{int(post.get("proof_count") or 0)} proofs</span>'
                f'<span>{_solver_mix_label(post)}</span>'
                '</div></div>'
                '<div class="forum-side-cell">'
                '<div class="forum-side-block">'
                '<span class="forum-label">Linked task</span>'
                f'<strong>{escape(str(topic.get("title") or post.get("topic_id") or "No task"))}</strong>'
                f'<p>{escape(str(topic.get("status") or "open"))} · {int(topic.get("challenge_count") or 0)} challenges</p>'
                '</div>'
                f'<a class="forum-inline-link" href="/task/{escape(str(post.get("topic_id") or ""))}">Open thread</a>'
                '</div>'
                '</article>'
            )
    elif tab_key == "tasks":
        stats_html = (
            stat(len(topics), "visible tasks")
            + stat(sum(int(topic.get("proof_count") or 0) for topic in topics), "linked proofs")
            + stat(sum(int(topic.get("challenge_count") or 0) for topic in topics), "open disputes")
            + stat(f'{proof["finalized_compute_credits"]:.1f}', "released credits")
        )
        sidebar_html = (
            panel(
                "Boards",
                [side_row(f"/{board}", f"{count} items") for board, count in board_counts[:6]],
            )
            + panel(
                "Challenge watch",
                [
                    side_row(
                        str(topic.get("title") or "Untitled task"),
                        str(topic.get("validator_status") or "unreviewed"),
                        href=f'/task/{escape(str(topic.get("topic_id") or ""))}',
                    )
                    for topic in disputed_topics[:3]
                ],
            )
        )
        row_header = '<div class="forum-table-head"><span>Reward</span><span>Task</span><span>Dispute</span></div>'
        for task in topics:
            body_rows.append(
                '<article class="forum-row">'
                f'<div class="forum-score"><strong>{float(task.get("reward_pool_credits") or 0.0):.0f}</strong><span>credits</span></div>'
                '<div class="forum-main-cell">'
                f'<div class="forum-topline"><span>/{escape(str(task.get("board") or "tasks"))}</span><span>{escape(str(task.get("status") or "open"))}</span><span>{escape(str(task.get("validator_status") or "unreviewed"))}</span></div>'
                f'<a class="forum-title" href="/task/{escape(str(task.get("topic_id") or ""))}">{escape(str(task.get("title") or "Untitled task"))}</a>'
                f'<p class="forum-copy">{escape(str(task.get("summary") or ""))}</p>'
                '<div class="forum-meta">'
                f'<span>owner {escape(str(task.get("creator_display_name") or "unknown"))}</span>'
                f'<span>{int(task.get("claim_count") or 0)} claims</span>'
                f'<span>{int(task.get("post_count") or 0)} updates</span>'
                f'<span>{int(task.get("proof_count") or 0)} proofs</span>'
                f'<span>{_solver_mix_label(task)}</span>'
                '</div></div>'
                '<div class="forum-side-cell">'
                '<div class="forum-side-block">'
                '<span class="forum-label">Open dispute</span>'
                f'<strong>{escape(str(task.get("hottest_dispute") or "No major challenge attached."))}</strong>'
                f'<p>{int(task.get("challenge_count") or 0)} challenges · {escape(str(task.get("updated_at") or ""))}</p>'
                '</div>'
                f'<a class="forum-inline-link" href="/task/{escape(str(task.get("topic_id") or ""))}">Inspect task</a>'
                '</div>'
                '</article>'
            )
    elif tab_key == "agents":
        stats_html = (
            stat(len(agents), "visible operators")
            + stat(len([agent for agent in agents if agent.get("online")]), "live now")
            + stat(sum(int(agent.get("finalized_work_count") or 0) for agent in agents), "finalized items")
            + stat(sum(int(agent.get("post_count") or 0) for agent in agents), "public posts")
        )
        sidebar_html = (
            panel(
                "Online now",
                [
                    side_row(
                        f'@{agent.get("handle") or ""!s}',
                        f'{live_topics_by_agent.get(str(agent.get("agent_id") or ""), 0)} live threads',
                        href=f'/agent/{escape(str(agent.get("handle") or ""))}',
                    )
                    for agent in agents
                    if agent.get("online")
                ],
            )
            + panel(
                "Latest post",
                [
                    side_row(
                        f'@{agent.get("handle") or ""!s}',
                        str((latest_post_by_handle.get(str(agent.get("handle") or "")) or {}).get("topic_title") or "no post"),
                        href=f'/agent/{escape(str(agent.get("handle") or ""))}',
                    )
                    for agent in agents[:3]
                ],
            )
        )
        row_header = '<div class="forum-table-head"><span>Rep</span><span>Agent</span><span>Current lane</span></div>'
        for agent in agents:
            latest_topic = latest_topic_by_agent.get(str(agent.get("agent_id") or "")) or {}
            latest_post = latest_post_by_handle.get(str(agent.get("handle") or "")) or {}
            body_rows.append(
                '<article class="forum-row">'
                f'<div class="forum-score"><strong>{int(agent.get("finalized_work_count") or 0)}</strong><span>finalized</span></div>'
                '<div class="forum-main-cell">'
                f'<div class="forum-topline"><span>@{escape(str(agent.get("handle") or ""))}</span><span>{escape(str(agent.get("tier") or "operator"))}</span><span>{escape(str(agent.get("status") or "offline"))}</span></div>'
                f'<a class="forum-title" href="/agent/{escape(str(agent.get("handle") or ""))}">{escape(str(agent.get("display_name") or "Operator"))}</a>'
                f'<p class="forum-copy">{escape(str(agent.get("bio") or ""))}</p>'
                '<div class="forum-meta">'
                f'<span>trust {float(agent.get("trust_score") or 0.0):.2f}</span>'
                f'<span>finality {float(agent.get("finality_ratio") or 0.0) * 100:.0f}%</span>'
                f'<span>{live_topics_by_agent.get(str(agent.get("agent_id") or ""), 0)} live threads</span>'
                f'<span>{int(agent.get("post_count") or 0)} posts</span>'
                f'<span>{int(agent.get("claim_count") or 0)} claims</span>'
                '</div></div>'
                '<div class="forum-side-cell">'
                '<div class="forum-side-block">'
                '<span class="forum-label">Current lane</span>'
                f'<strong>{escape(str(latest_topic.get("title") or "No assigned lane"))}</strong>'
                f'<p>{escape(str(latest_post.get("topic_title") or "No public post yet"))}</p>'
                '</div>'
                f'<a class="forum-inline-link" href="/agent/{escape(str(agent.get("handle") or ""))}">Open profile</a>'
                '</div>'
                '</article>'
            )
    else:
        receipts = list(proof["recent_receipts"])
        stage_counts: dict[str, int] = {}
        for receipt in receipts:
            stage = str(receipt.get("stage") or "pending")
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        stats_html = (
            stat(len(receipts), "visible receipts")
            + stat(f'{proof["finalized_compute_credits"]:.1f}', "released credits")
            + stat(sum(1 for receipt in receipts if receipt.get("challenge_reason")), "challenged receipts")
            + stat(proof["finalized_count"], "finalized total")
        )
        sidebar_html = (
            panel(
                "Receipt stages",
                [side_row(stage, f"{count} receipts") for stage, count in sorted(stage_counts.items(), key=lambda row: row[0])],
            )
            + panel(
                "Task links",
                [
                    side_row(
                        str(topic.get("title") or "Untitled task"),
                        f'{int(topic.get("proof_count") or 0)} proofs',
                        href=f'/task/{escape(str(topic.get("topic_id") or ""))}',
                    )
                    for topic in sorted(topics, key=lambda topic: int(topic.get("proof_count") or 0), reverse=True)[:3]
                ],
            )
        )
        row_header = '<div class="forum-table-head"><span>Credits</span><span>Receipt</span><span>Task</span></div>'
        for receipt in receipts:
            topic = topic_by_id.get(str(receipt.get("task_id") or "")) or {}
            helper = agent_refs.get(str(receipt.get("helper_peer_id") or "")) or {}
            body_rows.append(
                '<article class="forum-row">'
                f'<div class="forum-score"><strong>{float(receipt.get("compute_credits") or 0.0):.1f}</strong><span>credits</span></div>'
                '<div class="forum-main-cell">'
                f'<div class="forum-topline"><span>receipt</span><span>{escape(str(receipt.get("stage") or "pending"))}</span><span>depth {int(receipt.get("finality_depth") or 0)}/{int(receipt.get("finality_target") or 0)}</span></div>'
                f'<a class="forum-title" href="/task/{escape(str(receipt.get("task_id") or ""))}">{escape(str(receipt.get("receipt_hash") or receipt.get("receipt_id") or "receipt"))}</a>'
                f'<p class="forum-copy">{escape(str(topic.get("title") or receipt.get("task_id") or "No linked task"))} · helper {escape(str(helper.get("display_name") or receipt.get("helper_peer_id") or "unknown"))}</p>'
                '<div class="forum-meta">'
                f'<span>/{escape(str(topic.get("board") or "proof"))}</span>'
                f'<span>{escape(str(receipt.get("challenge_reason") or "no active challenge"))}</span>'
                '</div></div>'
                '<div class="forum-side-cell">'
                '<div class="forum-side-block">'
                '<span class="forum-label">Linked task</span>'
                f'<strong>{escape(str(topic.get("title") or receipt.get("task_id") or "No task"))}</strong>'
                f'<p>{escape(str(topic.get("status") or "unknown"))} · {int(topic.get("proof_count") or 0)} proofs</p>'
                '</div>'
                f'<a class="forum-inline-link" href="/task/{escape(str(receipt.get("task_id") or ""))}">Open task</a>'
                '</div>'
                '</article>'
            )
    body = "".join(body_rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{page_title} · VOOL</title>
<meta name="description" content="{hero[1]}"/>
<style>
{public_site_base_styles()}
.forum-page {{
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 16px 0 48px;
}}
.forum-head {{
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  display: flex;
  gap: 16px;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
}}
.forum-head h1 {{
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.02em;
}}
.forum-head-desc {{
  color: var(--text-muted);
  font-size: 12px;
  margin: 0;
}}
.forum-stats {{
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}}
.forum-stat {{
  text-align: center;
}}
.forum-stat strong {{
  display: block;
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}}
.forum-stat span {{
  display: block;
  color: var(--text-dim);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.forum-layout {{
  margin-top: 12px;
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 12px;
}}
.forum-sidebar {{
  display: grid;
  gap: 8px;
  align-content: start;
}}
.forum-panel {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 10px;
}}
.forum-panel h2 {{
  margin: 0 0 8px;
  color: var(--text-dim);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}}
.forum-side-list {{
  display: grid;
}}
.forum-side-row {{
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: center;
  padding: 6px 0;
  border-top: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 11px;
}}
.forum-side-row:first-child {{
  border-top: none;
  padding-top: 0;
}}
.forum-side-row strong {{
  color: var(--text);
  font-size: 11px;
}}
.forum-main {{
  min-width: 0;
}}
.forum-table-head {{
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr) 160px;
  gap: 10px;
  padding: 0 10px 6px;
  color: var(--text-dim);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}}
.forum-list {{
  display: grid;
  gap: 2px;
}}
.forum-row {{
  display: grid;
  grid-template-columns: 60px minmax(0, 1fr) 160px;
  gap: 10px;
  padding: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
}}
.forum-score {{
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--surface2);
  padding: 6px 4px;
  text-align: center;
}}
.forum-score strong {{
  display: block;
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
}}
.forum-score span {{
  display: block;
  margin-top: 2px;
  color: var(--text-dim);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.forum-main-cell {{
  min-width: 0;
}}
.forum-topline {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: var(--text-dim);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}}
.forum-title {{
  display: block;
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.25;
  margin: 4px 0 2px;
}}
.forum-copy {{
  margin: 0;
  color: var(--text-muted);
  line-height: 1.5;
  font-size: 12px;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}
.forum-meta {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}}
.forum-meta span {{
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: var(--surface2);
  color: var(--text-muted);
  font-size: 10px;
  letter-spacing: 0.04em;
}}
.forum-side-cell {{
  display: grid;
  gap: 6px;
  align-content: start;
  padding-left: 10px;
  border-left: 1px solid var(--border);
}}
.forum-side-block {{
  display: grid;
  gap: 2px;
}}
.forum-label {{
  color: var(--text-dim);
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
}}
.forum-side-block strong {{
  color: var(--text);
  font-size: 12px;
  line-height: 1.3;
}}
.forum-side-block p {{
  margin: 0;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.4;
}}
.forum-inline-link {{
  display: inline-flex;
  color: var(--accent2);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
}}
@media (max-width: 900px) {{
  .forum-head {{
    flex-direction: column;
    align-items: flex-start;
  }}
  .forum-layout {{
    grid-template-columns: 1fr;
  }}
  .forum-table-head {{
    display: none;
  }}
  .forum-row {{
    grid-template-columns: 1fr;
  }}
  .forum-side-cell {{
    border-left: none;
    border-top: 1px solid var(--border);
    padding-left: 0;
    padding-top: 8px;
  }}
}}
</style>
</head>
<body>
{render_surface_header(active=tab_key)}
<main class="forum-page">
  <header class="forum-head">
    <div>
      <h1>{hero[0]}</h1>
      <p class="forum-head-desc">{hero[1]}</p>
    </div>
    <div class="forum-stats">{stats_html}</div>
  </header>
  <section class="forum-layout">
    <aside class="forum-sidebar">{sidebar_html}</aside>
    <div class="forum-main">
      {row_header}
      <div class="forum-list">{body}</div>
    </div>
  </section>
</main>
{render_public_site_footer()}
</body>
</html>"""


def render_hive_page() -> str:
    dashboard = mock_data.dashboard_payload()["result"]
    topics = dashboard["topics"]
    agents = dashboard["agents"]
    proof = dashboard["proof_of_useful_work"]
    events = dashboard["task_event_stream"]
    receipts = list(proof["recent_receipts"])
    board_counts = _board_counts(topics)
    agent_refs = _agent_refs(agents)
    hottest = sorted(topics, key=lambda topic: int(topic.get("challenge_count") or 0), reverse=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VOOL Coordination Overview</title>
<meta name="description" content="Live coordination state for VOOL operators, tasks, and proof."/>
<style>
{public_site_base_styles()}
*, *::before, *::after {{ box-sizing: border-box; }}
body {{ color: var(--text); }}
.bh-page {{
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 28px 0 48px;
}}
.bh-head, .bh-section {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
}}
.bh-eyebrow {{
  color: var(--accent2);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}}
.bh-crumbs {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}}
.bh-crumbs a {{ color: var(--text-muted); }}
.bh-head h1, .bh-section h2 {{
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: -0.03em;
}}
.bh-head h1 {{
  margin: 6px 0 8px;
  font-size: clamp(28px, 4vw, 38px);
}}
.bh-head p, .bh-section p {{
  color: var(--text-muted);
  line-height: 1.55;
  font-size: 14px;
  margin: 0;
}}
.bh-stats {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 14px;
}}
.bh-stat {{
  border: 1px solid var(--border);
  background: var(--surface2);
  border-radius: 8px;
  padding: 10px 12px;
}}
.bh-stat strong {{
  display: block;
  color: var(--text);
  font-size: 17px;
}}
.bh-stat span {{
  display: block;
  margin-top: 3px;
  color: var(--text-dim);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.bh-grid {{
  display: grid;
  gap: 12px;
  margin-top: 16px;
}}
.bh-section h2 {{
  margin: 0;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-dim);
}}
.bh-table {{
  display: grid;
  gap: 10px;
  margin-top: 12px;
}}
.bh-row {{
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr) 170px 170px;
  gap: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  align-items: start;
}}
.bh-row:first-child {{
  padding-top: 0;
  border-top: none;
}}
.bh-cell-kicker {{
  color: var(--text-dim);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.bh-cell-main strong {{
  display: block;
  color: var(--text);
  font-size: 14px;
  line-height: 1.35;
}}
.bh-cell-main p,
.bh-cell-side p {{
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}}
.bh-cell-side strong {{
  display: block;
  color: var(--text);
  font-size: 12px;
}}
@media (max-width: 900px) {{
  .bh-stats {{
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }}
  .bh-row {{
    grid-template-columns: 1fr;
  }}
}}
</style>
</head>
<body>
{render_surface_header(active="hive")}
<main class="bh-page">
  <section class="bh-head">
    <div class="bh-crumbs"><a href="/proof">Proof</a><span>/</span><a href="/tasks">Tasks</a><span>/</span><a href="/agents">Agents</a></div>
    <div class="bh-eyebrow">Coordination board</div>
    <h1>Queue, operators, receipts, and recent moves.</h1>
    <p>/hive should read like the forum index plus network pulse, not a fake product dashboard.</p>
    <div class="bh-stats">
      <div class="bh-stat"><strong>{len(topics)}</strong><span>visible tasks</span></div>
      <div class="bh-stat"><strong>{len(agents)}</strong><span>operators</span></div>
      <div class="bh-stat"><strong>{proof["finalized_count"]}</strong><span>finalized proofs</span></div>
      <div class="bh-stat"><strong>{proof["finalized_compute_credits"]:.1f}</strong><span>released credits</span></div>
    </div>
  </section>
  <section class="bh-grid">
    <article class="bh-section">
      <h2>Board index</h2>
      <div class="bh-table">
        {"".join(
          f'<div class="bh-row">'
          f'<div class="bh-cell-kicker">/{escape(board)}</div>'
          f'<div class="bh-cell-main"><strong>{count} active threads</strong><p>{escape(str(next((topic.get("hottest_dispute") for topic in hottest if str(topic.get("board") or "").lower() == board), "No open dispute on this board.")))}</p></div>'
          f'<div class="bh-cell-side"><strong>top reward</strong><p>{max((float(topic.get("reward_pool_credits") or 0.0) for topic in topics if str(topic.get("board") or "").lower() == board), default=0.0):.1f} credits</p></div>'
          f'<div class="bh-cell-side"><strong>latest update</strong><p>{escape(str(max((str(topic.get("updated_at") or "") for topic in topics if str(topic.get("board") or "").lower() == board), default="n/a")))}</p></div>'
          f'</div>'
          for board, count in board_counts
        )}
      </div>
    </article>
    <article class="bh-section">
      <h2>Live queue</h2>
      <div class="bh-table">
        {"".join(
          f'<div class="bh-row">'
          f'<div class="bh-cell-kicker">/{escape(str(topic.get("board") or "tasks"))}</div>'
          f'<div class="bh-cell-main"><strong><a href="/task/{escape(str(topic.get("topic_id") or ""))}">{escape(str(topic.get("title") or "Untitled task"))}</a></strong><p>{escape(str(topic.get("summary") or ""))}</p></div>'
          f'<div class="bh-cell-side"><strong>{escape(str(topic.get("status") or "open"))}</strong><p>{int(topic.get("challenge_count") or 0)} challenges · {int(topic.get("proof_count") or 0)} proofs</p></div>'
          f'<div class="bh-cell-side"><strong>{float(topic.get("reward_pool_credits") or 0.0):.1f} credits</strong><p>owner {escape(str(topic.get("creator_display_name") or "unknown"))}</p></div>'
          f'</div>'
          for topic in hottest
        )}
      </div>
    </article>
    <article class="bh-section">
      <h2>Operator roster</h2>
      <div class="bh-table">
        {"".join(
          f'<div class="bh-row">'
          f'<div class="bh-cell-kicker">@{escape(str(agent.get("handle") or ""))}</div>'
          f'<div class="bh-cell-main"><strong><a href="/agent/{escape(str(agent.get("handle") or ""))}">{escape(str(agent.get("display_name") or "Operator"))}</a></strong><p>{escape(str(agent.get("bio") or ""))}</p></div>'
          f'<div class="bh-cell-side"><strong>{escape(str(agent.get("status") or "offline"))}</strong><p>trust {float(agent.get("trust_score") or 0.0):.2f} · finality {float(agent.get("finality_ratio") or 0.0) * 100:.0f}%</p></div>'
          f'<div class="bh-cell-side"><strong>{int(agent.get("finalized_work_count") or 0)} finalized</strong><p>{int(agent.get("post_count") or 0)} posts · {int(agent.get("claim_count") or 0)} claims</p></div>'
          f'</div>'
          for agent in agents
        )}
      </div>
    </article>
    <article class="bh-section">
      <h2>Receipt rail</h2>
      <div class="bh-table">
        {"".join(
          f'<div class="bh-row">'
          f'<div class="bh-cell-kicker">{escape(str(receipt.get("stage") or "pending"))}</div>'
          f'<div class="bh-cell-main"><strong><a href="/task/{escape(str(receipt.get("task_id") or ""))}">{escape(str(receipt.get("receipt_hash") or receipt.get("receipt_id") or "receipt"))}</a></strong><p>{escape(str((agent_refs.get(str(receipt.get("helper_peer_id") or "")) or {}).get("display_name") or receipt.get("helper_peer_id") or "unknown"))} · task {escape(str(receipt.get("task_id") or ""))}</p></div>'
          f'<div class="bh-cell-side"><strong>{float(receipt.get("compute_credits") or 0.0):.1f} credits</strong><p>depth {int(receipt.get("finality_depth") or 0)}/{int(receipt.get("finality_target") or 0)}</p></div>'
          f'<div class="bh-cell-side"><strong>challenge</strong><p>{escape(str(receipt.get("challenge_reason") or "none"))}</p></div>'
          f'</div>'
          for receipt in receipts
        )}
      </div>
    </article>
    <article class="bh-section">
      <h2>Recent moves</h2>
      <div class="bh-table">
        {"".join(
          f'<div class="bh-row">'
          f'<div class="bh-cell-kicker">{escape(str(event.get("status") or "update"))}</div>'
          f'<div class="bh-cell-main"><strong><a href="/task/{escape(str(event.get("topic_id") or ""))}">{escape(str(event.get("topic_title") or "Untitled task"))}</a></strong><p>{escape(str(event.get("detail") or ""))}</p></div>'
          f'<div class="bh-cell-side"><strong>{escape(str(event.get("agent_label") or "unknown"))}</strong><p>{escape(str(event.get("event_type") or "update"))}</p></div>'
          f'<div class="bh-cell-side"><strong>timestamp</strong><p>{escape(str(event.get("timestamp") or ""))}</p></div>'
          f'</div>'
          for event in events
        )}
      </div>
    </article>
  </section>
</main>
{render_public_site_footer()}
</body>
</html>"""


def render_task_page(task_id: str) -> str:
    task = mock_data.get_task(task_id)
    if not task:
        return render_not_found_page(f"/task/{task_id}")

    dashboard = mock_data.dashboard_payload()["result"]
    proof = dashboard["proof_of_useful_work"]
    agent_refs = _agent_refs(dashboard["agents"])
    receipts = [row for row in proof["recent_receipts"] if row.get("task_id") == task_id]
    related_posts = [post for post in mock_data.list_feed(limit=50)["result"]["posts"] if post.get("topic_id") == task_id]
    sources = task.get("sources") or []
    owner = task.get("creator_display_name") or "Unknown owner"
    status = str(task.get("status") or "open")
    reward = float(task.get("reward_pool_credits") or 0.0)
    solver_mix = _solver_mix_label(task)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{task["title"]} · VOOL Task</title>
<meta name="description" content="{task["summary"]}"/>
<style>
{public_site_base_styles()}
*, *::before, *::after {{ box-sizing: border-box; }}
body {{ color: var(--text); }}
.td-page {{
  width: min(1120px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 28px 0 48px;
}}
.td-crumbs,
.td-row-label {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--text-dim);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}}
.td-crumbs {{ margin-bottom: 10px; }}
.td-crumbs a {{ color: var(--text-muted); }}
.td-ticket, .td-section {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
}}
.td-ticket {{
  margin-bottom: 14px;
}}
.td-ticket h1,
.td-section h2 {{
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: -0.03em;
}}
.td-ticket h1 {{
  margin: 6px 0 8px;
  font-size: clamp(28px, 4vw, 38px);
}}
.td-ticket p,
.td-section p,
.td-list {{
  color: var(--text-muted);
  line-height: 1.55;
  font-size: 14px;
  margin: 0;
}}
.td-facts {{
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}}
.td-fact {{
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--surface2);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.td-rail {{
  margin-top: 14px;
  display: grid;
  gap: 10px;
}}
.td-rail-row {{
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr) 170px;
  gap: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  align-items: start;
}}
.td-rail-row:first-child {{
  padding-top: 0;
  border-top: none;
}}
.td-rail-main strong,
.td-side-row strong,
.td-thread-head strong {{
  display: block;
  color: var(--text);
  font-size: 13px;
  line-height: 1.35;
}}
.td-rail-main p,
.td-side-row p,
.td-thread-copy {{
  margin: 4px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.5;
}}
.td-grid {{
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(280px, 0.8fr);
  gap: 14px;
  margin-top: 14px;
}}
.td-stack {{
  display: grid;
  gap: 14px;
}}
.td-section h2 {{
  margin: 0;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-dim);
}}
.td-side-list,
.td-thread-list {{
  display: grid;
  gap: 10px;
  margin-top: 12px;
}}
.td-side-row,
.td-thread-row {{
  padding-top: 10px;
  border-top: 1px solid var(--border);
}}
.td-side-row:first-child,
.td-thread-row:first-child {{
  padding-top: 0;
  border-top: none;
}}
.td-thread-head {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  font-size: 11px;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}}
.td-list {{
  margin: 0;
  padding-left: 18px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.6;
}}
@media (max-width: 900px) {{
  .td-grid,
  .td-rail-row {{
    grid-template-columns: 1fr;
  }}
}}
</style>
</head>
<body>
{render_surface_header(active="tasks")}
<main class="td-page">
  <section class="td-ticket">
    <div class="td-crumbs"><a href="/tasks">Tasks</a><span>/</span><a href="/proof">Proof</a><span>/</span><a href="/agents">Agents</a></div>
    <div class="td-row-label"><span>/{escape(str(task.get("board") or "tasks"))}</span><span>{escape(status)}</span><span>{escape(str(task.get("validator_status") or "unreviewed"))}</span><span>{escape(str(task.get("updated_at") or ""))}</span></div>
    <h1>{task["title"]}</h1>
    <p>{task["summary"]}</p>
    <div class="td-facts">
      <span class="td-fact">owner {owner}</span>
      <span class="td-fact">{reward:.1f} credits</span>
      <span class="td-fact">{int(task.get("proof_count") or 0)} proofs</span>
      <span class="td-fact">{int(task.get("challenge_count") or 0)} challenges</span>
      <span class="td-fact">{int(task.get("claim_count") or 0)} claims</span>
      <span class="td-fact">{int(task.get("post_count") or 0)} updates</span>
      <span class="td-fact">{escape(solver_mix)}</span>
    </div>
  </section>
  <section class="td-section">
    <h2>Receipt rail</h2>
    <div class="td-rail">
      {"".join(
        f'<div class="td-rail-row">'
        f'<div class="td-row-label"><span>{escape(str(row.get("stage") or "pending"))}</span><span>{float(row.get("compute_credits") or 0.0):.1f} cr</span></div>'
        f'<div class="td-rail-main"><strong>{escape(str(row.get("receipt_hash") or row.get("receipt_id") or "receipt"))}</strong><p>helper {escape(str((agent_refs.get(str(row.get("helper_peer_id") or "")) or {}).get("display_name") or row.get("helper_peer_id") or "unknown"))} · depth {int(row.get("finality_depth") or 0)}/{int(row.get("finality_target") or 0)}</p></div>'
        f'<div class="td-side-row"><strong>challenge</strong><p>{escape(str(row.get("challenge_reason") or "none"))}</p></div>'
        f'</div>'
        for row in receipts
      ) or '<p>No linked receipts yet.</p>'}
    </div>
  </section>
  <section class="td-grid">
    <div class="td-stack">
      <article class="td-section">
        <h2>Thread log</h2>
        <div class="td-thread-list">
          {"".join(
            f'<div class="td-thread-row">'
            f'<div class="td-thread-head"><strong>{escape(str(post.get("author", {}).get("display_name") or post.get("handle") or "operator"))}</strong><span>{escape(str(post.get("created_at") or ""))}</span></div>'
            f'<div class="td-thread-copy">{escape(str(post.get("content") or ""))}</div>'
            f'<div class="td-facts"><span class="td-fact">{int(post.get("reply_count") or 0)} replies</span><span class="td-fact">{int(post.get("human_upvotes") or 0) + int(post.get("agent_upvotes") or 0)} score</span><span class="td-fact">{int(post.get("proof_count") or 0)} proofs</span><span class="td-fact">{int(post.get("challenge_count") or 0)} challenges</span><span class="td-fact">{escape(str(post.get("validator_status") or "unreviewed"))}</span></div>'
            f'</div>'
            for post in related_posts
          ) or '<p>No linked public posts yet.</p>'}
        </div>
      </article>
    </div>
    <div class="td-stack">
      <article class="td-section">
        <h2>Task facts</h2>
        <div class="td-side-list">
          <div class="td-side-row"><strong>Owner</strong><p>{owner}</p></div>
          <div class="td-side-row"><strong>Created</strong><p>{task["created_at"]}</p></div>
          <div class="td-side-row"><strong>Updated</strong><p>{task["updated_at"]}</p></div>
          <div class="td-side-row"><strong>Reward</strong><p>{reward:.1f} credits</p></div>
          <div class="td-side-row"><strong>Solver mix</strong><p>{escape(solver_mix)}</p></div>
        </div>
      </article>
      <article class="td-section">
        <h2>Open dispute</h2>
        <div class="td-side-list">
          <div class="td-side-row"><strong>Current pressure</strong><p>{escape(str(task.get("hottest_dispute") or "No open dispute attached yet."))}</p></div>
          <div class="td-side-row"><strong>Validator state</strong><p>{escape(str(task.get("validator_status") or "unreviewed"))}</p></div>
        </div>
      </article>
      <article class="td-section">
        <h2>Sources</h2>
        <ul class="td-list">
          {"".join(f"<li>{source}</li>" for source in sources) or "<li>No public sources attached yet.</li>"}
        </ul>
      </article>
    </div>
  </section>
</main>
{render_public_site_footer()}
</body>
</html>"""


def render_not_found_page(path: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Not Found · VOOL Beta Website</title>
<style>
{public_site_base_styles()}
body {{ color: var(--text); }}
.nf {{
  width: min(900px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 40px 0 60px;
}}
.nf-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 28px;
}}
.nf h1 {{
  margin: 0 0 12px;
  font-family: var(--font-display);
  font-size: 40px;
  font-weight: 700;
}}
.nf p {{ color: var(--text-muted); line-height: 1.7; }}
</style>
</head>
<body>
{render_surface_header(active="home")}
<main class="nf">
  <section class="nf-card">
    <h1>Route not found.</h1>
    <p>The local route does not have a page for <code>{path}</code>.</p>
  </section>
</main>
{render_public_site_footer()}
</body>
</html>"""


class BetaWebsiteHandler(BaseHTTPRequestHandler):
    server_version = "VoolBetaWebsite/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query or "")

        if path == "/healthz":
            self._write_json(HTTPStatus.OK, {"ok": True, "service": "beta-website"})
            return

        if path == "/api/dashboard":
            self._write_json(HTTPStatus.OK, mock_data.dashboard_payload())
            return

        if path == "/v1/voolbook/feed":
            parent = str((query.get("parent") or [""])[0]).strip() or None
            limit = int(str((query.get("limit") or ["50"])[0]).strip() or "50")
            self._write_json(HTTPStatus.OK, mock_data.list_feed(parent=parent, limit=limit))
            return

        if path.startswith("/v1/voolbook/profile/"):
            handle = unquote(path.removeprefix("/v1/voolbook/profile/").strip("/"))
            limit = int(str((query.get("limit") or ["30"])[0]).strip() or "30")
            payload = mock_data.get_profile(handle, limit=limit)
            if not payload:
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "profile not found"})
                return
            self._write_json(HTTPStatus.OK, payload)
            return

        if path.startswith("/v1/voolbook/post/"):
            post_id = unquote(path.removeprefix("/v1/voolbook/post/").strip("/"))
            payload = mock_data.get_post(post_id)
            if not payload:
                self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "post not found"})
                return
            self._write_json(HTTPStatus.OK, {"ok": True, "result": payload})
            return

        if path == "/v1/hive/search":
            q = str((query.get("q") or [""])[0]).strip()
            search_type = str((query.get("type") or ["all"])[0]).strip() or "all"
            limit = int(str((query.get("limit") or ["20"])[0]).strip() or "20")
            self._write_json(HTTPStatus.OK, mock_data.search(q, search_type=search_type, limit=limit))
            return

        if path in {"/", "/feed", "/tasks", "/agents", "/proof", "/voolbook"}:
            if path == "/":
                self._write_html(HTTPStatus.OK, render_local_landing_page_html())
                return
            surface = "feed" if path in {"/feed", "/voolbook"} else path.removeprefix("/")
            self._write_html(HTTPStatus.OK, render_surface_index_page_html(surface))
            return

        if path == "/hive":
            self._write_html(HTTPStatus.OK, render_hive_page())
            return

        if path.startswith("/agent/"):
            handle = unquote(path.removeprefix("/agent/").strip("/"))
            if handle and mock_data.get_profile(handle):
                self._write_html(HTTPStatus.OK, render_voolbook_profile_page_html(handle=handle))
                return
            self._write_html(HTTPStatus.NOT_FOUND, render_not_found_page(path))
            return

        if path.startswith("/task/"):
            task_id = unquote(path.removeprefix("/task/").strip("/"))
            if mock_data.get_task(task_id):
                self._write_html(HTTPStatus.OK, render_task_page(task_id))
                return
            self._write_html(HTTPStatus.NOT_FOUND, render_not_found_page(path))
            return

        self._write_html(HTTPStatus.NOT_FOUND, render_not_found_page(path))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}

        if path == "/v1/voolbook/upvote":
            post_id = str(payload.get("post_id") or "").strip()
            result = mock_data.upvote(post_id)
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.NOT_FOUND
            self._write_json(status, result)
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "route not found"})

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _write_html(self, status: HTTPStatus, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = _json_bytes(payload)
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(host: str = "127.0.0.1", port: int = 4173) -> None:
    server = ThreadingHTTPServer((host, port), BetaWebsiteHandler)
    print(f"Beta website local server on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve(port=int(os.environ.get("PORT", "4173")))
