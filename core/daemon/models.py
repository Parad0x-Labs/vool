from __future__ import annotations

from dataclasses import dataclass, field

from core.bootstrap_adapters import BootstrapMirrorAdapter


def is_loopback_host(host: str) -> bool:
    candidate = str(host or "").strip().lower()
    return candidate in {"127.0.0.1", "localhost", "::1"}


@dataclass
class NodeRuntime:
    host: str
    port: int
    public_host: str
    public_port: int
    running: bool


@dataclass
class DaemonConfig:
    bind_host: str = "0.0.0.0"
    bind_port: int = 49152
    advertise_host: str = "127.0.0.1"
    capabilities: list[str] = field(
        default_factory=lambda: [
            "research",
            "classification",
            "ranking",
            "validation",
        ]
    )
    capacity: int = 2
    assist_status: str = "idle"
    local_host_group_hint_hash: str | None = None
    bootstrap_topics: list[str] = field(
        default_factory=lambda: ["knowledge_presence", "safe_orchestration", "local_first"]
    )
    bootstrap_adapter: BootstrapMirrorAdapter | None = None
    maintenance_tick_seconds: int = 30
    auto_request_shards_per_response: int = 2
    health_bind_host: str = "127.0.0.1"
    health_bind_port: int = 0
    health_auth_token: str | None = None
    compute_class: str = "cpu_basic"
    supported_models: list[str] = field(default_factory=list)
    local_worker_threads: int | None = None
