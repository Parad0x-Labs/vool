"""A tool receipt asserts that a named tool ran with named arguments. Both halves were untrue.

MEASURED ON THE LIVE STORE, 2026-08-18 -- 92 receipts total:

    tool_name = 'unknown'      55 rows (60%)
    arguments_json = '{}'      65 rows (71%)
    tools ever named            5  (web.search 23, workspace.search_text 5,
                                    workspace.read_file 4, web.fetch 3, unknown 53)

DEFECT 1 -- THE MAJORITY OF THE TABLE WAS NOT ABOUT TOOLS. Every one of the 55 `unknown` rows was
`provider_did_not_answer`, emitted by `core/agent_runtime/research_tool_loop_facade.py` when the
MODEL provider returned no reply and no tool was ever selected. That emitter's own message says it:
"This is a transport failure, not a model output defect." The guard in `build_tool_action_record`
rejected only the EMPTY string, so the literal sentinel walked through and a receipt was written
asserting an execution that never happened.

The Activity event is untouched and still shown -- the user needs to see that the provider did not
answer. Only the receipt is refused.

DEFECT 2 -- ARGUMENTS WERE SILENTLY OPTIONAL. `details.get("arguments")` defaults to `{}` with no
validation, so an emitter that omits the key produces a receipt that cannot say what was read. The
split in the live data is perfectly clean: `web.search`, which passes `arguments=`, was 0% empty;
`workspace.read_file` and `workspace.search_text`, emitted from `fast_paths_utility.py` without it,
were 100% empty.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.orchestrator import build_tool_action_record

CONTEXT = {"session_id": "receipt-test", "workspace": "/tmp"}


def _record(tool_name: str, *, event_type: str = "tool_executed", **details):
    return build_tool_action_record(
        CONTEXT,
        event_type=event_type,
        message="m",
        details={"tool_name": tool_name, **details},
    )


# ------------------------------------------------------------------ defect 1: the sentinel


@pytest.mark.parametrize("sentinel", ("unknown", "none", "n/a", "-", "UNKNOWN", " unknown "))
def test_a_placeholder_name_produces_no_receipt(sentinel: str) -> None:
    assert _record(sentinel, event_type="tool_failed", status="provider_did_not_answer") is None


def test_the_exact_transport_failure_that_was_60_percent_of_the_table() -> None:
    assert _record("unknown", event_type="tool_failed", status="provider_did_not_answer") is None


def test_an_empty_name_is_still_refused() -> None:
    """The pre-existing half of the guard must survive the new half."""
    assert _record("") is None
    assert _record("   ") is None


def test_a_real_tool_still_gets_its_receipt() -> None:
    record = _record("workspace.read_file", arguments={"path": "notes.txt"})

    assert record is not None
    assert record.get("tool_name") == "workspace.read_file"


def test_a_non_terminal_event_is_still_refused() -> None:
    assert _record("workspace.read_file", event_type="tool_selected") is None


# ------------------------------------------------------------------ defect 2: the arguments


def test_a_receipt_carries_the_arguments_it_was_given() -> None:
    record = _record("workspace.read_file", arguments={"path": "~/Desktop/notes.md"})

    assert record is not None
    assert "notes.md" in repr(record)


def test_the_workspace_emitter_supplies_its_path() -> None:
    """The production emitter, not a hand-built dict: it holds `path` and dropped it, which is why
    both workspace tools were 100% empty while web.search was 0%."""
    captured: list[dict] = []

    class _Agent:
        def _emit_runtime_event(self, source_context, **details):
            captured.append(details)

    class _Execution:
        ok = True
        response_text = "read 3 lines"
        details: dict = {}

    from core.agent_runtime.fast_paths_utility import _emit_workspace_tool_events

    _emit_workspace_tool_events(
        _Agent(), dict(CONTEXT), intent="workspace.read_file",
        execution=_Execution(), path="notes.txt",
    )

    terminal = [item for item in captured if item.get("event_type") in {"tool_executed", "tool_failed"}]
    assert terminal, "the emitter produced no terminal tool event"
    assert terminal[0].get("arguments") == {"path": "notes.txt"}


# ------------------------------------------------------------------ sabotage


def test_sabotage_accepting_the_sentinel_restores_the_fabricated_receipt(monkeypatch) -> None:
    """Revert the sentinel check and a transport failure is filed as a tool execution again."""
    from core.agent_runtime import orchestrator

    monkeypatch.setattr(orchestrator, "_NON_TOOL_SENTINELS", frozenset())
    record = orchestrator.build_tool_action_record(
        CONTEXT,
        event_type="tool_failed",
        message="The provider returned no reply for this step, so no tool was selected.",
        details={"tool_name": "unknown", "status": "provider_did_not_answer"},
    )

    assert record is not None, "sabotage did not bite: the sentinel is refused by something else"
    assert record.get("tool_name") == "unknown"


def test_sabotage_dropping_the_path_blanks_the_workspace_receipt(monkeypatch) -> None:
    """Revert the emitter and the receipt goes back to saying nothing about what was read."""
    captured: list[dict] = []

    class _Agent:
        def _emit_runtime_event(self, source_context, **details):
            captured.append(details)

    class _Execution:
        ok = True
        response_text = "read 3 lines"
        details: dict = {}

    import core.agent_runtime.fast_paths_utility as util

    real = util._emit_workspace_tool_events

    def without_arguments(agent, source_context, *, intent, execution, path=""):
        agent._emit_runtime_event(
            source_context, event_type="tool_executed", message="m", tool_name=intent, summary="m",
        )

    monkeypatch.setattr(util, "_emit_workspace_tool_events", without_arguments)
    util._emit_workspace_tool_events(
        _Agent(), dict(CONTEXT), intent="workspace.read_file", execution=_Execution(), path="notes.txt",
    )

    assert real is not without_arguments
    assert captured[0].get("arguments") is None
    assert build_tool_action_record(
        CONTEXT, event_type="tool_executed", message="m", details=captured[0],
    ) is not None
