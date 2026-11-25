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
