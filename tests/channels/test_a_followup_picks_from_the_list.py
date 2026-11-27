"""A follow-up that points into the list VOOL just gave sends (or shows) those files.

sls's phone and Telegram, 2026-11-27, on installed ab3fcc19e: after "Pictures, newest first, under ~/Downloads"
the follow-ups "fetch me the top one from the list", "download this for me" and "let's view this picture" went to
the model with no file tool. It named the 13th file as the top one, then said no step ran. "ok so fetch me latest
10" after a count failed the same way, and "fetch me the 10 latest pictures from Downloads" was searched as a file
named "10 latest". The wordings below are new ones, not the ones from that chat.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.execution.constants import listed_item_pick, machine_send_to_phone_intent


@pytest.mark.parametrize(("said", "verb", "positions", "count", "this"), [
    ("could you send me the first one", "send", (1,), 0, False),
    ("grab the 3rd photo for me", "send", (3,), 0, False),
    ("pls give me no. 2", "send", (2,), 0, False),
    ("get me the five newest", "send", (), 5, False),
    ("and download a couple of the latest", "send", (), 2, False),
    ("forward them all here", "send", (), 10, False),
    ("I meant that pic, get it", "send", (), 0, True),
    ("open the second picture on the list", "view", (2,), 0, False),
    ("let me see that one", "view", (), 0, True),
])
def test_a_pick_from_the_list(said, verb, positions, count, this):
    assert listed_item_pick(said) == (verb, positions, count, this)


@pytest.mark.parametrize("said", [
    "send it to anna", "share that with my brother", "delete the first one", "rename the second file",
    "email them to me", "send me the newest picture in documents", "what's the top one?", "how are you today",
    "show me pictures from the last 10 days",
])
def test_not_a_pick(said):
    assert listed_item_pick(said) is None


@pytest.mark.parametrize(("said", "count", "kind", "place"), [
    ("send me the 4 newest photos from my downloads", 4, "image", "Downloads"),
    ("get me latest three pdfs in documents", 3, "pdf", "Documents"),
    ("fetch me the twenty newest pictures from my desktop", 10, "image", "Desktop"),  # at most ten at once
])
def test_a_count_of_a_kind_is_that_many_not_a_file_name(said, count, kind, place):
    asked = machine_send_to_phone_intent(said, phone_turn=True)
    assert asked is not None and asked.search is None, asked
    assert (asked.listing.limit, asked.listing.kind, asked.listing.place) == (count, kind, place)


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents", "Pictures"):
        (root / name).mkdir(parents=True)
    now = time.time()
    for age, name in enumerate(["beach.png", "dog.jpg", "receipt.png", "sunset.jpg", "map.png"]):
        path = root / "Downloads" / name
        path.write_bytes(b"x" * 2048)
        os.utime(path, (now - age * 3600, now - age * 3600))
    (root / "Downloads" / "notes.txt").write_text("not a picture")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    sent: list[str] = []
    import core.authorized_tool_execution as ate
    from core.runtime_execution_tools import RuntimeExecutionResult, _list_machine_directory

    def run(intent, args, source_context=None):
        if intent == "machine.send_to_phone":
            if "map" in args["path"]:
                return RuntimeExecutionResult(handled=True, ok=False, status="not_allowed", response_text="outside the folders")
            sent.append(args["path"])
            name = os.path.basename(args["path"])
            return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text=f"Here it is: {name}.",
                                          details={"sent": {"name": name}})
        return _list_machine_directory(args, workspace_root=root)

    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", run)
    return root, sent


def _after(monkeypatch, *replies):
    import core.agent_runtime.fast_paths_machine as fpm

    rows = [{"role": "assistant", "content": reply} for reply in replies]
    monkeypatch.setattr(fpm, "canonical_runtime_transcript", lambda **_: (rows, "test"))


LISTING = "Pictures, newest first, under `~/Downloads`:\n- beach.png\n- dog.jpg\n- receipt.png\n- sunset.jpg\n- map.png"


def _followup(said):
    from core.agent_runtime.fast_paths_machine import _listed_item_followup

    return _listed_item_followup(said, session_id="s", source_context={})


def test_the_top_one_is_the_first_row_not_a_model_guess(home, monkeypatch):
    _, sent = home
    _after(monkeypatch, LISTING)
    result = _followup("ok send me the top one from that list")
    assert result.ok and sent == ["~/Downloads/beach.png"], (result.response_text, sent)


def test_this_is_the_file_the_last_reply_named(home, monkeypatch):
    _, sent = home
    _after(monkeypatch, LISTING, "The one you want is probably receipt.png.")
    assert _followup("just download this for me").ok
    assert sent == ["~/Downloads/receipt.png"]


def test_this_with_several_listed_and_none_named_asks_which(home, monkeypatch):
    _, sent = home
    _after(monkeypatch, LISTING)
    result = _followup("get it")
    assert result.status == "needs_choice" and "2. dog.jpg" in result.response_text and sent == []


def test_several_are_sent_and_one_refused_is_said(home, monkeypatch):
    _, sent = home
    _after(monkeypatch, LISTING)
    result = _followup("forward all of them")
    assert sent == ["~/Downloads/beach.png", "~/Downloads/dog.jpg", "~/Downloads/receipt.png", "~/Downloads/sunset.jpg"]
    assert "Sending 4 files" in result.response_text and "map.png: outside the folders" in result.response_text


def test_a_count_after_a_count_lists_that_kind_again(home, monkeypatch):
    _, sent = home
    _after(monkeypatch, "`~/Downloads` holds 5 pictures (not counting subfolders).")
    result = _followup("cool, bring me the 3 newest")
    assert result.ok and sent == ["~/Downloads/beach.png", "~/Downloads/dog.jpg", "~/Downloads/receipt.png"], result.response_text


def test_a_number_past_the_list_is_said_not_guessed(home, monkeypatch):
    _, sent = home
    _after(monkeypatch, LISTING)
    result = _followup("send me number 9")
    assert "no number 9" in result.response_text and sent == []


def test_view_on_the_computer_names_the_file_and_claims_nothing(home, monkeypatch):
    from core.agent_runtime.fast_paths_machine import OPEN_ON_COMPUTER_NOTE

    _, sent = home
    _after(monkeypatch, LISTING)
    result = _followup("open the second one")
    assert "~/Downloads/dog.jpg" in result.response_text and result.response_text.endswith(OPEN_ON_COMPUTER_NOTE)
    assert sent == []


def test_no_listing_just_before_means_no_guess(home, monkeypatch):
    _, sent = home
    _after(monkeypatch, "Sure, here is a poem about the sea.")
    assert _followup("send me the first one") is None and sent == []
