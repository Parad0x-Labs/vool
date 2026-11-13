"""Chat continuity, round 7: three failures measured on the Mac app (build 11eef8dc, qwen3:8b).

T12: after "list my downloads" (12 files, all 2.0 KB), "meh, just tell me whats the largest one" answered "holiday_budget_2026.xlsx
at 4 KB": a file of another folder, no tool run in the turn. The loop's look-first step measured the whole home folder because the ask
named no place, and the exact folder lanes only took a follow-up that said "there" or "that folder".
T11: "no thanks, try the documents folder instead" after a found-nothing find searched for a folder NAMED documents.
S3: a file named like a tool (`vool-database.backup`) that the turn's own listing held was cut out of the reply as an unrun tool.
"""
from __future__ import annotations

import pytest

from core import conversation_state as cs
from core import final_answer_authorship as fa
from core.clean_loop.tools import UNCHECKED_FILE_CLAIM_TEXT, unbacked_file_claims
from core.execution.constants import machine_kind_listing_intent
from tests.test_clean_local_loop_turn import turn_setup  # noqa: F401  (fixture)

SC = {"surface": "openclaw", "_owner_local": True, "platform": "cli"}
OUTSIDE = "holiday_budget_2026.xlsx"
DOWNLOADS = [
    "beach-sunset.png", "cat_on_sofa.jpg", "dog_park.jpg", "IMG_20230614_101522.jpg", "IMG_20240102_090000.jpg",
    "IMG_20250105_081212.jpg", "IMG_20261108_094501.jpg", "passport_scan.png", "Screenshot 2025-03-03 at 10.00.00.png",
    "Screenshot 2026-11-10 at 14.02.11.png", "Screenshot 2026-11-12 at 09.15.40.png", "whatsapp image 2026-11-09.jpeg",
]
LISTING = '{"tool": "list_dir", "path": "Downloads"}'
LISTED = '{"answer": "Here are the files in your Downloads folder: ' + ", ".join(DOWNLOADS[:3]) + '."}'
CLAIM = '{"answer": "The largest file in your Downloads is ' + OUTSIDE + ' at 4 KB."}'


def _seed(home):
    for name in DOWNLOADS:
        (home / "Downloads" / name).write_bytes(b"x" * 2048)
    (home / "Documents" / OUTSIDE).write_bytes(b"y" * 4096)


def _two(agent, fake, monkeypatch, first, second, replies_second):
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = [LISTING, LISTED]
    r1 = agent.run_once(first, source_context=dict(SC))
    done = len(fake.chats)
    fake.replies[:] = [*fake.replies[:done], *replies_second]
    r2 = agent.run_once(second, source_context=dict(SC, session_id=r1.get("session_id")))
    return r1, r2, len(fake.chats) - done


# --- T12 -------------------------------------------------------------------------------------------------------------------

def test_t12_the_largest_one_after_a_listing_is_measured_in_that_folder(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    _seed(home)
    r1, r2, model_calls = _two(agent, fake, monkeypatch, "list my downloads", "meh, just tell me whats the largest one", [CLAIM])
    assert r1.get("route_reason") == "clean_local_loop"
    assert OUTSIDE not in r2["response"], r2["response"]
    assert "Downloads" in r2["response"] and "2.0 KB" in r2["response"]
    assert model_calls == 0 and r2.get("route_reason") == "machine_read_fast_path"
    assert r2.get("tool_receipts")


def test_t12_the_record_of_a_loop_listing_holds_its_folder_and_text(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    _seed(home)
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = [LISTING, LISTED]
    r1 = agent.run_once("list my downloads", source_context=dict(SC))
    rec = cs.load(r1["session_id"])
    assert rec is not None and rec.slots.get("path") == "~/Downloads"
    assert "beach-sunset.png" in rec.slots.get("evidence", "") and OUTSIDE not in rec.slots.get("evidence", "")


# Wordings the exact lanes cannot answer (oldest, a kind in words, dates): the loop answers, and a file outside the evidence never goes out.
GUARDED = [
    "which one is the oldest",
    "and which was changed last",
    "so whats the heaviest of them, name and size pls",
    "but which of those is the newest, with its date",
    "is there a spreadsheet in there? name it",
    "tell me the one with the biggest size again",
    "any xlsx among them? which",
    "what is the oldest one and when was it made",
    "give me the name and size of the largest pls",
    "hmm which one takes the most space",
    "name the smallest and the largest",
]


@pytest.mark.parametrize("ask", GUARDED)
def test_t12_no_file_outside_the_evidence_is_published(turn_setup, monkeypatch, ask):
    agent, fake, home = turn_setup
    _seed(home)
    # The model claims the outside file every time, even after the folder was listed again for it.
    _r1, r2, _n = _two(agent, fake, monkeypatch, "list my downloads", ask, [CLAIM])
    assert OUTSIDE not in r2["response"], r2["response"]


def test_t12_a_claim_outside_the_evidence_forces_a_listing_and_the_true_answer_goes_out(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    _seed(home)
    truthful = '{"answer": "They are all the same size, 2.0 KB. The newest is Screenshot 2026-11-12 at 09.15.40.png."}'
    _r1, r2, model_calls = _two(agent, fake, monkeypatch, "list my downloads", "but which of those is the oldest, with its date", [CLAIM, CLAIM, truthful])
    assert OUTSIDE not in r2["response"]
    assert "Screenshot 2026-11-12 at 09.15.40.png" in r2["response"]
    steps = r2["clean_local_loop"]["steps"]
    assert any(s.get("tool") == "list_dir" and s.get("by") == "claim_guard" for s in steps), steps
    assert model_calls == 3  # the claim, the loop's look-first reminder, then one answer after the forced listing


def test_t12_when_it_cannot_be_checked_the_reply_says_so(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    _seed(home)
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = [CLAIM]
    r = agent.run_once("remind me what that holiday spreadsheet is called and its size", source_context=dict(SC))
    assert OUTSIDE not in r["response"]
    assert r["response"] == UNCHECKED_FILE_CLAIM_TEXT


def test_t12_a_fact_a_tool_showed_this_turn_stands(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    _seed(home)
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = [LISTING, '{"answer": "Your newest is IMG_20261108_094501.jpg, 2.0 KB."}']
    r = agent.run_once("which is my newest download and its size", source_context=dict(SC))
    assert "IMG_20261108_094501.jpg" in r["response"] and r["response"] != UNCHECKED_FILE_CLAIM_TEXT


EVIDENCE = ["Visible entries under `~/Downloads`:\n- beach-sunset.png (2.0 KB, modified 2024-08-14)\n- IMG_1.jpg (1.5 MB, modified 2024-01-02, 3 days ago)"]


@pytest.mark.parametrize("answer, bad", [
    ("The largest file in your Downloads is holiday_budget_2026.xlsx at 4 KB.", True),
    ("The largest is IMG_1.jpg at 1.5 MB.", False),
    ("The largest is IMG_1.jpg at 1.4 MB, from 2024-01-02.", False),
    ("The largest is IMG_1.jpg at 4 KB.", True),
    ("IMG_1.jpg was modified 2025-05-05.", True),
    ("- beach-sunset.png (2.0 KB)\n- notes_2026.txt (1 KB)", True),
    ("All of them are small. Version 1.2 of nothing, e.g. 3.5.", False),
])
def test_unbacked_file_claims_reads_names_sizes_and_dates(answer, bad):
    assert bool(unbacked_file_claims(answer, EVIDENCE)) is bad, answer


# Folder follow-ups the exact lane now answers from the record, with no pointer word ("there", "that folder").
LANE = [
    "meh, just tell me whats the largest one", "and the biggest one?", "which is the biggest", "whats the biggest file", "ok the smallest one then",
    "how many are there", "how many files", "whats the newest one", "which is the most recent", "show me the largest",
]


@pytest.mark.parametrize("ask", LANE)
def test_t12_a_bare_superlative_or_count_after_a_listing_means_the_listed_folder(ask):
    got = machine_kind_listing_intent(ask, folder_hint="~/Downloads")
    assert got is not None and got["path"] == "~/Downloads", (ask, got)
    assert machine_kind_listing_intent(ask) is None


@pytest.mark.parametrize("ask", [
    "whats the biggest country", "how many planets are there", "what is the largest ocean", "which is the tallest building",
    "how many days until friday", "whats the biggest one in my downloads", "the largest file on my computer",
])
def test_t12_a_subject_of_its_own_is_not_the_listed_folder(ask):
    got = machine_kind_listing_intent(ask, folder_hint="~/Documents/Invoices")
    assert got is None or got["path"] != "~/Documents/Invoices", (ask, got)


# --- T11 -------------------------------------------------------------------------------------------------------------------

BOILER = "is there a note about the boiler service in my files"


def test_t11_a_redirect_reruns_the_same_ask_in_that_folder(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    (home / "Documents" / "boiler_service_2026.txt").write_text("Boiler service booked 12 March 2027.\n")
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = ['{"tool": "find", "query": "boiler service"}', '{"answer": "There is no note about the boiler service in your files."}']
    r1 = agent.run_once(BOILER, source_context=dict(SC))
    done = len(fake.chats)
    fake.replies[:] = [*fake.replies[:done], '{"tool": "find", "query": "boiler service"}',
                       '{"answer": "Yes: boiler_service_2026.txt in Documents says the service is booked."}']
    r2 = agent.run_once("no thanks, try the documents folder instead", source_context=dict(SC, session_id=r1.get("session_id")))
    assert "boiler_service_2026.txt" in r2["response"] and "Found 1 folder" not in r2["response"], r2["response"]
    asked = fake.chats[done]["messages"][-1]["content"]
    assert "boiler service" in asked and "~/Documents" in asked, asked
    assert r2.get("route_reason") == "clean_local_loop"


def _pending(outcome, **slots):
    return cs.PendingRequest(BOILER, outcome, "executed", {"tool": "machine.find_file", **slots}, 0)


REDIRECTS = [
    ("no thanks, try the documents folder instead", "~/Documents"),
    ("nah look in the desktop", "~/Desktop"),
    ("meh, how about downloads", "~/Downloads"),
    ("ok what about the pictures folder", "~/Pictures"),
    ("the documents folder instead", "~/Documents"),
    ("hmm ok, check desktop then", "~/Desktop"),
    ("no, search the downloads folder", "~/Downloads"),
    ("ugh fine, use the documents folder", "~/Documents"),
]


@pytest.mark.parametrize("outcome", [cs.DONE, cs.ANSWERED])
@pytest.mark.parametrize("message, folder", REDIRECTS)
def test_t11_a_redirect_is_the_last_ask_with_a_changed_folder(message, folder, outcome):
    res = cs.resolve("s", message, pending=_pending(outcome), cleared=None)
    assert res.kind == "retry_changed" and res.request == BOILER, (message, res)
    assert res.slots.get("path") == folder


TRAPS = [
    "no thanks, try the documents folder for the plumber invoice",
    "try finding my passport scan in documents",
    "no, find the lease renewal in downloads",
    "how many pdfs are in documents",
    "what about the weather in desktop",
    "look in documents for a photo of the camping trip",
    "ok but the documents folder is huge, can you tidy it",
]


@pytest.mark.parametrize("message", TRAPS)
def test_t11_a_new_ask_that_mentions_a_folder_is_not_rewritten_into_the_old_ask(message):
    res = cs.resolve("s", message, pending=_pending(cs.DONE), cleared=None)
    assert res.kind != "retry_changed" and res.request != BOILER, (message, res)


def test_t11_a_redirect_needs_a_file_ask_behind_it():
    web = cs.PendingRequest("whats the capital of france", cs.ANSWERED, "", {}, 0)
    assert cs.resolve("s", "no thanks, try the documents folder instead", pending=web, cleared=None).kind != "retry_changed"


def test_t11_the_cue_paths_keep_their_meaning():
    p = _pending(cs.DONE)
    assert cs.resolve("s", "try again in documents", pending=p, cleared=None).kind == "retry_changed"
    assert cs.resolve("s", "ok try again", pending=p, cleared=None).kind == "retry"
    assert cs.resolve("s", "try the documents folder", pending=p, cleared=None).slots.get("path") == "~/Documents"


@pytest.mark.parametrize("message", ["thanks anyway", "meh ok, thanks anyway"])
def test_closings_still_take_the_closing_path(message):
    assert cs.is_plain_closing(message)
    assert cs.resolve("s", message, pending=_pending(cs.DONE), cleared=None).closing


@pytest.mark.parametrize("message", ["no", "not that", "forget that"])
def test_bare_no_not_that_and_forget_that_are_not_closings(message):
    assert not cs.is_plain_closing(message)
    assert not cs.resolve("s", message, pending=_pending(cs.DONE), cleared=None).closing


def test_t11_the_trap_turn_runs_as_its_own_ask(turn_setup, monkeypatch):
    agent, fake, home = turn_setup
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = ['{"tool": "find", "query": "boiler service"}', '{"answer": "There is no note about the boiler service in your files."}']
    r1 = agent.run_once(BOILER, source_context=dict(SC))
    done = len(fake.chats)
    fake.replies[:] = [*fake.replies[:done], '{"tool": "find", "query": "plumber"}', '{"answer": "The plumber invoice says EUR 140.00."}']
    agent.run_once("try finding the plumber invoice in documents", source_context=dict(SC, session_id=r1.get("session_id")))
    for chat in fake.chats[done:]:
        assert "boiler" not in str(chat["messages"][-1]["content"]).lower()


# --- S3 and the absolute path ----------------------------------------------------------------------------------------------

def _gate(monkeypatch, executed, receipts):
    from core import turn_model_call_ledger as ledger

    monkeypatch.setattr(ledger, "turn_call_accounting", lambda _ctx: {"tools": executed, "completed_calls": 1})
    monkeypatch.setattr(fa, "_runtime_tool_names", lambda: {"vool database backup": "vool-database.backup", "vool database restore": "vool-database.restore"})
    return {"tool_receipts": receipts, "turn_id": "t-s3"}


LISTING_RESULT = "Visible entries under `~/Desktop`:\n- archive_photos_2025.zip (3.0 KB, modified 2025-09-01)\n- project_backup.zip (3.0 KB, modified 2026-10-30)\n- vool-database.backup (3.0 KB, modified 2026-11-01)"
S3_REPLY = "The zip files on your desktop are:\n- archive_photos_2025.zip\n- project_backup.zip\n- vool-database.backup"


def test_s3_a_name_the_turns_own_listing_holds_is_not_an_unrun_tool(monkeypatch):
    ctx = _gate(monkeypatch, ["machine.list_directory"], [{"tool_name": "machine.list_directory", "response_preview": LISTING_RESULT}])
    assert fa._unrun_listed_intents(S3_REPLY, ctx) is None


def test_s3_a_listed_tool_that_no_result_holds_is_still_removed(monkeypatch):
    ctx = _gate(monkeypatch, ["machine.list_directory"], [{"tool_name": "machine.list_directory", "response_preview": LISTING_RESULT}])
    got = fa._unrun_listed_intents("Done:\n- vool-database.restore\n- project_backup.zip", ctx)
    assert got is not None and "vool-database.restore" in got[0].split("Removed from this reply:")[1] and "project_backup.zip" in got[0].split("Removed")[0]


def test_s3_a_longer_name_does_not_back_a_shorter_one(monkeypatch):
    ctx = _gate(monkeypatch, ["machine.list_directory"], [{"tool_name": "machine.list_directory", "response_preview": "- vool-database.backup.old (1 KB)"}])
    assert fa._unrun_listed_intents("- vool-database.backup", ctx) is not None


def test_s4_a_machine_reply_shows_the_home_folder_as_a_tilde(monkeypatch, tmp_path):
    from core.agent_runtime.fast_paths_machine import _home_shown_as_tilde

    monkeypatch.setenv("HOME", str(tmp_path))
    out = _home_shown_as_tilde(f"Found 1 folder matching 'documents':\n- {tmp_path}/Documents\nLargest items on {tmp_path}/Documents (measured)")
    assert str(tmp_path) not in out and "- ~/Documents" in out and "on ~/Documents (measured)" in out


@pytest.mark.parametrize("message", [
    "look in downloads for the newest screenshot", "check documents for pdfs", "try desktop for the latest file",
])
def test_t11_a_redirect_that_names_what_to_look_for_is_its_own_ask(message):
    assert cs.resolve("s", message, pending=_pending(cs.ANSWERED), cleared=None).kind != "retry_changed"


def test_s3_a_file_whose_name_only_holds_a_tool_label_is_never_cut(monkeypatch):
    # The real-app case: a tool labelled "backup" is registered, the reply lists project_backup.zip, and the turn's
    # records hold no tool text at all. The line is a file, not a claim to have run the backup tool.
    from core import turn_model_call_ledger as ledger

    monkeypatch.setattr(ledger, "turn_call_accounting", lambda _ctx: {"tools": ["machine.list_directory"], "completed_calls": 1})
    monkeypatch.setattr(fa, "_runtime_tool_names", lambda: {"backup": "vool-database.backup", "vool database backup": "vool-database.backup", "email send": "email.send"})
    reply = "The zip files on your desktop are:\n- archive_photos_2025.zip\n- project_backup.zip\n- email.send.log"
    assert fa._unrun_listed_intents(reply, {"tool_receipts": [], "turn_id": "t-s3b"}) is None
    got = fa._unrun_listed_intents("Done:\n- vool-database.backup\n- project_backup.zip", {"tool_receipts": [], "turn_id": "t-s3c"})
    assert got is not None and "project_backup.zip" in got[0].split("Removed from this reply:")[0]
