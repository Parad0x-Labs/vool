from __future__ import annotations

from core.agent_runtime.tool_result_history_surface import (
    DeferredToolBatchSurfaceMixin,
    ToolResultHistorySurfaceMixin,
)
from core.agent_runtime.tool_result_text_surface import ToolResultTextSurfaceMixin
from core.agent_runtime.tool_result_truth_metrics import ToolResultTruthMetricsMixin
from core.agent_runtime.tool_result_workflow_surface import ToolResultWorkflowSurfaceMixin


class ToolResultSurfaceMixin(
    ToolResultTruthMetricsMixin,
    ToolResultTextSurfaceMixin,
    ToolResultHistorySurfaceMixin,
    DeferredToolBatchSurfaceMixin,
    ToolResultWorkflowSurfaceMixin,
):
    pass
