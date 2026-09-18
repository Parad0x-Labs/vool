from __future__ import annotations

from core.runtime_task_rail_client import RUNTIME_TASK_RAIL_CLIENT_SCRIPT
from core.runtime_task_rail_document import render_runtime_task_rail_document


def render_runtime_task_rail_html() -> str:
    return render_runtime_task_rail_document(client_script=RUNTIME_TASK_RAIL_CLIENT_SCRIPT)
