"""Chat continuity, round 8 (Mac app, qwen3:8b, build f4f048f7).

M3: "list the files on my desktop" ran list_dir and was still refused by the file-claim guard.
M2: "and the oldest?" after a listing was answered by the model from old text and picked the wrong file.
M6: "no thanks, try the documents folder instead" after a recall miss searched for a folder named documents.
M5: "what is the heaviest thing on my whole computer" was declined as not about the owner's files.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

from core import conversation_state as cs
from core.clean_loop.tools import UNCHECKED_FILE_CLAIM_TEXT, unbacked_file_claims
from core.execution.constants import machine_kind_listing_intent
from tests.test_clean_local_loop_turn import turn_setup

SC = {"surface": "openclaw", "_owner_local": True, "platform": "cli"}


# --- seed ------------------------------------------------------------------------------------------------------------------

def _seed_home(home):
    """The fake home the real-app rounds use: a Downloads of 12 files with distinct dates, a Desktop of 7."""
    pics = [("IMG_20230614_101522.jpg", "2023-06-14", 2100), ("IMG_20240102_090000.jpg", "2024-01-02", 2300),
            ("IMG_20250105_081212.jpg", "2025-01-05", 1900), ("IMG_20261108_094501.jpg", "2026-11-08", 2700),
            ("dog_park.jpg", "2024-05-19", 3100), ("cat_on_sofa.jpg", "2026-02-14", 1500),
            ("whatsapp image 2026-11-09.jpeg", "2026-11-09", 1200), ("Screenshot 2026-11-10 at 14.02.11.png", "2026-11-10", 2900),
            ("Screenshot 2026-11-12 at 09.15.40.png", "2026-11-12", 2050), ("Screenshot 2025-03-03 at 10.00.00.png", "2025-03-03", 2500),
            ("beach-sunset.png", "2024-08-14", 3500), ("passport_scan.png", "2022-08-08", 1000)]
    desk = [("archive_photos_2025.zip", "2025-09-01", 3000), ("project_backup.zip", "2026-10-30", 3004),
            ("Screenshot 2026-11-01 at 10.00.00.png", "2026-11-01", 3003), ("Screenshot 2026-11-05 at 12.30.00.png", "2026-11-05", 3002),
            ("Screenshot 2026-11-09 at 08.15.00.png", "2026-11-09", 3001), ("Screenshot 2026-11-11 at 19.45.00.png", "2026-11-11", 3005),
            ("Lease_renewal_2026.pdf", "2026-11-12", 653)]
    (home / "Downloads" / "plumber_invoice.txt").unlink(missing_ok=True)  # the shared fixture's own file
    for folder, rows in (("Downloads", pics), ("Desktop", desk)):
        for name, date, size in rows:
            path = home / folder / name
            path.write_bytes(b"x" * size)
            stamp = time.mktime(datetime.strptime(date, "%Y-%m-%d").timetuple())
            os.utime(path, (stamp, stamp))
    return pics, desk


# --- M6 --------------------------------------------------------------------------------------------------------------------

NOTE_ASK = "do i have a note about the boiler service anywhere"
RECALL_MISS = {"response": "I don't have that saved, and I looked through your other chats and projects (6 chats) without finding it."}


def test_m6_whole_turn_a_redirect_after_a_recall_miss_looks_in_that_folder(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    (home / "Documents" / "boiler_service_2026.txt").write_text("Boiler service booked 12 March 2027.\n")
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    cs.record_sealed_turn("m6-chat", NOTE_ASK, dict(RECALL_MISS))
    fake.replies[:] = ['{"tool": "find", "query": "boiler service"}',
                       '{"answer": "Yes: boiler_service_2026.txt in Documents says the service is booked."}']
    r = agent.run_once("no thanks, try the documents folder instead", source_context=dict(SC, session_id="m6-chat"))
    assert "Found 1 folder" not in r["response"], r["response"]
    assert "boiler_service_2026.txt" in r["response"], r["response"]
    asked = fake.chats[0]["messages"][-1]["content"]
    assert "boiler service" in asked and "~/Documents" in asked, asked


@pytest.mark.parametrize("message", ["nah look in documents", "meh, how about the documents", "ok try documents instead"])
def test_m6_whole_turn_variants_stay_a_file_search(turn_setup, monkeypatch, message):
    agent, fake, home = turn_setup
    (home / "Documents" / "boiler_service_2026.txt").write_text("Boiler service booked 12 March 2027.\n")
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    cs.record_sealed_turn("m6-chat", NOTE_ASK, dict(RECALL_MISS))
    fake.replies[:] = ['{"tool": "find", "query": "boiler service"}',
                       '{"answer": "Yes: boiler_service_2026.txt in Documents says the service is booked."}']
    r = agent.run_once(message, source_context=dict(SC, session_id="m6-chat"))
    assert "Found 1 folder" not in r["response"], r["response"]
    asked = fake.chats[0]["messages"][-1]["content"]
    assert "boiler service" in asked and "~/Documents" in asked, asked


# --- M3 --------------------------------------------------------------------------------------------------------------------

DESKTOP_LIST = '{"tool": "list_dir", "path": "Desktop"}'


def _desktop_turn(agent, fake, monkeypatch, home, answer, ask="list the files on my desktop"):
    _seed_home(home)
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = [DESKTOP_LIST, '{"answer": ' + __import__("json").dumps(answer, ensure_ascii=False) + "}"]
    return agent.run_once(ask, source_context=dict(SC))


# What a model writes from the real Desktop listing: names with spaces, emoji and bold marks, a folder path or a size in
# front of a name, other date forms, sizes rounded. Every one is true, so every one is published as the model wrote it.
TRUE_ANSWERS = [
    "Your Desktop has:\n- \U0001F4C4 Lease_renewal_2026.pdf (653 bytes)\n- \U0001F4C4 project_backup.zip (2.9 KB)",
    "- ~/Desktop/project_backup.zip (2.9 KB, modified 2026-10-30)\n- ~/Desktop/Lease_renewal_2026.pdf (653 B)",
    "- `Screenshot 2026-11-01 at 10.00.00.png` (2.9 KB, modified Nov 1, 2026)\n- `Screenshot 2026-11-05 at 12.30.00.png` (2.9 KB)",
    "The Lease_renewal_2026.pdf was last changed on Nov 12, 2026 and is 653 bytes.",
    "Lease_renewal_2026.pdf, 653 bytes, modified 12 November 2026.",
    "1. **archive_photos_2025.zip** — about 3 KB\n2. **project_backup.zip** — about 3 KB",
    "Lease_renewal_2026.pdf is about 0.6 KB; project_backup.zip is 2.9 KB.",
    "- \"archive_photos_2025.zip\" (2.9 KB, modified 1 Sep 2025)\n- 'project_backup.zip' (2.9 KB, modified 30 Oct 2026)",
    "- 2.9 KB project_backup.zip\n- 653 bytes Lease_renewal_2026.pdf",
    "Lease_renewal_2026.pdf: 1 KB, changed 12/11/2026. project_backup.zip: 3 KB, changed 30/10/2026.",
    "Newest is Screenshot 2026-11-11 at 19.45.00.png (2.9 KB, modified November 11th, 2026).",
    "You have 7 files: archive_photos_2025.zip, Lease_renewal_2026.pdf, project_backup.zip and four screenshots from 1, 5, 9 and 11 Nov.",
]


@pytest.mark.parametrize("answer", TRUE_ANSWERS)
def test_m3_a_true_answer_from_the_listing_is_published_as_written(turn_setup, monkeypatch, answer):
    agent, fake, home = turn_setup
    r = _desktop_turn(agent, fake, monkeypatch, home, answer)
    assert r["response"].strip() == answer.strip(), r["response"]
    assert r["response"] != UNCHECKED_FILE_CLAIM_TEXT


# A name no tool result holds: never published, and because a tool ran this turn the reply is the tool's own listing.
FALSE_ANSWERS = [
    ("Your Desktop has notes_2026.txt (1 KB) and project_backup.zip.", "notes_2026.txt"),
    ("- \U0001F4C4 holiday_budget_2026.xlsx (4 KB)\n- \U0001F4C4 project_backup.zip (2.9 KB)", "holiday_budget_2026.xlsx"),
    ("- ~/Desktop/old/backup_2024.zip (2.9 KB)", "backup_2024.zip"),
    ("- `Screenshot 2026-11-05 at 12.30.00 PM.png` (2.9 KB)", "12.30.00 PM.png"),
    ("Lease_renewal_2026.pdf (653 bytes) and rent_receipt_oct.pdf (80 KB).", "rent_receipt_oct.pdf"),
    ("1. **resume_final_v3.docx** — 40 KB", "resume_final_v3.docx"),
]


@pytest.mark.parametrize("answer, ghost", FALSE_ANSWERS)
def test_m3_a_name_in_no_evidence_is_replaced_by_the_tools_own_listing(turn_setup, monkeypatch, answer, ghost):
    agent, fake, home = turn_setup
    r = _desktop_turn(agent, fake, monkeypatch, home, answer)
    assert ghost not in r["response"], r["response"]
    assert r["response"] != UNCHECKED_FILE_CLAIM_TEXT, "a tool ran this turn: the listing is the fallback, not the refusal"
    assert "Lease_renewal_2026.pdf" in r["response"] and "project_backup.zip" in r["response"], r["response"]
    assert r["response"].startswith("Visible entries under"), r["response"]


def test_m3_when_nothing_could_be_listed_the_refusal_stands(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = ['{"answer": "The largest file in your Downloads is holiday_budget_2026.xlsx at 4 KB."}']
    r = agent.run_once("remind me what that holiday spreadsheet is called and its size", source_context=dict(SC))
    assert r["response"] == UNCHECKED_FILE_CLAIM_TEXT


@pytest.mark.parametrize("answer, bad", [
    ("- \U0001F4C4 beach-sunset.png (2.0 KB)", False),
    ("- ~/Downloads/beach-sunset.png (2.0 KB, modified Aug 14, 2024)", False),
    ("- beach-sunset.png (2.0 KB, modified 14/08/2024)", False),
    ("- beach-sunset.png (2.0 KB, modified Aug 15, 2024)", True),
    ("- beach-sunset.png (2.0 KB, modified 2024-08-15)", True),
    ("- \U0001F4C4 nowhere.png (2.0 KB)", True),
    ("- beach-sunset.png (9 KB)", True),
    ("- 2.0 KB beach-sunset.png", False),
])
def test_m3_the_matcher_tolerates_wrapping_formats_and_rounding_but_not_a_wrong_fact(answer, bad):
    evidence = ["Visible entries under `~/Downloads`:\n- beach-sunset.png (2.0 KB, modified 2024-08-14, 700 days ago)"]
    assert bool(unbacked_file_claims(answer, evidence)) is bad, answer


# --- M2 --------------------------------------------------------------------------------------------------------------------

LISTING = '{"tool": "list_dir", "path": "Downloads"}'
LISTED = '{"answer": "Here are the files in your Downloads folder: beach-sunset.png, cat_on_sofa.jpg, dog_park.jpg and nine more."}'
WRONG_OLDEST = '{"answer": "The oldest file is IMG_20230614_101522.jpg, modified on 2023-06-14."}'


def _after_a_listing(agent, fake, monkeypatch, home, second, model_replies, chat="m2-chat"):
    _seed_home(home)
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = [LISTING, LISTED]
    r1 = agent.run_once("show me my downloads", source_context=dict(SC, session_id=chat))
    done = len(fake.chats)
    fake.replies[:] = [*fake.replies[:done], *model_replies]
    r2 = agent.run_once(second, source_context=dict(SC, session_id=r1.get("session_id") or chat))
    return r2, len(fake.chats) - done


def test_m2_the_oldest_after_a_listing_is_the_minimum_modified_time(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    r, model_calls = _after_a_listing(agent, fake, monkeypatch, home, "and the oldest?", [WRONG_OLDEST])
    assert "passport_scan.png" in r["response"] and "IMG_20230614_101522.jpg" not in r["response"], r["response"]
    assert "2022-08-08" in r["response"]
    assert model_calls == 0 and r.get("route_reason") == "machine_read_fast_path"


# fresh ordering follow-ups the exact lane answers from the folder itself: (ask, the one right file)
ORDERED = [
    ("and the oldest?", "passport_scan.png"),
    ("which one is the earliest", "passport_scan.png"),
    ("whats the oldest one", "passport_scan.png"),
    ("ok which is the least recent", "passport_scan.png"),
    ("meh, just show me the newest", "Screenshot 2026-11-12 at 09.15.40.png"),
    ("and the latest one?", "Screenshot 2026-11-12 at 09.15.40.png"),
    ("what is the heaviest", "beach-sunset.png"),
    ("whats the biggest one", "beach-sunset.png"),
    ("and the lightest?", "passport_scan.png"),
    ("which is the smallest one", "passport_scan.png"),
    ("tell me the tiniest", "passport_scan.png"),
]


@pytest.mark.parametrize("ask, expected", ORDERED)
def test_m2_every_ordering_direction_names_the_right_file(turn_setup, monkeypatch, ask, expected):
    agent, fake, home = turn_setup
    r, model_calls = _after_a_listing(agent, fake, monkeypatch, home, ask, [WRONG_OLDEST])
    first_line = next(ln for ln in r["response"].splitlines() if ln.startswith("- "))
    assert expected in first_line, r["response"]
    assert model_calls == 0, "the folder answers, no model compares dates or sizes"


# An ordering ask with words of its own (so not the bare lane) after a listing: the model, reading old text, says the wrong
# file with no tool run. The structural guard puts the folder's own ordering in its place.
EXTRA_WORDS = [
    "but which of those is the oldest, with its date",
    "so which one is the oldest overall, name and age pls",
    "hm which of these is the earliest one i got, when was it made",
]


@pytest.mark.parametrize("ask", EXTRA_WORDS)
def test_m2_an_ordering_ask_is_never_left_to_the_model_reading_old_text(turn_setup, monkeypatch, ask):
    agent, fake, home = turn_setup
    r, _n = _after_a_listing(agent, fake, monkeypatch, home, ask, [WRONG_OLDEST, WRONG_OLDEST, WRONG_OLDEST])
    assert "passport_scan.png" in r["response"] and "IMG_20230614_101522.jpg" not in r["response"], r["response"]


@pytest.mark.parametrize("ask, answer", [
    ("whats the biggest country in europe", "The largest country in Europe is Russia."),
    ("how many planets are there", "There are eight planets."),
    ("who is the oldest person alive", "I can't look that up here."),
])
def test_m2_an_ask_with_a_subject_of_its_own_is_not_taken(turn_setup, monkeypatch, ask, answer):
    agent, fake, home = turn_setup
    r, _n = _after_a_listing(agent, fake, monkeypatch, home, ask, ['{"answer": "' + answer + '"}'])
    assert "passport_scan" not in r["response"] and "Downloads" not in r["response"], r["response"]


def test_m2_oldest_leaves_the_bare_ask_in_the_lane():
    for ask in ("and the oldest?", "whats the earliest", "ok the least recent one"):
        got = machine_kind_listing_intent(ask, folder_hint="~/Downloads")
        assert got and got["path"] == "~/Downloads" and got.get("size_order") == "oldest" and not got.get("newest_first"), (ask, got)
    assert machine_kind_listing_intent("and the oldest?") is None
    assert machine_kind_listing_intent("whats the oldest tree in europe", folder_hint="~/Downloads") is None


def test_m2_the_newest_reply_says_it_is_the_newest_and_gives_the_date(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    r, _n = _after_a_listing(agent, fake, monkeypatch, home, "which one is the newest", [WRONG_OLDEST])
    assert r["response"].startswith("Newest in `~/Downloads`:"), r["response"]
    assert "Screenshot 2026-11-12 at 09.15.40.png (modified 2026-11-12)" in r["response"], r["response"]


def _recall_miss(outcome=cs.ANSWERED):
    return cs.PendingRequest(NOTE_ASK, outcome, "", {}, 0)


MISS_REDIRECTS = [
    ("no thanks, try the documents folder instead", "~/Documents"),
    ("nah, look in the desktop", "~/Desktop"),
    ("meh, how about downloads", "~/Downloads"),
    ("ok what about the pictures folder", "~/Pictures"),
    ("hmm, check documents then", "~/Documents"),
    ("the documents folder instead", "~/Documents"),
    ("no, search the desktop folder", "~/Desktop"),
]


@pytest.mark.parametrize("message, folder", MISS_REDIRECTS)
def test_m6_a_redirect_after_a_recall_miss_is_the_same_lookup_in_that_folder(message, folder):
    res = cs.resolve("s", message, pending=_recall_miss(), cleared=None)
    assert res.kind == "retry_changed" and res.request == NOTE_ASK, (message, res)
    assert res.slots.get("path") == folder


MISS_TRAPS = [
    "no thanks, try the documents folder for the plumber invoice",
    "try finding my passport scan in documents",
    "look in downloads for the newest screenshot",
    "try documents for my tax return",
    "how many pdfs are in documents",
    "thanks anyway",
    "whats the weather in desktop",
]


@pytest.mark.parametrize("message", MISS_TRAPS)
def test_m6_traps_after_a_recall_miss_stay_their_own_asks(message):
    res = cs.resolve("s", message, pending=_recall_miss(), cleared=None)
    assert res.kind != "retry_changed" and res.request != NOTE_ASK, (message, res)


def test_m6_a_redirect_after_a_general_question_is_not_rewritten():
    web = cs.PendingRequest("whats the capital of france", cs.ANSWERED, "", {}, 0)
    for message in ("no thanks, try the documents folder instead", "meh, how about downloads"):
        assert cs.resolve("s", message, pending=web, cleared=None).kind != "retry_changed"
