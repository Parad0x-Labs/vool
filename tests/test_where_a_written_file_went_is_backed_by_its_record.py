""""Where did you put that file?" answered with the file a verified write made is not replaced as an unbacked claim.

iMac gate on c78c018da (a chat bound to a project): "make a file named seed-order.txt ..." was written and verified;
"where did you put that file?" was answered "It's in this chat's workspace: `<project>/seed-order.txt`. Files
created/updated there: `seed-order.txt`." and then replaced with "I cannot verify the claimed action". The write lane's
receipt held its path only in the typed record (arguments {}), and the past-receipt check read the arguments alone.
The receipts below have the shape that run stored; names and folders are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core.agent_runtime import action_honesty_validator as validator


def _receipt(path: str, *, where: str) -> dict:
    record = {"action_type": "tool_executed", "parameters": {}, "target": ""}
    if where == "parameters":
        record["parameters"] = {"path": path}
    else:
        record["target"] = path
    return {"tool_name": "workspace.write_file", "arguments": {}, "session_id": "chat-1",
            "execution": {"action_record": record}}


@pytest.mark.parametrize("seed", range(6))
def test_the_answer_naming_the_recorded_file_is_backed(seed, monkeypatch) -> None:
    rng = random.Random(f"written-{seed}")
    name = rng.choice(["seed-order.txt", "packliste.md", "pirkiniai.csv", "notes-2026.txt"])
    root = rng.choice(["/srv/work/proj", "/data/garden"])
    receipt = _receipt(name, where=rng.choice(["parameters", "target"]))
    monkeypatch.setattr("core.runtime_continuity.list_runtime_tool_receipts", lambda *_a, **_k: [receipt])
    monkeypatch.setattr(validator, "_receipt_shows_execution", lambda *_a, **_k: True)
    output = {"route": "location_followup_receipts",
              "response": f"It's in this chat's workspace: `{root}/{name}`.\nFiles created/updated there: `{name}`."}

    assert validator._location_answer_has_verified_past_receipt(
        output, session_id="chat-1", source_context={"workspace": root})


def test_an_answer_naming_another_file_is_still_not_backed(monkeypatch) -> None:
    receipt = _receipt("seed-order.txt", where="parameters")
    monkeypatch.setattr("core.runtime_continuity.list_runtime_tool_receipts", lambda *_a, **_k: [receipt])
    monkeypatch.setattr(validator, "_receipt_shows_execution", lambda *_a, **_k: True)
    output = {"route": "location_followup_receipts", "response": "I saved it as `budget-2027.xlsx` in your project."}

    assert not validator._location_answer_has_verified_past_receipt(output, session_id="chat-1", source_context={})


def test_an_unverified_record_backs_nothing(monkeypatch) -> None:
    receipt = _receipt("seed-order.txt", where="parameters")
    monkeypatch.setattr("core.runtime_continuity.list_runtime_tool_receipts", lambda *_a, **_k: [receipt])
    monkeypatch.setattr(validator, "_receipt_shows_execution", lambda *_a, **_k: False)
    output = {"route": "location_followup_receipts", "response": "Files created/updated there: `seed-order.txt`."}

    assert not validator._location_answer_has_verified_past_receipt(output, session_id="chat-1", source_context={})


# iMac gate on 174b25378: a write made by the builder lane ("please create plumber-notes.txt saying ...") left no path
# in its receipt at all (arguments {}, parameters {}, target ""). The location render is built from this chat's
# completed-write events, which only the runtime emits, so those events back it.
def _completed(name: str, *, ok: bool = True, where: str = "message") -> dict:
    details = {"ok": ok, "canonical_target": name if where == "target" else ""}
    message = f"Created `{name}`." if where == "message" else "Created the file."
    return {"event_type": "workspace_mutation_completed", "message": message, "details": details}


@pytest.mark.parametrize("seed", range(6))
def test_a_builder_write_with_an_empty_receipt_is_backed_by_its_completed_event(seed, monkeypatch) -> None:
    rng = random.Random(f"builder-{seed}")
    name = rng.choice(["plumber-notes.txt", "druskos-sarasas.txt", "einkauf.md", "tap/drip-log.txt"])
    root = rng.choice(["/srv/work/projA", "/data/garden"])
    empty = {"tool_name": "workspace.write_file", "arguments": {}, "session_id": "chat-1",
             "execution": {"action_record": {"action_type": "tool_executed", "parameters": {}, "target": ""}}}
    monkeypatch.setattr("core.runtime_continuity.list_runtime_tool_receipts", lambda *_a, **_k: [empty])
    monkeypatch.setattr(validator, "_receipt_shows_execution", lambda *_a, **_k: False)
    events = [_completed("other.txt"), _completed(name, where=rng.choice(["message", "target"]))]
    monkeypatch.setattr("core.runtime_continuity.list_recent_runtime_session_events", lambda *_a, **_k: events)
    output = {"route": "location_followup_receipts",
              "response": f"It's in this chat's workspace: `{root}`.\nFiles created/updated there: `{name}`."}

    assert validator._location_answer_has_verified_past_receipt(output, session_id="chat-1", source_context={})


@pytest.mark.parametrize("case", ["no_event", "failed_event", "other_route", "one_unknown_file"])
def test_completed_events_back_only_the_runtime_render_of_their_own_files(case, monkeypatch) -> None:
    monkeypatch.setattr("core.runtime_continuity.list_runtime_tool_receipts", lambda *_a, **_k: [])
    events = {"no_event": [], "failed_event": [_completed("plumber-notes.txt", ok=False)]}.get(
        case, [_completed("plumber-notes.txt")])
    monkeypatch.setattr("core.runtime_continuity.list_recent_runtime_session_events", lambda *_a, **_k: events)
    listed = "`plumber-notes.txt`, `budget.xlsx`" if case == "one_unknown_file" else "`plumber-notes.txt`"
    output = {"route": "model_reply" if case == "other_route" else "location_followup_receipts",
              "response": f"It's in this chat's workspace: `/srv/work`.\nFiles created/updated there: {listed}."}

    assert not validator._location_answer_has_verified_past_receipt(output, session_id="chat-1", source_context={})
