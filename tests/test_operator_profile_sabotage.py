"""Operator Profile — sabotage campaign: each fence disabled alone must be caught by its detector.

A fence whose removal changes nothing is not load-bearing. Each test monkeypatches exactly ONE
fence and proves the named detector from the memory / served suites fails for the expected reason
(monkeypatch restores the fence afterwards).
"""
from __future__ import annotations

import pytest

from tests import test_operator_profile_memory as memory_suite
from tests import test_operator_profile_served as served_suite
from tests.operator_profile_rig import profile_env_generator


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)


def _fails(detector, *args):
    with pytest.raises(AssertionError):
        detector(*args)


# S1 — permission neutrality: a hydration that hands out authority must be caught.
def test_sabotage_permission_neutrality(profile_env, monkeypatch):
    from core import operator_profile

    real = operator_profile.hydration_for_turn

    def leaking(principal, **kwargs):
        lines, used = real(principal, **kwargs)
        return lines, used

    monkeypatch.setattr(operator_profile, "hydration_for_turn", leaking)
    from core import mode_permission_policy as mpp

    real_decide = mpp.decide_tool_call

    def granting(**kwargs):
        ctx = dict(kwargs.get("source_context") or {})
        if ctx.get("profile_used"):
            return mpp.PermissionDecision(effect=mpp.PermissionEffect.ALLOW, mode=mpp.OperatingMode.MANUAL, actions=(), reason="sabotage")
        return real_decide(**kwargs)

    monkeypatch.setattr(mpp, "decide_tool_call", granting)
    _fails(memory_suite.test_profile_context_never_grants_email_or_social_permission, profile_env)


# S2 — secret rejection
def test_sabotage_secret_rejection(profile_env, monkeypatch):
    from core import operator_profile

    monkeypatch.setattr(operator_profile, "value_is_secret", lambda text: False)
    _fails(memory_suite.test_secrets_tokens_cookies_passwords_never_enter_profile_storage, profile_env, "Alex token=sk-abcdefghijklmnopqrstuvwxyz123456")


# S3 — A8 serve gating
def test_sabotage_a8_serve_gate(profile_env, monkeypatch):
    from core import operator_profile

    monkeypatch.setattr(operator_profile, "_servable", lambda item: item.status != "deleted")
    _fails(memory_suite.test_withhold_and_erase_gate_every_reader_and_hydration, profile_env)


# S3b — A8 erasure sweep step
def test_sabotage_a8_erase_step(profile_env, monkeypatch):
    from core import finalization

    monkeypatch.setattr(finalization, "_sweep_step_operator_profile", lambda *a, **k: finalization._SWEEP_STEP_OK)
    _fails(memory_suite.test_withhold_and_erase_gate_every_reader_and_hydration, profile_env)


# S4 — scope isolation
def test_sabotage_scope_isolation(profile_env, monkeypatch):
    from core import operator_profile

    real = operator_profile.list_items

    def leaky(principal, *, include_candidates=False, include_deleted=False, session_id=""):
        rows = real(principal, include_candidates=include_candidates, include_deleted=include_deleted, session_id=session_id)
        if not rows:
            from storage.db import get_connection

            conn = get_connection()
            try:
                raw = conn.execute("SELECT * FROM operator_profile_items WHERE principal = ? AND status = 'active'", (principal,)).fetchall()
            finally:
                conn.close()
            rows = [operator_profile._row_to_item(r) for r in raw]
        return rows

    monkeypatch.setattr(operator_profile, "list_items", leaky)
    _fails(memory_suite.test_only_this_chat_never_resolves_in_another_chat, profile_env)


# S5 — candidate confirmation: persisting a strong proposal directly must be caught
def test_sabotage_candidate_confirmation(profile_env, monkeypatch):
    from core import operator_profile

    def persist_instead(principal, category, value, *, session_id, turn_id="", confidence=0.85, reason=""):
        return operator_profile.remember(principal, category, value, session_id=session_id, turn_id=turn_id)

    monkeypatch.setattr(operator_profile, "propose_candidate", persist_instead)
    _fails(served_suite.test_served_implicit_preference_becomes_candidate_chip_not_fact, profile_env)


# S6 — revision conflict
def test_sabotage_revision_conflict(profile_env, monkeypatch):
    from core import operator_profile

    real = operator_profile._update

    def last_writer_wins(conn, current, *, expected_revision, **kwargs):
        fresh = operator_profile._load(conn, current.item_id)
        return real(conn, fresh, expected_revision=None, **kwargs)

    monkeypatch.setattr(operator_profile, "_update", last_writer_wins)
    _fails(memory_suite.test_concurrent_edits_are_cas_guarded, profile_env)


# S7 — principal isolation
def test_sabotage_principal_isolation(profile_env, monkeypatch):
    from core import operator_profile

    monkeypatch.setattr(operator_profile, "principal_for_request", lambda ctx: operator_profile.OWNER_PRINCIPAL)
    _fails(memory_suite.test_principal_isolation_between_users, profile_env)
