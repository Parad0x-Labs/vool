"""The to-do list VOOL keeps (core/pa_tasks.py + core/todo_list_turn.py), driven as a person drives it.

Every chat case runs through ``VoolAgent.run_once`` on the model-less test backend (the daemon's own
turn function; nothing on the path is mocked), in an isolated VOOL home and SQLite, with ``~``
pointed at a separate fake home so the to-do FILE lane reads a real Desktop/todo.txt.

What is pinned:
* the store's typed transitions (ported from the PA lane's tests, 2026-09-09);
* add / tick off / take off / show from chat, with the store as the only truth;
* which list a to-do ask means: the FILE when the ask names a file or a place, the VOOL list when it
  names VOOL, and for a plain ask the VOOL list once it has ever been used, the FILE before that;
* the failure paths: an ambiguous or unknown item changes nothing, a private chat saves nothing new,
  an agent's brief and an unknown remote stamp never reach the list.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._person_turn_rig import make_agent, person_env, reply, turn

LANE = "vool_todo_list"
FILE_LANE = "todo_list_read_fast_path"


@pytest.fixture()
def someone(person_env, monkeypatch, tmp_path_factory):
    """A person's home with Desktop/Documents/Downloads, apart from VOOL's own home and database
    (the rig keeps its database under tmp_path, which the file tools rightly refuse to read)."""
    home = tmp_path_factory.mktemp("someone")
    for room in ("Desktop", "Documents", "Downloads"):
        (home / room).mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert Path("~").expanduser() == home
    return home


@pytest.fixture()
def desk_file(someone: Path) -> Path:
    (someone / "Desktop" / "todo.txt").write_text("water the plants\nreturn the library book\n", encoding="utf-8")
    return someone


def _open_titles() -> list[str]:
    from core import pa_tasks

    return [t["title"] for t in pa_tasks.list_tasks()]


def _route(result: dict) -> str:
    return str(result.get("route_reason") or "")


# ----------------------------------------------------------------------------------------------
# the store
# ----------------------------------------------------------------------------------------------


class TestTheStore:
    def test_lifecycle_and_typed_transitions(self, someone: Path) -> None:
        from core import pa_tasks

        task = pa_tasks.create_task(title="Pay the rent", notes="by the 5th")
        assert task["status"] == "open"
        assert pa_tasks.list_tasks()[0]["task_id"] == task["task_id"]
        done = pa_tasks.complete_task(task["task_id"])
        assert done["ok"] and done["status"] == "done"
        again = pa_tasks.complete_task(task["task_id"])  # an honest no-op, not a second completion
        assert again["ok"] and again["status"] == "already_done"
        cancelled = pa_tasks.cancel_task(task["task_id"])  # a done task keeps its history
        assert not cancelled["ok"] and cancelled["status"] == "already_done"

    def test_cancel_then_complete_is_refused(self, someone: Path) -> None:
        from core import pa_tasks

        task = pa_tasks.create_task(title="abandon me")
        assert pa_tasks.cancel_task(task["task_id"])["ok"]
        result = pa_tasks.complete_task(task["task_id"])
        assert not result["ok"] and result["status"] == "cancelled"

    def test_a_title_is_required(self, someone: Path) -> None:
        from core import pa_tasks

        with pytest.raises(ValueError):
            pa_tasks.create_task(title="   ")

    def test_remove_deletes_the_row(self, someone: Path) -> None:
        from core import pa_tasks

        task = pa_tasks.create_task(title="call the bank")
        assert pa_tasks.remove_task(task["task_id"])["status"] == "removed"
        assert pa_tasks.get_task(task["task_id"]) is None
        assert pa_tasks.remove_task(task["task_id"])["status"] == "not_found"

    def test_the_list_keeps_the_order_items_were_added(self, someone: Path) -> None:
        from core import pa_tasks

        stamps = iter(["2026-11-12T10:00:00+00:00", "2026-11-12T10:00:01+00:00", "2026-11-12T10:00:02+00:00"])
        for title in ("first", "second", "third"):
            pa_tasks.create_task(title=title, now_fn=lambda: next(stamps))
        assert _open_titles() == ["first", "second", "third"]

    def test_matching_never_picks_one_of_several(self, someone: Path) -> None:
        from core import pa_tasks

        for title in ("buy milk", "buy oat milk", "call the plumber"):
            pa_tasks.create_task(title=title)
        assert [t["title"] for t in pa_tasks.find_open_tasks("buy milk")] == ["buy milk"]  # exact wins
        assert sorted(t["title"] for t in pa_tasks.find_open_tasks("milk")) == ["buy milk", "buy oat milk"]
        assert [t["title"] for t in pa_tasks.find_open_tasks("the plumber")] == ["call the plumber"]
        assert pa_tasks.find_open_tasks("dentist") == []


# ----------------------------------------------------------------------------------------------
# a whole journey from chat
# ----------------------------------------------------------------------------------------------


class TestFromChat:
    def test_add_show_tick_off_take_off(self, someone: Path) -> None:
        agent = make_agent()
        added = turn(agent, "add buy milk, call the plumber and book the dentist to my to-do list")
        assert _route(added) == LANE
        assert _open_titles() == ["buy milk", "call the plumber", "book the dentist"]
        assert "3 things" in reply(added)

        shown = turn(agent, "what's on my to do list?")
        assert _route(shown) == LANE
        assert reply(shown).startswith("Your to-do list:\n1. buy milk\n2. call the plumber\n3. book the dentist")

        ticked = turn(agent, "tick off the plumber")
        assert reply(ticked).startswith("Ticked off “call the plumber”")
        assert _open_titles() == ["buy milk", "book the dentist"]

        # A number means the list this chat was last shown, not the list as it is now: item 3 is
        # still the dentist although the plumber is gone.
        by_number = turn(agent, "check off 3")
        assert "book the dentist" in reply(by_number)
        assert _open_titles() == ["buy milk"]

        removed = turn(agent, "remove buy milk from my to-do list")
        assert _route(removed) == LANE and _open_titles() == []
        assert "Nothing left" in reply(removed)

        history = turn(agent, "what have I ticked off on my to-do list?")
        assert "Your to-do list is empty." in reply(history)
        assert "- book the dentist" in reply(history) and "- call the plumber" in reply(history)
        assert "buy milk" not in reply(history)  # taken off, not done

    def test_it_means_the_item_just_added(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add renew the passport to my todo list")
        turn(agent, "add pay the gas bill to my todo list")
        result = turn(agent, "cross it off")
        assert reply(result).startswith("Ticked off “pay the gas bill”")
        assert _open_titles() == ["renew the passport"]

    def test_the_same_item_is_not_added_twice(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        again = turn(agent, "put buy milk on my to-do list")
        assert "already on it" in reply(again)
        assert _open_titles() == ["buy milk"]

    def test_the_list_survives_a_new_agent(self, someone: Path) -> None:
        turn(make_agent(), "add water the tomatoes to my to-do list")
        shown = turn(make_agent(), "show me my to-do list", session="another-chat")
        assert "1. water the tomatoes" in reply(shown)


# ----------------------------------------------------------------------------------------------
# which list: the FILE or the list VOOL keeps
# ----------------------------------------------------------------------------------------------


class TestWhichList:
    def test_before_the_vool_list_is_used_a_plain_ask_reads_the_file(self, desk_file: Path) -> None:
        result = turn(make_agent(), "what's on my to do list?")
        assert _route(result) == FILE_LANE
        assert reply(result).startswith("Your to-do list (Desktop/todo.txt):")
        assert "water the plants" in reply(result)

    def test_once_used_a_plain_ask_shows_the_vool_list_and_names_the_file_once(self, desk_file: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        first = turn(agent, "whats on my to do list again")
        assert _route(first) == LANE
        assert "1. buy milk" in reply(first)
        assert "water the plants" not in reply(first)
        assert "You also have a to-do file, Desktop/todo.txt" in reply(first)
        second = turn(agent, "read me my to do list")
        assert "1. buy milk" in reply(second) and "Desktop/todo.txt" not in reply(second)

    @pytest.mark.parametrize(
        "text",
        [
            "read my to-do file",
            "what was on that todo list on my desktop again?",
            "open the to do list in my documents",
            "show me todo.txt",
        ],
    )
    def test_an_ask_that_names_a_file_or_place_reads_the_file(self, desk_file: Path, text: str) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        result = turn(agent, text)
        assert _route(result) != LANE
        assert "buy milk" not in reply(result)

    def test_an_ask_that_names_vool_gets_the_vool_list_even_when_empty(self, desk_file: Path) -> None:
        result = turn(make_agent(), "what's on my vool to-do list?")
        assert _route(result) == LANE
        assert reply(result) == "Your to-do list is empty."

    def test_a_change_that_names_a_file_is_not_this_lists(self, desk_file: Path) -> None:
        result = turn(make_agent(), "add buy milk to the todo list on my desktop")
        assert _route(result) != LANE
        assert _open_titles() == []
        assert (desk_file / "Desktop" / "todo.txt").read_text(encoding="utf-8").count("buy milk") == 0

    def test_no_file_and_no_vool_list_says_vool_can_keep_one(self, someone: Path) -> None:
        result = turn(make_agent(), "what's on my to do list?")
        assert _route(result) == FILE_LANE
        assert "I'll keep one for you in VOOL" in reply(result)


# ----------------------------------------------------------------------------------------------
# failure paths: nothing changes that the person did not clearly ask for
# ----------------------------------------------------------------------------------------------


class TestFailsClosed:
    def test_an_ambiguous_tick_off_changes_nothing(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk, buy oat milk to my to-do list")
        result = turn(agent, "tick off milk")
        assert "More than one item" in reply(result) and "Nothing changed" in reply(result)
        assert _open_titles() == ["buy milk", "buy oat milk"]
        picked = turn(agent, "tick off 2")
        assert "buy oat milk" in reply(picked)
        assert _open_titles() == ["buy milk"]

    def test_an_unknown_item_changes_nothing(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        result = turn(agent, "cross the dentist off my to-do list")
        assert _route(result) == LANE
        assert "couldn't find" in reply(result) and "nothing changed" in reply(result)
        assert _open_titles() == ["buy milk"]

    def test_done_words_with_no_list_and_no_item_are_not_this_lists(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        result = turn(agent, "mark the report as done")
        assert _route(result) != LANE
        assert _open_titles() == ["buy milk"]

    def test_a_number_with_no_list_shown_is_not_this_lists(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        result = turn(agent, "check off 1", session="a-chat-that-saw-no-list")
        assert _route(result) != LANE
        assert _open_titles() == ["buy milk"]

    def test_a_whole_list_change_is_refused_plainly(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        result = turn(agent, "clear my to-do list")
        assert _route(result) == LANE and "nothing changed" in reply(result)
        assert _open_titles() == ["buy milk"]

    def test_writing_a_new_list_is_not_this_lists(self, someone: Path) -> None:
        result = turn(make_agent(), "make me a to do list for the trip")
        assert _route(result) != LANE
        assert _open_titles() == []

    def test_a_private_chat_adds_nothing(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        on = turn(agent, "private chat", session="secret")
        assert "private" in reply(on).lower()
        result = turn(agent, "add see the lawyer to my to-do list", session="secret")
        assert "Private chat is on" in reply(result)
        assert _open_titles() == ["buy milk"]
        shown = turn(agent, "what's on my to-do list?", session="secret")  # reading saves nothing
        assert "1. buy milk" in reply(shown)

    def test_an_agents_brief_never_reaches_the_list(self, someone: Path) -> None:
        result = turn(make_agent(), "add wire the money to my to-do list", turn_author="agent")
        assert _route(result) != LANE
        assert _open_titles() == []

    def test_a_remote_stamp_with_no_paired_phone_keeps_its_lanes(self, someone: Path) -> None:
        from core.request_trust import OWNER_LOCAL_KEY, REMOTE_DEVICE_KEY

        result = turn(
            make_agent(), "add buy milk to my to-do list",
            **{OWNER_LOCAL_KEY: False, REMOTE_DEVICE_KEY: "dev-unknown"},
        )
        assert _route(result) != LANE
        assert _open_titles() == []


# ----------------------------------------------------------------------------------------------
# the phone keeps the same list
# ----------------------------------------------------------------------------------------------


def test_a_paired_phone_sees_and_changes_the_same_list(someone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.request_trust as request_trust

    device = {"status": "active", "kind": "phone", "sealed_protocol": True, "device_id": "dev-todo"}
    monkeypatch.setattr(request_trust, "remote_turn_device", lambda context: device)
    phone = {request_trust.OWNER_LOCAL_KEY: False, request_trust.REMOTE_DEVICE_KEY: "dev-todo"}
    agent = make_agent()
    turn(agent, "add buy milk to my to-do list")
    added = turn(agent, "add call mum to my to-do list", session="phone-chat", **phone)
    assert _route(added) == LANE
    shown = turn(agent, "what's on my to do list?", session="phone-chat", **phone)
    assert "1. buy milk\n2. call mum" in reply(shown)
    assert "to-do file" not in reply(shown)  # the phone is never told what files this disk holds


# ----------------------------------------------------------------------------------------------
# metadata, the to-do folder, and the question a whole erase asks (sls 2026-11-12)
# ----------------------------------------------------------------------------------------------


def _list_file() -> Path:
    from core import pa_tasks

    return pa_tasks.folder_path() / pa_tasks.LIST_FILE_NAME


class TestTheFolderAndMetadata:
    def test_each_item_keeps_when_where_and_why(self, someone: Path) -> None:
        from core import pa_tasks

        agent = make_agent()
        turn(agent, "add a new kettle to my to-do list because the old one leaks", session="kitchen-chat")
        task = pa_tasks.list_tasks()[0]
        assert task["title"] == "a new kettle"
        assert task["notes"] == "the old one leaks"
        assert task["added_in"]  # the chat's name (its first message when it has no name yet)
        text = _list_file().read_text(encoding="utf-8")
        assert "- [ ] a new kettle" in text and "why: the old one leaks" in text
        assert f"added {task['created_at'][:10]}" in text

    def test_the_folder_follows_every_change(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk, call the bank to my to-do list")
        turn(agent, "tick off buy milk")
        turn(agent, "remove call the bank from my to-do list")
        text = _list_file().read_text(encoding="utf-8")
        assert "- [x] buy milk" in text and "call the bank" not in text

    def test_the_folder_is_not_where_the_file_lane_looks(self, someone: Path) -> None:
        """VOOL's own copy must never come back as 'a to-do file' the person wrote."""
        from core import pa_tasks

        turn(make_agent(), "add buy milk to my to-do list")
        assert _list_file().exists()
        for room in ("Desktop", "Documents", "Downloads"):
            assert someone / room not in pa_tasks.folder_path().parents

    def test_where_is_my_to_do_list_names_the_folder(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        result = turn(agent, "where is my to-do list kept?")
        assert _route(result) == LANE
        assert str(_list_file()) in reply(result)


class TestAWholeErase:
    """The erase side: on 28fc78dd the only whole erase in code is the profile forget-all
    (core/operator_profile_turn._forget, category "*"), and in chat the memory lane answers
    "forget everything you know about me" before it. So the question is armed here the way that
    erase arms it (``erase_followup``), and the answers run through the real turn."""

    def _erase(self, session: str = "person-chat") -> str:
        from core.todo_list_turn import erase_followup

        return erase_followup(session)

    def test_the_profile_forget_all_reply_carries_the_question(self, someone: Path) -> None:
        from core import operator_profile as profile
        from core.operator_profile_interpretation import ProfileProposal
        from core.operator_profile_turn import _forget

        turn(make_agent(), "add buy milk to my to-do list")
        change = _forget(
            ProfileProposal("*", "", "explicit", action="forget", clause="forget everything you know about me"),
            principal=profile.OWNER_PRINCIPAL, session_id="person-chat",
        )
        assert "You also have 1 thing on your to-do list" in change.report
        assert _open_titles() == ["buy milk"]

    def test_delete_deletes_the_list_and_its_file(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk, call the bank to my to-do list")
        assert "You also have 2 things on your to-do list" in self._erase()
        assert _open_titles() == ["buy milk", "call the bank"]  # nothing touched until the answer
        unsure = turn(agent, "yes")
        assert "Nothing changed yet" in reply(unsure) and len(_open_titles()) == 2
        done = turn(agent, "delete it")
        assert "Deleted your to-do list (2 items)" in reply(done)
        from core import pa_tasks

        assert pa_tasks.list_tasks(include_done=True) == []
        assert not _list_file().exists()

    def test_keep_keeps_the_list_and_the_folder(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        self._erase()
        kept = turn(agent, "keep it for later")
        assert "Kept your to-do list" in reply(kept)
        assert _open_titles() == ["buy milk"] and _list_file().exists()

    def test_the_question_lapses_on_any_other_turn(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        self._erase()
        turn(agent, "what's 2 + 2?")
        later = turn(agent, "delete it")
        assert _route(later) != LANE
        assert _open_titles() == ["buy milk"]

    def test_the_answer_counts_only_in_the_chat_that_was_asked(self, someone: Path) -> None:
        agent = make_agent()
        turn(agent, "add buy milk to my to-do list")
        self._erase("person-chat")
        other = turn(agent, "delete it", session="another-chat")
        assert _route(other) != LANE
        assert _open_titles() == ["buy milk"]

    def test_no_list_no_question(self, someone: Path) -> None:
        assert self._erase() == ""
