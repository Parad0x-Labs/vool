"""What a chat app or phone reads under and inside an answer: plain words for someone away from the computer.

sls's Telegram, 2026-11-27: "On this computer · qwen3:8b" under answers read on the phone; a refusal listed
"/Users/<name>/Downloads, /Users/<name>/Documents, ..."; and a reminder was confirmed twice, the second time
as a receipt line repeating the note and time.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.request_trust import folder_name, folder_names, phone_folders_text


def test_folders_are_named_never_given_as_full_paths():
    home = str(Path.home())
    chosen = (f"{home}/Downloads", f"{home}/Documents/", "D:\\Work\\Invoices", f"{home}/Work/Notes", f"{home}/School/Notes")
    said = folder_names(chosen)
    assert said == "Downloads, Documents, Invoices, Work/Notes, School/Notes", said
    assert home not in said and "/Users/" not in said
    assert folder_name(home) == "your home folder" and folder_name("") == ""
    assert home not in phone_folders_text(chosen)


@pytest.fixture
def proof(monkeypatch):
    import core.proof_projection as projection

    shown = {}
    monkeypatch.setattr(projection, "build_turn_proof", lambda **_: shown["proof"])
    return shown


def _notes(proof, receipts, route, reply):
    from core.mobile_companion.bot_runtime import turn_notes

    proof["proof"] = {"compact": {"action_receipts": receipts, "route": route}, "expanded": {"model": "qwen3:8b"}}
    return turn_notes("telegram", {"canonical_chat_id": "c", "turn_id": "t"}, "en", reply=reply)


def test_the_place_is_your_desktop_away_from_it(proof):
    assert _notes(proof, [], {"summary": "local"}, "Hi") == ["On your desktop \u00b7 qwen3:8b"]
    assert _notes(proof, [], {"summary": "mixed"}, "Hi") == ["Your desktop + online \u00b7 qwen3:8b"]
    assert _notes(proof, [], {"summary": "local", "own_network": True}, "Hi") == ["On your own network \u00b7 qwen3:8b"]


def test_an_answer_no_model_was_asked_for_still_says_your_desktop(proof):
    # Telegram 2026-11-27 (list item 49): a greeting showed "No AI model" as where it ran.
    assert _notes(proof, [], {"summary": "none"}, "Hi") == ["On your desktop"]


def test_a_receipt_the_reply_already_says_is_its_state_alone(proof):
    receipt = {"intent": "schedule_reminder", "action_key": "approval.action.schedule_reminder", "state": "checked",
               "note": "water the ferns", "due_utc": "2026-11-28T08:00:00Z", "due_tz": "UTC"}
    said = _notes(proof, [receipt], {"summary": "local"}, "Reminder set: water the ferns, Sat 28 Nov 2026 at 08:00.")
    assert said == ["Done, checked \u00b7 On your desktop \u00b7 qwen3:8b"], said


def test_a_receipt_the_reply_does_not_say_keeps_its_line(proof):
    kept = {"intent": "memory_keep", "action_key": "approval.action.memory_keep", "state": "checked", "note": "my bike is blue"}
    said = _notes(proof, [kept], {"summary": "local"}, "Locked in.")
    assert len(said) == 2 and "my bike is blue" in said[0] and said[1].startswith("On your desktop"), said


def test_a_receipt_that_did_not_go_through_keeps_its_line_and_folder_name(proof):
    failed = {"intent": "send_file", "action_key": "", "state": "failed", "note": "report.pdf", "reason": "too big",
              "file": "report.pdf", "folder": str(Path.home() / "Documents")}
    said = _notes(proof, [failed], {"summary": "local"}, "I sent report.pdf")
    assert "Not done: too big" in said[0] and "in Documents" in said[0] and str(Path.home()) not in said[0], said
