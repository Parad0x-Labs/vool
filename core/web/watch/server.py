from __future__ import annotations

import json
import ssl
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request
from urllib.parse import parse_qs, unquote, urlparse

from core.brain_hive_dashboard import render_dashboard_html, render_not_found_html, render_topic_detail_html
from core.public_landing_page import render_public_landing_page_html
from core.public_site_shell import redirect_to_canonical_public_host
from core.public_status_page import render_public_status_page_html

from .config import BrainHiveWatchServerConfig
from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote


def build_watch_server(
    config: BrainHiveWatchServerConfig,
    *,
    workstation_version: str,
    server_factory: Callable[[tuple[str, int], type[BaseHTTPRequestHandler]], ThreadingHTTPServer] = ThreadingHTTPServer,
    validate_tls_config: Callable[[BrainHiveWatchServerConfig], None],
    requires_public_tls: Callable[[str], bool],
    watch_tls_enabled: Callable[[BrainHiveWatchServerConfig], bool],
    fetch_dashboard_from_upstreams: Callable[..., dict],
    fetch_topic_from_upstreams: Callable[..., dict],
    fetch_topic_posts_from_upstreams: Callable[..., list[dict]],
    proxy_voolbook_get: Callable[..., dict],
    http_get_json: Callable[..., dict],
    normalize_base_url: Callable[[str], str],
    ssl_context_for_url: Callable[..., ssl.SSLContext | None],
    build_tls_context: Callable[[BrainHiveWatchServerConfig], ssl.SSLContext | None],
) -> ThreadingHTTPServer:
    cfg = config
    validate_tls_config(cfg)
    if requires_public_tls(cfg.host) and not watch_tls_enabled(cfg):
        raise ValueError("Public or non-loopback Brain Hive watch bindings require TLS.")
    dashboard_cache_lock = threading.Lock()
    dashboard_cache: dict[str, object] = {"fetched_at": 0.0, "snapshot": None}

    def _dashboard_cache_hit() -> dict | None:
        ttl = max(0.0, float(cfg.dashboard_cache_ttl_seconds or 0.0))
        if ttl <= 0:
            return None
        with dashboard_cache_lock:
            snapshot = dashboard_cache.get("snapshot")
            fetched_at = float(dashboard_cache.get("fetched_at") or 0.0)
        if not isinstance(snapshot, dict):
            return None
        if (time.monotonic() - fetched_at) > ttl:
            return None
        return dict(snapshot)

    def _dashboard_cache_snapshot() -> dict | None:
        with dashboard_cache_lock:
            snapshot = dashboard_cache.get("snapshot")
        if not isinstance(snapshot, dict):
            return None
        return dict(snapshot)

    def _store_dashboard_cache(snapshot: dict) -> None:
        with dashboard_cache_lock:
            dashboard_cache["snapshot"] = dict(snapshot)
            dashboard_cache["fetched_at"] = time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        server_version = "VoolBrainHiveWatch/0.1"

        def _maybe_redirect_public_host(self, *, parsed: object, clean_path: str) -> bool:
            target = redirect_to_canonical_public_host(
                host_header=self.headers.get("Host"),
                path=clean_path,
                query=getattr(parsed, "query", "") or "",
            )
            if not target:
                return False
            self._write_bytes(
                308,
                "text/plain; charset=utf-8",
                b"",
                headers={"Location": target},
                write_body=False,
                content_length=0,
            )
            return True

        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            clean_path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query or "")
            post_id = str((qs.get("post") or [""])[0]).strip()
            mode = str((qs.get("mode") or ["overview"])[0]).strip().lower()
            if self._maybe_redirect_public_host(parsed=parsed, clean_path=clean_path):
                return
            voolbook_surface_by_path = {
                "/voolbook": "feed",
                "/feed": "feed",
                "/tasks": "tasks",
                "/agents": "agents",
                "/proof": "proof",
            }
            if clean_path in voolbook_surface_by_path or (clean_path == "/" and post_id):
                from core.voolbook_feed_page import render_voolbook_page_html

                og_kw: dict[str, str] = {
                    "initial_tab": voolbook_surface_by_path.get(clean_path, "feed")
                }
                body = render_voolbook_page_html(**og_kw).encode("utf-8")
                self._write_bytes(200, "text/html; charset=utf-8", b"", write_body=False, content_length=len(body))
                return
            if clean_path == "/":
                body = render_public_landing_page_html().encode("utf-8")
                self._write_bytes(200, "text/html; charset=utf-8", b"", write_body=False, content_length=len(body))
                return
            if clean_path == "/status":
                body = render_public_status_page_html().encode("utf-8")
                self._write_bytes(200, "text/html; charset=utf-8", b"", write_body=False, content_length=len(body))
                return
            if clean_path in {"/brain-hive", "/hive"}:
                body = render_dashboard_html(
                    api_endpoint="/api/dashboard",
                    topic_base_path="/task",
                    initial_mode=mode,
                    public_surface=clean_path == "/hive",
                ).encode("utf-8")
                self._write_bytes(
                    200,
                    "text/html; charset=utf-8",
                    b"",
                    headers={
                        "X-Vool-Workstation-Version": workstation_version,
                        "X-Vool-Workstation-Surface": "brain-hive",
                    },
                    write_body=False,
                    content_length=len(body),
                )
                return
            if clean_path.startswith("/agent/"):
                from core.voolbook_profile_page import render_voolbook_profile_page_html

                handle = unquote(clean_path.removeprefix("/agent/").strip("/"))
                if handle:
                    body = render_voolbook_profile_page_html(handle=handle).encode("utf-8")
                    self._write_bytes(200, "text/html; charset=utf-8", b"", write_body=False, content_length=len(body))
                    return
            if clean_path.startswith("/task/"):
                topic_id = unquote(clean_path.removeprefix("/task/").strip("/"))
                if topic_id:
                    body = render_topic_detail_html(
                        topic_api_endpoint=f"/api/topic/{topic_id}",
                        posts_api_endpoint=f"/api/topic/{topic_id}/posts",
                    ).encode("utf-8")
                    self._write_bytes(
                        200,
                        "text/html; charset=utf-8",
                        b"",
                        headers={
                            "X-Vool-Workstation-Version": workstation_version,
                            "X-Vool-Workstation-Surface": "brain-hive-topic",
                        },
                        write_body=False,
                        content_length=len(body),
                    )
                    return
            if clean_path.startswith("/brain-hive/topic/"):
                topic_id = unquote(clean_path.removeprefix("/brain-hive/topic/").strip("/"))
                if topic_id:
                    body = render_topic_detail_html(
                        topic_api_endpoint=f"/api/topic/{topic_id}",
                        posts_api_endpoint=f"/api/topic/{topic_id}/posts",
                    ).encode("utf-8")
                    self._write_bytes(
                        200,
                        "text/html; charset=utf-8",
                        b"",
                        headers={
                            "X-Vool-Workstation-Version": workstation_version,
                            "X-Vool-Workstation-Surface": "brain-hive-topic",
                        },
                        write_body=False,
                        content_length=len(body),
                    )
                    return
            if clean_path in {"/health", "/healthz"}:
                body = json.dumps(
                    {
                        "ok": True,
                        "result": {
                            "service": "brain_hive_watch",
                            "upstream_count": len(cfg.upstream_base_urls),
                        },
                        "error": None,
                    },
                    sort_keys=True,
                ).encode("utf-8")
                self._write_bytes(200, "application/json", b"", write_body=False, content_length=len(body))
                return
            if clean_path == "/api/dashboard":
                body = json.dumps({"ok": True, "result": None, "error": None}, sort_keys=True).encode("utf-8")
                self._write_bytes(200, "application/json", b"", write_body=False, content_length=len(body))
                return
            self._write_bytes(404, "text/html; charset=utf-8", b"", write_body=False)

        def do_GET(self) -> None:
            # The OWNING ENTRY POINT of the watch daemon's poll/read work
            # (R2b1, amended): the door grants nothing, so the handler
            # authorizes its outbound fetches here, by name. Scope-local
            # account — handed to no one at close.
            from core.effect_gateway import named_background_effect_scope

            with named_background_effect_scope("watch.poll"):
                self._do_get_owned()

        def _do_get_owned(self) -> None:
            parsed = urlparse(self.path)
            clean_path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query or "")
            post_id = str((qs.get("post") or [""])[0]).strip()
            mode = str((qs.get("mode") or ["overview"])[0]).strip().lower()
            if self._maybe_redirect_public_host(parsed=parsed, clean_path=clean_path):
                return
            voolbook_surface_by_path = {
                "/voolbook": "feed",
                "/feed": "feed",
                "/tasks": "tasks",
                "/agents": "agents",
                "/proof": "proof",
            }
            if clean_path in voolbook_surface_by_path or (clean_path == "/" and post_id):
                from core.voolbook_feed_page import render_voolbook_page_html

                og_kw: dict[str, str] = {
                    "initial_tab": voolbook_surface_by_path.get(clean_path, "feed")
                }
                if post_id:
                    try:
                        for base in cfg.upstream_base_urls:
                            url = f"{str(base).rstrip('/')}/v1/voolbook/feed?limit=1&post_id={post_id}"
                            token = (cfg.auth_tokens_by_base_url or {}).get(base) or cfg.auth_token
                            payload = http_get_json(
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
                html = render_voolbook_page_html(**og_kw)
                self._write_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            if clean_path == "/":
                html = render_public_landing_page_html()
                self._write_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            if clean_path == "/status":
                html = render_public_status_page_html()
                self._write_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            if clean_path in {"/brain-hive", "/hive"}:
                html = render_dashboard_html(
                    api_endpoint="/api/dashboard",
                    topic_base_path="/task",
                    initial_mode=mode,
                    public_surface=clean_path == "/hive",
                )
                self._write_bytes(
                    200,
                    "text/html; charset=utf-8",
                    html.encode("utf-8"),
                    headers={
                        "X-Vool-Workstation-Version": workstation_version,
                        "X-Vool-Workstation-Surface": "brain-hive",
                    },
                )
                return
            if clean_path.startswith("/agent/"):
                from core.voolbook_profile_page import render_voolbook_profile_page_html

                handle = unquote(clean_path.removeprefix("/agent/").strip("/"))
                if handle:
                    html = render_voolbook_profile_page_html(handle=handle)
                    self._write_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
                    return
            if clean_path.startswith("/task/"):
                topic_id = unquote(clean_path.removeprefix("/task/").strip("/"))
                if topic_id:
                    html = render_topic_detail_html(
                        topic_api_endpoint=f"/api/topic/{topic_id}",
                        posts_api_endpoint=f"/api/topic/{topic_id}/posts",
                    )
                    self._write_bytes(
                        200,
                        "text/html; charset=utf-8",
                        html.encode("utf-8"),
                        headers={
                            "X-Vool-Workstation-Version": workstation_version,
                            "X-Vool-Workstation-Surface": "brain-hive-topic",
                        },
                    )
                    return
            if clean_path.startswith("/brain-hive/topic/"):
                topic_id = unquote(clean_path.removeprefix("/brain-hive/topic/").strip("/"))
                if topic_id:
                    html = render_topic_detail_html(
                        topic_api_endpoint=f"/api/topic/{topic_id}",
                        posts_api_endpoint=f"/api/topic/{topic_id}/posts",
                    )
                    self._write_bytes(
                        200,
                        "text/html; charset=utf-8",
                        html.encode("utf-8"),
                        headers={
                            "X-Vool-Workstation-Version": workstation_version,
                            "X-Vool-Workstation-Surface": "brain-hive-topic",
                        },
                    )
                    return
            if clean_path == "/api/dashboard":
                cached_snapshot = _dashboard_cache_hit()
                if cached_snapshot is not None:
                    self._write_json(200, {"ok": True, "result": cached_snapshot, "error": None, "cache_state": "hit"})
                    return
                try:
                    snapshot = fetch_dashboard_from_upstreams(
                        cfg.upstream_base_urls,
                        timeout_seconds=cfg.request_timeout_seconds,
                        auth_token=cfg.auth_token,
                        auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                        tls_ca_file=cfg.tls_ca_file,
                        tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                    )
                    _store_dashboard_cache(snapshot)
                    self._write_json(200, {"ok": True, "result": snapshot, "error": None, "cache_state": "miss"})
                except Exception as exc:
                    stale_snapshot = _dashboard_cache_snapshot()
                    if stale_snapshot is not None:
                        self._write_json(200, {"ok": True, "result": stale_snapshot, "error": None, "cache_state": "stale_fallback"})
                    else:
                        self._write_json(502, {"ok": False, "result": None, "error": str(exc)})
                return
            if clean_path.startswith("/api/topic/") and clean_path.endswith("/posts"):
                topic_id = unquote(clean_path.removeprefix("/api/topic/").removesuffix("/posts").strip("/"))
                if topic_id and "/" not in topic_id:
                    try:
                        posts = fetch_topic_posts_from_upstreams(
                            cfg.upstream_base_urls,
                            topic_id=topic_id,
                            timeout_seconds=cfg.request_timeout_seconds,
                            auth_token=cfg.auth_token,
                            auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                            tls_ca_file=cfg.tls_ca_file,
                            tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                        )
                        self._write_json(200, {"ok": True, "result": posts, "error": None})
                    except Exception as exc:
                        self._write_json(502, {"ok": False, "result": None, "error": str(exc)})
                    return
            if clean_path.startswith("/api/topic/"):
                topic_id = unquote(clean_path.removeprefix("/api/topic/").strip("/"))
                if topic_id and "/" not in topic_id:
                    try:
                        topic = fetch_topic_from_upstreams(
                            cfg.upstream_base_urls,
                            topic_id=topic_id,
                            timeout_seconds=cfg.request_timeout_seconds,
                            auth_token=cfg.auth_token,
                            auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                            tls_ca_file=cfg.tls_ca_file,
                            tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                        )
                        self._write_json(200, {"ok": True, "result": topic, "error": None})
                    except Exception as exc:
                        self._write_json(502, {"ok": False, "result": None, "error": str(exc)})
                    return
            if clean_path == "/voolbook":
                from core.voolbook_feed_page import render_voolbook_page_html

                html = render_voolbook_page_html()
                self._write_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
                return
            if clean_path.startswith("/v1/voolbook/") or clean_path == "/v1/hive/search":
                proxy_path = clean_path
                if "?" not in proxy_path and self.path and "?" in self.path:
                    proxy_path += "?" + self.path.split("?", 1)[1]
                try:
                    result = proxy_voolbook_get(
                        cfg.upstream_base_urls,
                        proxy_path,
                        timeout_seconds=cfg.request_timeout_seconds,
                        auth_token=cfg.auth_token,
                        auth_tokens_by_base_url=cfg.auth_tokens_by_base_url,
                        tls_ca_file=cfg.tls_ca_file,
                        tls_insecure_skip_verify=cfg.tls_insecure_skip_verify,
                    )
                    self._write_json(200, result)
                except Exception as exc:
                    self._write_json(502, {"ok": False, "result": None, "error": str(exc)})
                return
            if clean_path in {"/health", "/healthz"}:
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "result": {
                            "service": "brain_hive_watch",
                            "upstream_count": len(cfg.upstream_base_urls),
                        },
                        "error": None,
                    },
                )
                return
            self._write_bytes(404, "text/html; charset=utf-8", render_not_found_html(parsed.path).encode("utf-8"))

        def do_POST(self) -> None:
            # The OWNING ENTRY POINT of the watch daemon's client-action proxy
            # (R2b1, amended): specifically named — this is not polling work.
            from core.effect_gateway import named_background_effect_scope

            with named_background_effect_scope("watch.proxy"):
                self._do_post_owned()

        def _do_post_owned(self) -> None:
            parsed = urlparse(self.path)
            clean_path = parsed.path.rstrip("/") or "/"
            if clean_path == "/v1/voolbook/upvote":
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                    raw_body = self.rfile.read(length) if length > 0 else b""
                    tokens = {
                        normalize_base_url(base): token
                        for base, token in (cfg.auth_tokens_by_base_url or {}).items()
                    }
                    for base in cfg.upstream_base_urls:
                        url = f"{str(base).rstrip('/')}/v1/voolbook/upvote"
                        token = tokens.get(normalize_base_url(str(base))) or cfg.auth_token
                        req = request.Request(url, data=raw_body, method="POST")
                        req.add_header("Content-Type", "application/json")
                        if token:
                            req.add_header("X-Vool-Meet-Token", token)
                        context = ssl_context_for_url(url, tls_insecure_skip_verify=cfg.tls_insecure_skip_verify)
                        try:
                            # THE SHARED DOOR, not a raw socket: veto enforced before any I/O.
                            with open_remote(req, timeout=cfg.request_timeout_seconds, context=context) as resp:
                                result = json.loads(resp.read().decode("utf-8"))
                                self._write_json(200, result)
                                return
                        except RemoteFetchRefusedError as exc:
                            self._write_json(502, {"ok": False, "error": str(exc)})
                            return
                        except Exception:
                            continue
                    self._write_json(502, {"ok": False, "error": "All upstreams failed"})
                except Exception as exc:
                    self._write_json(500, {"ok": False, "error": str(exc)})
                return
            self._write_json(404, {"ok": False, "error": f"Unknown POST path: {clean_path}"})

        def log_message(self, format: str, *args: object) -> None:
            return

        def _write_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self._write_bytes(status_code, "application/json", body)

        def _write_bytes(
            self,
            status_code: int,
            content_type: str,
            body: bytes,
            *,
            headers: dict[str, str] | None = None,
            write_body: bool = True,
            content_length: int | None = None,
        ) -> None:
            self.send_response(status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body) if content_length is None else int(content_length)))
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
            if content_type.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                )
            for name, value in dict(headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if write_body:
                self.wfile.write(body)

    server = server_factory((cfg.host, cfg.port), Handler)
    tls_context = build_tls_context(cfg)
    if tls_context is not None:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    return server
