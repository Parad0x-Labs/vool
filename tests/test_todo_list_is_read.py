"""A to-do list ask reads the person's to-do FILE, on the computer and on the phone.

Measured on the phone parity audit 2026-10-29, with todo.txt sitting on the Desktop the whole
time: "what was on that todo list on my desktop again?" came back as the Desktop's entry list
(flat viewing notes.md, two screenshots, todo.txt -- everything except the list), and "read me my
to do list" / "whats on my to do list again" came back "I can't access your to-do list directly"
/ "I don't have access". The noun names a file the file tools already read; every case here pins
that resolution end to end, in-process, against a fake home built with the audit's exact layout.
"""
from __future__ import annotations

import itertools
import os
from pathlib import Path

import pytest

from apps.vool_agent import VoolAgent

# One agent per surface, built once: VoolAgent holds session state and the file must stay well
# under a minute. HOME is read at call time, not at construction, so a module-level agent still
# lands in whatever fake home the running test pinned. Each ask uses its own session id so no
# state crosses a test.
_COMPUTER_AGENT = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
_PHONE_AGENT = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
_TURN = itertools.count()

# Pinned mtimes so "newest" is a fact about the fixture, not a race with the file system clock.
_STAMP_BASE = 1_700_000_000


def _session() -> str:
    return f"todo-read-{next(_TURN)}"


def _ask(
    agent: VoolAgent,
    text: str,
    *,
    source_surface: str = "api",
    source_context: dict[str, object] | None = None,
) -> dict[str, object] | None:
    return agent._maybe_handle_direct_machine_read_request(
        text,
        session_id=_session(),
        source_surface=source_surface,
        source_context=dict(source_context if source_context is not None else {"surface": "api"}),
    )


def _text(result: dict[str, object] | None) -> str:
    assert result is not None, "the ask was not claimed, so no lane read the file"
    return str(result["response"])


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty fake home with the folders the lane may read.

    HOME is redirected so the test cannot reach the real machine; the assert keeps a silently
    failed redirect from reading a real Desktop somewhere far less legible than here.
    """
    root = tmp_path / "fakehome"
    for name in ("Desktop", "Documents", "Downloads"):
        (root / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    assert Path("~").expanduser() == root, "HOME redirect did not take"
    return root


@pytest.fixture()
def audit_home(home: Path) -> Path:
    """The exact layout the audit cases name: todo.txt on the Desktop among its decoys."""
    desktop = home / "Desktop"
    (desktop / "todo.txt").write_text("buy milk\ncall the plumber about the drip\nbook dentist\n", encoding="utf-8")
    (desktop / "flat viewing notes.md").write_text("# Flat viewing\n- ask about the lease\n", encoding="utf-8")
    (desktop / "Screenshot 2026-10-20 at 11.40.png").write_bytes(b"\x89PNG screenshot one")
    (desktop / "Screenshot 2026-10-27 at 18.02.png").write_bytes(b"\x89PNG screenshot two")
    (home / "Documents" / "recipes.md").write_text("# Pasta\n- make the sauce\n", encoding="utf-8")
    return home


def _todo_file(home: Path, folder: str, name: str, items: str, *, age: int) -> str:
    """One to-do-looking file with a pinned mtime; returns its home-relative label."""
    target = home / folder / name
    target.write_text(items, encoding="utf-8")
    os.utime(target, (_STAMP_BASE + age, _STAMP_BASE + age))
    return f"{folder}/{name}"


@pytest.fixture()
def phone_grant(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """A paired phone turn that holds the "computer tools" right.

    The grant check is answered directly, exactly as tests/test_owner_file_demand.py does for the
    requirements layer, and the phone's own record stands in for the pairing store the sealed
    phone door keeps (that door has its own tests). Everything else -- the scan, the authorized
    read, the folder fences inside it, the wording -- runs for real. A granted phone with no
    scope limit reads the whole readable set, which is what an owner granted without choosing
    folders has.
    """
    import core.request_trust as request_trust

    device = {"status": "active", "kind": "phone", "sealed_protocol": True, "device_id": "dev-todo-test"}
    monkeypatch.setattr(
        request_trust,
        "remote_turn_has_grant",
        lambda context, grant: grant == request_trust.REMOTE_GRANT_COMPUTER,
    )
    monkeypatch.setattr(request_trust, "remote_turn_device", lambda context: device)
    monkeypatch.setattr(request_trust, "remote_turn_folders", lambda context: None)
    return {
        request_trust.OWNER_LOCAL_KEY: False,
        request_trust.REMOTE_DEVICE_KEY: "dev-todo-test",
        "surface": "channel",
    }


# --------------------------------------------------------------------------------------
# the classifier: noun + read cue = a file ask; an edit ask is never a read
# --------------------------------------------------------------------------------------


class TestTheClassifier:
    def test_the_place_the_ask_names_is_reported(self) -> None:
        from core.execution.constants import machine_todo_list_read_intent

        todo = machine_todo_list_read_intent("what was on that todo list on my desktop again?")
        assert todo is not None and todo.place == "Desktop"
        plain = machine_todo_list_read_intent("read me my to do list")
        assert plain is not None and plain.place is None

    @pytest.mark.parametrize(
        "text", ["add milk to my todo list", "make me a new to do list"]
    )
    def test_an_ask_that_edits_the_list_is_not_a_read(self, text: str) -> None:
        from core.execution.constants import machine_todo_list_read_intent

        assert machine_todo_list_read_intent(text) is None


# --------------------------------------------------------------------------------------
# the three audit asks, answered with the file's own lines
# --------------------------------------------------------------------------------------


class TestTheAuditAsksReadTheFile:
    def test_what_was_on_that_todo_list_on_my_desktop_again(self, audit_home: Path) -> None:
        """The audit's N04 ask was answered with the Desktop's entry list, todo.txt included."""

        reply = _text(_ask(_COMPUTER_AGENT, "what was on that todo list on my desktop again?"))
        assert reply.startswith("Your to-do list (Desktop/todo.txt):")
        for item in ("buy milk", "call the plumber about the drip", "book dentist"):
            assert item in reply
        # The measured wrong answer was the folder listing around the list.
        assert "flat viewing notes" not in reply
        assert ".png" not in reply
        assert "water the plants" not in reply  # items are read, never invented

    def test_read_me_my_to_do_list(self, audit_home: Path) -> None:
        """The audit's F48 ask was answered "I can't access your to-do list directly"."""

        reply = _text(_ask(_COMPUTER_AGENT, "read me my to do list"))
        assert reply.startswith("Your to-do list (Desktop/todo.txt):")
        for item in ("buy milk", "call the plumber about the drip", "book dentist"):
            assert item in reply

    def test_whats_on_my_to_do_list_again(self, audit_home: Path) -> None:
        """The audit's F14 ask was answered "I don't have access"."""

        reply = _text(_ask(_COMPUTER_AGENT, "whats on my to do list again"))
        assert reply.startswith("Your to-do list (Desktop/todo.txt):")
        for item in ("buy milk", "call the plumber about the drip", "book dentist"):
            assert item in reply

    def test_the_list_is_named_relative_to_home_with_no_tilde(self, audit_home: Path) -> None:
        header = _text(_ask(_COMPUTER_AGENT, "read me my to do list")).splitlines()[0]
        assert header == "Your to-do list (Desktop/todo.txt):"


# --------------------------------------------------------------------------------------
# how the file is resolved: the named place, the default places, newest first
# --------------------------------------------------------------------------------------


class TestHowTheFileIsResolved:
    def test_the_place_the_ask_names_is_the_only_folder_searched(self, home: Path) -> None:
        """A newer list in Documents must not win when the ask says "on my desktop"."""

        _todo_file(home, "Documents", "to-do.md", "renew the passport\n", age=9000)
        _todo_file(home, "Desktop", "todo.txt", "buy milk\n", age=1000)
        reply = _text(_ask(_COMPUTER_AGENT, "show me my todo list on my desktop"))
        assert reply.startswith("Your to-do list (Desktop/todo.txt):")
        assert "buy milk" in reply
        assert "renew the passport" not in reply

    def test_with_no_place_named_downloads_is_searched_too(self, home: Path) -> None:
        _todo_file(home, "Downloads", "todo.md", "return the library book\n", age=1000)
        reply = _text(_ask(_COMPUTER_AGENT, "what's on my to do list?"))
        assert reply.startswith("Your to-do list (Downloads/todo.md):")
        assert "return the library book" in reply

    def test_several_matches_name_the_candidates_and_read_the_newest(self, home: Path) -> None:
        _todo_file(home, "Desktop", "todo.txt", "water the plants\n", age=1000)
        _todo_file(home, "Downloads", "to_do.txt", "return the library book\n", age=2000)
        _todo_file(home, "Documents", "to-do.md", "renew the passport\n", age=3000)
        reply = _text(_ask(_COMPUTER_AGENT, "what's on my to do list?"))
        for label in ("Desktop/todo.txt", "Documents/to-do.md", "Downloads/to_do.txt"):
            assert label in reply, f"the reply must name the candidates; {label} was missing"
        assert "renew the passport" in reply  # the newest one is the one read
        assert "water the plants" not in reply  # the others are named, not read
        assert "return the library book" not in reply

    def test_at_most_three_candidates_are_named(self, home: Path) -> None:
        _todo_file(home, "Desktop", "todo.txt", "water the plants\n", age=1000)
        _todo_file(home, "Desktop", "todo list.txt", "wash the car\n", age=500)
        _todo_file(home, "Downloads", "to_do.txt", "return the library book\n", age=2000)
        _todo_file(home, "Documents", "to-do.md", "renew the passport\n", age=3000)
        reply = _text(_ask(_COMPUTER_AGENT, "what's on my to do list?"))
        assert "renew the passport" in reply
        assert "todo list.txt" not in reply, "a fourth candidate is past the three-name cap"
        assert "wash the car" not in reply

    @pytest.mark.parametrize(
        "text",
        [
            "what's on my to do list",
            "whats on my to-do list",
            "what is on my todo",
            "what was on my todos",
            "read my to-do",
            "read me my checklist",
            "show me my to_do list",
            "open my checklist",
            "remind me what's on my list of things to do",
            "tell me what's on my to do list",
        ],
    )
    def test_every_noun_and_cue_shape_reads_the_list(self, text: str, audit_home: Path) -> None:
        """The spelling of the noun and the wording of the cue must not decide whether it reads."""

        reply = _text(_ask(_COMPUTER_AGENT, text))
        assert "buy milk" in reply


# --------------------------------------------------------------------------------------
# no file: a plain answer naming what was searched, never an invented list
# --------------------------------------------------------------------------------------


class TestNoMatch:
    def test_no_list_anywhere_names_the_folders_searched(self, home: Path) -> None:
        reply = _text(_ask(_COMPUTER_AGENT, "what's on my to do list?"))
        assert reply == "I couldn't find a to-do list file in Desktop or Documents. Tell me where it is."

    def test_no_list_in_the_place_the_ask_named(self, home: Path) -> None:
        reply = _text(_ask(_COMPUTER_AGENT, "what's on my todo list on my desktop"))
        assert reply == "I couldn't find a to-do list file on your Desktop. Tell me where it is."


# --------------------------------------------------------------------------------------
# read-only: an ask that edits the list keeps the lane it already has
# --------------------------------------------------------------------------------------


class TestWritesKeepTheirLanes:
    @pytest.mark.parametrize(
        "text", ["add milk to my todo list", "make me a new to do list"]
    )
    def test_an_ask_that_edits_the_list_is_not_claimed(self, text: str, audit_home: Path) -> None:
        """The list file is right there, which is exactly why the read lane must not take it."""

        assert _ask(_COMPUTER_AGENT, text) is None


# --------------------------------------------------------------------------------------
# the phone hears what the computer hears
# --------------------------------------------------------------------------------------


class TestThePhoneHearsTheSameList:
    def test_the_phone_gets_the_computers_answer(self, audit_home: Path, phone_grant: dict[str, object]) -> None:
        computer = _text(_ask(_COMPUTER_AGENT, "read me my to do list"))
        phone = _text(
            _ask(_PHONE_AGENT, "read me my to do list", source_surface="channel", source_context=phone_grant)
        )
        assert phone == computer
        assert "buy milk" in phone

    def test_the_desktop_ask_matches_on_the_phone_too(
        self, audit_home: Path, phone_grant: dict[str, object]
    ) -> None:
        """The audit's N04 ask is the phone thread's own; parity is measured on it directly."""

        computer = _text(_ask(_COMPUTER_AGENT, "what was on that todo list on my desktop again?"))
        phone = _text(
            _ask(
                _PHONE_AGENT,
                "what was on that todo list on my desktop again?",
                source_surface="channel",
                source_context=phone_grant,
            )
        )
        assert phone == computer
        assert "call the plumber about the drip" in phone

    def test_a_phone_without_the_computer_right_hears_where_to_turn_it_on(
        self, audit_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The scan never runs for a phone without the right: what is on this disk is not that
        phone's to learn, and the answer it needs is where the right is switched on."""

        import core.request_trust as request_trust

        device = {"status": "active", "kind": "phone", "sealed_protocol": True, "device_id": "dev-no-right"}
        monkeypatch.setattr(request_trust, "remote_turn_device", lambda context: device)
        monkeypatch.setattr(request_trust, "remote_turn_has_grant", lambda context, grant: False)
        context = {
            request_trust.OWNER_LOCAL_KEY: False,
            request_trust.REMOTE_DEVICE_KEY: "dev-no-right",
            "surface": "channel",
        }
        reply = _text(_ask(_PHONE_AGENT, "whats on my to do list again", source_surface="channel", source_context=context))
        assert reply == request_trust.REMOTE_GRANT_REFUSAL_TEXT[request_trust.REMOTE_GRANT_COMPUTER]
        assert "buy milk" not in reply  # the list on this disk never reaches that phone

    def test_a_phone_held_to_chosen_folders_never_hears_of_a_list_outside_them(
        self, audit_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The owner limited this phone to Documents, so a Desktop list is not this phone's to
        learn about -- not even its name, not even when the ask says "on my desktop"."""

        import core.request_trust as request_trust

        device = {"status": "active", "kind": "phone", "sealed_protocol": True, "device_id": "dev-folders"}
        monkeypatch.setattr(request_trust, "remote_turn_device", lambda context: device)
        monkeypatch.setattr(request_trust, "remote_turn_has_grant", lambda context, grant: True)
        monkeypatch.setattr(
            request_trust,
            "remote_turn_folders",
            lambda context: (str((audit_home / "Documents").resolve()),),
        )
        context = {
            request_trust.OWNER_LOCAL_KEY: False,
            request_trust.REMOTE_DEVICE_KEY: "dev-folders",
            "surface": "channel",
        }
        reply = _text(_ask(_PHONE_AGENT, "what was on that todo list on my desktop again?", source_surface="channel", source_context=context))
        assert reply == "I couldn't find a to-do list file in Documents. Tell me where it is."
        assert "todo.txt" not in reply and "buy milk" not in reply

    def test_a_remote_stamp_with_no_paired_phone_keeps_its_lanes(
        self, audit_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The remote stamp the sealed phone door never issued is not this lane's to answer for:
        the turn keeps whatever lanes it had (measured: the handler stands down, returns None)."""

        import core.request_trust as request_trust

        monkeypatch.setattr(request_trust, "remote_turn_device", lambda context: None)
        monkeypatch.setattr(request_trust, "remote_turn_has_grant", lambda context, grant: False)
        context = {
            request_trust.OWNER_LOCAL_KEY: False,
            request_trust.REMOTE_DEVICE_KEY: "dev-unknown",
            "surface": "channel",
        }
        assert (
            _ask(_PHONE_AGENT, "whats on my to do list again", source_surface="channel", source_context=context)
            is None
        )
