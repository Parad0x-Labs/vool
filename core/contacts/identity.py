"""Identity comparison for saved contacts: one deterministic, versioned owner shared by create, rename, alias, merge,
import, search and resolve.

Law (``policy_version()`` names the exact version a decision was made under):

* The display name is kept as typed (NFC, surrounding space trimmed); comparison never rewrites it.
* ``comparison_key``: NFKC, default-ignorable code points removed (joiners included -- they shape letters, they do not
  make a different name), full case folding, whitespace collapsed. Equal comparison keys are the SAME name.
* Confusable skeleton, Unicode UTS #39 section 4: ``skeleton(x) = NFD(prototype(NFD(x)))`` with the prototype map generated
  from the system ICU into ``data/uts39_identity.json`` (the file records the ICU and Unicode versions). A name's key set
  is ``{skeleton(casefold(skeleton(p))), skeleton(casefold(p))}`` for ``p`` = NFKC with ignorables removed: the first
  catches uppercase lookalikes (Cyrillic ТОМ, the digit 0 for O), the second case variants of ambiguous letters
  (Ill / ill). Intersecting key sets are CONFUSABLE names.
* ``names.fuzzy_score >= SIMILAR_THRESHOLD`` is only SIMILAR: a warning shown with both identities, never an identity.
* A name holding a direction-changing or invisible character other than a joiner or variation selector is refused with
  the code point named, because it can display as a different name on a review sheet.
* Scripts are reported, not refused: a non-Latin name is ordinary. A mix of scripts outside UTS #39's highly restrictive
  combinations (Han with Hiragana and Katakana, with Hangul, or with Bopomofo) is flagged for display.
* A deleted contact's name keys survive only as HMAC-SHA256 values under a device key derived from this runtime's node
  signing key (``tombstone_hashes``): enough to recognise the same or a lookalike name again, not enough to read it back.

Limits, stated: UTS #39 confusables are visual approximations for identifiers, the table is as good as the ICU data it came
from, and fonts differ. This turns known confusables into a protected review; it is not complete spoofing protection.
Legitimately different names can share a skeleton ("m" and "rn"); they are then reviewed, never refused.
"""
from __future__ import annotations

import bisect
import hashlib
import hmac
import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.contacts import names

SIMILAR_THRESHOLD = names.FUZZY_THRESHOLD
_DATA = Path(__file__).resolve().parent / "data" / "uts39_identity.json"
_TOMBSTONE_LABEL = "vool-contacts-identity-tombstone-v1"

#: Default_Ignorable_Code_Point (Unicode DerivedCoreProperties), as ranges.
_DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F),
    (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
_BIDI_CONTROLS = frozenset({0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)})
#: ignorable but legitimate inside names: joiners and variation selectors (Mongolian free variation selectors included)
_ALLOWED_IGNORABLE = frozenset({0x200C, 0x200D, *range(0xFE00, 0xFE10), *range(0x180B, 0x1810), *range(0xE0100, 0xE01F0)})
_RESTRICTIVE_MIXES = (frozenset({"Hani", "Hira", "Kana"}), frozenset({"Hani", "Hang"}), frozenset({"Hani", "Bopo"}))
_NEUTRAL_SCRIPTS = frozenset({"Zyyy", "Zinh", "Zzzz"})


class IdentityError(ValueError):
    def __init__(self, reason: str, message: str, *, field: str = "display_name", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.field = field
        self.details = dict(details or {})


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[int, str], tuple[int, ...], tuple[tuple[int, int, str], ...], dict[str, Any]]:
    data = json.loads(_DATA.read_text(encoding="utf-8"))
    prototypes = {int(code, 16): value for code, value in data["prototypes"].items()}
    ranges = tuple((int(start), int(end), str(script)) for start, end, script in data["scripts"])
    return prototypes, tuple(start for start, _end, _script in ranges), ranges, dict(data["meta"])


def policy_version() -> str:
    meta = _tables()[3]
    return f"contacts-identity-1/uts39-icu{meta['icu_version']}-unicode{meta['unicode_version']}/similar-{SIMILAR_THRESHOLD}"


def is_default_ignorable(code_point: int) -> bool:
    index = bisect.bisect_right(_DEFAULT_IGNORABLE, (code_point, 0x10FFFF)) - 1
    return index >= 0 and _DEFAULT_IGNORABLE[index][0] <= code_point <= _DEFAULT_IGNORABLE[index][1]


def unsafe_characters(text: Any) -> list[dict[str, Any]]:
    """Characters that can make a name display as a different name: direction controls, invisible characters that are
    not joiners or variation selectors, line and paragraph separators, other controls, private-use characters."""
    found = []
    for position, char in enumerate(str(text or "")):
        code_point = ord(char)
        if char in "\t\n\r" or code_point in _ALLOWED_IGNORABLE:
            continue
        category = unicodedata.category(char)
        if code_point in _BIDI_CONTROLS or is_default_ignorable(code_point) or category in ("Cc", "Zl", "Zp", "Co", "Cs"):
            found.append({"position": position, "code_point": f"U+{code_point:04X}", "name": unicodedata.name(char, "UNNAMED CHARACTER")})
    return found


def require_safe_name(text: Any, *, field: str = "display_name") -> None:
    problems = unsafe_characters(text)
    if problems:
        first = problems[0]
        what = "name" if field == "display_name" else "alias" if field == "aliases" else field
        raise IdentityError(
            "unsafe_characters",
            f"The {what} contains {first['code_point']} {first['name']}, an invisible or direction-changing character that can make it "
            "display as a different name. Type it without that character.",
            field=field, details={"characters": problems},
        )


def _prepared(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = "".join(char for char in value if not is_default_ignorable(ord(char)))
    return " ".join(value.split())


def comparison_key(text: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", _prepared(text).casefold()).split())


def skeleton(text: str) -> str:
    prototypes = _tables()[0]
    return unicodedata.normalize("NFD", "".join(prototypes.get(ord(char), char) for char in unicodedata.normalize("NFD", text)))


@dataclass(frozen=True)
class IdentityKeys:
    comparison: str
    skeleton_upper: str
    skeleton_folded: str

    def skeletons(self) -> frozenset[str]:
        return frozenset(value for value in (self.skeleton_upper, self.skeleton_folded) if value)


def identity_keys(text: Any) -> IdentityKeys:
    return _identity_keys(str(text or ""))


@lru_cache(maxsize=65536)
def _identity_keys(text: str) -> IdentityKeys:
    prepared = _prepared(text)
    if not prepared:
        return IdentityKeys("", "", "")
    return IdentityKeys(comparison_key(text), skeleton(skeleton(prepared).casefold()), skeleton(prepared.casefold()))


MATCH_SAME = "same_name"
MATCH_CONFUSABLE = "confusable"
MATCH_SIMILAR = "similar"


def compare(first: IdentityKeys, second: IdentityKeys) -> str | None:
    """``same_name``, ``confusable`` or None. Similarity is decided separately and never makes an identity."""
    if first.comparison and first.comparison == second.comparison:
        return MATCH_SAME
    if first.skeletons() & second.skeletons():
        return MATCH_CONFUSABLE
    return None


def lookalike_of(query: Any, *, display_name: Any, aliases: Any = ()) -> str | None:
    """``confusable`` when the query looks like -- but is not the same string as -- the display name, an alias, or one word
    of the display name; else None. Search and resolution use this so a lookalike is offered as a choice, never chosen."""
    wanted = identity_keys(query)
    if not wanted.comparison:
        return None
    for candidate in (display_name, *list(aliases or ())):
        if compare(wanted, identity_keys(candidate)) == MATCH_CONFUSABLE:
            return MATCH_CONFUSABLE
    if " " not in wanted.comparison:
        for token in names.tokens(display_name):
            if compare(wanted, identity_keys(token)) == MATCH_CONFUSABLE:
                return MATCH_CONFUSABLE
    return None


def _script_of(code_point: int) -> str:
    _prototypes, starts, ranges, _meta = _tables()
    index = bisect.bisect_right(starts, code_point) - 1
    if index >= 0 and ranges[index][0] <= code_point <= ranges[index][1]:
        return ranges[index][2]
    return "Zzzz"


def scripts_of(text: Any) -> list[str]:
    return sorted({_script_of(ord(char)) for char in str(text or "")} - _NEUTRAL_SCRIPTS)


def mixed_script(text: Any) -> bool:
    found = frozenset(scripts_of(text))
    return len(found) > 1 and not any(found <= combination for combination in _RESTRICTIVE_MIXES)


def visible_characters(text: Any) -> str:
    """The name with every invisible or control character spelled out, for the exact-characters line of a review."""
    out = []
    for char in str(text or ""):
        code_point = ord(char)
        if is_default_ignorable(code_point) or unicodedata.category(char)[0] == "C":
            out.append(f"⟨U+{code_point:04X}⟩")
        else:
            out.append(char)
    return "".join(out)


def code_points(text: Any) -> list[str]:
    """Every code point with its Unicode name, so two names that look alike can be told apart on a review."""
    return [f"U+{ord(char):04X} {unicodedata.name(char, 'UNNAMED CHARACTER')}" for char in str(text or "")[:80]]


def describe(text: Any) -> dict[str, Any]:
    """What a review sheet shows about one name besides the name itself."""
    value = str(text or "")
    return {"display": value, "visible_characters": visible_characters(value), "scripts": scripts_of(value), "mixed_script": mixed_script(value),
            "non_ascii": any(ord(char) > 0x7F for char in value), "code_points": code_points(value), "policy_version": policy_version()}


# --- deleted names ------------------------------------------------------------------------------------------------------

def _tombstone_key() -> bytes:
    from network.signer import derive_local_secret

    return derive_local_secret(_TOMBSTONE_LABEL)


def tombstone_hashes_for_keys(keys: IdentityKeys, *, secret: bytes | None = None) -> dict[str, list[str]]:
    """Keyed hashes of one name's comparison key (``same``) and skeletons (``confusable``)."""
    if not keys.comparison:
        return {"same": [], "confusable": []}
    key = secret if secret is not None else _tombstone_key()

    def digest(tag: str, value: str) -> str:
        return hmac.new(key, f"{tag}|{value}".encode("utf-8"), hashlib.sha256).hexdigest()

    return {"same": [digest("c", keys.comparison)], "confusable": sorted({digest("s", value) for value in keys.skeletons()})}


def tombstone_hashes(texts: Any) -> list[str]:
    """Every keyed hash for these names (a deleted contact's display name and aliases)."""
    key = _tombstone_key()
    found: set[str] = set()
    for text in texts or ():
        tagged = tombstone_hashes_for_keys(identity_keys(text), secret=key)
        found.update(tagged["same"])
        found.update(tagged["confusable"])
    return sorted(found)


__all__ = [
    "IdentityError", "IdentityKeys", "MATCH_CONFUSABLE", "MATCH_SAME", "MATCH_SIMILAR", "SIMILAR_THRESHOLD", "code_points", "compare", "comparison_key",
    "describe", "identity_keys", "is_default_ignorable", "lookalike_of", "mixed_script", "policy_version", "require_safe_name", "scripts_of", "skeleton",
    "tombstone_hashes", "tombstone_hashes_for_keys", "unsafe_characters", "visible_characters",
]
