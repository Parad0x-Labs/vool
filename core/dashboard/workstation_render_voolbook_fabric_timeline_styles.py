from __future__ import annotations

"""VoolBook fabric timeline styles for workstation dashboard."""

from core.dashboard.workstation_render_voolbook_fabric_timeline_event_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_EVENT_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_timeline_topic_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_TOPIC_STYLES,
)

WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_STYLES = (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_TOPIC_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_EVENT_STYLES
)
