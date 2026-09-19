from __future__ import annotations

from core.runtime_task_rail_assets import (
    RUNTIME_TASK_RAIL_SHELL_HTML,
    RUNTIME_TASK_RAIL_STYLE_BLOCK,
)
from core.vool_workstation_ui import (
    render_workstation_header,
    render_workstation_script,
    render_workstation_styles,
)


def render_runtime_task_rail_document(*, client_script: str) -> str:
    workstation_header = render_workstation_header(
        title="VOOL Operator Workstation",
        subtitle="Trace turns runtime causality, retries, stop reasons, and artifacts into one inspectable execution lane.",
        default_mode="trace",
        surface="trace-rail",
    )
    shell_html = RUNTIME_TASK_RAIL_SHELL_HTML.replace("__WORKSTATION_HEADER__", workstation_header)
    return (
        _TASK_RAIL_DOCUMENT_TEMPLATE
        .replace("__WORKSTATION_STYLES__", render_workstation_styles())
        .replace("__RUNTIME_TASK_RAIL_STYLES__", RUNTIME_TASK_RAIL_STYLE_BLOCK)
        .replace("__RUNTIME_TASK_RAIL_SHELL__", shell_html)
        .replace("__WORKSTATION_SCRIPT__", render_workstation_script())
        .replace("__TASK_RAIL_CLIENT_SCRIPT__", client_script)
    )


_TASK_RAIL_DOCUMENT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>VOOL Trace Rail</title>
  <style>
    __WORKSTATION_STYLES__
__RUNTIME_TASK_RAIL_STYLES__
  </style>
</head>
<body>
__RUNTIME_TASK_RAIL_SHELL__

  <script>
    __WORKSTATION_SCRIPT__
    __TASK_RAIL_CLIENT_SCRIPT__
  </script>
</body>
</html>
"""
