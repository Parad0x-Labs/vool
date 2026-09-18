"""The ONE typed LocaleSpec authority for VOOL internationalization.

This module EXTENDS the canonical response-language authority
``core.response_language_policy``: the set of response-supported primary languages is
IMPORTED from that module's closed ``LANGUAGE_NAMES`` table and input-inference support
is IMPORTED from its ``_INPUT_EVIDENCE`` table. No second language list exists here —
registry tests fail if the two ever drift apart.

Capability fields are independent by law (the lane's prime directive). In particular:
``response_policy`` says VOOL can REQUEST/PREFER a response language (provider ability
is a separate, unmeasured claim); ``inference`` says VOOL can resolve the INPUT language
from exclusive orthographic evidence alone; ``ui``/``docs`` describe deterministic
artifacts THIS repository actually ships; ``tool_routing`` records the honest C18 verdict
(translated-verb-only execution demands fail closed today); ``speech_ocr`` records that
the local recogniser is not locale-guaranteed; ``proof`` records which locales have a
served, scripted-provider journey in the test suite.

Regional/script distinctions are represented honestly as EXTENDED tags whose response
policy is inherited from their primary (``pt-BR``/``pt-PT`` → ``pt``; ``zh-Hans``/
``zh-Hant`` → ``zh``; ``sr-Cyrl``/``sr-Latn`` → ``sr``). An extended tag gets its own UI
catalog/docs only when that exact artifact exists; otherwise deterministic negotiation
falls back exact → declared parent → English. No row invents support to populate a table.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.response_language_policy import _INPUT_EVIDENCE
from core.response_language_policy import LANGUAGE_NAMES as _RESPONSE_LANGUAGES

DIRECTION_LTR = "ltr"
DIRECTION_RTL = "rtl"

# Capability levels. Plain strings so the exported capability matrix stays JSON-honest.
RESPONSE_NONE = "none"
RESPONSE_REQUESTABLE = "requestable"  # explicit request + scoped operator preference tiers
INFERENCE_NONE = "none"
INFERENCE_EXCLUSIVE = "exclusive_evidence"  # resolvable from input evidence alone
UI_NONE = "none"
UI_CATALOG = "catalog"  # a deterministic catalog for this EXACT tag ships in core/i18n/catalogs
DOCS_ABSENT = "absent"
DOCS_MACHINE_DRAFT = "machine_draft"  # full core set exists; machine-translated, unreviewed
DOCS_COMPLETE = "complete"  # full core set exists AND is human-verified (requires a reviewer)
ROUTING_EN_VERB_ANCHORED = "en_verb_anchored"  # English verb-anchored typed demand routes
ROUTING_FAILS_CLOSED = "fails_closed"  # translated-verb-only demand carries no route (C18 §3b)
SPEECH_NOT_GUARANTEED = "not_locale_guaranteed"  # local recogniser; no per-locale promise
PROOF_NONE = "none"
PROOF_SERVED_SCRIPTED = "served_scripted"  # served journey proven with a scripted provider

#: Endonyms for the 50 response-supported primaries (identity data, not a support claim).
_PRIMARY_ENDONYMS: dict[str, str] = {
    "en": "English", "lt": "lietuvių", "lv": "latviešu", "et": "eesti",
    "pl": "polski", "de": "Deutsch", "it": "italiano", "fr": "français",
    "nl": "Nederlands", "pt": "português", "es": "español", "sv": "svenska",
    "da": "dansk", "nb": "norsk (bokmål)", "fi": "suomi", "cs": "čeština",
    "sk": "slovenčina", "hu": "magyar", "ro": "română", "el": "Ελληνικά",
    "tr": "Türkçe", "uk": "українська", "ru": "русский", "be": "беларуская",
    "bg": "български", "sr": "српски", "mk": "македонски", "hr": "hrvatski",
    "he": "עברית", "ar": "العربية", "fa": "فارسی", "ur": "اردو", "hi": "हिन्दी",
    "bn": "বাংলা", "ta": "தமிழ்", "th": "ไทย", "vi": "Tiếng Việt",
    "id": "Bahasa Indonesia", "ms": "Bahasa Melayu", "ja": "日本語", "zh": "中文",
    "ko": "한국어", "sw": "Kiswahili", "ha": "Hausa", "yo": "Yorùbá", "ig": "Igbo",
    "zu": "isiZulu", "am": "አማርኛ", "ka": "ქართული", "hy": "հայերեն",
}

#: Primary languages whose script runs right-to-left.
_RTL_PRIMARIES = frozenset({"ar", "fa", "ur", "he"})

#: Honest regional/script extensions: tag -> (endonym, parent primary). Response policy
#: and inference are INHERITED from the parent; UI catalogs and docs exist per exact tag
#: only where actually shipped. Serbian's constitutional default script is Cyrillic, so
#: the bare ``sr`` row serves Cyrillic and ``sr-Latn`` is the explicit Latin-script ask.
_EXTENDED_ENDONYMS: dict[str, tuple[str, str]] = {
    "pt-BR": ("português do Brasil", "pt"),
    "pt-PT": ("português europeu", "pt"),
    "zh-Hans": ("简体中文", "zh"),
    "zh-Hant": ("繁體中文", "zh"),
    "sr-Cyrl": ("српски", "sr"),
    "sr-Latn": ("srpski", "sr"),
}


@dataclass(frozen=True)
class LocaleSpec:
    """One immutable, capability-specific locale row. See module docstring for the law."""

    tag: str
    language: str  # primary BCP-47 subtag this row resolves to for response policy
    endonym: str
    direction: str
    parent: str | None
    response_policy: str
    inference: str
    ui: str
    docs: str
    tool_routing: str
    speech_ocr: str
    proof: str
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "language": self.language,
            "endonym": self.endonym,
            "direction": self.direction,
            "fallback_chain": list(fallback_chain(self.tag)),
            "response_policy": self.response_policy,
            "inference": self.inference,
            "ui": self.ui,
            "docs": self.docs,
            "tool_routing": self.tool_routing,
            "speech_ocr": self.speech_ocr,
            "proof": self.proof,
            "notes": list(self.notes),
        }

    def __post_init__(self) -> None:
        if self.direction not in (DIRECTION_LTR, DIRECTION_RTL):
            raise ValueError(f"locale {self.tag!r}: direction must be ltr or rtl")
        if self.tag == "en" and self.parent is not None:
            raise ValueError("en is the root fallback and cannot have a parent")
        for level, allowed in (
            ("response_policy", (RESPONSE_NONE, RESPONSE_REQUESTABLE)),
            ("inference", (INFERENCE_NONE, INFERENCE_EXCLUSIVE)),
            ("ui", (UI_NONE, UI_CATALOG)),
            ("docs", (DOCS_ABSENT, DOCS_MACHINE_DRAFT, DOCS_COMPLETE)),
            ("tool_routing", (ROUTING_EN_VERB_ANCHORED, ROUTING_FAILS_CLOSED)),
            ("speech_ocr", (SPEECH_NOT_GUARANTEED,)),
            ("proof", (PROOF_NONE, PROOF_SERVED_SCRIPTED)),
        ):
            value = getattr(self, level)
            if value not in allowed:
                raise ValueError(f"locale {self.tag!r}: unknown {level} level {value!r}")


#: Primaries with a SERVED, scripted-provider journey at this tip
#: (tests/test_i18n_served_ten_languages.py — the ten-language amendment drives a
#: real /api/chat turn per locale to a TARGET-LANGUAGE final answer — plus the C18
#: served suites). Scripted provider = protocol proof, NOT real-model usefulness;
#: every other locale stays unproven.
_PROVEN_SERVED: frozenset[str] = frozenset(
    {"en", "lt", "es", "de", "ja", "ar", "uk", "hi", "zh", "pt"}
)


def _build_registry() -> dict[str, LocaleSpec]:
    registry: dict[str, LocaleSpec] = {}
    for code in sorted(_RESPONSE_LANGUAGES):
        endonym = _PRIMARY_ENDONYMS.get(code, _RESPONSE_LANGUAGES[code])
        registry[code] = LocaleSpec(
            tag=code,
            language=code,
            endonym=endonym,
            direction=DIRECTION_RTL if code in _RTL_PRIMARIES else DIRECTION_LTR,
            parent=None,
            response_policy=RESPONSE_REQUESTABLE,
            inference=INFERENCE_EXCLUSIVE if code in _INPUT_EVIDENCE else INFERENCE_NONE,
            # English verb-anchored typed demands route (C18 §3b); every other primary
            # fails closed today. UI/docs/proof levels derive from artifacts this
            # repository actually ships (catalog files / docs directories / served
            # proofs), so the registry cannot claim support that does not exist.
            ui=UI_CATALOG if _has_ui_catalog(code) else UI_NONE,
            # Docs level says what SHIPS: machine-draft core set per locale, canonical
            # English complete. Whether a draft ever becomes human-verified is recorded
            # per document in its front matter, never inferred from existence.
            docs=(DOCS_COMPLETE if code == "en" else DOCS_MACHINE_DRAFT)
            if _has_docs_set(code) else DOCS_ABSENT,
            tool_routing=ROUTING_EN_VERB_ANCHORED if code == "en" else ROUTING_FAILS_CLOSED,
            speech_ocr=SPEECH_NOT_GUARANTEED,
            proof=PROOF_SERVED_SCRIPTED if code in _PROVEN_SERVED else PROOF_NONE,
        )
    for tag, (endonym, parent) in _EXTENDED_ENDONYMS.items():
        primary = registry[parent]
        registry[tag] = LocaleSpec(
            tag=tag,
            language=parent,
            endonym=endonym,
            direction=primary.direction,
            parent=parent,
            response_policy=primary.response_policy,
            inference=primary.inference,
            ui=UI_CATALOG if _has_ui_catalog(tag) else UI_NONE,
            docs=DOCS_MACHINE_DRAFT if _has_docs_set(tag) else DOCS_ABSENT,
            tool_routing=primary.tool_routing,
            speech_ocr=primary.speech_ocr,
            proof=PROOF_NONE,
            notes=("regional/script extension; response policy inherited from parent",),
        )
    return registry


_CORE_DOC_NAMES = (
    "quickstart",
    "providers",
    "permissions",
    "troubleshooting",
    "privacy",
    "wallet-x402",
)


def _has_ui_catalog(tag: str) -> bool:
    """A UI level exists exactly when a deterministic catalog file ships for the tag."""
    from pathlib import Path

    return (Path(__file__).resolve().parent / "catalogs" / f"{tag}.json").is_file()


def _has_docs_set(tag: str) -> bool:
    """A docs level exists exactly when the full six-document core set ships for the
    tag under docs/i18n/<tag>/ (canonical English lives in docs/i18n/sources/)."""
    from pathlib import Path

    base = Path(__file__).resolve().parents[2] / "docs" / "i18n"
    if tag == "en":
        docs_dir = base / "sources"
    else:
        # A bare primary whose docs ship under its script-default sibling (zh ->
        # zh-Hans) reads that directory, mirroring the UI catalog resolution.
        docs_dir = base / {"zh": "zh-Hans"}.get(tag, tag)
    return all((docs_dir / f"{name}.md").is_file() for name in _CORE_DOC_NAMES)


#: The immutable registry. Rebuilt only by editing this module (tests pin its contents
#: against core.response_language_policy so a second list cannot silently appear).
_LOCALES: dict[str, LocaleSpec] = _build_registry()


def get_locale(tag: str) -> LocaleSpec | None:
    """Exact-tag lookup. Unknown tags return None (callers negotiate, never guess)."""
    return _LOCALES.get(tag)


def all_locale_tags() -> tuple[str, ...]:
    return tuple(sorted(_LOCALES))


def response_supported_tags() -> tuple[str, ...]:
    """Primary languages VOOL can explicitly request/prefer (canonical table, 50 rows)."""
    return tuple(sorted(_RESPONSE_LANGUAGES))


def inferable_tags() -> tuple[str, ...]:
    """Languages resolvable from exclusive input evidence alone (canonical, 32 rows)."""
    return tuple(sorted(_INPUT_EVIDENCE))


def ui_catalog_tags() -> tuple[str, ...]:
    return tuple(sorted(t for t, spec in _LOCALES.items() if spec.ui == UI_CATALOG))


def docs_supported_tags() -> tuple[str, ...]:
    return tuple(sorted(t for t, spec in _LOCALES.items() if spec.docs != DOCS_ABSENT))


def extended_tags_for(primary: str) -> tuple[str, ...]:
    return tuple(sorted(t for t, spec in _LOCALES.items() if spec.parent == primary))


def endonym_for(tag: str) -> str:
    spec = _LOCALES.get(tag)
    return spec.endonym if spec else tag


def fallback_chain(tag: str) -> tuple[str, ...]:
    """Deterministic fallback: exact locale → declared parent(s) → English. No model."""
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = tag
    while current is not None and current not in seen:
        if current in _LOCALES or current == "en":
            chain.append(current)
            seen.add(current)
        spec = _LOCALES.get(current)
        current = spec.parent if spec is not None else ("en" if current != "en" else None)
    if not chain or chain[-1] != "en":
        chain.append("en")
    return tuple(chain)


def negotiate_ui_locale(requested: str | None) -> str:
    """Negotiate a UI locale tag: exact registry tag, else its primary subtag, else en.

    Pure and deterministic; accepts any BCP-47-ish string and never invents a tag that
    is not in the registry.
    """
    if not requested:
        return "en"
    candidate = requested.strip()
    if not candidate:
        return "en"
    # Case-normalize the script/region subtags the registry uses (e.g. "PT-br" → "pt-BR").
    parts = candidate.split("-")
    normalized = parts[0].lower()
    for part in parts[1:]:
        normalized += "-" + (part[:1].upper() + part[1:].lower() if len(part) == 4 else part.upper())
    if normalized in _LOCALES:
        return normalized
    primary = normalized.split("-")[0]
    if primary in _LOCALES:
        return primary
    # A requested script-only variant of a supported primary (e.g. "zh-Hant" aliases) or
    # an unknown primary both fall back to English rather than guessing.
    return "en"


def capability_matrix() -> dict[str, Any]:
    """The exported matrix structure (docs/I18N_CAPABILITY_MATRIX.json is its artifact)."""
    return {
        "schema": "vool.i18n.capability_matrix.v1",
        "counts": {
            "response_supported_primaries": len(response_supported_tags()),
            "input_inferable": len(inferable_tags()),
            "ui_catalogs": len(ui_catalog_tags()),
            "docs_locales": len(docs_supported_tags()),
            "registry_rows": len(_LOCALES),
        },
        "locales": {tag: spec.to_dict() for tag, spec in sorted(_LOCALES.items())},
    }
