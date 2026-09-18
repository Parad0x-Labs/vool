"""Hardening regression for the BYOK paid-cloud path (PR #4 review).

Two latent spend vectors, closed and locked here:
- The AUTO (ACTION_CLOUD) branch of the escalation gate must authorize a burst against the user's
  own cloud key ONLY for the owner's local session -- a non-owner-local caller (a remote channel
  message, an exposed 0.0.0.0 API) must never trigger an auto spend.
- ``authorized_paid_call`` is a server-built reservation that participates in the spend decision, so
  it is a reserved trust key: a caller-supplied one is stripped from every inbound body.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core import cloud_escalation_policy
from core.memory_first_router import _gate_paid_by_cloud_escalation
from core.request_trust import RESERVED_TRUST_KEYS, strip_reserved_trust_keys


def _auto_gate(source_context):
    # Force the policy to AUTO so only the owner-local check on the auto branch is under test.
    with mock.patch.object(
        cloud_escalation_policy,
        "decide_escalation",
        return_value=SimpleNamespace(action=cloud_escalation_policy.ACTION_CLOUD),
    ), mock.patch.object(cloud_escalation_policy, "load_policy", return_value=object()), mock.patch.object(
        cloud_escalation_policy, "used_today", return_value=object()
    ):
        return _gate_paid_by_cloud_escalation(
            resolved_allow_paid=True,
            allow_paid_fallback=True,
            requested_paid_cloud=False,
            source_context=source_context,
        )


def test_auto_cloud_burst_authorized_only_for_owner_local() -> None:
    # Owner's own local session in auto mode: burst authorized.
    assert _auto_gate({"_owner_local": True}) == (True, True)
    # Non-owner-local callers: burst DENIED, task stays local -- even with allow_paid_fallback set.
    assert _auto_gate({"_owner_local": False}) == (False, False)
    assert _auto_gate({"surface": "openclaw"}) == (False, False)


def test_authorized_paid_call_is_a_reserved_caller_forbidden_key() -> None:
    assert "authorized_paid_call" in RESERVED_TRUST_KEYS
    stripped = strip_reserved_trust_keys(
        {"authorized_paid_call": {"forged": True}, "surface": "openclaw", "keep": 1}
    )
    assert "authorized_paid_call" not in stripped
    assert stripped == {"surface": "openclaw", "keep": 1}
