"""The one text representation an entity span is allowed to mean something against.

A span is a pair of integers. Integers are silent when they are wrong: `(6, 11)` resolves to *some*
substring of *any* string long enough, so a span computed against one rendering of the user's
message and resolved against another returns a plausible wrong answer rather than an error. That is
the failure this module exists to make impossible, and it is not hypothetical in this runtime --
three different renderings of the same turn are already in flight at once:

* ``raw_input``       -- what the user typed, newlines intact (what `plan_conductor_turn` plans on)
* ``effective_input`` -- after interpretation/correction
* ``normalized_input`` -- after `core.input_normalizer` collapses whitespace

So a span here carries three things beside its offsets: the NAME of the representation it was
measured against, the length of that text, and a digest of it. `EntitySpan.resolve` re-checks all
three and raises rather than guessing. A span that cannot prove which string it belongs to is not a
span, it is two integers.

**The unit is a Python code point, never a UTF-8 byte.** `len("é")` is 1 in Python and 2 in UTF-8;
`len("👋")` is 1 in Python and 4 in UTF-8. Every offset here indexes the `str` directly, which is
what `text[start:end]` already does, so there is exactly one indexing convention in the system and
no conversion step that could be skipped. Byte offsets are rejected by construction: nothing in this
module ever encodes.

**Code points, not grapheme clusters.** A family emoji is one grapheme and several code points, and
a span may legally fall inside one. That is stated rather than fixed: the alternative is a
`regex`/ICU dependency for a boundary the resolver does not currently need, and an undocumented
grapheme convention would be a second silent indexing rule -- the exact defect class this file
closes.

Normalization is part of the identity, not a step applied on the way past. `CanonicalText.of`
returns NFC-normalized text under the name `user_text.nfc.v1`, because the same accented word
arrives as NFC from one keyboard and NFD from another, and the two disagree about every offset after
the accent. Pinning the form in the representation NAME means a span carried across a normalization
rewrite fails loudly instead of pointing one code point off.
"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Any

#: The canonical representation entity spans are measured against, everywhere in this package.
#: Versioned in the name: changing the normal form is a new representation, never a silent
#: reinterpretation of spans already written into a receipt.
CANONICAL_REPRESENTATION = "user_text.nfc.v1"

#: How much of the SHA-256 hex digest is carried. 16 hex chars is 64 bits -- enough that an
#: accidental collision between two renderings of one short turn is not a thing that happens, and
#: short enough to sit in a receipt without dominating it.
_DIGEST_CHARS = 16


class SpanBindingError(ValueError):
    """A span was resolved against text it was not measured against.

    Deliberately an exception and not a `None` return. A span that cannot be bound is a defect in
    whoever produced or carried it, and the one thing that must never happen is for it to yield a
    substring anyway -- that is how "the user asked about Paris" becomes "the user asked about aris".
    """


class _NeverRaisedError(Exception):
    """Never raised. Exists as a mutation anchor: swapping the UnicodeEncodeError handler for this
    is how the matrix proves the typed-refusal guard is load-bearing."""


class CanonicalTextError(ValueError):
    """Text that cannot be put into a canonical representation at all.

    Its own type so a caller sees a semantic failure rather than whatever the encoder happened to
    raise. `CanonicalText.of("\\ud800")` used to escape as a raw `UnicodeEncodeError` out of the
    digest -- a lone surrogate is not encodable as UTF-8 -- which meant a hostile input produced a
    codec error from three frames down instead of a typed refusal from this module.
    """


#: Normal forms this module will honour, keyed by the representation name that declares them. A
#: representation is a PROMISE about the text; serving a name whose form we do not apply is a lie in
#: the one field spans use to decide whether they may bind.
_REPRESENTATION_FORMS = {
    CANONICAL_REPRESENTATION: "NFC",
    "user_text.nfd.v1": "NFD",
    "user_text.nfkc.v1": "NFKC",
    "user_text.nfkd.v1": "NFKD",
    #: Deliberate escape hatch for a caller that has already normalized and wants no second pass.
    "user_text.raw.v1": "",
}


def _digest(text: str) -> str:
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]
    except UnicodeEncodeError as exc:
        # Lone surrogates (a truncated UTF-16 pair, a mangled paste) are valid `str` and invalid
        # UTF-8. Refused here rather than allowed to surface as a codec error from inside a digest.
        raise CanonicalTextError(
            f"text cannot be represented in UTF-8 and so has no canonical form: {exc}"
        ) from exc


@dataclass(frozen=True)
class CanonicalText:
    """A user message under one named, digested representation.

    Construct with `CanonicalText.of`. The digest is computed at construction and never recomputed
    from a mutated field, because the dataclass is frozen -- the identity of the text and the text
    itself cannot drift apart.

    **`of()` being safe was not enough.** A hostile review went around it: `CanonicalText(...)` is a
    public dataclass constructor, and it accepted NFC text labelled `user_text.nfd.v1`, NFD text
    labelled NFC, a representation name this module has never heard of, a lone surrogate, and a
    digest belonging to some other string entirely. Every span measured against such a value is
    wrong in the one field spans consult to decide whether they may bind, and nothing downstream can
    detect it. `__post_init__` now enforces the same contract on EVERY construction path rather than
    on the polite one -- which is the narrowest fix that makes the type's promise true.
    """

    representation: str
    text: str
    digest: str

    def __post_init__(self) -> None:
        form = _REPRESENTATION_FORMS.get(self.representation)
        if form is None:
            raise CanonicalTextError(
                f"unknown representation {self.representation!r}; a span binds on this name, so a "
                f"name with no declared normal form cannot be honoured"
            )
        # The text must ALREADY be in the form its name promises. Normalizing here instead would
        # silently rewrite the caller's text and leave the digest describing something else.
        # `user_text.raw.v1` declares no form on purpose -- it is the escape hatch for a caller that
        # has already normalized -- so there is nothing to check for it beyond the digest.
        if form and unicodedata.normalize(form, self.text) != self.text:
            raise CanonicalTextError(
                f"text is not in {form} but is labelled {self.representation!r}; a representation "
                f"is a promise about the text, and this one is not kept"
            )
        expected = _digest(self.text)
        if self.digest != expected:
            raise CanonicalTextError(
                "digest does not describe this text; an identity that belongs to another string "
                "makes every span measured against it resolve somewhere else"
            )

    @classmethod
    def of(cls, raw: Any, *, representation: str = CANONICAL_REPRESENTATION) -> CanonicalText:
        """Normalize `raw` into the NAMED representation and stamp its identity.

        `None`, numbers and anything else become their `str()` -- a caller handing this a non-string
        has a bug, but silently raising here would take a turn down with it, and an empty canonical
        text is empty and says so, rather than absent.

        The normal form comes from `representation`, not from a hard-coded NFC. It used to be
        hard-coded, so `CanonicalText.of(x, representation="user_text.nfd.v1")` NFC-normalized the
        text while labelling it NFD -- a hostile review's finding, and a bad one: the representation
        name is the single field a span consults to decide whether it may bind, so a name that
        misdescribes the form makes every span over that text wrong in a way nothing can detect.

        An unknown representation is refused rather than served under the default form. Guessing
        which normalization a caller meant is how the same lie comes back.
        """
        name = str(representation)
        if name not in _REPRESENTATION_FORMS:
            raise CanonicalTextError(
                f"unknown representation {name!r}; known: {sorted(_REPRESENTATION_FORMS)}. "
                "A representation names the normal form actually applied -- it cannot be invented."
            )
        form = _REPRESENTATION_FORMS[name]
        source = "" if raw is None else str(raw)
        text = unicodedata.normalize(form, source) if form else source
        return cls(representation=name, text=text, digest=_digest(text))

    @property
    def length(self) -> int:
        """Length in code points -- the same unit every span offset is in."""
        return len(self.text)

    @property
    def normal_form(self) -> str:
        """The Unicode normal form this representation declares, or "" for the raw form."""
        return _REPRESENTATION_FORMS.get(self.representation, "")

    def normalize(self, raw: Any) -> str:
        """Put `raw` into THIS text's form, so a search or comparison speaks the same language."""
        text = "" if raw is None else str(raw)
        form = self.normal_form
        return unicodedata.normalize(form, text) if form else text

    def span(self, start: int, end: int, *, label: str = "", kind: str = "") -> EntitySpan:
        """A span bound to THIS text. The only construction path that cannot be mis-bound."""
        return EntitySpan(
            start=int(start),
            end=int(end),
            representation=self.representation,
            text_digest=self.digest,
            text_length=self.length,
            label=str(label),
            kind=str(kind),
        )

    def find(self, needle: str, *, start: int = 0, label: str = "", kind: str = "") -> EntitySpan | None:
        """First occurrence of `needle` at or after `start`, as a bound span, or None.

        `needle` is NFC-normalized first, so looking for a word typed with a combining accent finds
        it in text that arrived precomposed. Repeated entities are why this takes `start`: "weather
        in Paris and Paris" is two spans that resolve to the same string and are not the same span.
        """
        # Normalized to THIS text's own form, not to a hard-coded NFC. A hostile review searched an
        # NFD haystack for "café" typed as NFC and got None: the needle was folded to NFC while the
        # haystack stayed decomposed, so the two could never match. A search must speak the
        # representation it is searching.
        probe = self.normalize(str(needle or ""))
        if not probe:
            return None
        index = self.text.find(probe, max(0, int(start)))
        if index < 0:
            return None
        return self.span(index, index + len(probe), label=label or probe, kind=kind)

    def find_all(self, needle: str, *, label: str = "", kind: str = "") -> tuple[EntitySpan, ...]:
        """Every non-overlapping occurrence, left to right. Each repeat gets its own distinct span."""
        spans: list[EntitySpan] = []
        cursor = 0
        while True:
            found = self.find(needle, start=cursor, label=label, kind=kind)
            if found is None:
                return tuple(spans)
            spans.append(found)
            # Advance past this match. A zero-width needle is impossible (`find` rejects empty), so
            # this always makes progress and the loop cannot spin.
            cursor = found.end

    def to_dict(self) -> dict[str, Any]:
        """Identity only -- the text itself is NOT included.

        A receipt records which message a span was measured against, not the message. The turn's
        text is already carried by the event ledger's own request preview, redacted there; copying
        it into every span record would duplicate user content into a second store under a second
        redaction policy.
        """
        return {
            "representation": self.representation,
            "digest": self.digest,
            "length": self.length,
        }


@dataclass(frozen=True)
class EntitySpan:
    """A half-open `[start, end)` code-point range over one named canonical text.

    Half-open to match `str` slicing exactly, so `canonical.text[span.start:span.end]` and
    `span.resolve(canonical)` cannot disagree about the boundary.
    """

    start: int
    end: int
    representation: str
    text_digest: str
    text_length: int
    #: What this span is about, in the user's own words. Carried for readability in a receipt; it is
    #: never the authority -- `resolve` reads the text, it does not trust this.
    label: str = ""
    #: The producer's classification ("location", "asset", ...). Free-form on purpose: a closed
    #: enum here would be a vocabulary the registry does not own, which is the calcification this
    #: architecture exists to avoid.
    kind: str = ""

    @property
    def is_well_formed(self) -> bool:
        """Whether the offsets could describe a range at all, before any text is consulted."""
        return 0 <= self.start <= self.end <= self.text_length

    def binds_to(self, canonical: CanonicalText) -> bool:
        """Whether this span was measured against exactly `canonical`."""
        return (
            self.representation == canonical.representation
            and self.text_digest == canonical.digest
            and self.text_length == canonical.length
        )

    def resolve(self, canonical: CanonicalText) -> str:
        """The substring this span names, or `SpanBindingError`.

        Every rejection names which of the three identity checks failed, because "span does not
        bind" during a Phase-1 debug session is the difference between "you passed the normalized
        text" and "the offsets are stale".
        """
        if self.representation != canonical.representation:
            raise SpanBindingError(
                f"span is measured against {self.representation!r}, "
                f"resolved against {canonical.representation!r}"
            )
        if self.text_length != canonical.length:
            raise SpanBindingError(
                f"span is measured against text of {self.text_length} code points, "
                f"resolved against {canonical.length}"
            )
        if self.text_digest != canonical.digest:
            raise SpanBindingError(
                f"span is measured against text {self.text_digest}, "
                f"resolved against {canonical.digest} -- same length, different text"
            )
        if not self.is_well_formed:
            raise SpanBindingError(
                f"span [{self.start}, {self.end}) is not a range within {self.text_length} code points"
            )
        return canonical.text[self.start : self.end]

    @property
    def binding(self) -> tuple[str, str, int]:
        """The complete identity a span is bound to: representation, digest AND length.

        All three, because all three are what `resolve` checks. `overlaps` used to compare only the
        first two, so two spans claiming texts of different lengths compared as comparable -- an
        inconsistency a hostile review found, and the kind that lets a stale span be reasoned about
        as if it belonged to the current turn.
        """
        return (self.representation, self.text_digest, self.text_length)

    def binds_to_same_text(self, other: EntitySpan) -> bool:
        return self.binding == other.binding

    def overlaps(self, other: EntitySpan) -> bool:
        """Half-open intersection, and only between spans over the same text.

        Two spans over different messages are not comparable, so this is False rather than a
        coincidence of integers.
        """
        if not self.binds_to_same_text(other):
            return False
        return self.start < other.end and other.start < self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "representation": self.representation,
            "text_digest": self.text_digest,
            "text_length": self.text_length,
            "label": self.label,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> EntitySpan | None:
        """Rebuild a span from a receipt row, or None when the row is not one.

        None rather than an exception: this reads persisted data written by an older build, and a
        receipt that cannot be fully parsed should degrade to "that span is unreadable", not take
        the reader down.
        """
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                start=int(payload["start"]),
                end=int(payload["end"]),
                representation=str(payload["representation"]),
                text_digest=str(payload["text_digest"]),
                text_length=int(payload["text_length"]),
                label=str(payload.get("label") or ""),
                kind=str(payload.get("kind") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


__all__ = [
    "CANONICAL_REPRESENTATION",
    "CanonicalText",
    "CanonicalTextError",
    "EntitySpan",
    "SpanBindingError",
]
