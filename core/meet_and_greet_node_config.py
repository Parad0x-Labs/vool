"""The meet-and-greet NODE's typed configuration — runtime-owned truth (M1).

Lives in core because `core.meet_and_greet_config_loader` constructs it from disk:
core may not import apps (the composition-boundary law), and a config dataclass the
runtime loads is behavior, not process composition. `apps/meet_and_greet_node.py`
imports it from here and re-exports it so every existing caller keeps working; that
facade binding may be removed once callers import directly from this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.meet_and_greet_replication import ReplicationConfig
from core.meet_and_greet_service import MeetAndGreetConfig


@dataclass
class MeetPeerSeed:
    node_id: str
    base_url: str
    region: str = "global"
    role: str = "seed"
    platform_hint: str = "unknown"
    priority: int = 100


@dataclass
class MeetAndGreetNodeConfig:
    node_id: str
    public_base_url: str
    region: str = "global"
    role: str = "seed"
    priority: int = 100
    bind_host: str = "127.0.0.1"
    bind_port: int = 8766
    auth_token: str | None = None
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    tls_ca_file: str | None = None
    tls_require_client_cert: bool = False
    allow_insecure_public_http: bool = False
    sync_interval_seconds: int = 15
    service_config: MeetAndGreetConfig = field(default_factory=MeetAndGreetConfig)
    replication_config: ReplicationConfig = field(default_factory=ReplicationConfig)
    seed_peers: list[MeetPeerSeed] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.service_config.local_region == "global" and self.region != "global":
            self.service_config.local_region = self.region
        if self.replication_config.local_region == "global" and self.region != "global":
            self.replication_config.local_region = self.region
        if not str(self.replication_config.auth_token or "").strip():
            self.replication_config.auth_token = str(self.auth_token or "").strip() or None
