"""Single source of truth for the web-search APIs VOOL can bring-your-own-key to.

This is the search-side twin of ``core/cloud_providers.py`` and deliberately mirrors its shape, so
the settings surface, the credential slots, the auth probe and the search chain all read one table
instead of hardcoding provider facts in four places.

A provider is fully described here: where to send the query, how the key rides the request, where
the results list lives in the answer, and which fields carry title/url/snippet. That is what lets
``tools/web/search_api_client.py`` serve every provider with no per-provider branch.

Two design points worth stating, because both are load-bearing:

**A pasted key is the permission.** ``policy_engine.allowed_web_engines()`` is a closed allowlist
whose own comment records that an installed user policy file "WILL silently strip" an engine added
after it was written -- deliberately, because that list is the permission boundary for engines the
runtime would otherwise use on its own. A key-backed provider is a different case: the user pasted
a credential for this specific service, which is a narrower and more explicit act of consent than
a config default. So key-backed providers are admitted by KEY PRESENCE rather than by the keyless
allowlist, and they are still subject to every other gate -- the per-turn remote-fetch veto, the
egress door's accounting, and the transport check. What must never happen is the silent case: a
user pastes a key and nothing changes with no explanation.

**Detection may guess, but must never guess silently.** ``detect_provider`` returns a confidence
exactly like the cloud table's does. A key whose prefix is unique identifies its provider outright;
a key with no distinguishing prefix is reported as ambiguous with candidates, and the caller asks
rather than storing under a guess. Storing a key in the wrong slot produces a provider that fails
auth forever with a key the user knows is good -- the worst failure this feature can have.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Credential-store slot names. The store is name-agnostic, so a slot is just a stable string;
# keeping the prefix uniform makes the whitelist and any secret sweep grep-able. It intentionally
# does not collide with the cloud table's "llm.cloud." prefix.
_SLOT_PREFIX = "search.web."

Confidence = Literal["high", "low", "none"]
#: How the key rides the request. "body" is POST-JSON only.
AuthStyle = Literal["header", "bearer", "query", "body"]


@dataclass(frozen=True)
class SearchProviderConfig:
    provider_id: str
    label: str
    search_url: str
    auth_style: AuthStyle
    #: Header name, query-parameter name, or JSON body field carrying the key. Empty for "bearer".
    auth_name: str = ""
    method: Literal["GET", "POST"] = "GET"
    #: Where the query text goes: query-string parameter (GET) or JSON field (POST).
    query_param: str = "q"
    query_field: str = "query"
    #: Parameter/field asking for N results. Empty when the provider has no such control.
    count_param: str = ""
    #: Path into the response JSON to the results list, and an optional second shape to try.
    results_path: tuple[str, ...] = ("results",)
    fallback_results_path: tuple[str, ...] = ()
    #: Field names to try, in order, for each normalized field.
    title_fields: tuple[str, ...] = ("title",)
    url_fields: tuple[str, ...] = ("url",)
    snippet_fields: tuple[str, ...] = ("description", "snippet", "content", "text")
    #: Prefixes that HIGH-confidence identify this provider's keys.
    key_prefixes: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_params: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, object] = field(default_factory=dict)
    #: Where the user gets a key, shown in settings so the flow is self-serve.
    signup_url: str = ""
    free_tier: str = ""
    #: Error codes in the provider's JSON error body that name a rejected credential. Only these turn
    #: an error into "unauthorized"; any other code on the same HTTP status stays an unidentified
    #: provider error, so a bad request parameter is never reported as a bad key.
    invalid_key_error_codes: tuple[str, ...] = ()

    @property
    def credential_slot(self) -> str:
        return f"{_SLOT_PREFIX}{self.provider_id}"


@dataclass(frozen=True)
class SearchProviderGuess:
    provider_id: str | None
    confidence: Confidence
    candidates: tuple[str, ...] = ()


# Every endpoint, auth header, result path and key prefix below was verified against the provider's
# live documentation on 2026-08-19; see docs/SEARCH_API_BYOK.md for the links and the two facts that
# could NOT be confirmed from primary docs (noted inline). A wrong auth header here is
# indistinguishable to the user from a bad key, so nothing in this table is guessed.
#
# Order matters twice: it is the order the settings list shows, and the order in which several
# stored keys are tried. Providers that mirror a general web index (Serper/Brave) lead, because the
# everyday questions this feature exists for -- prices, travel, product facts -- are what they are
# best at.
SEARCH_PROVIDERS: dict[str, SearchProviderConfig] = {
    "serper": SearchProviderConfig(
        provider_id="serper",
        label="Serper",
        search_url="https://google.serper.dev/search",
        auth_style="header",
        auth_name="X-API-KEY",
        method="POST",
        query_field="q",
        count_param="num",
        results_path=("organic",),
        title_fields=("title",),
        url_fields=("link", "url"),  # Serper calls it `link`
        snippet_fields=("snippet", "description"),
        env_names=("SERPER_API_KEY",),
        signup_url="https://serper.dev/",
        free_tier="2,500 free, no card",
    ),
    "brave": SearchProviderConfig(
        provider_id="brave",
        label="Brave Search",
        search_url="https://api.search.brave.com/res/v1/web/search",
        auth_style="header",
        auth_name="X-Subscription-Token",
        method="GET",
        query_param="q",
        count_param="count",
        results_path=("web", "results"),
        title_fields=("title",),
        url_fields=("url",),
        snippet_fields=("description", "extra_snippets"),
        key_prefixes=("BSAI",),
        env_names=("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY"),
        signup_url="https://api-dashboard.search.brave.com/",
        # Stated plainly because it is the one provider here that cannot deliver the
        # "paste a key, no billing" experience the rest of this feature promises.
        free_tier="card required (free tier ended Feb 2026)",
        # Brave's documented error body is {"type": "ErrorResponse", "error": {"code", ...}} (API
        # reference, read 2026-09-14). This code value -- a 422 with meta.component "authentication"
        # -- comes from a public report of an invalid token, not from Brave's reference. It names the
        # credential, so only it reads as a rejected key; every other 422 stays unidentified.
        invalid_key_error_codes=("SUBSCRIPTION_TOKEN_INVALID",),
    ),
    "tavily": SearchProviderConfig(
        provider_id="tavily",
        label="Tavily",
        search_url="https://api.tavily.com/search",
        auth_style="bearer",
        method="POST",
        query_field="query",
        count_param="max_results",
        results_path=("results",),
        title_fields=("title",),
        url_fields=("url",),
        snippet_fields=("content", "description"),
        key_prefixes=("tvly-",),
        env_names=("TAVILY_API_KEY",),
        signup_url="https://tavily.com/",
        free_tier="1,000/month free",
    ),
    "exa": SearchProviderConfig(
        provider_id="exa",
        label="Exa",
        search_url="https://api.exa.ai/search",
        auth_style="header",
        auth_name="x-api-key",
        method="POST",
        query_field="query",
        # Exa's result-count field name could not be confirmed from primary docs, and sending a
        # wrong field is worse than sending none (some APIs 422 on unknown keys), so the request
        # omits it and takes the provider default; `max_hits` still trims the parsed list.
        count_param="",
        results_path=("results",),
        title_fields=("title",),
        url_fields=("url",),
        snippet_fields=("highlights", "text", "summary"),
        env_names=("EXA_API_KEY",),
        signup_url="https://exa.ai/",
        free_tier="$10/month credit, no card",
    ),
    "firecrawl": SearchProviderConfig(
        provider_id="firecrawl",
        label="Firecrawl",
        search_url="https://api.firecrawl.dev/v2/search",
        auth_style="bearer",
        method="POST",
        query_field="query",
        count_param="limit",
        results_path=("data", "web"),
        title_fields=("title",),
        url_fields=("url",),
        snippet_fields=("description", "markdown"),
        key_prefixes=("fc-",),
        env_names=("FIRECRAWL_API_KEY",),
        signup_url="https://www.firecrawl.dev/",
        free_tier="1,000/month free, no card",
    ),
    "jina": SearchProviderConfig(
        provider_id="jina",
        label="Jina",
        search_url="https://s.jina.ai/",
        auth_style="bearer",
        method="POST",
        query_field="q",
        count_param="num",
        results_path=("data",),
        title_fields=("title",),
        url_fields=("url",),
        snippet_fields=("description", "content"),
        key_prefixes=("jina_",),
        env_names=("JINA_API_KEY",),
        # Without an explicit JSON Accept, Jina answers in Markdown for LLM consumption, which the
        # parser cannot read. The client sends Accept: application/json for every provider, so this
        # is satisfied there rather than repeated here.
        signup_url="https://jina.ai/",
        free_tier="free tokens on signup",
    ),
    "you": SearchProviderConfig(
        provider_id="you",
        label="You.com",
        search_url="https://ydc-index.io/v1/search",
        auth_style="header",
        auth_name="X-API-Key",
        method="POST",
        query_field="query",
        count_param="count",
        results_path=("results", "web"),
        title_fields=("title",),
        url_fields=("url",),
        snippet_fields=("description", "snippets"),
        env_names=("YDC_API_KEY", "YOU_API_KEY"),
        signup_url="https://you.com/",
        free_tier="$100 signup credit",
    ),
}


def config_for(provider_id: str) -> SearchProviderConfig | None:
    return SEARCH_PROVIDERS.get(str(provider_id or "").strip().lower())


def slot_for(provider_id: str) -> str:
    cfg = config_for(provider_id)
    return cfg.credential_slot if cfg else ""


def provider_for_slot(slot: str) -> str:
    text = str(slot or "").strip()
    for cfg in SEARCH_PROVIDERS.values():
        if cfg.credential_slot == text:
            return cfg.provider_id
    return ""


def all_slots() -> tuple[str, ...]:
    return tuple(cfg.credential_slot for cfg in SEARCH_PROVIDERS.values())


def detect_provider(key: str) -> SearchProviderGuess:
    """Map a pasted key to a provider by prefix, reporting how sure that is.

    A pasted key routinely arrives with decoration -- surrounding quotes from a config file, a
    leading "Bearer ", stray whitespace from a copy. Normalizing here (rather than in each caller)
    is what makes "drop the key in and it works" true for the paste people actually perform.
    """
    k = normalize_key(key)
    if not k:
        return SearchProviderGuess(None, "none")
    for cfg in SEARCH_PROVIDERS.values():
        for prefix in cfg.key_prefixes:
            if k.startswith(prefix):
                return SearchProviderGuess(cfg.provider_id, "high")
    # No distinguishing prefix: every provider whose keys carry no prefix is a candidate, and the
    # caller must ask. Returning a single id here would silently store the key in a slot that can
    # never authenticate.
    candidates = tuple(cfg.provider_id for cfg in SEARCH_PROVIDERS.values() if not cfg.key_prefixes)
    if candidates:
        return SearchProviderGuess(None, "low", candidates)
    return SearchProviderGuess(None, "none")


def normalize_key(key: str) -> str:
    """Strip the decoration a real paste carries, without altering the key body."""
    text = str(key or "").strip()
    if text[:1] in {'"', "'"} and text[-1:] == text[:1] and len(text) >= 2:
        text = text[1:-1].strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    elif text.lower() == "bearer":
        # A paste of the scheme word with no key behind it. Returning "bearer" here would store it
        # as the secret and produce a provider that fails auth forever, with settings cheerfully
        # showing it connected -- so this normalizes to nothing and the caller refuses it.
        return ""
    return text


def keyed_providers(has_key=None) -> tuple[str, ...]:
    """Provider ids that currently hold a key, in table order.

    ``has_key`` is a callable ``slot -> bool`` (defaults to the credential store) so this stays
    testable and import-light, exactly like ``cloud_providers.active_provider``.
    """
    if has_key is None:
        try:
            from core import credential_store

            has_key = credential_store.has_credential
        except Exception:
            return ()
    keyed: list[str] = []
    for provider_id, cfg in SEARCH_PROVIDERS.items():
        try:
            if has_key(cfg.credential_slot):
                keyed.append(provider_id)
        except Exception:
            continue
    return tuple(keyed)


__all__ = [
    "SEARCH_PROVIDERS",
    "AuthStyle",
    "Confidence",
    "SearchProviderConfig",
    "SearchProviderGuess",
    "all_slots",
    "config_for",
    "detect_provider",
    "keyed_providers",
    "normalize_key",
    "provider_for_slot",
    "slot_for",
]
