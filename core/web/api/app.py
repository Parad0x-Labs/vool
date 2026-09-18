from __future__ import annotations

import functools
import json
import logging
import time
from collections.abc import Callable
from urllib.parse import parse_qs

import anyio.to_thread
from anyio import CapacityLimiter
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from ..request_ids import log_http_request, resolve_request_id, response_headers_with_request_id
from .runtime import RuntimeServices, default_workspace_root, host_header_allowed
from .service import (
    ApiResponse,
    dictation_body_ceiling,
    dispatch_dictation,
    dispatch_get,
    dispatch_post,
    dispatch_upload,
    is_raw_dictation_path,
    is_raw_upload_path,
    json_response,
    raw_upload_body_ceiling,
)

logger = logging.getLogger("vool.api.http")

# Global request-body ceiling applied BEFORE json.loads, so an oversized body cannot force a
# large in-memory parse on any route (the per-route 413 only guarded the settings POSTs). Chat
# and generation payloads are well under this; a bounded stream/upload path could raise it later.
_MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024  # 4 MiB

# --------------------------------------------------------------------------------------
# Liveness must not queue behind work.
#
# Every request on this server -- a 200ms `/healthz` and a two-minute buffered `/api/chat` alike --
# used `starlette.concurrency.run_in_threadpool`, which shares anyio's DEFAULT thread limiter: 40
# tokens for the whole process, and a buffered chat turn holds its token for the entire turn. So
# once ~40 chat turns are in flight, `/healthz` gets no thread at all and does not answer.
#
# That is not a cosmetic problem, because the daemon is supervised by its own health probe. The
# shipped launchd unit sets `VOOL_LAUNCHD_SUPERVISOR=1` (installer/install_vool.sh), and
# `Start_VOOL.sh` then runs `curl -sf --max-time 2 .../healthz` every 5s and SIGTERMs the API after
# THREE consecutive failures. Measured 2026-07-31 against the installed daemon:
#
#     N=20 concurrent chat turns -> 0 failed probes
#     N=40                       -> 1 failed probe
#     N=48                       -> probes 2,3,4,5,6 all failed at the 2.0s cap (~30s of silence)
#
# At N=48 the supervisor's third strike lands on probe 4. A perfectly healthy server, mid-batch,
# gets killed -- which is exactly the reported "the daemon crashed and took the whole test batch
# with it". No traceback, because there is no crash: the process was terminated from outside.
#
# Two dedicated limiters fix it. Health gets its own tokens that chat can never consume, and chat is
# capped below the process default so it cannot exhaust the pool it shares with everything else.
_HEALTH_PATHS = frozenset({"/healthz", "/v1/healthz", "/readyz", "/livez"})
_HEALTH_LIMITER = CapacityLimiter(8)
_WORK_LIMITER = CapacityLimiter(24)


def _is_liveness_path(path: str) -> bool:
    return str(path or "").rstrip("/").lower() in _HEALTH_PATHS or path == "/healthz"


async def _run_dispatch(dispatcher: Callable[..., ApiResponse], *, liveness: bool, **kwargs) -> ApiResponse:
    """Run one blocking dispatcher on the lane that matches what it is."""
    return await anyio.to_thread.run_sync(
        functools.partial(dispatcher, **kwargs),
        limiter=_HEALTH_LIMITER if liveness else _WORK_LIMITER,
    )


def _starlette_response(response: ApiResponse) -> Response:
    if response.stream is not None:
        return StreamingResponse(response.stream, status_code=response.status, media_type=response.content_type, headers=response.headers)
    payload = response.body or b""
    return Response(payload, status_code=response.status, media_type=response.content_type, headers=response.headers)


async def _dispatch(request: Request) -> Response:
    request_id = resolve_request_id(dict(request.headers.items()))
    started = time.perf_counter()
    # Record admission before dispatch, without bodies, query strings or
    # credentials. A stalled request must be distinguishable from an unsent one.
    logger.info(
        "http_request_started",
        extra={"event": "http_request_started", "component": "api",
               "request_id": request_id, "trace_id": request_id,
               "details": {"method": request.method, "path": request.url.path}},
    )
    runtime: RuntimeServices = request.app.state.runtime
    model_name: str = request.app.state.model_name
    get_dispatcher: Callable[..., ApiResponse] = getattr(request.app.state, "get_dispatcher", dispatch_get)
    post_dispatcher: Callable[..., ApiResponse] = getattr(request.app.state, "post_dispatcher", dispatch_post)
    workspace_root_provider: Callable[[], str] = getattr(
        request.app.state,
        "workspace_root_provider",
        default_workspace_root,
    )
    response: ApiResponse
    if not host_header_allowed(request.headers.get("host", "")):
        # DNS-rebinding guard: only loopback/localhost Host headers (or VOOL_ALLOWED_HOSTS) may
        # reach the always-on local API, so a rebound external origin cannot read local data.
        response = json_response(403, {"error": "host not allowed"})
    elif request.method == "GET":
        if is_raw_dictation_path(request.url.path):
            # The dictation availability probe rides the same door: GET answers whether THIS
            # machine can transcribe, with the typed reason when it cannot.
            response = await _run_dispatch(
                dispatch_dictation,
                liveness=False,
                method="GET",
                raw_body=b"",
                query=request.url.query,
                headers=dict(request.headers.items()),
                runtime=runtime,
                client_host=(request.client.host if request.client else ""),
            )
        else:
            response = await _run_dispatch(
                get_dispatcher,
                liveness=_is_liveness_path(request.url.path),
                path=request.url.path,
                query=parse_qs(request.url.query),
                headers=dict(request.headers.items()),
                runtime=runtime,
                model_name=model_name,
                client_host=(request.client.host if request.client else ""),
            )
    elif request.method in {"POST", "DELETE"}:
        # DELETE rides the POST dispatcher: the service layer owns method-shaped semantics
        # (the operator's learning forget gesture) and the owner-local law; transport only.
        # CSRF guard for EVERY state-changing route (not just the settings POSTs that carried
        # their own check): owner-local trust is derived from the loopback TCP peer, and a
        # malicious page in the user's browser also connects from 127.0.0.1, so a cross-origin
        # Origin header must be rejected up front. A same-origin request (the app's own webview)
        # sends its own loopback Origin (allowed); a non-browser client (curl) sends none.
        _origin = str(request.headers.get("origin") or "").strip()
        if _origin and not host_header_allowed(_origin.split("://", 1)[-1]):
            response = json_response(403, {"error": "cross-origin request not allowed"})
        elif is_raw_dictation_path(request.url.path):
            # The dictation raw door: same chunked read against its own ceiling, and the same
            # shape for GET (an availability probe) and POST (bytes in, draft text out).
            ceiling = dictation_body_ceiling()
            chunks: list[bytes] = []
            received = 0
            oversized = False
            async for chunk in request.stream():
                received += len(chunk)
                if received > ceiling:
                    oversized = True
                    break
                chunks.append(chunk)
            if oversized:
                response = json_response(413, {"ok": False, "error": "recording_too_large", "message": "The recording is over the dictation size limit."})
            else:
                response = await _run_dispatch(
                    dispatch_dictation,
                    liveness=False,
                    method=request.method,
                    raw_body=b"".join(chunks),
                    query=request.url.query,
                    headers=dict(request.headers.items()),
                    runtime=runtime,
                    client_host=(request.client.host if request.client else ""),
                )
        elif is_raw_upload_path(request.url.path):
            # The ONE raw-body door: a chat attachment arrives as bytes, never as JSON. Read in
            # chunks against the attachment authority's own ceiling and stop the moment it is
            # exceeded, so an oversized upload is refused without being held whole in memory.
            ceiling = raw_upload_body_ceiling(request.url.path)
            chunks: list[bytes] = []
            received = 0
            oversized = False
            async for chunk in request.stream():
                received += len(chunk)
                if received > ceiling:
                    oversized = True
                    break
                chunks.append(chunk)
            if oversized:
                response = json_response(413, {"ok": False, "error": "too_large", "message": "The upload is over the attachment size limit."})
            else:
                response = await _run_dispatch(
                    dispatch_upload,
                    liveness=False,
                    path=request.url.path,
                    raw_body=b"".join(chunks),
                    headers=dict(request.headers.items()),
                    runtime=runtime,
                    client_host=(request.client.host if request.client else ""),
                )
        else:
            raw_body = await request.body()
            if len(raw_body) > _MAX_REQUEST_BODY_BYTES:
                response = json_response(413, {"error": "request body too large"})
            elif not raw_body:
                response = json_response(400, {"error": "empty body"})
            else:
                try:
                    body = json.loads(raw_body)
                except json.JSONDecodeError:
                    response = json_response(400, {"error": "invalid JSON"})
                else:
                    response = await _run_dispatch(
                        post_dispatcher,
                        liveness=False,
                        path=request.url.path,
                        body=body,
                        headers=dict(request.headers.items()),
                        runtime=runtime,
                        model_name=model_name,
                        workspace_root_provider=workspace_root_provider,
                        # Thread the server-resolved HTTP request ID into the chat runtime.  The
                        # request ID is already returned to the client; carrying it here links the
                        # context and provider manifests without trusting a caller-supplied body
                        # field.
                        request_id=request_id,
                        # Real TCP peer address, for the server-side owner-local trust signal.
                        # Only the loopback peer (the owner's own session) may drive privileged,
                        # spend- or shutdown-touching actions — never a caller-supplied body field.
                        client_host=(request.client.host if request.client else ""),
                    )
    elif request.method == "OPTIONS":
        if request.url.path.rstrip("/") == "/gate/unlock":
            from core.web0_gated_html import gate_cors_headers

            response = ApiResponse(
                204,
                content_type="text/plain; charset=utf-8",
                body=b"",
                headers=gate_cors_headers(),
            )
        else:
            response = json_response(404, {"error": "not found"})
    else:
        response = json_response(404, {"error": "not found"})
    response.headers = response_headers_with_request_id(response.headers, request_id=request_id)
    latency_ms = (time.perf_counter() - started) * 1000.0
    log_http_request(
        logger,
        component="api",
        method=request.method,
        path=request.url.path,
        status_code=response.status,
        latency_ms=latency_ms,
        request_id=request_id,
    )
    return _starlette_response(response)


def create_api_app(
    *,
    runtime: RuntimeServices,
    model_name: str,
    get_dispatcher: Callable[..., ApiResponse] = dispatch_get,
    post_dispatcher: Callable[..., ApiResponse] = dispatch_post,
    workspace_root_provider: Callable[[], str] = default_workspace_root,
) -> Starlette:
    app = Starlette(
        debug=False,
        routes=[
            Route("/", _dispatch, methods=["GET", "POST", "OPTIONS"]),
            Route("/{path:path}", _dispatch, methods=["GET", "POST", "DELETE", "OPTIONS"]),
        ],
    )
    app.state.runtime = runtime
    app.state.model_name = model_name
    app.state.get_dispatcher = get_dispatcher
    app.state.post_dispatcher = post_dispatcher
    app.state.workspace_root_provider = workspace_root_provider
    return app
