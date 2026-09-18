"""GOBLIN inv 2 (NO SELF-MINT) — a caller-supplied `operating_mode` string cannot mint
BYPASS_PERMISSIONS authority without a canonical validated bypass grant.

The `active_mode_state` fallback (no server-side session record) previously honored
`context["operating_mode"]` verbatim, so a client/model-controlled context carrying
`operating_mode="bypass_permissions"` was granted bypass unvalidated. It now fails closed
to MANUAL, matching the two authoritative paths (`set_active_mode`, the state-exists branch).

Sabotage seam: revert the fallback's bypass-grant check and the first two tests go red.
"""
from __future__ import annotations

from core import mode_permission_policy as m


def test_unvalidated_context_bypass_falls_closed_to_manual():
    for mode in ("bypass_permissions", "bypass"):
        state = m.active_mode_state({"operating_mode": mode, "runtime_session_id": f"s-{mode}"})
        assert state["mode"] == m.OperatingMode.MANUAL.value, mode


def test_bypass_with_bogus_token_falls_closed_to_manual():
    state = m.active_mode_state({
        "operating_mode": "bypass_permissions",
        "bypass_token": "not-a-real-grant",
        "runtime_session_id": "s-bogus",
    })
    assert state["mode"] == m.OperatingMode.MANUAL.value
    assert state["bypass_token"] == ""          # not carried forward for a non-bypass result


def test_legit_non_bypass_modes_are_untouched():
    # The fix must only affect the bypass path; ordinary modes still resolve normally.
    for req in ("manual", "review_edits", "plan"):
        state = m.active_mode_state({"operating_mode": req, "runtime_session_id": f"s-{req}"})
        assert state["mode"] == req


def test_valid_bypass_grant_still_grants_bypass():
    # A canonical grant (minted via set_active_mode) is honored — the fix blocks only the
    # UNVALIDATED self-mint, not a real grant.
    import core.mode_permission_policy as pol
    session = "s-real-grant"
    token = "grant-token-xyz"
    # register a live grant directly in the grant store (what a real bypass approval does)
    import time
    with pol._LOCK:
        pol._BYPASS_GRANTS[token] = {
            "scope": "session", "session_id": session, "revoked": False,
            "expires_at": time.time() + 3600,
        }
    try:
        state = m.active_mode_state({
            "operating_mode": "bypass_permissions",
            "bypass_token": token,
            "runtime_session_id": session,
        })
        assert state["mode"] == m.OperatingMode.BYPASS_PERMISSIONS.value
    finally:
        with pol._LOCK:
            pol._BYPASS_GRANTS.pop(token, None)
