"""Served: with web lookups off, a message that names a link answers instead of failing the turn.

Measured on main: "summarize <link>" with web off returned HTTP 500 ("K-08 violation:
url-grounding would mutate post-admission bytes"). The URL-grounding decline was rewritten by the
evidence binder inside the seal, so the boundary's verify-only grounding pass no longer recognised
it. A scratch daemon with a scripted local model drives the real /api/chat door.
"""
from __future__ import annotations

import uuid

import pytest

import tests._reader_served_rig as rig

pytestmark = [pytest.mark.served]


@pytest.mark.parametrize(
    "message",
    [
        "summarize https://example.org/article",
        "what is on https://example.com/blog/post-1 ?",
    ],
)
def test_a_link_with_web_off_gets_an_answer_not_a_server_error(tmp_path, message):
    provider = rig.CapturingProvider(default="That page explains how the new release improves battery life.")
    provider.__enter__()
    daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_ENABLE_WEB": "0"})
    try:
        daemon.register_provider()
        daemon.start()
        assert daemon.certify().get("state") == "verified"
        out = daemon.chat(message, session_id=rig.canonical_session("url-web-off-" + uuid.uuid4().hex[:8]))
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
    answer = str((out.get("message") or {}).get("content") or "")
    assert answer, out
    # Web is off, so nothing was read: the answer must not describe the page.
    assert "battery life" not in answer, answer
