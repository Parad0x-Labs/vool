"""Unit tests for the per-session self-update offer marker store."""
from __future__ import annotations

from storage.self_update_offer_store import clear_offered, load_offered, mark_offered


def test_mark_load_clear_roundtrip() -> None:
    sid = "offer-roundtrip-1"
    clear_offered(sid)
    assert load_offered(sid) is None
    mark_offered(sid, "v0.5.0")
    row = load_offered(sid)
    assert row is not None and row["target_version"] == "v0.5.0"
    clear_offered(sid)
    assert load_offered(sid) is None


def test_mark_overwrites_version() -> None:
    sid = "offer-overwrite-1"
    mark_offered(sid, "v0.5.0")
    mark_offered(sid, "v0.6.0")
    row = load_offered(sid)
    assert row is not None and row["target_version"] == "v0.6.0"
    clear_offered(sid)


def test_ttl_expiry_drops_stale_offer() -> None:
    sid = "offer-ttl-1"
    mark_offered(sid, "v0.5.0")
    assert load_offered(sid, ttl_seconds=0) is None
    assert load_offered(sid) is None


def test_blank_ids_are_safe_noops() -> None:
    assert load_offered("") is None
    mark_offered("", "v0.5.0")  # no-op
    clear_offered("")  # no-op
