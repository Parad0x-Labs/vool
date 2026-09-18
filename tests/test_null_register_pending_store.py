"""Unit tests for the isolated pending-registration store."""
from __future__ import annotations

from storage.null_register_pending_store import (
    clear_pending_registration,
    load_pending_registration,
    stage_pending_registration,
)

_OWNER = "28hxXaSfXrY2UTEEuHseP1VfRdq3nUyyPaYBMHsWW2VX"


def test_stage_load_clear_roundtrip() -> None:
    sid = "store-roundtrip-1"
    clear_pending_registration(sid)
    stage_pending_registration(sid, name="mysite.null", cost_lamports=11_200_000, owner_pubkey=_OWNER)
    row = load_pending_registration(sid)
    assert row is not None
    assert row["name"] == "mysite.null"
    assert row["cost_lamports"] == 11_200_000
    assert row["owner_pubkey"] == _OWNER
    clear_pending_registration(sid)
    assert load_pending_registration(sid) is None


def test_stage_overwrites_prior_offer() -> None:
    sid = "store-overwrite-1"
    stage_pending_registration(sid, name="first.null", cost_lamports=1, owner_pubkey=_OWNER)
    stage_pending_registration(sid, name="second.null", cost_lamports=2, owner_pubkey=_OWNER)
    row = load_pending_registration(sid)
    assert row is not None and row["name"] == "second.null" and row["cost_lamports"] == 2
    clear_pending_registration(sid)


def test_ttl_expiry_drops_stale_offer() -> None:
    sid = "store-ttl-1"
    stage_pending_registration(sid, name="mysite.null", cost_lamports=100, owner_pubkey=_OWNER)
    # ttl_seconds=0 -> any elapsed time is "expired"; the row must be dropped + cleared.
    assert load_pending_registration(sid, ttl_seconds=0) is None
    assert load_pending_registration(sid) is None  # confirmed cleared


def test_missing_session_returns_none() -> None:
    assert load_pending_registration("no-such-session-xyz") is None
    # empty / blank ids are safe no-ops
    assert load_pending_registration("") is None
    stage_pending_registration("", name="x.null", cost_lamports=1, owner_pubkey=_OWNER)  # no-op
    clear_pending_registration("")  # no-op
