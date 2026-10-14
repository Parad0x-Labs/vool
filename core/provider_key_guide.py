"""Public onboarding links, projected over the providers this runtime supports.

This is presentation metadata only. Verification destinations and credential slots stay
with the existing provider registries; no link here is an API transport destination.

Category and provider copy resolve through the i18n catalog at the presentation
owner (stable ``keyguide.*`` keys, English literals below as the fallback source);
provider ids, labels and URLs are brand identifiers and never translate.
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


def provider_key_guide(locale: str = "en") -> list[dict]:
    from core.cloud_providers import PROVIDERS
    from core.coingecko_auth import PLANS as COINGECKO_PLANS
    from core.eyebrow_client import KEY_NAME
    from core.i18n.catalog import catalog_for
    from core.search_providers import SEARCH_PROVIDERS

    catalog = catalog_for(locale)

    def text(key: str, fallback: str) -> str:
        resolved = catalog.text(key)
        return resolved if resolved != key else fallback

    models = []
    for pid, cfg in PROVIDERS.items():
        description, url = _MODEL_HELP.get(pid, ("Use this provider’s hosted models.", ""))
        models.append(dict(provider=pid, label=cfg.label, slot=cfg.credential_slot,
                           description=text(f"keyguide.provider.{pid}.description", description), url=url))
    search = [dict(provider=pid, label=cfg.label, slot=cfg.credential_slot,
                   description=text("keyguide.provider.search.description",
                                    "Find web pages and source links for live research."), url=cfg.signup_url)
              for pid, cfg in SEARCH_PROVIDERS.items()]
    from core.chain_providers import setup_guide

    chain = []
    for row in setup_guide():
        pid = row["provider"]
        steps = [text(f"keyguide.provider.{pid}.step{i}", step) for i, step in enumerate(row["steps"], start=1)]
        notes = [text(f"keyguide.provider.{pid}.note{i}", note) for i, note in enumerate(row["notes"], start=1)]
        chain.append(dict(provider=pid, label=row["label"], slot=row["slot"], url=row["url"],
                          description=text(f"keyguide.provider.{pid}.description", row["description"]),
                          steps=steps, notes=notes, chains=row["chains"]))
    return [
        dict(id="models", title=text("keyguide.models.title", "AI models"),
             description=text("keyguide.models.description",
                              "Connect a cloud model provider. Local models need no API key."), providers=models),
        dict(id="search", title=text("keyguide.search.title", "Web search"),
             description=text("keyguide.search.description",
                              "Add a search provider for live lookups. Built-in keyless search remains available."),
             providers=search),
        dict(id="security", title=text("keyguide.security.title", "Security scans"),
             description=text("keyguide.security.description",
                              "Optional checks for external skills and plugins. Scanning does not grant tool permissions."),
             providers=[dict(provider="eyebrow", label="Eyebrow", slot=KEY_NAME,
                             description=text("keyguide.provider.eyebrow.description",
                                              "Check external add-on contents before installing them."),
                             url="https://eyebrow.cc/dashboard")]),
        dict(id="chain", title=text("keyguide.chain.title", "Blockchain data"),
             description=text("keyguide.chain.description",
                              "Add a chain-data provider for read-only wallet, token and transaction lookups in any chat. "
                              "These keys never sign or pay."),
             providers=chain),
        dict(id="market", title=text("keyguide.market.title", "Market data"),
             description=text("keyguide.market.description",
                              "Raise the limits on live crypto prices. Prices keep working without a key."),
             providers=[dict(provider=plan.provider_id, label=plan.label, slot=plan.credential_slot,
                             description=text(f"keyguide.provider.{plan.provider_id}.description",
                                              "Free Demo key from your CoinGecko developer dashboard."
                                              if plan.provider_id == "coingecko"
                                              else "Paid CoinGecko API plan key."),
                             url=plan.signup_url)
                        for plan in COINGECKO_PLANS.values()]),
    ]
