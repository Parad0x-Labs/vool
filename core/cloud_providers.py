"""Single source of truth for the cloud LLM providers VOOL can bring-your-own-key to.

Every provider here is reachable through its OpenAI-compatible endpoint with a Bearer key, so the
one generic ``adapters/openai_compatible_adapter.py`` serves all of them — a provider is fully
described by this table (base URL, credential slot, probe path, id style, catalog source). Key
detection maps a pasted key to a provider by its prefix; a bare ``sk-…`` is deliberately ambiguous
(OpenAI / DeepSeek / Moonshot share it), so callers must ASK rather than guess and store.

The one exception to "Bearer key" is marked by ``auth_placement``: UsePod authenticates with a token
in the URL path, has its own adapter (``adapters/usepod_adapter.py``) and its own URL builder
(``core.usepod.descriptor``). Any consumer of this table that builds ``base_url + path`` with an
``Authorization`` header must check ``auth_placement`` first.

Nothing else in the codebase should hardcode a provider fact — import it from here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Credential-store slot names. The store is name-agnostic (AES-256-GCM), so a slot is just a
# stable string; keeping the prefix uniform makes the whitelist + sweep grep-able.
_SLOT_PREFIX = "llm.cloud."

Confidence = Literal["high", "low", "none"]
AUTH_BEARER = "authorization_bearer"
AUTH_URL_PATH_TOKEN = "url_path_token"

# The custom endpoint's base URL is user-supplied. It persists in the credential store (the store
# is name-agnostic) so a Settings save survives a restart, with env overrides taking precedence.
CUSTOM_BASE_URL_SLOT = "llm.cloud.custom_base_url"

# UsePod's origin is the table's base_url unless the owner explicitly saved another one together
# with the token (a loopback service, a staging gateway). No env var can set it: an environment
# value silently re-pointing a credential-bearing request is an exfiltration route, not config.
USEPOD_ORIGIN_SLOT = "llm.cloud.usepod_origin"


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    label: str
    base_url: str
    probe_path: str  # auth-gated GET that is 200 with a good key, 401/403 without
    model_id_style: Literal["slash", "bare"]
    model_catalog_source: Literal["openrouter_live", "static", "discover", "usepod_marketplace"]
    default_model: str = ""
    # Prefixes that HIGH-confidence identify this provider. A bare "sk-" is handled separately
    # (ambiguous) and is intentionally NOT listed here for openai/deepseek/moonshot.
    key_prefixes: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()
    base_url_env_names: tuple[str, ...] = ()
    # Static request headers merged alongside the key auth (e.g. Anthropic's version pin).
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Where the key rides the VERIFICATION/PROBE request (the ordinary API, not the OpenAI-
    # compatibility chat endpoint): "" = "Authorization: Bearer"; a name = that header carries
    # the bare key. Anthropic's Models API documents X-Api-Key + anthropic-version and does not
    # document a Bearer option (platform.claude.com, read 2026-09-15); a Bearer probe would
    # 401 every real Anthropic key. The chat adapter keeps its own (compat) placement.
    auth_header: str = ""
    # The models-list DIALECT this provider's ordinary API documents. First-party contract, not
    # something to infer from row keys: "openai_models_list" ({id, context_length,
    # top_provider...}) or "anthropic_models_list" ({id, display_name, max_tokens...}).
    model_list_protocol: str = "openai_models_list"
    # A user-supplied base URL (custom endpoint) rather than a fixed vendor URL.
    user_base_url: bool = False
    # Where the credential travels: an ``Authorization: Bearer`` header (every entry before UsePod),
    # or a segment of the request URL path, in which case there is no key header at all and the
    # URL itself is the secret.
    auth_placement: Literal["authorization_bearer", "url_path_token"] = AUTH_BEARER
    # For ``url_path_token`` only: the path prefix holding the credential, with ``{credential}`` where
    # the token goes (joined as ``resolved_base_url + template + probe_path``). Filled in at request
    # time, never stored with a token in it.
    credential_path_template: str = ""
    # The credential slot holding the endpoint this provider's key is bound to when key and endpoint
    # are ONE committed pair: the custom endpoint's base URL, UsePod's origin. Both halves commit in
    # one credential transaction and are read by the one pair reader (``resolved_provider_pair``), so
    # a current key is never combined with an endpoint another save left behind. Empty: the key's
    # destination is the fixed ``base_url``.
    endpoint_slot: str = ""
    # What the probe endpoint answers for a GOOD key, so a 200 that is not the provider's API (a
    # captive portal's HTML page, another service's JSON) is never read as a verified key. The path
    # is walked into the JSON body and must reach a list ("list"), an object ("object") or, for a
    # scalar field, any non-null value ("value").
    probe_response_path: tuple[str, ...] = ("data",)
    probe_response_kind: Literal["list", "object", "value"] = "list"
    # Public billing metadata the probe answer carries (never a secret), and the numeric field whose
    # value <= 0 means the key is valid but has no credit left -- kept apart from key validity.
    probe_account_fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    probe_exhausted_when_zero: tuple[str, ...] = ()

    @property
    def credential_slot(self) -> str:
        return f"{_SLOT_PREFIX}{self.provider_id}"


@dataclass(frozen=True)
class ProviderGuess:
    provider_id: str | None
    confidence: Confidence
    candidates: tuple[str, ...] = ()


PROVIDERS: dict[str, ProviderConfig] = {
    "openrouter": ProviderConfig(
        provider_id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        probe_path="/key",  # /models is PUBLIC on OpenRouter, so it cannot prove a key; /key can
        model_id_style="slash",
        model_catalog_source="openrouter_live",
        default_model="openai/gpt-4.1-mini",
        key_prefixes=("sk-or-",),
        env_names=("OPENROUTER_API_KEY", "VOOL_OPENROUTER_API_KEY"),
        # GET /api/v1/key answers {"data": {"label", "limit", "limit_remaining", "usage",
        # "is_free_tier", ...}} (openrouter.ai API reference, current-key endpoint, read 2026-09-14).
        probe_response_kind="object",
        probe_account_fields={
            "label": ("data", "label"),
            "limit_remaining": ("data", "limit_remaining"),
            "is_free_tier": ("data", "is_free_tier"),
        },
        probe_exhausted_when_zero=("data", "limit_remaining"),
    ),
    "openai": ProviderConfig(
        provider_id="openai",
        label="OpenAI (GPT)",
        base_url="https://api.openai.com/v1",
        probe_path="/models",
        model_id_style="bare",
        model_catalog_source="static",
        default_model="gpt-4.1-mini",
        key_prefixes=("sk-proj-",),  # bare "sk-" is ambiguous, resolved in detect_provider
        env_names=("OPENAI_API_KEY", "VOOL_OPENAI_API_KEY"),
    ),
    "anthropic": ProviderConfig(
        provider_id="anthropic",
        label="Anthropic (Claude)",
        base_url="https://api.anthropic.com/v1",  # OpenAI-compatibility endpoint
        probe_path="/models",
        model_id_style="bare",
        model_catalog_source="static",
        default_model="claude-sonnet-4-5",
        key_prefixes=("sk-ant-",),
        env_names=("ANTHROPIC_API_KEY", "VOOL_ANTHROPIC_API_KEY"),
        extra_headers={"anthropic-version": "2023-06-01"},
        auth_header="X-Api-Key",
        model_list_protocol="anthropic_models_list",
    ),
    "groq": ProviderConfig(
        provider_id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        probe_path="/models",
        model_id_style="bare",
        model_catalog_source="static",
        default_model="llama-3.3-70b-versatile",
        key_prefixes=("gsk_",),
        env_names=("GROQ_API_KEY", "VOOL_GROQ_API_KEY"),
    ),
    "google": ProviderConfig(
        provider_id="google",
        label="Google (Gemini)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        probe_path="/models",  # returns ids prefixed "models/" — normalize before /chat/completions
        model_id_style="bare",
        model_catalog_source="static",
        default_model="gemini-2.5-flash",
        key_prefixes=("AIza",),
        env_names=("GEMINI_API_KEY", "GOOGLE_API_KEY", "VOOL_GEMINI_API_KEY"),
    ),
    "deepseek": ProviderConfig(
        provider_id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        probe_path="/models",
        model_id_style="bare",
        model_catalog_source="static",
        default_model="deepseek-chat",
        key_prefixes=(),  # bare "sk-" — ambiguous, never auto-selected alone
        env_names=("DEEPSEEK_API_KEY", "VOOL_DEEPSEEK_API_KEY"),
    ),
    "moonshot": ProviderConfig(
        provider_id="moonshot",
        label="Kimi / Moonshot",
        base_url="https://api.moonshot.ai/v1",
        probe_path="/models",
        model_id_style="bare",
        model_catalog_source="static",
        default_model="kimi-k2-0711-preview",
        key_prefixes=(),  # bare "sk-" — ambiguous
        env_names=("KIMI_API_KEY", "MOONSHOT_API_KEY", "VOOL_KIMI_API_KEY"),
    ),
    "usepod": ProviderConfig(
        provider_id="usepod",
        label="UsePod (inference marketplace)",
        # The ORIGIN only. The token is a URL path segment (/proxy/<token>/v1) and never lives in
        # this table, a manifest, or a URL built from it here -- see core.usepod.descriptor.
        base_url="https://api.usepod.ai",
        probe_path="/balance",  # under /proxy/<token>: an auth-gated read that spends nothing
        # UsePod rejects the urllib default client with 403 even for a valid token. Save, Test
        # and discovery share these headers through the provider verification descriptor.
        extra_headers={"User-Agent": "VOOL/0.5"},
        model_id_style="bare",
        model_catalog_source="usepod_marketplace",
        default_model="",  # none: a model is chosen from the live marketplace, with a price bound
        key_prefixes=(),  # a UUID token has no vendor prefix; never auto-detected from a key alone
        env_names=("VOOL_USEPOD_TOKEN",),
        auth_placement=AUTH_URL_PATH_TOKEN,
        credential_path_template="/proxy/{credential}",
        endpoint_slot=USEPOD_ORIGIN_SLOT,
        # GET /proxy/<token>/balance answers {"usdc_balance": <integer µUSDC>} (docs.usepod.ai
        # api/deposit-on-chain, read 2026-09-15). A genuine answer carries that field; a zero balance
        # is a valid token with no credit (exhausted), kept apart from the token's validity.
        probe_response_path=("usdc_balance",),
        probe_response_kind="value",
        probe_account_fields={"usdc_balance_microunits": ("usdc_balance",)},
        probe_exhausted_when_zero=("usdc_balance",),
    ),
    "custom": ProviderConfig(
        provider_id="custom",
        label="Custom (OpenAI-compatible)",
        base_url="",  # user-supplied; validated as HTTPS/loopback before a key is attached
        probe_path="/models",
        model_id_style="bare",
        model_catalog_source="discover",
        default_model="",
        key_prefixes=(),  # never auto-detected — the user picks "custom" explicitly
        env_names=("VOOL_CUSTOM_API_KEY",),
        base_url_env_names=("VOOL_CUSTOM_BASE_URL", "VOOL_REMOTE_BASE_URL"),
        user_base_url=True,
        endpoint_slot=CUSTOM_BASE_URL_SLOT,
    ),
}

# The bare "sk-" collision set, most-likely first (OpenAI is the common case).
_BARE_SK_CANDIDATES: tuple[str, ...] = ("openai", "deepseek", "moonshot")


def config_for(provider_id: str) -> ProviderConfig | None:
    return PROVIDERS.get(str(provider_id or "").strip().lower())


def slot_for(provider_id: str) -> str:
    cfg = config_for(provider_id)
    return cfg.credential_slot if cfg else ""


def all_slots() -> frozenset[str]:
    """Every valid credential slot — the closed whitelist the credentials endpoint accepts."""
    return frozenset(cfg.credential_slot for cfg in PROVIDERS.values())


def uses_url_path_token(provider_id: str) -> bool:
    """Whether this provider's credential travels in the URL path (and never in a header)."""
    cfg = config_for(provider_id)
    return bool(cfg and cfg.auth_placement == AUTH_URL_PATH_TOKEN)


def credential_env_for(provider_id: str) -> str:
    """The PRIMARY credential env alias for a provider, from this module's table.

    ``ProviderConfig.env_names`` is priority-ordered; the first entry is the
    canonical alias the broker path and any fast-command surface must both read.
    Adding or renaming an alias is therefore a ONE-file edit here — adapters and
    command surfaces derive their facts from this function / :func:`slot_for`
    instead of restating slot/env literals. Empty string for unknown providers.
    """
    cfg = config_for(provider_id)
    return cfg.env_names[0] if cfg and cfg.env_names else ""


def key_env_names(provider_id: str) -> tuple[str, ...]:
    """ALL accepted credential env aliases for a provider, priority-ordered
    (vendor-canonical first, compatibility aliases after) — from the ONE table.
    Every readiness / availability / transport consumer must scan this full
    tuple, never just :func:`credential_env_for`'s primary entry; a host keyed
    through a compatibility alias would otherwise be told "no key" by one
    surface while another escalates successfully. Empty tuple for unknown or
    alias-less providers.
    """
    cfg = config_for(provider_id)
    return cfg.env_names if cfg else ()


def env_key_present(provider_id: str) -> bool:
    """Whether ANY canonical/compatibility alias of ``provider_id`` holds a
    non-empty value in the environment. Presence only — never returns or logs
    the value."""
    import os

    for name in key_env_names(provider_id):
        if str(os.environ.get(name) or "").strip():
            return True
    return False


def provider_for_slot(slot: str) -> str:
    slot = str(slot or "").strip()
    for cfg in PROVIDERS.values():
        if cfg.credential_slot == slot:
            return cfg.provider_id
    return ""


def custom_base_url() -> str:
    import os

    for name in PROVIDERS["custom"].base_url_env_names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value.rstrip("/")
    try:
        from core import credential_store
    except Exception:
        return ""
    try:
        return str(credential_store.get_credential(CUSTOM_BASE_URL_SLOT) or "").strip().rstrip("/")
    except credential_store.CredentialReadError:
        # Raised only inside credential_store.strict_reads(): the pair reader must never read an
        # unreadable slot as "no endpoint" (revision-5 review R4). Every other caller stays lenient.
        raise
    except Exception:
        return ""


class StaleProviderBindingError(RuntimeError):
    """A dispatch lane's frozen destination no longer matches the committed credential binding.

    The lane's manifest captured its base URL at registration; the credential transaction has
    since committed a different pair. The CURRENT key must never travel to the lane's old
    destination, and a request authorized for one destination must not be silently redirected
    to another: the caller either re-resolves the lane through its route authority
    (re-registration against the committed pair) or refuses with this error. Never retried
    blindly inside the adapter — that would be the silent redirect."""


def _env_first(names: tuple[str, ...]) -> str:
    import os

    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def manifest_uses_committed_custom_pair(manifest: object) -> bool:
    """Whether a lane manifest's destination and credential are the custom provider's REPLACEABLE pair
    committed by the credential transaction — a lane the BYOK registrar minted for the custom provider
    (``custom-byok`` carrying the custom slot) — rather than a fixed vendor endpoint, an explicitly
    non-custom manifest credential or a local runner. Only such a lane's frozen destination can go
    stale. The ONE definition: the adapter's dispatch binding and the route authority's dispatch
    resolution both ask it, so they can never disagree about which lanes re-resolve."""
    if str(getattr(manifest, "provider_name", "") or "").strip().lower() != "custom-byok":
        return False
    runtime_config = getattr(manifest, "runtime_config", None) or {}
    try:
        credential_key = str(runtime_config.get("credential_key") or "").strip()
    except AttributeError:
        return False
    return credential_key == PROVIDERS["custom"].credential_slot


def resolved_custom_pair() -> tuple[str, str]:
    """The custom provider's (base_url, key) read as ONE fact: :func:`resolved_provider_pair` for the
    custom endpoint, kept under its own name for its existing readers."""
    return resolved_provider_pair("custom")


def resolved_provider_pair(provider_id: str) -> tuple[str, str]:
    """A paired provider's (endpoint, key) read as ONE fact, under the credential
    transaction lock the writers hold — and COHERENCE-ADMITTED (review F1/F3).

    A paired provider's key commits together with the endpoint it travels to
    (``ProviderConfig.endpoint_slot``): the custom endpoint's base URL, UsePod's origin.
    Verification, probing, promotion and dispatch commit the endpoint and the key together
    inside that lock (CredentialStore.save_verified); a reader that takes the two slots
    separately can observe key A beside endpoint B mid-commit. Readers that act on the pair
    (the connection probe, the completion adapter, UsePod's dispatch) use THIS.

    Admission, checked under the same lock before anything is returned:

    * an UNRESOLVED pair — a pending journal intent for either slot — refuses with
      ``StorageConflictError``. A transaction whose storage reply was lost, whose restoration
      failed, or whose write may still complete late is not a binding any request may use;
      reconcile (restart or on demand) decides it.
    * an INCOHERENT pair — live bytes that disagree with the committed binding row's recorded
      key digest or endpoint — refuses the same way. This is the permanent backstop against a
      bounded backend write completing LATE, after its transaction was decided.
    * a pair the ENVIRONMENT owns is returned as-is: that binding is the operator's explicit
      choice, not a store transaction. For the custom endpoint both halves come from the
      operator's env aliases; a UsePod token from the environment is bound to the provider
      table's origin, because the environment never re-points a credential-bearing request.

    Legacy states without a recorded endpoint (rows written before this contract, raw
    pre-intake saves) keep their historical behavior — the live pair is returned and the
    caller's own verification decides it."""
    cfg = config_for(provider_id)
    if cfg is None or not cfg.endpoint_slot:
        raise ValueError(f"{provider_id!r} is not a provider whose key commits with an endpoint")
    from core.credential_intelligence import binding as binding_module
    from core.credential_intelligence._state_lock import credential_state_lock
    from core.credential_intelligence.store import JOURNAL_FILE, StorageConflictError, StorageReadError

    pid = cfg.provider_id
    with credential_state_lock():
        # The ONE base/key resolvers (env override first, then the store), read together under the
        # writers' lock so no mid-commit pair is observable — and inside the backend's strict-read
        # scope, so an unreadable store half raises StorageReadError instead of reading as "no
        # endpoint" or "no key" (revision-5 review R4): an unknown state never admits a pair.
        from core import credential_store

        env_key = _env_first(cfg.env_names)
        try:
            with credential_store.strict_reads():
                if cfg.user_base_url:
                    base = custom_base_url()
                elif env_key:
                    base = cfg.base_url.rstrip("/")
                else:
                    base = _stored_endpoint(cfg)
                key = _slot_value(cfg.credential_slot, cfg.env_names)
        except credential_store.CredentialReadError as exc:
            raise StorageReadError(
                f"the {pid} credential pair could not be read ({exc}); an unreadable slot is not an empty "
                "one — nothing was sent"
            ) from exc
        endpoint_from_env = bool(_env_first(cfg.base_url_env_names)) if cfg.base_url_env_names else True
        if env_key and endpoint_from_env:
            # The operator pinned the binding in the environment: an explicit binding, not a
            # store transaction — never second-guessed here.
            return base, key
        # The store owns at least one half: the pair is only readable as a DECIDED,
        # COHERENT transaction. Both gates run under the writers' lock.
        from core.runtime_paths import active_data_dir

        journal_path = active_data_dir() / JOURNAL_FILE
        if journal_path.exists():
            import json

            try:
                journal_rows = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise StorageConflictError(
                    f"the {pid} credential pair is unreadable (intent journal damaged); refusing "
                    "to read the pair — inspect the journal or run reconcile"
                ) from exc
            watched = {cfg.endpoint_slot, cfg.credential_slot}
            for row in journal_rows if isinstance(journal_rows, list) else []:
                if (
                    isinstance(row, dict)
                    and row.get("phase") in ("write_pending", "delete_pending")
                    and str(row.get("slot") or "") in watched
                ):
                    raise StorageConflictError(
                        f"the {pid} endpoint/key pair has an unresolved operation (a write or "
                        "delete whose completion is unknown); no request may use it until "
                        "reconcile (restart or on demand) decides it"
                    )
        row = binding_module.load_index().get(pid) if _index_exists() else None
        if (
            isinstance(row, dict)
            and str(row.get("endpoint") or "")
            and row.get("status") in (binding_module.STATUS_VERIFIED, binding_module.STATUS_UNVERIFIED)
        ):
            digest = _pair_digest(key)
            endpoint_committed = str(row.get("endpoint") or "").strip().rstrip("/")
            endpoint_live = (base or "").strip().rstrip("/")
            if digest != str(row.get("key_digest") or "") or endpoint_live != endpoint_committed:
                raise StorageConflictError(
                    f"the {pid} endpoint/key pair does not match its committed binding (a late or "
                    "partial write); refusing to use it — re-save the pair or run reconcile"
                )
        return base, key


def _stored_endpoint(cfg: ProviderConfig) -> str:
    """The endpoint committed beside a paired provider's key (its ``endpoint_slot``), else the table's
    documented ``base_url``. Inside ``credential_store.strict_reads()`` an unreadable slot raises
    instead of reading as the default; every other caller stays lenient."""
    try:
        from core import credential_store
    except Exception:
        return cfg.base_url.rstrip("/")
    try:
        stored = str(credential_store.get_credential(cfg.endpoint_slot) or "").strip().rstrip("/")
    except credential_store.CredentialReadError:
        raise  # only inside credential_store.strict_reads(): unreadable is not the default (review R4)
    except Exception:
        stored = ""
    return stored or cfg.base_url.rstrip("/")


def _index_exists() -> bool:
    from core.credential_intelligence import binding as binding_module

    try:
        return binding_module.index_path().exists()
    except Exception:
        return False


def _pair_digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _slot_value(slot: str, env_names: tuple[str, ...] | None = None) -> str:
    """A key slot's value: the provider's env aliases first (the custom provider's when none are
    named), then the store. Inside ``credential_store.strict_reads()`` an unreadable slot raises."""
    import os

    for name in PROVIDERS["custom"].env_names if env_names is None else env_names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    try:
        from core import credential_store
    except Exception:
        return ""
    try:
        return str(credential_store.get_credential(slot) or "").strip()
    except credential_store.CredentialReadError:
        raise  # only inside credential_store.strict_reads(): unreadable is not empty (review R4)
    except Exception:
        return ""


def usepod_origin() -> str:
    """The origin committed beside the UsePod token, else the documented origin."""
    return _stored_endpoint(PROVIDERS["usepod"])


def resolved_base_url(provider_id: str) -> str:
    """The effective base URL for a provider — the custom endpoint's is resolved from env/store.

    For a path-token provider this is the ORIGIN, never a credential-bearing URL.
    """
    cfg = config_for(provider_id)
    if cfg is None:
        return ""
    if provider_id == "custom":
        return custom_base_url()
    if provider_id == "usepod":
        return usepod_origin()
    return cfg.base_url.rstrip("/")


def _is_loopback_hostname(host: str) -> bool:
    """True only for a genuine loopback host: the literal name ``localhost`` or an IP that is
    actually in a loopback range (127.0.0.0/8, ::1). A name that merely *starts with* ``127.`` or
    ``localhost`` (e.g. ``127.0.0.1.evil.com``) is NOT loopback — that is the bypass a raw string
    prefix check permits."""
    import ipaddress

    h = str(host or "").strip().lower()
    if not h:
        return False
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def is_safe_key_transport(base_url: str) -> bool:
    """Whether a Bearer API key may be sent to ``base_url`` without cleartext exposure.

    Safe = HTTPS to any host, or plain HTTP only to a genuine loopback host (a local model
    server). The host is taken from the PARSED url (so userinfo/port are stripped) and loopback
    is decided by real IP membership, never a string prefix — ``http://127.0.0.1.evil.com`` and
    ``http://localhost.evil.com`` are correctly rejected. This is the ONE canonical transport
    gate: the credentials endpoint, the connection probe, and the completion adapter all defer
    here so no path is a weaker key-exfiltration route than another.
    """
    from urllib.parse import urlparse

    parsed = urlparse(str(base_url or "").strip())
    scheme = (parsed.scheme or "").lower()
    if scheme == "https":
        return True
    return scheme == "http" and _is_loopback_hostname(parsed.hostname or "")


def detect_provider(key: str) -> ProviderGuess:
    """Map a pasted key to a provider by prefix.

    High-confidence when the prefix is unique (sk-or-/sk-ant-/sk-proj-/gsk_/AIza). A bare ``sk-…``
    (not one of the specific prefixes) is LOW confidence — OpenAI, DeepSeek and Moonshot all use
    it — so the caller must ask which provider and NEVER auto-store it under a guess.
    """
    k = str(key or "").strip()
    if not k:
        return ProviderGuess(None, "none")
    # Specific prefixes first (sk-or-/sk-ant-/sk-proj- must beat the bare "sk-").
    for cfg in PROVIDERS.values():
        for prefix in cfg.key_prefixes:
            if k.startswith(prefix):
                return ProviderGuess(cfg.provider_id, "high")
    if k.startswith("sk-"):
        return ProviderGuess("openai", "low", _BARE_SK_CANDIDATES)
    return ProviderGuess(None, "none")


def active_provider(policy_provider: str = "", has_key=None) -> str:
    """Resolve the single active cloud provider (v1 = one at a time).

    Order: an explicit policy provider → else the only slot that currently holds a key → else
    openrouter when its slot is keyed (legacy default) → else "". ``has_key`` is a callable
    ``slot -> bool`` (defaults to the credential store) so this stays testable and import-light.
    """
    explicit = str(policy_provider or "").strip().lower()
    if explicit in PROVIDERS:
        return explicit
    if has_key is None:
        try:
            from core import credential_store

            has_key = credential_store.has_credential
        except Exception:
            return ""
    keyed = [pid for pid, cfg in PROVIDERS.items() if _safe_has(has_key, cfg.credential_slot)]
    if len(keyed) == 1:
        return keyed[0]
    if "openrouter" in keyed:
        return "openrouter"  # legacy default when several (or ambiguous) keys exist
    return keyed[0] if keyed else ""


def _safe_has(has_key, slot: str) -> bool:
    try:
        return bool(has_key(slot))
    except Exception:
        return False


__all__ = [
    "AUTH_BEARER",
    "AUTH_URL_PATH_TOKEN",
    "PROVIDERS",
    "USEPOD_ORIGIN_SLOT",
    "Confidence",
    "ProviderConfig",
    "ProviderGuess",
    "StaleProviderBindingError",
    "active_provider",
    "all_slots",
    "config_for",
    "custom_base_url",
    "detect_provider",
    "env_key_present",
    "is_safe_key_transport",
    "key_env_names",
    "provider_for_slot",
    "resolved_base_url",
    "resolved_custom_pair",
    "slot_for",
    "usepod_origin",
    "uses_url_path_token",
]
