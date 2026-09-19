"""Permission authority is not optional — the absence of a mode is never a bypass.

The defect this pins: `execute_tool_intent` consulted `decide_tool_call` only when
`mode_policy_is_active(source_context)` said the caller had "opted in" to the mode contract. A
caller that carried no `operating_mode` (and had no server-side session record) therefore reached
workspace writes and side-effecting shell commands with NO permission decision at all — measured on
the base commit: a modeless `workspace.write_file` created the file on disk and
`decide_tool_call` was never called once.

Defaulting the missing mode to AUTO would not fix it: AUTO *permits* writes and side-effecting
commands, so the same actions still run unasked. The only safe default is MANUAL, where mutations
require an approval and reads still work.

Every test here drives the real seam — `execute_tool_intent` with the real handler stack, or the
real HTTP ingress — and asserts on the environment (did the file appear? did the command's side
effect land?) rather than on prose.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from apps.vool_api_server import _dispatch_post
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import (
    OperatingMode,
    PermissionAction,
    PermissionEffect,
    activate_bypass_grant,
    active_mode_state,
    decide_tool_call,
    grant_internal_authority,
    reset_mode_permission_state,
    resolve_approval,
    resolve_effective_mode,
    set_active_mode,
)
from core.tool_intent_executor import execute_tool_intent
from core.web.api.runtime import RuntimeServices


@pytest.fixture(autouse=True)
def _clean_mode_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


def _run(intent: str, arguments: dict[str, Any], *, context: dict[str, Any], task: str = "task-a"):
    """Drive the real executor and record whether the permission seam was consulted.

    The spy WRAPS the real `decide_tool_call` — it does not replace its answer — so a test that
    passes here passes against real policy, not against a stub that always allows.
    """
    import core.tool_intent_executor as tie

    seen: list[str] = []
    real = tie.decide_tool_call

    def spy(**kwargs):
        seen.append(str(kwargs.get("intent") or ""))
        return real(**kwargs)

    with mock.patch.object(tie, "decide_tool_call", side_effect=spy):
        execution = execute_tool_intent(
            {"intent": intent, "arguments": arguments},
            task_id=task,
            session_id=str(context.get("runtime_session_id") or "sess"),
            source_context=context,
            hive_activity_tracker=_tracker(),
        )
    return execution, seen


# --------------------------------------------------------------------------------------
# RED 1 + 2 — a modeless caller reaches real side effects with no permission decision.
# --------------------------------------------------------------------------------------
def test_modeless_workspace_write_is_permission_decided_and_does_not_touch_disk(tmp_path) -> None:
    context = {"workspace_root": str(tmp_path), "runtime_session_id": "sess-modeless-write"}

    execution, consulted = _run(
        "workspace.write_file",
        {"path": "pwned.txt", "content": "written with no mode at all"},
        context=context,
    )

    assert consulted == ["workspace.write_file"], "the permission seam must run for EVERY dispatch"
    assert execution.status == "pending_approval"
    assert execution.details["executed"] is False
    assert execution.details["operating_mode"] == OperatingMode.MANUAL.value
    # The environmental assertion is the one that matters: no mode must mean no write.
    assert not (tmp_path / "pwned.txt").exists()


def test_modeless_shell_command_is_permission_decided_and_side_effect_never_lands(tmp_path) -> None:
    context = {"workspace_root": str(tmp_path), "runtime_session_id": "sess-modeless-shell"}
    marker = tmp_path / "side_effect.txt"

    execution, consulted = _run(
        "sandbox.run_command",
        {"command": f"touch {marker.name}", "cwd": str(tmp_path)},
        context=context,
    )

    assert consulted == ["sandbox.run_command"]
    assert execution.status == "pending_approval"
    assert execution.details["executed"] is False
    assert not marker.exists(), "a side-effecting command ran without any permission decision"


def test_modeless_reads_still_work_so_the_default_is_manual_not_a_blanket_block(tmp_path) -> None:
    """The control that keeps the fix from being 'deny everything'.

    MANUAL allows reads. If this goes red the default became PLAN-or-stricter, which would be a
    different defect wearing the fix's clothes.
    """
    (tmp_path / "readable.txt").write_text("visible", encoding="utf-8")
    context = {"workspace_root": str(tmp_path), "runtime_session_id": "sess-modeless-read"}

    execution, consulted = _run("workspace.read_file", {"path": "readable.txt"}, context=context)

    assert consulted == ["workspace.read_file"], "reads are decided too — allowed, but decided"
    assert execution.ok is True
    assert execution.status == "executed"
    assert "visible" in (execution.response_text or "")


# --------------------------------------------------------------------------------------
# RED 3 — HTTP ingress must stamp a controller-owned mode on every chat turn.
# --------------------------------------------------------------------------------------
def _chat_source_context(body: dict[str, Any]) -> dict[str, Any]:
    runtime = RuntimeServices(display_name="VOOL")
    seen: list[dict[str, Any]] = []

    def fake_run_agent(user_text: str, *, session_id: str | None = None, source_context: dict[str, Any] | None = None):
        seen.append(dict(source_context or {}))
        return {"response": "ok", "confidence": 1.0}

    with mock.patch("apps.vool_api_server._run_agent", side_effect=fake_run_agent):
        response = _dispatch_post(
            path="/api/chat",
            body=body,
            headers={"content-type": "application/json"},
            runtime=runtime,
            model_name="vool",
            workspace_root_provider=lambda: "/tmp",
        )
    assert response.status == 200
    assert len(seen) == 1
    return seen[0]


def test_http_chat_without_a_mode_still_stamps_controller_owned_manual() -> None:
    context = _chat_source_context(
        {"model": "vool", "session_id": "http-nomode", "messages": [{"role": "user", "content": "hello"}]}
    )
    assert context["operating_mode"] == OperatingMode.MANUAL.value
    assert "operating_mode_revision" in context


def test_http_chat_with_an_unsupported_mode_lands_on_manual_not_on_nothing() -> None:
    """`mode: "bypass"` is not a supported value.

    It used to be DROPPED — which left the turn modeless, and modeless was the bypass. It must now
    resolve to MANUAL: the strictest reading, and never the bypass it names.
    """
    context = _chat_source_context(
        {
            "model": "vool",
            "session_id": "http-badmode",
            "mode": "bypass",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    assert context["operating_mode"] == OperatingMode.MANUAL.value


def test_http_chat_with_an_explicit_mode_is_unchanged() -> None:
    context = _chat_source_context(
        {
            "model": "vool",
            "session_id": "http-plan",
            "mode": "Plan",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    assert context["operating_mode"] == OperatingMode.PLAN.value


def test_a_later_modeless_turn_keeps_the_session_mode_it_was_given() -> None:
    """Turn 1 selects Auto; turn 2 omits the field. The controller's mode must persist.

    Otherwise the fix would silently downgrade every second turn to MANUAL and drown the user in
    approvals for a mode they already chose.
    """
    first = _chat_source_context(
        {
            "model": "vool",
            "session_id": "http-sticky",
            "mode": "auto",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    assert first["operating_mode"] == OperatingMode.AUTO.value
    second = _chat_source_context(
        {"model": "vool", "session_id": "http-sticky", "messages": [{"role": "user", "content": "again"}]}
    )
    assert second["operating_mode"] == OperatingMode.AUTO.value


# --------------------------------------------------------------------------------------
# RED 4 — a restart drops in-memory session state; that must not reopen the hole.
# --------------------------------------------------------------------------------------
def test_restart_losing_session_state_does_not_reopen_the_bypass(tmp_path) -> None:
    set_active_mode("sess-restart", "auto", client_turn_id="turn-1")
    context = {"workspace_root": str(tmp_path), "runtime_session_id": "sess-restart"}

    # The process restarts: in-memory mode state is gone, but the client keeps talking to the
    # same session id and (being a non-chat/internal caller) sends no operating_mode.
    reset_mode_permission_state()

    execution, consulted = _run("workspace.write_file", {"path": "after_restart.txt", "content": "x"}, context=context)

    assert consulted == ["workspace.write_file"]
    assert execution.details["executed"] is False
    assert not (tmp_path / "after_restart.txt").exists()


# --------------------------------------------------------------------------------------
# RED 5 — a caller-supplied mode string cannot mint its own authority.
# --------------------------------------------------------------------------------------
def test_spoofed_bypass_in_the_context_cannot_mint_authority(tmp_path) -> None:
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-spoof",
        "operating_mode": "bypass_permissions",
        "bypass_token": "not-a-real-token",
    }

    execution, consulted = _run("workspace.write_file", {"path": "spoofed.txt", "content": "x"}, context=context)

    assert consulted == ["workspace.write_file"]
    assert active_mode_state(context)["mode"] == OperatingMode.MANUAL.value
    assert execution.details["executed"] is False
    assert not (tmp_path / "spoofed.txt").exists()


def test_invalid_mode_string_falls_to_manual_not_to_auto(tmp_path) -> None:
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-garbage",
        "operating_mode": "super_auto_do_whatever",
    }

    execution, consulted = _run("workspace.write_file", {"path": "garbage.txt", "content": "x"}, context=context)

    assert consulted == ["workspace.write_file"]
    assert execution.details["operating_mode"] == OperatingMode.MANUAL.value
    assert not (tmp_path / "garbage.txt").exists()


def test_a_real_grant_still_produces_a_working_bypass(tmp_path) -> None:
    """The control proving the spoof tests above are not passing merely because bypass is broken."""
    from core.mode_permission_policy import request_bypass_confirmation

    _cid = request_bypass_confirmation(
        session_id="sess-real-bypass", task_id="turn-1", scope="task", duration_seconds=120
    )
    grant = activate_bypass_grant(
        session_id="sess-real-bypass", task_id="turn-1", scope="task", duration_seconds=120, confirmation_id=_cid
    )
    set_active_mode("sess-real-bypass", "bypass_permissions", client_turn_id="turn-1", bypass_token=grant["token"])
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-real-bypass",
        "cancel_turn_id": "turn-1",
    }

    execution, consulted = _run("workspace.write_file", {"path": "granted.txt", "content": "allowed"}, context=context)

    assert consulted == ["workspace.write_file"]
    assert execution.ok is True
    assert (tmp_path / "granted.txt").read_text(encoding="utf-8") == "allowed"


# --------------------------------------------------------------------------------------
# Every mode, through the real executor.
# --------------------------------------------------------------------------------------
def test_plan_mode_denies_mutations_through_the_executor(tmp_path) -> None:
    set_active_mode("sess-plan", "plan", client_turn_id="turn-1")
    context = {"workspace_root": str(tmp_path), "runtime_session_id": "sess-plan"}

    execution, _ = _run("workspace.write_file", {"path": "planned.txt", "content": "x"}, context=context)

    assert execution.status == "blocked_by_mode"
    assert not (tmp_path / "planned.txt").exists()


@pytest.mark.parametrize("mode", ["manual", "review_edits"])
def test_manual_and_review_require_approval_for_mutations(tmp_path, mode: str) -> None:
    set_active_mode(f"sess-{mode}", mode, client_turn_id="turn-1")
    context = {"workspace_root": str(tmp_path), "runtime_session_id": f"sess-{mode}"}

    execution, _ = _run("workspace.write_file", {"path": f"{mode}.txt", "content": "x"}, context=context)

    assert execution.status == "pending_approval"
    assert not (tmp_path / f"{mode}.txt").exists()


def test_auto_mode_contract_is_preserved(tmp_path) -> None:
    """Auto's explicit contract must not change: a new-file write runs, an overwrite still prompts."""
    set_active_mode("sess-auto", "auto", client_turn_id="turn-1")
    context = {"workspace_root": str(tmp_path), "runtime_session_id": "sess-auto"}

    execution, _ = _run("workspace.write_file", {"path": "fresh.txt", "content": "new"}, context=context)
    assert execution.ok is True
    assert (tmp_path / "fresh.txt").read_text(encoding="utf-8") == "new"

    (tmp_path / "user_work.txt").write_text("do not clobber", encoding="utf-8")
    overwrite, _ = _run("workspace.write_file", {"path": "user_work.txt", "content": "clobbered"}, context=context)
    assert overwrite.status == "pending_approval"
    assert (tmp_path / "user_work.txt").read_text(encoding="utf-8") == "do not clobber"


# --------------------------------------------------------------------------------------
# Approval resume — the prompt a modeless write now raises must be completable.
# --------------------------------------------------------------------------------------
def test_a_modeless_write_can_be_approved_and_then_runs(tmp_path) -> None:
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-approve",
        "cancel_turn_id": "turn-approve",
    }
    arguments = {"path": "approved.txt", "content": "operator said yes"}

    pending, _ = _run("workspace.write_file", arguments, context=context, task="task-approve")
    assert pending.status == "pending_approval"
    token = str(pending.details["approval_request"]["approval_id"])
    assert not (tmp_path / "approved.txt").exists()

    assert resolve_approval(token, decision="allow") is not None

    resumed, consulted = _run(
        "workspace.write_file",
        arguments,
        context={**context, "mode_approval_token": token},
        task="task-approve",
    )
    assert consulted == ["workspace.write_file"], "the resume is still a decision, not a skip"
    assert resumed.ok is True
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "operator said yes"


def test_an_approval_does_not_carry_to_a_different_call(tmp_path) -> None:
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-approve-scope",
        "cancel_turn_id": "turn-scope",
    }
    pending, _ = _run(
        "workspace.write_file", {"path": "one.txt", "content": "a"}, context=context, task="task-scope"
    )
    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None

    other, _ = _run(
        "workspace.write_file",
        {"path": "two.txt", "content": "b"},
        context={**context, "mode_approval_token": token},
        task="task-scope",
    )
    assert other.details["executed"] is False
    assert not (tmp_path / "two.txt").exists()


# --------------------------------------------------------------------------------------
# Outward effect — the class where a missing decision costs the most.
# --------------------------------------------------------------------------------------
def test_modeless_outward_effect_is_not_dispatched(tmp_path) -> None:
    """`pay.x402` names a money effect outside this machine — a real dispatchable tool, not a
    stand-in. Today the tool itself is a retired surface behind the wallet authority (it answers a
    typed refusal); the controller must still decide BEFORE that tool is ever reached."""
    from core.faults.recorder import list_faults
    from core.wallet.authority import LEGACY_RETIRED

    context = {"workspace_root": str(tmp_path), "runtime_session_id": "sess-outward"}
    arguments = {"resource": "https://compute.example.test/job"}

    decision = decide_tool_call(
        intent="pay.x402", arguments=arguments, task_id="task-pay", source_context=context
    )
    assert decision.effect is not PermissionEffect.ALLOW

    with mock.patch("core.web0_tools.dna_pay_and_unlock") as paid:
        execution, consulted = _run("pay.x402", arguments, context=context)

    assert consulted == ["pay.x402"]
    assert execution.status == "pending_approval"
    assert execution.details["executed"] is False
    paid.assert_not_called()
    # The decision came first: the retired tool's own refusal receipt was never filed.
    assert list_faults(code=LEGACY_RETIRED, limit=10) == []


# --------------------------------------------------------------------------------------
# The typed internal scope — the ONLY legitimate non-chat exception.
# --------------------------------------------------------------------------------------
def test_internal_authority_needs_an_explicit_typed_grant(tmp_path) -> None:
    """A background caller may be given narrow authority — but it must ASK for it, by token.

    Absence of a mode is not that grant. This is the seam that keeps the fix from breaking real
    internal callers without reopening the hole for every caller that simply forgot a mode.
    """
    token = grant_internal_authority(
        label="test-background-writer",
        actions={PermissionAction.CREATE_FILES},
        duration_seconds=300,
        session_id="sess-internal",
    )
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-internal",
        "internal_authority_token": token,
    }

    execution, consulted = _run("workspace.write_file", {"path": "internal.txt", "content": "ok"}, context=context)
    assert consulted == ["workspace.write_file"], "the scope is consulted INSIDE the decision, not around it"
    assert execution.ok is True
    assert (tmp_path / "internal.txt").read_text(encoding="utf-8") == "ok"


def test_internal_authority_cannot_exceed_its_declared_actions(tmp_path) -> None:
    token = grant_internal_authority(
        label="test-narrow",
        actions={PermissionAction.CREATE_FILES},
        duration_seconds=300,
        session_id="sess-internal-narrow",
    )
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-internal-narrow",
        "internal_authority_token": token,
    }
    marker = tmp_path / "escalated.txt"

    execution, _ = _run(
        "sandbox.run_command", {"command": f"touch {marker.name}", "cwd": str(tmp_path)}, context=context
    )

    assert execution.details["executed"] is False
    assert not marker.exists(), "a file-creation scope must not carry command execution"


def test_a_forged_internal_authority_token_grants_nothing(tmp_path) -> None:
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-forged",
        "internal_authority_token": "i-made-this-up",
    }

    execution, _ = _run("workspace.write_file", {"path": "forged.txt", "content": "x"}, context=context)

    assert execution.details["executed"] is False
    assert not (tmp_path / "forged.txt").exists()


def test_internal_authority_can_never_reach_secrets_or_money(tmp_path) -> None:
    token = grant_internal_authority(
        label="test-overreach",
        actions={PermissionAction.ACCESS_SECRETS, PermissionAction.FINANCIAL_ACTION, PermissionAction.CREATE_FILES},
        duration_seconds=300,
        session_id="sess-overreach",
    )
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-overreach",
        "internal_authority_token": token,
    }

    for intent, arguments in (("credential.read", {"name": "openrouter"}), ("pay.send", {"amount": "1", "to": "x"})):
        decision = decide_tool_call(
            intent=intent, arguments=arguments, task_id="task-o", source_context=context
        )
        assert decision.effect is not PermissionEffect.ALLOW, f"{intent} must stay outside any internal scope"


# --------------------------------------------------------------------------------------
# The central authority itself.
# --------------------------------------------------------------------------------------
def test_resolve_effective_mode_defaults_to_manual_never_auto() -> None:
    state = resolve_effective_mode(session_id="sess-authority", requested_mode=None)
    assert state["mode"] == OperatingMode.MANUAL.value

    for junk in ("", "   ", "auto-ish", "bypass", None, 17, object()):
        resolved = resolve_effective_mode(session_id=f"sess-junk-{id(junk)}", requested_mode=junk)
        assert resolved["mode"] == OperatingMode.MANUAL.value, f"{junk!r} must not resolve to a permissive mode"


def test_resolve_effective_mode_will_not_mint_bypass_without_a_grant() -> None:
    state = resolve_effective_mode(session_id="sess-mint", requested_mode="bypass_permissions")
    assert state["mode"] == OperatingMode.MANUAL.value


def test_resolve_effective_mode_honors_a_real_bypass_grant() -> None:
    from core.mode_permission_policy import request_bypass_confirmation

    _cid = request_bypass_confirmation(
        session_id="sess-mint-ok", task_id="turn-1", scope="task", duration_seconds=120
    )
    grant = activate_bypass_grant(
        session_id="sess-mint-ok", task_id="turn-1", scope="task", duration_seconds=120, confirmation_id=_cid
    )
    state = resolve_effective_mode(
        session_id="sess-mint-ok",
        requested_mode="bypass_permissions",
        client_turn_id="turn-1",
        bypass_token=grant["token"],
    )
    assert state["mode"] == OperatingMode.BYPASS_PERMISSIONS.value


def test_every_supported_mode_round_trips_through_the_authority() -> None:
    for mode in (OperatingMode.MANUAL, OperatingMode.REVIEW_EDITS, OperatingMode.PLAN, OperatingMode.AUTO):
        state = resolve_effective_mode(session_id=f"sess-rt-{mode.value}", requested_mode=mode.value)
        assert state["mode"] == mode.value
        assert "revision" in state


# --------------------------------------------------------------------------------------
# Gap repairs — an EXPLICITLY INVALID mode is not an omitted one, and an internal
# authority is never a process-lifetime, action-only bearer.
# --------------------------------------------------------------------------------------
def test_an_explicitly_invalid_mode_never_inherits_the_sessions_auto() -> None:
    """Omitted and invalid are different facts and must not collapse.

    The controller set AUTO for this session. A turn that OMITS the mode keeps that
    selection (next test). A turn that EXPLICITLY names a mode the authority cannot
    parse must fail closed for THAT turn — MANUAL plus a marker naming the refused
    value — even though the session record says auto.
    """
    resolve_effective_mode(session_id="sess-inv-auto", requested_mode="auto")
    resolved = resolve_effective_mode(session_id="sess-inv-auto", requested_mode="turbo mode")
    assert resolved["mode"] == OperatingMode.MANUAL.value
    assert str(resolved.get("invalid_mode_refused") or "") == "turbo mode"


def test_an_omitted_mode_still_preserves_the_sessions_auto_selection() -> None:
    """Negative control for the test above: omission is the honest `keep` case."""
    resolve_effective_mode(session_id="sess-inv-keep", requested_mode="auto")
    resolved = resolve_effective_mode(session_id="sess-inv-keep", requested_mode=None)
    assert resolved["mode"] == OperatingMode.AUTO.value


def test_the_front_door_refuses_an_invalid_mode_in_an_auto_session() -> None:
    """The same hole through the real ingress: `/api/chat` dropped an unrecognised
    `mode` to "omitted", so an AUTO session ran the turn under AUTO. It must answer a
    typed refusal naming the value instead, and the turn must not run."""
    import json as _json

    runtime = RuntimeServices(display_name="VOOL")
    resolve_effective_mode(session_id="http-inv-auto", requested_mode="auto")
    with mock.patch("apps.vool_api_server._run_agent") as fake_agent:
        response = _dispatch_post(
            path="/api/chat",
            body={
                "model": "vool",
                "session_id": "http-inv-auto",
                "mode": "fullauto",
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers={"content-type": "application/json"},
            runtime=runtime,
            model_name="vool",
            workspace_root_provider=lambda: "/tmp",
        )
    assert response.status == 400
    assert "fullauto" in _json.loads(response.body.decode("utf-8")).get("error", "")
    fake_agent.assert_not_called()


def test_internal_authority_refuses_to_be_unbounded() -> None:
    """No duration, no binding: not mintable. The token may not be a process-lifetime,
    action-only bearer — expiry is mandatory."""
    with pytest.raises(ValueError):
        grant_internal_authority(label="unbounded", actions={PermissionAction.CREATE_FILES})


def test_internal_authority_requires_a_binding_beyond_its_actions() -> None:
    """A duration alone is not enough: the scope must also name WHAT it is for — a
    session, a task, an intent set, or a workspace/target root."""
    with pytest.raises(ValueError):
        grant_internal_authority(
            label="action-only", actions={PermissionAction.CREATE_FILES}, duration_seconds=60
        )


def test_internal_scope_honours_its_session_binding(tmp_path) -> None:
    token = grant_internal_authority(
        label="sess-bound-writer",
        actions={PermissionAction.CREATE_FILES},
        duration_seconds=120,
        session_id="sess-bound-a",
    )
    inside = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-bound-a",
        "internal_authority_token": token,
    }
    outside = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-bound-b",
        "internal_authority_token": token,
    }

    ok_run, _ = _run("workspace.write_file", {"path": "inside.txt", "content": "x"}, context=inside)
    assert ok_run.ok is True
    assert (tmp_path / "inside.txt").exists()

    refused, _ = _run("workspace.write_file", {"path": "outside.txt", "content": "x"}, context=outside)
    assert refused.details["executed"] is False
    assert not (tmp_path / "outside.txt").exists(), "a token from another session must carry nothing"


def test_internal_scope_honours_its_workspace_binding(tmp_path) -> None:
    root_a = tmp_path / "job-a"
    root_a.mkdir()
    token = grant_internal_authority(
        label="ws-bound-writer",
        actions={PermissionAction.CREATE_FILES},
        duration_seconds=120,
        workspace_root=str(root_a),
    )
    inside = {
        "workspace_root": str(root_a),
        "runtime_session_id": "sess-ws",
        "internal_authority_token": token,
    }
    outside = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-ws",
        "internal_authority_token": token,
    }

    ok_run, _ = _run("workspace.write_file", {"path": "in-root.txt", "content": "x"}, context=inside)
    assert ok_run.ok is True
    assert (root_a / "in-root.txt").exists()

    refused, _ = _run("workspace.write_file", {"path": "escaped.txt", "content": "x"}, context=outside)
    assert refused.details["executed"] is False
    assert not (tmp_path / "escaped.txt").exists()


def test_internal_scope_expires_rather_than_living_for_the_process(tmp_path) -> None:
    import core.mode_permission_policy as mpp

    token = grant_internal_authority(
        label="short-lived-writer",
        actions={PermissionAction.CREATE_FILES},
        duration_seconds=30,
        session_id="sess-exp",
    )
    context = {
        "workspace_root": str(tmp_path),
        "runtime_session_id": "sess-exp",
        "internal_authority_token": token,
    }
    real_time = mpp.time.time
    with mock.patch.object(mpp.time, "time", side_effect=lambda: real_time() + 10_000):
        decision = decide_tool_call(
            intent="workspace.write_file",
            arguments={"path": "later.txt", "content": "x"},
            task_id="task-exp",
            source_context=context,
        )
    assert decision.effect is not PermissionEffect.ALLOW, "an expired scope must decide like no scope"
