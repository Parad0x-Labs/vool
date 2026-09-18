from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any


def asgi_request(
    app: Any,
    *,
    method: str,
    path: str,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
    query_string: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    sent_body = False
    messages: list[dict[str, Any]] = []

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string,
        "headers": [
            (str(key).lower().encode("latin-1"), str(value).encode("latin-1"))
            for key, value in dict(headers or {}).items()
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if sent_body:
            return {"type": "http.disconnect"}
        sent_body = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))

    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in list(start.get("headers") or [])
    }
    response_body = b"".join(
        bytes(message.get("body") or b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), response_headers, response_body
