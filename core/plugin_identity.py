"""Who a plugin says it is, and how its author's words are carried: read as data, never as VOOL speaking.

A package writes its own id, its name, its maker's name and its descriptions. Nothing in those
fields is proof of anything, so three things are decided here, in one place:

* **Which ids and names a package from outside may not use** (``identity_refusal``): the ids of
  the packs VOOL ships, VOOL's and its maker's own names, the names VOOL's own tools start
  with, and words or marks that say someone checked or chose the package. Whether a pack IS
  VOOL's own is decided only by where it is loaded from (``core.plugin_catalog``), never here
  and never by what a package calls itself.
* **How a plugin's name is written into a sentence VOOL says itself** (``shown_name``): plain
  letters and a few marks, short, no link and no second sentence.
* **How an author's description is put into a prompt** (``prompt_data``): as one flat piece of
  text that cannot close a quote or open a line of its own.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any

#: Said when a name, maker or key label could not be read as plain text.
PLAIN_TEXT = ("This plugin's name, maker or key label has hidden marks, look-alike letters, links or symbols. "
              "Write it in plain letters, numbers and spaces to install it.")
#: Said when one of them uses VOOL's own names.
OWN_NAME = ("This plugin's name, maker or key label uses VOOL's own name. Only VOOL's own plugins can carry it. "
            "Take it out to install it.")
#: Said when one of them claims a check or a choice nobody made.
VOUCHING = ("This plugin's name, maker or key label says it was checked or chosen, with a word like Official or "
            "Verified, or with a tick or a star. Take that out to install it.")

#: Names the runtime answers to that no tool contract carries (the memory lane, the connection-server
#: prefix, the plugin surface itself). Every other built-in name is read from the tools at run time.
_RUNTIME_NAMES = frozenset({"memory", "mcp", "plugin", "builtin"})

# Letters of other alphabets that are drawn like the letters of VOOL's own name, beside the shared
# look-alike table the skill checks keep.
_MORE_LOOKALIKES = str.maketrans({
    "\u0475": "v", "\u0474": "V", "\u2c7d": "V", "\ua4e6": "V", "\u13d9": "V", "\u142f": "V",
    "\u0585": "o", "\u0555": "O", "\ua4f3": "O", "\u2d54": "O", "\u0966": "o", "\u0d20": "o",
    "\u01c0": "l", "\u0196": "l", "\u026d": "l", "\u029f": "l", "\ua4e1": "L", "\u13de": "L", "\u216c": "L",
    "\u0131": "i", "\u0269": "i",
})
# Digits and signs written in place of letters. "1" is read both ways, as l and as i.
_STAND_INS = str.maketrans({"0": "o", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_APOSTROPHES = re.compile("['\u2018\u2019\u02bc`\u00b4]")
_NOT_A_LETTER = re.compile(r"[^a-z0-9]+")

# VOOL's name, also with a zero for an o or a one for the l. "v001" alone is a model or version number.
_VOOL = re.compile(r"v(?!001)[o0]{2}[l1]")
_OWN_NAMES = re.compile(r"\bv o o l\b|skill ?codex|\bparadox ?labs?\b")
_VOUCHING_WORDS = re.compile(
    r"\beditors? ?(?:choice|pick)s?\b|\bstaff ?(?:pick|choice)s?\b|"
    r"\b(?:tested|recommended|approved|verified|checked|reviewed|certified|endorsed|trusted|picked|chosen|"
    r"featured) by\b|"
    r"\b(?:verified|official|officially|certified|endorsed|featured|trusted)\b")
# Ticks, stars, medals, shields and the two trade marks: read as a badge beside a name.
_BADGE_MARKS = frozenset(
    "\u2713\u2714\u2705\u2611\u2612\U0001f5f8\U0001f5f9\u2605\u2606\u2b50\U0001f31f\u2729\u272a\u272b\u272c\u272d"
    "\u272e\u272f\u2730\U0001f3c5\U0001f396\U0001f3c6\U0001f947\U0001f948\U0001f949\U0001f6e1\U0001f530\U0001f4af"
    "\U0001f451\u2122\u00ae")
# Letters that show as nothing. Variation selectors 15 and 16 only pick how an emoji is drawn.
_BLANK_LETTERS = frozenset({"\u034f", "\u115f", "\u1160", "\u17b4", "\u17b5", "\u2800", "\u3164", "\uffa0",
                            *(chr(code) for code in range(0xFE00, 0xFE0E)),
                            *(chr(code) for code in range(0xE0100, 0xE01F0))})
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})
# One word written in two alphabets: the usual way to make one name look like another.
_MIXED_WORD = re.compile(r"(?<!\w)(?=\w*[A-Za-z])(?=\w*[\u0370-\u03ff\u0400-\u052f])\w+")
# The marks a name or a maker's name may carry beside letters, numbers and spaces.
_NAME_MARKS = frozenset(" .,'\u2019&+-\u2013\u2014()!:/#")
_LINK = re.compile(r"//|\bwww\.", re.IGNORECASE)


def _hidden(text: str) -> bool:
    return any(unicodedata.category(char) in _HIDDEN_CATEGORIES or char in _BLANK_LETTERS for char in text)


def _read_forms(text: str) -> tuple[str, tuple[str, ...]]:
    """The text as a person reads it at a glance.

    Returns (letters, forms). ``letters`` is lower case with full-width and styled letters made
    plain, accents and other combining marks dropped and look-alike letters of other alphabets
    made Latin. ``forms`` are the same with digits and signs that stand in for letters made
    letters and everything that is not a letter or a number made one space.
    """
    from core.registry.checks import _LOOKALIKE

    plain = "".join(char for char in unicodedata.normalize("NFKC", str(text or ""))
                    if unicodedata.category(char)[0] != "M")
    letters = plain.translate(_LOOKALIKE).translate(_MORE_LOOKALIKES).casefold()
    spaced = _NOT_A_LETTER.sub(" ", _APOSTROPHES.sub("", letters).translate(_STAND_INS)).strip()
    return letters, tuple(dict.fromkeys((spaced.replace("1", "l"), spaced.replace("1", "i"))))


def display_refusal(text: Any, *, name: bool = True) -> str:
    """Why this text may not be shown as a plugin's name, maker or key label; "" when it may.

    ``name`` is for a name or a maker's name, which also keep to plain letters, numbers, spaces
    and a few marks. A key label may use more punctuation and is held to everything else.
    """
    if not isinstance(text, str):
        return ""   # the manifest rules say what a wrong type is; nothing is shown from it
    if _hidden(text):
        return PLAIN_TEXT
    if any(char in _BADGE_MARKS for char in text):
        return VOUCHING
    letters, forms = _read_forms(text)
    if "parad0x" in letters or _VOOL.search(letters) or any(_OWN_NAMES.search(form) for form in forms):
        return OWN_NAME
    if any(_VOUCHING_WORDS.search(form) for form in forms):
        return VOUCHING
    from core.registry.checks import badge_label

    if badge_label(text):   # "Tested by VOOL" or "Editor's Choice" as VOOL shows them, in any shipped language
        return VOUCHING
    plain = unicodedata.normalize("NFKC", text)
    if _MIXED_WORD.search(plain):
        return PLAIN_TEXT
    if name and (_LINK.search(plain) or any(
            char not in _NAME_MARKS and unicodedata.category(char)[0] not in "LMN" for char in plain)):
        return PLAIN_TEXT
    return ""


@lru_cache(maxsize=1)
def _tool_names() -> frozenset[str]:
    from core import tool_registry
    from core.runtime_tool_contracts import runtime_tool_contracts

    names = {str(contract.intent).split(".", 1)[0].strip().lower() for contract in runtime_tool_contracts()}
    return frozenset(name for name in names | set(tool_registry._RESERVED_NAMESPACES) | _RUNTIME_NAMES if name)


def builtin_tool_names() -> frozenset[str]:
    """Every name VOOL's own tools start with ("email", "workspace", "skill" ...).

    Read from the tools the runtime has, so a tool added later is covered with no list to keep.
    A plugin's tools are named ``<plugin id>.<tool>``: a plugin with one of these ids would hand
    the model tools that read as VOOL's own.
    """
    return _tool_names()


def _shipped_ids() -> frozenset[str]:
    """The ids of the packs shipped inside VOOL, read from its own plugins folder."""
    from core.plugin_catalog import _plugin_identity, bundled_plugin_dirs

    return frozenset(name for pack in bundled_plugin_dirs() for name in (pack.name, _plugin_identity(pack)))


def reserved_id(plugin_id: Any) -> bool:
    """Whether a package from outside may not use this id.

    Kept back: the id of every pack VOOL ships, anything that is or starts with VOOL's or its
    maker's name (also written with digits in place of letters), and every name VOOL's own tools
    start with. An id that cannot be read at all is kept back too.
    """
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        return True
    clean = plugin_id.strip()
    letters, _forms = _read_forms(clean)
    if _VOOL.match(letters) or letters.translate(_STAND_INS).startswith("paradox"):
        return True
    lowered = clean.casefold()
    try:
        return lowered in builtin_tool_names() or lowered in {name.casefold() for name in _shipped_ids()}
    except Exception:
        return True   # the tools or the shipped packs could not be read: nothing is let through unseen


def identity_refusal(manifest: Any, *, digest: str = "") -> str:
    """The plain reason a package from outside may not be installed under the identity it claims; "" when it may.

    The one check every door that takes a package from outside asks before it reviews or installs
    anything. It reads the id, the name, the maker's name and the key labels. A package whose
    exact bytes are one of VOOL's own (``core.plugin_migration``) is VOOL's and is not refused.
    """
    return identity_note(manifest, digest=digest)[0]


_ID_FIX = ("Choose another id. Kept back: the ids of the packs VOOL ships, any id that starts with vool or parad0x, "
           "and the names VOOL's own tools start with (email, skill, workspace, memory and the rest).")
_TEXT_FIX = ("Change this field. It is plain text: letters, numbers, spaces and a few marks, with no VOOL or Skill "
             "Codex name, no word or mark that says it was checked or chosen, no hidden or look-alike letters, no link.")


def identity_note(manifest: Any, *, digest: str = "") -> tuple[str, dict]:
    """(the refusal sentence, the note for the plugin's maker: which field and what to change); ("", {}) when fine."""
    return next(iter(identity_notes(manifest, digest=digest)), ("", {}))


def identity_notes(manifest: Any, *, digest: str = "") -> list[tuple[str, dict]]:
    """Every identity refusal this setup file has, in the order ``identity_note`` meets them; [] when fine.

    The first is the one a door refuses with. The rest are for a maker's check, so one run names
    every field to change.
    """
    if not isinstance(manifest, dict):
        return []   # not a manifest at all: the reader that follows says so
    from core.plugin_migration import own_package, reserved_refusal

    block = manifest.get("vool") if isinstance(manifest.get("vool"), dict) else {}
    ids = [(where, value) for where, value in (("name", manifest.get("name")), ("vool.id", block.get("id")))
           if isinstance(value, str)]
    if any(own_package(plugin_id, digest) for _where, plugin_id in ids):
        return []
    found: list[tuple[str, dict]] = []
    for where, plugin_id in ids:
        refusal = reserved_refusal(plugin_id.strip(), digest)
        if refusal:
            found.append((refusal, {"where": where, "fix": _ID_FIX}))
    interface = manifest.get("interface") if isinstance(manifest.get("interface"), dict) else {}
    for where, shown in (("vool.name", block.get("name")), ("vool.publisher", block.get("publisher")),
                         ("interface.displayName", interface.get("displayName"))):
        refusal = display_refusal(shown)
        if refusal:
            found.append((refusal, {"where": where, "fix": _TEXT_FIX}))
    credentials = block.get("credentials") if isinstance(block.get("credentials"), dict) else {}
    for slot_name, slot in credentials.items():
        refusal = display_refusal(slot.get("label") if isinstance(slot, dict) else None, name=False)
        if refusal:
            where = "vool.credentials." + "".join(char for char in str(slot_name) if char.isalnum() or char in "_-")[:80] + ".label"
            found.append((refusal, {"where": where, "fix": _TEXT_FIX}))
    return found


_SHOWN_LIMIT = 60
_SENTENCE_END = re.compile(r"[.!?]\s")


def _full_stop(match: re.Match) -> str:
    """A full stop stays only where it ends a word or sits between two digits, so no address survives."""
    text, at = match.string, match.start()
    before, after = text[max(0, at - 1):at], text[at + 1:at + 2]
    return "." if not after or after.isspace() or (before.isdigit() and after.isdigit()) else " "


def _plain_words(text: str, marks: str) -> str:
    kept = "".join(char if (char in marks or unicodedata.category(char)[0] in "LMN") else " "
                   for char in unicodedata.normalize("NFKC", text))
    return " ".join(re.sub(r"\.", _full_stop, kept).split())


def shown_name(name: Any, plugin_id: Any = "") -> str:
    """A plugin's name as VOOL writes it into a sentence of its own; "" when nothing readable is left.

    The name as its maker wrote it is used only when it would be accepted at install today. It is
    then cut to plain letters, numbers, spaces and a few marks, to its first sentence and to sixty
    characters. Any other name is replaced by the plugin's id, cleaned the same way. With "" the
    caller says "a plugin".
    """
    text = str(name or "")
    if text.strip() and not display_refusal(text):
        shown = _plain_words(text, " .,'\u2019&+-()/")
        end = _SENTENCE_END.search(shown)
        shown = (shown[:end.start()] if end else shown)[:_SHOWN_LIMIT].strip(" .,'\u2019&+-/")
        if shown:
            return shown
    fallback = _plain_words(str(plugin_id or ""), "-_")[:_SHOWN_LIMIT].strip(" -_")
    return fallback if any(char.isalnum() for char in fallback) else ""


_QUOTES = re.compile("[\"\u201c\u201d\u201e\u201f\u00ab\u00bb\u2033\u2036]")
_STRUCTURE = re.compile(r"[\\{}\[\]<>`|]")
_FIELD_LABELS = re.compile(r"(?i)\b(tools?|(?:previous )?message|answer|question|reply)\s*:")


def prompt_data(text: Any, limit: int) -> str:
    """An author's words as one flat piece of text for a prompt, at most ``limit`` characters.

    The prompt writes each choice between double quotes on one line. So double quotes of every
    kind become an apostrophe; backslashes, braces, brackets and angle brackets, line breaks and
    control and hidden characters go; and the question's own field labels lose their colon. What
    is left can be read, and cannot end the quote it sits in or start a line or an example.
    """
    plain = unicodedata.normalize("NFKC", str(text or ""))
    plain = "".join(" " if unicodedata.category(char) in _HIDDEN_CATEGORIES or char in _BLANK_LETTERS else char
                    for char in plain)
    plain = _STRUCTURE.sub(" ", _QUOTES.sub("'", plain))
    plain = _FIELD_LABELS.sub(r"\1", plain)
    return " ".join(plain.split())[:max(0, int(limit))].rstrip()


__all__ = [
    "OWN_NAME",
    "PLAIN_TEXT",
    "VOUCHING",
    "builtin_tool_names",
    "display_refusal",
    "identity_note",
    "identity_notes",
    "identity_refusal",
    "prompt_data",
    "reserved_id",
    "shown_name",
]
