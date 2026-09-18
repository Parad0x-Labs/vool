"""MILESTONE 5, slice 1 — the one permission/effect gateway: network fetch.

WHAT THIS FILE PINS
-------------------
The convergence decision (from the seam survey): `decide_tool_call` in
core/mode_permission_policy is THE gateway. Slice 1 normalizes the FIRST
effect class — network fetch — at the one outbound HTTP door:

* every door outcome emits a typed EffectReceipt — the DENIAL is a first-class
  fact, recorded where it previously vanished into an exception;
* the door consults the same gateway the web.* tool path consults
  (`decide_network_fetch` wraps `decide_tool_call`), so the fetch door and the
  model-tool path cannot disagree;
* the legacy explicit veto stays and is now receipted too;
* no mode policy active -> allowed (the frontdoor's legacy behavior, preserved).

SABOTAGE DISCIPLINE: remove the receipt emission at the door and the
fail-on-old-defect pins go red naming the missing receipt.
"""
from __future__ import annotations

from urllib.error import URLError

import pytest

from core.effect_gateway import (
    DECISION_ALLOWED,
    DECISION_DENIED,
    DECISION_SIMULATED,
    EFFECT_NETWORK_FETCH,
    EffectReceipt,
    TurnPolicy,
    close_effect_receipt_scope,
    effect_receipts,
    open_effect_receipt_scope,
    record_effect_receipt,
)
from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    remote_fetch_policy_scope,
)


@pytest.fixture()
def fetch_scope():
    with remote_fetch_policy_scope({"surface": "openclaw", "platform": "openclaw"}):
        yield


def _open(url: str):
    from core.remote_fetch_policy import open_remote_url

    open_remote_url(url, timeout=0.001)


# ------------------------------------------------------------------ the receipt channel


def test_the_channel_is_turn_scoped_and_drained():
    open_effect_receipt_scope()
    record_effect_receipt(
        EffectReceipt(
            effect_class=EFFECT_NETWORK_FETCH,
            decision=DECISION_DENIED,
            reason="test",
            recorded_at="t",
        )
    )
    assert len(effect_receipts()) == 1
    drained = close_effect_receipt_scope()
    assert len(drained) == 1 and effect_receipts() == ()
    # No channel open: the receipt is dropped, never invented onto a foreign turn.
    record_effect_receipt(
        EffectReceipt(effect_class=EFFECT_NETWORK_FETCH, decision=DECISION_DENIED)
    )
    assert effect_receipts() == ()


# ------------------------------------------------------------------ the door


def test_the_veto_receipt_is_recorded_before_the_raise():
    with pytest.raises(RemoteFetchRefusedError):
        with remote_fetch_policy_scope({"allow_remote_fetch": False}):
            try:
                _open("https://example.invalid/x")
            finally:
                captured = list(effect_receipts())
    assert captured, "the denial left no receipt"
    receipt = captured[-1]
    assert receipt["effect_class"] == EFFECT_NETWORK_FETCH
    assert receipt["decision"] == DECISION_DENIED
    assert receipt["decided_by"] == "remote_fetch_policy.explicit_veto"
    assert receipt["host"] == "example.invalid"


def test_the_door_consults_the_gateway_and_receipts_the_allow():
    """With no mode policy active the consult allows (fail-open to the legacy
    default) and the ATTEMPT is receipted — an allowed fetch is a fact too."""
    with pytest.raises(URLError):  # the fetch itself fails (invalid host)
        with remote_fetch_policy_scope({"surface": "openclaw"}):
            try:
                _open("https://gateway-probe.invalid/x")
            finally:
                captured = list(effect_receipts())
    allow = [r for r in captured if r["decision"] == DECISION_ALLOWED]
    assert allow, captured
    assert allow[-1]["decided_by"] == "mode_permission_policy.decide_tool_call"
    assert allow[-1]["host"] == "gateway-probe.invalid"


def test_the_gateway_consult_is_the_same_matrix_as_tool_calls():
    """The non-disagreement law: decide_network_fetch wraps decide_tool_call on
    a read-only web.research intent — no second policy exists."""
    import inspect

    from core import effect_gateway, mode_permission_policy

    source = inspect.getsource(effect_gateway.decide_network_fetch)
    assert "decide_tool_call" in source
    assert hasattr(mode_permission_policy, "decide_tool_call")


def test_a_gateway_deny_blocks_the_door_with_a_receipt(monkeypatch):
    from core import effect_gateway

    def _deny(source_context):
        return DECISION_DENIED, "mode blocks network"

    monkeypatch.setattr(effect_gateway, "decide_network_fetch", _deny)
    with pytest.raises(RemoteFetchRefusedError) as excinfo:
        with remote_fetch_policy_scope({"surface": "openclaw"}):
            try:
                _open("https://denied.invalid/x")
            finally:
                captured = list(effect_receipts())
    assert "permission gateway" in str(excinfo.value)
    receipt = captured[-1]
    assert receipt["decision"] == DECISION_DENIED
    assert receipt["reason"] == "mode blocks network"
    assert receipt["decided_by"] == "mode_permission_policy.decide_tool_call"


# ==================================================================================================
# M5 slice 2 — the filesystem effect class at evaluate_machine_effect's seam
# ==================================================================================================


def _machine_effect(effect_type: str = "machine.write_file", path: str = "/tmp/x.txt") -> dict:
    from core import execution_gate

    return execution_gate.ExecutionGate.evaluate_machine_effect(
        effect_type=effect_type, resolved_path=path
    )


def test_the_machine_effect_seam_receipts_the_allowed_case(fetch_scope):
    decision = _machine_effect()
    assert decision["decision"] == "authorized"
    receipts = list(effect_receipts())
    assert receipts, "an authorized machine effect left no receipt"
    receipt = receipts[-1]
    assert receipt["effect_class"] == "filesystem:machine.write_file"
    assert receipt["decision"] == DECISION_ALLOWED
    assert receipt["decided_by"] == "execution_gate.evaluate_machine_effect"


def test_the_machine_effect_seam_receipts_the_denial(fetch_scope, monkeypatch):
    """THE fail-on-old-defect pin: a REFUSED machine effect emits a typed denial
    receipt — the refusal previously vanished into the caller's blocked
    response with no per-effect record."""
    import core.policy_engine as policy_engine

    monkeypatch.setattr(
        policy_engine, "get", lambda path, default=None: False
        if path == "execution.allow_machine_writes" else default
    )
    decision = _machine_effect(path="/tmp/locked.txt")
    assert decision["decision"] == "refused"
    receipts = list(effect_receipts())
    denial = [r for r in receipts if r["decision"] == DECISION_DENIED]
    assert denial, [r["decision"] for r in receipts]
    assert denial[-1]["effect_class"] == "filesystem:machine.write_file"
    assert "not authorized" in denial[-1]["reason"]
    assert denial[-1]["decided_by"] == "execution_gate.evaluate_machine_effect"


def test_the_receipt_channel_is_shared_across_effect_classes(monkeypatch):
    """One turn, two effect classes: a network denial and a filesystem allow land
    on the SAME turn-scoped channel — one receipt authority, not per-lane records.
    (One scope for the whole turn: a nested scope's finally would DRAIN the
    channel, which is itself the turn-boundary law working correctly.)"""
    import core.policy_engine as policy_engine

    monkeypatch.setattr(
        policy_engine, "get", lambda path, default=None: True
        if path == "execution.allow_machine_writes" else default
    )
    with remote_fetch_policy_scope({"allow_remote_fetch": False, "surface": "openclaw"}):
        with pytest.raises(RemoteFetchRefusedError):
            _open("https://veto.invalid/x")
        _machine_effect()
        receipts = list(effect_receipts())
    classes = {r["effect_class"] for r in receipts}
    assert EFFECT_NETWORK_FETCH in classes
    assert "filesystem:machine.write_file" in classes


# ==================================================================================================
# M5 slice 3 — the command effect class + the scoped TurnPolicy
# ==================================================================================================


def _evaluate_command(cmd: str) -> dict:
    from core import execution_gate

    return execution_gate.ExecutionGate.evaluate_command(cmd)


def test_every_command_outcome_leaves_a_typed_receipt(fetch_scope, monkeypatch):
    import core.policy_engine as policy_engine

    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda path, default=None: {
            "execution.allow_sandbox_execution": True,
            "execution.allow_simulation": True,
            "filesystem.allow_write_workspace": True,
            "filesystem.allow_read_workspace": True,
        }.get(path, default),
    )
    # allowed (sandbox) / denied (destructive) / denied (compound) — all receipted:
    _evaluate_command("ls -la")
    _evaluate_command("rm -rf /")
    _evaluate_command("echo hi && echo bye")
    receipts = list(effect_receipts())
    assert len(receipts) == 3, [r["decision"] for r in receipts]
    assert receipts[0]["effect_class"] == "command"
    assert receipts[0]["decision"] == DECISION_ALLOWED
    assert receipts[1]["decision"] == DECISION_DENIED
    assert receipts[2]["decision"] == DECISION_DENIED
    assert all(r["decided_by"] == "execution_gate.evaluate_command" for r in receipts)


def test_the_simulate_only_outcome_is_mechanically_distinct(fetch_scope, monkeypatch):
    """THE third-outcome pin: a command policy allows only simulation — not
    executed, not refused — and the receipt says SIMULATED, distinct from both."""
    import core.policy_engine as policy_engine

    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda path, default=None: {
            "execution.allow_sandbox_execution": False,
            "execution.allow_simulation": True,
        }.get(path, default),
    )
    result = _evaluate_command("cat notes.txt")
    assert result["decision"] == "simulate_only"
    receipts = list(effect_receipts())
    assert receipts, "the simulated outcome left no receipt"
    assert receipts[-1]["decision"] == DECISION_SIMULATED
    assert receipts[-1]["decision"] != DECISION_DENIED
    assert receipts[-1]["decision"] != DECISION_ALLOWED


def test_the_turn_policy_is_frozen_and_server_derived():
    policy = TurnPolicy.from_source_context(
        {"surface": "channel", "platform": "telegram"}
    )
    assert policy.principal == "remote_untrusted"
    owner = TurnPolicy.from_source_context({"surface": "cli", "platform": "cli"})
    assert owner.principal == "owner_local"
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.principal = "owner_local"
