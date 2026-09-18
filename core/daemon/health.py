from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core.web.request_ids import log_http_request, resolve_request_id, response_headers_with_request_id
from network.signer import get_local_peer_id as local_peer_id

from .models import is_loopback_host

logger = logging.getLogger("vool.daemon.health")


def _json_response(
    status_code: int,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> Response:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    return Response(
        body,
        status_code=status_code,
        media_type="application/json",
        headers=response_headers_with_request_id({"Content-Length": str(len(body))}, request_id=request_id),
    )


def create_health_app(daemon: Any) -> Starlette:
    async def _dispatch(request: Request) -> Response:
        request_id = resolve_request_id(dict(request.headers.items()))
        started = time.perf_counter()
        clean_path = request.url.path.rstrip("/") or "/"

        def _finish(response: Response) -> Response:
            latency_ms = (time.perf_counter() - started) * 1000.0
            log_http_request(
                logger,
                component="daemon-health",
                method=request.method,
                path=clean_path,
                status_code=response.status_code,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            return response

        if clean_path not in {"/healthz", "/v1/healthz"}:
            return _finish(_json_response(404, {"ok": False, "error": "not_found"}, request_id=request_id))

        require_auth = (not is_loopback_host(daemon.config.health_bind_host)) or bool(
            str(daemon.config.health_auth_token or "").strip()
        )
        if require_auth:
            header_token = str(request.headers.get("X-Vool-Health-Token") or "").strip()
            expected = str(daemon.config.health_auth_token or "").strip()
            if not expected or header_token != expected:
                return _finish(_json_response(401, {"ok": False, "error": "unauthorized"}, request_id=request_id))

        return _finish(_json_response(200, {"ok": True, "result": daemon._health_snapshot()}, request_id=request_id))

    return Starlette(
        debug=False,
        routes=[
            Route("/healthz", _dispatch, methods=["GET"]),
            Route("/v1/healthz", _dispatch, methods=["GET"]),
            Route("/{path:path}", _dispatch, methods=["GET"]),
        ],
    )


def start_health_server(daemon: Any) -> None:
    if int(daemon.config.health_bind_port) <= 0:
        return

    app = create_health_app(daemon)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=str(daemon.config.health_bind_host),
            port=int(daemon.config.health_bind_port),
            access_log=False,
            log_level="warning",
        )
    )
    daemon._health_server = server
    daemon._health_thread = threading.Thread(
        target=server.run,
        name="vool-daemon-health",
        daemon=True,
    )
    daemon._health_thread.start()


def health_snapshot(daemon: Any) -> dict[str, Any]:
    runtime = daemon._runtime
    return {
        "peer_id": local_peer_id(),
        "running": True,
        "bind_host": daemon.config.bind_host,
        "bind_port": int(daemon.config.bind_port),
        "public_host": runtime.public_host if runtime else None,
        "public_port": int(runtime.public_port) if runtime else None,
        "active_assignments": int(daemon._active_assignment_count()),
        "capacity": int(daemon.config.capacity),
        "advertised_capacity": int(daemon._refresh_advertised_capacity()),
        "maintenance_running": bool(daemon.maintenance is not None),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
