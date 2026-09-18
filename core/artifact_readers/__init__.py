"""The declared reader registry: which formats this runtime can read, and who reads each one.

Format support is a *table*, not a pattern scattered across a picker, a validator and three
providers. One place answers "can this runtime read a .docx", and the attachment door, the
composer's accept list and the operator-facing capability report all read that same answer. Adding
a format is adding a row; a format whose decoder is missing on this machine reports itself
unavailable in the same table, with the reason.

The registry deliberately does not know about attachments, sessions, turns, models or budgets. It
takes bytes and returns what was found, addressed by where it was found. Everything about who may
see how much of that, and alongside which question, belongs to `core.chat_attachments`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import _sandbox, archive, audio, documents, image, office, pdf, video
from . import speech_tool as speech_tool  # re-exported: importing it registers the speech tool
from ._types import (
    UNIT_FRAME,
    UNIT_MEMBER,
    UNIT_PAGE,
    UNIT_SECTION,
    UNIT_SHEET,
    UNIT_SLIDE,
    UNIT_TRACK,
    ReaderError,
    ReaderFailed,
    ReaderRefused,
    ReaderResult,
    ReaderUnavailable,
    ReaderUnit,
)

#: Bumped when the shape of a derivative changes, so a stored derivative is never read under the
#: wrong assumptions after an upgrade.
REGISTRY_VERSION = "1.2.0"


@dataclass(frozen=True)
class ReaderSpec:
    """One row of the table: a format, how to recognise it, how to read it, what it needs."""

    fmt: str
    label: str
    media_type: str
    extensions: tuple[str, ...]
    sniff: Callable[[bytes], bool]
    read: Callable[..., ReaderResult]
    #: Names the operator would recognise for what must exist on the machine. Empty = always works.
    requires: tuple[str, ...] = ()
    availability: Callable[[], bool] = lambda: True
    #: True when this format is read by spawning an external decoder. Such a format is available
    #: ONLY where the decoder can be confined; see `core.artifact_readers._sandbox`.
    external_decoder: bool = False
    #: What the format's units are addressed by. Drives the citation vocabulary in the prompt.
    unit_kind: str = UNIT_SECTION
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def available(self, *, passive: bool = False) -> bool:
        """A format is available only if something can read it AND that reader can be confined.

        The second half is not a formality. Without it a machine with no sandbox would report PDF
        as supported, accept a PDF at the door, and hand attacker-chosen bytes to a parser with the
        operator's home directory readable -- which is precisely the failure this flag prevents.
        """
        if self.external_decoder and not _sandbox.confinement_available():
            return False
        try:
            if passive and self.fmt == "audio":
                return speech_tool.speech_available(probe_if_needed=False)
            return bool(self.availability())
        except Exception:
            return False

    def blocked_reason(self, *, available: bool | None = None) -> str:
        if self.available() if available is None else available:
            return ""
        if self.external_decoder and not _sandbox.confinement_available():
            return _sandbox.confinement_unavailable_reason()
        if self.fmt == "audio":
            return "local on-device speech recogniser unavailable or not yet checked; use dictation to check"
        return f"missing {' and '.join(self.requires) or 'a decoder'}"


# Availability is resolved through the MODULE on every call, never captured as a bound function at
# import time. A decoder can appear or disappear between one page load and the next -- the Xcode
# tools get installed, ffmpeg gets removed -- and a registry that answered from an import-time
# snapshot would keep offering a format nothing can read, or keep hiding one that now works.
def _pdf_available() -> bool:
    return bool(pdf.decoders_available()["text_layer"])


def _doc_available() -> bool:
    return bool(office.doc_converter_available())


def _rar_available() -> bool:
    return bool(archive.decoders_available()["rar"])


def _video_available() -> bool:
    return bool(video.decoders_available()["video"])


def _xls_available() -> bool:
    return documents.xlrd_available()


def _audio_available() -> bool:
    return audio.decoders_available()["speech"]


REGISTRY: tuple[ReaderSpec, ...] = (
    ReaderSpec(
        fmt="pdf",
        external_decoder=True,
        label="PDF",
        media_type="application/pdf",
        extensions=(".pdf",),
        sniff=pdf.sniff,
        read=pdf.read,
        requires=("a PDF decoder (PDFKit or pypdf)",),
        availability=_pdf_available,
        unit_kind=UNIT_PAGE,
        notes="Native text where the PDF has a text layer; rendered-page OCR where it does not.",
    ),
    ReaderSpec(
        fmt="docx",
        label="Word document (.docx)",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        extensions=(".docx",),
        sniff=office.sniff_docx,
        read=office.read_docx,
        unit_kind=UNIT_SECTION,
        notes="Paragraphs, headings and tables. Macros and embedded objects are reported, never run.",
    ),
    ReaderSpec(
        fmt="doc",
        external_decoder=True,
        label="Legacy Word document (.doc)",
        media_type="application/msword",
        extensions=(".doc",),
        sniff=office.sniff_doc,
        read=office.read_doc,
        requires=("the macOS textutil converter",),
        availability=_doc_available,
        unit_kind=UNIT_SECTION,
        notes="Converted by the system converter in a sandbox with no network.",
    ),
    ReaderSpec(
        fmt="xlsx",
        label="Excel workbook (.xlsx)",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        extensions=(".xlsx",),
        sniff=documents.sniff_xlsx,
        read=documents.read_xlsx,
        unit_kind=UNIT_SHEET,
        notes="Sheets as cell-addressed grids. Formulas are reported with their cached values, never run.",
    ),
    ReaderSpec(
        fmt="pptx",
        label="PowerPoint presentation (.pptx)",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        extensions=(".pptx",),
        sniff=documents.sniff_pptx,
        read=documents.read_pptx,
        unit_kind=UNIT_SLIDE,
        notes="Slides in deck order, titles and speaker notes included. Links and media are not fetched.",
    ),
    ReaderSpec(
        fmt="odt",
        label="OpenDocument text (.odt)",
        media_type="application/vnd.oasis.opendocument.text",
        extensions=(".odt",),
        sniff=documents.sniff_odt,
        read=documents.read_odt,
        unit_kind=UNIT_SECTION,
        notes="Headings, paragraphs and tables. Basic macros and embedded objects are reported, never run.",
    ),
    ReaderSpec(
        fmt="epub",
        label="EPUB e-book (.epub)",
        media_type="application/epub+zip",
        extensions=(".epub",),
        sniff=documents.sniff_epub,
        read=documents.read_epub,
        unit_kind=UNIT_SECTION,
        notes="Sections in the book's spine order, headings kept. DRM-encrypted books are refused.",
    ),
    ReaderSpec(
        fmt="xls",
        label="Legacy Excel workbook (.xls)",
        media_type="application/vnd.ms-excel",
        extensions=(".xls",),
        sniff=documents.sniff_xls,
        read=documents.read_xls,
        requires=("the pinned xlrd decoder (xlrd==2.0.1)",),
        availability=_xls_available,
        unit_kind=UNIT_SHEET,
        notes="Read in process by the pinned pure-Python xlrd. Formulas marked with their cached values; macros reported, never run.",
    ),
    ReaderSpec(
        fmt="rtf",
        label="Rich Text Format (.rtf)",
        media_type="application/rtf",
        extensions=(".rtf",),
        sniff=documents.sniff_rtf,
        read=documents.read_rtf,
        unit_kind=UNIT_SECTION,
        notes="Body paragraphs; embedded objects and pictures are counted, never decoded.",
    ),
    ReaderSpec(
        fmt="zip",
        label="ZIP archive",
        media_type="application/zip",
        extensions=(".zip",),
        sniff=lambda data: archive.sniff(data) == "zip",
        read=archive.read,
        unit_kind=UNIT_MEMBER,
        notes="Every member listed; selected members read. Traversal, links and bombs refused.",
    ),
    ReaderSpec(
        fmt="rar",
        external_decoder=True,
        label="RAR archive",
        media_type="application/vnd.rar",
        extensions=(".rar",),
        sniff=lambda data: archive.sniff(data) == "rar",
        read=archive.read,
        requires=("a RAR-capable libarchive (bsdtar)",),
        availability=_rar_available,
        unit_kind=UNIT_MEMBER,
        notes="Every member listed; selected members read to memory, never written to disk.",
    ),
    ReaderSpec(
        fmt="audio",
        external_decoder=True,
        label="Audio",
        media_type="audio/x-wav",
        extensions=audio.EXTENSIONS,
        sniff=audio.sniff,
        read=audio.read,
        requires=("the local on-device speech recogniser (macOS Speech framework)",),
        availability=_audio_available,
        unit_kind=UNIT_TRACK,
        notes="Transcribed locally, on device only, bounded in time and characters. Never sent to a cloud service.",
    ),
    ReaderSpec(
        fmt="video",
        external_decoder=True,
        label="Video",
        media_type="video/mp4",
        extensions=tuple(sorted(ext for exts in video.VIDEO_TYPES.values() for ext in exts)),
        sniff=lambda data: bool(video.sniff(data)),
        read=video.read,
        requires=("ffmpeg",),
        availability=_video_available,
        unit_kind=UNIT_FRAME,
        notes="Timestamped frames sampled across the whole clip. Audio is not transcribed.",
    ),
)

_BY_FORMAT = {spec.fmt: spec for spec in REGISTRY}
#: Extensions this registry claims. The door consults it AFTER its own text/image tables, so an
#: existing type never changes hands.
_BY_EXTENSION = {extension: spec for spec in REGISTRY for extension in spec.extensions}


def spec_for_format(fmt: str) -> ReaderSpec | None:
    return _BY_FORMAT.get(str(fmt or "").strip().lower())


def spec_for_extension(extension: str) -> ReaderSpec | None:
    return _BY_EXTENSION.get(str(extension or "").strip().lower())


def detect(data: bytes, *, extension: str = "") -> ReaderSpec | None:
    """Which reader owns these bytes. **Bytes decide**; the extension only orders the candidates.

    A ``.pdf`` that is really a ZIP is read as a ZIP or refused -- never read as the thing its name
    claimed. That is the same rule the attachment door already applies to images and text, kept
    identical here so one type authority governs every format.
    """
    candidate = spec_for_extension(extension)
    ordered = [candidate, *[spec for spec in REGISTRY if spec is not candidate]] if candidate else list(REGISTRY)
    for spec in ordered:
        if spec is None:
            continue
        try:
            if spec.sniff(bytes(data)):
                return spec
        except Exception:
            continue
    return None


def read(data: bytes, *, name: str = "", extension: str = "", **kwargs: Any) -> ReaderResult:
    """Read bytes with whichever registered reader owns them.

    Raises ``ReaderUnavailable`` when the format is recognised but nothing on this machine can
    decode it -- which is a BLOCKED format, never an implemented one that returned nothing.
    """
    spec = detect(data, extension=extension)
    if spec is None:
        raise ReaderUnavailable(
            f"{name or 'That file'} is not a format this runtime reads.",
            code="unsupported_format",
            fmt="",
        )
    if not spec.available():
        if spec.external_decoder and not _sandbox.confinement_available():
            # Refuse for the REAL reason. "Install ffmpeg" would send the operator to fix a
            # decoder that is present and working; what is missing is the ability to confine it.
            raise ReaderUnavailable(
                f"{name or 'That file'} is {spec.label}, and {_sandbox.confinement_unavailable_reason()}. "
                f"It was not read: this runtime does not run untrusted document parsers unconfined.",
                code="confinement_unavailable",
                remediation="Use a macOS host where the sandbox is available, or attach the content as text.",
                fmt=spec.fmt,
            )
        raise ReaderUnavailable(
            f"{name or 'That file'} is {spec.label}, and this machine is missing {' and '.join(spec.requires) or 'its decoder'}, so it was not read.",
            code=f"{spec.fmt}_decoder_unavailable",
            remediation=f"Install {' and '.join(spec.requires)} to read {spec.label} attachments.",
            fmt=spec.fmt,
        )
    accepted = {"name": name or f"file{spec.extensions[0]}"}
    for key, value in kwargs.items():
        accepted[key] = value
    return spec.read(data, **_filtered(spec, accepted))


def _filtered(spec: ReaderSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    """Only the keywords this reader actually takes; a caller may pass the union of all of them."""
    import inspect

    try:
        parameters = inspect.signature(spec.read).parameters
    except (TypeError, ValueError):
        return {"name": arguments.get("name", "")}
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return arguments
    return {key: value for key, value in arguments.items() if key in parameters}


def capability_report() -> dict[str, Any]:
    """What this machine can actually read, right now, with the reason for anything it cannot.

    This is the one place an operator-facing answer to "can you read RAR files?" comes from, and
    the one place the composer's accept list is derived from. Neither guesses.
    """
    formats: list[dict[str, Any]] = []
    for spec in REGISTRY:
        available = spec.available(passive=True)
        formats.append(
            {
                "format": spec.fmt,
                "label": spec.label,
                "media_type": spec.media_type,
                "extensions": list(spec.extensions),
                "available": available,
                "unit_kind": spec.unit_kind,
                "requires": list(spec.requires),
                "notes": spec.notes,
                "external_decoder": spec.external_decoder,
                "blocked_reason": spec.blocked_reason(available=available),
            }
        )
    return {
        "registry_version": REGISTRY_VERSION,
        # Stated once, at the top: everything below that spawns a decoder depends on it.
        "confinement_available": _sandbox.confinement_available(),
        "confinement_unavailable_reason": _sandbox.confinement_unavailable_reason(),
        "formats": formats,
        "available_extensions": sorted(ext for row in formats if row["available"] for ext in row["extensions"]),
        "blocked_formats": [row["format"] for row in formats if not row["available"]],
        "ocr_available": image.decoders_available()["image_ocr"],
    }


def available_extensions() -> list[str]:
    return sorted(extension for spec in REGISTRY if spec.available() for extension in spec.extensions)


def media_type_for_extension(extension: str) -> str:
    spec = spec_for_extension(extension)
    if spec is None:
        return ""
    if spec.fmt == "video":
        return video.media_type_for_extension(extension) or spec.media_type
    return spec.media_type


__all__ = [
    "REGISTRY",
    "REGISTRY_VERSION",
    "UNIT_FRAME",
    "UNIT_MEMBER",
    "UNIT_PAGE",
    "UNIT_SECTION",
    "UNIT_SHEET",
    "UNIT_SLIDE",
    "ReaderError",
    "ReaderFailed",
    "ReaderRefused",
    "ReaderResult",
    "ReaderSpec",
    "ReaderUnavailable",
    "ReaderUnit",
    "archive",
    "available_extensions",
    "capability_report",
    "detect",
    "documents",
    "image",
    "media_type_for_extension",
    "office",
    "pdf",
    "read",
    "spec_for_extension",
    "spec_for_format",
    "video",
]
