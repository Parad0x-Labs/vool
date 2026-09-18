from __future__ import annotations

from core.runtime_task_rail_panel_session_styles import (
    RUNTIME_TASK_RAIL_PANEL_SESSION_STYLES,
)
from core.runtime_task_rail_panel_shell_styles import (
    RUNTIME_TASK_RAIL_PANEL_SHELL_STYLES,
)
from core.runtime_task_rail_panel_trace_styles import (
    RUNTIME_TASK_RAIL_PANEL_TRACE_STYLES,
)

RUNTIME_TASK_RAIL_PANEL_STYLES = (
    RUNTIME_TASK_RAIL_PANEL_SHELL_STYLES
    + RUNTIME_TASK_RAIL_PANEL_SESSION_STYLES
    + RUNTIME_TASK_RAIL_PANEL_TRACE_STYLES
)
