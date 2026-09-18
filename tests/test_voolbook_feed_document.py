from __future__ import annotations

from core.voolbook_feed_base_styles import VOOLBOOK_FEED_BASE_STYLES
from core.voolbook_feed_interaction_styles import VOOLBOOK_FEED_INTERACTION_STYLES
from core.voolbook_feed_layout_styles import VOOLBOOK_FEED_LAYOUT_STYLES
from core.voolbook_feed_markup import render_voolbook_feed_markup
from core.voolbook_feed_overlay_styles import VOOLBOOK_FEED_OVERLAY_STYLES
from core.voolbook_feed_search_styles import VOOLBOOK_FEED_SEARCH_STYLES
from core.voolbook_feed_sidebar_styles import VOOLBOOK_FEED_SIDEBAR_STYLES
from core.voolbook_feed_skeleton_styles import VOOLBOOK_FEED_SKELETON_STYLES
from core.voolbook_feed_styles import render_voolbook_feed_document_styles


def test_voolbook_feed_document_styles_keep_primary_layout_fragments() -> None:
    styles = render_voolbook_feed_document_styles()

    assert ".nb-layout" in styles
    assert ".nb-hero" in styles
    assert ".nb-overlay" in styles
    assert ".nb-search-wrap" in styles
    assert ".nb-layout" in VOOLBOOK_FEED_BASE_STYLES
    assert ".nb-layout" in VOOLBOOK_FEED_LAYOUT_STYLES
    assert ".nb-post-footer" in VOOLBOOK_FEED_INTERACTION_STYLES
    assert ".nb-snapshot-block" in VOOLBOOK_FEED_SKELETON_STYLES
    assert ".nb-hero" in VOOLBOOK_FEED_SIDEBAR_STYLES
    assert ".nb-search-wrap" in VOOLBOOK_FEED_SEARCH_STYLES
    assert ".nb-overlay" in VOOLBOOK_FEED_OVERLAY_STYLES


def test_voolbook_feed_markup_renders_document_shell_without_placeholders() -> None:
    html = render_voolbook_feed_markup(
        page_title="VOOL Worklog",
        page_description="Public work tied to proof.",
        og_meta_block="<meta property='og:title' content='VOOL Worklog'/>",
        site_base_styles=":root { --text: #fff; }",
        document_styles=".nb-layout { display: grid; }",
        surface_header="<header>Header</header>",
        surface_chrome="<nav>Chrome</nav>",
        surface_kicker="Public worklog",
        surface_hero_title="Read the work, not the theater.",
        surface_hero_body="Public worklogs point back to tasks and proof.",
        surface_hero_chips="<span>Proof</span>",
        surface_title="Worklog",
        surface_subtitle="Public work tied to proof.",
        initial_feed_markup="<article>Feed</article>",
        initial_snapshot="<section>Snapshot</section>",
        runtime_block="console.log('runtime');",
        site_footer="<footer>Footer</footer>",
    )

    assert "__PAGE_TITLE__" not in html
    assert "__RUNTIME_BLOCK__" not in html
    assert "<header>Header</header>" in html
    assert "<article>Feed</article>" in html
    assert "console.log('runtime');" in html
