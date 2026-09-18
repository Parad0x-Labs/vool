from __future__ import annotations

from core.dashboard.workstation_render_shell_components import (
    WORKSTATION_RENDER_SHELL_COMPONENTS,
)
from core.dashboard.workstation_render_shell_layout import (
    WORKSTATION_RENDER_SHELL_LAYOUT,
)
from core.dashboard.workstation_render_shell_primitives import (
    WORKSTATION_RENDER_SHELL_PRIMITIVES,
)

WORKSTATION_RENDER_SHELL_STYLES = (
    WORKSTATION_RENDER_SHELL_PRIMITIVES
    + WORKSTATION_RENDER_SHELL_COMPONENTS
    + WORKSTATION_RENDER_SHELL_LAYOUT
)
