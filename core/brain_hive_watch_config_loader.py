from __future__ import annotations

from pathlib import Path

from core.config_loader_utils import load_json_config, resolve_optional_config_path
from core.web.watch.config import BrainHiveWatchServerConfig


def load_brain_hive_watch_config(path: str | Path) -> BrainHiveWatchServerConfig:
    config_path, raw = load_json_config(path)
    host = raw.get("host", raw.get("bind_host", BrainHiveWatchServerConfig.host))
    port = int(raw.get("port", raw.get("bind_port", BrainHiveWatchServerConfig.port)))
    upstreams = tuple(raw.get("upstream_base_urls", ()))
    timeout_seconds = int(
        raw.get(
            "request_timeout_seconds",
            BrainHiveWatchServerConfig.request_timeout_seconds,
        )
    )
    auth_token = str(raw.get("auth_token") or "").strip() or None
    auth_tokens_by_base_url = {
        str(base).strip(): str(token).strip()
        for base, token in dict(raw.get("auth_tokens_by_base_url") or {}).items()
        if str(base).strip() and str(token).strip()
    }
    tls_certfile = resolve_optional_config_path(config_path.parent, raw.get("tls_certfile"))
    tls_keyfile = resolve_optional_config_path(config_path.parent, raw.get("tls_keyfile"))
    tls_ca_file = resolve_optional_config_path(config_path.parent, raw.get("tls_ca_file"))
    tls_insecure_skip_verify = bool(raw.get("tls_insecure_skip_verify", False))
    return BrainHiveWatchServerConfig(
        host=str(host),
        port=port,
        upstream_base_urls=upstreams,
        request_timeout_seconds=timeout_seconds,
        auth_token=auth_token,
        auth_tokens_by_base_url=auth_tokens_by_base_url,
        tls_certfile=tls_certfile,
        tls_keyfile=tls_keyfile,
        tls_ca_file=tls_ca_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
    )
