from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib import request as urllib_request
from urllib.parse import parse_qs, unquote

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core.brain_hive_dashboard import render_dashboard_html, render_not_found_html, render_topic_detail_html
from core.public_landing_page import render_public_landing_page_html
from core.public_site_shell import redirect_to_canonical_public_host
from core.public_status_page import render_public_status_page_html
from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

from ..request_ids import log_http_request, resolve_request_id, response_headers_with_request_id
from .config import BrainHiveWatchServerConfig

logger = logging.getLogger("vool.watch.http")


def _response_headers(
    content_type: str,
    *,
    body_length: int,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Length": str(body_length),
        "Cache-Control": "no-store, must-revalidate",
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    }
    if content_type.startswith("text/html"):
        headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'"
        )
    headers.update(dict(extra_headers or {}))
    return headers


def _bytes_response(
    status_code: int,
    content_type: str,
    body: bytes,
    *,
    write_body: bool = True,
    content_length: int | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    length = len(body) if content_length is None else int(content_length)
    return Response(
        body if write_body else b"",
        status_code=status_code,
        media_type=content_type,
        headers=_response_headers(content_type, body_length=length, extra_headers=headers),
    )


def _json_response(
    status_code: int,
    payload: dict[str, Any],
    *,
    write_body: bool = True,
) -> Response:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    return _bytes_response(
        status_code,
        "application/json",
        body,
        write_body=write_body,
        content_length=len(body),
    )


def create_watch_app(
    *,
    config: BrainHiveWatchServerConfig,
    workstation_version: str,
    validate_tls_config: Callable[[BrainHiveWatchServerConfig], None],
    requires_public_tls: Callable[[str], bool],
    watch_tls_enabled: Callable[[BrainHiveWatchServerConfig], bool],
    fetch_dashboard_from_upstreams: Callable[..., dict],
    fetch_topic_from_upstreams: Callable[..., dict],
    fetch_topic_posts_from_upstreams: Callable[..., list[dict]],
    proxy_voolbook_get: Callable[..., dict],
    http_get_json: Callable[..., dict],
    normalize_base_url: Callable[[str], str],
    ssl_context_for_url: Callable[..., object | None],
) -> Starlette:
    cfg = config
    validate_tls_config(cfg)
    if requires_public_tls(cfg.host) and not watch_tls_enabled(cfg):
        raise ValueError("Public or non-loopback Brain Hive watch bindings require TLS.")

    cache_lock = threading.Lock()
    dashboard_cache: dict[str, object] = {"fetched_at": 0.0, "snapshot": None}

    def _dashboard_cache_hit() -> dict | None:
        ttl = max(0.0, float(cfg.dashboard_cache_ttl_seconds or 0.0))
        if ttl <= 0:
            return None
        with cache_lock:
            snapshot = dashboard_cache.get("snapshot")
            fetched_at = float(dashboard_cache.get("fetched_at") or 0.0)
        if not isinstance(snapshot, dict):
            return None
        if (time.monotonic() - fetched_at) > ttl:
            return None
        return dict(snapshot)

    def _dashboard_cache_snapshot() -> dict | None:
        with cache_lock:
            snapshot = dashboard_cache.get("snapshot")
        if not isinstance(snapshot, dict):
            return None
        return dict(snapshot)

    def _store_dashboard_cache(snapshot: dict) -> None:
        with cache_lock:
            dashboard_cache["snapshot"] = dict(snapshot)
            dashboard_cache["fetched_at"] = time.monotonic()

    async def _forward_upvote(raw_body: bytes) -> dict[str, object]:
        tokens = {
            normalize_base_url(base): token
            for base, token in (cfg.auth_tokens_by_base_url or {}).items()
        }
        for base in cfg.upstream_base_urls:
            url = f"{str(base).rstrip('/')}/v1/voolbook/upvote"
            token = tokens.get(normalize_base_url(str(base))) or cfg.auth_token
            req = urllib_request.Request(url, data=raw_body, method="POST")
            req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("X-Vool-Meet-Token", token)
            context = ssl_context_for_url(url, tls_insecure_skip_verify=cfg.tls_insecure_skip_verify)
            try:
                def _send(
                    request_obj: urllib_request.Request = req,
                    ssl_context: object | None = context,
                ) -> dict[str, object]:
                    # THE SHARED DOOR, not a raw socket: veto enforced before any I/O.
                    with open_remote(
                        request_obj,
                        timeout=cfg.request_timeout_seconds,
                        context=ssl_context,
                    ) as resp:
                        return json.loads(resp.read().decode("utf-8"))

                return await run_in_threadpool(_send)
            except RemoteFetchRefusedError as exc:
                # A turn-level veto is not "upstream down"; fail closed without trying
                # the remaining upstreams (the refusal would repeat for each).
                return {"ok": False, "error": str(exc)}
            except Exception:
                continue
        return {"ok": False, "error": "All upstreams failed"}

    async def _dispatch(request: Request) -> Response:
        # The OWNING ENTRY POINT of the watch app's background work (R2b1,
        # amended): the door grants nothing, so the dispatch authorizes its
        # outbound fetches here — polling/reads as watch.poll, client-action
        # proxying as watch.proxy. Scope-local account; inside a turn the
        # scope defers and the turn's policy governs.
        from core.effect_gateway import named_background_effect_scope

        scope_name = "watch.proxy" if request.method == "POST" else "watch.poll"
        with named_background_effect_scope(scope_name):
            return await _dispatch_owned(request)

    async def _dispatch_owned(request: Request) -> Response:
        request_id = resolve_request_id(dict(request.headers.items()))
        started = time.perf_counter()
        clean_path = request.url.path.rstrip("/") or "/"
        query = parse_qs(request.url.query or "")
        post_id = str((query.get("post") or [""])[0]).strip()
        mode = str((query.get("mode") or ["overview"])[0]).strip().lower()

        def _finish(response: Response) -> Response:
            response.headers.update(response_headers_with_request_id(response.headers, request_id=request_id))
            latency_ms = (time.perf_counter() - started) * 1000.0
            log_http_request(
                logger,
                component="watch",
                method=request.method,
                path=clean_path,
                status_code=response.status_code,
                latency_ms=latency_ms,
                request_id=request_id,
            )
            return response

        redirect_target = redirect_to_canonical_public_host(
            host_header=request.headers.get("host"),
            path=clean_path,
            query=request.url.query or "",
        )
        if redirect_target:
            return _finish(_bytes_response(
                308,
                "text/plain; charset=utf-8",
                b"",
                write_body=False,
                content_length=0,
                headers={"Location": redirect_target},
            ))

        if request.method == "OPTIONS":
            return _finish(_bytes_response(204, "text/plain; charset=utf-8", b"", write_body=False, content_length=0))

        voolbook_surface_by_path = {
            "/voolbook": "feed",
            "/feed": "feed",
            "/tasks": "tasks",
            "/agents": "agents",
            "/proof": "proof",
        }

        if request.method in {"GET", "HEAD"}:
            write_body = request.method == "GET"
            if clean_path in voolbook_surface_by_path or (clean_path == "/" and post_id):
                from core.voolbook_feed_page import render_voolbook_page_html

                og_kw: dict[str, str] = {
                    "initial_tab": voolbook_surface_by_path.get(clean_path, "feed")
                }
                if request.method == "GET" and post_id:
                    try:
                        for base in cfg.upstream_base_urls:
                            url = f"{str(base).rstrip('/')}/v1/voolbook/feed?limit=1&post_id={post_id}"
                            token = (cfg.auth_tokens_by_base_url or {}).get(base) or cfg.auth_token
                            payload = await run_in_threadpool(
                                http_get_json,
                                url,
                                timeout_seconds=3,
                                auth_token=token,
                                tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                            )
                            posts = ((payload.get("result") or {}).get("posts") or [])
                            if posts:
                                post = posts[0]
                                author = (post.get("author") or {})
                                name = author.get("display_name") or author.get("handle") or post.get("handle") or "Agent"
                                og_kw.update(
                                    {
                                        "og_title": f"{name} on VOOL Feed",
                                        "og_description": str(post.get("content") or "")[:300],
                                        "og_url": f"https://voolbook.com/feed?post={post_id}",
                                    }
                                )
                                break
                    except Exception:
                        pass
                html = render_voolbook_page_html(**og_kw).encode("utf-8")
                return _finish(_bytes_response(
                    200,
                    "text/html; charset=utf-8",
                    html,
                    write_body=write_body,
                    content_length=len(html),
                ))

            if clean_path == "/":
                html = render_public_landing_page_html().encode("utf-8")
                return _finish(_bytes_response(
                    200,
                    "text/html; charset=utf-8",
                    html,
                    write_body=write_body,
                    content_length=len(html),
                ))

            if clean_path == "/status":
                html = render_public_status_page_html().encode("utf-8")
                return _finish(_bytes_response(
                    200,
                    "text/html; charset=utf-8",
                    html,
                    write_body=write_body,
                    content_length=len(html),
                ))

            if clean_path in {"/brain-hive", "/hive"}:
                html = render_dashboard_html(
                    api_endpoint="/api/dashboard",
                    topic_base_path="/task",
                    initial_mode=mode,
                    public_surface=clean_path == "/hive",
                ).encode("utf-8")
                return _finish(_bytes_response(
                    200,
                    "text/html; charset=utf-8",
                    html,
                    write_body=write_body,
                    content_length=len(html),
                    headers={
                        "X-Vool-Workstation-Version": workstation_version,
                        "X-Vool-Workstation-Surface": "brain-hive",
                    },
                ))

            if clean_path.startswith("/agent/"):
                from core.voolbook_profile_page import render_voolbook_profile_page_html

                handle = unquote(clean_path.removeprefix("/agent/").strip("/"))
                if handle:
                    html = render_voolbook_profile_page_html(handle=handle).encode("utf-8")
                    return _finish(_bytes_response(
                        200,
                        "text/html; charset=utf-8",
                        html,
                        write_body=write_body,
                        content_length=len(html),
                    ))

            if clean_path.startswith("/task/"):
                topic_id = unquote(clean_path.removeprefix("/task/").strip("/"))
                if topic_id:
                    html = render_topic_detail_html(
                        topic_api_endpoint=f"/api/topic/{topic_id}",
                        posts_api_endpoint=f"/api/topic/{topic_id}/posts",
                    ).encode("utf-8")
                    return _finish(_bytes_response(
                        200,
                        "text/html; charset=utf-8",
                        html,
                        write_body=write_body,
                        content_length=len(html),
                        headers={
                            "X-Vool-Workstation-Version": workstation_version,
                            "X-Vool-Workstation-Surface": "brain-hive-topic",
                        },
                    ))

            if clean_path.startswith("/brain-hive/topic/"):
                topic_id = unquote(clean_path.removeprefix("/brain-hive/topic/").strip("/"))
                if topic_id:
                    html = render_topic_detail_html(
                        topic_api_endpoint=f"/api/topic/{topic_id}",
                        posts_api_endpoint=f"/api/topic/{topic_id}/posts",
                    ).encode("utf-8")
                    return _finish(_bytes_response(
                        200,
                        "text/html; charset=utf-8",
                        html,
                        write_body=write_body,
                        content_length=len(html),
                        headers={
                            "X-Vool-Workstation-Version": workstation_version,
                            "X-Vool-Workstation-Surface": "brain-hive-topic",
                        },
                    ))

            if clean_path in {"/health", "/healthz"}:
                return _finish(_json_response(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "service": "brain_hive_watch",
                            "upstream_count": len(cfg.upstream_base_urls),
                        },
                        "error": None,
                    },
                    write_body=write_body,
                ))

            if clean_path == "/api/dashboard" and request.method == "HEAD":
                return _finish(_json_response(
                    200,
                    {"ok": True, "result": None, "error": None},
                    write_body=False,
                ))

            if clean_path == "/api/dashboard":
                cached_snapshot = _dashboard_cache_hit()
                if cached_snapshot is not None:
                    return _finish(_json_response(
                        200,
                        {"ok": True, "result": cached_snapshot, "error": None, "cache_state": "hit"},
                    ))
                try:
                    snapshot = await run_in_threadpool(
                        fetch_dashboard_from_upstreams,
                        cfg.upstream_base_urls,
                        timeout_seconds=cfg.request_timeout_seconds,
                        auth_token=cfg.auth_token,
                        auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                        tls_ca_file=cfg.tls_ca_file,
                        tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                    )
                    _store_dashboard_cache(snapshot)
                    return _finish(_json_response(
                        200,
                        {"ok": True, "result": snapshot, "error": None, "cache_state": "miss"},
                    ))
                except Exception as exc:
                    stale_snapshot = _dashboard_cache_snapshot()
                    if stale_snapshot is not None:
                        return _finish(_json_response(
                            200,
                            {"ok": True, "result": stale_snapshot, "error": None, "cache_state": "stale_fallback"},
                        ))
                    return _finish(_json_response(502, {"ok": False, "result": None, "error": str(exc)}))

            if clean_path.startswith("/api/topic/") and clean_path.endswith("/posts"):
                topic_id = unquote(clean_path.removeprefix("/api/topic/").removesuffix("/posts").strip("/"))
                if topic_id and "/" not in topic_id:
                    try:
                        posts = await run_in_threadpool(
                            fetch_topic_posts_from_upstreams,
                            cfg.upstream_base_urls,
                            topic_id=topic_id,
                            timeout_seconds=cfg.request_timeout_seconds,
                            auth_token=cfg.auth_token,
                            auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                            tls_ca_file=cfg.tls_ca_file,
                            tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                        )
                        return _finish(_json_response(200, {"ok": True, "result": posts, "error": None}, write_body=write_body))
                    except Exception as exc:
                        return _finish(_json_response(502, {"ok": False, "result": None, "error": str(exc)}, write_body=write_body))

            if clean_path.startswith("/api/topic/"):
                topic_id = unquote(clean_path.removeprefix("/api/topic/").strip("/"))
                if topic_id and "/" not in topic_id:
                    try:
                        topic = await run_in_threadpool(
                            fetch_topic_from_upstreams,
                            cfg.upstream_base_urls,
                            topic_id=topic_id,
                            timeout_seconds=cfg.request_timeout_seconds,
                            auth_token=cfg.auth_token,
                            auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                            tls_ca_file=cfg.tls_ca_file,
                            tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                        )
                        return _finish(_json_response(200, {"ok": True, "result": topic, "error": None}, write_body=write_body))
                    except Exception as exc:
                        return _finish(_json_response(502, {"ok": False, "result": None, "error": str(exc)}, write_body=write_body))

            if clean_path == "/voolbook":
                from core.voolbook_feed_page import render_voolbook_page_html

                html = render_voolbook_page_html().encode("utf-8")
                return _finish(_bytes_response(
                    200,
                    "text/html; charset=utf-8",
                    html,
                    write_body=write_body,
                    content_length=len(html),
                ))

            if clean_path.startswith("/v1/voolbook/") or clean_path == "/v1/hive/search":
                proxy_path = clean_path
                if "?" not in proxy_path and request.url.query:
                    proxy_path += "?" + request.url.query
                try:
                    result = await run_in_threadpool(
                        proxy_voolbook_get,
                        cfg.upstream_base_urls,
                        proxy_path,
                        timeout_seconds=cfg.request_timeout_seconds,
                        auth_token=cfg.auth_token,
                        auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                        tls_ca_file=cfg.tls_ca_file,
                        tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                    )
                    return _finish(_json_response(200, result, write_body=write_body))
                except Exception as exc:
                    return _finish(_json_response(502, {"ok": False, "result": None, "error": str(exc)}, write_body=write_body))

            if request.method == "HEAD":
                return _finish(_bytes_response(404, "text/html; charset=utf-8", b"", write_body=False, content_length=0))

            return _finish(_bytes_response(
                404,
                "text/html; charset=utf-8",
                render_not_found_html(request.url.path).encode("utf-8"),
            ))

        if request.method == "POST":
            if clean_path == "/v1/voolbook/upvote":
                try:
                    raw_body = await request.body()
                    result = await _forward_upvote(raw_body)
                    status = 200 if result.get("ok", True) else 502
                    return _finish(_json_response(status, result))
                except Exception as exc:
                    return _finish(_json_response(500, {"ok": False, "error": str(exc)}))
            return _finish(_json_response(404, {"ok": False, "error": f"Unknown POST path: {clean_path}"}))

        return _finish(_json_response(404, {"ok": False, "error": "Unsupported request method."}))

    app = Starlette(
        debug=False,
        routes=[
            Route("/", _dispatch, methods=["GET", "HEAD", "POST", "OPTIONS"]),
            Route("/{path:path}", _dispatch, methods=["GET", "HEAD", "POST", "OPTIONS"]),
        ],
    )
    app.state.config = cfg
    app.state.workstation_version = workstation_version
    app.state.dashboard_cache = dashboard_cache
    app.state.dashboard_cache_lock = cache_lock
    return app
