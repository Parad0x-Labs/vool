"""Who a Drop is with: contact cards, key tags, safety numbers and name rules.

A person is their key. Its fingerprint is the only identity Drop trusts; a name is a
label the owner of this computer gives to one key, and it is never taken from the wire
as proof of anything. Three things are derived from the fingerprint alone, so nobody
can choose them:

* the **card**: a link that carries the fingerprint and the name its owner suggests;
* the **tag**: three words shown next to the name everywhere, so two people who were
  given similar names never look the same;
* the **safety number**: twenty digits the two people read to each other once (on a
  call or in person). Digits read the same in every language.

The tag is short (33 bits) and only tells contacts apart at a glance; the safety number
(66 bits) is what verification compares. There is no directory and no server, so a name
is unique on this computer, not in the world: one name maps to one key, a second key
under the same or a look-alike name is refused, and that refusal is the warning.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from core.drop.session import DropError, unb64

CARD_PREFIX = "vool-drop://card/"
NAME_LIMIT = 40
_FINGERPRINT = re.compile(r"[A-Za-z0-9_-]{43}")


def check_fingerprint(value) -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise DropError("INVALID_PEER")
    unb64(value, 32)
    return value


@lru_cache(maxsize=1)
def _words() -> tuple[str, ...]:
    path = Path(__file__).resolve().parent.parent / "wallet" / "bip39_english.txt"
    words = tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if len(words) != 2048:
        raise DropError("WORDLIST_UNAVAILABLE")
    return words


def _bits(label: str, fingerprint: str) -> int:
    return int.from_bytes(hashlib.sha256((label + "\0" + check_fingerprint(fingerprint)).encode()).digest(), "big")


def tag(fingerprint: str) -> str:
    """Three words for this key, e.g. ``river-lamp-stone``."""
    value = _bits("vool-drop-tag-v1", fingerprint) >> (256 - 33)
    words = _words()
    return "-".join(words[(value >> shift) & 2047] for shift in (22, 11, 0))


def safety_number(fingerprint: str) -> str:
    """Twenty digits in four groups for this key."""
    digits = f"{(_bits('vool-drop-safety-v1', fingerprint) >> (256 - 66)) % 10**20:020d}"
    return " ".join(digits[i:i + 5] for i in range(0, 20, 5))


def clean_name(value) -> str:
    """A display name as typed: no control or invisible characters, one line, bounded."""
    from core.contacts.names import clean_display

    if not isinstance(value, str):
        raise DropError("INVALID_NAME")
    from core.contacts.identity import unsafe_characters

    # Direction controls and invisible characters are removed, never kept: they can make
    # one name display as another.
    name = clean_display("".join(ch for ch in value if not unsafe_characters(ch)))
    if not name or len(name) > NAME_LIMIT:
        raise DropError("INVALID_NAME")
    return name


def name_relation(first: str, second: str) -> str:
    """``same``, ``lookalike`` or ``''`` for two display names."""
    from core.contacts.identity import MATCH_SAME, compare, identity_keys

    found = compare(identity_keys(first), identity_keys(second))
    if found is None:
        return ""
    return "same" if found == MATCH_SAME else "lookalike"


def make_card(fingerprint: str, name: str) -> str:
    body = json.dumps({"v": 1, "fp": check_fingerprint(fingerprint), "name": name},
                      ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return CARD_PREFIX + base64.urlsafe_b64encode(body).decode().rstrip("=")


def read_card(card) -> tuple[str, str]:
    """(fingerprint, suggested name) from a card. The name is only a suggestion."""
    if not isinstance(card, str):
        raise DropError("INVALID_CARD")
    text = "".join(card.split())
    if not text.startswith(CARD_PREFIX) or len(text) > 1024:
        raise DropError("INVALID_CARD")
    encoded = text[len(CARD_PREFIX):]
    try:
        value = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DropError("INVALID_CARD") from exc
    if not isinstance(value, dict) or set(value) != {"v", "fp", "name"} or value["v"] != 1:
        raise DropError("INVALID_CARD")
    try:
        fingerprint = check_fingerprint(value["fp"])
    except DropError as exc:
        raise DropError("INVALID_CARD") from exc
    try:
        suggested = clean_name(value["name"]) if value["name"] else ""
    except DropError:
        suggested = ""
    return fingerprint, suggested
