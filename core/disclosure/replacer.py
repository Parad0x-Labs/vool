"""Deterministic, text-only replacement with a process-local reverse mapping.

This is best effort, never an anonymity guarantee. Callers must show the resulting
text for review. This module does not grant cloud access or change secret policy.
Maps belong to one preparation session, must never be serialized into a request,
and should be discarded when that session ends.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from core.secret_redaction import luhn_valid

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-]|\.[\w-])", re.UNICODE)
_IBAN = re.compile(r"(?<!\w)[A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]){11,30}(?!\w)", re.I)
_NUMBER = re.compile(r"(?<!\w)\+?\d(?:[\d ()\-.]*\d)?(?!\w)")
_STREET_WORD = r"[\w'’-]+"
_STREET_END = r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Lane|Ln\.?|Drive|Dr\.?|Boulevard|Blvd\.?)"
_ADDRESS = re.compile(
    rf"\b(?:\d{{1,5}}[A-Za-z]? +(?:{_STREET_WORD} +){{1,5}}{_STREET_END}"
    rf"|(?:{_STREET_WORD} +){{1,5}}{_STREET_END} +\d{{1,5}}[A-Za-z]?)(?!\w)", re.I
)
_PLACEHOLDER = re.compile(r"\[(?:Person|Service|Email|Phone|Bank account|Card|Address) \d+\]")
_CHECK_PREVIEW = "Best effort: check the preview. Unrecognized details may still be present."


@dataclass(frozen=True)
class Replacement:
    placeholder: str
    original: str = field(repr=False)


@dataclass(frozen=True)
class PreparedText:
    texts: tuple[str, ...]
    replacements: tuple[Replacement, ...] = field(repr=False)
    warnings: tuple[str, ...] = (_CHECK_PREVIEW,)

    def restore(self, answer: str) -> str:
        """Restore only this session's exact placeholders in one pass.

        Single-pass substitution prevents an original containing a placeholder
        from being interpreted as another replacement. Unknown tokens stay put.
        """
        originals = {item.placeholder: item.original for item in self.replacements}
        return _PLACEHOLDER.sub(lambda match: originals.get(match[0], match[0]), answer)


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: str


def _iban_valid(value: str) -> bool:
    compact = value.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34 or not compact.isascii():
        return False
    rearranged = compact[4:] + compact[:4]
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    return int(digits) % 97 == 1


def _spans(text: str, names: tuple[str, ...], services: tuple[str, ...]) -> list[_Span]:
    candidates = [_Span(m.start(), m.end(), "Email") for m in _EMAIL.finditer(text)]
    unknown_accounts: list[tuple[int, int]] = []
    for match in _IBAN.finditer(text):
        # Grouped account numbers can be followed by ordinary words. Try only
        # complete-word endings rather than letting greedy matching hide an
        # otherwise valid account. Unknown formats remain visible for review.
        for end in range(match.end(), match.start() + 14, -1):
            if end < len(text) and text[end].isalnum():
                continue
            value = text[match.start():end]
            if not value.endswith(" ") and _iban_valid(value):
                candidates.append(_Span(match.start(), end, "Bank account"))
                break
        else:
            unknown_accounts.append((match.start(), match.end()))
    for match in _ADDRESS.finditer(text):
        candidates.append(_Span(match.start(), match.end(), "Address"))
    for match in _NUMBER.finditer(text):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", match[0]):
            continue
        digits = re.sub(r"\D", "", match[0])
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            kind = "Card"
        elif 7 <= len(digits) <= 15:
            kind = "Phone"
        else:
            continue
        candidates.append(_Span(match.start(), match.end(), kind))
    # Full contact names win over a shorter overlapping alias. Only names the
    # local contacts authority supplied are eligible; prose never defines names.
    for kind, known_names in (("Person", names), ("Service", services)):
        for name in known_names:
            for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
                candidates.append(_Span(match.start(), match.end(), kind))
    # An email/address/account wins over a contained phone-like digit sequence
    # or contact name. Then choose the longest name at a shared boundary.
    priority = {"Email": 0, "Bank account": 0, "Address": 0, "Card": 1, "Phone": 2, "Person": 3, "Service": 3}
    accepted: list[_Span] = []
    reserved = [(m.start(), m.end()) for m in _PLACEHOLDER.finditer(text)] + unknown_accounts
    for span in sorted(candidates, key=lambda s: (priority[s.kind], -(s.end - s.start), s.start)):
        if any(span.start < end and start < span.end for start, end in reserved):
            continue
        accepted.append(span)
        reserved.append((span.start, span.end))
    return sorted(accepted, key=lambda s: s.start)


def replace_texts(
    texts: Sequence[str], *, contact_names: Iterable[str] = (), service_names: Iterable[str] = (),
) -> PreparedText:
    """Prepare all sections together so placeholders stay stable across them.

    Literal pre-existing placeholder tokens are reserved before assigning new
    ones. Exact original spellings have separate placeholders so restoration
    cannot silently change capitalization or formatting. No filesystem or
    network access takes place.
    """
    if not isinstance(texts, Sequence) or isinstance(texts, (str, bytes)) or any(not isinstance(t, str) for t in texts):
        raise TypeError("texts must be a sequence of strings")
    sections = tuple(texts)
    if isinstance(contact_names, (str, bytes)):
        raise TypeError("contact_names must contain strings")
    supplied_names = tuple(contact_names)
    if any(not isinstance(n, str) for n in supplied_names):
        raise TypeError("contact_names must contain strings")
    names = tuple(sorted({n.strip() for n in supplied_names if n.strip()},
                         key=lambda n: (-len(n), n)))
    if isinstance(service_names, (str, bytes)):
        raise TypeError("service_names must contain strings")
    supplied_services = tuple(service_names)
    if any(not isinstance(n, str) for n in supplied_services):
        raise TypeError("service_names must contain strings")
    services = tuple(sorted({n.strip() for n in supplied_services if n.strip()}, key=lambda n: (-len(n), n)))
    reserved = {m[0] for text in sections for m in _PLACEHOLDER.finditer(text)}
    counters: dict[str, int] = defaultdict(int)
    mapping: dict[tuple[str, str], str] = {}
    replacements: list[Replacement] = []
    output: list[str] = []
    for text in sections:
        chunks: list[str] = []
        end = 0
        for span in _spans(text, names, services):
            original = text[span.start:span.end]
            key = (span.kind, original)
            if key not in mapping:
                while True:
                    counters[span.kind] += 1
                    token = f"[{span.kind} {counters[span.kind]}]"
                    if token not in reserved:
                        break
                reserved.add(token)
                mapping[key] = token
                replacements.append(Replacement(token, original))
            chunks.extend((text[end:span.start], mapping[key]))
            end = span.end
        chunks.append(text[end:])
        output.append("".join(chunks))
    return PreparedText(tuple(output), tuple(replacements))


def replace_using_contacts(texts: Sequence[str]) -> PreparedText:
    """Read the active local contacts through their public API, then prepare text.

    A contacts-read failure propagates. A caller must not label an unchecked
    payload as having had contact names replaced.
    """
    from core.contacts.store import active_contacts

    names: list[str] = []
    services: list[str] = []
    for contact in active_contacts():
        target = names if contact["kind"] == "person" else services
        target.append(contact["display_name"])
        target.extend(contact.get("aliases", []))
    return replace_texts(texts, contact_names=names, service_names=services)
