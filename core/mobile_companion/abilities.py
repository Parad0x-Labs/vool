"""Foreground phone abilities; output is a draft, never a dispatched turn."""

from __future__ import annotations

import base64
import binascii
import math
import re

from core.mobile_companion.policy import enabled

ABILITIES = frozenset({"camera", "photos", "location", "microphone"})
MAX_BYTES = 3 * 1024 * 1024


def check(params, *, service, device, **_kw):
    ability = params.get("ability")
    if ability not in ABILITIES:
        return 400, {"error": "Choose a phone ability.", "code": "unknown_phone_ability"}
    fresh = service.store.device(device["device_id"]) or {}
    if (
        fresh.get("status") != "active"
        or fresh.get("kind") == "bot"
        or not enabled(fresh, ability)
        or (ability in {"camera", "photos"} and not enabled(fresh, "attachments"))
    ):
        service.store.append_receipt(
            "security.phone_ability_refused", device_id=device["device_id"], detail={"ability": ability}
        )
        return 403, {"code": "phone_right_off_or_expired", "error": "Change this phone’s permissions on your computer."}
    return 200, {"allowed": True, "ability": ability}


def dictate(params, *, service, device, **_kw):
    from core import dictation

    code, result = check({"ability": "microphone"}, service=service, device=device)
    if code != 200:
        return code, result
    encoded = params.get("content_b64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES * 4 // 3 + 4):
        return 413, {"code": "recording_too_large", "error": "Keep the recording under three megabytes."}
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return 400, {"code": "invalid_recording", "error": "This recording could not be read."}
    if not raw or len(raw) > MAX_BYTES:
        return 400 if not raw else 413, {"code": "invalid_recording", "error": "This recording is empty or too large."}
    locale = str(params.get("locale") or "en-US")
    if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8}){0,3}", locale):
        return 400, {"code": "invalid_dictation_locale", "error": "Choose a supported speaking language."}
    try:
        result = dictation.transcribe(raw, media_type=str(params.get("media_type") or ""), locale=locale)
    except dictation.DictationUnavailable as error:
        service.store.append_receipt(
            "phone.dictation_unavailable", device_id=device["device_id"], detail={"code": error.code}
        )
        return error.http_status, error.to_dict()
    service.store.append_receipt("phone.dictation_draft", device_id=device["device_id"], detail={"bytes": len(raw)})
    return 200, {**result, "draft_only": True}


def location(params, *, service, device, **_kw):
    code, result = check({"ability": "location"}, service=service, device=device)
    if code != 200:
        return code, result
    values = []
    for name, bound in [("latitude", 90), ("longitude", 180)]:
        value = params.get(name)
        if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,3}(?:\.\d{1,8})?", value):
            return 400, {"code": "invalid_location", "error": "This location could not be read."}
        number = float(value)
        if not math.isfinite(number) or abs(number) > bound:
            return 400, {"code": "invalid_location", "error": "This location could not be read."}
        values.append(f"{number:.6f}")
    service.store.append_receipt("phone.location_draft", device_id=device["device_id"])
    return 200, {"text": ", ".join(values), "draft_only": True}
