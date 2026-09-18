from __future__ import annotations

from pathlib import Path

from core.config_loader_utils import load_json_config, resolve_optional_config_path
from core.meet_and_greet_node_config import MeetAndGreetNodeConfig, MeetPeerSeed
from core.meet_and_greet_replication import ReplicationConfig
from core.meet_and_greet_service import MeetAndGreetConfig


def load_meet_node_config(path: str | Path) -> MeetAndGreetNodeConfig:
    config_path, raw = load_json_config(path)
    raw["tls_certfile"] = resolve_optional_config_path(config_path.parent, raw.get("tls_certfile"))
    raw["tls_keyfile"] = resolve_optional_config_path(config_path.parent, raw.get("tls_keyfile"))
    raw["tls_ca_file"] = resolve_optional_config_path(config_path.parent, raw.get("tls_ca_file"))
    service_config = MeetAndGreetConfig(**dict(raw.pop("service_config", {})))
    replication_payload = dict(raw.pop("replication_config", {}))
    if "tls_insecure_skip_verify" not in replication_payload and "tls_insecure_skip_verify" in raw:
        replication_payload["tls_insecure_skip_verify"] = bool(raw.get("tls_insecure_skip_verify", False))
    raw.pop("tls_insecure_skip_verify", None)
    replication_payload["tls_ca_file"] = resolve_optional_config_path(
        config_path.parent,
        replication_payload.get("tls_ca_file"),
    )
    replication_config = ReplicationConfig(**replication_payload)
    seed_peers = [MeetPeerSeed(**dict(item)) for item in list(raw.pop("seed_peers", []))]
    return MeetAndGreetNodeConfig(
        service_config=service_config,
        replication_config=replication_config,
        seed_peers=seed_peers,
        **raw,
    )
