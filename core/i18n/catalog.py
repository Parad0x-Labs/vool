"""Deterministic message catalogs for VOOL's UI. Static data only — never a model.

Laws enforced here:

- **English is the source of truth.** ``catalogs/en.json`` is authored in this
  repository; every other locale file records the SHA-256 of the ``en.json`` bytes it
  was translated from, so stale or drifting translations are detectable (the
  stale-translation checker in ``tools/i18n/check_translations.py`` fails CI on it).
- **Fallback is deterministic**: exact locale → declared parent → English → key name,
  in that order, resolved by pure functions. A missing key falls back VISIBLY to
  English and is recorded in a bounded diagnostic; text is never blank and a model is
  never consulted.
- **Corrupt or missing locale files fail safely to English** (bounded diagnostic; no
  exception escapes the render path).
- **Placeholders are schema-checked.** ``{name}`` placeholders in a translation must
  match the source set exactly; a mismatched translation is rejected at load time and
  its key falls back to English. Placeholder VALUES are HTML-escaped when formatted.
  Messages explicitly listed in ``html_keys`` may carry a strict whitelist of inline
  markup (``b/strong/em/i/code/br/kbd``, ``span class="…"``); anything else in any
  message is rejected and falls back to English.
- **ICU-lite plural/select.** ``{n, plural, one {…} other {…}}`` and
  ``{name, select, a {…} other {…}}`` are the only compound forms; ``one``/``other``
  are the only categories (resolved ``n === 1 → one``, else ``other``), identically
  here and in the page's mirrored JS helper.
- **Authority text and its PRESENTATION are separated, not excluded.** The authority
  seams (core.faults codes/severities, permission decision scopes, wallet amounts and
  signed payloads, updater state machines) stay deterministic and English-anchored at
  runtime by design. Their *presentations* — the sentences an operator reads — are
  cataloged here under stable keys (``fault.<code>.message`` mirrors
  ``core.faults.catalog`` ``user_message`` byte-exactly in English; a test pins the
  mirror so the two cannot drift). Rendering resolves the key by locale and falls back
  to the authority's own English; the authority value itself is never rewritten, and
  no consumer parses translated prose to decide permission, spending, routing or
  recovery behavior. This module imports no provider, model, or runtime translation
  machinery, and tests pin that structurally.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CATALOG_SCHEMA = "vool.i18n.catalog.v1"
CATALOGS_DIR = Path(__file__).resolve().parent / "catalogs"
SOURCE_LOCALE = "en"

# Bounded diagnostics: a render path must never grow an unbounded log.
MAX_RECORDED_MISSING_KEYS = 20

_PLURAL_RE = re.compile(r"\{(\w+),\s*plural,\s*([^{}]*\{[^{}]*\}[^{}]*)\}", re.DOTALL)
_SELECT_RE = re.compile(r"\{(\w+),\s*select,\s*([^{}]*\{[^{}]*\}[^{}]*)\}", re.DOTALL)
_BRANCH_RE = re.compile(r"\b(one|other)\s*\{([^{}]*)\}")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)((?:\s+[^<>]*?)?)>")
_INLINE_TAGS = frozenset({"b", "strong", "em", "i", "code", "br", "kbd"})
_COMPOUND_HEAD_RE = re.compile(r"\{(\w+),\s*(plural|select),\s*")


def _walk_braced(text: str, start: int) -> int:
    """Index just past the brace group that opens at ``start`` (text[start] == '{')."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _extract_compounds(text: str) -> list[tuple[str, str, str, int, int]]:
    """Every ``{name, plural, …}`` / ``{name, select, …}`` with (name, kind, body, start, end)."""
    out: list[tuple[str, str, str, int, int]] = []
    pos = 0
    while True:
        m = _COMPOUND_HEAD_RE.search(text, pos)
        if not m:
            return out
        end = _walk_braced(text, m.start())
        body = text[m.end(): end - 1]
        out.append((m.group(1), m.group(2), body, m.start(), end))
        pos = end


def _branches(body: str) -> dict[str, str]:
    """``one {…} other {…}`` branches, depth-walked so nested ``{placeholder}`` survives."""
    out: dict[str, str] = {}
    for m in re.finditer(r"([A-Za-z][A-Za-z0-9_]*)\s*\{", body):
        name = m.group(1)
        if name in out:
            continue
        close = _walk_braced(body, m.end() - 1)
        out[name] = body[m.end(): close - 1]
    return out


class CatalogError(Exception):
    """Raised only for a corrupt *English source* catalog (a build defect, not runtime)."""


def escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def markup_is_whitelisted(text: str) -> bool:
    """True when every tag in ``text`` is an allowed inline tag with no attributes
    (except ``span class="…"``). Anything else — script, events, URLs in tags — fails."""
    for match in _TAG_RE.finditer(text):
        whole = match.group(0)
        tag = match.group(1).lower()
        attrs = match.group(2).strip()
        closing = whole.startswith("</")
        if tag == "span":
            if closing:
                if attrs:
                    return False
            elif not re.fullmatch(r'(class|id)="[a-zA-Z0-9 _-]+"', attrs):
                return False
        elif tag in _INLINE_TAGS:
            if attrs:
                return False
        else:
            return False
    return True


def _strip_compound_forms(text: str) -> str:
    out: list[str] = []
    pos = 0
    for _, _, _, start, end in _extract_compounds(text):
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    return "".join(out)


def placeholders_of(text: str) -> frozenset[str]:
    """All simple ``{name}`` placeholders, excluding plural/select compound spans."""
    return frozenset(_PLACEHOLDER_RE.findall(_strip_compound_forms(text)))


def _branch_map(compound_body: str) -> dict[str, str]:
    return _branches(compound_body)


def format_message(text: str, params: dict[str, Any] | None = None) -> str:
    """Deterministically format a catalog message: plural/select first, then ``{name}``.

    Placeholder values are HTML-escaped (catalog text targets markup contexts); the
    page's JS helper mirrors this contract at its insertion points.
    """
    resolved = text
    while True:
        compounds = _extract_compounds(resolved)
        if not compounds:
            break
        name, kind, body, start, end = compounds[0]
        branches = _branches(body)
        if kind == "plural":
            count = int(params.get(name, 2)) if params else 2
            chosen = branches.get("one" if count == 1 else "other", branches.get("other", ""))
        else:
            value = str(params.get(name, "")) if params else ""
            chosen = branches.get(value, branches.get("other", ""))
        resolved = resolved[:start] + chosen + resolved[end:]
    if params:
        for name, value in params.items():
            resolved = resolved.replace("{" + name + "}", escape_html(str(value)))
    return resolved


@dataclass
class CatalogDiagnostic:
    """Bounded record of every way a catalog fell back to English. Never raises."""

    locale: str = SOURCE_LOCALE
    missing_keys: list[str] = field(default_factory=list)
    rejected_keys: dict[str, str] = field(default_factory=dict)
    stale_source: bool = False
    corrupt_file: bool = False

    @property
    def used_fallback(self) -> bool:
        return bool(self.missing_keys or self.rejected_keys or self.stale_source or self.corrupt_file)

    def note(self, key: str, reason: str) -> None:
        if len(self.missing_keys) < MAX_RECORDED_MISSING_KEYS:
            self.missing_keys.append(key)
        if len(self.rejected_keys) < MAX_RECORDED_MISSING_KEYS:
            self.rejected_keys[key] = reason


def catalog_diagnostic_note(diagnostic: CatalogDiagnostic) -> str:
    if not diagnostic.used_fallback:
        return f"locale {diagnostic.locale}: all keys resolved"
    parts = [f"locale {diagnostic.locale}: fell back to English"]
    if diagnostic.corrupt_file:
        parts.append("catalog file missing/corrupt")
    if diagnostic.stale_source:
        parts.append("catalog source hash does not match current en.json (stale)")
    if diagnostic.missing_keys:
        shown = ", ".join(diagnostic.missing_keys[:5])
        more = len(diagnostic.missing_keys) - 5
        parts.append(f"missing keys [{shown}{' …' + str(more) + ' more' if more > 0 else ''}]")
    if diagnostic.rejected_keys:
        first = next(iter(diagnostic.rejected_keys.items()))
        parts.append(f"rejected keys (first: {first[0]}: {first[1]})")
    return "; ".join(parts)


def source_catalog_sha256() -> str:
    return hashlib.sha256((CATALOGS_DIR / "en.json").read_bytes()).hexdigest()


def load_catalog_data(locale: str) -> dict[str, Any]:
    data = json.loads((CATALOGS_DIR / f"{locale}.json").read_bytes())
    if data.get("schema") != CATALOG_SCHEMA:
        raise CatalogError(f"catalog {locale}: wrong schema {data.get('schema')!r}")
    return data


def _validate_locale_entry(
    key: str, value: str, source_value: str, *, is_html: bool = False
) -> str | None:
    """Return a rejection reason, or None when the translation is structurally valid."""
    if not isinstance(value, str) or not value.strip():
        return "empty or non-string translation"
    if placeholders_of(value) != placeholders_of(source_value):
        return (
            f"placeholder mismatch: source {sorted(placeholders_of(source_value))} "
            f"vs translation {sorted(placeholders_of(value))}"
        )
    compound_source = bool(_extract_compounds(source_value))
    compound_translation = bool(_extract_compounds(value))
    if compound_source and not compound_translation:
        return "source has plural/select but translation lost it"
    if compound_translation and not compound_source:
        return "translation added plural/select the source does not have"
    if compound_translation:
        for _name, _kind, body, _s, _e in _extract_compounds(value):
            if "other" not in _branches(body):
                return "plural/select branch set missing the required 'other' category"
    if is_html:
        # An html message must keep the EXACT tag multiset: dropping a span loses
        # structure; adding one can smuggle styling or structure past review.
        if not markup_is_whitelisted(value):
            return "html translation contains markup outside the whitelist"
        if sorted(_TAG_RE.findall(value)) != sorted(_TAG_RE.findall(source_value)):
            return "html translation changed the source tag multiset"
    elif "<" in value and not markup_is_whitelisted(value):
        if not markup_is_whitelisted(source_value):
            return "translation introduced markup the source does not carry"
    return None


class MessageCatalog:
    """One locale's resolved view over the English source. Immutable after load."""

    def __init__(self, locale: str, diagnostic: CatalogDiagnostic | None = None) -> None:
        self.locale = locale
        self.diagnostic = diagnostic or CatalogDiagnostic(locale=locale)
        self._source = self._load_json(SOURCE_LOCALE)
        self._source_messages: dict[str, str] = self._source["messages"]
        self._messages: dict[str, str] = dict(self._source_messages)
        self._html_keys: frozenset[str] = frozenset(self._source.get("html_keys", []))
        # The keys this locale's own file supplied and the loader accepted. A key whose
        # translation is byte-identical to English (a technical identifier such as "PIN")
        # still counts as supplied — completeness measures the catalog file, not difference.
        self._locale_keys: set[str] = set()
        if locale != SOURCE_LOCALE:
            self._apply_locale(locale)

    def _load_json(self, locale: str) -> dict[str, Any]:
        try:
            return load_catalog_data(locale)
        except (OSError, json.JSONDecodeError, CatalogError) as exc:
            if locale == SOURCE_LOCALE:
                raise CatalogError(f"English source catalog is unusable: {exc}") from exc
            raise

    def _apply_locale(self, locale: str) -> None:
        try:
            data = self._load_json(locale)
        except (OSError, json.JSONDecodeError, CatalogError):
            self.diagnostic.corrupt_file = True
            return
        if data.get("locale") != locale:
            self.diagnostic.corrupt_file = True
            return
        recorded = data.get("source_catalog_sha256")
        if recorded and recorded != source_catalog_sha256():
            # Deterministic staleness law: a catalog recorded against different source
            # bytes serves English rather than half-drifted text. The checker in
            # tools/i18n fails CI on this before it can ship.
            self.diagnostic.stale_source = True
            return
        for key, value in data.get("messages", {}).items():
            source_value = self._source_messages.get(key)
            if source_value is None:
                self.diagnostic.note(key, "key not in English source")
                continue
            reason = _validate_locale_entry(
                key, value, source_value, is_html=key in self._html_keys
            )
            if reason is None and key not in self._html_keys and "<" in value:
                reason = "markup in a non-html message"
            if reason is None:
                self._messages[key] = value
                self._locale_keys.add(key)
            else:
                self.diagnostic.note(key, reason)

    @property
    def locale_keys(self) -> frozenset[str]:
        """Keys this locale's own catalog file supplied (English source returns the full set)."""
        if self.locale == SOURCE_LOCALE:
            return frozenset(self._source_messages)
        return frozenset(self._locale_keys)

    def text(self, key: str) -> str:
        """Resolved text for ``key``: locale → English → the key itself (never blank)."""
        value = self._messages.get(key)
        if value is not None:
            return value
        if key not in self._source_messages:
            self.diagnostic.note(key, "unknown key (not in English source)")
            return key
        self.diagnostic.note(key, "missing in this locale")
        return self._source_messages[key]

    def format(self, key: str, **params: Any) -> str:
        return format_message(self.text(key), params)

    def is_html_key(self, key: str) -> bool:
        return key in self._html_keys

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._source_messages))

    def client_bundle(self) -> dict[str, Any]:
        """The page's deterministic fallback bundle: locale messages + full English."""
        return {
            "locale": self.locale,
            "messages": dict(self._messages),
            "english": dict(self._source_messages),
            "htmlKeys": sorted(self._html_keys),
        }


def resolve_render_locale(
    query_locale: str | None, cookie_locale: str | None, default: str = "en"
) -> str:
    """Deterministic UI-locale precedence for one render: query > cookie > default.

    A candidate wins only when it names a supported locale (negotiated exact registry
    tag → primary); an unknown candidate is skipped, not guessed into English past a
    valid later candidate.
    """
    from core.i18n.locales import negotiate_ui_locale

    for candidate in (query_locale, cookie_locale, default):
        if candidate is None or not str(candidate).strip():
            continue
        text = str(candidate).strip()
        if text.lower() == SOURCE_LOCALE:
            return SOURCE_LOCALE
        negotiated = negotiate_ui_locale(text)
        if negotiated != SOURCE_LOCALE:
            return negotiated
    return SOURCE_LOCALE


_CATALOG_CACHE: dict[str, MessageCatalog] = {}

#: When a primary language asks for UI localization without naming a script, the
#: script-default catalog serves it (the reverse direction of the declared parent
#: chain: ``zh-Hans`` falls back to ``zh``; ``zh`` resolves forward to ``zh-Hans``).
_SCRIPT_DEFAULT_CATALOGS = {"zh": "zh-Hans"}


def resolve_catalog_tag(locale: str) -> str:
    """Deterministic catalog tag: negotiated tag → its fallback chain (declared
    parent) → script default → English. Never guesses beyond these."""
    from core.i18n.locales import fallback_chain, negotiate_ui_locale

    negotiated = negotiate_ui_locale(locale)
    for tag in fallback_chain(negotiated):
        if tag == SOURCE_LOCALE:
            break
        if (CATALOGS_DIR / f"{tag}.json").is_file():
            return tag
    default = _SCRIPT_DEFAULT_CATALOGS.get(negotiated)
    if default and (CATALOGS_DIR / f"{default}.json").is_file():
        return default
    return SOURCE_LOCALE


def catalog_for(locale: str) -> MessageCatalog:
    """Cached catalog accessor. A locale with no shipped catalog serves English."""
    tag = resolve_catalog_tag(locale)
    catalog = _CATALOG_CACHE.get(tag)
    if catalog is None:
        catalog = MessageCatalog(tag)
        _CATALOG_CACHE[tag] = catalog
    return catalog


def fault_message_key(code: str) -> str:
    """The stable presentation key for one fault code (``fault.<code>.message``).

    The English entry mirrors ``core.faults.catalog``'s ``user_message`` byte-exactly
    (a test pins the mirror). Presentations resolve through the normal fallback chain;
    the fault authority itself is never looked up from here.
    """
    return f"fault.{str(code).strip()}.message"


def clear_catalog_cache() -> None:
    """Test/sabotage hook: the cache must never mask an on-disk catalog change."""
    _CATALOG_CACHE.clear()
