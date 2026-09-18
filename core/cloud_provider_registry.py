from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from adapters.cloudflare_workers_ai_provider import CloudflareWorkersAIProvider
from adapters.generic_openai_cloud_provider import GenericOpenAICloudProvider
from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
from core.cloud_credential_broker import CloudCredentialBroker
from core.cloud_provider_contract import CloudProviderAdapter


@dataclass(frozen=True)
class DeclarativeOpenAIProvider:
    provider_id: str
    base_url: str
    credential_name: str
    credential_env: str = ""


class CloudProviderRegistry:
    """Provider registry with no arbitrary executable-plugin registration surface."""

    def __init__(self, *, credential_broker: CloudCredentialBroker | None = None) -> None:
        self._credentials = credential_broker or CloudCredentialBroker()
        self._providers: dict[str, CloudProviderAdapter] = {}

    def add_openrouter(self) -> OpenRouterCloudProvider:
        provider = OpenRouterCloudProvider(credential_broker=self._credentials)
        self._providers[provider.provider_id] = provider
        return provider

    def add_cloudflare(self, *, account_id: str) -> CloudflareWorkersAIProvider:
        provider = CloudflareWorkersAIProvider(account_id=account_id, credential_broker=self._credentials)
        self._providers[provider.provider_id] = provider
        return provider

    def add_declarative_openai(self, config: DeclarativeOpenAIProvider) -> GenericOpenAICloudProvider:
        parsed = urlparse(str(config.base_url or ""))
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("declarative provider base_url must be absolute HTTPS")
        provider = GenericOpenAICloudProvider(
            provider_id=config.provider_id,
            base_url=config.base_url,
            credential_name=config.credential_name,
            credential_env=config.credential_env,
            credential_broker=self._credentials,
        )
        self._providers[provider.provider_id] = provider
        return provider

    def get(self, provider_id: str) -> CloudProviderAdapter | None:
        return self._providers.get(str(provider_id or "").strip().lower())

    def list(self) -> tuple[CloudProviderAdapter, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))


__all__ = ["CloudProviderRegistry", "DeclarativeOpenAIProvider"]
