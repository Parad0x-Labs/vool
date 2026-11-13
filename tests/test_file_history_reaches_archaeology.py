"""A file-history question reaches the archaeology trace, not a code task's report.

Measured 2026-11-13 in the skills A/B on f4f048f7 (real Mac, qwen3:8b, clean loop on and off):
"which task changed this file" was offered `code.task.report` (ranked in by the word "task") and
never `operator.command.archaeology.trace`. The model called the report with the question as the
task id and the turn answered "No code task `which task changed this file` exists in the journal."

Two seams, both proven here:
- the demand rule (core.tool_demand_signals) names the trace for file-history wording, and the
  capability graph can seat a command-registry read by demand (it had no spec row for one);
- with no code task open, the offer drops `code.task.report` for those turns.

The served test drives a real daemon whose scripted model calls whichever of the two tools the
offer carries, so it fails on a tree that offers the report.
"""
from __future__ import annotations

import json

import pytest

TRACE = "operator.command.archaeology.trace"
REPORT = "code.task.report"

FILE_HISTORY_ASKS = [
    "which task changed this file",  # the A/B wording
    "Which task changed this file?",
    "which turn edited notes.md?",
    "What run modified config.yaml yesterday?",
    "which chat deleted my budget.xlsx",
    "who last modified src/app.py",
    "who changed this file",
    "what happened to this file",
    "show me the history of this file",
    "Which of your runs updated index.html?",
]

NOT_FILE_HISTORY = [
    "change this file to use tabs",
    "edit notes.md and add a title",
    "which file did the task change?",
    "what changed in the code task?",
    "what is the schema of the sqlite database file in my documents",
    "which tasks are on my list today?",
]


@pytest.fixture(scope="module")
def offer():
    from core.command_registry.registry import registry
    import core.capability_graph as graph

    registry()
    graph.refresh_from_registry()
    from core.tool_offer_assembly import assemble_tool_offer

    def _offer(text: str) -> list[str]:
        return list(assemble_tool_offer(user_text=text, source_context={}).intents)

    return _offer


@pytest.mark.parametrize("text", FILE_HISTORY_ASKS)
def test_a_file_history_question_names_the_trace(text):
    from core.tool_demand_signals import resolve_demand_signals

    assert TRACE in resolve_demand_signals(text).explicit_intents, text


@pytest.mark.parametrize("text", NOT_FILE_HISTORY)
def test_other_wordings_do_not_name_the_trace(text):
    from core.tool_demand_signals import resolve_demand_signals

    assert TRACE not in resolve_demand_signals(text).explicit_intents, text


@pytest.mark.parametrize("text", FILE_HISTORY_ASKS)
def test_the_offer_seats_the_trace_and_not_the_task_report(offer, text):
    intents = offer(text)
    assert TRACE in intents, (text, intents)
    assert REPORT not in intents, (text, intents)


def test_the_seated_trace_carries_its_arguments(offer):
    from core.tool_offer_assembly import assemble_tool_offer

    specs = assemble_tool_offer(user_text="which task changed this file", source_context={}).specs
    row = next(spec for spec in specs if spec.get("intent") == TRACE)
    assert {"path", "session_id", "turn_id", "since", "until"} <= set(row["arguments"])
    # Every selector is optional: a call that names only the file must not be refused.
    assert all("optional" in str(kind) for kind in row["arguments"].values()), row["arguments"]
    assert row["read_only"] is True


@pytest.mark.parametrize("text", ["which file did the task change?", "what changed in the code task?"])
def test_a_question_about_a_code_task_keeps_its_report(offer, text):
    intents = offer(text)
    assert REPORT in intents, (text, intents)
    assert TRACE not in intents, (text, intents)


# ------------------------------------------------------------------------------------------- served


_PROMPTED_TURN = None


def _choosing_model(body, state, model):
    """A stand-in for qwen3:8b on the Mac: on a native tool turn it calls the code task report when the
    offer carries it (what the A/B saw) and the archaeology trace otherwise; once a tool result is back
    it answers from it."""
    from tests import _served_skill_rig as rig

    tool_results = [m for m in (body.get("messages") or []) if m.get("role") == "tool"]
    if tool_results:
        return rig._completion(content="Here is what the records show: " + str(tool_results[-1].get("content") or "")[:300])
    blob = " ".join(str(m.get("content") or "") for m in body.get("messages") or [])
    if "trace joined" in blob:
        # The tool's observation came back inside the prompt (the answer-writing call).
        return rig._completion(content="VOOL's own records hold no change to that file yet (the trace joined 0 objects).")
    names = [str((t.get("function") or {}).get("name") or "") for t in body.get("tools") or []]
    if "code__task__report" in names:
        return rig._completion(tool_calls=[rig._tool_call(
            "c1", "code__task__report", {"task_id": "which task changed this file"})], finish="tool_calls")
    if "operator__command__archaeology__trace" in names:
        return rig._completion(tool_calls=[rig._tool_call(
            "c1", "operator__command__archaeology__trace", {"path": "notes.md"})], finish="tool_calls")
    # Not a native tool turn (the prompted planning call): the rig's own default answer.
    return _PROMPTED_TURN(body, state, model)


@pytest.mark.served
def test_served_which_task_changed_this_file_runs_the_trace(tmp_path, monkeypatch):
    from core.chat_session_identity import canonical_chat_session_id
    from tests import _served_skill_rig as rig

    global _PROMPTED_TURN
    _PROMPTED_TURN = rig._turn_response
    monkeypatch.setattr(rig, "_turn_response", _choosing_model)
    state = rig.ProviderState()
    server, port = rig.make_provider_server(state)
    home = tmp_path / "home"
    home.mkdir()
    daemon = rig.ServedDaemon(home, port).start()
    try:
        daemon.pin_provider_model()
        cert = daemon.certify_provider_model()
        assert (cert.get("result") or cert).get("state") == "verified", json.dumps(cert)[:400]
        for mode in ("manual", "auto"):
            session = f"openclaw:file-history-served-{mode}"
            reply = daemon.chat("which task changed this file", session=session, mode=mode)
            answer = str(((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            events = daemon.session_events(canonical_chat_session_id(session))
            called = [str(e.get("tool_name") or e.get("intent") or "") for e in events
                      if e.get("event_type") in {"tool_selected", "tool_executed", "tool_failed", "tool_result"}]
            offered = [[str((t.get("function") or {}).get("name") or "") for t in body.get("tools") or []]
                       for body in state.requests if body.get("tools") and any(
                           "which task changed" in str(m.get("content") or "") for m in body.get("messages") or [])]
            assert offered, "no native tool turn reached the model"
            assert all("operator__command__archaeology__trace" in names for names in offered), offered
            assert not any("code__task__report" in names for names in offered), offered
            assert "No code task" not in answer, (mode, answer)
            assert TRACE in called and REPORT not in called, (mode, called)
            if mode == "manual":
                # Manual mode asks before the trace reads the named file: the card is the trace's.
                assert "requires approval" in answer and "notes.md" in answer, answer
            else:
                assert "trace joined" in answer, (mode, answer, called)
    finally:
        daemon.stop()
        server.shutdown()
