from __future__ import annotations

"""VoolBook telemetry, fabric, proof, and onboarding styles for the workstation dashboard."""

from core.dashboard.workstation_render_voolbook_fabric_cards_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_CARDS_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_onboarding_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_ONBOARDING_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_telemetry_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TELEMETRY_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_timeline_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_STYLES,
)

WORKSTATION_RENDER_VOOLBOOK_FABRIC_STYLES = (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TELEMETRY_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_FABRIC_CARDS_STYLES
    + WORKSTATION_RENDER_VOOLBOOK_FABRIC_ONBOARDING_STYLES
)
