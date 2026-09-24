"""The four coding/approval workflow repairs reported from the owner's beta acceptance test
(mission 2026-09-18, scope confirmation). Each test names the reported symptom and proves the
repaired owning contract, plus a genuinely different case and the refusal controls.

1. "Allow workspace edits in this chat" refused with "The chat workspace is not known yet --
   run one action in this chat first". The server owns the chat's workspace binding; the grant
   must resolve it there, so the FIRST action in a bound chat is approvable immediately, and an
   unbound chat gets the stable `reason: unbound` code that drives the existing folder-selection
   flow.
2. The finalbot folder/file execution narrated `FAILED_PROVIDER` over turns that were paused
   awaiting the operator's approval (and over question turns that created junk directories).
3. One approval of an unchanged, explicitly reviewed request kept being re-asked: the resumed
   turn re-emits the identical call and a consumed grant must still cover its exact fingerprint.
4. An internal tool-selection routing call under an explicit paid pin was reported as "Selected
   model could not run (internal_tool_intent_call)" -- a provider failure that never happened.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.mode_permission_policy import (
    PermissionEffect,
    decide_tool_call,
    preview_tool_call,
    resolve_approval,
)

DUMMY_KEY = "sk-ant-DUMMY-NOT-A-REAL-KEY"


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import os_consent_gate, runtime_paths
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )
    from storage.db import (
        active_default_db_path,
        configure_default_db_path,
        reset_default_connection,
    )
    from storage.migrations import run_migrations

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    os_consent_gate.set_consent_override_for_tests(lambda reason: False)
    # These tests mint/grant approvals through the PROCESS-GLOBAL mode/permission registry;
    # a PENDING entry left behind leaks past this suite (the residue that makes a later
    # approval-gated rig auto-resolve the wrong token, so its turn pauses as pending_approval
    # and never writes its conversation-log row). Same discipline as the sandbox-cwd isolation.
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    yield
    os_consent_gate.set_consent_override_for_tests(None)
    reset_mode_permission_state()
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    configure_default_db_path(None)
    reset_default_connection()
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _isolated_authority_store(tmp_path, monkeypatch):
    from core import mode_permission_policy as policy

    store = tmp_path / "authority" / "bypass_grants.json"
    monkeypatch.setattr(policy, "_bypass_grants_path", lambda: store)
    yield


def _ws(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir(exist_ok=True)
    return str(root)


def _bind_chat_to_workspace(session_id: str, ws: str) -> str:
    """The SERVER-OWNED binding the grant lane resolves (project + chat namespace)."""
    from core import project_store
    from core.context_namespace import ensure_chat_namespace

    ok, payload = project_store.create_project("workflow-repairs", ws)
    assert ok, payload
    project_id = str(payload["id"])
    namespace = ensure_chat_namespace(session_id, project_id=project_id, grant_confirmed_profile=True)
    assert namespace.lifecycle_state == "active"
    return project_id


def _write_context(session_id: str, turn_id: str, workspace: str) -> dict[str, Any]:
    return {
        "runtime_session_id": session_id,
        "session_id": session_id,
        "cancel_turn_id": turn_id,
        "workspace": workspace,
        "workspace_root": workspace,
    }


# ============================================================================================
# Repair 3 -- one approval covers the unchanged request; no re-ask for an already-granted call.
# ============================================================================================

def _ask_and_allow(session_id: str, turn_id: str, workspace: str, content: str) -> str:
    context = _write_context(session_id, turn_id, workspace)
    args = {"path": "src/thing.py", "content": content}
    decision = decide_tool_call(
        intent="workspace.write_file", arguments=dict(args), task_id=turn_id, source_context=context
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL
    assert decision.approval_request and decision.approval_request["approval_id"]
    token = decision.approval_request["approval_id"]
    resolved = resolve_approval(token, decision="allow", scope="once")
    assert resolved and resolved.get("status") == "approved"
    return token


def test_a_consumed_once_grant_covers_the_identical_reemitted_call(tmp_path):
    """The reported defect: after Allow, the resumed turn re-emits the SAME write and the
    operator is asked again. The prior grant covers its exact fingerprint -- no token needed."""
    ws = _ws(tmp_path)
    _ask_and_allow("chat-1", "turn-1", ws, "print('hello')\n")
    context = _write_context("chat-1", "turn-1", ws)
    args = {"path": "src/thing.py", "content": "print('hello')\n"}

    # First presentation with the token: allowed and consumed (the Allow once spend).
    spent = decide_tool_call(
        intent="workspace.write_file", arguments=dict(args), task_id="turn-1", source_context=context
    )
    assert spent.effect is PermissionEffect.ALLOW

    # The resumed turn re-emits the identical call with NO token in hand.
    resumed = decide_tool_call(
        intent="workspace.write_file", arguments=dict(args), task_id="turn-1", source_context=context
    )
    assert resumed.effect is PermissionEffect.ALLOW, (
        "an already-approved identical call must not prompt a second time"
    )
    preview = preview_tool_call(
        intent="workspace.write_file", arguments=dict(args), task_id="turn-1", source_context=context
    )
    assert preview.effect is PermissionEffect.ALLOW, "preview and consume must agree on prior coverage"


def test_a_changed_call_still_asks_after_a_prior_grant(tmp_path):
    """Allow once stays narrow: different bytes are a different call and prompt on their own."""
    ws = _ws(tmp_path)
    _ask_and_allow("chat-1", "turn-1", ws, "print('hello')\n")
    context = _write_context("chat-1", "turn-1", ws)
    changed = decide_tool_call(
        intent="workspace.write_file",
        arguments={"path": "src/thing.py", "content": "print('goodbye')\n"},
        task_id="turn-1",
        source_context=context,
    )
    assert changed.effect is PermissionEffect.REQUIRE_APPROVAL


def test_a_prior_grant_never_crosses_chat_or_turn_boundaries(tmp_path):
    """Another chat's grant, or the same chat's next turn, covers nothing."""
    ws = _ws(tmp_path)
    _ask_and_allow("chat-1", "turn-1", ws, "print('hello')\n")
    other_chat = decide_tool_call(
        intent="workspace.write_file",
        arguments={"path": "src/thing.py", "content": "print('hello')\n"},
        task_id="turn-1",
        source_context=_write_context("chat-2", "turn-1", ws),
    )
    assert other_chat.effect is PermissionEffect.REQUIRE_APPROVAL
    next_turn = decide_tool_call(
        intent="workspace.write_file",
        arguments={"path": "src/thing.py", "content": "print('hello')\n"},
        task_id="turn-2",
        source_context=_write_context("chat-1", "turn-2", ws),
    )
    assert next_turn.effect is PermissionEffect.REQUIRE_APPROVAL


def test_a_denied_call_is_never_covered_by_a_prior_ask(tmp_path):
    """Refusal control: a denied approval never becomes coverage for anything."""
    ws = _ws(tmp_path)
    context = _write_context("chat-1", "turn-1", ws)
    args = {"path": "src/evil.py", "content": "import os\n"}
    decision = decide_tool_call(
        intent="workspace.write_file", arguments=dict(args), task_id="turn-1", source_context=context
    )
    assert decision.effect is PermissionEffect.REQUIRE_APPROVAL
    resolve_approval(decision.approval_request["approval_id"], decision="deny")
    denied = decide_tool_call(
        intent="workspace.write_file", arguments=dict(args), task_id="turn-1", source_context=context
    )
    assert denied.effect is PermissionEffect.REQUIRE_APPROVAL


# ============================================================================================
# Repair 1 -- the chat workspace grant resolves the SERVER-OWNED binding on first action.
# ============================================================================================

def _pending_workspace_ask(session_id: str, turn_id: str, workspace: str) -> None:
    decide_tool_call(
        intent="workspace.write_file",
        arguments={"path": "src/a.py", "content": "x = 1\n"},
        task_id=turn_id,
        source_context=_write_context(session_id, turn_id, workspace),
    )


def _grant_chat_workspace(body: dict[str, Any]):
    import json as _json

    from core.web.api.service import RuntimeServices, dispatch_post

    response = dispatch_post(
        path="/api/mode",
        body={"op": "grant_chat_workspace", **body},
        headers={"Content-Type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: None,
        client_host="127.0.0.1",
    )
    raw = response.body
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        parsed = _json.loads(text)
    except ValueError:
        parsed = {"error": text}
    return SimpleNamespace(status=response.status, body=parsed)


def test_a_bound_workspace_allows_first_action_approval_immediately(tmp_path):
    """The reported refusal: a bound chat's FIRST action had to be run before approving.
    The grant mints against the server-owned binding with no claimed root at all."""
    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-bound", ws)
    _pending_workspace_ask("chat-bound", "turn-1", ws)
    response = _grant_chat_workspace({"session_id": "chat-bound", "workspace_root": ""})
    assert response.status == 200, response.body
    payload = response.body
    assert payload["ok"] is True
    assert payload["scope"]["workspace_root"]
    from core.mode_permission_policy import chat_workspace_authority_state

    assert chat_workspace_authority_state("chat-bound").get("workspace_root") == payload["scope"]["workspace_root"]


@pytest.mark.parametrize("alias", [False, True])
def test_a_claimed_root_matching_the_binding_grants_only_that_workspace(tmp_path, alias):
    from core.mode_permission_policy import chat_workspace_authority_state

    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-matching", ws)
    _pending_workspace_ask("chat-matching", "turn-1", ws)
    claimed = ws + "/." if alias else ws
    response = _grant_chat_workspace({"session_id": "chat-matching", "workspace_root": claimed})
    assert response.status == 200 and response.body["ok"], response.body
    assert chat_workspace_authority_state("chat-matching")["workspace_root"] == ws
    assert not chat_workspace_authority_state("another-chat").get("workspace_root")


def test_an_unbound_chat_reports_the_stable_unbound_reason(tmp_path):
    """The client's folder-selection flow keys on a code, never on prose."""
    ws = _ws(tmp_path)
    _pending_workspace_ask("chat-free", "turn-1", ws)
    response = _grant_chat_workspace({"session_id": "chat-free", "workspace_root": ""})
    assert response.status == 409
    payload = response.body
    assert payload["reason"] == "unbound"
    assert "project folder" in payload["error"]


def test_a_claimed_root_that_disagrees_with_the_binding_is_refused(tmp_path):
    """Preservation control: the server-owned binding still wins over any claimed root."""
    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-bound", ws)
    _pending_workspace_ask("chat-bound", "turn-1", ws)
    response = _grant_chat_workspace(
        {"session_id": "chat-bound", "workspace_root": str(tmp_path / "not-the-bound-root")}
    )
    assert response.status == 409


def test_the_grant_still_requires_a_pending_ask(tmp_path):
    """Preservation control: no pending workspace ask, no mintable grant (operator-answer law)."""
    ws = _ws(tmp_path)
    _bind_chat_to_workspace("chat-quiet", ws)
    response = _grant_chat_workspace({"session_id": "chat-quiet", "workspace_root": ""})
    assert response.status == 409
    assert "asking" in response.body["error"]


# ============================================================================================
# Repair 2c -- a question about creation is not a request to create.
# ============================================================================================

def test_a_creation_question_never_plans_a_directory():
    """The reported junk mutation: "wait so did you created folder or not?" created `or`."""
    from core.execution.planner import (
        _extract_safe_machine_directory_create,
        _looks_like_workspace_bootstrap_request,
    )

    for question in (
        "wait so did you created folder or not?",
        "did you create the folder or not",
        "have you created folder finalbot yet?",
    ):
        assert _looks_like_workspace_bootstrap_request(question) is False, question
        assert _extract_safe_machine_directory_create(question) is None, question


def test_imperative_creates_still_plan():
    from core.execution.planner import (
        _extract_safe_machine_directory_create,
        _looks_like_workspace_bootstrap_request,
    )

    assert _looks_like_workspace_bootstrap_request("Create a folder named `finalbot`.") is True
    machine = _extract_safe_machine_directory_create("create a folder called test on the desktop")
    assert machine == {"path": "~/Desktop/test"}


# ============================================================================================
# Repair 2b -- the wording fallback names the ACTUAL model-failure cause.
# ============================================================================================

def test_the_wording_failure_note_carries_the_decision_reason():
    from core.agent_runtime.chat_surface import _model_wording_failure_note

    decision = SimpleNamespace(
        model_name="z-ai/glm-5.3-flash",
        details={
            "block_reason": "provider transfer exceeded its wall-clock deadline",
            "requested_model": "z-ai/glm-5.3-flash",
        },
    )
    note = _model_wording_failure_note(decision)
    assert "glm-5.3-flash" in note
    assert "wall-clock deadline" in note


def test_the_wording_failure_note_is_absent_without_a_typed_cause():
    from core.agent_runtime.chat_surface import _model_wording_failure_note

    assert _model_wording_failure_note(SimpleNamespace(model_name="m", details={})) == ""


# ============================================================================================
# Repair 2a -- an approval pause leaves the attempt WAITING_APPROVAL, not FAILED_PROVIDER.
# ============================================================================================

def test_a_waiting_approval_attempt_stays_resumable_not_terminal(tmp_path):
    """The spine now finalizes approval pauses as WAITING_APPROVAL (non-terminal); this pins
    the store contract that change relies on: no completed_at, and a later terminal write
    still lands (terminal absorbs only from terminal states)."""
    from core.runtime_continuity import (
        create_runtime_attempt,
        get_runtime_attempt,
        update_runtime_attempt,
    )

    created = create_runtime_attempt(session_id="chat-1", original_request="create finalbot")
    attempt_id = str(created.get("attempt_id") or "")
    updated = update_runtime_attempt(attempt_id, lifecycle_state="WAITING_APPROVAL", completed=False)
    assert updated is not None
    row = get_runtime_attempt(attempt_id)
    assert row["lifecycle_state"] == "WAITING_APPROVAL"
    assert not row.get("completed_at")
    # The approved turn's own attempt may still close it later.
    closed = update_runtime_attempt(attempt_id, lifecycle_state="SUCCEEDED", completed=True)
    assert closed is not None and closed["lifecycle_state"] == "SUCCEEDED"


# ============================================================================================
# Repair 4 -- an internal routing call is not the selected model failing.
# ============================================================================================

@pytest.fixture
def paid_pin_lane(monkeypatch):
    from unittest import mock

    import core.runtime_provider_defaults as rpd

    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    with mock.patch("core.credential_store.has_credential", lambda slot: slot == "llm.cloud.anthropic"):
        provider_id = rpd.activate_provider_byok("anthropic", env={"ANTHROPIC_API_KEY": DUMMY_KEY})
    assert provider_id
    from storage.model_provider_manifest import list_provider_manifests

    return next(m for m in list_provider_manifests(enabled_only=True) if m.provider_name == "anthropic-byok")


def _run_internal_tool_intent(paid_pin_lane, monkeypatch):
    from unittest import mock

    from core.memory_first_router import MemoryFirstRouter
    from core.runtime_task_events import (
        new_runtime_event_stream_id,
        register_runtime_event_sink,
    )

    events: list[dict] = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))

    task = SimpleNamespace(task_id="task-1", task_summary="pick the tool")
    interpretation = SimpleNamespace(reconstructed_text="pick the tool")
    report = SimpleNamespace(
        retrieval_confidence=0.2,
        to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
    )
    context_result = SimpleNamespace(
        retrieval_confidence_score=0.2,
        report=report,
        assembled_context=lambda: "",
        context_snippets=lambda: [],
    )
    with mock.patch("core.memory_first_router._cached_free_vram_gb", return_value=None), mock.patch(
        "core.memory_first_router.should_probe_health", return_value=False
    ), mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=[]
    ):
        decision = MemoryFirstRouter()._execute_provider_task(
            task=task,
            classification={"task_class": "chat_conversation"},
            interpretation=interpretation,
            context_result=context_result,
            persona=SimpleNamespace(tone="neutral"),
            task_hash="hash",
            task_kind="tool_intent",
            output_mode="tool_intent",
            allow_paid_fallback=False,
            provider_role="drone",
            surface="cli",
            source_context={
                "_owner_local": True,
                "requested_model": paid_pin_lane.provider_id,
                "model_selection": "pin",
                "turn_id": "t",
                "session_id": "s",
                "runtime_event_stream_id": stream_id,
            },
        )
    return decision, events


def test_internal_tool_intent_is_not_reported_as_the_selected_model_failing(paid_pin_lane, monkeypatch):
    """The reservation's own law -- the pick authorizes answering, not internal classification
    calls -- must not surface as "Selected model could not run"."""
    decision, events = _run_internal_tool_intent(paid_pin_lane, monkeypatch)
    assert decision.source != "selected_model_blocked"
    assert str((decision.details or {}).get("block_reason") or "") != "internal_tool_intent_call"
    for event in events:
        message = str(event.get("message") or "")
        assert "could not run (internal_tool_intent_call)" not in message
    assert any(
        "Internal tool-selection step" in str(event.get("message") or "")
        for event in events
    ), "the ledger must name the internal routing lane distinctly"


def test_reservation_refusal_code_is_stable_for_internal_calls(paid_pin_lane):
    """The typed refusal the router keys on, pinned so the repair cannot drift silently."""
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    denial: dict[str, str] = {}
    reservation = reserve_owner_pick_paid_call(
        manifest=paid_pin_lane,
        task=SimpleNamespace(task_id="t"),
        source_context={"_owner_local": True},
        task_kind="tool_intent",
        denial=denial,
    )
    assert reservation is None
    assert denial["reason"] == "internal_tool_intent_call"
