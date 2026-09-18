"""What a given lane's model can actually take in and give back, resolved offline.

The output budget is currently one table keyed on output_mode alone
(``core.prompt_normalizer._max_output_tokens``): 240 tokens for plain_text, 220 for a summary
block, 700 for a tool intent. It takes one argument, so a 550B free cloud model and qwen3:0.6b
get the identical 240 -- and a reasoning model spent all 240 of them thinking and returned an
EMPTY completion, which the user saw as "I couldn't get a live model response".

The real numbers already exist and go unread: OpenRouter publishes ``context_length`` and
``top_provider.max_completion_tokens`` per model, while every cloud manifest in
``core.runtime_provider_defaults`` hardcodes ``metadata["context_window"] = 128000`` as a literal
in five places. This module is the read side of that gap: one lookup, no network, no guessing.

ZERO MEANS UNKNOWN, everywhere in here, and never "no output allowed" -- the same rule
``core.openrouter_catalog._price`` follows for an unpublished price. A caller that clamps a budget
to a 0 it read as a cap reproduces the empty-completion failure exactly. Use the
``*_is_known`` properties rather than testing the ints.

``source`` is load-bearing: when a budget later comes out wrong, it says whether the numbers came
from a live catalog, a cache that has not been refreshed in weeks, the curated list, or nowhere.
"""
from __future__ import annotations

from dataclasses import dataclass

# One freshness truth. Duplicating the 3600 here would let this module call a cache "live" that
# the catalog itself considers expired.
from core.openrouter_catalog import _CACHE_SECONDS as _CATALOG_FRESH_SECONDS

SOURCE_CATALOG_LIVE = "catalog_live"
SOURCE_CATALOG_STALE = "catalog_stale"
SOURCE_CURATED = "curated"
SOURCE_DISCOVERY = "discovery_observed"
SOURCE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelCapability:
    """A model's context window and single-completion cap, with where they were read from.

    Either integer may be 0, which means the number was never published for this model -- not
    that the model accepts no input or emits no output.
    """

    context_window: int
    max_output_tokens: int
    source: str

    @property
    def context_window_is_known(self) -> bool:
        return self.context_window > 0

    @property
    def max_output_tokens_is_known(self) -> bool:
        return self.max_output_tokens > 0


_UNKNOWN = ModelCapability(context_window=0, max_output_tokens=0, source=SOURCE_UNKNOWN)

# A lane whose provider_name resolves to one of the direct providers in core.cloud_providers is
# looked up in the curated list. Two lanes need naming help:
#
#   kimi-remote -> moonshot: the registrar's default base URL is "https://api.moonshot.ai/v1",
#   byte-identical to cloud_providers' moonshot entry, so its model ids come from that catalog.
#
# Deliberately absent: tether-remote and openai-compatible-remote. Both are pointed at whatever
# endpoint the user configures (TETHER_BASE_URL is required, VOOL_REMOTE_BASE_URL overrides the
# default), so the lane name is no evidence of whose model ids these are. Guessing "openai"
# because that is the default base URL would attach OpenAI's 1M-token window to a model served by
# something else entirely. They resolve to unknown, and that is the correct answer.
_LANE_PROVIDER_ALIASES = {"kimi-remote": "moonshot"}

_BYOK_SUFFIX = "-byok"


def capability_for(provider_name: str, model_name: str) -> ModelCapability:
    """The published limits for ``model_name`` on ``provider_name``, or unknown.

    Never reaches the network and never raises: this is meant to be safe on a boot path and on
    the per-turn path, where a blocking catalog fetch would cost the user a visible stall.
    """
    lane = str(provider_name or "").strip().lower()
    model_id = str(model_name or "").strip()
    if not model_id:
        return _UNKNOWN
    if _is_openrouter_lane(lane):
        return _from_openrouter_catalog(model_id)
    provider_id = _direct_provider_id(lane)
    if provider_id:
        from_discovery = _from_discovery(provider_id, model_id)
        if from_discovery is not None:
            return from_discovery
        return _from_curated_list(provider_id, model_id)
    return _UNKNOWN


def _is_openrouter_lane(lane: str) -> bool:
    return _strip_byok(lane) == "openrouter"


def _strip_byok(lane: str) -> str:
    return lane[: -len(_BYOK_SUFFIX)] if lane.endswith(_BYOK_SUFFIX) else lane


def _direct_provider_id(lane: str) -> str:
    """The core.cloud_providers id a lane draws its model ids from, or "" when nothing says.

    Local lanes (ollama-local, llamacpp-local, mlx-local, vllm-local) land here and return "":
    their context window is a property of how the weights were loaded, not of any catalog, so
    this module has nothing to say about them.
    """
    from core.cloud_providers import config_for

    candidate = _LANE_PROVIDER_ALIASES.get(lane) or _strip_byok(lane)
    return candidate if config_for(candidate) is not None else ""


def _from_openrouter_catalog(model_id: str) -> ModelCapability:
    from core.openrouter_catalog import has_cached_catalog, safe_all_models

    # The guard is what keeps this offline. safe_all_models(allow_network=False) is cache-only
    # only while a cache exists; with none it falls through to a live refresh and swallows the
    # failure, so an unguarded call on a first boot pays a 15s timeout to learn nothing.
    if not has_cached_catalog():
        return _UNKNOWN
    models, age_seconds = safe_all_models(allow_network=False)
    if not models:
        return _UNKNOWN
    wanted = model_id.lower()
    match = next((model for model in models if model.model_id.strip().lower() == wanted), None)
    if match is None:
        # An id the catalog does not carry is unknown. Falling back to a neighbouring row -- the
        # biggest window, the first entry -- would hand out a number belonging to another model.
        return _UNKNOWN
    return ModelCapability(
        context_window=max(0, int(match.context_length or 0)),
        max_output_tokens=max(0, int(match.max_output_tokens or 0)),
        source=_catalog_source(age_seconds),
    )


def _catalog_source(age_seconds: float | None) -> str:
    """Live only when the cache is provably within the catalog's own freshness window.

    An unreadable age is reported stale: this module cannot prove the numbers are current, and
    the whole point of the field is that a later wrong budget is traceable to old data.
    """
    if age_seconds is None:
        return SOURCE_CATALOG_STALE
    return SOURCE_CATALOG_LIVE if age_seconds <= _CATALOG_FRESH_SECONDS else SOURCE_CATALOG_STALE


def _from_discovery(provider_id: str, model_id: str) -> ModelCapability | None:
    """This provider's own OBSERVED discovery evidence, when it is worth acting on: a complete
    (untruncated) listing, observed under the CURRENT key/endpoint generation and unexpired.
    The credential-intelligence discovery owner decides all of that offline
    (``observed_capability``); None means there is nothing usable and the next source speaks.
    Unpublished numbers stay 0 (unknown) -- never guessed here either."""
    from core.credential_intelligence.discovery import observed_capability

    try:
        observed = observed_capability(provider_id, model_id)
    except Exception:
        return None
    if observed is None:
        return None
    context_window, max_output_tokens = observed
    return ModelCapability(
        context_window=max(0, int(context_window or 0)),
        max_output_tokens=max(0, int(max_output_tokens or 0)),
        source=SOURCE_DISCOVERY,
    )


def _from_curated_list(provider_id: str, model_id: str) -> ModelCapability:
    """The curated row's context window; the output cap stays unknown because none is published.

    core.cloud_model_catalog rows carry context_length and price only. Filling max_output_tokens
    with a plausible 4096 would be this module inventing the exact number it exists to stop the
    runtime from inventing.
    """
    from core.cloud_model_catalog import curated_models

    wanted = model_id.lower()
    for row in curated_models(provider_id):
        if str(row.get("id") or "").strip().lower() == wanted:
            return ModelCapability(
                context_window=max(0, int(row.get("context_length") or 0)),
                max_output_tokens=0,
                source=SOURCE_CURATED,
            )
    return _UNKNOWN


__all__ = [
    "SOURCE_CATALOG_LIVE",
    "SOURCE_CATALOG_STALE",
    "SOURCE_CURATED",
    "SOURCE_DISCOVERY",
    "SOURCE_UNKNOWN",
    "ModelCapability",
    "capability_for",
]
