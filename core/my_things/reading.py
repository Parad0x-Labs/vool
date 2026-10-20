"""Local text evidence; no model or network path exists in this reader."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePath

from core.artifact_readers import image
from core.artifact_readers._types import ReaderFailed, ReaderRefused, ReaderResult, ReaderUnavailable, ReaderUnit
from core.my_things.store import ThingsError

MAX_TEXT = 32000


@contextmanager
def _reading_scratch(prefix):
    from core.my_things.store import _private
    from core.runtime_paths import active_data_dir

    parent = active_data_dir() / "my_things"
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private(parent, 0o700)
    root = parent / "read_scratch"
    root.mkdir(exist_ok=True, mode=0o700)
    _private(root, 0o700)
    scratch = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    try:
        yield scratch
    finally:
        shutil.rmtree(scratch)


def _read_document(data, name, extension):
    from core.runtime_paths import active_vool_home

    # The shared readers choose TMPDIR internally. Give only this operation its
    # own process environment and umask; never mutate server-wide temp settings.
    with _reading_scratch("document-") as scratch:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "core.my_things.read_worker", name, extension],
                input=data, capture_output=True,
                env=dict(os.environ, TMPDIR=str(scratch), VOOL_HOME=str(active_vool_home()), PYTHONDONTWRITEBYTECODE="1"),
                umask=0o077 if os.name == "posix" else -1, timeout=360, check=False,
            )
            if result.returncode:
                raise ReaderFailed("Local file reading failed.", code="reader_worker_failed")
            reading = json.loads(result.stdout)
            if reading.get("error") == "unavailable":
                raise ReaderUnavailable("Local file reading is unavailable.")
            if reading.get("error"):
                raise ReaderFailed("Local file reading failed.")
            return reading
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise ReaderFailed("Local file reading failed.", code="reader_worker_failed") from exc


def _read_image(data: bytes, name: str):
    """Use the existing confined OCR API with private, caller-owned scratch."""
    from core.artifact_readers._sandbox import extraction_budget
    from core.artifact_readers.vision_tool import OCR_MAX_EDGE, TOOL_VERSION, ocr_available, ocr_text_with_confidence
    width, height = image.dimensions(data)  # includes the existing decompression-bomb refusal
    if not ocr_available():
        raise ReaderUnavailable("Local photo reading is unavailable.", code="ocr_unavailable")
    try:
        from PIL import Image
    except ImportError:
        raise ReaderUnavailable("Local photo decoding is unavailable.", code="image_decoder_unavailable") from None
    if not width or not height:
        raise ReaderRefused("This photo cannot be decoded.", code="invalid_image")
    with _reading_scratch("photo-") as scratch:
        with extraction_budget(scratch=scratch):
            with Image.open(io.BytesIO(data)) as original:
                frame = original.convert("RGB")
                longest = max(frame.size)
                if longest > OCR_MAX_EDGE:
                    scale = OCR_MAX_EDGE / longest
                    frame = frame.resize((max(1, int(frame.width * scale)), max(1, int(frame.height * scale))), Image.Resampling.LANCZOS)
                path = scratch / "input.png"
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
                    frame.save(handle, format="PNG")
            text, facts = ocr_text_with_confidence(path, scratch=scratch)
            return ReaderResult(fmt="image", extractor=f"{facts['engine']} {TOOL_VERSION}",
                                units=(ReaderUnit(locator=f"text in {name}", kind="section", text=text),),
                                meta={"ocr": facts})


def read_local(data: bytes, name: str) -> dict:
    extension = PurePath(name).suffix.lower()
    if extension in {".txt", ".md", ".csv"}:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return {"state": "unreadable", "text": "", "message": "The words in this file could not be read. You can enter them yourself."}
        if "\x00" in text or len(text) > MAX_TEXT:
            return {"state": "unreadable", "text": "", "message": "This file could not be read completely. Use a smaller text file."}
        return {"state": "read", "text": text, "source": "attached text", "warnings": []}
    try:
        is_image = extension in {".png", ".jpg", ".jpeg", ".gif", ".webp"} or data.startswith((b"\x89PNG\r\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        if not is_image:
            return _read_document(data, name, extension)
        result = _read_image(data, name)
    except ReaderUnavailable:
        message = ("Reading the words in this photo is not available on this computer. Enter its model or serial number instead." if is_image
                   else "Reading the words in this file is not available on this computer. Enter the details yourself.")
        return {"state": "unavailable", "text": "", "message": message}
    except (ReaderFailed, ReaderRefused):
        return {"state": "unreadable", "text": "", "message": "The words in this file could not be read safely. Try a clearer photo, or enter them yourself."}
    return _summarize(result)


def _summarize(result):
    text = "\n".join(unit.text for unit in result.units)
    ocr = result.meta.get("ocr", {})
    if not text.strip():
        return {"state": "unreadable", "text": "", "message": "No readable words were found. Try a clearer photo, or enter the model number."}
    if len(text) > MAX_TEXT or result.meta.get("truncated") or result.omitted:
        return {"state": "unreadable", "text": "", "message": "This file could not be read completely. Use a smaller file."}
    if ocr and (float(ocr.get("mean_confidence", 0)) < 0.8 or ocr.get("low_confidence_lines", 0)):
        return {"state": "uncertain", "text": text, "message": "Some letters are unclear. Check these words before using them.", "warnings": list(result.warnings)}
    return {"state": "read", "text": text, "source": result.extractor, "warnings": list(result.warnings)}


def proposal(data: bytes, name: str) -> dict:
    reading = read_local(data, name)
    if reading["state"] != "read":
        return {"reading": reading, "fields": {}, "evidence": [], "requires_confirmation": True}
    fields, evidence = {}, []
    labels = {"name": "name", "item": "name", "maker": "maker", "brand": "maker", "model": "model", "serial": "serial", "bought on": "bought_on", "bought from": "bought_from", "price": "price", "warranty end": "warranty_end", "specs": "specs"}
    for line in reading["text"].splitlines():
        label, sep, value = line.partition(":")
        field = labels.get(label.strip().casefold())
        if sep and field and value.strip() and len(value.strip()) <= 250:
            fields[field] = value.strip()
            evidence.append({"field": field, "quote": line.strip()})
    return {"reading": reading, "fields": fields, "evidence": evidence, "requires_confirmation": True}


def _matching_line(value, text):
    # Preserve word boundaries. Punctuation between identifier components may differ,
    # but WH-1000 must never match WH-10000 or a prose substring.
    parts = re.findall(r"[^\W_]+", value, re.UNICODE)
    if len("".join(parts)) < 4:
        return ""
    pattern = r"(?<!\w)" + r"[\W_]*".join(re.escape(p) for p in parts) + r"(?!\w)"
    return next((line.strip() for line in text.splitlines() if re.search(pattern, line, re.IGNORECASE)), "")


def match_text(text: str, records: list[dict]) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ThingsError("bad_text", "Enter the words on the label.")
    candidates = []
    for record in records:
        evidence = []
        for field in ("serial", "model"):
            value = record.get(field, "")
            line = _matching_line(value, text) if value else ""
            if line:
                evidence.append({"field": field, "saved": value, "quote": line})
        if evidence:
            candidates.append({"id": record["id"], "name": record["name"], "evidence": evidence})
    state = "match" if len(candidates) == 1 else "choose" if candidates else "no_match"
    messages = {"match": "This matches a saved thing. Check the evidence below.", "choose": "More than one saved thing matches. Choose the one you mean.", "no_match": "I do not have a matching thing saved. You can add it."}
    return {"state": state, "candidates": candidates, "message": messages[state]}


def match_photo(data, name, records):
    reading = read_local(data, name)
    if reading["state"] != "read":
        return {"state": reading["state"], "candidates": [], "message": reading["message"], "reading": reading}
    return {**match_text(reading["text"], records), "reading": reading}
