"""Audit hardening: non-numeric query ints degrade to the default (no 500), and an oversized
POST body is rejected 413 before it is parsed."""
from __future__ import annotations

import json

from core.web.api.runtime import RuntimeServices
from core.web.api.service import _qint, dispatch_get


def test_qint_is_fail_soft():
    assert _qint({"limit": ["abc"]}, "limit", 120) == 120
    assert _qint({"limit": ["50"]}, "limit", 120) == 50
    assert _qint({}, "limit", 24) == 24
    assert _qint({"after": [""]}, "after", 0) == 0


def test_non_numeric_limit_does_not_500():
    # /api/runtime/events used a bare int(); a non-numeric ?after must not crash the handler.
    res = dispatch_get(
        path="/api/runtime/events",
        query={"session": ["s"], "after": ["abc"], "limit": ["xyz"]},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
    )
    assert res.status == 200
    body = json.loads(res.body.decode("utf-8"))
    assert "events" in body


def test_body_cap_constant_present_and_reasonable():
    # The global pre-parse cap exists in app.py (guards chat/generate/null, not just settings).
    from core.web.api.app import _MAX_REQUEST_BODY_BYTES

    assert 1024 * 1024 <= _MAX_REQUEST_BODY_BYTES <= 64 * 1024 * 1024
