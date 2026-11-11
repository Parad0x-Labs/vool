"""A "free pin needs no reservation" refusal is a clearance, not a block.

Measured 2026-11-03 (build 8af687ca, two served chats): the operator pinned OpenRouter
``:free`` models (dots-studio/dots-3-note-preview:free, nvidia/nemotron-3.5-lightning:free).
The spend authority refused the reservation with ``free_pin_needs_no_reservation`` — its own
statement that the pin is free and may run without one — and the router consumed that refusal
as a block: the paid arm switched off, the pinned model died at a downstream paid gate or was
emptied from the ranking, and the turn failed with the clearance itself as the block reason.
These tests pin the repaired contract: the clearance is minted ONCE from the authority's own
refusal and travels under a server-authored stamp no caller can write.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.memory_first_router import FREE_PIN_CLEARANCE_REASON, pin_reservation_clearance
from core.request_trust import RESERVED_TRUST_KEYS


def _manifest(provider_id: str) -> SimpleNamespace:
    return SimpleNamespace(provider_id=provider_id, model_name=provider_id.split(":", 1)[-1])


def test_free_pin_denial_clears_the_pinned_provider() -> None:
    manifest = _manifest("openrouter-byok:dots-studio/dots-3-note-preview:free")
    cleared = pin_reservation_clearance(
        denial_reason=FREE_PIN_CLEARANCE_REASON,
        authorized_paid_call=None,
        requested_paid_cloud=True,
        requested_manifest=manifest,
    )
    assert cleared == "openrouter-byok:dots-studio/dots-3-note-preview:free"


def test_any_other_denial_clears_nothing() -> None:
    manifest = _manifest("openrouter-byok:z-ai/glm-5.3-flash")
    for reason in (
        "",
        "not_owner_local",
        "no_explicit_paid_pick",
        "daily_paid_call_cap_exceeded",
        "selected_provider_excluded_before_invocation",
    ):
        assert (
            pin_reservation_clearance(
                denial_reason=reason,
                authorized_paid_call=None,
                requested_paid_cloud=True,
                requested_manifest=manifest,
            )
            == ""
        ), reason


def test_granted_reservation_clears_nothing() -> None:
    """A paid pin that DID reserve runs under the reservation; no clearance exists."""
    manifest = _manifest("openrouter-byok:z-ai/glm-5.3-flash")
    authorized = SimpleNamespace(model_call_id="model-call-test")
    assert (
        pin_reservation_clearance(
            denial_reason=FREE_PIN_CLEARANCE_REASON,
            authorized_paid_call=authorized,
            requested_paid_cloud=True,
            requested_manifest=manifest,
        )
        == ""
    )


def test_non_paid_or_unresolved_pin_clears_nothing() -> None:
    manifest = _manifest("openrouter-byok:dots-studio/dots-3-note-preview:free")
    assert (
        pin_reservation_clearance(
            denial_reason=FREE_PIN_CLEARANCE_REASON,
            authorized_paid_call=None,
            requested_paid_cloud=False,
            requested_manifest=manifest,
        )
        == ""
    )
    assert (
        pin_reservation_clearance(
            denial_reason=FREE_PIN_CLEARANCE_REASON,
            authorized_paid_call=None,
            requested_paid_cloud=True,
            requested_manifest=None,
        )
        == ""
    )


def test_clearance_stamp_is_reserved_from_callers() -> None:
    """The stamp participates in the spend decision, so a request body can never write it."""
    assert "pin_reservation_free_provider" in RESERVED_TRUST_KEYS
    assert "authorized_paid_call" in RESERVED_TRUST_KEYS
