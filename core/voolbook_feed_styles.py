from __future__ import annotations

from core.voolbook_feed_base_styles import VOOLBOOK_FEED_BASE_STYLES
from core.voolbook_feed_overlay_styles import VOOLBOOK_FEED_OVERLAY_STYLES
from core.voolbook_feed_search_styles import VOOLBOOK_FEED_SEARCH_STYLES
from core.voolbook_feed_sidebar_styles import VOOLBOOK_FEED_SIDEBAR_STYLES


def render_voolbook_feed_document_styles() -> str:
    return (
        VOOLBOOK_FEED_BASE_STYLES
        + VOOLBOOK_FEED_SIDEBAR_STYLES
        + VOOLBOOK_FEED_SEARCH_STYLES
        + VOOLBOOK_FEED_OVERLAY_STYLES
    )
