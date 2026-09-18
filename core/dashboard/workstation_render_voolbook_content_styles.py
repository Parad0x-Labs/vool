from __future__ import annotations

"""VoolBook content styles for the workstation dashboard."""

from core.dashboard.workstation_render_voolbook_directory_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_STYLES,
)
from core.dashboard.workstation_render_voolbook_feed_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES,
)

WORKSTATION_RENDER_VOOLBOOK_CONTENT_STYLES = (
    WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_FABRIC_STYLES
)
