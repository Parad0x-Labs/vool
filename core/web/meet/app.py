from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from core import audit_logger, policy_engine
from core.api_write_auth import unwrap_signed_write_with_meta
from core.brain_hive_service import BrainHiveService
from core.hive_write_grants import consume_hive_write_grant
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from core.public_hive_quotas import reserve_public_hive_write_quota

from ..request_ids import log_http_request, resolve_request_id, response_headers_with_request_id
from .readiness import build_meet_readiness
from .routes import (
    _enforce_voolbook_request_identity,
    _error_envelope,
    _format_public_hive_quota_error,
    _is_forbidden_write_error,
    _is_voolbook_mutation_path,
    _is_protected_api_path,
    _metrics_access_allowed,
    _voolbook_post_hook,
    _requires_auth_for_request,
    _requires_public_hive_quota,
    _requires_scoped_hive_grant,
    _resolve_write_rate_limit,
    _server_peer_id,
    _verify_voolbook_token_safe,
    dispatch_request,
    resolve_static_route,
)
from .server import MeetAndGreetServerConfig, MeetMetricsCollector
from .write_limits import reserve_meet_write_rate_limit

logger = logging.getLogger("vool.meet.http")


def _cors_headers(cfg: MeetAndGreetServerConfig) -> dict[str, str]:
    origin = str(cfg.cors_allowed_origin or "").strip()
    if not origin:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": str(cfg.cors_allowed_methods),
        "Access-Control-Allow-Headers": str(cfg.cors_allowed_headers),
        "Access-Control-Max-Age": "600",
    }


def _json_response(cfg: MeetAndGreetServerConfig, status_code: int, payload: dict[str, Any]) -> Response:
    return Response(
        json.dumps(payload, sort_keys=True).encode("utf-8"),
        status_code=status_code,
        media_type="application/json",
        headers=_cors_headers(cfg),
    )


def _bytes_response(
    cfg: MeetAndGreetServerConfig,
    status_code: int,
    content_type: str,
    body: bytes,
    *,
    write_body: bool = True,
    content_length: int | None = None,
) -> Response:
    headers = _cors_headers(cfg)
    headers["Content-Length"] = str(len(body) if content_length is None else int(content_length))
    payload = body if write_body else b""
    return Response(payload, status_code=status_code, media_type=content_type, headers=headers)


async def _dispatch(request: Request) -> Response:
    request_id = resolve_request_id(dict(request.headers.items()))
    cfg: MeetAndGreetServerConfig = request.app.state.config
    svc: MeetAndGreetService = request.app.state.service
    hive_service: BrainHiveService = request.app.state.hive_service
    metrics: MeetMetricsCollector = request.app.state.metrics
    policy_get = request.app.state.policy_get

    parsed_path = request.url.path
    query = parse_qs(request.url.query)
    started = time.perf_counter()

    def _finish(response: Response) -> Response:
        response.headers.update(response_headers_with_request_id(response.headers, request_id=request_id))
        latency_ms = (time.perf_counter() - started) * 1000.0
        log_http_request(
            logger,
            component="meet",
            method=request.method,
            path=parsed_path,
            status_code=response.status_code,
            latency_ms=latency_ms,
            request_id=request_id,
        )
        return response

    if request.method == "OPTIONS":
        return _finish(_bytes_response(cfg, 204, "text/plain", b""))

    if request.method == "HEAD":
        if parsed_path == "/metrics":
            if not _metrics_access_allowed(cfg.host):
                return _finish(_bytes_response(cfg, 403, "text/plain; charset=utf-8", b"", write_body=False))
            body = metrics.render_prometheus().encode("utf-8")
            return _finish(_bytes_response(
                cfg,
                200,
                "text/plain; version=0.0.4",
                b"",
                write_body=False,
                content_length=len(body),
            ))
        static_response = resolve_static_route(parsed_path)
        if static_response is not None:
            status_code, content_type, body = static_response
            return _finish(_bytes_response(cfg, status_code, content_type, b"", write_body=False, content_length=len(body)))
        if parsed_path == "/v1/health":
            body = json.dumps({"ok": True, "result": {"status": "ok"}}, sort_keys=True).encode("utf-8")
            return _finish(_bytes_response(cfg, 200, "application/json", b"", write_body=False, content_length=len(body)))
        if parsed_path in {"/v1/readyz", "/readyz"}:
            readiness = build_meet_readiness(svc)
            body = json.dumps({"ok": readiness.status == "ready", "result": readiness.model_dump(mode="json")}, sort_keys=True).encode("utf-8")
            status_code = 200 if readiness.status == "ready" else 503
            return _finish(_bytes_response(cfg, status_code, "application/json", b"", write_body=False, content_length=len(body)))
        return _finish(_bytes_response(cfg, 404, "text/plain; charset=utf-8", b"", write_body=False))

    if request.method == "GET":
        if _requires_auth_for_request(cfg.host) and _is_protected_api_path(parsed_path):
            header_token = str(request.headers.get("X-Vool-Meet-Token") or "").strip()
            if header_token != str(cfg.auth_token or ""):
                return _finish(_json_response(cfg, 401, _error_envelope("Unauthorized request.")))
        if parsed_path == "/metrics":
            if not _metrics_access_allowed(cfg.host):
                return _finish(_json_response(cfg, 403, _error_envelope("Metrics are not exposed on public binds.")))
            body = metrics.render_prometheus().encode("utf-8")
            return _finish(_bytes_response(cfg, 200, "text/plain; version=0.0.4", body))
        static_response = resolve_static_route(parsed_path)
        if static_response is not None:
            status_code, content_type, body = static_response
            return _finish(_bytes_response(cfg, status_code, content_type, body))
        dispatch_started = time.perf_counter()
        status_code, envelope = dispatch_request("GET", parsed_path, query, None, svc, hive_service, metrics, policy_get=policy_get)
        metrics.record(method="GET", path=parsed_path, status_code=status_code, latency_ms=(time.perf_counter() - dispatch_started) * 1000.0)
        return _finish(_json_response(cfg, status_code, envelope))

    if request.method != "POST":
        return _finish(_json_response(cfg, 404, _error_envelope("Unsupported request method.")))

    write_started = time.perf_counter()
    is_public_write = not _is_protected_api_path(parsed_path.rstrip("/") or "/")
    if not is_public_write and _requires_auth_for_request(cfg.host):
        header_token = str(request.headers.get("X-Vool-Meet-Token") or "").strip()
        if header_token != str(cfg.auth_token or ""):
            latency_ms = (time.perf_counter() - write_started) * 1000.0
            metrics.record(method="POST", path=parsed_path, status_code=401, latency_ms=latency_ms)
            return _finish(_json_response(cfg, 401, _error_envelope("Unauthorized write request.")))

    request_meta: dict[str, Any] = {}
    nb_peer_id: str | None = None
    try:
        raw = await request.body()
        if len(raw) > cfg.max_request_bytes:
            raise ValueError(f"Request body exceeds max_request_bytes={cfg.max_request_bytes}")
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        if cfg.require_signed_writes and not is_public_write:
            payload, request_meta = unwrap_signed_write_with_meta(target_path=parsed_path, raw_payload=payload)
    except Exception as exc:
        audit_logger.log(
            "meet_write_rejected",
            target_id=parsed_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        status_code = 403 if _is_forbidden_write_error(exc) else 400
        latency_ms = (time.perf_counter() - write_started) * 1000.0
        metrics.record(method="POST", path=parsed_path, status_code=status_code, latency_ms=latency_ms)
        error_message = str(exc).strip() or "Invalid request envelope."
        return _finish(_json_response(
            cfg,
            status_code,
            _error_envelope(error_message if status_code == 403 else "Invalid request envelope."),
        ))

    limit_key, limit_per_minute = _resolve_write_rate_limit(
        cfg.host,
        parsed_path,
        client_host=(request.client.host if request.client else ""),
        request_meta=request_meta,
        default_limit=cfg.write_requests_per_minute,
        policy_get=policy_get,
    )
    limit_reservation = reserve_meet_write_rate_limit(
        limit_key,
        limit_per_minute,
        metadata={"host": cfg.host, "path": parsed_path},
    )
    if not limit_reservation.allowed:
        latency_ms = (time.perf_counter() - write_started) * 1000.0
        status_code = 429 if limit_reservation.reason == "rate_limit_exceeded" else 503
        metrics.record(method="POST", path=parsed_path, status_code=status_code, latency_ms=latency_ms)
        error_message = "Write rate limit exceeded." if status_code == 429 else "Meet write limiter is not ready."
        return _finish(_json_response(cfg, status_code, _error_envelope(error_message)))

    try:
        if _requires_public_hive_quota(cfg.host, parsed_path):
            quota = reserve_public_hive_write_quota(
                str(request_meta.get("signer_peer_id") or ""),
                parsed_path,
                request_nonce=str(request_meta.get("nonce") or ""),
                metadata={"host": cfg.host},
            )
            if not quota.allowed:
                audit_logger.log(
                    "meet_public_hive_quota_blocked",
                    target_id=parsed_path,
                    target_type="meet_server",
                    details={
                        "peer_id": str(request_meta.get("signer_peer_id") or ""),
                        "reason": quota.reason,
                        "trust_score": quota.trust_score,
                        "trust_tier": quota.trust_tier,
                        "used_points": quota.used_points,
                        "limit_points": quota.limit_points,
                    },
                )
                status_code = 403 if quota.reason in {"insufficient_claim_trust", "insufficient_route_trust"} else 429
                latency_ms = (time.perf_counter() - write_started) * 1000.0
                metrics.record(method="POST", path=parsed_path, status_code=status_code, latency_ms=latency_ms)
                return _finish(_json_response(cfg, status_code, _error_envelope(_format_public_hive_quota_error(quota))))
        if _requires_scoped_hive_grant(cfg.host, parsed_path, policy_get=policy_get):
            raw_grant = dict(request_meta.get("write_grant") or {})
            if not raw_grant:
                raise ValueError("Scoped Hive write grant is required for this route.")
            grant = consume_hive_write_grant(
                raw_grant=raw_grant,
                target_path=parsed_path,
                signer_peer_id=str(request_meta.get("signer_peer_id") or ""),
                payload=payload,
                allowed_issuer_peer_ids={_server_peer_id()},
            )
            if grant.review_required_by_default and parsed_path in {"/v1/hive/topics", "/v1/hive/posts"}:
                payload["force_review_required"] = True
        nb_token = str(request.headers.get("X-VoolBook-Token") or "").strip()
        if nb_token:
            nb_peer_id = _verify_voolbook_token_safe(nb_token)
            if _is_voolbook_mutation_path(parsed_path) and not nb_peer_id:
                latency_ms = (time.perf_counter() - write_started) * 1000.0
                metrics.record(method="POST", path=parsed_path, status_code=401, latency_ms=latency_ms)
                return _finish(_json_response(cfg, 401, _error_envelope("Invalid VoolBook token.")))
        if _is_voolbook_mutation_path(parsed_path):
            for field in ("origin_kind", "origin_channel", "origin_peer_id", "provenance"):
                payload.pop(field, None)
            _enforce_voolbook_request_identity(
                parsed_path,
                payload,
                signer_peer_id=str(request_meta.get("signer_peer_id") or "").strip(),
                token_peer_id=nb_peer_id,
            )
            if nb_peer_id and parsed_path != "/v1/voolbook/register":
                payload["voolbook_peer_id"] = nb_peer_id
            if parsed_path != "/v1/voolbook/register":
                if nb_peer_id:
                    payload["_voolbook_origin_kind"] = "human"
                    payload["_voolbook_origin_channel"] = "voolbook_token"
                    payload["_voolbook_origin_peer_id"] = nb_peer_id
                else:
                    signer_peer_id = str(request_meta.get("signer_peer_id") or "").strip()
                    if signer_peer_id:
                        payload["_voolbook_origin_kind"] = "ai"
                        payload["_voolbook_origin_channel"] = "signed_write"
                        payload["_voolbook_origin_peer_id"] = signer_peer_id
    except Exception as exc:
        audit_logger.log(
            "meet_write_rejected",
            target_id=parsed_path,
            target_type="meet_server",
            details={"error": str(exc)},
        )
        status_code = 403 if _is_forbidden_write_error(exc) else 400
        latency_ms = (time.perf_counter() - write_started) * 1000.0
        metrics.record(method="POST", path=parsed_path, status_code=status_code, latency_ms=latency_ms)
        error_message = str(exc).strip() or "Invalid request envelope."
        return _finish(_json_response(
            cfg,
            status_code,
            _error_envelope(error_message if status_code == 403 else "Invalid request envelope."),
        ))

    status_code, envelope = dispatch_request(
        "POST",
        parsed_path,
        query,
        payload,
        svc,
        hive_service,
        metrics,
        request_meta=request_meta,
    )
    if status_code < 300 and nb_peer_id and parsed_path in {"/v1/hive/posts"}:
        _voolbook_post_hook(nb_peer_id)
    latency_ms = (time.perf_counter() - write_started) * 1000.0
    metrics.record(method="POST", path=parsed_path, status_code=status_code, latency_ms=latency_ms)
    return _finish(_json_response(cfg, status_code, envelope))


def create_meet_app(
    *,
    config: MeetAndGreetServerConfig | None = None,
    service: MeetAndGreetService | None = None,
    hive_service: BrainHiveService | None = None,
    metrics: MeetMetricsCollector | None = None,
    policy_get=None,
) -> Starlette:
    cfg = config or MeetAndGreetServerConfig()
    app = Starlette(
        debug=False,
        routes=[
            Route("/", _dispatch, methods=["GET", "POST", "HEAD", "OPTIONS"]),
            Route("/{path:path}", _dispatch, methods=["GET", "POST", "HEAD", "OPTIONS"]),
        ],
    )
    app.state.config = cfg
    app.state.service = service or MeetAndGreetService(MeetAndGreetConfig())
    app.state.hive_service = hive_service or BrainHiveService()
    app.state.metrics = metrics or MeetMetricsCollector()
    app.state.write_windows = defaultdict(deque)
    app.state.write_lock = threading.Lock()
    app.state.policy_get = policy_get or policy_engine.get
    return app
