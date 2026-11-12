"""How VOOL picks up the operator's name without being told to in a settings field.

A person learns a name three ways, and so does VOOL:

1. **They say it.** "My name is Rick", "call me Rick", "I'm Rick, can you help me ..." -- read by
   :mod:`core.operator_profile_interpretation`, applied by :mod:`core.operator_profile_turn`.
   This module only answers one question for that path: which part of a message is the
   operator's OWN voice, and which part is material they pasted (:func:`own_voice_spans`).
2. **It is on their mail.** A message in the operator's own mailbox addressed to their own address
   carries the name people use for them (:func:`name_from_own_mail`).
3. **It is in what they paste.** A pasted letter greets someone and is signed by someone, and
   nothing in the letter says which of the two is the operator. So a pasted name is only ever
   EVIDENCE, and it is believed only when it agrees with something this machine already knows
   about its owner -- the full name on the computer account (:func:`name_from_pasted_text`).

Everything here is pure reading: no store, no network, no model. The one writer is
``core.operator_profile.learn_name``, which fills an empty name and nothing else -- a name the
operator stated, set in Settings, or dropped is never touched by a guess.
"""
from __future__ import annotations

import os
import re
from email.utils import getaddresses

__all__ = [
    "PASTED_ROLE_SALUTATION",
    "PASTED_ROLE_SIGNOFF",
    "account_given_name",
    "given_name_from_display",
    "has_pasted_material",
    "name_from_own_mail",
    "name_from_pasted_text",
    "own_voice_spans",
    "pasted_name_observations",
]

PASTED_ROLE_SALUTATION = "salutation"
PASTED_ROLE_SIGNOFF = "signoff"

#: A message this long, or with this many lines, is carrying something the operator pasted.
_PASTE_CHARS = 600
_PASTE_LINES = 4
#: In a message with pasted material, only a first or last line this short can be the operator
#: speaking for themselves.
_OWN_VOICE_LINE_CHARS = 80

_NAME = r"[A-Z][^\W\d_]{1,30}(?:[-'][^\W\d_]{1,30})?"
_HEADER_LINE_RE = re.compile(r"^\s{0,8}(?:from|to|cc|bcc|subject|sent|date|reply-to)\s{0,4}:\s{0,8}\S", re.IGNORECASE)
_QUOTE_LINE_RE = re.compile(r"^\s{0,8}(?:>|```|\|)")
_SALUTATION_RE = re.compile(
    r"^\s{0,8}(?:hi|hello|hey|dear|good\s{1,4}(?:morning|afternoon|evening)|greetings)\s{1,4}(?:(?:mr|mrs|ms|miss|dr|prof)\.?\s{1,4})?"
    r"(?P<v>" + _NAME + r")(?:\s{1,4}" + _NAME + r")?\s{0,4}[,:!–—-]?\s{0,4}$",
    re.IGNORECASE,
)
_SIGNOFF_RE = re.compile(
    r"^\s{0,8}(?:(?:best|kind|warm|warmest)(?:\s{1,4}(?:regards|wishes))?|regards|(?:many\s{1,4})?thanks(?:\s{1,4}(?:a\s{1,4}lot|again|so\s{1,4}much))?|"
    r"thank\s{1,4}you|cheers|sincerely|yours(?:\s{1,4}(?:sincerely|truly|faithfully))?|talk\s{1,4}soon|take\s{1,4}care|all\s{1,4}the\s{1,4}best|"
    r"respectfully|love|br)\s{0,4}[,.!–—-]?\s{0,4}$",
    re.IGNORECASE,
)
_SIGNOFF_INLINE_RE = re.compile(
    r"^\s{0,8}(?:(?:best|kind|warm)(?:\s{1,4}regards)?|regards|thanks|cheers|sincerely)\s{0,4}[,–—-]\s{0,4}(?P<v>" + _NAME + r")(?:\s{1,4}" + _NAME + r")?\s{0,4}$",
    re.IGNORECASE,
)
_SIGNATURE_NAME_RE = re.compile(r"^\s{0,8}(?:[-–—]{1,2}\s{0,4})?(?P<v>" + _NAME + r")(?:\s{1,4}" + _NAME + r"){0,2}\s{0,4}$")
#: The name must really be capitalized: the salutation words are matched case-insensitively, the
#: name is not.
_CAPITALIZED_RE = re.compile(r"^[A-Z]")
_NOT_A_PERSON = frozenset({
    "all", "team", "everyone", "everybody", "there", "folks", "guys", "sir", "madam", "customer", "client", "user",
    "friend", "friends", "colleagues", "colleague", "support", "sales", "info", "admin", "hr", "billing", "hiring",
    "recruiter", "manager", "member", "applicant", "candidate", "subscriber", "valued", "both", "again", "you",
})
_GENERIC_ACCOUNT_WORDS = frozenset({
    "admin", "administrator", "user", "root", "owner", "guest", "ubuntu", "debian", "mac", "macbook", "imac", "home",
    "pc", "runner", "default", "test", "work", "office", "family", "shared", "system", "computer", "laptop", "me",
    "info", "support", "sales", "team", "mail", "contact", "hello", "noreply", "no-reply",
})


def _lines(text: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    cursor = 0
    for raw in str(text or "").splitlines(keepends=True):
        end = cursor + len(raw)
        if raw.strip():
            out.append((raw.rstrip("\r\n"), cursor, end))
        cursor = end
    return out


def has_pasted_material(text: str) -> bool:
    """True when a message carries more than the operator's own sentence or two: a long block,
    several lines, mail headers, quoted lines or a code fence."""
    raw = str(text or "")
    lines = _lines(raw)
    if len(raw) > _PASTE_CHARS or len(lines) >= _PASTE_LINES:
        return True
    return any(_HEADER_LINE_RE.match(line) or _QUOTE_LINE_RE.match(line) for line, _, _ in lines)


def own_voice_spans(text: str) -> list[tuple[int, int]] | None:
    """The character spans of ``text`` where a name statement is the operator's own.

    ``None`` means the whole message is their own voice (an ordinary short message). For a
    message with pasted material it is at most the first and the last line, and only when that
    line is short, is not a salutation, a sign-off, a mail header or a quoted line, and -- for
    the last line -- does not sit directly under a sign-off (that is a signature, not a remark).
    """
    raw = str(text or "")
    if not has_pasted_material(raw):
        return None
    lines = _lines(raw)
    spans: list[tuple[int, int]] = []
    for index in sorted({0, len(lines) - 1}):
        if index < 0 or index >= len(lines):
            continue
        line, start, end = lines[index]
        if len(line.strip()) > _OWN_VOICE_LINE_CHARS:
            continue
        if _HEADER_LINE_RE.match(line) or _QUOTE_LINE_RE.match(line) or _SALUTATION_RE.match(line) or _SIGNOFF_RE.match(line) or _SIGNOFF_INLINE_RE.match(line):
            continue
        if index and index == len(lines) - 1 and _SIGNOFF_RE.match(lines[index - 1][0]):
            continue
        spans.append((start, end))
    return spans


def _person(value: str) -> str:
    name = str(value or "").strip()
    if not name or not _CAPITALIZED_RE.match(name) or name.lower() in _NOT_A_PERSON or len(name) < 2:
        return ""
    return name


def pasted_name_observations(text: str) -> list[tuple[str, str]]:
    """``(given name, role)`` for every person a pasted letter greets or is signed by. Only a
    message that carries pasted material is read; an ordinary sentence yields nothing."""
    raw = str(text or "")
    if not has_pasted_material(raw):
        return []
    lines = _lines(raw)
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(name: str, role: str) -> None:
        clean = _person(name)
        key = (clean.lower(), role)
        if clean and key not in seen:
            seen.add(key)
            out.append((clean, role))

    for index, (line, _, _) in enumerate(lines):
        salutation = _SALUTATION_RE.match(line)
        if salutation:
            _add(salutation.group("v"), PASTED_ROLE_SALUTATION)
            continue
        inline = _SIGNOFF_INLINE_RE.match(line)
        if inline:
            _add(inline.group("v"), PASTED_ROLE_SIGNOFF)
            continue
        if _SIGNOFF_RE.match(line) and index + 1 < len(lines):
            signature = _SIGNATURE_NAME_RE.match(lines[index + 1][0])
            if signature:
                _add(signature.group("v"), PASTED_ROLE_SIGNOFF)
    return out


def given_name_from_display(display: str) -> str:
    """The given name inside a display name ("Dana Whitfield", "Whitfield, Dana", "dana w") or
    "" when it is not a person's name (an address, a role mailbox, an initial)."""
    text = " ".join(str(display or "").replace('"', " ").replace("'", " ").split())
    if not text or "@" in text or any(ch.isdigit() for ch in text):
        return ""
    if "," in text:
        family, _, given = text.partition(",")
        text = f"{given.strip()} {family.strip()}".strip()
    tokens = [tok for tok in re.split(r"[\s._]+", text) if tok]
    if not tokens or len(tokens) > 4:
        return ""
    first = tokens[0]
    if len(first) < 3 or not re.fullmatch(r"[^\W\d_]+(?:[-'][^\W\d_]+)?", first):
        return ""
    if any(tok.lower() in _GENERIC_ACCOUNT_WORDS or tok.lower() in _NOT_A_PERSON for tok in tokens):
        return ""
    return first if not first.islower() and not first.isupper() else first.capitalize()


def account_given_name() -> str:
    """The given name on this computer's user account, or "" when it does not hold a person's
    name. Read locally, used only to check a name seen in pasted text; it is never saved or shown
    by this function."""
    full = ""
    try:
        import pwd

        full = str(pwd.getpwuid(os.getuid()).pw_gecos or "").split(",", 1)[0].strip()
    except Exception:
        full = ""
    if not full or " " not in full:
        # A one-word account label ("sam", "admin", "studio") is a login name as often as a
        # person's name; only a first-and-last full name is treated as one.
        return ""
    return given_name_from_display(full)


def _agrees(observed: str, known: str) -> bool:
    """A pasted name agrees with the account name when it IS that name, or a short form of it
    ("Sam" for "Samantha"). A longer name ("Dana" for "Dan") is somebody else."""
    seen, known = observed.lower(), known.lower()
    if not seen or not known:
        return False
    return seen == known or (len(seen) >= 3 and known.startswith(seen))


def name_from_pasted_text(text: str, *, known_given_name: str | None = None) -> str:
    """The operator's name as a pasted letter spells it, or "" when the paste does not settle it.

    Believed only when exactly one person in the paste agrees with the name on the computer
    account. Two different people who both agree, or nobody who does, settles nothing.
    """
    known = account_given_name() if known_given_name is None else str(known_given_name or "")
    if not known:
        return ""
    matches = {name.lower(): name for name, _ in pasted_name_observations(text) if _agrees(name, known)}
    if len(matches) != 1:
        return ""
    return next(iter(matches.values()))


def name_from_own_mail(messages: list[dict] | None, own_addresses: list[str] | tuple[str, ...] | set[str]) -> str:
    """The name the operator's own mailbox shows for their own address, or "".

    Reads the ``to`` and ``from`` headers of messages VOOL has just read: wherever one of the
    operator's configured addresses appears with a display name, that display name is theirs.
    Every such appearance has to give the same given name; if two disagree nothing is learned.
    """
    own = {str(addr or "").strip().lower() for addr in (own_addresses or []) if "@" in str(addr or "")}
    if not own:
        return ""
    found: dict[str, str] = {}
    for message in list(messages or []):
        if not isinstance(message, dict):
            continue
        headers = [str(message.get(key) or "") for key in ("to", "from") if str(message.get(key) or "").strip()]
        for display, address in getaddresses(headers):
            if str(address or "").strip().lower() not in own:
                continue
            local = str(address).partition("@")[0].lower()
            given = given_name_from_display(display)
            if not given or given.lower() == local:
                continue
            found.setdefault(given.lower(), given)
    if len(found) != 1:
        return ""
    return next(iter(found.values()))
