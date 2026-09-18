"""vool:// OAuth callback delivery: the desktop-owned receiver + the window's URL parse/relay.

The website is a pure relay; the macOS app catches vool://auth/<provider>/callback?code=&state= and
POSTs {code, state} to the runtime. Here we cover the receiver (redacted status, single-use, never
logs the code) and the window's parse+deliver (correct endpoint/body; malformed/foreign URLs ignored).
"""
from __future__ import annotations

import json
from unittest import mock

from core import oauth_callback
from installer.bundle import vool_window


def test_callback_store_roundtrip_and_redaction():
    oauth_callback.clear()
    ack = oauth_callback.record_callback("OpenRouter", "authcode-SECRET-123", " state-xyz ")
    assert ack == {"provider": "openrouter", "state": "state-xyz"}
    status = oauth_callback.callback_status()
    assert status["pending"] and status["provider"] == "openrouter" and status["state"] == "state-xyz"
    assert status["has_code"] is True
    assert "authcode-SECRET-123" not in json.dumps(status)   # the code is NEVER in the status view
    popped = oauth_callback.take_callback()
    assert popped["code"] == "authcode-SECRET-123"           # the exchanger gets the real code
    assert oauth_callback.callback_status() == {"pending": False}  # single-use


def test_window_parses_and_delivers_code_state():
    with mock.patch("installer.bundle.vool_window.urllib.request.urlopen") as urlopen:
        urlopen.return_value.read.return_value = b"{}"
        vool_window._deliver_oauth_url("vool://auth/openrouter/callback?code=abc123&state=xyz789")
    request = urlopen.call_args.args[0]
    assert request.full_url.endswith("/api/auth/openrouter/callback")
    assert json.loads(request.data) == {"code": "abc123", "state": "xyz789"}
    assert request.get_method() == "POST"


def test_window_ignores_malformed_or_foreign_urls():
    with mock.patch("installer.bundle.vool_window.urllib.request.urlopen") as urlopen:
        vool_window._deliver_oauth_url("vool://auth/openrouter/callback?code=abc")     # no state
        vool_window._deliver_oauth_url("vool://auth/openrouter/callback?state=xyz")    # no code
        vool_window._deliver_oauth_url("https://evil.example/?code=a&state=b")         # not vool://
        vool_window._deliver_oauth_url("vool://something/else?code=a&state=b")         # not an auth callback
    urlopen.assert_not_called()
