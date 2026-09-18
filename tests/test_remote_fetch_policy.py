from __future__ import annotations

from unittest import mock

from core.remote_fetch_policy import (
    remote_fetch_allowed_by_context,
    remote_fetch_attempt_count,
    remote_fetch_policy_scope,
)
from tools.browser.browser_render import browser_render
from tools.web.http_fetch import http_fetch_text


def test_trusted_live_surface_uses_ambient_policy_when_override_is_absent() -> None:
    assert remote_fetch_allowed_by_context(
        {"surface": "channel", "platform": "openclaw"}
    )
    assert remote_fetch_allowed_by_context({"surface": "api"})


def test_explicit_remote_fetch_veto_beats_trusted_live_surface() -> None:
    assert not remote_fetch_allowed_by_context(
        {
            "surface": "channel",
            "platform": "openclaw",
            "allow_remote_fetch": False,
        }
    )


def test_untrusted_library_context_does_not_gain_ambient_remote_authority() -> None:
    assert not remote_fetch_allowed_by_context({"surface": "cli"})
    assert not remote_fetch_allowed_by_context({})


def test_direct_http_fetch_is_blocked_below_provider_paths() -> None:
    with remote_fetch_policy_scope({"allow_remote_fetch": False}), mock.patch(
        "tools.web.http_fetch.urllib.request.urlopen",
        side_effect=AssertionError("network must not open"),
    ) as urlopen:
        result = http_fetch_text("https://example.com/private-context")
        attempts = remote_fetch_attempt_count()

    assert result == {
        "status": "remote_fetch_disabled",
        "text": "",
        "html": "",
        "final_url": "https://example.com/private-context",
    }
    # R2b2b: http_fetch_text goes through the one door, and the door's own
    # law (test_a_refused_fetch_is_not_counted_as_an_attempt) is that a
    # refusal never went to the network and is not counted. This module used
    # to tally its own vetoed attempts — the outlier, now converged.
    assert attempts == 0
    urlopen.assert_not_called()


def test_direct_browser_render_is_blocked_before_playwright_loading() -> None:
    with remote_fetch_policy_scope({"allow_remote_fetch": False}), mock.patch(
        "tools.browser.browser_render.policy_engine.playwright_enabled",
        side_effect=AssertionError("browser policy must not be evaluated"),
    ) as playwright_enabled:
        result = browser_render("https://example.com/private-context")
        attempts = remote_fetch_attempt_count()

    assert result == {
        "status": "remote_fetch_disabled",
        "final_url": "https://example.com/private-context",
    }
    assert attempts == 1
    playwright_enabled.assert_not_called()
