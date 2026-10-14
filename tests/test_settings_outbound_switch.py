"""The Settings Automatic online checks switch.

Automatic online checks (the policy key ``network.outbound_enabled``) are off on a fresh
install and this row is the in-app control for them. The row reads the current policy
through the pact boundary projection (GET /api/onboarding/pact?view=boundary) and writes
through the boundary command that already exists (POST /api/onboarding/pact/boundary).
Everything here drives the real served dispatch seam -- the same routes the browser calls.

A fresh install must still have the value false and make zero network calls; the
network-default tests own that claim and this file must never disturb it.
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


def test_a_fresh_install_reads_automatic_online_checks_off(pact_rig) -> None:
    from core import policy_engine

    payload = _read(pact_rig)
    assert payload["outbound_enabled"] is False
    assert payload["local_only_mode"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.automatic_outbound_enabled() is False


def test_the_row_switches_checks_on_and_the_policy_agrees(pact_rig) -> None:
    from core import policy_engine

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is True
    assert policy_engine.get("network.outbound_enabled") is True
    # the gate every reader consults, not just the stored value
    assert policy_engine.automatic_outbound_enabled() is True

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.automatic_outbound_enabled() is False


def test_turning_the_checks_off_is_allowed_while_local_only_is_on(pact_rig) -> None:
    """Off is always allowed: the refusal only guards turning background network work on."""
    from core import policy_engine

    pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    policy_engine.set_operator_policy_values({"system.local_only_mode": True})

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is False


def test_local_only_mode_refuses_the_switch_and_it_stays_off(pact_rig) -> None:
    from core import policy_engine

    policy_engine.set_operator_policy_values({"system.local_only_mode": True})

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 409 and payload["ok"] is False, (status, payload)
    assert payload["error"] == "invalid_transition"

    payload = _read(pact_rig)
    assert payload["local_only_mode"] is True
    assert payload["outbound_enabled"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    # local-only vetoes the composite gate too, value or not
    assert policy_engine.automatic_outbound_enabled() is False


def test_a_non_loopback_caller_gets_403_and_nothing_changes(pact_rig) -> None:
    from core import policy_engine

    status, payload = pact_rig.post(
        BOUNDARY_WRITE, {"key": "automatic_checks", "value": True}, client_host="10.1.2.3"
    )
    assert status == 403 and payload["ok"] is False, (status, payload)
    assert payload["error"] == "owner_local_required"

    assert _read(pact_rig)["outbound_enabled"] is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.automatic_outbound_enabled() is False


def test_the_switch_never_changes_local_only_mode_or_the_other_boundaries(pact_rig) -> None:
    from core import policy_engine

    for value in (True, False, True):
        status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": value})
        assert status == 200 and payload["ok"] is True, (status, payload)
        payload = _read(pact_rig)
        assert payload["local_only_mode"] is False
        assert payload["allow_web_fallback"] is False  # the web lookups row owns that key
        assert payload["outbound_enabled"] is value
        assert policy_engine.local_only_mode() is False
        assert policy_engine.allow_web_fallback() is False


def test_the_settings_row_is_wired_to_the_boundary_authority() -> None:
    from core.vool_settings_page import render_vool_settings_html, settings_groups

    rows = {row["id"]: row for group in settings_groups() for row in group["rows"]}
    assert rows["web_lookups"]["read"]["url"] == BOUNDARY_READ  # the neighbour it sits next to
    row = rows["automatic_checks"]
    assert row["read"]["url"] == BOUNDARY_READ
    assert row["widget"] == "automatic_checks"
    html = render_vool_settings_html()
    assert "widgetAutomaticChecks" in html
    # the row's widget posts this exact key through the shared boundary command
    assert "{ key: 'automatic_checks', value: want }" in html
    assert BOUNDARY_WRITE in html
    assert BOUNDARY_READ in html


def test_a_home_from_before_the_tour_can_use_the_switch_without_touching_the_tour(pact_rig) -> None:
    from core import policy_engine

    pact_file = _pre_tour_home()
    before = pact_file.read_bytes()

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": True})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert _read(pact_rig)["outbound_enabled"] is True
    assert policy_engine.automatic_outbound_enabled() is True

    status, payload = pact_rig.post(BOUNDARY_WRITE, {"key": "automatic_checks", "value": False})
    assert status == 200 and payload["ok"] is True, (status, payload)
    assert policy_engine.get("network.outbound_enabled") is False

    # The tour record is not touched: such a home still shows no tour, exactly as before.
    assert pact_file.read_bytes() == before


def _pre_tour_home():
    """A home that existed before the tour shipped: its pact file says ``not_applicable``.

    Same shape as the web lookups switch's pre-tour test: most upgraded users have this.
    """
    import json

    from core.runtime_paths import active_data_dir

    pact_file = active_data_dir() / "first_run_pact_state.json"
    pact_file.parent.mkdir(parents=True, exist_ok=True)
    pact_file.write_text(json.dumps({
        "schema_name": "vool.first_run_pact", "schema_version": 1, "state": "not_applicable",
        "revision": 0, "welcome_hidden": False, "seed_reason": "existing_user",
        "steps": {}, "created_at": "2026-09-05T00:00:00Z", "updated_at": "2026-09-05T00:00:00Z",
    }))
    return pact_file
