from __future__ import annotations

import os
from urllib.parse import urlparse

from core.cloud_broker import CloudModelBroker
from core.cloud_credential_broker import CloudCredentialBroker
from core.cloud_provider_registry import CloudProviderRegistry, DeclarativeOpenAIProvider
from core.cloud_transport import PolicyBoundCloudTransport

# A monotonically-increasing epoch bumped whenever a cloud key is added or removed. The broker
# (System B, the free/paid escalation path) is cached on the router and reads the key set only at
# build time, so a mid-session key change would otherwise not appear there until a restart. The
# router compares this epoch and rebuilds when it changed.
_cloud_broker_epoch = 0


def cloud_broker_epoch() -> int:
    return _cloud_broker_epoch


def bump_cloud_broker_epoch() -> int:
    global _cloud_broker_epoch
    _cloud_broker_epoch += 1
    return _cloud_broker_epoch


def build_default_cloud_broker() -> CloudModelBroker:
    credentials = CloudCredentialBroker()
    registry = CloudProviderRegistry(credential_broker=credentials)
    transports: dict[str, PolicyBoundCloudTransport] = {}

    # The full accepted alias tuple comes from the ONE canonical table; a VOOL_-prefixed
    # compat key must arm escalation exactly like the vendor-canonical spelling.
    from core.cloud_providers import key_env_names

    if any(credentials.has("llm.cloud.openrouter", env_name=name) for name in key_env_names("openrouter")):
        provider = registry.add_openrouter()
        transports[provider.provider_id] = PolicyBoundCloudTransport(
            allowed_hosts=("openrouter.ai",), credential_broker=credentials
        )

    cloudflare_account = str(os.getenv("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    if cloudflare_account and credentials.has("llm.cloud.cloudflare", env_name="CLOUDFLARE_API_TOKEN"):
        provider = registry.add_cloudflare(account_id=cloudflare_account)
        transports[provider.provider_id] = PolicyBoundCloudTransport(
            allowed_hosts=("api.cloudflare.com",), credential_broker=credentials
        )

    generic_base = str(os.getenv("VOOL_REMOTE_BASE_URL") or "").rstrip("/")
    generic_env = "VOOL_REMOTE_API_KEY"
    if generic_base and credentials.has("llm.cloud.generic", env_name=generic_env):
        host = str(urlparse(generic_base).hostname or "").strip().lower()
        provider = registry.add_declarative_openai(
            DeclarativeOpenAIProvider(
                provider_id="generic-openai",
                base_url=generic_base,
                credential_name="llm.cloud.generic",
                credential_env=generic_env,
            )
        )
        transports[provider.provider_id] = PolicyBoundCloudTransport(
            allowed_hosts=(host,), credential_broker=credentials
        )

    return CloudModelBroker(registry=registry, transports=transports)


__all__ = ["build_default_cloud_broker", "bump_cloud_broker_epoch", "cloud_broker_epoch"]
