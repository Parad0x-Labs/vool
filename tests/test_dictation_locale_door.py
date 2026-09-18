"""The dictation door's recognition-language contract (NEW, product/desktop-usability-20260917).

Distinct from the existing audio/dictation suites (which prove the recogniser authority and
the draft-not-turn law): these pin the DOOR's handling of an explicit recognition locale —
header or query, normalisation, typed refusal of malformed tags, and pass-through to the
authority — plus the distinction that the dictation locale is NOT the app UI locale and NOT
the answer language.
"""

from __future__ import annotations

import pytest

from core.web.api.service import _dictation_locale, dispatch_dictation


class _Runtime:
    """dispatch_dictation only needs None-ness for header application."""


def _headers(extra: dict | None = None) -> dict:
    base = {"x-vool-session-id": "chat-1", "x-vool-audio-type": "audio/webm"}
    if extra:
        base.update(extra)
    return base


def test_locale_normalisation() -> None:
    assert _dictation_locale("en-US") == "en-US"
    assert _dictation_locale("lt_LT") == "lt-LT"
    assert _dictation_locale("  lt-LT ") == "lt-LT"
    assert _dictation_locale("") == ""
    for bad in ("en-US-extra-deep", "e", "!!!!", "en US", "x" * 20, "<script>"):
        with pytest.raises(ValueError):
            _dictation_locale(bad)


def test_post_passes_the_declared_locale_to_the_authority(monkeypatch) -> None:
    from core import dictation as authority

    seen: dict[str, object] = {}

    def fake_transcribe(data, *, media_type="", locale="en_US"):
        seen["locale"] = locale
        seen["media_type"] = media_type
        return {"ok": True, "text": "tekstas", "complete": True, "duration_s": 1.0}

    monkeypatch.setattr(authority, "transcribe", fake_transcribe)
    response = dispatch_dictation(
        method="POST",
        raw_body=b"0000",
        headers=_headers({"x-vool-dictation-locale": "lt_LT"}),
        runtime=None,
        client_host="127.0.0.1",
    )
    assert response.status == 200
    assert seen["locale"] == "lt-LT", "the door normalises and forwards the recogniser locale"


def test_post_without_a_locale_keeps_the_recogniser_default(monkeypatch) -> None:
    from core import dictation as authority

    seen: dict[str, object] = {}

    def fake_transcribe(data, *, media_type="", locale="en_US"):
        seen["locale"] = locale
        return {"ok": True, "text": "text", "complete": True, "duration_s": 1.0}

    monkeypatch.setattr(authority, "transcribe", fake_transcribe)
    response = dispatch_dictation(
        method="POST", raw_body=b"0000", headers=_headers(), runtime=None, client_host="127.0.0.1"
    )
    assert response.status == 200
    assert seen["locale"] == authority.speech_tool.DEFAULT_LOCALE


def test_malformed_locale_is_refused_typed(monkeypatch) -> None:
    from core import dictation as authority

    def fail_transcribe(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("a malformed locale must be refused before the authority")

    monkeypatch.setattr(authority, "transcribe", fail_transcribe)
    response = dispatch_dictation(
        method="POST",
        raw_body=b"0000",
        headers=_headers({"x-vool-dictation-locale": "not a locale"}),
        runtime=None,
        client_host="127.0.0.1",
    )
    assert response.status == 400
    body = __import__("json").loads(response.body)
    assert body["error"] == "invalid_dictation_locale"


def test_get_availability_honours_the_locale_query(monkeypatch) -> None:
    from core import dictation as authority

    seen: dict[str, object] = {}

    def fake_availability(*, locale="en_US"):
        seen["locale"] = locale
        return {"available": False, "error": "speech_recognizer_unavailable",
                "message": "No speech recogniser is installed for this language.",
                "remediation": "Add the language in System Settings."}

    monkeypatch.setattr(authority, "availability", fake_availability)
    response = dispatch_dictation(
        method="GET", raw_body=b"", headers={}, runtime=None, client_host="127.0.0.1",
        query="locale=lt-LT",
    )
    assert response.status == 200
    assert seen["locale"] == "lt-LT"
    body = __import__("json").loads(response.body)
    assert body["available"] is False and "remediation" in body
