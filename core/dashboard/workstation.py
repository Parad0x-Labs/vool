from __future__ import annotations

import json
from typing import Any

from core.dashboard.workstation_render import render_workstation_document
from core.dashboard.workstation_state import build_workstation_initial_state_payload


def render_workstation_dashboard_html(
    *,
    api_endpoint: str = "/v1/hive/dashboard",
    topic_base_path: str = "/task",
    initial_mode: str = "overview",
    canonical_url: str = "",
    hooks: Any,
) -> str:
    safe_initial_mode = str(initial_mode or "overview")
    resolved_canonical_url = str(canonical_url or "")
    initial_state = json.dumps(
        build_workstation_initial_state_payload(hooks=hooks),
        sort_keys=True,
    )
    return render_workstation_document(
        initial_state=initial_state,
        api_endpoint=api_endpoint,
        topic_base_path=topic_base_path,
        initial_mode=safe_initial_mode,
        canonical_url=resolved_canonical_url,
    )
