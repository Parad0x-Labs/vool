"""Classification → likely-provider shortlist. Pure local computation, like the classifier.

The shortlist is what the operator chooses from. Three honest shapes:

* **unique prefix** — one high-confidence entry (``sk-or-`` → openrouter). The operator still
  confirms, but there is nothing to disambiguate.
* **ambiguous family** — a bare ``sk-`` is every registry member of the format's ambiguous
  group (openai/deepseek/moonshot) at medium confidence; nobody is pre-picked, because
  storing a key in the wrong slot produces a provider that fails auth forever with a key the
  user knows is good — the worst failure this flow can have.
* **recognized but unconfigured** (a Slack token with no Slack provider) or **unrecognized**
  — no entries at all, and the flag says which. An unfamiliar prefix is a reason to ASK, never a
  reason to refuse: every shape carries a one-sentence ``reason`` the setup surface shows beside
  the full provider selection, so the operator can pick the service (or a custom endpoint) and
  verify with exactly that one.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from core.credential_intelligence.format_classifier import KeyFormat

Confidence = Literal["high", "medium", "low"]

#: format-family → human name, used only when NO registry provider claims the family.
_RECOGNIZED_FAMILY_NAMES: dict[str, str] = {
    "slack": "slack",
    "github": "github",
    "aws": "aws",
    "gitlab": "gitlab",
    "npm": "npm",
    "jwt": "jwt",
    "pem": "pem-private-key",
    "hex": "hex-token",
    "openai_style": "openai-style",
    "openrouter": "openrouter",
    "anthropic": "anthropic",
    "openai": "openai",
    "groq": "groq",
    "google": "google",
    "tavily": "tavily",
}

_UNRECOGNIZED_REASON = (
    "No documented key prefix matched, so VOOL cannot tell which service this key is for. "
    "Pick the service from the list, or the custom endpoint for an OpenAI-compatible API."
)


@dataclass(frozen=True)
class ShortlistEntry:
    provider_id: str
    label: str
    confidence: Confidence
    rationale: str


@dataclass(frozen=True)
class ProviderShortlist:
    entries: tuple[ShortlistEntry, ...]
    #: True when the format matches nothing known — the surface says so, stores nothing on this
    #: basis, and offers the full provider selection instead.
    unrecognized: bool
    #: A non-empty family name the industry recognizes but no registry provider claims.
    recognized_family: str = ""
    #: One concise sentence for the setup surface. Never contains key material.
    reason: str = ""

    @property
    def ambiguous(self) -> bool:
        return len(self.entries) > 1

    @property
    def suggestion(self) -> str:
        """The single provider a unique documented prefix names, or "" when ambiguous or unknown."""
        if len(self.entries) == 1 and self.entries[0].confidence == "high":
            return self.entries[0].provider_id
        return ""


def _joined(labels: Iterable[str]) -> str:
    names = [str(label) for label in labels if str(label)]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " or " + names[-1]


def build_shortlist(fmt: KeyFormat, registry) -> ProviderShortlist:
    """Map a classified key format to the providers that could own it. Never sends anything
    anywhere; never picks a winner among ambiguous candidates."""
    if not fmt.plausible_key and not fmt.is_pem:
        return ProviderShortlist(
            (), True, "",
            "That does not look like a complete API key (it is too short or spans more than one line).",
        )

    # Documented prefixes: the registry's own order decides presentation order.
    if fmt.prefix:
        entries = tuple(
            ShortlistEntry(d.provider_id, d.label, "high", f"key prefix {fmt.prefix!r}")
            for d in registry.providers()
            if fmt.prefix in d.key_prefixes
        )
        if len(entries) == 1:
            return ProviderShortlist(
                entries, False, "", f"The key starts with {fmt.prefix!r}, the documented prefix for {entries[0].label}.",
            )
        if entries:
            return ProviderShortlist(
                entries, False, "",
                f"Keys starting with {fmt.prefix!r} are used by {_joined(e.label for e in entries)}; pick the one this key is for.",
            )

    # Ambiguous group (the bare-sk family): every member, medium confidence, no pre-pick.
    group = _group_for_family(fmt.family)
    if group:
        entries = tuple(
            ShortlistEntry(d.provider_id, d.label, "medium", f"ambiguous {fmt.family} format")
            for d in registry.providers()
            if d.ambiguous_group == group
        )
        if entries:
            return ProviderShortlist(
                entries, False, "",
                f"Keys starting with 'sk-' are used by {_joined(e.label for e in entries)}; pick the one this key is for.",
            )

    family = _RECOGNIZED_FAMILY_NAMES.get(fmt.family, "")
    if fmt.family in _RECOGNIZED_FAMILY_NAMES:
        # Known shape, but this registry has no provider for it (or a hex token nobody claims).
        if fmt.family == "pem":
            reason = "This is a private key, not a service API key."
        elif fmt.family == "hex":
            reason = _UNRECOGNIZED_REASON
        else:
            reason = (
                f"This looks like a {family} token, not a model or web-search key. "
                "Pick the service yourself if you are sure it belongs to one."
            )
        return ProviderShortlist((), False, family, reason)
    return ProviderShortlist((), True, "", _UNRECOGNIZED_REASON)


def _group_for_family(family: str) -> str:
    return "bare_sk" if family == "openai_style" else ""


__all__ = [
    "Confidence",
    "ProviderShortlist",
    "ShortlistEntry",
    "build_shortlist",
]
