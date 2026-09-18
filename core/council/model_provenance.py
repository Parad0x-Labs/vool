"""Model identity, family resolution and the independence unit.

A council seat's model is replaceable; its FAMILY is the independence unit. Two seats
running aliases of one model — a dated snapshot (``gpt-4o-2024-05-13``), a
provider-prefixed route (``openai/gpt-4o``), a quantized or ``:free`` variant — are
one model wearing two name tags, and any quorum that counts them as two reviewers
has fabricated the independence it was built to have.

Family resolution is a pure string computation over a CLOSED set of normalizations:

* case-folded, whitespace-stripped;
* routing prefixes (``openai/``, ``meta-llama/``, ``@cf/``, …) identify the provider
  and are stripped from the stem;
* routing suffixes (``:free``, ``:nitro``, ``-latest``, ``-stable``, …) are dropped;
* dated snapshot suffixes (``-2024-05-13``, ``-20240620``) are dropped;
* decimal versions are normalized (``claude-3.5-sonnet`` ≡ ``claude-3-5-sonnet``).

What is deliberately NOT collapsed: different stems are different families
(``gpt-4o`` vs ``gpt-4o-mini``, ``claude-3-5-sonnet`` vs ``claude-3-5-haiku``). A
smaller sibling model is a different model, not an alias; conflating them would
deny real (if partial) diversity rather than protect against fake diversity.

The family carries the CANONICAL provider (``openai/gpt-4o``), inferred for bare
names from a prefix table, so "who trained this" survives a route that hid it.

Provenance records keep REQUESTED and ACTUAL identities as two separate facts, the
same law ``core/council/dispatch.py`` enforces on the live stream: what a request
named is not evidence of what answered, and an unproven actual stays ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Union


class ModelProvenanceError(ValueError):
    """A model identity could not be stated honestly."""


#: Routing prefixes that may appear before the model stem. Longest-first matching is
#: applied by the resolver so ``meta-llama/`` wins over ``llama`` inference.
_PROVIDER_PREFIXES: tuple[str, ...] = (
    "openai/",
    "anthropic/",
    "google/",
    "meta-llama/",
    "mistralai/",
    "mistral/",
    "qwen/",
    "deepseek/",
    "xai/",
    "grok/",
    "openrouter/",
    "groq/",
    "together/",
    "fireworks/",
    "cloudflare/",
    "@cf/",
    "octoai/",
    "perplexity/",
    "cohere/",
)

_PROVIDER_ALIASES: dict[str, str] = {
    "mistralai": "mistral",
    "grok": "xai",
    "google": "google",
}

#: Bare-stem prefixes that identify the canonical provider when no routing prefix
#: was present. First match wins; the keys are lowercase stems.
_BARE_PROVIDER_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("chatgpt", "openai"),
    ("claude-", "anthropic"),
    ("gemini", "google"),
    ("gemma", "google"),
    ("llama", "meta-llama"),
    ("codellama", "meta-llama"),
    ("mixtral", "mistral"),
    ("mistral-", "mistral"),
    ("qwen", "qwen"),
    ("deepseek", "deepseek"),
    ("grok", "xai"),
)

#: Route suffixes that describe WHERE or HOW a model is served, not WHICH model it
#: is. Dropped before family comparison.
_ROUTE_SUFFIXES: tuple[str, ...] = (
    ":free",
    ":beta",
    ":extended",
    ":nitro",
    ":floor",
    ":online",
    ":thinking",
    "-latest",
    "-stable",
    "-preview",
)

_DATED_SUFFIX = re.compile(r"-(20\d{2})[-_]?(0[1-9]|1[0-2])[-_]?([0-3]\d)(\d{0,4})$")
_COMPACT_DATE_SUFFIX = re.compile(r"-20\d{6}$")
_DECIMAL_VERSION = re.compile(r"(\d)\.(\d)")


def infer_provider(model_id: str) -> str:
    """The canonical provider for a model id, from its prefix when it has one."""
    text = str(model_id or "").strip().lower()
    if not text:
        raise ModelProvenanceError("a model id must be non-empty")
    for prefix in sorted(_PROVIDER_PREFIXES, key=len, reverse=True):
        if text.startswith(prefix):
            provider = prefix.rstrip("/@")
            return _PROVIDER_ALIASES.get(provider, provider)
    for stem, provider in _BARE_PROVIDER_BY_PREFIX:
        if text.startswith(stem):
            return provider
    return "unknown"


def resolve_family(model_id: str) -> str:
    """The canonical family of a model id: ``provider/stem`` with aliases collapsed.

    Deterministic and total over non-empty strings: every two ids that name the same
    underlying model resolve to the same family, and the refusal set is exactly the
    empty/whitespace string.
    """
    text = str(model_id or "").strip().lower()
    if not text:
        raise ModelProvenanceError("a model id must be non-empty")

    provider = "unknown"
    for prefix in sorted(_PROVIDER_PREFIXES, key=len, reverse=True):
        if text.startswith(prefix):
            provider = prefix.rstrip("/@")
            provider = _PROVIDER_ALIASES.get(provider, provider)
            text = text[len(prefix):]
            break
    if provider == "unknown":
        for stem, candidate in _BARE_PROVIDER_BY_PREFIX:
            if text.startswith(stem):
                provider = candidate
                break

    for suffix in _ROUTE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break  # one route suffix; "-latest" after ":free" is not a real shape

    text = _DATED_SUFFIX.sub("", text)
    text = _COMPACT_DATE_SUFFIX.sub("", text)
    text = _DECIMAL_VERSION.sub(r"\1-\2", text)
    text = text.strip("-._")

    if not text:
        raise ModelProvenanceError(f"`{model_id!r}` carries no model stem")
    return f"{provider}/{text}"


@dataclass(frozen=True)
class ModelIdentity:
    """One model as a seat may run it: provider, exact id, family, harness.

    ``family`` is DERIVED from ``model`` (via :func:`resolve_family`) and cannot be
    declared independently: passing a family that disagrees with the model id is a
    construction error, because a caller naming families into existence is exactly
    the fabrication this module exists to prevent. ``harness`` records HOW the model
    is reached ("app-chat", "openrouter", "local") and participates in provenance
    only — never in independence.
    """

    provider: str
    model: str
    harness: str = ""
    family: str = ""

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip().lower()
        model = str(self.model or "").strip()
        if not provider:
            raise ModelProvenanceError("a model identity requires a provider")
        if not model:
            raise ModelProvenanceError("a model identity requires a model id")
        resolved = resolve_family(f"{provider}/{model}")
        declared = str(self.family or "").strip()
        if declared and declared != resolved:
            raise ModelProvenanceError(
                f"family {declared!r} does not match the model id {model!r} "
                f"(resolves to {resolved!r}) — families are derived, not declared"
            )
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "harness", str(self.harness or "").strip())
        object.__setattr__(self, "family", resolved)

    @property
    def key(self) -> str:
        """The exact requested identity, e.g. ``openai/gpt-4o`` (role-support key)."""
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class ProvenanceRecord:
    """What a seat ASKED for vs what the runtime PROVED actually answered.

    ``actual`` is empty until streamed evidence established it, and is never a copy
    of the request: dispatch's evidence ladder (unknown → requested → selected →
    actual_adapter) is the only thing that raises it. ``proven`` states whether the
    actual identity is established at all.
    """

    requested: ModelIdentity
    actual: ModelIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.requested, ModelIdentity):
            raise ModelProvenanceError("a provenance record requires a requested identity")
        if self.actual is not None and not isinstance(self.actual, ModelIdentity):
            raise ModelProvenanceError("an actual identity is a ModelIdentity or nothing")

    @property
    def proven(self) -> bool:
        return self.actual is not None

    @property
    def effective(self) -> ModelIdentity:
        """The identity independence is counted over: the PROVEN actual when there
        is one, otherwise the request — labelled by ``proven`` either way."""
        return self.actual if self.actual is not None else self.requested


IndependenceItem = Union[ModelIdentity, ProvenanceRecord]


def _effective_identity(item: IndependenceItem) -> ModelIdentity:
    if isinstance(item, ProvenanceRecord):
        return item.effective
    if isinstance(item, ModelIdentity):
        return item
    raise ModelProvenanceError(
        "independence is counted over model identities or provenance records"
    )


def independent_family_count(items: Iterable[IndependenceItem]) -> int:
    """How many DISTINCT families are actually present. Aliases collapse to one."""
    return len({identity.family for identity in (_effective_identity(i) for i in items)})


def collapse_by_family(
    items: Iterable[IndependenceItem],
) -> dict[str, list[ModelIdentity]]:
    """Group identities under their family key. One family, one bucket — the shape
    a family-collapsed vote is computed from."""
    grouped: dict[str, list[ModelIdentity]] = {}
    for item in items:
        identity = _effective_identity(item)
        grouped.setdefault(identity.family, []).append(identity)
    return grouped


__all__ = [
    "ModelIdentity",
    "ModelProvenanceError",
    "ProvenanceRecord",
    "collapse_by_family",
    "independent_family_count",
    "infer_provider",
    "resolve_family",
]
