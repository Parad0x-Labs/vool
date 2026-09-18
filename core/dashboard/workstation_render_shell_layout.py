from __future__ import annotations

"""Dashboard layout, inspector, and responsive shell styles."""

from core.dashboard.workstation_render_shell_inspector_styles import (
    WORKSTATION_RENDER_SHELL_INSPECTOR_STYLES,
)
from core.dashboard.workstation_render_shell_responsive_styles import (
    WORKSTATION_RENDER_SHELL_RESPONSIVE_STYLES,
)
from core.dashboard.workstation_render_shell_workbench_styles import (
    WORKSTATION_RENDER_SHELL_WORKBENCH_STYLES,
)

WORKSTATION_RENDER_SHELL_LAYOUT = (
    WORKSTATION_RENDER_SHELL_WORKBENCH_STYLES
    + WORKSTATION_RENDER_SHELL_INSPECTOR_STYLES
    + WORKSTATION_RENDER_SHELL_RESPONSIVE_STYLES
)
