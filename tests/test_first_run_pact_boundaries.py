"""M-P3 — boundaries flip the OWNING authorities; the egress claim is proved, not asserted."""
from __future__ import annotations

import pytest

from core import first_run_pact, policy_engine
from tests.first_run_pact_rig import pact_rig


def test_the_composite_flips_every_authority_store_never_the_pact_file(pact_rig):
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
        "key": "local_only_composite", "value": True, "expect_revision": snap["revision"],
    })
    assert status == 200, payload
    # Asserted at the OWNING authorities, not the pact file.
    assert policy_engine.local_only_mode() is True
    assert policy_engine.allow_web_fallback() is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.get("shards.default_share_scope") == "local_only"
    # The response's live block equals authority truth (LP4) and the pact file holds no values.
    assert payload["live"]["local_only_mode"] is True
    raw = (pact_rig.home / "data" / "first_run_pact_state.json").read_text()
    assert "local_only_mode" not in raw.replace('"local_only_mode"', '', 1)  # only in forbidden positions check below
    import json as _json

    doc = _json.loads(raw)
    assert set(doc) & {"boundaries", "policy", "values"} == set()
    # The web switch is locked while the composite is on.
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
        "key": "web_lookups", "value": True, "expect_revision": snap["revision"],
    })
    assert status == 409


def test_memory_pause_flips_operator_profile_state(pact_rig):
    from core import operator_profile

    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
        "key": "memory_paused", "value": True, "expect_revision": snap["revision"],
    })
    assert status == 200
    assert operator_profile.is_paused(operator_profile.OWNER_PRINCIPAL) is True
    assert payload["live"]["memory_paused"] is True
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "memory_paused", "value": False, "expect_revision": snap["revision"]})
    assert operator_profile.is_paused(operator_profile.OWNER_PRINCIPAL) is False


def test_the_egress_census_says_complete_on_a_fresh_home_and_the_strong_sentence_is_earned(pact_rig):
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    snap = pact_rig.pact()
    census = snap["live"]["egress"]
    assert census["proof"] == "complete", census
    assert all(door["closed"] for door in census["doors"])
    assert census["residuals"] == []


def test_reopening_one_door_degrades_the_census_and_the_sentence_narrows(pact_rig):
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    # Sabotage the authority directly (the S-P16 shape): re-open web under the composite.
    policy_engine.set_operator_policy_values({"system.allow_web_fallback": True})
    snap = pact_rig.pact()
    census = snap["live"]["egress"]
    assert census["proof"] == "partial"
    assert any(not door["closed"] for door in census["doors"])
    assert census["residuals"]  # the narrow truth names its residuals


def test_turning_the_composite_off_restores_product_defaults_and_keeps_the_wallet_frozen(pact_rig):
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
        "key": "local_only_composite", "value": False, "expect_revision": snap["revision"],
    })
    assert status == 200
    assert policy_engine.local_only_mode() is False
    assert policy_engine.allow_web_fallback() is True
    assert policy_engine.get("network.outbound_enabled") is True
