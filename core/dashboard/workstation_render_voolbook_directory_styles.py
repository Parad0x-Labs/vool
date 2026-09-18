from __future__ import annotations

"""VoolBook directory and section styles for the workstation dashboard."""

from core.dashboard.workstation_render_voolbook_directory_agent_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_AGENT_STYLES,
)
from core.dashboard.workstation_render_voolbook_directory_community_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_COMMUNITY_STYLES,
)
from core.dashboard.workstation_render_voolbook_directory_surface_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_SURFACE_STYLES,
)

WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_STYLES = (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_COMMUNITY_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_AGENT_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_SURFACE_STYLES
)
