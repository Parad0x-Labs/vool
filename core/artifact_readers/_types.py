"""The typed vocabulary every reader speaks: provenance-addressed units, and errors that say why.

Two rules shape everything here:

* **Extraction is not consumption.** A reader returns what it *found*, addressed by where it found
  it (page 3, member ``docs/a.txt``, ``t=12.500s``). Deciding which of that reaches a model, under
  which budget, alongside which question, belongs to the attachment door -- never to a reader.
* **An absent decoder is a typed answer, not a silent empty read.** ``ReaderUnavailable`` names the
  format that stayed unread and what would make it readable. A format that raises it is BLOCKED;
  it is never reported as an implemented format that happened to return nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Unit kinds. The locator vocabulary a receipt, a citation and a targeted re-read all share.
UNIT_PAGE = "page"
UNIT_MEMBER = "member"
UNIT_FRAME = "frame"
UNIT_SECTION = "section"
UNIT_SHEET = "sheet"
UNIT_SLIDE = "slide"
UNIT_TRACK = "track"


class ReaderError(Exception):
    """Base of every typed reader outcome that is not a successful extraction."""

    #: Stable machine code. Callers branch on this, never on the message.
    code = "reader_error"

    def __init__(self, message: str, *, code: str | None = None, remediation: str = "", fmt: str = "") -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.remediation = remediation
        self.format = fmt

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.code,
            "message": self.message,
            "remediation": self.remediation,
            "format": self.format,
        }


class ReaderUnavailable(ReaderError):
    """No decoder for this format on this machine. The format is BLOCKED, not implemented-and-empty.

    Raised *before* any byte is interpreted, so a caller can never mistake the result for a read
    that found nothing. ``remediation`` names the missing component in the operator's own terms.
    """

    code = "decoder_unavailable"


class ReaderRefused(ReaderError):
    """The bytes were reached and refused: password-protected, corrupt, hostile, or over a bound.

    A refusal is a fact about the file, and it is actionable: the operator learns which file, what
    was wrong with it, and what would let it through.
    """

    code = "reader_refused"


class ReaderFailed(ReaderError):
    """The decoder ran and failed. A sandbox kill, a timeout, an internal decoder fault."""

    code = "reader_failed"


@dataclass(frozen=True)
class ReaderUnit:
    """One provenance-addressed piece of a source document.

    ``locator`` is the human-and-machine address the model is shown and a re-read is asked for:
    ``page 3``, ``member reports/q3.csv``, ``t=12.500s``. It is the ONLY handle that travels; a
    reader never hands out a filesystem path, and never a byte offset a caller could mistake for
    one.
    """

    locator: str
    kind: str
    text: str = ""
    #: Extraction facts about THIS unit alone: OCR confidence, "no text layer", "table rebuilt from
    #: glyph positions". Rendered to the model verbatim, because an unstated uncertainty is a lie.
    warnings: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "locator": self.locator,
            "kind": self.kind,
            "text": self.text,
            "warnings": list(self.warnings),
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class ReaderResult:
    """Everything one reader found in one source, plus how it was found.

    ``extractor`` is a name and a version, recorded on the derivative so a later answer can be
    traced to the exact code that produced its evidence. ``source_sha256`` is the hash of the
    IMMUTABLE original -- the derivative never replaces it, and never claims to be it.
    """

    fmt: str
    extractor: str
    units: tuple[ReaderUnit, ...] = ()
    warnings: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)
    source_sha256: str = ""
    #: Units the reader deliberately did not extract (over a bound, or not asked for). Stated so
    #: "the whole document was read" is never assumed from a partial result.
    omitted: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not any(unit.text.strip() for unit in self.units)

    def unit(self, locator: str) -> ReaderUnit | None:
        wanted = str(locator or "").strip().lower()
        for candidate in self.units:
            if candidate.locator.lower() == wanted:
                return candidate
        return None

    def rendered_text(self) -> str:
        """The extraction as one text, each unit under its own locator heading.

        The heading is not decoration: it is the citation handle. A model asked "which page says
        X" can only answer from text that carries where each part came from.
        """
        blocks: list[str] = []
        for unit in self.units:
            head = f"[{unit.locator}]"
            if unit.warnings:
                head += " (" + "; ".join(unit.warnings) + ")"
            body = unit.text.strip("\n")
            blocks.append(f"{head}\n{body}" if body else f"{head}\n[no text extracted]")
        return "\n\n".join(blocks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.fmt,
            "extractor": self.extractor,
            "source_sha256": self.source_sha256,
            "units": [unit.as_dict() for unit in self.units],
            "warnings": list(self.warnings),
            "omitted": list(self.omitted),
            "meta": dict(self.meta),
        }
