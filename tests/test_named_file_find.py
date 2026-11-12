"""A file asked for by the words in its name is found on the disk, not refused (audit 2026-10-29).

Measured on the phone-branch build: "pull up the plumber invoice for me please" was answered "I
can't access files" on the computer and with a plain promise from the paired phone (F42);
"which of my videos is the biggest, the trailer or the school play?" came back as "Could you tell
me where your videos are stored?" about a disk the runtime may read (F27); "is that invoice from
this month?" right after the invoice was named was refused as needing current information while
the changed date sat on the file (F46); and "what's the biggest file in my downloads?" told the
owner to review every row before deleting when nothing had been asked about deleting (F26).

Every case below replays the ask in-process, with no model and no daemon, against a fake home
whose file sizes and changed dates the test itself sets (so "this month" is computed, never
hardcoded), on the computer surface and on the paired-phone surface alike.
"""
from __future__ import annotations

import datetime
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import request_trust as rt

KB = 1024
# The paired-phone surface: an HTTP turn from a sealed phone with computer-tool rights.
_PHONE_TURN = {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False, rt.REMOTE_DEVICE_KEY: "phone:abc"}
_PC_TURN: dict = {}


def _month_name(moment: datetime.datetime) -> str:
    return moment.strftime("%B")


def _at(moment: datetime.datetime, *, day: int, hour: int, minute: int) -> datetime.datetime:
    return moment.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)


@pytest.fixture
def dates():
    """The moments this run stamps its files with, computed once so a month change mid-test cannot
    split the fixture from its assertions."""
    now = datetime.datetime.now()
    end_of_last_month = now.replace(day=1) - datetime.timedelta(days=1)
    return SimpleNamespace(
        now=now,
        # The plumber invoice was changed earlier this month; its date answer is a Yes.
        invoice_changed=_at(now, day=4, hour=10, minute=30),
        # invoice_4471.pdf and the bank csv were changed LAST month, so only the csv's own text
        # (a date written inside it, this month) can turn its answer into a Yes.
        last_month_changed=_at(end_of_last_month, day=15, hour=9, minute=15),
        in_file_date=_at(now, day=2, hour=12, minute=0),
        this_month=_month_name(now),
        last_month=_month_name(end_of_last_month),
    )


@pytest.fixture
def home(tmp_path, monkeypatch, dates):
    # A resolved root: the find lane walks resolved folders, and the reply labels a match
    # relative to the home it expanded, so the two must be the very same string.
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Downloads", "Documents", "Pictures", "Movies", "Music"):
        (root / name).mkdir(parents=True)

    def put(relative: str, data: bytes, when: datetime.datetime | None = None) -> Path:
        placed = root / relative
        placed.write_bytes(data)
        if when is not None:
            os.utime(placed, (when.timestamp(), when.timestamp()))
        return placed

    put("Downloads/movie_night_trailer.mp4", b"x" * (885 * KB))
    put("Downloads/setup_helper.dmg", b"x" * (293 * KB))
    put("Downloads/dog_park.HEIC", b"x" * (8 * KB))
    put("Downloads/wallpaper_mountains.png", b"x" * (6 * KB))
    # Dummy bytes on purpose: the PDF reader cannot read these, so presenting one names the file
    # instead of quoting it, and its date answer has to come from the changed date.
    put("Downloads/invoice_4471.pdf", b"%" * (30 * KB), dates.last_month_changed)
    put("Downloads/plumber_invoice.pdf", b"%" * (28 * KB), dates.invoice_changed)
    put(
        "Downloads/bank_export.csv",
        f"date,amount\n{dates.in_file_date.date().isoformat()},-42.10\n".encode(),
        dates.last_month_changed,
    )
    put("Downloads/old_meme.gif", b"x" * 512)
    put("Downloads/screenshot_1.png", b"x" * (2 * KB))
    put("Movies/school_play.mp4", b"x" * 1_153_433)  # 1.1 MB, bigger than the trailer
    put("Movies/holiday_clip.mov", b"x" * (4 * KB))
    put("Desktop/todo.txt", b"buy milk\ncall plumber\n")
    put("Desktop/notes.md", b"# notes\nnothing to see\n")
    put("Desktop/screenshot_a.png", b"x" * KB)
    put("Desktop/screenshot_b.png", b"x" * KB)
    put("Documents/taxes_2025.pdf", b"%" * (2 * KB))
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    import core.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_paths, "active_data_dir", lambda: tmp_path / "data")
    return root


class _Store:
    def __init__(self, *devices):
        self.records = {d["device_id"]: d for d in devices}
        self.receipts = []

    def device(self, device_id):
        record = self.records.get(device_id)
        return dict(record) if record else None

    def devices(self):
        return dict(self.records)

    def append_receipt(self, kind, **kw):
        self.receipts.append(kind)

    def mark_device(self, *a, **k):
        return None


def _phone(device_id="phone:abc", **extra):
    return {"device_id": device_id, "name": "Pixel", "status": "active", "kind": "phone", "sealed_protocol": True,
            "rules": {"computer_tools": True, "chat": True}, "right_expiry": {"computer_tools": time.time() + 3600},
            **extra}


@pytest.fixture
def phones(monkeypatch):
    store = _Store(_phone())
    import core.mobile_companion.listener as listener
    import core.web.api.mobile_companion_api as api

    monkeypatch.setattr(api, "mobile_companion_service", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(listener, "load_settings", lambda: {"enabled": True})
    return store


@pytest.fixture(autouse=True)
def _no_leftover_file_memory():
    from core.agent_runtime.fast_paths_machine import reset_machine_followup_state

    reset_machine_followup_state()
    yield
    reset_machine_followup_state()


@pytest.fixture
def machine_tools(home, monkeypatch):
    import core.agent_runtime.fast_paths_machine as fpm
    import core.authorized_tool_execution as ate
    import core.runtime_execution_tools as ret

    calls = []

    def run(intent, args, source_context=None, **_k):
        calls.append((intent, dict(args)))
        if intent == "machine.list_directory":
            return ret._list_machine_directory(args, workspace_root=home)
        if intent == "machine.find_file":
            return ret._machine_find_file(args)
        if intent == "machine.find_folder":
            return ret._machine_find_folder(args)
        if intent == "machine.read_file":
            return ret._read_machine_file(args)
        if intent == "machine.find_largest":
            return ret._machine_find_largest(args)
        if intent == "pdf.extract_text":
            return ret._pdf_extract_text(args)
        raise AssertionError(intent)

    # The handlers look the executor up inside each function AND hold a top-level import of it.
    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", run)
    monkeypatch.setattr(fpm, "execute_authorized_runtime_tool", run, raising=False)
    return calls


def _fake_agent():
    return SimpleNamespace(
        _emit_runtime_event=lambda *a, **k: None,
        _fast_path_result=lambda **kw: {"response": kw["response"], "reason": kw["reason"]},
    )


def _ask(text, *, session_id, source_context):
    from core.agent_runtime.fast_paths_machine import maybe_handle_direct_machine_read_request

    return maybe_handle_direct_machine_read_request(
        _fake_agent(), text, session_id=session_id, source_surface="api", source_context=source_context,
    )


# ---- F42: "pull up the plumber invoice for me please" finds and presents the file ----


def test_the_plumber_invoice_ask_names_the_file_its_size_and_its_date(machine_tools, dates):
    reply = _ask(
        "pull up the plumber invoice for me please, i want to look at it",
        session_id="s-f42-pc", source_context=_PC_TURN,
    )
    assert reply is not None
    text = reply["response"]
    assert "~/Downloads/plumber_invoice.pdf" in text
    assert "28 KB" in text
    assert dates.this_month in text  # the changed date the test stamped
    assert "can't access" not in text and "cannot access" not in text


def test_the_same_ask_from_the_paired_phone_gets_the_same_answer(machine_tools, phones, dates):
    computer = _ask(
        "pull up the plumber invoice for me please, i want to look at it",
        session_id="s-f42-pc-2", source_context=_PC_TURN,
    )
    phone = _ask(
        "pull up the plumber invoice for me please, i want to look at it",
        session_id="s-f42-phone", source_context=dict(_PHONE_TURN),
    )
    assert computer is not None and phone is not None
    assert "~/Downloads/plumber_invoice.pdf" in phone["response"]
    assert "28 KB" in phone["response"]
    assert computer["response"] == phone["response"]  # parity: same words, same disk, same answer


def test_showing_a_text_file_quotes_its_first_lines(machine_tools, dates):
    reply = _ask("show me the bank csv", session_id="s-csv", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "~/Downloads/bank_export.csv" in text
    assert "date,amount" in text  # the file's own first line, not a description of it
    assert dates.in_file_date.date().isoformat() in text


def test_two_files_with_the_same_words_are_both_listed(machine_tools, dates, home):
    (home / "Documents" / "plumber_invoice.pdf").write_bytes(b"%" * (28 * KB))
    reply = _ask("open the plumber invoice", session_id="s-two", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "I found 2" in text
    assert text.count("plumber_invoice.pdf") == 2
    assert "~/Downloads/plumber_invoice.pdf" in text and "~/Documents/plumber_invoice.pdf" in text


def test_no_file_with_those_words_is_a_plain_miss_that_names_the_folders(machine_tools, home):
    (home / "Downloads" / "plumber_invoice.pdf").unlink()
    reply = _ask("open the plumber invoice", session_id="s-miss", source_context=_PC_TURN)
    assert reply is not None  # a miss is an answer, not a crash or a fall-through to a guess
    text = reply["response"]
    assert "couldn't find" in text
    assert "plumber" in text
    assert "Downloads" in text and "Movies" in text  # it says where it looked


# ---- F27: "which of my videos is the biggest, the trailer or the school play?" ----


def test_the_bigger_of_two_named_videos_is_answered_with_both_sizes(machine_tools, phones):
    ask = "which of my videos is the biggest, the trailer or the school play?"
    computer = _ask(ask, session_id="s-f27-pc", source_context=_PC_TURN)
    phone = _ask(ask, session_id="s-f27-phone", source_context=dict(_PHONE_TURN))
    for reply in (computer, phone):
        assert reply is not None
        text = reply["response"]
        assert "The bigger one is school_play.mp4" in text
        assert "movie_night_trailer.mp4" in text
        assert "1.1 MB" in text and "885 KB" in text  # both sizes, measured
        assert "where your videos" not in text and "Could you tell me" not in text
    assert computer["response"] == phone["response"]


def test_a_named_video_that_is_not_there_is_said_plainly(machine_tools):
    reply = _ask(
        "which of my videos is the biggest, the trailer or the birthday?",
        session_id="s-f27-miss", source_context=_PC_TURN,
    )
    assert reply is not None
    text = reply["response"]
    assert "birthday" in text and "trailer" in text
    assert "where your videos" not in text


def test_the_biggest_of_all_videos_is_named_without_asking_where_they_live(machine_tools):
    reply = _ask("whats the biggest of my videos", session_id="s-f27-all", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "school_play.mp4" in text and "1.1 MB" in text
    assert "where your videos" not in text


# ---- F46: "is that invoice from this month?" answered from the file that was just named ----


def test_a_date_question_about_the_shown_invoice_answers_from_the_file(machine_tools, dates):
    shown = _ask("pull up the plumber invoice", session_id="s-f46a", source_context=_PC_TURN)
    assert shown is not None
    reply = _ask("is that invoice from this month?", session_id="s-f46a", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "plumber_invoice.pdf" in text
    assert "Yes" in text or dates.this_month in text
    assert "changed" in text or "date in" in text  # it says which date it used


def test_a_file_with_no_date_inside_uses_its_changed_date(machine_tools, dates):
    shown = _ask("show me the invoice 4471", session_id="s-f46b", source_context=_PC_TURN)
    assert shown is not None
    reply = _ask("is that invoice from this month?", session_id="s-f46b", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "invoice_4471.pdf" in text
    assert "No" in text or dates.last_month in text  # changed last month, so not this month


def test_a_date_written_in_the_file_beats_its_changed_date(machine_tools, dates):
    shown = _ask("pull up the bank csv", session_id="s-f46c", source_context=_PC_TURN)
    assert shown is not None
    reply = _ask("is that csv from this month?", session_id="s-f46c", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "bank_export.csv" in text
    # The csv was changed LAST month; only the date written inside it is this month, so the
    # month named in the answer proves which of the two the answer came from.
    assert f"2 {dates.this_month}" in text
    assert dates.last_month not in text
    assert "written in the file" in text or "in the file" in text


def test_a_date_question_with_no_file_shown_is_not_answered_by_this_lane(machine_tools):
    assert _ask("is that invoice from this month?", session_id="s-f46-none", source_context=_PC_TURN) is None


def test_a_when_did_i_get_it_ask_answers_with_a_calendar_date(machine_tools, dates):
    shown = _ask("pull up the plumber invoice", session_id="s-f46d", source_context=_PC_TURN)
    assert shown is not None
    reply = _ask("when did i get the invoice one?", session_id="s-f46d", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "plumber_invoice.pdf" in text
    assert dates.this_month in text or dates.invoice_changed.date().isoformat() in text


# ---- F26: a size question is a measurement; delete notes belong to cleanup asks ----


def test_a_plain_biggest_file_ask_keeps_the_delete_notes_out(machine_tools):
    reply = _ask("whats the biggest file in my downloads?", session_id="s-f26-plain", source_context=_PC_TURN)
    assert reply is not None
    text = reply["response"]
    assert "movie_night_trailer.mp4" in text  # it really measured Downloads
    assert "review before deleting" not in text
    assert "won't delete" not in text


def test_a_storage_cleanup_ask_keeps_the_delete_safety_notes(machine_tools):
    reply = _ask(
        "what's eating all my storage? show me the biggest files i have",
        session_id="s-f26-cleanup", source_context=_PC_TURN,
    )
    assert reply is not None
    assert "review before deleting" in reply["response"]


# ---- The intent layer: what each plain ask is understood to mean ----


@pytest.mark.parametrize(("text", "expected"), [
    ("pull up the plumber invoice for me please, i want to look at it", ("", ("plumber", "invoice"), "")),
    ("show me the bank csv", ("csv", ("bank",), "")),
    ("open the trailer video", ("video", ("trailer",), "")),
    ("find my taxes pdf in documents", ("pdf", ("taxes",), "Documents")),
    ("bring up the plumber invoice in my downloads", ("", ("plumber", "invoice"), "Downloads")),
])
def test_a_present_file_ask_parses(text, expected):
    from core.execution.constants import machine_present_file_intent

    ask = machine_present_file_intent(text)
    assert ask is not None
    assert (str(ask.kind), tuple(ask.words), str(ask.place)) == expected


@pytest.mark.parametrize("text", [
    "pull up my meeting notes", "show me the booking confirmation pdf", "open the dentist appointment invoice",
])
def test_a_document_about_an_event_is_still_a_file(text):
    from core.execution.constants import machine_present_file_intent

    assert machine_present_file_intent(text) is not None


@pytest.mark.parametrize("text", [
    "read me my to do list", "find my phone", "copy notes.txt to desktop",
    # The storage scan's asks (test_operator_actions: 'find disk bloat in "<dir>"').
    'find disk bloat in "/tmp/somewhere"', "find the junk on my drive", "show me what's using space",
    # Calendar and list items keep their own lanes (test_calendar_reads_person_path: the event row).
    "show me the event called School play", "pull up my dentist appointment", "open the meeting called standup",
    "show me the reminder about the bins",
])
def test_not_a_present_file_ask(text):
    from core.execution.constants import machine_present_file_intent

    assert machine_present_file_intent(text) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("which of my videos is the biggest, the trailer or the school play?",
     ("video", (("trailer",), ("school", "play")), "")),
    ("which is bigger, the trailer or the school play?", ("", (("trailer",), ("school", "play")), "")),
    ("whats the biggest of my videos", ("video", (), "")),
])
def test_a_named_compare_ask_parses(text, expected):
    from core.execution.constants import machine_named_compare_intent

    ask = machine_named_compare_intent(text)
    assert ask is not None
    assert (str(ask.kind), tuple(tuple(name) for name in ask.names), str(ask.place)) == expected


@pytest.mark.parametrize("text", [
    "which of my photos is the nicest", "who is the biggest pop star",
])
def test_not_a_named_compare_ask(text):
    from core.execution.constants import machine_named_compare_intent

    assert machine_named_compare_intent(text) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("is that invoice from this month?", (("invoice",), "this month")),
    ("is the bank csv from this week?", (("bank",), "this week")),  # "csv" is the kind word, as in the present parse
    ("when did i get the invoice?", (("invoice",), "")),
    ("when did i download the invoice 4471?", (("invoice", "4471"), "")),
    ("what date was that?", ((), "")),
    ("how old is that?", ((), "")),
])
def test_a_file_date_question_parses(text, expected):
    from core.execution.constants import machine_file_date_question

    question = machine_file_date_question(text)
    assert question is not None
    assert (tuple(question.words), str(question.period)) == expected


@pytest.mark.parametrize("text", [
    "is that invoice paid", "when is my dentist appointment",
])
def test_not_a_file_date_question(text):
    from core.execution.constants import machine_file_date_question

    assert machine_file_date_question(text) is None
