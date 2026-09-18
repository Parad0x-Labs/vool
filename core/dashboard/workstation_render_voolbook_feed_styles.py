from __future__ import annotations

"""VoolBook feed and post styles for the workstation dashboard."""

from core.dashboard.workstation_render_voolbook_feed_layout_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FEED_LAYOUT_STYLES,
)
from core.dashboard.workstation_render_voolbook_feed_post_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FEED_POST_STYLES,
)

WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES = (
    WORKSTATION_RENDER_VOOLBOOK_FEED_LAYOUT_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_FEED_POST_STYLES
)
