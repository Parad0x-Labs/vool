"""M-P4 — the five facts: typed profile items, secret and address fences, forget, A8 sweep."""
from __future__ import annotations

import pytest

from core import operator_profile
from tests.first_run_pact_rig import pact_rig


def _items(pact_rig):
    return {i.category: i for i in operator_profile.list_items(operator_profile.OWNER_PRINCIPAL) if i.status == "active"}


def test_typed_facts_land_in_the_operator_profile_with_explicit_origin(pact_rig):
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/facts", {
        "items": [
            {"category": "preferred_name", "value": "Alex"},
            {"category": "locale", "value": "lt-LT"},
            {"category": "email_signature", "value": "— Sent from my Mac"},
            {"category": "shipping_region_preference", "value": "Europe"},
        ],
        "expect_revision": snap["revision"],
    })
    assert status == 200, payload
    results = {r["category"]: r["status"] for r in payload["results"]}
    assert results == {
        "preferred_name": "saved", "locale": "saved",
        "email_signature": "saved", "shipping_region_preference": "saved",
    }
    items = _items(pact_rig)
    assert items["preferred_name"].origin == "explicit"
    assert items["shipping_region_preference"].value_text == "Europe"
    # The saved name is FUNCTIONAL from turn one: hydration addresses the operator.
    lines, _meta = operator_profile.hydration_for_turn(operator_profile.OWNER_PRINCIPAL)
    assert any("Alex" in line for line in lines)


def test_a_pasted_secret_is_refused_and_stored_nowhere_in_the_home(pact_rig):
    secret = "sk-or-v1-abcdef0123456789abcdef0123456789"
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/facts", {
        "items": [{"category": "email_signature", "value": secret}],
        "expect_revision": snap["revision"],
    })
    assert status == 200
    assert payload["results"][0]["status"] == "refused_secret"
    assert secret not in (pact_rig.home / "data" / "first_run_pact_state.json").read_text()
    assert "sk-or-v1" not in (pact_rig.home / "data" / "first_run_pact_state.json").read_text()
    assert all(item.value_text != secret for item in operator_profile.list_items(operator_profile.OWNER_PRINCIPAL))


def test_an_address_classified_value_is_refused_in_every_fact_field_c2(pact_rig):
    address = "Flat 7, 21 Baker Street, London NW1 6XE"
    snap = pact_rig.pact()
    for category in ("preferred_name", "email_signature", "locale", "shipping_region_preference"):
        status, payload = pact_rig.post("/api/onboarding/pact/facts", {
            "items": [{"category": category, "value": address}],
            "expect_revision": pact_rig.pact()["revision"],
        })
        assert payload["results"][0]["status"] == "refused_secret", (category, payload)
        assert payload["results"][0]["detail"] == "address_shaped"
    assert all(item.value_text != address for item in operator_profile.list_items(operator_profile.OWNER_PRINCIPAL))


def test_shipping_region_takes_only_its_enum_and_is_never_inferred(pact_rig):
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/facts", {
        "items": [{"category": "shipping_region_preference", "value": "Atlantis"}],
        "expect_revision": snap["revision"],
    })
    assert payload["results"][0]["status"] in {"refused_secret", "validation_failed"}
    # Never inferred: the category is marked inferable=False so the inference path excludes it.
    assert operator_profile.CATEGORIES["shipping_region_preference"]["inferable"] is False


def test_forget_tombstones_and_the_a8_sweep_touches_the_category(pact_rig):
    snap = pact_rig.pact()
    pact_rig.post("/api/onboarding/pact/facts", {
        "items": [{"category": "shipping_region_preference", "value": "Europe"}],
        "expect_revision": snap["revision"],
    })
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/facts/forget", {
        "category": "shipping_region_preference", "expect_revision": snap["revision"],
    })
    assert status == 200 and payload["forgotten"] is True
    items = {i.category: i for i in operator_profile.list_items(operator_profile.OWNER_PRINCIPAL, include_deleted=True)}
    assert items["shipping_region_preference"].status == "deleted"

    # A8 erasure sweep participates: governed items matching a digest are scrubbed.
    removed = operator_profile.erase_governed_items("req:pact-a8", "sha256:" + "0" * 64)
    assert removed.startswith("ok:")


def test_file_never_contains_fact_values(pact_rig):
    """S-P5 pin: the pact file records THAT a fact step happened, never WHAT was typed."""
    marker = "LT-PACT-FACT-VALUE-CANARY"
    snap = pact_rig.pact()
    status, payload = pact_rig.post("/api/onboarding/pact/facts", {
        "items": [{"category": "locale", "value": marker}],
        "expect_revision": snap["revision"],
    })
    assert status == 200 and payload["results"][0]["status"] == "saved"
    raw = (pact_rig.home / "data" / "first_run_pact_state.json").read_text()
    assert marker not in raw
