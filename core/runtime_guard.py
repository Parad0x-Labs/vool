from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from core.runtime_paths import PROJECT_ROOT, active_vool_home, vool_env

_PLACEHOLDER_TOKENS = ("placeholder", "change-me", "sample", "example", "replace-me", "todo")


def automatic_launch_network_allowed(capability: str) -> bool:
    """Consent for source-launcher sidecars before the API has booted.

    Only a readable saved profile policy grants automatic outbound work. Bundled
    defaults and a malformed/missing file cannot be mistaken for user consent.
    """
    if capability not in {"outbound", "web"}:
        return False
    try:
        import yaml
    except ImportError:
        return False
    try:
        path = active_vool_home() / "config" / "default_policy.yaml"
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            return False
        network = saved.get("network")
        system = saved.get("system")
        from core import policy_engine
        from core.remote_fetch_policy import local_only_active

        policy_engine.load(force_reload=True)
        if local_only_active():
            return False
        outbound = isinstance(network, dict) and network.get("outbound_enabled") is True
        web = isinstance(system, dict) and system.get("allow_web_fallback") is True
        if capability == "web" and any(
            policy_engine._env_is_truthy(vool_env(name)) for name in ("DISABLE_WEB", "NO_WEB")
        ):
            return False
        return outbound if capability == "outbound" else web
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return False


def is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def looks_placeholder_text(value: str | None) -> bool:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return True
    return any(token in lowered for token in _PLACEHOLDER_TOKENS)


def validate_meet_public_deployment(
    *,
    bind_host: str,
    public_base_url: str,
    auth_token: str | None,
    tls_certfile: str | None = None,
    tls_keyfile: str | None = None,
    allow_insecure_public_http: bool = False,
) -> list[str]:
    issues: list[str] = []
    if is_loopback_host(bind_host):
        return issues
    if looks_placeholder_text(auth_token):
        issues.append("Non-loopback meet node requires a non-placeholder auth token.")
    if not allow_insecure_public_http:
        if not str(tls_certfile or "").strip() or not str(tls_keyfile or "").strip():
            issues.append("Non-loopback meet node requires TLS cert/key unless explicitly marked insecure for closed testing.")
    parsed = urlparse(public_base_url)
    host = (parsed.hostname or "").strip().lower()
    if not parsed.scheme or not host:
        issues.append("Non-loopback meet node requires a valid public_base_url.")
    elif host.endswith(".example.vool") or host.endswith(".example.test") or host == "localhost" or host.startswith("127."):
        issues.append("Non-loopback meet node cannot use placeholder or loopback public_base_url.")
    if (PROJECT_ROOT / ".vool_local").resolve() == active_vool_home():
        issues.append("Non-loopback meet node should use a dedicated VOOL_HOME instead of the default project-local runtime.")
    return issues


def enforce_meet_public_deployment(
    *,
    bind_host: str,
    public_base_url: str,
    auth_token: str | None,
    tls_certfile: str | None = None,
    tls_keyfile: str | None = None,
    allow_insecure_public_http: bool = False,
) -> None:
    issues = validate_meet_public_deployment(
        bind_host=bind_host,
        public_base_url=public_base_url,
        auth_token=auth_token,
        tls_certfile=tls_certfile,
        tls_keyfile=tls_keyfile,
        allow_insecure_public_http=allow_insecure_public_http,
    )
    if issues:
        raise ValueError("Unsafe meet public deployment configuration: " + " ".join(issues))


def runtime_artifact_hints() -> list[str]:
    hints: list[str] = []
    if (
        Path(PROJECT_ROOT / "storage" / "vool_web0_v2.db").exists()
        or Path(PROJECT_ROOT / "storage" / "nulla_web0_v2.db").exists()
    ):
        hints.append("workspace_db_artifact")
    if any(
        Path(PROJECT_ROOT / "data" / "keys" / name).exists()
        for name in ("node_signing_key.b64", "node_signing_key.json", "node_signing_key.keyring.json")
    ):
        hints.append("workspace_key_artifact")
    return hints
