"""The production HTTP doors, called in process: the same dispatch functions the daemon serves."""
from __future__ import annotations

import json
from typing import Any


def post(path: str, body: Any, *, client_host: str = "127.0.0.1", headers: dict[str, str] | None = None) -> tuple[int, dict]:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(
        path=path,
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )
    return response.status, json.loads(response.body.decode("utf-8"))


def get(path: str, query: dict[str, list[str]] | None = None) -> dict:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    response = dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool")
    return json.loads(response.body.decode("utf-8"))


__all__ = ["get", "post"]
