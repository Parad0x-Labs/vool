from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.remote_fetch_policy import open_remote_url
from core.runtime_paths import active_data_dir

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_SECONDS = 3600


@dataclass(frozen=True)
class OpenRouterModel:
    model_id: str
    name: str
    context_length: int
    # None means the provider published no usable price for this field. It is not zero. The
    # per-token pair must be published for a model to be readable at all; request_usd is a
    # surcharge OpenRouter publishes for no model, so absent there means no surcharge.
    prompt_usd_per_token: float | None
    completion_usd_per_token: float | None
    request_usd: float | None
    supported_parameters: tuple[str, ...]
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    fetched_at: str
    # The provider's published ceiling on a single completion. 0 means UNPUBLISHED, never "no
    # output allowed" -- OpenRouter omits top_provider.max_completion_tokens for many rows, and a
    # budget picker that read the absent value as a cap would ask for zero tokens and get an empty
    # reply, which is the failure this field exists to end. Last field with a default so the
    # keyword constructions that predate it keep building.
    max_output_tokens: int = 0

    def estimate_cost(self, *, input_tokens: int, output_tokens: int, retries: int = 0) -> float | None:
        """The estimated spend, or None when a price this depends on was never published.

        Returning 0.0 for unpublished pricing would rank an unknown-cost model as the cheapest
        candidate available, which is the opposite of what not knowing the price should mean.
        """
        if None in (self.prompt_usd_per_token, self.completion_usd_per_token):
            return None
        per_attempt = (
            max(0, input_tokens) * float(self.prompt_usd_per_token or 0.0)
            + max(0, output_tokens) * float(self.completion_usd_per_token or 0.0)
            + float(self.request_usd or 0.0)
        )
        return round(per_attempt * (1 + max(0, retries)), 8)


def refresh_openrouter_catalog(*, api_key: str = "", timeout_seconds: float = 15.0) -> tuple[OpenRouterModel, ...]:
    # The OWNING ENTRY POINT of the catalog refresh's network work (R2b1,
    # amended): the door grants nothing, so the refresh authorizes itself here,
    # by name. Scope-local account — handed to no one at close.
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("model_catalog.refresh"):
        return _refresh_openrouter_catalog_owned(
            api_key=api_key,
            timeout_seconds=timeout_seconds,
        )


def _refresh_openrouter_catalog_owned(
    *,
    api_key: str = "",
    timeout_seconds: float = 15.0,
) -> tuple[OpenRouterModel, ...]:
    from core.runtime_provider_defaults import apply_openrouter_attribution_headers

    headers = apply_openrouter_attribution_headers({"Accept": "application/json"})
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # The ONE outbound door: the catalog GET obeys the per-turn veto and reports
    # itself; a non-200 raises (requests' raise_for_status semantics preserved).
    response = open_remote_url(_MODELS_URL, headers=headers, timeout=timeout_seconds)
    if response.status >= 400:
        raise RuntimeError(f"openrouter catalog refresh failed: HTTP {response.status}")
    payload = json.loads(response.read().decode("utf-8"))
    fetched_at = datetime.now(timezone.utc).isoformat()
    models = parse_openrouter_catalog(payload, fetched_at=fetched_at)
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps({"fetched_at": fetched_at, "payload": payload}, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)
    return models


def load_openrouter_catalog(
    *, max_age_seconds: int = _CACHE_SECONDS, cache_only: bool = False
) -> tuple[OpenRouterModel, ...]:
    """The cached catalog, refreshing over the network when it is missing or stale.

    ``cache_only`` reads the disk cache and returns ``()`` without any network work — the
    semantic ``safe_all_models(allow_network=False)`` always intended. Without it, an absent or
    unreadable cache fell through to ``refresh_openrouter_catalog()``: a BLOCKING, keyless,
    15-second-timeout GET issued from inside read paths that must not reach the network at all
    (measured and documented on ``has_cached_catalog``; the UI's model-list GET stalled on the
    first open after a key was connected, and a vetoed or offline refresh then served
    ``models: []`` with no distinction from "provider has no models").
    """
    path = _cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = str(data["fetched_at"])
        age = time.time() - datetime.fromisoformat(fetched_at).timestamp()
        if age <= max(0, max_age_seconds):
            return parse_openrouter_catalog(data["payload"], fetched_at=fetched_at)
    except Exception:
        pass
    if cache_only:
        return ()
    return refresh_openrouter_catalog()


def parse_openrouter_catalog(payload: dict[str, Any], *, fetched_at: str) -> tuple[OpenRouterModel, ...]:
    models: list[OpenRouterModel] = []
    for item in list(payload.get("data") or []):
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        pricing = dict(item.get("pricing") or {})
        architecture = dict(item.get("architecture") or {})
        # `or {}` rather than dict.get's default: a payload carrying an explicit `"top_provider":
        # null` would raise on .get() and, because safe_all_models swallows the parse, drop the
        # ENTIRE catalog rather than one row. Same value for every payload the cloud adapter reads.
        top_provider = dict(item.get("top_provider") or {})
        models.append(
            OpenRouterModel(
                model_id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                context_length=max(0, int(item.get("context_length") or 0)),
                prompt_usd_per_token=_price(pricing.get("prompt")),
                completion_usd_per_token=_price(pricing.get("completion")),
                request_usd=_price(pricing.get("request")),
                supported_parameters=tuple(str(value) for value in list(item.get("supported_parameters") or [])),
                input_modalities=tuple(str(value) for value in list(architecture.get("input_modalities") or [])),
                output_modalities=tuple(str(value) for value in list(architecture.get("output_modalities") or [])),
                fetched_at=fetched_at,
                max_output_tokens=_output_cap(top_provider.get("max_completion_tokens")),
            )
        )
    return tuple(models)


def recommend_openrouter_model(
    models: tuple[OpenRouterModel, ...],
    *,
    required_context: int,
    required_parameters: tuple[str, ...],
    input_tokens: int,
    output_tokens: int,
    acceptance_probability: dict[str, float] | None = None,
    allowlist: tuple[str, ...] = (),
) -> OpenRouterModel | None:
    allowed = set(allowlist)
    required = set(required_parameters)
    candidates = [
        model for model in models
        if model.context_length >= required_context
        and required.issubset(set(model.supported_parameters))
        and (not allowed or model.model_id in allowed)
    ]
    # A model whose price was never published cannot be compared on cost, and treating its
    # unknown cost as zero would rank it first precisely because nobody knows what it charges.
    candidates = [model for model in candidates if model.estimate_cost(input_tokens=1, output_tokens=1) is not None]
    if not candidates:
        return None
    probabilities = acceptance_probability or {}

    def _cost(model: OpenRouterModel) -> float:
        return float(model.estimate_cost(input_tokens=input_tokens, output_tokens=output_tokens) or 0.0)

    return max(
        candidates,
        key=lambda model: (
            max(0.01, min(1.0, float(probabilities.get(model.model_id, 0.5)))) / max(_cost(model), 1e-9),
            -_cost(model),
        ),
    )


def _output_cap(value: Any) -> int:
    """A published output cap, or 0 meaning "not published" -- never an exception.

    The provider controls this payload and does not keep to one type. Measured against real and
    plausible rows, a bare `int(value)` raises on `"16384.0"`, `"unlimited"`, `[4096]` and
    `{"value": 4096}`. That exception does not stay local: `parse_openrouter_catalog` is called
    inside `safe_all_models`, which swallows it and returns an EMPTY catalog -- so one malformed row
    would take down free-model picking, pricing, the usage meter and escalation for every caller,
    and send them to a blocking network refresh. `_price` below is the same defence for the same
    reason; this is that idiom, applied to the field next to it.

    A float-shaped string is accepted and truncated, because "16384.0" is a published cap written
    carelessly, not an absent one.
    """
    if value is None or isinstance(value, bool):
        return 0
    try:
        # OverflowError is not optional here: `int(float("inf"))` raises it, and a JSON `Infinity`
        # survives Python's decoder by default. NaN raises ValueError. Both are shapes a provider
        # can put on the wire, and either one uncaught costs the whole catalog.
        cap = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, cap)


def _price(value: Any) -> float | None:
    """A published price, or None when the provider did not publish a usable one.

    Coercing a missing or malformed price to 0.0 makes "no price published" indistinguishable
    from "free": a catalog entry with no pricing block, an empty one, nulls, or a non-numeric
    string all read as $0 and are then offered -- and auto-switched to -- as free. The provider
    controls that payload, so the coercion is what turns a charge into a silent one.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    # A negative price is not a discount to clamp to zero -- it is a value this parser does not
    # understand, and clamping it would resolve the one malformed case toward "free".
    return price if price >= 0.0 else None


def _cache_path() -> Path:
    return (active_data_dir() / "openrouter_models_cache.json").resolve()


def has_cached_catalog() -> bool:
    """Whether any catalog cache exists on disk, fresh or stale.

    Measured: ``safe_all_models(allow_network=False)`` still issues one blocking GET to
    openrouter.ai when the cache file is ABSENT -- load_openrouter_catalog's except-branch calls
    refresh_openrouter_catalog() unconditionally -- and then swallows the failure into ``((),
    None)``. Callers that must not reach the network at all, such as a per-turn budget lookup,
    check this first and treat False as "no catalog" rather than paying a 15s timeout for it.
    """
    try:
        return _cache_path().is_file()
    except Exception:
        return False


_CACHED_ROW_MEMO: dict[str, OpenRouterModel | None] = {}


def cached_catalog_row(model_id: str) -> OpenRouterModel | None:
    """The cached catalog row for one model, or None. Never touches the network.

    The adapter's per-call budget decisions and the byok manifest's registration-time facts both
    need what the catalog already published about a model (its `supported_parameters`, its real
    context length, its completion cap). Reading it must never block a turn on a refresh, so this
    is cache-only: no cache file, or the model absent from it, returns None — an unknown model,
    not an error, and not a 15-second timeout. Memoized per model for the process because the
    cache file does not change under a running turn loop.
    """
    key = str(model_id or "").strip()
    if not key:
        return None
    if key in _CACHED_ROW_MEMO:
        return _CACHED_ROW_MEMO[key]
    row: OpenRouterModel | None = None
    if has_cached_catalog():
        try:
            for model in load_openrouter_catalog(cache_only=True):
                if model.model_id == key:
                    row = model
                    break
        except Exception:
            row = None
    _CACHED_ROW_MEMO[key] = row
    return row


# --- free-model surface (chat commands + the auto lane picker) -----------------------------
#
# A model is free exactly when its published per-token prices are 0 — determined from the LIVE
# catalog, never from a hardcoded list, so the free set follows OpenRouter as it changes.

_CODING_RE = re.compile(r"coder|codestral|devstral|[-/_.]code\b|\bcode[-/_.]", re.IGNORECASE)
# Real, free, but wrong as a conversational default: moderation/safety/embedding/rerank rows.
_UTILITY_RE = re.compile(r"safety|guard(?:rail)?|moderat|embed|rerank", re.IGNORECASE)
# Families with a track record get a ranking nudge so "best free" is not just "biggest context".
_KNOWN_FAMILY_RE = re.compile(r"deepseek|qwen|llama|gemma|nemotron|glm|kimi|mistral", re.IGNORECASE)


def model_pricing_is_known(model: OpenRouterModel) -> bool:
    """Whether the prices this model actually charges on were published.

    Per-token prompt and completion rates are the load-bearing pair: OpenRouter publishes both
    for every model in the live catalog (338 of 338 when this was measured), so a model missing
    either is one whose cost genuinely cannot be read, which is the case this guards.

    A per-request fee is optional and OpenRouter publishes it for no model at all, so demanding
    it classes the entire catalog as unknown -- measured: 0 of 338 models readable, including all
    14 whose ids end in ":free". Absent means no surcharge, and it is only read that way once the
    per-token pair is present, so an entry with no pricing block at all is still unknown.
    """
    return None not in (model.prompt_usd_per_token, model.completion_usd_per_token)


def _request_fee(model: OpenRouterModel) -> float:
    return float(model.request_usd or 0.0)


def model_is_free(model: OpenRouterModel) -> bool:
    """True for a model that costs nothing: OpenRouter's explicit ``:free`` variant, or one whose
    published prices are all an explicit zero.

    The ``:free`` id suffix is OpenRouter's own guaranteed-free tag, so it counts as free even when the
    catalog leaves that variant's per-token price unpublished (many ``:free`` models ship with null
    prices). For everything else, unpublished pricing stays unknown -> paid, so the escalation gate
    keeps deciding it rather than spending the user's key at a rate nobody has read.
    """
    from core.cloud_provider_contract import text_only_output

    # One reading of "a text model" for the whole cloud lane: a row that also emits audio or
    # images is not a free TEXT model, however its per-token prices read.
    if not text_only_output(model.output_modalities):
        return False
    if str(model.model_id or "").strip().lower().endswith(":free"):
        return True
    if not model_pricing_is_known(model):
        return False
    return (
        model.prompt_usd_per_token == 0.0
        and model.completion_usd_per_token == 0.0
        and _request_fee(model) == 0.0
    )


def model_is_coding(model: OpenRouterModel) -> bool:
    return bool(_CODING_RE.search(f"{model.model_id} {model.name}"))


def _free_rank(model: OpenRouterModel) -> tuple[int, int]:
    return (1 if _KNOWN_FAMILY_RE.search(model.model_id) else 0, model.context_length)


def safe_all_models(*, allow_network: bool = True) -> tuple[tuple[OpenRouterModel, ...], float | None]:
    """The whole catalog with its cache age (None = no data at all).

    Never raises and never invents: network failure degrades to the stale cache; no cache at all
    returns an empty tuple so the caller can say "could not check" instead of guessing.
    """
    models: tuple[OpenRouterModel, ...] = ()
    age: float | None = None
    try:
        if allow_network:
            models = load_openrouter_catalog()  # fresh-or-refresh (raises when offline w/o cache)
        else:
            # Cache-only means CACHE-ONLY: no cache file is "no data", answered instantly, never a
            # blocking network refresh smuggled through the unreadable-cache except-branch.
            models = load_openrouter_catalog(max_age_seconds=10**9, cache_only=True)
    except Exception:
        try:  # stale cache beats nothing
            data = json.loads(_cache_path().read_text(encoding="utf-8"))
            models = parse_openrouter_catalog(data["payload"], fetched_at=str(data["fetched_at"]))
        except Exception:
            return (), None
    if models:
        try:
            age = max(0.0, time.time() - datetime.fromisoformat(models[0].fetched_at).timestamp())
        except Exception:
            age = None
    return models, age


def safe_free_models(*, allow_network: bool = True) -> tuple[tuple[OpenRouterModel, ...], float | None]:
    """The live free list, best-first, with the cache age in seconds (None = no data at all)."""
    models, age = safe_all_models(allow_network=allow_network)
    free = tuple(sorted((m for m in models if model_is_free(m)), key=_free_rank, reverse=True))
    return free, age


def pick_auto_free_models(*, allow_network: bool = True) -> dict[str, str]:
    """The current best free model per lane: {"general": id, "coding": id}; lanes may be absent."""
    free, _age = safe_free_models(allow_network=allow_network)
    picks: dict[str, str] = {}
    general = [m for m in free if not model_is_coding(m) and not _UTILITY_RE.search(f"{m.model_id} {m.name}")]
    if general:
        picks["general"] = general[0].model_id
    coding = [m for m in free if model_is_coding(m)]
    if coding:
        picks["coding"] = coding[0].model_id
    return picks


__all__ = [
    "OpenRouterModel",
    "has_cached_catalog",
    "load_openrouter_catalog",
    "model_is_coding",
    "model_is_free",
    "model_pricing_is_known",
    "parse_openrouter_catalog",
    "pick_auto_free_models",
    "recommend_openrouter_model",
    "refresh_openrouter_catalog",
    "safe_all_models",
    "safe_free_models",
]
