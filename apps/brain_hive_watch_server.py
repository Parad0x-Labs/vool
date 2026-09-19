from __future__ import annotations

import argparse
import os

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import ssl
import sys as _bootstrap_sys
from collections.abc import Callable
from http.server import ThreadingHTTPServer

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)


from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)
import os as _research_os

_research_os.environ.setdefault("VOOL_RESEARCH_NETWORKING", "1")  # explicit research invocation

"""Experimental research. Not part of the standard VOOL production runtime.
Not shipped or enabled in official releases. Security assumptions, APIs and
architecture may change or be discarded. See research/README.md and
core/runtime_mode.py for the production/research boundary."""
from core.vool_workstation_ui import VOOL_WORKSTATION_DEPLOYMENT_VERSION
from core.web.watch.app import create_watch_app
from core.web.watch.config import BrainHiveWatchServerConfig
from core.web.watch.fetchers import (
    fetch_dashboard_from_upstreams as _core_fetch_dashboard_from_upstreams,
)
from core.web.watch.fetchers import (
    fetch_topic_from_upstreams as _core_fetch_topic_from_upstreams,
)
from core.web.watch.fetchers import (
    fetch_topic_posts_from_upstreams as _core_fetch_topic_posts_from_upstreams,
)
from core.web.watch.fetchers import (
    http_get_json as _core_http_get_json,
)
from core.web.watch.fetchers import (
    proxy_voolbook_get as _core_proxy_voolbook_get,
)
from core.web.watch.server import build_watch_server
from core.web.watch.tls import (
    build_tls_context as _core_build_tls_context,
)
from core.web.watch.tls import (
    normalize_base_url as _core_normalize_base_url,
)
from core.web.watch.tls import (
    requires_public_tls as _core_requires_public_tls,
)
from core.web.watch.tls import (
    ssl_context_for_url as _core_ssl_context_for_url,
)
from core.web.watch.tls import (
    validate_tls_config as _core_validate_tls_config,
)
from core.web.watch.tls import (
    watch_tls_enabled as _core_watch_tls_enabled,
)


def _http_get_json(
    url: str,
    *,
    timeout_seconds: int,
    auth_token: str | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
) -> dict:
    return _core_http_get_json(
        url,
        timeout_seconds=timeout_seconds,
        auth_token=auth_token,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        ssl_context_for_url_func=_ssl_context_for_url,
    )


def _proxy_voolbook_get(
    upstream_base_urls: tuple[str, ...],
    path: str,
    *,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
) -> dict:
    return _core_proxy_voolbook_get(
        upstream_base_urls,
        path,
        timeout_seconds=timeout_seconds,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        fetch_json=_http_get_json,
        normalize_base_url_func=_normalize_base_url,
    )


def fetch_dashboard_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
) -> dict:
    delegated_fetch = fetch_json or (
        lambda url, token: _http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    return _core_fetch_dashboard_from_upstreams(
        upstream_base_urls,
        timeout_seconds=timeout_seconds,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        fetch_json=delegated_fetch,
        normalize_base_url_func=_normalize_base_url,
    )


def fetch_topic_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    topic_id: str,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
) -> dict:
    delegated_fetch = fetch_json or (
        lambda url, token: _http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    return _core_fetch_topic_from_upstreams(
        upstream_base_urls,
        topic_id=topic_id,
        timeout_seconds=timeout_seconds,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        fetch_json=delegated_fetch,
        normalize_base_url_func=_normalize_base_url,
    )


def fetch_topic_posts_from_upstreams(
    upstream_base_urls: tuple[str, ...],
    *,
    topic_id: str,
    limit: int = 120,
    timeout_seconds: int = 5,
    auth_token: str | None = None,
    auth_tokens_by_base_url: dict[str, str] | None = None,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
    fetch_json: Callable[[str, str | None], dict] | None = None,
) -> list[dict]:
    delegated_fetch = fetch_json or (
        lambda url, token: _http_get_json(
            url,
            timeout_seconds=timeout_seconds,
            auth_token=token,
            tls_ca_file=tls_ca_file,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
        )
    )
    return _core_fetch_topic_posts_from_upstreams(
        upstream_base_urls,
        topic_id=topic_id,
        limit=limit,
        timeout_seconds=timeout_seconds,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
        fetch_json=delegated_fetch,
        normalize_base_url_func=_normalize_base_url,
    )


def build_server(config: BrainHiveWatchServerConfig | None = None) -> ThreadingHTTPServer:
    cfg = config or BrainHiveWatchServerConfig()
    return build_watch_server(
        cfg,
        workstation_version=VOOL_WORKSTATION_DEPLOYMENT_VERSION,
        server_factory=ThreadingHTTPServer,
        validate_tls_config=lambda inner_cfg: _validate_tls_config(inner_cfg),
        requires_public_tls=lambda host: _requires_public_tls(host),
        watch_tls_enabled=lambda inner_cfg: _watch_tls_enabled(inner_cfg),
        fetch_dashboard_from_upstreams=lambda *args, **kwargs: fetch_dashboard_from_upstreams(*args, **kwargs),
        fetch_topic_from_upstreams=lambda *args, **kwargs: fetch_topic_from_upstreams(*args, **kwargs),
        fetch_topic_posts_from_upstreams=lambda *args, **kwargs: fetch_topic_posts_from_upstreams(*args, **kwargs),
        proxy_voolbook_get=lambda *args, **kwargs: _proxy_voolbook_get(*args, **kwargs),
        http_get_json=lambda *args, **kwargs: _http_get_json(*args, **kwargs),
        normalize_base_url=lambda url: _normalize_base_url(url),
        ssl_context_for_url=lambda *args, **kwargs: _ssl_context_for_url(*args, **kwargs),
        build_tls_context=lambda inner_cfg: _build_tls_context(inner_cfg),
    )


def serve(config: BrainHiveWatchServerConfig | None = None) -> None:
    cfg = config or BrainHiveWatchServerConfig()
    app = create_watch_app(
        config=cfg,
        workstation_version=VOOL_WORKSTATION_DEPLOYMENT_VERSION,
        validate_tls_config=lambda inner_cfg: _validate_tls_config(inner_cfg),
        requires_public_tls=lambda host: _requires_public_tls(host),
        watch_tls_enabled=lambda inner_cfg: _watch_tls_enabled(inner_cfg),
        fetch_dashboard_from_upstreams=lambda *args, **kwargs: fetch_dashboard_from_upstreams(*args, **kwargs),
        fetch_topic_from_upstreams=lambda *args, **kwargs: fetch_topic_from_upstreams(*args, **kwargs),
        fetch_topic_posts_from_upstreams=lambda *args, **kwargs: fetch_topic_posts_from_upstreams(*args, **kwargs),
        proxy_voolbook_get=lambda *args, **kwargs: _proxy_voolbook_get(*args, **kwargs),
        http_get_json=lambda *args, **kwargs: _http_get_json(*args, **kwargs),
        normalize_base_url=lambda url: _normalize_base_url(url),
        ssl_context_for_url=lambda *args, **kwargs: _ssl_context_for_url(*args, **kwargs),
    )

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=str(cfg.host),
            port=int(cfg.port),
            access_log=False,
            log_level="info",
            ssl_certfile=cfg.tls_certfile,
            ssl_keyfile=cfg.tls_keyfile,
            ssl_ca_certs=cfg.tls_ca_file,
        )
    )
    server.run()


def _normalize_base_url(url: str) -> str:
    return _core_normalize_base_url(url)


def _requires_public_tls(host: str) -> bool:
    return _core_requires_public_tls(host)


def _watch_tls_enabled(cfg: BrainHiveWatchServerConfig) -> bool:
    return _core_watch_tls_enabled(cfg)


def _ssl_context_for_url(
    url: str,
    *,
    tls_ca_file: str | None = None,
    tls_insecure_skip_verify: bool = False,
) -> ssl.SSLContext | None:
    return _core_ssl_context_for_url(
        url,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
    )


def _build_tls_context(cfg: BrainHiveWatchServerConfig) -> ssl.SSLContext | None:
    return _core_build_tls_context(cfg)


def _validate_tls_config(cfg: BrainHiveWatchServerConfig) -> None:
    _core_validate_tls_config(cfg)


def _env_text(name: str, default: str) -> str:
    return str(os.environ.get(name, default) or default).strip() or str(default)


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _parse_upstream_base_urls(raw: str) -> tuple[str, ...]:
    urls = tuple(part.strip() for part in str(raw or "").split(",") if str(part).strip())
    if urls:
        return urls
    return BrainHiveWatchServerConfig.upstream_base_urls


def _env_watch_config() -> BrainHiveWatchServerConfig:
    upstream_raw = _env_text(
        "VOOL_WATCH_UPSTREAM_BASE_URLS",
        _env_text("VOOL_WATCH_UPSTREAM", ",".join(BrainHiveWatchServerConfig.upstream_base_urls)),
    )
    return BrainHiveWatchServerConfig(
        host=_env_text("VOOL_WATCH_HOST", BrainHiveWatchServerConfig.host),
        port=_env_int("VOOL_WATCH_PORT", BrainHiveWatchServerConfig.port),
        upstream_base_urls=_parse_upstream_base_urls(upstream_raw),
        request_timeout_seconds=_env_int(
            "VOOL_WATCH_TIMEOUT_SECONDS",
            BrainHiveWatchServerConfig.request_timeout_seconds,
        ),
        auth_token=str(os.environ.get("VOOL_WATCH_AUTH_TOKEN") or "").strip() or None,
        tls_certfile=str(os.environ.get("VOOL_WATCH_TLS_CERTFILE") or "").strip() or None,
        tls_keyfile=str(os.environ.get("VOOL_WATCH_TLS_KEYFILE") or "").strip() or None,
        tls_ca_file=str(os.environ.get("VOOL_WATCH_TLS_CA_FILE") or "").strip() or None,
        tls_insecure_skip_verify=bool(
            str(os.environ.get("VOOL_WATCH_TLS_INSECURE_SKIP_VERIFY", "") or "").strip().lower()
            in {"1", "true", "yes", "on"}
        ),
        dashboard_cache_ttl_seconds=_env_float(
            "VOOL_WATCH_CACHE_TTL_SECONDS",
            BrainHiveWatchServerConfig.dashboard_cache_ttl_seconds,
        ),
    )


def main() -> int:
    # C15: the ONE bounded unattended preflight before bootstrap reaches a credential API.
    from core.unattended_preflight import preflight

    preflight('apps.brain_hive_watch_server')
    env_cfg = _env_watch_config()
    parser = argparse.ArgumentParser(prog="vool-watch")
    parser.add_argument("--host", default=env_cfg.host)
    parser.add_argument("--port", type=int, default=int(env_cfg.port))
    parser.add_argument(
        "--upstream-base-url",
        action="append",
        dest="upstream_base_urls",
        default=list(env_cfg.upstream_base_urls),
        help="Repeat to add multiple upstream meet/watch bases.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=int(env_cfg.request_timeout_seconds))
    parser.add_argument("--auth-token", default=env_cfg.auth_token or "")
    parser.add_argument("--tls-certfile", default=env_cfg.tls_certfile or "")
    parser.add_argument("--tls-keyfile", default=env_cfg.tls_keyfile or "")
    parser.add_argument("--tls-ca-file", default=env_cfg.tls_ca_file or "")
    parser.add_argument(
        "--tls-insecure-skip-verify",
        action="store_true",
        default=bool(env_cfg.tls_insecure_skip_verify),
    )
    parser.add_argument("--cache-ttl-seconds", type=float, default=float(env_cfg.dashboard_cache_ttl_seconds))
    args = parser.parse_args()
    serve(
        BrainHiveWatchServerConfig(
            host=str(args.host),
            port=int(args.port),
            upstream_base_urls=tuple(
                str(url).strip() for url in list(args.upstream_base_urls or ()) if str(url).strip()
            )
            or env_cfg.upstream_base_urls,
            request_timeout_seconds=max(1, int(args.timeout_seconds)),
            auth_token=str(args.auth_token or "").strip() or None,
            tls_certfile=str(args.tls_certfile or "").strip() or None,
            tls_keyfile=str(args.tls_keyfile or "").strip() or None,
            tls_ca_file=str(args.tls_ca_file or "").strip() or None,
            tls_insecure_skip_verify=bool(args.tls_insecure_skip_verify),
            dashboard_cache_ttl_seconds=max(0.0, float(args.cache_ttl_seconds)),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
