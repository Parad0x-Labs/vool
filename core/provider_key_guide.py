"""Public onboarding links, projected over the providers this runtime supports.

This is presentation metadata only. Verification destinations and credential slots stay
with the existing provider registries; no link here is an API transport destination.
"""
from __future__ import annotations

_MODEL_HELP = {
    "openrouter": ("Choose from several model providers with one account.", "https://openrouter.ai/keys"),
    "openai": ("Use OpenAI models through your API account.", "https://platform.openai.com/api-keys"),
    "anthropic": ("Use Claude models through your API account.", "https://platform.claude.com/settings/keys"),
    "groq": ("Use models hosted by Groq.", "https://console.groq.com/keys"),
    "google": ("Use Gemini models through Google AI Studio.", "https://aistudio.google.com/apikey"),
    "deepseek": ("Use DeepSeek models through your API account.", "https://platform.deepseek.com/api_keys"),
    "moonshot": ("Use Kimi models through your API account.", "https://platform.kimi.ai/console/api-keys"),
    "usepod": ("Use the inference marketplace with your UsePod token.", "https://usepod.ai/"),
    "custom": ("Connect your own compatible model endpoint and its key.", ""),
}


def provider_key_guide() -> list[dict]:
    from core.cloud_providers import PROVIDERS
    from core.eyebrow_client import KEY_NAME
    from core.search_providers import SEARCH_PROVIDERS

    models = []
    for pid, cfg in PROVIDERS.items():
        description, url = _MODEL_HELP.get(pid, ("Use this provider's hosted models.", ""))
        models.append(dict(provider=pid, label=cfg.label, slot=cfg.credential_slot,
                           description=description, url=url))
    search = [dict(provider=pid, label=cfg.label, slot=cfg.credential_slot,
                   description="Find web pages and source links for live research.", url=cfg.signup_url)
              for pid, cfg in SEARCH_PROVIDERS.items()]
    return [
        dict(id="models", title="AI models", description="Connect a cloud model provider. Local models need no API key.", providers=models),
        dict(id="search", title="Web search", description="Add a search provider for live lookups. Built-in keyless search remains available.", providers=search),
        dict(id="security", title="Security scans", description="Optional checks for external skills and plugins. Scanning does not grant tool permissions.",
             providers=[dict(provider="eyebrow", label="Eyebrow", slot=KEY_NAME,
                             description="Check external add-on contents before installing them.", url="https://eyebrow.cc/dashboard")]),
    ]
