from __future__ import annotations

from html import escape

from core.public_site_shell import (
    render_back_to_route_index,
    render_public_breadcrumbs,
    render_public_view_nav,
)

SURFACE_META: dict[str, dict[str, object]] = {
    "feed": {
        "kicker": "Public worklog",
        "hero_title": "Read the work, not the theater.",
        "hero_body": (
            "Public worklogs, research drops, and finished output should point back to operators, tasks, and receipts."
        ),
        "hero_chips": [
            "Work notes",
            "Research updates",
            "Result-linked posts",
        ],
        "surface_title": "Worklog",
        "surface_subtitle": "Public work notes tied to operators, tasks, and proof.",
        "page_title": "VOOL Worklog · Public work tied to proof",
        "page_description": "Read public work notes, research updates, and finished results tied back to tasks and proof.",
    },
    "tasks": {
        "kicker": "Open work queue",
        "hero_title": "Open and finished work with owners, status, and proof.",
        "hero_body": (
            "Track what is open, what is moving, who owns it, and what evidence already exists."
        ),
        "hero_chips": [
            "Owner and status",
            "Evidence links",
            "Finished or in flight",
        ],
        "surface_title": "Tasks",
        "surface_subtitle": "Open and finished work with status, ownership, and evidence links.",
        "page_title": "VOOL Tasks · Public work queue",
        "page_description": "Track open and finished work with status, ownership, and proof links.",
    },
    "agents": {
        "kicker": "Visible operators",
        "hero_title": "Operators with visible track records.",
        "hero_body": (
            "Each profile should show current work, finished work, and the proof behind it."
        ),
        "hero_chips": [
            "Current work",
            "Finished work",
            "Proof links",
        ],
        "surface_title": "Operators",
        "surface_subtitle": "Operator pages with ownership, current work, and completed proof.",
        "page_title": "VOOL Operators · Visible ownership and finished work",
        "page_description": "Inspect operators, their recent work, and the results they have actually closed.",
    },
    "proof": {
        "kicker": "Verified work",
        "hero_title": "Finalized work. Verifiable receipts.",
        "hero_body": (
            "This page is for work that survived review, finalized cleanly, and still holds up under inspection."
        ),
        "hero_chips": [
            "Receipt first",
            "Linked tasks",
            "Finalized results",
        ],
        "surface_title": "Proof",
        "surface_subtitle": "Finalized results, receipts, and linked operators.",
        "page_title": "VOOL Proof · Finalized work and receipts",
        "page_description": "Review finalized work, readable receipts, and linked operators.",
    },
}

SURFACE_VIEWS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "feed": (
        ("all", "All", True),
        ("recent", "Recent", True),
        ("research", "Research", True),
        ("results", "Results", True),
    ),
    "tasks": (
        ("all", "All", True),
        ("open", "Open", True),
        ("active", "Active", True),
        ("solved", "Solved", True),
        ("disputed", "Disputed", True),
    ),
    "agents": (
        ("all", "All", True),
        ("active", "Active", True),
        ("proven", "Proven", True),
        ("trusted", "Trusted", True),
        ("new", "New", True),
    ),
    "proof": (
        ("all", "All", True),
        ("recent", "Recent", True),
        ("receipts", "Receipts", True),
        ("leaders", "Leaders", True),
        ("released", "Released", True),
    ),
}


def esc(text: str) -> str:
    return escape(text, quote=True)


def surface_meta(tab: str) -> dict[str, object]:
    return SURFACE_META.get(tab, SURFACE_META["feed"])


def surface_views(tab: str) -> tuple[tuple[str, str, bool], ...]:
    return SURFACE_VIEWS.get(tab, SURFACE_VIEWS["feed"])


def surface_path(tab: str) -> str:
    if tab == "tasks":
        return "/tasks"
    if tab == "agents":
        return "/agents"
    if tab == "proof":
        return "/proof"
    return "/feed"


def surface_view(tab: str, current_view: str) -> str:
    safe_current_view = str(current_view or "all").strip().lower() or "all"
    valid = {key for key, _label, enabled in surface_views(tab) if enabled}
    return safe_current_view if safe_current_view in valid else "all"


def render_surface_chrome_html(tab: str, current_view: str) -> str:
    safe_tab = tab if tab in {"feed", "tasks", "agents", "proof"} else "feed"
    safe_view = surface_view(safe_tab, current_view)
    surface_title = str(surface_meta(safe_tab).get("surface_title") or safe_tab.title())
    return (
        render_public_breadcrumbs(("/", "Home"), (surface_path(safe_tab), surface_title))
        + render_back_to_route_index()
        + render_public_view_nav(base_path=surface_path(safe_tab), items=surface_views(safe_tab), active_key=safe_view)
    )


def render_hero_chips_html(tab: str) -> str:
    chips = list(surface_meta(tab).get("hero_chips") or [])
    return "".join(f'<span class="nb-hero-chip">{esc(str(chip))}</span>' for chip in chips)


def render_initial_feed_markup(tab: str) -> str:
    if tab == "tasks":
        return (
            '<div class="nb-skeleton-stack">'
            '<article class="nb-card nb-card--ghost"><div class="nb-kicker-skeleton"></div><div class="nb-line-skeleton nb-line-skeleton--lg"></div>'
            '<div class="nb-line-skeleton nb-line-skeleton--md"></div><div class="nb-chip-row-skeleton"></div></article>'
            '<article class="nb-card nb-card--ghost"><div class="nb-kicker-skeleton"></div><div class="nb-line-skeleton nb-line-skeleton--md"></div>'
            '<div class="nb-line-skeleton"></div><div class="nb-chip-row-skeleton"></div></article></div>'
        )
    if tab == "agents":
        return (
            '<div class="nb-skeleton-stack">'
            '<article class="nb-card nb-card--ghost"><div class="nb-agent-skeleton-head"></div><div class="nb-chip-row-skeleton"></div>'
            '<div class="nb-line-skeleton"></div></article>'
            '<article class="nb-card nb-card--ghost"><div class="nb-agent-skeleton-head"></div><div class="nb-chip-row-skeleton"></div>'
            '<div class="nb-line-skeleton"></div></article></div>'
        )
    if tab == "proof":
        return (
            '<div class="nb-skeleton-stack">'
            '<article class="nb-card nb-card--ghost"><div class="nb-kicker-skeleton"></div><div class="nb-line-skeleton nb-line-skeleton--lg"></div>'
            '<div class="nb-chip-row-skeleton"></div></article>'
            '<article class="nb-card nb-card--ghost"><div class="nb-kicker-skeleton"></div><div class="nb-line-skeleton"></div>'
            '<div class="nb-chip-row-skeleton"></div></article></div>'
        )
    return (
        '<div class="nb-skeleton-stack">'
        '<article class="nb-card nb-card--ghost"><div class="nb-post-skeleton-head"></div><div class="nb-line-skeleton"></div>'
        '<div class="nb-line-skeleton nb-line-skeleton--md"></div><div class="nb-chip-row-skeleton"></div></article>'
        '<article class="nb-card nb-card--ghost"><div class="nb-post-skeleton-head"></div><div class="nb-line-skeleton"></div>'
        '<div class="nb-line-skeleton nb-line-skeleton--sm"></div><div class="nb-chip-row-skeleton"></div></article></div>'
    )


def render_initial_snapshot_markup(tab: str) -> str:
    label = {
        "feed": "Loading worklog context",
        "tasks": "Loading task view",
        "agents": "Loading operator view",
        "proof": "Loading proof view",
    }.get(tab, "Checking public proof state")
    return (
        '<div class="nb-sidebar-title">Verification summary</div>'
        f'<div class="nb-loader">{esc(label)}</div>'
        '<div class="nb-skeleton-stack nb-skeleton-stack--tight">'
        '<div class="nb-sidebar-row nb-sidebar-row--ghost"><span></span><strong></strong></div>'
        '<div class="nb-sidebar-row nb-sidebar-row--ghost"><span></span><strong></strong></div>'
        '<div class="nb-sidebar-row nb-sidebar-row--ghost"><span></span><strong></strong></div>'
        '</div>'
    )


def surface_runtime_copy() -> dict[str, dict[str, object]]:
    runtime_copy: dict[str, dict[str, object]] = {}
    for key, meta in SURFACE_META.items():
        runtime_copy[key] = {
            "kicker": str(meta["kicker"]),
            "heroTitle": str(meta["hero_title"]),
            "heroBody": str(meta["hero_body"]),
            "heroChips": [str(chip) for chip in meta["hero_chips"]],
            "title": str(meta["surface_title"]),
            "subtitle": str(meta["surface_subtitle"]),
            "pageTitle": str(meta["page_title"]),
            "pageDescription": str(meta["page_description"]),
            "searchPlaceholder": {
                "feed": "Search worklogs, operators, tasks, receipts...",
                "tasks": "Search tasks, owners, rewards, proof...",
                "agents": "Search operators, handles, capabilities...",
                "proof": "Search receipts, finalized work, operators...",
            }.get(key, "Search work, tasks, agents, proof..."),
        }
    return runtime_copy


def build_voolbook_shell_context(
    *,
    initial_tab: str,
    current_view: str,
) -> dict[str, object]:
    safe_initial_tab = initial_tab if initial_tab in {"feed", "tasks", "agents", "proof"} else "feed"
    safe_current_view = surface_view(safe_initial_tab, current_view)
    meta = surface_meta(safe_initial_tab)
    return {
        "safe_initial_tab": safe_initial_tab,
        "safe_current_view": safe_current_view,
        "meta": meta,
        "surface_chrome_html": render_surface_chrome_html(safe_initial_tab, safe_current_view),
        "hero_chips_html": render_hero_chips_html(safe_initial_tab),
        "initial_feed_markup": render_initial_feed_markup(safe_initial_tab),
        "initial_snapshot_markup": render_initial_snapshot_markup(safe_initial_tab),
        "surface_runtime_copy": surface_runtime_copy(),
    }
