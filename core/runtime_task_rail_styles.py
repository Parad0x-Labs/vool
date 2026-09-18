from core.runtime_task_rail_event_feed_styles import (
    RUNTIME_TASK_RAIL_EVENT_FEED_STYLES,
)
from core.runtime_task_rail_panel_styles import RUNTIME_TASK_RAIL_PANEL_STYLES
from core.runtime_task_rail_trace_styles import RUNTIME_TASK_RAIL_TRACE_STYLES
from core.runtime_task_rail_workbench_styles import (
    RUNTIME_TASK_RAIL_WORKBENCH_STYLES,
)

RUNTIME_TASK_RAIL_STYLE_BLOCK = (
    RUNTIME_TASK_RAIL_PANEL_STYLES
    + RUNTIME_TASK_RAIL_TRACE_STYLES
    + RUNTIME_TASK_RAIL_EVENT_FEED_STYLES
    + RUNTIME_TASK_RAIL_WORKBENCH_STYLES
)
