from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BrainHiveWatchServerConfig:
    host: str = "127.0.0.1"
    port: int = 8788
    upstream_base_urls: tuple[str, ...] = ("http://127.0.0.1:8766",)
    request_timeout_seconds: int = 5
    auth_token: str | None = None
    auth_tokens_by_base_url: dict[str, str] = field(default_factory=dict)
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    tls_ca_file: str | None = None
    tls_insecure_skip_verify: bool = False
    dashboard_cache_ttl_seconds: float = 5.0
