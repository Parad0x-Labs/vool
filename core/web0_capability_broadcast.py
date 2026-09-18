from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

_ANNOUNCE_ENV_KEYS = ("VOOL_WEB0_ANNOUNCE", "WEB0_ANNOUNCE")
_MESH_URL_ENV_KEYS = ("VOOL_WEB0_MESH_URL", "WEB0_MESH_URL")
_WORKER_ID_ENV_KEYS = ("VOOL_WORKER_ID", "VOOL_AGENT_ID")
_PRICE_ENV_KEYS = ("VOOL_WEB0_PRICE_PER_TOKEN", "WEB0_PRICE_PER_TOKEN")
_PRIVACY_ENV_KEYS = ("VOOL_WEB0_PRIVACY_MODE", "WEB0_PRIVACY_MODE")

DEFAULT_PRICE_PER_TOKEN = 0.000001  # 1 micro-USDC per output token
VALID_PRIVACY_MODES = ("plain", "zk_ready", "zk_active")


def resolve_announced_price_usdc(
    env: Mapping[str, str], *, default: float = DEFAULT_PRICE_PER_TOKEN
) -> float:
    """Per-output-token price this node advertises, from env. Clamped >= 0."""
    return max(0.0, _env_float(env, *_PRICE_ENV_KEYS, default=default))


def resolve_privacy_mode(env: Mapping[str, str]) -> str:
    """Advertised privacy posture from env; falls back to 'plain' if unset/invalid."""
    mode = _env_first(env, *_PRIVACY_ENV_KEYS).lower()
    return mode if mode in VALID_PRIVACY_MODES else "plain"


@dataclass(frozen=True)
class Web0CapabilityManifest:
    worker_id: str
    provider_ids: tuple[str, ...]
    top_tps: float
    top_tier: str             # "drone" | "queen"
    context_window: int
    tools: tuple[str, ...]
    price_per_token_usdc: float
    privacy_mode: str         # "plain" | "zk_ready" | "zk_active"
    announced_at: float

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["provider_ids"] = list(self.provider_ids)
        d["tools"] = list(self.tools)
        return d


def build_manifest(
    *,
    worker_id: str,
    provider_ids: tuple[str, ...],
    top_tps: float = 0.0,
    top_tier: str = "drone",
    context_window: int = 32768,
    tools: tuple[str, ...] = (),
    price_per_token_usdc: float = DEFAULT_PRICE_PER_TOKEN,
    privacy_mode: str = "plain",
) -> Web0CapabilityManifest:
    return Web0CapabilityManifest(
        worker_id=worker_id,
        provider_ids=provider_ids,
        top_tps=top_tps,
        top_tier=top_tier,
        context_window=context_window,
        tools=tools,
        price_per_token_usdc=price_per_token_usdc,
        privacy_mode=privacy_mode,
        announced_at=time.time(),
    )


def build_manifest_from_env(
    env: Mapping[str, str] | None = None,
) -> Web0CapabilityManifest:
    """Build a minimal manifest from env when no registry is available."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    worker_id = _env_first(env_map, *_WORKER_ID_ENV_KEYS) or "vool"
    return build_manifest(
        worker_id=worker_id,
        provider_ids=(),
        price_per_token_usdc=resolve_announced_price_usdc(env_map),
        privacy_mode=resolve_privacy_mode(env_map),
    )


def announce(
    manifest: Web0CapabilityManifest,
    *,
    mesh_url: str,
    zk_attest_fn: Callable[[Web0CapabilityManifest], str] | None = None,
) -> bool:
    """
    POST capability manifest to the Web0 mesh registry.

    zk_attest_fn is the Dark-Null-Protocol hook — when provided it receives
    the manifest and returns a ZK attestation string that proves the capability
    claim without revealing hardware details. Pass None (default) to skip.
    """
    # The OWNING ENTRY POINT of the startup broadcast's network work (R2b1,
    # amended): the door grants nothing, so the announce authorizes itself
    # here, by name. Scope-local account — handed to no one at close.
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("web0.announce"):
        return _announce_owned(
            manifest,
            mesh_url=mesh_url,
            zk_attest_fn=zk_attest_fn,
        )


def _announce_owned(
    manifest: Web0CapabilityManifest,
    *,
    mesh_url: str,
    zk_attest_fn: Callable[[Web0CapabilityManifest], str] | None = None,
) -> bool:
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote_url

    payload = manifest.to_dict()
    if zk_attest_fn is not None:
        try:
            payload["zk_attestation"] = zk_attest_fn(manifest)
        except Exception:
            payload["zk_attestation"] = None

    data = json.dumps(payload).encode("utf-8")
    try:
        # THE SHARED DOOR, not a private socket: veto enforced before any I/O.
        with open_remote_url(
            f"{mesh_url.rstrip('/')}/v1/workers/announce",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
            timeout=5,
        ) as resp:
            return getattr(resp, "status", None) in (200, 201, 204)
    except RemoteFetchRefusedError:
        return False   # refused before any socket: fail closed, nothing announced
    except Exception:
        return False


def announce_from_env(
    manifest: Web0CapabilityManifest,
    *,
    env: Mapping[str, str] | None = None,
    zk_attest_fn: Callable[[Web0CapabilityManifest], str] | None = None,
) -> bool:
    """
    Announce only when VOOL_WEB0_ANNOUNCE=1 and VOOL_WEB0_MESH_URL is set.
    Safe to call unconditionally at boot — no-ops when env gate is off.
    """
    from core.product_edition import edition_allows

    announce_ok, _announce_reason = edition_allows("web0_announce")
    if not announce_ok:
        return False
    env_map: Mapping[str, str] = os.environ if env is None else env
    if not _env_flag(env_map, *_ANNOUNCE_ENV_KEYS):
        return False
    mesh_url = _env_first(env_map, *_MESH_URL_ENV_KEYS)
    if not mesh_url:
        return False
    return announce(manifest, mesh_url=mesh_url, zk_attest_fn=zk_attest_fn)


def _env_first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        v = str(env.get(name) or "").strip()
        if v:
            return v
    return ""


def _env_flag(env: Mapping[str, str], *names: str) -> bool:
    return any(str(env.get(name) or "").strip().lower() in ("1", "true", "yes", "on") for name in names)


def _env_float(env: Mapping[str, str], *names: str, default: float) -> float:
    for name in names:
        v = str(env.get(name) or "").strip()
        if not v:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return default


__all__ = [
    "DEFAULT_PRICE_PER_TOKEN",
    "VALID_PRIVACY_MODES",
    "Web0CapabilityManifest",
    "announce",
    "announce_from_env",
    "build_manifest",
    "build_manifest_from_env",
    "resolve_announced_price_usdc",
    "resolve_privacy_mode",
]
