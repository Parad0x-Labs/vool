"""Local text evidence; no model or network path exists in this reader."""
from __future__ import annotations

import re
from pathlib import PurePath

from core.artifact_readers import image, read
from core.artifact_readers._types import ReaderFailed, ReaderRefused, ReaderUnavailable
from core.my_things.store import ThingsError

MAX_TEXT = 32000


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
        is_image = data.startswith((b"\x89PNG\r\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        result = image.read(data, name=name) if is_image else read(data, name=name, extension=extension)
    except ReaderUnavailable:
        return {"state": "unavailable", "text": "", "message": "Reading the words in this photo is not available on this computer. Enter its model or serial number instead."}
    except (ReaderFailed, ReaderRefused):
        return {"state": "unreadable", "text": "", "message": "The words in this file could not be read safely. Try a clearer photo, or enter them yourself."}
    text = "\n".join(unit.text for unit in result.units)
    ocr = result.meta.get("ocr", {})
    if not text.strip():
        return {"state": "unreadable", "text": "", "message": "No readable words were found. Try a clearer photo, or enter the model number."}
    if len(text) > MAX_TEXT or result.meta.get("truncated"):
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
