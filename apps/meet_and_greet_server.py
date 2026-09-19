from __future__ import annotations

import argparse
import os

# Repo-root bootstrap: allow running as a file (python3 apps/<x>.py), not just -m.
import os as _bootstrap_os
import ssl
import sys as _bootstrap_sys

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)


from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)
from core import policy_engine
from core.brain_hive_service import BrainHiveService as _BrainHiveService
from core.web.meet.app import create_meet_app
from core.web.meet.routes import _allow_write as _allow_write_impl
from core.web.meet.routes import _query_int as _query_int_impl
from core.web.meet.routes import _resolve_write_rate_limit as _resolve_write_rate_limit_impl
from core.web.meet.routes import dispatch_request as _dispatch_request_impl
from core.web.meet.routes import resolve_static_route as _resolve_static_route_impl
from core.web.meet.server import MeetAndGreetServerConfig
from core.web.meet.server import MeetMetricsCollector as _MeetMetricsCollector
from core.web.meet.server import build_server as _build_server_impl

BrainHiveService = _BrainHiveService
MeetMetricsCollector = _MeetMetricsCollector
build_server = _build_server_impl
dispatch_request = _dispatch_request_impl
resolve_static_route = _resolve_static_route_impl


def _allow_write(bucket_key: str, max_requests_per_minute: int, windows, lock, *, max_clients: int = 4096) -> bool:
    return _allow_write_impl(
        bucket_key,
        max_requests_per_minute,
        windows,
        lock,
        max_clients=max_clients,
    )


def _query_int(query: dict[str, list[str]], key: str) -> int | None:
    return _query_int_impl(query, key, policy_get=policy_engine.get)


def _resolve_write_rate_limit(
    host: str,
    path: str,
    *,
    client_host: str,
    request_meta: dict[str, object] | None,
    default_limit: int,
) -> tuple[str, int]:
    return _resolve_write_rate_limit_impl(
        host,
        path,
        client_host=client_host,
        request_meta=request_meta,
        default_limit=default_limit,
        policy_get=policy_engine.get,
    )


def main() -> int:
    from core.product_edition import edition_allows

    meet_ok, meet_reason = edition_allows("meet_server")
    if not meet_ok:
        print(f"[meet] refusing to start: {meet_reason}", flush=True)
        return 10

    # C15: the ONE bounded unattended preflight before bootstrap reaches a credential API.
    from core.unattended_preflight import preflight

    preflight('apps.meet_and_greet_server')
    parser = argparse.ArgumentParser(prog="vool-meet")
    parser.add_argument("--host", default=os.environ.get("VOOL_MEET_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VOOL_MEET_PORT", "8766")))
    parser.add_argument("--auth-token", default=os.environ.get("VOOL_MEET_AUTH_TOKEN"))
    parser.add_argument("--no-signed-writes", action="store_true")
    parser.add_argument("--tls-certfile", default=os.environ.get("VOOL_MEET_TLS_CERTFILE"))
    parser.add_argument("--tls-keyfile", default=os.environ.get("VOOL_MEET_TLS_KEYFILE"))
    parser.add_argument("--tls-ca-file", default=os.environ.get("VOOL_MEET_TLS_CA_FILE"))
    parser.add_argument("--tls-require-client-cert", action="store_true")
    args = parser.parse_args()
    config = MeetAndGreetServerConfig(
        host=str(args.host),
        port=int(args.port),
        auth_token=str(args.auth_token or "").strip() or None,
        require_signed_writes=not bool(args.no_signed_writes),
        tls_certfile=str(args.tls_certfile or "").strip() or None,
        tls_keyfile=str(args.tls_keyfile or "").strip() or None,
        tls_ca_file=str(args.tls_ca_file or "").strip() or None,
        tls_require_client_cert=bool(args.tls_require_client_cert),
    )
    app = create_meet_app(config=config)

    import uvicorn

    ssl_cert_reqs = None
    if str(config.tls_ca_file or "").strip():
        ssl_cert_reqs = ssl.CERT_REQUIRED if config.tls_require_client_cert else ssl.CERT_OPTIONAL
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=str(config.host),
            port=int(config.port),
            access_log=False,
            log_level="info",
            ssl_certfile=config.tls_certfile,
            ssl_keyfile=config.tls_keyfile,
            ssl_ca_certs=config.tls_ca_file,
            ssl_cert_reqs=ssl_cert_reqs,
        )
    )
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
