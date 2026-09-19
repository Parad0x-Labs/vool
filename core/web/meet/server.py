from __future__ import annotations

import json
import ssl
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from core import audit_logger, policy_engine
from core.api_write_auth import unwrap_signed_write_with_meta
from core.brain_hive_service import BrainHiveService
from core.hive_write_grants import consume_hive_write_grant
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from core.public_hive_quotas import reserve_public_hive_write_quota

from .readiness import build_meet_readiness
from .routes import (
    _enforce_voolbook_request_identity,
    _error_envelope,
    _format_public_hive_quota_error,
    _is_forbidden_write_error,
    _is_protected_api_path,
    _is_voolbook_mutation_path,
    _metrics_access_allowed,
    _requires_auth_for_request,
    _requires_public_hive_quota,
    _requires_scoped_hive_grant,
    _resolve_write_rate_limit,
    _server_peer_id,
    _verify_voolbook_token_safe,
    _voolbook_post_hook,
    dispatch_request,
    resolve_static_route,
)
from .write_limits import reserve_meet_write_rate_limit


@dataclass
class MeetAndGreetServerConfig:
    host: str = "127.0.0.1"
    port: int = 8766
    auth_token: str | None = None
    max_request_bytes: int = 262_144
    write_requests_per_minute: int = 120
    write_rate_limit_max_clients: int = 4096
    require_signed_writes: bool = True
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    tls_ca_file: str | None = None
    tls_require_client_cert: bool = False
    cors_allowed_origin: str | None = "*"
    cors_allowed_methods: str = "GET,POST,OPTIONS"
    cors_allowed_headers: str = "Content-Type,X-Vool-Meet-Token,X-VoolBook-Token"


class MeetMetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests_total = 0
        self._errors_total = 0
        self._latency_sum_ms = 0.0
        self._latency_count = 0
        self._by_status: dict[int, int] = defaultdict(int)
        self._by_route: dict[str, int] = defaultdict(int)

    def record(self, *, method: str, path: str, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self._requests_total += 1
            if int(status_code) >= 400:
                self._errors_total += 1
            self._latency_sum_ms += max(0.0, float(latency_ms))
            self._latency_count += 1
            self._by_status[int(status_code)] += 1
            self._by_route[f"{method.upper()} {path}"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg_latency = self._latency_sum_ms / max(1, self._latency_count)
            return {
                "requests_total": int(self._requests_total),
                "errors_total": int(self._errors_total),
                "latency_avg_ms": round(avg_latency, 3),
                "status_counts": {str(k): int(v) for k, v in sorted(self._by_status.items())},
                "route_counts": {k: int(v) for k, v in sorted(self._by_route.items())},
            }

    def render_prometheus(self) -> str:
        snap = self.snapshot()
        lines = [
            "# HELP vool_meet_requests_total Total HTTP requests handled by meet server.",
            "# TYPE vool_meet_requests_total counter",
            f"vool_meet_requests_total {snap['requests_total']}",
            "# HELP vool_meet_errors_total Total HTTP requests returning 4xx/5xx.",
            "# TYPE vool_meet_errors_total counter",
            f"vool_meet_errors_total {snap['errors_total']}",
            "# HELP vool_meet_latency_avg_ms Average request latency in milliseconds.",
            "# TYPE vool_meet_latency_avg_ms gauge",
            f"vool_meet_latency_avg_ms {snap['latency_avg_ms']}",
        ]
        status_counts = dict(snap.get("status_counts") or {})
        for status, count in sorted(status_counts.items()):
            lines.append(f'vool_meet_status_total{{status="{status}"}} {int(count)}')
        route_counts = dict(snap.get("route_counts") or {})
        for route, count in sorted(route_counts.items()):
            method, path = route.split(" ", 1) if " " in route else (route, "")
            safe_path = path.replace('"', '\\"')
            lines.append(f'vool_meet_route_total{{method="{method}",path="{safe_path}"}} {int(count)}')
        return "\n".join(lines) + "\n"


def build_server(
    config: MeetAndGreetServerConfig | None = None,
    *,
    service: MeetAndGreetService | None = None,
) -> ThreadingHTTPServer:
    cfg = config or MeetAndGreetServerConfig()
    svc = service or MeetAndGreetService(MeetAndGreetConfig())
    _validate_tls_config(cfg)
    if _requires_auth_for_request(cfg.host) and not str(cfg.auth_token or "").strip():
        raise ValueError("Public or non-loopback meet bindings require an auth_token.")
    write_windows: dict[str, deque[float]] = defaultdict(deque)
    write_lock = threading.Lock()
    hive_service = BrainHiveService()
    metrics = MeetMetricsCollector()
    policy_get = policy_engine.get
    try:
        from storage.voolbook_store import ensure_upvote_columns

        ensure_upvote_columns()
    except Exception:
        pass

    class Handler(BaseHTTPRequestHandler):
        server_version = "VoolMeetAndGreet/0.1"

        def do_OPTIONS(self) -> None:
            self._write_bytes_response(204, "text/plain", b"")

        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/metrics":
                if not _metrics_access_allowed(cfg.host):
                    self._write_bytes_response(403, "text/plain; charset=utf-8", b"", write_body=False)
                    return
                body = metrics.render_prometheus().encode("utf-8")
                self._write_bytes_response(
                    200,
                    "text/plain; version=0.0.4",
                    b"",
                    write_body=False,
                    content_length=len(body),
                )
                return
            static_response = resolve_static_route(parsed.path)
            if static_response is not None:
                status_code, content_type, body = static_response
                self._write_bytes_response(
                    status_code,
                    content_type,
                    b"",
                    write_body=False,
                    content_length=len(body),
                )
                return
            if parsed.path == "/v1/health":
                body = json.dumps({"ok": True, "result": {"status": "ok"}}, sort_keys=True).encode("utf-8")
                self._write_bytes_response(
                    200,
                    "application/json",
                    b"",
                    write_body=False,
                    content_length=len(body),
                )
                return
            if parsed.path in {"/v1/readyz", "/readyz"}:
                readiness = build_meet_readiness(svc)
                body = json.dumps(
                    {"ok": readiness.status == "ready", "result": readiness.model_dump(mode="json")},
                    sort_keys=True,
                ).encode("utf-8")
                self._write_bytes_response(
                    200 if readiness.status == "ready" else 503,
                    "application/json",
                    b"",
                    write_body=False,
                    content_length=len(body),
                )
                return
            self._write_bytes_response(404, "text/plain; charset=utf-8", b"", write_body=False)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if _requires_auth_for_request(cfg.host) and _is_protected_api_path(parsed.path):
                header_token = str(self.headers.get("X-Vool-Meet-Token") or "").strip()
                if header_token != str(cfg.auth_token or ""):
                    self._write_response(401, _error_envelope("Unauthorized request."))
                    return
            if parsed.path == "/metrics":
                if not _metrics_access_allowed(cfg.host):
                    self._write_response(403, _error_envelope("Metrics are not exposed on public binds."))
                    return
                body = metrics.render_prometheus().encode("utf-8")
                self._write_bytes_response(200, "text/plain; version=0.0.4", body)
                return
            static_response = resolve_static_route(parsed.path)
            if static_response is not None:
                status_code, content_type, body = static_response
                self._write_bytes_response(status_code, content_type, body)
                return
            query = parse_qs(parsed.query)
            started = time.perf_counter()
            status_code, envelope = dispatch_request(
                "GET",
                parsed.path,
                query,
                None,
                svc,
                hive_service,
                metrics,
                policy_get=policy_get,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            metrics.record(method="GET", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
            self._write_response(status_code, envelope)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            started = time.perf_counter()
            is_public_write = not _is_protected_api_path(parsed.path.rstrip("/") or "/")
            if not is_public_write and _requires_auth_for_request(cfg.host):
                header_token = str(self.headers.get("X-Vool-Meet-Token") or "").strip()
                if header_token != str(cfg.auth_token or ""):
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    metrics.record(method="POST", path=parsed.path, status_code=401, latency_ms=latency_ms)
                    self._write_response(401, _error_envelope("Unauthorized write request."))
                    return
            try:
                payload = self._read_json_body()
                request_meta: dict[str, Any] = {}
                if cfg.require_signed_writes and not is_public_write:
                    payload, request_meta = unwrap_signed_write_with_meta(target_path=parsed.path, raw_payload=payload)
            except Exception as exc:
                audit_logger.log(
                    "meet_write_rejected",
                    target_id=parsed.path,
                    target_type="meet_server",
                    details={"error": str(exc)},
                )
                status_code = 403 if _is_forbidden_write_error(exc) else 400
                latency_ms = (time.perf_counter() - started) * 1000.0
                metrics.record(method="POST", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
                error_message = str(exc).strip() or "Invalid request envelope."
                self._write_response(
                    status_code,
                    _error_envelope(error_message if status_code == 403 else "Invalid request envelope."),
                )
                return
            limit_key, limit_per_minute = _resolve_write_rate_limit(
                cfg.host,
                parsed.path,
                client_host=self.client_address[0],
                request_meta=request_meta,
                default_limit=cfg.write_requests_per_minute,
                policy_get=policy_get,
            )
            limit_reservation = reserve_meet_write_rate_limit(
                limit_key,
                limit_per_minute,
                metadata={"host": cfg.host, "path": parsed.path},
            )
            if not limit_reservation.allowed:
                latency_ms = (time.perf_counter() - started) * 1000.0
                status_code = 429 if limit_reservation.reason == "rate_limit_exceeded" else 503
                metrics.record(method="POST", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
                error_message = "Write rate limit exceeded." if status_code == 429 else "Meet write limiter is not ready."
                self._write_response(status_code, _error_envelope(error_message))
                return
            try:
                if _requires_public_hive_quota(cfg.host, parsed.path):
                    quota = reserve_public_hive_write_quota(
                        str(request_meta.get("signer_peer_id") or ""),
                        parsed.path,
                        request_nonce=str(request_meta.get("nonce") or ""),
                        metadata={"host": cfg.host},
                    )
                    if not quota.allowed:
                        audit_logger.log(
                            "meet_public_hive_quota_blocked",
                            target_id=parsed.path,
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
                        latency_ms = (time.perf_counter() - started) * 1000.0
                        metrics.record(method="POST", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
                        self._write_response(status_code, _error_envelope(_format_public_hive_quota_error(quota)))
                        return
                if _requires_scoped_hive_grant(cfg.host, parsed.path, policy_get=policy_get):
                    raw_grant = dict(request_meta.get("write_grant") or {})
                    if not raw_grant:
                        raise ValueError("Scoped Hive write grant is required for this route.")
                    grant = consume_hive_write_grant(
                        raw_grant=raw_grant,
                        target_path=parsed.path,
                        signer_peer_id=str(request_meta.get("signer_peer_id") or ""),
                        payload=payload,
                        allowed_issuer_peer_ids={_server_peer_id()},
                    )
                    if grant.review_required_by_default and parsed.path in {"/v1/hive/topics", "/v1/hive/posts"}:
                        payload["force_review_required"] = True
                nb_token = str(self.headers.get("X-VoolBook-Token") or "").strip()
                nb_peer_id: str | None = None
                if nb_token:
                    nb_peer_id = _verify_voolbook_token_safe(nb_token)
                    if _is_voolbook_mutation_path(parsed.path) and not nb_peer_id:
                        latency_ms = (time.perf_counter() - started) * 1000.0
                        metrics.record(method="POST", path=parsed.path, status_code=401, latency_ms=latency_ms)
                        self._write_response(401, _error_envelope("Invalid VoolBook token."))
                        return
                if _is_voolbook_mutation_path(parsed.path):
                    for field in ("origin_kind", "origin_channel", "origin_peer_id", "provenance"):
                        payload.pop(field, None)
                    _enforce_voolbook_request_identity(
                        parsed.path,
                        payload,
                        signer_peer_id=str(request_meta.get("signer_peer_id") or "").strip(),
                        token_peer_id=nb_peer_id,
                    )
                    if nb_peer_id and parsed.path != "/v1/voolbook/register":
                        payload["voolbook_peer_id"] = nb_peer_id
                    if parsed.path != "/v1/voolbook/register":
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
                    target_id=parsed.path,
                    target_type="meet_server",
                    details={"error": str(exc)},
                )
                status_code = 403 if _is_forbidden_write_error(exc) else 400
                latency_ms = (time.perf_counter() - started) * 1000.0
                metrics.record(method="POST", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
                error_message = str(exc).strip() or "Invalid request envelope."
                self._write_response(
                    status_code,
                    _error_envelope(error_message if status_code == 403 else "Invalid request envelope."),
                )
                return
            query = parse_qs(parsed.query)
            status_code, envelope = dispatch_request(
                "POST",
                parsed.path,
                query,
                payload,
                svc,
                hive_service,
                metrics,
                request_meta=request_meta,
                policy_get=policy_get,
            )
            if status_code < 300 and nb_peer_id and parsed.path in {"/v1/hive/posts"}:
                _voolbook_post_hook(nb_peer_id)
            latency_ms = (time.perf_counter() - started) * 1000.0
            metrics.record(method="POST", path=parsed.path, status_code=status_code, latency_ms=latency_ms)
            self._write_response(status_code, envelope)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > cfg.max_request_bytes:
                raise ValueError(f"Request body exceeds max_request_bytes={cfg.max_request_bytes}")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _write_response(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self._write_bytes_response(status_code, "application/json", body)

        def _write_bytes_response(
            self,
            status_code: int,
            content_type: str,
            body: bytes,
            *,
            write_body: bool = True,
            content_length: int | None = None,
        ) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body) if content_length is None else int(content_length)))
            origin = str(cfg.cors_allowed_origin or "").strip()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", str(cfg.cors_allowed_methods))
                self.send_header("Access-Control-Allow-Headers", str(cfg.cors_allowed_headers))
                self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()
            if write_body:
                self.wfile.write(body)

    server = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    server.write_windows = write_windows  # type: ignore[attr-defined]
    server.write_lock = write_lock  # type: ignore[attr-defined]
    server.policy_get = policy_get  # type: ignore[attr-defined]
    _wrap_server_tls(server, cfg)
    return server


def serve(config: MeetAndGreetServerConfig | None = None) -> None:
    server = build_server(config)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _wrap_server_tls(server: ThreadingHTTPServer, cfg: MeetAndGreetServerConfig) -> None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if not certfile and not keyfile:
        return
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    cafile = str(cfg.tls_ca_file or "").strip()
    if cafile:
        context.load_verify_locations(cafile=cafile)
        context.verify_mode = ssl.CERT_REQUIRED if cfg.tls_require_client_cert else ssl.CERT_OPTIONAL
    server.socket = context.wrap_socket(server.socket, server_side=True)


def _validate_tls_config(cfg: MeetAndGreetServerConfig) -> None:
    certfile = str(cfg.tls_certfile or "").strip()
    keyfile = str(cfg.tls_keyfile or "").strip()
    if (certfile and not keyfile) or (keyfile and not certfile):
        raise ValueError("Both tls_certfile and tls_keyfile are required when TLS is enabled.")
