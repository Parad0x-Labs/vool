"""A follow-up about a file VOOL just wrote is answered from the receipt, never from a search.

THE LIVE DEFECT
---------------
The user asked for a one-sentence description of their app written into ``README.md``. The Files
table reported it written at the workspace root. The three turns that followed:

    "where was this file storred?"
        -> VOOL searched ``/`` for a FOLDER whose name matched "file storred", reported that no
           such folder existed, and never mentioned README.md at all.
    "u did created readme.md where did u created it?"
        -> "the root of your workspace" -- no machine path anywhere in the reply.
    "I see, but since we are in General chat now - how can i find it in my machine?"
        -> vague, escalated to cloud, still no absolute root.

Driving the untouched front door with every phrasing below shows the cause: not one of them was
claimed by any lane, so the model received the question with no receipt attached and guessed a
tool. `test_the_whole_family_is_claimed_before_any_search_lane` is the regression that holds that
shut; `SabotageTests` at the bottom proves it is the wiring being tested and not something else.

WHY THIS FILE IS NOT KEYED TO "README.md"
-----------------------------------------
A fix that answers this one conversation is the hard-coded input->output mapping CLAUDE.md 0.2
bans. So the subject the detector accepts is read from the RECEIPT at call time, and the tests
prove exactly that: the same question is claimed under a receipt that names its file and refused
under one that does not (`test_a_named_file_is_this_family_only_when_the_receipt_names_it`), and
the end-to-end tests drive a real `workspace.write_file` and assert against the file that actually
appeared on disk -- a name generated per-run, which no code here could have been written around.

The adversarial half is the negative controls: five questions that LOOK like this family and must
reach their own lanes instead, including two that name the receipt's own file.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from core.action_receipt_location import (
    FileActionReceipt,
    latest_file_action_receipt,
    location_followup_kind,
    render_location_answer,
    render_missing_receipt_answer,
)
from core.agent_runtime.fast_paths_receipt_location import (
    maybe_handle_action_receipt_location_request,
)
from core.runtime_execution_tools import execute_runtime_tool

# --------------------------------------------------------------------------------------------
# The semantic family. Every entry is a real way of asking the one question, including the four
# typed sloppily -- the live defect's own turn was misspelled, and the next one will be misspelled
# differently.
# --------------------------------------------------------------------------------------------
FAMILY = (
    "where was this file stored?",
    "where did you create it?",
    "what folder did that file go into?",
    "show me the path to the file you just wrote",
    "since this is General chat, how do I find it in Finder?",
    "open location?",
    "reveal path?",
    "where exactly did that file end up?",
    "which folder is it in?",
    # The live turns, verbatim.
    "where was this file storred?",
    "u did created readme.md where did u created it?",
    "I see, but since we are in General chat now - how can i find it in my machine?",
    # Sloppy.
    "where file storred?",
    "wher u created readme",
    "path pls",
    "where on mac?",
    "finder location?",
)

# The questions that name the file. Rendered against whichever file the receipt actually holds, so
# no filename is hard-coded into an assertion.
NAMED_FILE_FAMILY = (
    "where is {name} on my machine?",
    "where is {name}?",
)

# Adversarial: each one wants a DIFFERENT lane, and each was chosen because it shares surface
# features with the family above.
NEGATIVE_CONTROLS = (
    # A search directive. The user asked for a search; they should get one.
    "find folders named storage",
    "search / for file stored",
    # Names the receipt's own file, but wants its CONTENT searched, not its location.
    "search the project for README.md content",
    # "where ... stored/installed" about a subject this lane has no business answering for.
    "where is Python installed?",
    "where are app settings stored?",
    # Workspace identity: `maybe_handle_workspace_identity_request` owns these.
    "where am i?",
    "which folder are we in?",
    # Ordinary machine/folder searches, and an ordinary read.
    "where is the Website V3 folder on this pc",
    "grep the repo for TODO",
    "read README.md and summarise it",
    "delete README.md",
    # Nothing to do with the filesystem at all.
    "where is the nearest coffee shop?",
    "what time is it?",
    # Still carries WORK. This lane sits above the skill, hive and builder lanes, so claiming any
    # of these would answer the question and silently drop the thing being asked for.
    "create a skill called qa-echo. where do i find it?",
    "write a CHANGELOG then tell me where it is",
    "please save it to my desktop and tell me the path",
    "delete README.md and tell me what folder it was in",
)

# The mirror of the veto above: the same verbs in the PAST tense are how this family talks about
# what already happened, and must still be claimed.
PAST_TENSE_FAMILY = (
    "where was this file saved?",
    "where did you write it?",
    "where was that file created?",
    "where did you store it?",
)


def _receipt(name: str = "README.md", *, root: str = "/Users/qa/projects/thunder") -> FileActionReceipt:
    return FileActionReceipt(
        file_name=name,
        relative_path=name,
        workspace_root=root,
        absolute_path=f"{root}/{name}",
        action="created",
        intent="workspace.write_file",
        source="workspace_mutation_ledger",
    )


# =============================================================================================
# 1. The receipt lookup seam
# =============================================================================================


def _write_through_the_real_tool(session_id: str, workspace: str, *, name: str, content: str = "x"):
    result = execute_runtime_tool(
        "workspace.write_file",
        {"path": name, "content": content},
        source_context={"workspace": workspace, "session_id": session_id},
    )
    assert result is not None and result.ok, getattr(result, "response_text", result)
    return result


def test_a_real_write_leaves_a_receipt_that_resolves_to_the_file_on_disk() -> None:
    """End to end through the real tool: the answer's path is the file that actually appeared.

    The filename is generated per run, so nothing in this repository could have been written to
    satisfy this particular string.
    """
    session_id = f"loc-e2e-{uuid.uuid4().hex[:10]}"
    name = f"thunder_{uuid.uuid4().hex[:8]}.md"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name=name, content="Thunder is an app.")

        receipt = latest_file_action_receipt(session_id, workspace_root="")

        assert receipt is not None, "a real write left no receipt this lane can read"
        assert receipt.file_name == name
        assert receipt.intent == "workspace.write_file"
        assert receipt.action == "created"
        # The load-bearing assertion is environmental, not textual: the path the answer will print
        # is a real file, and it is the one the tool wrote.
        assert Path(receipt.absolute_path).is_file()
        assert Path(receipt.absolute_path).read_text(encoding="utf-8") == "Thunder is an app."
        assert Path(receipt.absolute_path).resolve() == (Path(workspace) / name).resolve()


def test_the_receipt_reports_the_latest_write_not_the_first() -> None:
    """"where did that file go" means the last one. Two writes, one session, newest wins."""
    session_id = f"loc-latest-{uuid.uuid4().hex[:10]}"
    first = f"first_{uuid.uuid4().hex[:6]}.txt"
    second = f"second_{uuid.uuid4().hex[:6]}.txt"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name=first)
        _write_through_the_real_tool(session_id, workspace, name=second)

        receipt = latest_file_action_receipt(session_id)

        assert receipt is not None and receipt.file_name == second


def test_a_rolled_back_write_is_not_reported_as_a_place_to_go_and_look() -> None:
    """A mutation that was undone left nothing on disk; naming its path would send the user to a
    file that is not there."""
    session_id = f"loc-rollback-{uuid.uuid4().hex[:10]}"
    name = f"undone_{uuid.uuid4().hex[:6]}.txt"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name=name)
        reverted = execute_runtime_tool(
            "workspace.rollback_last_change",
            {},
            source_context={"workspace": workspace, "session_id": session_id},
        )
        assert reverted is not None and reverted.ok, getattr(reverted, "response_text", reverted)
        assert not (Path(workspace) / name).exists()

        receipt = latest_file_action_receipt(session_id)

        assert receipt is None or receipt.file_name != name


def test_the_durable_tool_receipt_store_answers_when_the_ledger_is_empty(tmp_path) -> None:
    """A write recorded only as a durable tool receipt -- a resumed chat, a different process --
    still resolves, using the turn's own workspace root to complete the relative path."""
    session_id = f"loc-store-{uuid.uuid4().hex[:10]}"
    from core import runtime_continuity
    from storage.migrations import run_migrations

    db_path = tmp_path / "continuity.db"
    run_migrations(db_path=db_path)
    runtime_continuity.configure_runtime_continuity_db_path(str(db_path))
    try:
        runtime_continuity.store_tool_receipt(
            receipt_key=f"receipt-{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            checkpoint_id="checkpoint-1",
            tool_name="workspace.write_file",
            idempotency_key="idem-1",
            arguments={"path": "docs/notes.md", "content": "hello"},
            execution={"details": {"path": "docs/notes.md", "action": "created"}},
        )

        receipt = latest_file_action_receipt(session_id, workspace_root="/Users/qa/ws")
    finally:
        runtime_continuity.configure_runtime_continuity_db_path(None)

    assert receipt is not None
    assert receipt.source == "runtime_tool_receipt"
    assert receipt.file_name == "notes.md"
    assert receipt.relative_path == "docs/notes.md"
    assert receipt.absolute_path == "/Users/qa/ws/docs/notes.md"


def test_the_in_process_execution_record_answers_when_the_durable_stores_are_empty() -> None:
    """The third store. A tool that wrote outside a workspace records an ABSOLUTE target, and the
    lookup must keep it rather than rebasing it under some root."""
    session_id = f"loc-record-{uuid.uuid4().hex[:10]}"
    from core import execution_records

    execution_records.clear(session_id)
    execution_records.record(
        session_id=session_id,
        intent="machine.write_file",
        arguments={"path": "~/Desktop/report.md"},
        details={"resolved_target": "/Users/qa/Desktop/report.md"},
        ok=True,
        status="executed",
    )
    try:
        receipt = latest_file_action_receipt(session_id, workspace_root="/Users/qa/ws")
    finally:
        execution_records.clear(session_id)

    assert receipt is not None
    assert receipt.source == "execution_record"
    assert receipt.absolute_path == "/Users/qa/Desktop/report.md"
    assert "/Users/qa/ws" not in receipt.absolute_path


def test_the_activity_action_record_answers_when_the_other_three_are_empty(tmp_path) -> None:
    """The fourth store: the durable Activity row the panel itself renders from.

    This is the last-resort one, and the test says so honestly: emitting a terminal tool event
    registers the action in BOTH durable stores, so the integrated lookup is answered by whichever
    comes first, and the event reader is asserted on its own to prove it can carry the answer when
    it is all that is left -- a chat resumed after a restart, where the in-process records are gone.
    """
    session_id = f"loc-event-{uuid.uuid4().hex[:10]}"
    from core.agent_runtime import orchestrator
    from core.runtime_task_events import (
        configure_runtime_event_store,
        emit_runtime_event,
        reset_runtime_event_state,
    )
    from storage.migrations import run_migrations

    class _RuntimeAgent:
        @staticmethod
        def _runtime_checkpoint_id(source_context: dict[str, Any] | None) -> str:
            return str((source_context or {}).get("checkpoint_id") or "")

    db_path = tmp_path / "events.db"
    run_migrations(db_path=db_path)
    configure_runtime_event_store(str(db_path))
    reset_runtime_event_state()
    try:
        orchestrator.emit_runtime_event(
            _RuntimeAgent(),
            {"runtime_session_id": session_id, "chat_id": "c", "checkpoint_id": "k", "turn_id": "t"},
            event_type="tool_executed",
            message="Wrote the file",
            emit_runtime_event_fn=emit_runtime_event,
            tool_name="workspace.write_file",
            status="executed",
            mode="tool_executed",
            ok=True,
            summary="wrote docs/out.md",
            tool_call_id="call-1",
            arguments={"path": "docs/out.md", "content": "hi"},
        )

        receipt = latest_file_action_receipt(session_id, workspace_root="/Users/qa/ws")
        from core.action_receipt_location import _from_session_events

        from_events = _from_session_events(session_id)
    finally:
        configure_runtime_event_store(None)
        reset_runtime_event_state()

    assert receipt is not None
    assert receipt.file_name == "out.md"
    assert receipt.absolute_path == "/Users/qa/ws/docs/out.md"
    # The Activity row on its own carries the same answer, which is what a resumed chat has left.
    assert from_events is not None
    assert from_events.source == "runtime_session_event"
    assert from_events.relative_path == "docs/out.md"
    assert from_events.intent == "workspace.write_file"


def test_a_store_that_blows_up_does_not_take_the_turn_with_it() -> None:
    """Resource-exhausted / corrupt-store case. A ledger that raises must cost the answer its
    precision, never the turn."""
    session_id = f"loc-broken-{uuid.uuid4().hex[:10]}"
    from core import action_receipt_location as module

    def _explode(_session_id: str):
        raise RuntimeError("mutation ledger is unreadable")

    with mock.patch.object(module, "_from_mutation_ledger", _explode):
        with mock.patch.object(
            module,
            "_from_tool_receipts",
            lambda _s: FileActionReceipt(
                file_name="kept.md", relative_path="kept.md", intent="workspace.write_file",
                source="runtime_tool_receipt",
            ),
        ):
            receipt = latest_file_action_receipt(session_id, workspace_root="/Users/qa/ws")

    assert receipt is not None and receipt.file_name == "kept.md"


def test_a_session_that_wrote_nothing_has_no_receipt() -> None:
    assert latest_file_action_receipt(f"loc-empty-{uuid.uuid4().hex[:10]}") is None


# =============================================================================================
# 2. The follow-up family, and what it must refuse
# =============================================================================================


@pytest.mark.parametrize("phrasing", FAMILY)
def test_every_phrasing_in_the_family_is_recognised(phrasing: str) -> None:
    assert location_followup_kind(phrasing, receipt=_receipt()) is not None, phrasing


@pytest.mark.parametrize("template", NAMED_FILE_FAMILY)
def test_a_named_file_is_this_family_only_when_the_receipt_names_it(template: str) -> None:
    """The anti-overfit assertion. The SAME sentence is claimed or refused purely on what the
    receipt holds -- so nothing here is keyed to a filename, and a lane that answered by matching
    "readme" would fail the first half of this test.
    """
    subject = _receipt(name=f"zeta_{uuid.uuid4().hex[:6]}.log")
    question = template.format(name=subject.file_name)

    assert location_followup_kind(question, receipt=subject) is not None
    # Same question, a receipt about a different file: this lane has nothing to say about it.
    assert location_followup_kind(question, receipt=_receipt(name="README.md")) is None
    # And with no receipt at all it is just a question about a file on disk.
    assert location_followup_kind(question, receipt=None) is None


@pytest.mark.parametrize("phrasing", NEGATIVE_CONTROLS)
def test_the_negative_controls_are_left_to_their_own_lanes(phrasing: str) -> None:
    assert location_followup_kind(phrasing, receipt=_receipt()) is None, phrasing


@pytest.mark.parametrize(
    "phrasing",
    (
        "where is the thunder folder?",
        "where is thunder?",
        "which folder holds projects?",
    ),
)
def test_the_directories_on_the_way_to_the_file_are_not_this_lanes_subject(phrasing: str) -> None:
    """A receipt at /Users/qa/projects/thunder/README.md must not make ``thunder`` or ``projects``
    something this lane answers about. Those are folder searches; only the FILE is its subject."""
    assert location_followup_kind(phrasing, receipt=_receipt()) is None, phrasing


def test_a_path_inside_the_workspace_is_still_this_lanes_subject() -> None:
    """The other side of that line: a subdirectory the file actually lives in still names it."""
    nested = FileActionReceipt(
        file_name="notes.md",
        relative_path="docs/notes.md",
        workspace_root="/Users/qa/ws",
        absolute_path="/Users/qa/ws/docs/notes.md",
        intent="workspace.write_file",
    )

    assert location_followup_kind("where is docs/notes.md?", receipt=nested) is not None
    assert location_followup_kind("where is notes.md?", receipt=nested) is not None


@pytest.mark.parametrize("phrasing", PAST_TENSE_FAMILY)
def test_the_same_verbs_in_the_past_tense_are_still_this_family(phrasing: str) -> None:
    """The veto that refuses "please save it ..." must not refuse "where was this file saved?" --
    the family is built out of exactly those verbs, in the tense that means it already happened."""
    assert location_followup_kind(phrasing, receipt=_receipt()) is not None, phrasing


def test_a_reveal_ask_is_distinguished_from_a_plain_location_ask() -> None:
    assert location_followup_kind("open location?", receipt=_receipt()) == "reveal"
    assert location_followup_kind("reveal path?", receipt=_receipt()) == "reveal"
    assert location_followup_kind("where was this file stored?", receipt=_receipt()) == "locate"


def test_a_deictic_follow_up_survives_the_turns_in_between() -> None:
    """Cross-turn: the question arrives two turns after the write, wrapped in preamble. The subject
    of the ask clause is what decides it, not the sentence's first noun."""
    subject = _receipt(name="notes.md")
    for phrasing in (
        "ok thanks, and where did you put it?",
        "I see, but since we are in General chat now - how can i find it in my machine?",
        "sorry one more thing - what folder did that file go into?",
    ):
        assert location_followup_kind(phrasing, receipt=subject) is not None, phrasing


def test_an_overlong_message_is_not_this_family() -> None:
    """A paragraph that happens to contain "where" is a request to reason, not an elliptical
    follow-up."""
    long_turn = "where " + ("and some more context about the project " * 20)
    assert location_followup_kind(long_turn, receipt=_receipt()) is None


# =============================================================================================
# 3. The answer
# =============================================================================================


def test_the_answer_names_the_file_and_the_absolute_path() -> None:
    answer = render_location_answer(_receipt(), kind="locate")

    assert "README.md" in answer
    assert "/Users/qa/projects/thunder/README.md" in answer
    assert "workspace.write_file" in answer, "the answer must say which receipt produced it"
    assert "did not search" in answer


def test_a_reveal_ask_gets_the_path_plus_how_to_open_it() -> None:
    answer = render_location_answer(_receipt(), kind="reveal")

    assert "/Users/qa/projects/thunder/README.md" in answer
    assert "Files panel" in answer


def test_with_no_root_the_answer_says_so_instead_of_inventing_one() -> None:
    """The General-chat case. A relative path and a named limit, not a fabricated absolute root."""
    answer = render_location_answer(
        FileActionReceipt(
            file_name="README.md", relative_path="README.md", intent="workspace.write_file"
        )
    )

    assert "relative to the active workspace root as README.md" in answer
    assert "does not expose the absolute root" in answer
    assert "/" not in answer.replace("workspace.write_file", "")


def test_with_no_receipt_the_answer_explains_the_gap_and_asks() -> None:
    answer = render_missing_receipt_answer()

    assert "no file-write receipt" in answer
    assert "will not guess" in answer
    assert "?" in answer, "with nothing recorded, the turn owes the user a question"


# =============================================================================================
# 4. Routing -- what the lane does inside the real front door
# =============================================================================================


class _RecordingAgent:
    """The front-door contract this lane uses, and nothing else."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _fast_path_result(self, **kwargs):
        self.calls.append(kwargs)
        return {"reason": kwargs.get("reason"), "response": kwargs.get("response")}


def _drive_lane(agent: _RecordingAgent, text: str, *, session_id: str, workspace: str = ""):
    return maybe_handle_action_receipt_location_request(
        agent,
        text,
        session_id=session_id,
        source_surface="api",
        source_context={"workspace": workspace, "surface": "api"},
    )


def test_the_lane_answers_a_real_write_with_the_real_path() -> None:
    session_id = f"loc-lane-{uuid.uuid4().hex[:10]}"
    name = f"thunder_{uuid.uuid4().hex[:8]}.md"
    agent = _RecordingAgent()
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name=name)

        result = _drive_lane(agent, "where was this file storred?", session_id=session_id, workspace=workspace)

        assert result is not None
        assert result["reason"] == "action_receipt_location"
        assert str(Path(workspace).resolve() / name) in result["response"]
        assert name in result["response"]
        details = agent.calls[-1]["classification_details"]
        assert details["receipt_found"] is True
        assert details["file_name"] == name


def test_the_lane_declines_every_negative_control_even_with_a_receipt_in_hand() -> None:
    session_id = f"loc-lane-neg-{uuid.uuid4().hex[:10]}"
    agent = _RecordingAgent()
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name="README.md")
        for phrasing in NEGATIVE_CONTROLS:
            assert _drive_lane(agent, phrasing, session_id=session_id, workspace=workspace) is None, phrasing
    assert agent.calls == []


def test_the_lane_explains_the_missing_receipt_rather_than_answering_from_nothing() -> None:
    agent = _RecordingAgent()

    result = _drive_lane(agent, "where did you create it?", session_id=f"loc-none-{uuid.uuid4().hex[:8]}")

    assert result is not None
    assert "no file-write receipt" in result["response"]
    assert agent.calls[-1]["classification_details"] == {"receipt_found": False}


# ---------------------------------------------------------------------------------------------
# The real front door, driven end to end. A source-position assertion proves where a line sits;
# this proves what runs.
# ---------------------------------------------------------------------------------------------

# Every lane below this one that could answer the question with something other than the file's
# path: a filesystem search, a machine read, a web lookup, the bound folder's name, or a model call
# to arbitrate between them.
DOWNSTREAM_HANDLERS = (
    "_maybe_handle_workspace_identity_request",
    "_maybe_handle_direct_machine_read_request",
    "_maybe_handle_direct_machine_download_request",
    "_maybe_handle_direct_machine_write_request",
    "_maybe_handle_direct_workspace_runtime_request",
    "_maybe_handle_live_info_fast_path",
    "_maybe_handle_folder_overview_request",
)


@pytest.fixture(scope="module")
def real_agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _drive_front_door(agent, text: str, *, session_id: str, workspace: str):
    """Returns (result, handlers_reached). Every downstream handler is stubbed to decline."""
    reached: set[str] = set()
    patches = [
        mock.patch.object(agent, name, lambda *a, _n=name, **k: (reached.add(_n), None)[1])
        for name in DOWNSTREAM_HANDLERS
    ]
    for patch in patches:
        patch.start()
    try:
        outcome = agent._handle_turn_frontdoor(
            raw_user_input=text,
            effective_input=text,
            normalized_input=text.lower(),
            source_surface="api",
            session_id=session_id,
            source_context={
                "workspace": workspace,
                "workspace_root": workspace,
                "session_id": session_id,
                "operating_mode": "auto",
                "surface": "api",
            },
            persona=None,
            interpreted=None,
        )
    finally:
        for patch in patches:
            patch.stop()
    return (outcome or {}).get("result"), reached


@pytest.mark.parametrize("phrasing", FAMILY)
def test_the_whole_family_is_claimed_before_any_search_lane(real_agent, phrasing: str) -> None:
    """The regression. Measured on the untouched front door: all sixteen of these reached the END
    of it, claimed by nothing, and the model was left to guess a tool -- which is how "where was
    this file storred?" became a search of `/` for a folder named after the typo.
    """
    session_id = f"loc-front-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name="README.md")

        result, reached = _drive_front_door(real_agent, phrasing, session_id=session_id, workspace=workspace)

    assert result is not None, f"nothing claimed {phrasing!r}"
    assert result["route"] == "action_receipt_location"
    assert result["route_reason"] == "file_location_followup_from_receipt"
    # No search, no machine read, no web lookup, no arbiter -- the lane returns above all of them.
    assert reached == set(), f"{phrasing!r} still reached {sorted(reached)}"
    # And no model, local or cloud: the answer is a lookup.
    assert result["model_execution"] == {"source": "fast_path", "used_model": False}
    assert "model" in result["route_skips"]
    assert str(Path(workspace).resolve() / "README.md") in result["response"]


@pytest.mark.parametrize(
    "phrasing",
    ("what folder did that file go into?", "which folder is it in?", "where was this file stored?"),
)
def test_the_bound_folders_name_is_not_an_answer_to_where_the_file_went(real_agent, phrasing: str) -> None:
    """The second measured cause, held down by name.

    Before the lane moved above it, `_maybe_handle_workspace_identity_request` claimed these three
    and replied "The workspace is set to `<root>`. That is the project ..." -- which is the live
    turn that answered "the root of your workspace" and never named README.md. Naming the folder is
    not the same as naming the file.
    """
    session_id = f"loc-identity-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name="README.md")

        result, reached = _drive_front_door(real_agent, phrasing, session_id=session_id, workspace=workspace)

    assert result is not None
    assert result["route"] == "action_receipt_location", phrasing
    assert "_maybe_handle_workspace_identity_request" not in reached
    assert "README.md" in result["response"]
    assert str(Path(workspace).resolve() / "README.md") in result["response"]


@pytest.mark.parametrize(
    "phrasing",
    ("find folders named storage", "where is Python installed?", "search the project for README.md content"),
)
def test_the_negative_controls_still_reach_the_pipeline(real_agent, phrasing: str) -> None:
    """The other half of the routing proof: this lane did not simply swallow every "where"."""
    session_id = f"loc-front-neg-{uuid.uuid4().hex[:10]}"
    with tempfile.TemporaryDirectory() as workspace:
        _write_through_the_real_tool(session_id, workspace, name="README.md")

        result, reached = _drive_front_door(real_agent, phrasing, session_id=session_id, workspace=workspace)

    if result is not None:
        assert result.get("route") != "action_receipt_location", phrasing
    assert "_maybe_handle_direct_machine_read_request" in reached, (
        f"{phrasing!r} must still reach the lane that owns it"
    )


# =============================================================================================
# 5. Sabotage -- each fix, reverted, fails a test that names its cause
# =============================================================================================


class TestSabotage:
    def test_removing_the_front_door_wiring_drops_the_family_back_to_the_search_lane(self, real_agent) -> None:
        """Reverting the ROUTE: with the lane declining, the question reaches the machine-read lane
        that owns `machine.find_folder` -- i.e. exactly the defect, reproduced on demand.
        """
        session_id = f"loc-sabo-route-{uuid.uuid4().hex[:10]}"
        import core.agent_runtime.fast_paths_receipt_location as lane

        with tempfile.TemporaryDirectory() as workspace:
            _write_through_the_real_tool(session_id, workspace, name="README.md")
            with mock.patch.object(
                lane, "maybe_handle_action_receipt_location_request", lambda *a, **k: None
            ):
                result, reached = _drive_front_door(
                    real_agent, "where was this file storred?", session_id=session_id, workspace=workspace
                )

        assert result is None, "with the lane gone, nothing should claim the turn"
        assert "_maybe_handle_direct_machine_read_request" in reached, (
            "the sabotage must land on the search lane; if it does not, the routing test above was "
            "passing for some other reason"
        )

    def test_removing_the_receipt_lookup_leaves_the_answer_with_no_path(self) -> None:
        """Reverting the LOOKUP: with every store returning nothing, the lane can no longer name a
        path and says so, rather than printing a plausible one."""
        session_id = f"loc-sabo-lookup-{uuid.uuid4().hex[:10]}"
        agent = _RecordingAgent()
        with tempfile.TemporaryDirectory() as workspace:
            _write_through_the_real_tool(session_id, workspace, name="README.md")
            with mock.patch(
                "core.agent_runtime.fast_paths_receipt_location.latest_file_action_receipt",
                lambda *a, **k: None,
            ):
                result = _drive_lane(
                    agent, "where was this file storred?", session_id=session_id, workspace=workspace
                )

        assert result is not None
        assert "no file-write receipt" in result["response"]
        assert str(Path(workspace).resolve() / "README.md") not in result["response"]

    def test_dropping_the_foreign_subject_check_lets_the_lane_answer_about_python(self) -> None:
        """Reverting gate 3: with the vocabulary check disabled, "where is Python installed?" is
        claimed by this lane and answered with the README's path -- a confidently wrong answer to a
        question about a Python install. The control is that the family still passes either way, so
        only the negative-control test can catch this.
        """
        from core import action_receipt_location as module

        with mock.patch.object(module, "_token_is_familiar", lambda *a, **k: True):
            hijacked = module.location_followup_kind("where is Python installed?", receipt=_receipt())
            still_family = module.location_followup_kind("where was this file stored?", receipt=_receipt())

        assert hijacked is not None, "the negative control passes only because of the subject check"
        assert still_family is not None, "control: the family is unaffected by the sabotage"
        assert location_followup_kind("where is Python installed?", receipt=_receipt()) is None

    def test_dropping_the_pending_work_veto_swallows_the_work(self) -> None:
        """Reverting gate 1b: this lane sits above the skill/hive/builder lanes, so without the
        veto "create a skill called qa-echo. where do i find it?" is answered with an earlier
        file's path and the skill is never created."""
        from core import action_receipt_location as module

        with mock.patch.object(module, "_PENDING_WORK_RE", SimpleNamespace(match=lambda _t: None)):
            hijacked = module.location_followup_kind(
                "create a skill called qa-echo. where do i find it?", receipt=_receipt()
            )
            still_family = module.location_followup_kind("where was this file saved?", receipt=_receipt())

        assert hijacked is not None, "the pending-work controls pass only because of gate 1b"
        assert still_family is not None, "control: the past-tense family is unaffected"
        assert (
            location_followup_kind("create a skill called qa-echo. where do i find it?", receipt=_receipt())
            is None
        )

    def test_dropping_the_workspace_identity_veto_steals_where_are_we(self) -> None:
        """Reverting gate 1: "where are we?" is three familiar words plus an ask marker -- the exact
        shape of an elliptical follow-up -- so without the veto it is claimed here and answered with
        the last written file's path instead of the bound workspace.
        """
        from core import action_receipt_location as module

        with mock.patch.object(module, "_WORKSPACE_IDENTITY_RE", SimpleNamespace(search=lambda _t: None)):
            hijacked = module.location_followup_kind("where are we?", receipt=_receipt())
            still_family = module.location_followup_kind("path pls", receipt=_receipt())

        assert hijacked is not None, "the identity control passes only because of gate 1"
        assert still_family is not None, "control: the family is unaffected by the sabotage"
        assert location_followup_kind("where are we?", receipt=_receipt()) is None
