"""Bypass elevation requires a server-minted, single-use, exact-bindings confirmation.

Security review finding (P0): activation accepted a caller-asserted
``explicit_confirmation`` boolean — any local process (or a model driving the
shell lane) could confirm on the user's behalf, replay it, or transfer it to a
different action.

The repair (core/mode_permission_policy.py): ``request_bypass_confirmation``
mints a 60-second, single-use confirmation bound to the EXACT activation
(session, scope, task, project, workspace, duration, until_off);
``activate_bypass_grant`` consumes it atomically. There is no phrase to say and
no boolean to assert. The registry gate refuses model-principal callers and any
request without a confirmation_id.
"""
from __future__ import annotations

import pytest

from core.mode_permission_policy import (
    activate_bypass_grant,
    request_bypass_confirmation,
    reset_mode_permission_state,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _mint(**overrides):
    kwargs = dict(session_id="chat-a", task_id="turn-a", scope="task", duration_seconds=600)
    kwargs.update(overrides)
    return request_bypass_confirmation(**kwargs), kwargs


class TestCallerCannotSelfConfirm:
    def test_missing_confirmation_refuses(self):
        with pytest.raises(PermissionError):
            activate_bypass_grant(session_id="chat-a", task_id="turn-a")

    def test_garbage_confirmation_refuses(self):
        with pytest.raises(PermissionError):
            activate_bypass_grant(session_id="chat-a", task_id="turn-a", confirmation_id="not-a-nonce")

    def test_minted_confirmation_activates_exactly_once(self):
        cid, kwargs = _mint()
        grant = activate_bypass_grant(**kwargs, confirmation_id=cid)
        assert grant["token"]

    def test_replay_cannot_approve_a_second_action(self):
        cid, kwargs = _mint()
        activate_bypass_grant(**kwargs, confirmation_id=cid)
        with pytest.raises(PermissionError, match=r"already used|not found"):
            activate_bypass_grant(**kwargs, confirmation_id=cid)


class TestConfirmationBoundToExactAction:
    @pytest.mark.parametrize("swap", [
        {"session_id": "chat-b"},
        {"task_id": "turn-b"},
        {"scope": "session"},
        {"duration_seconds": 1200},
    ])
    def test_swapped_binding_refuses(self, swap):
        cid, kwargs = _mint()
        kwargs.update(swap)
        with pytest.raises(PermissionError, match="different"):
            activate_bypass_grant(**kwargs, confirmation_id=cid)

    def test_confirmation_expires_quickly(self, monkeypatch):
        import core.mode_permission_policy as mpp

        cid, kwargs = _mint()
        real_time = mpp.time.time

        class _Clock:
            t = real_time()

        monkeypatch.setattr(mpp.time, "time", lambda: _Clock.t)
        _Clock.t = real_time() + 61.0
        with pytest.raises(PermissionError, match="expired"):
            activate_bypass_grant(**kwargs, confirmation_id=cid)


class TestRegistryGate:
    def test_gate_refuses_model_principal(self):
        from types import SimpleNamespace

        from core.command_registry.groups.convergence import (
            BypassActivateInput,
            _gate_bypass_confirmation,
        )

        inp = BypassActivateInput(session_id="chat-a", task_id="turn-a", confirmation_id="x" * 32)
        ctx = SimpleNamespace(principal="model")
        decision = _gate_bypass_confirmation(inp, ctx)
        assert decision.granted is False
        assert "operator-only" in decision.reason

    def test_gate_refuses_missing_confirmation_id(self):
        from types import SimpleNamespace

        from core.command_registry.groups.convergence import (
            BypassActivateInput,
            _gate_bypass_confirmation,
        )

        inp = BypassActivateInput(session_id="chat-a", task_id="turn-a")
        ctx = SimpleNamespace(principal="operator")
        decision = _gate_bypass_confirmation(inp, ctx)
        assert decision.granted is False
        assert "confirmation_id" in decision.reason

    def test_old_boolean_field_is_gone_from_the_wire_input(self):
        from core.command_registry.groups.convergence import BypassActivateInput

        assert not hasattr(BypassActivateInput(session_id="x"), "explicit_confirmation")
