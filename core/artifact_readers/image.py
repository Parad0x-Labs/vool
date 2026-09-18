"""Images: the pixels still go to a vision model; this adds the text those pixels contain.

The attachment door already delivers PNG/JPEG/GIF/WebP to a vision-capable model and already tells
the truth when the selected model cannot see them. That behaviour is not replaced here.

What this adds is OCR, and it earns its place on the cases where sending pixels alone quietly
fails: a screenshot of a stack trace, a photographed invoice, a table in a PNG. A vision model
*paraphrases* text in an image -- it will render a serial number close enough to look right and
wrong enough to be useless. OCR returns the characters. So both travel: the image for what it
looks like, the recognised text for what it says, each labelled as what it is.

And when the model cannot see images at all, OCR is the difference between "the image was not
read" and the operator's actual question being answered from the words in it -- with the omission
of the *visual* content still stated, because reading the text of a photograph is not seeing it.

The decompression-bomb guard sits here rather than in the door: a 200-byte PNG can declare
60000x60000 and only a decoder discovers it, so the check happens where decoding does.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

from ._limits import MAX_IMAGE_PIXELS, OCR_MAX_EDGE
from ._sandbox import Scratch, extraction_budget
from ._types import UNIT_SECTION, ReaderRefused, ReaderResult, ReaderUnavailable, ReaderUnit
from .vision_tool import TOOL_VERSION, ocr_available, ocr_text_with_confidence

#: Below this mean confidence the recognised text is reported as unreliable rather than as content.
_UNRELIABLE_MEAN = 0.45


def _pillow():
    try:
        from PIL import Image

        return Image
    except ImportError:
        return None


def decoders_available() -> dict[str, bool]:
    return {"image_ocr": ocr_available(), "image_dimensions": bool(_pillow())}


def dimensions(data: bytes) -> tuple[int, int]:
    """Width and height, refusing a declared pixel count no one should decode."""
    image_module = _pillow()
    if image_module is None:
        return (0, 0)
    previous = image_module.MAX_IMAGE_PIXELS
    try:
        # Pillow's own bomb guard warns by default; this makes it a refusal, at OUR limit.
        image_module.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        with image_module.open(io.BytesIO(data)) as handle:
            width, height = handle.size
    except Exception as exc:
        if type(exc).__name__ == "DecompressionBombError":
            raise ReaderRefused(
                f"That image declares more than {MAX_IMAGE_PIXELS:,} pixels; it was not decoded.",
                code="image_too_many_pixels",
                remediation="Attach a smaller image.",
                fmt="image",
            ) from exc
        return (0, 0)
    finally:
        image_module.MAX_IMAGE_PIXELS = previous
    if width * height > MAX_IMAGE_PIXELS:
        raise ReaderRefused(
            f"That image is {width}x{height} ({width * height:,} pixels), over the {MAX_IMAGE_PIXELS:,}-pixel limit; it was not decoded.",
            code="image_too_many_pixels",
            remediation="Attach a smaller image.",
            fmt="image",
        )
    return (width, height)


def _prepared_png(data: bytes, scratch) -> Any:
    """A bounded PNG copy for the recognizer; the original bytes are never modified."""
    image_module = _pillow()
    path = scratch / "ocr-input.png"
    if image_module is None:
        path.write_bytes(data)
        return path
    previous = image_module.MAX_IMAGE_PIXELS
    try:
        image_module.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        with image_module.open(io.BytesIO(data)) as handle:
            handle.load()
            frame = handle.convert("RGB")
            longest = max(frame.size)
            if longest > OCR_MAX_EDGE:
                scale = OCR_MAX_EDGE / longest
                frame = frame.resize((max(1, int(frame.width * scale)), max(1, int(frame.height * scale))), image_module.LANCZOS)
            frame.save(path, format="PNG")
    except Exception:
        path.write_bytes(data)
    finally:
        image_module.MAX_IMAGE_PIXELS = previous
    return path


def read(data: bytes, *, name: str = "image.png", media_type: str = "image/png") -> ReaderResult:
    """OCR one image. Raises ``ReaderUnavailable`` when no OCR engine exists on this machine."""
    digest = hashlib.sha256(data).hexdigest()
    width, height = dimensions(data)
    if not ocr_available():
        raise ReaderUnavailable(
            f"No text-recognition engine is available on this machine, so the words in {name} were not read.",
            code="ocr_unavailable",
            remediation="Install the Xcode Command Line Tools to enable reading text out of images.",
            fmt="image",
        )
    with Scratch("vool-image-") as scratch, extraction_budget(scratch=scratch.path):
        path = _prepared_png(data, scratch.path)
        text, facts = ocr_text_with_confidence(path, scratch=scratch.path)

    warnings: list[str] = []
    if not text.strip():
        warnings.append("No text was recognised in this image; it may contain no writing.")
    else:
        note = f"Text recognised by OCR (mean confidence {facts['mean_confidence']:.2f} over {facts['lines']} lines)."
        if facts["mean_confidence"] < _UNRELIABLE_MEAN:
            note += " Confidence is low: treat these characters as uncertain and say so."
        elif facts["low_confidence_lines"]:
            note += f" {facts['low_confidence_lines']} line(s) were low-confidence and may be misread."
        warnings.append(note)
        warnings.append("OCR reads characters, not pictures: it says nothing about what the image depicts.")
    unit = ReaderUnit(
        locator=f"text in {name}",
        kind=UNIT_SECTION,
        text=text,
        warnings=tuple(warnings[:1]),
        meta={"ocr": facts, "width": width, "height": height},
    )
    return ReaderResult(
        fmt="image",
        extractor=f"{facts['engine']} {TOOL_VERSION}",
        units=(unit,),
        warnings=tuple(warnings),
        source_sha256=digest,
        meta={"width": width, "height": height, "media_type": media_type, "ocr": facts},
    )


__all__ = ["decoders_available", "dimensions", "read"]
