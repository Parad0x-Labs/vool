from __future__ import annotations

"""Shared workstation dashboard component styles."""

from core.dashboard.workstation_render_shell_card_styles import (
    WORKSTATION_RENDER_SHELL_CARD_STYLES,
)
from core.dashboard.workstation_render_shell_footer_styles import (
    WORKSTATION_RENDER_SHELL_FOOTER_STYLES,
)
from core.dashboard.workstation_render_shell_learning_styles import (
    WORKSTATION_RENDER_SHELL_LEARNING_STYLES,
)
from core.dashboard.workstation_render_shell_stat_styles import (
    WORKSTATION_RENDER_SHELL_STAT_STYLES,
)

WORKSTATION_RENDER_SHELL_COMPONENTS = (
    WORKSTATION_RENDER_SHELL_STAT_STYLES
    + WORKSTATION_RENDER_SHELL_CARD_STYLES
    + WORKSTATION_RENDER_SHELL_LEARNING_STYLES
    + WORKSTATION_RENDER_SHELL_FOOTER_STYLES
)
