from __future__ import annotations

from core.dashboard.workstation_card_normalizers import WORKSTATION_CARD_NORMALIZERS
from core.dashboard.workstation_card_render_sections import WORKSTATION_CARD_RENDER_SECTIONS

WORKSTATION_CARD_RENDERERS = (
    WORKSTATION_CARD_NORMALIZERS + WORKSTATION_CARD_RENDER_SECTIONS
)
