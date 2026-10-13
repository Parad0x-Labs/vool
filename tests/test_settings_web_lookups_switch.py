"""The Settings Web lookups switch.

Web lookups are off on a fresh install and the ONLY in-app control is the Settings row
(Privacy & Permissions). The row reads the current policy through the pact boundary
projection (GET /api/onboarding/pact?view=boundary) and writes through the boundary
command that already exists (POST /api/onboarding/pact/boundary). Everything here drives
the real served dispatch seam -- the same routes the browser calls.

A fresh install must still have web off and make zero network calls; the network-default
tests own that claim and this file must never disturb it.
"""
from __future__ import annotations

import pytest

from tests.first_run_pact_rig import pact_rig  # the shared isolated-home rig fixture

BOUNDARY_READ = "/api/onboarding/pact?view=boundary"
BOUNDARY_WRITE = "/api/onboarding/pact/boundary"


@pytest.fixture(autouse=True)
def _no_web_env(monkeypatch):
    """The switch under test is the app's own; no environment variable may answer for it."""
    for name in ("VOOL_ENABLE_WEB", "VOOL_ALLOW_WEB", "VOOL_DISABLE_WEB", "VOOL_NO_WEB"):
        monkeypatch.delenv(name, raising=False)


def _read(pact_rig) -> dict:
    status, payload = pact_rig.get(BOUNDARY_READ)
    assert status == 200 and payload["ok"] is True, (status, payload)
    return payload


def test_a_fresh_install_reads_web_lookups_off(pact_rig) -> None:
    from core import policy_engine

    payload = _read(pact_rig)
    assert payload["allow_web_fallback"] is False
    assert payload["local_only_mode"] is False
    assert policy_engine.allow_web_fallback() is False
    # The row's read is the boundary projection, not the onboarding tour's snapshot:
    # no tour state, no steps, no provider material — the fields a Settings row cannot use.
    for tour_field in ("schema_name", "state", "revision", "steps", "facts", "provider"):
        assert tour_field not in payload


def test_the_row_switches_web_lookups_on_and_off(pact_rig) -> None:
    from core import policy_engine

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "web_lookups", "value": True})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["allow_web_fallback"] is True
    assert policy_engine.allow_web_fallback() is True

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "web_lookups", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["allow_web_fallback"] is False
    assert policy_engine.allow_web_fallback() is False


def test_local_only_mode_refuses_the_switch_and_it_stays_off(pact_rig) -> None:
    from core import policy_engine

    policy_engine.set_operator_policy_values({"system.local_only_mode": True})

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "web_lookups", "value": True})
    assert status == 409 and payload["ok"] is False, (status, payload)
    assert payload["error"] == "invalid_transition"

    payload = _read(pact_rig)
    assert payload["local_only_mode"] is True
    assert payload["allow_web_fallback"] is False
    assert policy_engine.allow_web_fallback() is False


def test_the_settings_row_is_wired_to_the_boundary_authority() -> None:
    from core.vool_settings_page import render_vool_settings_html, settings_groups

    row = next(
        row
        for group in settings_groups()
        for row in group["rows"]
        if row["id"] == "web_lookups"
    )
    assert row["read"]["url"] == BOUNDARY_READ
    html = render_vool_settings_html()
    assert "widgetWebLookups" in html
    assert BOUNDARY_WRITE in html
    assert BOUNDARY_READ in html
