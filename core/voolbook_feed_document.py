from __future__ import annotations

from core.voolbook_feed_cards import VOOLBOOK_CARD_RENDERERS
from core.voolbook_feed_markup import render_voolbook_feed_markup
from core.voolbook_feed_post_interactions import VOOLBOOK_POST_INTERACTION_RUNTIME
from core.voolbook_feed_search_runtime import VOOLBOOK_SEARCH_RUNTIME
from core.voolbook_feed_shell import build_voolbook_shell_context, esc, surface_path
from core.voolbook_feed_styles import render_voolbook_feed_document_styles
from core.voolbook_feed_surface_runtime import render_voolbook_feed_surface_runtime
from core.public_site_shell import (
    canonical_public_url,
    public_site_base_styles,
    render_public_canonical_meta,
    render_public_site_footer,
    render_surface_header,
)


def render_voolbook_page_document(
    *,
    api_base: str = "",
    og_title: str = "",
    og_description: str = "",
    og_url: str = "",
    initial_tab: str = "feed",
    current_view: str = "",
    canonical_url: str = "",
) -> str:
    shell_context = build_voolbook_shell_context(
        initial_tab=initial_tab,
        current_view=current_view,
    )
    safe_initial_tab = str(shell_context["safe_initial_tab"])
    safe_current_view = str(shell_context["safe_current_view"])
    meta = dict(shell_context["meta"])
    page_title = str(meta.get("page_title") or f'VOOL {meta["surface_title"]}')
    page_description = str(meta.get("page_description") or str(meta["surface_subtitle"]))
    og_title = og_title or page_title
    og_description = og_description or page_description
    canonical_url = canonical_url or canonical_public_url(
        surface_path(safe_initial_tab),
        query={"view": safe_current_view} if safe_current_view != "all" else None,
    )
    og_url = og_url or canonical_url
    runtime_block = (
        VOOLBOOK_CARD_RENDERERS
        + render_voolbook_feed_surface_runtime(
            api_base=api_base or "",
            initial_tab=safe_initial_tab,
            initial_view=safe_current_view,
            surface_copy=dict(shell_context["surface_runtime_copy"]),
        )
        + VOOLBOOK_SEARCH_RUNTIME
        + VOOLBOOK_POST_INTERACTION_RUNTIME
    )
    og_block = render_public_canonical_meta(
        canonical_url=og_url,
        og_title=og_title,
        og_description=og_description,
        og_type="article",
    )
    return render_voolbook_feed_markup(
        page_title=esc(page_title),
        page_description=esc(page_description),
        og_meta_block=og_block,
        site_base_styles=public_site_base_styles(),
        document_styles=render_voolbook_feed_document_styles(),
        surface_header=render_surface_header(active=safe_initial_tab),
        site_footer=render_public_site_footer(),
        surface_chrome=str(shell_context["surface_chrome_html"]),
        surface_kicker=esc(str(meta["kicker"])),
        surface_hero_title=esc(str(meta["hero_title"])),
        surface_hero_body=esc(str(meta["hero_body"])),
        surface_hero_chips=str(shell_context["hero_chips_html"]),
        surface_title=esc(str(meta["surface_title"])),
        surface_subtitle=esc(str(meta["surface_subtitle"])),
        initial_feed_markup=str(shell_context["initial_feed_markup"]),
        initial_snapshot=str(shell_context["initial_snapshot_markup"]),
        runtime_block=runtime_block,
    )
