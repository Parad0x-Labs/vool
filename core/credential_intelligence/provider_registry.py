"""The typed provider registry for credential intelligence — where a key may be SENT, and how.

One descriptor fully describes one provider for the credential-intelligence flow: the PINNED
verification endpoint (the only place a pasted key may ever be sent for verification), where the
key rides that request (the provider's documented auth placement), what a genuine answer looks
like, the credential slot the verified key persists into, the key prefixes that hint at the
provider, and the capability family a verified binding switches on.

The registry is DATA, injected everywhere: the verifier, store, shortlist and intake all take
it as a parameter and never resolve an endpoint from anywhere else — that is what makes
env-var redirect attacks structurally impossible (there is no env read to subvert).
``default_registry()`` builds the production registry from the two existing provider tables
(``core.cloud_providers`` for LLM clouds, ``core.search_providers`` for web search) so there is
one source of truth for provider facts, per those tables' own law.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

#: The verification search: a stable, boring query that returns results on every engine and carries
#: nothing from the conversation (the same choice ``core/search_connection_state.py`` makes).
VERIFY_SEARCH_QUERY = "example"


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    label: str
    #: "llm_cloud" | "search_web" — selects the availability lane a verified binding activates.
    kind: str
    credential_slot: str
    #: The pinned, absolute verification URL. The ONLY endpoint a key may be verified against.
    verify_endpoint: str
    verify_method: str = "GET"
    #: Where the key rides the verification request, as the provider documents it: "bearer"
    #: (``Authorization: Bearer …``), "header" (``auth_name: key``, e.g. Brave's
    #: ``X-Subscription-Token``) or "body" (JSON field ``auth_name`` of a POST). The verifier refuses
    #: anything else — a query-string placement would put the key in the URL.
    auth_style: str = "bearer"
    auth_name: str = ""
    #: Extra query params merged into the verification URL (e.g. a minimal search query).
    verify_query: dict[str, str] = field(default_factory=dict)
    #: The JSON body of a POST verification (e.g. a minimal search for a POST search API).
    verify_body: dict[str, object] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: Prefixes that HIGH-confidence identify this provider's keys.
    key_prefixes: tuple[str, ...] = ()
    #: Providers sharing one ambiguous key format (the bare ``sk-`` set); the shortlist
    #: offers every member and the operator picks.
    ambiguous_group: str = ""
    capability_family: str = ""
    #: Whether using this provider beyond verification costs money (surfaces in intake UI).
    paid: bool = True
    #: What a genuine 2xx answer holds: the JSON path walked from the body (then the fallback path)
    #: must reach a "list" or an "object". An empty path accepts any JSON object or list. A 2xx that
    #: is not JSON, or JSON of another shape, never verifies a key.
    response_path: tuple[str, ...] = ()
    fallback_response_path: tuple[str, ...] = ()
    response_kind: str = ""
    #: Error codes in the provider's JSON error body that name a rejected credential. Any other
    #: code stays an unidentified provider error — never "invalid".
    invalid_error_codes: tuple[str, ...] = ()
    #: Public billing metadata in a verified answer: name → JSON path. Never a secret.
    account_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: A numeric field whose value <= 0 means the key is valid but its account has no credit left.
    exhausted_when_zero: tuple[str, ...] = ()
    #: True for an endpoint whose base URL the operator supplies (the custom OpenAI-compatible
    #: endpoint); ``verify_path`` is appended to that base URL.
    user_endpoint: bool = False
    verify_path: str = ""
    #: The credential slot holding the endpoint this key commits WITH, when key and endpoint are ONE
    #: pair (the custom endpoint's base URL, UsePod's origin). The store journals, writes and reads
    #: back both halves in one transaction; empty for a key whose destination is fixed.
    endpoint_slot: str = ""
    #: The endpoint a paired key binds to when the operator names none (UsePod's documented origin).
    #: Empty for a user endpoint, whose base URL must always be named.
    default_endpoint: str = ""
    #: The models-list dialect the provider's ordinary API documents (first-party contract,
    # not inferred from row keys).
    models_protocol: str = "openai_models_list"
    #: HTTP status → outcome refinements; every outcome stays distinct by construction.
    invalid_statuses: tuple[int, ...] = (401,)
    unauthorized_statuses: tuple[int, ...] = (403,)
    exhausted_statuses: tuple[int, ...] = (402,)
    rate_limit_statuses: tuple[int, ...] = (429,)
    not_found_statuses: tuple[int, ...] = (404,)
    unavailable_statuses: tuple[int, ...] = (408, 500, 502, 503, 504)
    #: JSON paths (tuples of keys) tried in order to read the account/owner id from a 200 body.
    account_paths: tuple[tuple[str, ...], ...] = (("account", "id"), ("id",), ("owner", "id"))

    def with_base_url(self, base_url: str) -> ProviderDescriptor:
        """This descriptor verified at an operator-supplied base URL (user endpoints only)."""
        return replace(self, verify_endpoint=f"{str(base_url or '').strip().rstrip('/')}{self.verify_path}")


class ProviderRegistry:
    """An immutable provider_id → descriptor mapping."""

    def __init__(self, descriptors: Mapping[str, ProviderDescriptor]):
        self._descriptors: dict[str, ProviderDescriptor] = {
            str(pid).strip().lower(): desc for pid, desc in descriptors.items()
        }

    def get(self, provider_id: str) -> ProviderDescriptor | None:
        return self._descriptors.get(str(provider_id or "").strip().lower())

    def __contains__(self, provider_id: object) -> bool:
        return isinstance(provider_id, str) and provider_id.strip().lower() in self._descriptors

    def providers(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(self._descriptors.values())

    def known_key_prefixes(self) -> tuple[str, ...]:
        """Every documented key prefix in the registry, longest first — local recognition hints."""
        prefixes = {prefix for d in self._descriptors.values() for prefix in d.key_prefixes if prefix}
        return tuple(sorted(prefixes, key=lambda prefix: (-len(prefix), prefix)))

    def __iter__(self):
        return iter(self._descriptors)

    def __len__(self) -> int:
        return len(self._descriptors)


def default_registry() -> ProviderRegistry:
    """The production registry, derived from the existing provider tables.

    LLM clouds verify against their catalog-pinned ``base_url + probe_path`` (OpenRouter's
    ``/key``, the direct providers' auth-gated ``/models``), with the documented answer shape from
    the same table. Web-search providers verify with a one-result search sent exactly the way the
    search client sends it: the key in the provider's own header (or Bearer), the query as a
    query-string parameter for GET APIs and as a JSON body for POST APIs, and the results list
    expected where the search client reads it. The ``custom`` OpenAI-compatible endpoint is the one
    entry whose URL is user-supplied by design, resolved through the existing ``custom_base_url()``.
    """
    from core.cloud_providers import PROVIDERS as CLOUD
    from core.cloud_providers import custom_base_url
    from core.search_providers import SEARCH_PROVIDERS

    descriptors: dict[str, ProviderDescriptor] = {}

    for pid, cfg in CLOUD.items():
        path_token = getattr(cfg, "auth_placement", "") == "url_path_token"
        if cfg.user_base_url:
            base = custom_base_url()
        elif getattr(cfg, "endpoint_slot", ""):
            # The endpoint committed beside this provider's key (UsePod's origin), else its documented one.
            from core.cloud_providers import resolved_base_url

            base = resolved_base_url(pid)
        else:
            base = cfg.base_url.rstrip("/")
        if path_token:
            # The credential is a URL path segment, not a header. The verification path is a
            # TEMPLATE the verifier fills with the pasted token at request time (never stored with a
            # token in it); a Bearer probe of base_url + probe_path would send the token to the wrong
            # path and verify nothing.
            template = str(getattr(cfg, "credential_path_template", "") or "")
            verify_path = f"{template}{cfg.probe_path}" if "{credential}" in template else ""
            auth_style = "url_path_token"
            auth_name = ""
        else:
            verify_path = cfg.probe_path
            # The key placement each provider's ordinary API documents: a named header (Anthropic's
            # Models API takes X-Api-Key) or the default Bearer. The verifier refuses to send a key
            # any other way, so this is the one fact that decides whether a real key verifies.
            auth_style = "header" if cfg.auth_header else "bearer"
            auth_name = cfg.auth_header
        extra_headers = dict(cfg.extra_headers)
        if pid == "openrouter":
            # The attribution headers the probe always sent (X-Title et al.): one policy means
            # Save, Test and refresh now all carry them identically.
            from core.runtime_provider_defaults import apply_openrouter_attribution_headers

            extra_headers = dict(apply_openrouter_attribution_headers(extra_headers))
        endpoint_slot = str(getattr(cfg, "endpoint_slot", "") or "")
        descriptors[pid] = ProviderDescriptor(
            provider_id=pid,
            label=cfg.label,
            kind="llm_cloud",
            credential_slot=cfg.credential_slot,
            verify_endpoint=f"{base}{verify_path}" if verify_path else "",
            verify_method="GET",
            auth_style=auth_style,
            auth_name=auth_name,
            extra_headers=extra_headers,
            key_prefixes=tuple(cfg.key_prefixes),
            ambiguous_group="bare_sk" if pid in ("openai", "deepseek", "moonshot") else "",
            capability_family="cloud_chat",
            paid=True,
            response_path=tuple(cfg.probe_response_path),
            response_kind=str(cfg.probe_response_kind),
            account_fields=dict(cfg.probe_account_fields),
            exhausted_when_zero=tuple(cfg.probe_exhausted_when_zero),
            user_endpoint=bool(cfg.user_base_url),
            verify_path=verify_path,
            models_protocol=str(cfg.model_list_protocol or "openai_models_list"),
            endpoint_slot=endpoint_slot,
            # A paired key with a documented endpoint (UsePod's origin) binds to it when the operator
            # names none; a user endpoint has no default and must always be named.
            default_endpoint="" if cfg.user_base_url or not endpoint_slot else cfg.base_url.rstrip("/"),
        )

    for pid, cfg in SEARCH_PROVIDERS.items():
        verify_query: dict[str, str] = {}
        verify_body: dict[str, object] = {}
        if cfg.method == "POST":
            verify_body = {**dict(cfg.extra_body), cfg.query_field: VERIFY_SEARCH_QUERY}
            if cfg.count_param:
                verify_body[cfg.count_param] = 1
        else:
            verify_query = {**dict(cfg.extra_params), cfg.query_param: VERIFY_SEARCH_QUERY}
            if cfg.count_param:
                verify_query[cfg.count_param] = "1"
        descriptors[f"search.{pid}"] = ProviderDescriptor(
            provider_id=f"search.{pid}",
            label=cfg.label,
            kind="search_web",
            credential_slot=cfg.credential_slot,
            verify_endpoint=cfg.search_url,
            verify_method=cfg.method,
            auth_style=cfg.auth_style,
            auth_name=cfg.auth_name,
            verify_query=verify_query,
            verify_body=verify_body,
            extra_headers=dict(cfg.extra_headers),
            key_prefixes=tuple(cfg.key_prefixes),
            capability_family="web_search",
            paid=True,
            response_path=tuple(cfg.results_path),
            fallback_response_path=tuple(cfg.fallback_results_path),
            response_kind="list",
            invalid_error_codes=tuple(cfg.invalid_key_error_codes),
        )

    return ProviderRegistry(descriptors)


__all__ = [
    "VERIFY_SEARCH_QUERY",
    "ProviderDescriptor",
    "ProviderRegistry",
    "default_registry",
]
