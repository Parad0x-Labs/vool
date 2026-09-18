"""The in-chat x402 emergency brake: /stopx402 freezes spending, /startx402 resumes.

The freeze is canonical in core.wallet.limits (set_frozen / is_frozen -- the gate every wallet
proposal, reservation and approval consults) and mirrored into the legacy, device-keyed policy
file for the surfaces that still read it. No wallet is loaded: the brake runs with
``wallet_fn=None``, the production shape. These lock in that a stop actually blocks spending in
BOTH stores, that a failed persist never reports a state the wallet is not in, and that only an
explicit command fires.
"""
from __future__ import annotations

import dataclasses

import pytest

import core.wallet_spend_policy_store as store
from core.vool_agent_brake import maybe_handle_agent_brake
from core.wallet import limits
from core.wallet_spend_policy import SpendLedger, SpendPolicy, check_spend_allowed

DESTINATION = "11111111111111111111111111111111"


class _State:
    def __init__(self, frozen: bool = False) -> None:
        self.policy = SpendPolicy(frozen=frozen)
        self.ledger = SpendLedger()
        self.saves = 0
        self.wallets_seen: list = []


def _wire(monkeypatch, state: _State, *, fail_save: bool = False) -> None:
    def _load(wallet, **_kw):
        # Like the real store, a load hands out a fresh object: only a successful save changes `state`.
        state.wallets_seen.append(wallet)
        return dataclasses.replace(state.policy), SpendLedger(entries=list(state.ledger.entries))

    monkeypatch.setattr(store, "load_policy_and_ledger", _load)

    def _save(wallet, policy, ledger, *, now=None):
        state.wallets_seen.append(wallet)
        if fail_save:
            raise RuntimeError("disk full")
        state.policy, state.ledger = policy, ledger
        state.saves += 1

    monkeypatch.setattr(store, "save_policy_and_ledger", _save)
    # keep the canonical store in step with the scripted legacy state
    limits.set_frozen(state.policy.frozen)


@pytest.fixture(autouse=True)
def _canonical_thawed():
    limits.set_frozen(False)
    yield
    limits.set_frozen(False)


def _call(text: str, *, consent=lambda _reason: True):
    # Unfreeze (/startx402) requires OS consent; default the stub to granted so the
    # existing happy-path unfreeze assertions hold. Freeze never calls consent.
    return maybe_handle_agent_brake(text, "sess-1", wallet_fn=None, consent_fn=consent)


def _canonical_verdict():
    return limits.check_limits("wallet-1", "SOL", 1, DESTINATION, now=1_000.0)


def test_stopx402_freezes_both_stores_and_blocks_a_spend(monkeypatch) -> None:
    state = _State()
    _wire(monkeypatch, state)
    result = _call("/stopx402")
    assert result is not None and result["success"] and result["intent"] == "agent_brake_stop"
    assert state.policy.frozen is True and state.saves == 1
    assert state.wallets_seen and all(w is None for w in state.wallets_seen), "no wallet may be loaded for a freeze"
    # The legacy mirror is the real gate for the surfaces that read it...
    allowed, reason = check_spend_allowed(state.policy, state.ledger, 1_000, 0.0)
    assert allowed is False and "frozen" in reason
    # ...and the canonical freeze is what the wallet lifecycle consults.
    assert limits.is_frozen() is True
    verdict = _canonical_verdict()
    assert verdict.ok is False and verdict.limit == limits.LIMIT_FROZEN


def test_startx402_unfreezes_both_stores(monkeypatch) -> None:
    state = _State(frozen=True)
    _wire(monkeypatch, state)
    assert limits.is_frozen() is True
    result = _call("/startx402")
    assert result is not None and result["success"] and result["intent"] == "agent_brake_start"
    assert state.policy.frozen is False and state.saves == 1
    allowed, _ = check_spend_allowed(state.policy, state.ledger, 1_000, 0.0)
    assert allowed is True
    assert limits.is_frozen() is False and _canonical_verdict().ok is True


def test_stop_when_already_frozen_is_idempotent(monkeypatch) -> None:
    state = _State(frozen=True)
    _wire(monkeypatch, state)
    result = _call("/stopx402")
    assert result is not None and result["success"]
    assert "already frozen" in result["response"].lower()
    assert state.policy.frozen is True and limits.is_frozen() is True


def test_stop_reports_already_frozen_when_only_the_canonical_store_is_frozen(monkeypatch) -> None:
    # The canonical freeze is authoritative: a legacy mirror that lagged behind does not make the
    # brake claim it just froze a wallet that was already frozen.
    state = _State(frozen=False)
    _wire(monkeypatch, state)
    limits.set_frozen(True)
    result = _call("/stopx402")
    assert result is not None and result["success"]
    assert "already frozen" in result["response"].lower()
    assert state.policy.frozen is True and limits.is_frozen() is True


def test_start_when_not_frozen_reports_already_active(monkeypatch) -> None:
    state = _State(frozen=False)
    _wire(monkeypatch, state)
    result = _call("/startx402")
    assert result is not None and "already active" in result["response"].lower()
    assert limits.is_frozen() is False


def test_failed_persist_never_reports_a_false_stop(monkeypatch) -> None:
    state = _State(frozen=False)
    _wire(monkeypatch, state, fail_save=True)
    result = _call("/stopx402")
    assert result is not None
    assert result["success"] is False
    assert "not stopped" in result["response"].lower()
    assert state.saves == 0  # nothing persisted
    # The response says spending is NOT stopped and the policy is unchanged: the canonical store
    # must agree with that report, or the user is told the opposite of what the wallet will do.
    assert limits.is_frozen() is False, "the canonical freeze flipped while the brake reported 'not stopped'"


def test_failed_persist_on_resume_leaves_spending_frozen(monkeypatch) -> None:
    # The risk-increasing direction: a resume whose persistence fails must leave BOTH stores frozen.
    # The response promises "Your spend policy is unchanged"; spending must not silently resume.
    state = _State(frozen=True)
    _wire(monkeypatch, state, fail_save=True)
    result = _call("/startx402")
    assert result is not None and result["success"] is False
    assert "unchanged" in result["response"].lower()
    assert state.policy.frozen is True and state.saves == 0
    assert limits.is_frozen() is True, "the canonical freeze was lifted while the brake reported the policy unchanged"
    assert _canonical_verdict().ok is False


def test_natural_language_variants_trigger_the_brake(monkeypatch) -> None:
    for phrase in ("freeze spending", "emergency stop", "pause the agent", "stop the x402 agent",
                   "please freeze the wallet", "panic"):
        state = _State()
        _wire(monkeypatch, state)
        assert _call(phrase) is not None, f"should have fired: {phrase!r}"
        assert state.policy.frozen is True, f"did not freeze: {phrase!r}"
        assert limits.is_frozen() is True, f"canonical freeze missing: {phrase!r}"
    for phrase in ("start x402", "resume spending", "unfreeze", "resume the agent"):
        state = _State(frozen=True)
        _wire(monkeypatch, state)
        assert _call(phrase) is not None, f"should have fired: {phrase!r}"
        assert state.policy.frozen is False, f"did not unfreeze: {phrase!r}"
        assert limits.is_frozen() is False, f"canonical freeze not lifted: {phrase!r}"


def test_startx402_denied_consent_keeps_spending_frozen(monkeypatch) -> None:
    # Declining the OS prompt must leave the freeze in place and persist nothing.
    state = _State(frozen=True)
    _wire(monkeypatch, state)
    result = _call("/startx402", consent=lambda _reason: False)
    assert result is not None and result["success"] is False
    assert state.policy.frozen is True
    assert state.saves == 0
    assert limits.is_frozen() is True


def test_startx402_consent_exception_keeps_spending_frozen(monkeypatch) -> None:
    # An unavailable/raising consent gate fails closed (spending stays frozen).
    state = _State(frozen=True)
    _wire(monkeypatch, state)

    def _boom(_reason):
        raise RuntimeError("Windows Hello unavailable")

    result = _call("/startx402", consent=_boom)
    assert result is not None and result["success"] is False
    assert state.policy.frozen is True
    assert state.saves == 0
    assert limits.is_frozen() is True


def test_stopall_launches_the_detached_hard_stop() -> None:
    hits = []
    result = maybe_handle_agent_brake("/stopall", "s", stopall_fn=lambda: hits.append(1) or True)
    assert result is not None and result["success"] and result["intent"] == "agent_brake_stopall"
    assert hits == [1]


def test_stopall_blocked_for_non_owner() -> None:
    # A non-owner-local caller (remote channel or non-loopback HTTP) cannot shut VOOL down.
    hits = []
    result = maybe_handle_agent_brake(
        "/stopall", "s", stopall_fn=lambda: hits.append(1) or True, owner_local=False
    )
    assert result is not None and result["success"] is False
    assert result["intent"] == "agent_brake_stopall"
    assert hits == []  # shutdown never launched


def test_unfreeze_blocked_for_non_owner(monkeypatch) -> None:
    # A non-owner caller cannot resume spending; it stays frozen and consent is never even asked.
    state = _State(frozen=True)
    _wire(monkeypatch, state)
    consent_calls = []
    result = maybe_handle_agent_brake(
        "/startx402", "s", wallet_fn=None,
        consent_fn=lambda _r: consent_calls.append(1) or True, owner_local=False,
    )
    assert result is not None and result["success"] is False
    assert consent_calls == []          # never prompted
    assert state.policy.frozen is True  # stays frozen
    assert limits.is_frozen() is True


def test_freeze_allowed_from_any_surface(monkeypatch) -> None:
    # Freezing is the safe direction and stays reachable even for a non-owner caller.
    state = _State()
    _wire(monkeypatch, state)
    result = maybe_handle_agent_brake("/stopx402", "s", wallet_fn=None, owner_local=False)
    assert result is not None and result["intent"] == "agent_brake_stop"
    assert state.policy.frozen is True and limits.is_frozen() is True


def test_stopall_variants_all_fire() -> None:
    for phrase in ("/stopall", "stop all vool", "kill vool", "stop everything",
                   "shut down vool", "shutdown vool", "kill everything"):
        hits = []
        result = maybe_handle_agent_brake(phrase, "s", stopall_fn=lambda h=hits: h.append(1) or True)
        assert result is not None and result["intent"] == "agent_brake_stopall", f"missed: {phrase!r}"
        assert hits, f"did not launch: {phrase!r}"


def test_stopall_launch_failure_points_at_the_desktop_button() -> None:
    result = maybe_handle_agent_brake("/stopall", "s", stopall_fn=lambda: False)
    assert result is not None and result["success"] is False
    assert "Stop VOOL" in result["response"]


def test_stopx402_is_not_treated_as_stopall(monkeypatch) -> None:
    # /stopx402 must hit the freeze path, never the shutdown path.
    state = _State()
    _wire(monkeypatch, state)
    stopall_calls = []
    result = maybe_handle_agent_brake(
        "/stopx402", "s", wallet_fn=None,
        stopall_fn=lambda: stopall_calls.append(1) or True,
    )
    assert result is not None and result["intent"] == "agent_brake_stop"
    assert stopall_calls == []  # shutdown never invoked
    assert state.policy.frozen is True


def test_questions_and_unrelated_text_do_not_fire(monkeypatch) -> None:
    # Full-string anchored: anything with extra words or a question falls through untouched.
    state = _State()
    _wire(monkeypatch, state)
    for phrase in (
        "should I stop the agent?",
        "why did the agent stop spending",
        "how do I freeze the wallet",
        "what does /stopx402 do",
        "tell me about the x402 agent",
        "hello",
        "",
    ):
        assert _call(phrase) is None, f"should NOT have fired: {phrase!r}"
    assert state.saves == 0  # never touched the policy
    assert limits.is_frozen() is False


def test_brake_never_mints_a_wallet(tmp_path, monkeypatch) -> None:
    # The real stores, an isolated home, no wallet_fn: the freeze lands in both stores and no legacy
    # key file appears anywhere under the home.
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    result = _call("/stopx402")
    assert result is not None and result["success"]
    policy, _ledger = store.load_policy_and_ledger(None)
    assert policy.frozen is True and limits.is_frozen() is True
    assert not list(tmp_path.rglob("solana_wallet.enc"))


def test_a_canonical_flip_failure_rolls_the_legacy_mirror_back(monkeypatch) -> None:
    # The mirrored flip writes the legacy file FIRST and the canonical freeze LAST. If the canonical
    # flip raises, the legacy file is rolled back to its prior state: a caller that then reports
    # "policy unchanged" is telling the truth in BOTH stores (no half-applied freeze/resume).
    state = _State(frozen=True)
    _wire(monkeypatch, state)
    assert limits.is_frozen() is True

    def _explode(_frozen: bool) -> bool:
        raise RuntimeError("wallet_controls locked")

    monkeypatch.setattr(limits, "set_frozen", _explode)
    result = _call("/startx402")
    assert result is not None and result["success"] is False
    assert "unchanged" in result["response"].lower()
    # first save = the attempted resume, second save = the rollback; the mirror ends frozen again
    assert state.saves == 2
    assert state.policy.frozen is True
    monkeypatch.undo()
    assert limits.is_frozen() is True, "the canonical freeze must be untouched by a failed flip"
