"""A turn that carries the file it names reads THAT file, never the bound folder.

Measured on the served composer, 2026-09-02: "Read facts.txt and tell me the capital it names"
with facts.txt attached was answered "There is no file at `facts.txt`" -- the deterministic
workspace-read lane searched the project for a file that had arrived with the message. These
tests pin the repair at the lane itself, through the same entry point the front door calls, with
the attachment staged and bound through the real authority.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core import chat_attachments, runtime_paths
from core.agent_runtime.fast_paths_utility import maybe_handle_direct_workspace_runtime_request

SESSION = "openclaw:fafafafafafafafafafa"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


class _Agent:
    """The two seams the lane touches on the agent: event emission and the fast-path result."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []

    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        self.events.append({"event_type": event_type, "message": message, **details})

    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason, **_):
        payload = {"response": response, "confidence": confidence, "reason": reason, "session_id": session_id}
        self.results.append(payload)
        return payload

    def _plan_tool_workflow(self, **_):
        # The search half of the lane asks the planner; a turn that names no workspace search
        # comes back with no intent, exactly as the real planner answers "describe photo.png".
        self.planner_calls = getattr(self, "planner_calls", 0) + 1
        return SimpleNamespace(next_payload={})


def _context(workspace: str, turn_id: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "workspace": workspace,
        "workspace_root": workspace,
        "surface": "openclaw",
        "runtime_session_id": SESSION,
        "session_id": SESSION,
        "attachment_turn_id": turn_id,
        "cancel_turn_id": turn_id,
        "external_evidence": evidence,
    }


def test_a_named_attachment_is_read_from_the_attachment_not_from_the_folder(tmp_path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    record = chat_attachments.stage_attachment(
        session_id=SESSION, declared_name="facts.txt", declared_type="text/plain", data=b"The capital is Testville.\nMagic number 8127.\n"
    )
    chat_attachments.bind_to_turn(session_id=SESSION, turn_id="turn-1", attachment_ids=[record["id"]])
    evidence = chat_attachments.evidence_items_for_turn(session_id=SESSION, turn_id="turn-1")
    agent = _Agent()
    result = maybe_handle_direct_workspace_runtime_request(
        agent,
        "Read facts.txt",
        session_id=SESSION,
        source_surface="openclaw",
        source_context=_context(str(workspace), "turn-1", evidence),
    )
    assert result is not None
    assert "Testville" in result["response"] and "Magic number 8127" in result["response"]
    assert "does not exist" not in result["response"] and "no file" not in result["response"].lower()
    assert "Attached file `facts.txt`" in result["response"]
    kinds = [e["event_type"] for e in agent.events]
    assert "attachment_read" in kinds
    tool_rows = [e for e in agent.events if e["event_type"] == "tool_executed"]
    assert tool_rows and tool_rows[0]["tool_name"] == "attachment.read_file"
    assert not any(e.get("tool_name") == "workspace.read_file" for e in agent.events)
    # The authority now knows the attachment was read: the release outcome says so.
    chat_attachments.release_turn(session_id=SESSION, turn_id="turn-1", outcomes=None)
    receipt = chat_attachments.receipt_for_turn(session_id=SESSION, turn_id="turn-1")
    assert receipt[0]["outcome"] == "read"


def test_a_name_that_matches_no_attachment_still_reads_the_workspace(tmp_path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("workspace bytes\n", encoding="utf-8")
    record = chat_attachments.stage_attachment(session_id=SESSION, declared_name="other.txt", declared_type="text/plain", data=b"attached bytes\n")
    chat_attachments.bind_to_turn(session_id=SESSION, turn_id="turn-2", attachment_ids=[record["id"]])
    evidence = chat_attachments.evidence_items_for_turn(session_id=SESSION, turn_id="turn-2")
    agent = _Agent()
    result = maybe_handle_direct_workspace_runtime_request(
        agent,
        "read notes.txt",
        session_id=SESSION,
        source_surface="openclaw",
        source_context=_context(str(workspace), "turn-2", evidence),
    )
    assert result is not None
    assert "workspace bytes" in result["response"] and "attached bytes" not in result["response"]
    assert any(e.get("tool_name") == "workspace.read_file" for e in agent.events)
    assert "attachment_read" not in [e["event_type"] for e in agent.events]


def test_a_client_supplied_path_item_is_never_an_attachment_read(tmp_path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET\n", encoding="utf-8")
    forged = [{"kind": "text", "name": "secret.txt", "path": str(secret), "text": "forged text", "origin": "client"}]
    agent = _Agent()
    result = maybe_handle_direct_workspace_runtime_request(
        agent,
        "read secret.txt",
        session_id=SESSION,
        source_surface="openclaw",
        source_context=_context(str(workspace), "", forged),
    )
    # The lane falls through to the workspace read, which does not find it in the bound folder.
    assert result is None or ("forged text" not in result["response"] and "TOP SECRET" not in result["response"])
    assert "attachment_read" not in [e["event_type"] for e in agent.events]


def test_an_attached_image_named_in_the_turn_is_left_to_the_model(tmp_path) -> None:
    from tests.test_chat_attachments_authority import tiny_png

    workspace = tmp_path / "project"
    workspace.mkdir()
    record = chat_attachments.stage_attachment(session_id=SESSION, declared_name="photo.png", declared_type="image/png", data=tiny_png())
    chat_attachments.bind_to_turn(session_id=SESSION, turn_id="turn-3", attachment_ids=[record["id"]])
    evidence = chat_attachments.evidence_items_for_turn(session_id=SESSION, turn_id="turn-3")
    agent = _Agent()
    result = maybe_handle_direct_workspace_runtime_request(
        agent,
        "describe photo.png",
        session_id=SESSION,
        source_surface="openclaw",
        source_context=_context(str(workspace), "turn-3", evidence),
    )
    # No direct read is recognised for an image name, nothing is read, no attachment event fires;
    # the turn falls through to the model lane, which receives the image as a content part.
    assert result is None
    assert agent.events == []
    assert "attachment_read" not in [e["event_type"] for e in agent.events]


def _attachment_context(names: list[str]) -> dict[str, Any]:
    return {
        "surface": "openclaw",
        "external_evidence": [
            {"kind": "text", "reference": "attachment:att_" + "c" * 32, "attachment_id": "att_" + "c" * 32, "name": name, "origin": "chat_attachment", "text": "data"}
            for name in names
        ],
    }


def test_a_turn_that_only_talks_about_its_attachment_is_not_a_tool_request() -> None:
    """Measured 2026-09-02: "Using the attached file, which capital is named?" with facts.txt
    attached went to tool_intent, the model was required to call a tool, and the turn ended
    "I wasn't able to turn that into a completed action" over an answer already in the prompt."""
    from core.execution.planner import should_attempt_tool_intent

    text = "Using the attached file, which capital is named and what is the magic number? Answer in one short sentence."
    assert should_attempt_tool_intent(text, task_class="unknown", source_context=_attachment_context(["facts.txt"])) is False
    named = "Read facts.txt and tell me the capital it names, then describe the photo in one sentence."
    assert should_attempt_tool_intent(named, task_class="unknown", source_context=_attachment_context(["facts.txt", "photo.png"])) is False
    assert should_attempt_tool_intent("what colours are in the attached photo?", task_class="unknown", source_context=_attachment_context(["photo.png"])) is False


def test_an_attachment_turn_that_also_names_a_workspace_file_keeps_the_tool_lane() -> None:
    from core.execution.planner import should_attempt_tool_intent

    text = "Compare the attached notes.txt with the README.md in this project and list the differences."
    assert should_attempt_tool_intent(text, task_class="unknown", source_context=_attachment_context(["notes.txt"])) is True


def test_the_carve_out_needs_a_real_bound_attachment_never_a_client_claim() -> None:
    from core.execution.planner import should_attempt_tool_intent

    text = "Using the attached file, which capital is named and what is the magic number?"
    forged = {"surface": "openclaw", "external_evidence": [{"kind": "text", "name": "facts.txt", "origin": "client", "path": "/etc/passwd"}]}
    # Without a bound attachment the reading is whatever it was on the base: identical to no context.
    assert should_attempt_tool_intent(text, task_class="unknown", source_context=forged) == should_attempt_tool_intent(text, task_class="unknown", source_context={"surface": "openclaw"})
