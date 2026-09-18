"""Acceptance regressions for `machine.list_directory`, from phrasings driven at the live daemon.

Three separate misses, all measured on the deployed build at 2026-07-30 (commit e3f59af), all of
which produced an answer that READ as correct:

1. "can you tell me what's sitting in my Desktop folder right now" came back as 81 folders under a
   "Visible folders" header, silently dropping all 83 files on the Desktop. The planner's folders-only
   test was the substring `" folder" in lowered`, which cannot tell a request to filter ("what are
   the FOLDERS on my desktop") from the directory noun naming the target ("my Desktop FOLDER").

2. "What is inside /etc?" was refused by the machine allowlist -- correctly, /etc is outside the
   readable lane -- and then the fast path threw that refusal away because it gated on `execution.ok`.
   The turn fell through to the model, which answered with a textbook description of /etc on a
   machine whose /etc had never been read. The refusal was the true answer and it was discarded.

3. "ls ~/Applications/vool" reached no lane at all and came back as ```vool  OpenClaw``` -- two
   invented entries matching neither that directory (105 entries) nor its parent (3).

Three more from the fresh phrasings driven after those were fixed:

4. "Could you enumerate the contents of ~/Downloads/Telegram Bot for me?" was cut at the space and
   answered "Local directory `~/Downloads/Telegram` does not exist." -- a confident negative about
   a folder that does exist.

5. "what folders live on my desktop" matched no listing pattern, fell through every lane, and after
   83 seconds refused with "that tool didn't run -- so I won't guess a value", for a directory the
   tool reads instantly.

6. "i cant remember what i put in the Documents dir, can you check" was claimed by the machine WRITE
   lane on the bare word " put " and answered with a refusal to write files it had not written.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from core.agent_runtime import fast_paths_machine
from core.agent_runtime.fast_paths_machine import looks_like_safe_machine_write_request
from core.execution.constants import explicit_path_in, machine_path_listing_intent
from core.execution.planner import _extract_safe_machine_directory_listing


def _folders_only(text: str) -> bool | None:
    extracted = _extract_safe_machine_directory_listing(text)
    return None if extracted is None else extracted["directories_only"]


# --------------------------------------------------------------------------------------
# 1. the directory noun naming the target is not a request to hide every file
# --------------------------------------------------------------------------------------


def test_whats_sitting_in_my_desktop_folder_lists_files_too() -> None:
    """The verbatim phrasing that returned folders-only and dropped 83 files."""

    assert _folders_only("can you tell me what's sitting in my Desktop folder right now") is False


@pytest.mark.parametrize(
    "text",
    [
        "show me whats in my Documents folder",
        "hey whats in teh Downloads dir",
        "what's in the Downloads directory",
        "list everything in my downloads folder",
    ],
)
def test_singular_directory_noun_names_the_target_not_a_filter(text: str) -> None:
    assert _folders_only(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "tell me what are the folders on my desktop",
        "can you see folders on my desktop?",
        "list the directories in ~/Desktop",
        # missed by the old substring test: "subfolders" has no space before "folder"
        "show me the subfolders of Downloads",
    ],
)
def test_a_real_folders_only_ask_still_filters(text: str) -> None:
    """Narrowing the detector must not cost the case it exists for."""

    assert _folders_only(text) is True


def test_asking_for_folders_and_files_still_lists_both() -> None:
    assert _folders_only("what are the folders and files on my desktop?") is False


# --------------------------------------------------------------------------------------
# 2. a grounded refusal about a typed path is the answer, not a reason to ask the model
# --------------------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        self.events.append({"event_type": event_type, "message": message, **details})

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs.get("response", ""), "reason": kwargs.get("reason", "")}

    def _plan_tool_workflow(self, **kwargs):
        # Nothing planned: the turn leaves this lane, which is what "fell through" means here.
        return types.SimpleNamespace(handled=False, next_payload=None, reason="")


def _drive(monkeypatch: pytest.MonkeyPatch, text: str, *, ok: bool, status: str, response_text: str):
    """Run the machine read fast path with list_directory stubbed to a known verdict."""

    calls: list[tuple[str, dict]] = []

    def _fake_execute(intent, arguments, source_context=None):
        calls.append((intent, dict(arguments or {})))
        return types.SimpleNamespace(
            ok=ok,
            status=status,
            response_text=response_text,
            details={"observation": {"intent": intent}},
        )

    monkeypatch.setattr(fast_paths_machine, "execute_authorized_runtime_tool", _fake_execute)
    result = fast_paths_machine.maybe_handle_direct_machine_read_request(
        _FakeAgent(),
        text,
        session_id="s-listdir",
        source_surface="api",
        source_context={"session_id": "s-listdir"},
    )
    return result, calls


def test_out_of_lane_path_answers_with_the_refusal_not_a_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verbatim phrasing that got a textbook description of an unread /etc."""

    refusal = "I can only inspect safe local directories in this lane: ~/Desktop, ~/Downloads, ~/Documents."
    result, calls = _drive(
        monkeypatch, "What is inside /etc?", ok=False, status="not_allowed", response_text=refusal
    )
    assert calls and calls[0][1]["path"] == "/etc"
    assert result is not None, "the refusal was dropped and the turn fell through to the model"
    assert result["response"] == refusal


def test_a_typed_path_that_does_not_exist_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = "Local directory `~/Desktop/nope` does not exist."
    result, _calls = _drive(
        monkeypatch,
        "what files are in ~/Desktop/nope",
        ok=False,
        status="not_found",
        response_text=missing,
    )
    assert result is not None
    assert result["response"] == missing


def test_an_unrecognised_failure_still_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only verdicts ABOUT the path are surfaced; an internal error keeps the old behaviour."""

    result, _calls = _drive(
        monkeypatch, "what files are in ~/Desktop", ok=False, status="error", response_text="boom"
    )
    assert result is None


# --------------------------------------------------------------------------------------
# 3. `ls <path>` is how a terminal user asks for a listing
# --------------------------------------------------------------------------------------


def test_ls_with_a_path_is_a_listing_ask() -> None:
    """The verbatim phrasing that returned two invented entries."""

    assert machine_path_listing_intent("ls ~/Applications/vool") == "~/Applications/vool"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ls /etc", "/etc"),
        ("please run ls /Users/example-user/Desktop", "/Users/example-user/Desktop"),
    ],
)
def test_ls_variants_resolve_to_the_typed_path(text: str, expected: str) -> None:
    assert machine_path_listing_intent(text) == expected


@pytest.mark.parametrize("text", ["what does ls do", "ls the downloads folder"])
def test_bare_ls_prose_does_not_claim_a_listing(text: str) -> None:
    """The lookahead is the guard: no path right after `ls` means no claim."""

    assert machine_path_listing_intent(text) is None


# --------------------------------------------------------------------------------------
# 4. a folder name with a space in it is still that folder
# --------------------------------------------------------------------------------------


@pytest.fixture()
def spaced_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real folder whose name contains a space, reachable through ~ expansion."""

    target = tmp_path / "Downloads" / "Telegram Bot"
    target.mkdir(parents=True)
    (tmp_path / "Downloads" / "plain").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    # These tests assert on what the DISK says, so a fixture that silently failed to redirect HOME
    # would read the real machine and fail somewhere far less legible than here.
    assert Path("~/Downloads/Telegram Bot").expanduser() == target, "HOME redirect did not take"
    return target


def test_a_path_with_a_space_is_not_cut_at_the_space(spaced_folder: Path) -> None:
    """The verbatim phrasing that reported a real folder as nonexistent."""

    assert (
        explicit_path_in("Could you enumerate the contents of ~/Downloads/Telegram Bot for me?")
        == "~/Downloads/Telegram Bot"
    )


def test_the_trailing_words_of_the_sentence_are_not_swallowed(spaced_folder: Path) -> None:
    """Growth stops at the longest path that EXISTS -- prose after it stays prose."""

    assert explicit_path_in("what is in ~/Downloads/plain and why is it there") == "~/Downloads/plain"


def test_a_path_that_does_not_exist_is_returned_unchanged(spaced_folder: Path) -> None:
    """No disk match means no growth, so a typo still reports the path the user typed."""

    assert explicit_path_in("what files are in ~/Downloads/Nope Missing") == "~/Downloads/Nope"


def test_the_spaced_path_reaches_the_listing_intent(spaced_folder: Path) -> None:
    assert (
        machine_path_listing_intent("Could you enumerate the contents of ~/Downloads/Telegram Bot for me?")
        == "~/Downloads/Telegram Bot"
    )


# --------------------------------------------------------------------------------------
# 5. a listing noun straight after "what" is a listing ask
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what folders live on my desktop",
        "how many folders are on my desktop",
        "what files sit in my downloads",
    ],
)
def test_a_noun_led_listing_ask_is_claimed_not_refused(text: str) -> None:
    """A refusal we did not need is still a wrong answer."""

    extracted = _extract_safe_machine_directory_listing(text)
    assert extracted is not None, "no lane claimed it, so the turn refused a directory it can read"


def test_what_folders_live_on_my_desktop_filters_to_folders() -> None:
    assert _folders_only("what folders live on my desktop") is True


# --------------------------------------------------------------------------------------
# 6. the user narrating their own past write is not a write request
# --------------------------------------------------------------------------------------


def test_recalling_what_i_put_somewhere_is_a_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verbatim phrasing that got a write refusal for a read question."""

    assert looks_like_safe_machine_write_request(
        "i cant remember what i put in the Documents dir, can you check"
    ) is False


@pytest.mark.parametrize(
    "text",
    [
        "what did i save to my desktop last week",
        "find the notes i wrote in documents",
    ],
)
def test_other_past_tense_recalls_are_reads_too(text: str) -> None:
    assert looks_like_safe_machine_write_request(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "please write a note to my desktop called hi.txt",
        "create a folder on my desktop called test",
        "save this to my downloads folder",
    ],
)
def test_a_real_write_request_is_still_claimed(text: str) -> None:
    """Narrowing the write lane must not cost it the turns it exists for."""

    assert looks_like_safe_machine_write_request(text) is True


def test_writing_about_the_desktop_remains_authoring_not_a_machine_write() -> None:
    assert looks_like_safe_machine_write_request("write a note about my desktop layout") is False


# --------------------------------------------------------------------------------------
# 7. the planner's own path extractor had the same cut-at-the-space bug
# --------------------------------------------------------------------------------------


def test_the_planner_extractor_keeps_a_spaced_folder_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verbatim phrasing that ended in "I wasn't able to turn that into a completed action"."""

    (tmp_path / "Documents" / "Vool testing").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    extracted = _extract_safe_machine_directory_listing(
        "check ~/Documents/Vool testing and tell me whats there"
    )
    assert extracted is not None
    assert extracted["path"] == "~/Documents/Vool testing"


# --------------------------------------------------------------------------------------
# 8. plain content questions that reached no lane at all
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_path"),
    [
        # went to the cloud model for 49s and answered nothing
        ("anything in ~/Desktop/nonexistent-xyz?", "~/Desktop/nonexistent-xyz"),
        # answered "Sure. Let me check the contents of the Documents directory for you." and stopped
        ("i cant remember what i put in the Documents dir, can you check", "~/Documents"),
    ],
)
def test_a_plain_content_question_reaches_the_listing_lane(text: str, expected_path: str) -> None:
    extracted = _extract_safe_machine_directory_listing(text)
    assert extracted is not None, "no lane claimed it, so a model answered a question about the disk"
    assert extracted["path"] == expected_path


# --------------------------------------------------------------------------------------
# 9. a count question wants a number
# --------------------------------------------------------------------------------------


def test_how_many_asks_for_a_count_not_a_page_of_names() -> None:
    """The verbatim phrasing answered with thirty filenames and a bare "-..."."""

    extracted = _extract_safe_machine_directory_listing("how many files are in ~/Downloads")
    assert extracted is not None
    assert extracted.get("count_only") is True


def test_a_plain_listing_ask_does_not_become_a_count() -> None:
    extracted = _extract_safe_machine_directory_listing("what files are in ~/Desktop")
    assert extracted is not None
    assert extracted.get("count_only") is not True


def test_the_count_is_the_whole_directory_not_the_printed_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counting the page we would have printed would under-report a big folder."""

    from core.runtime_execution_tools import execute_runtime_tool

    downloads = tmp_path / "Downloads"
    downloads.mkdir(parents=True)
    for index in range(12):
        (downloads / f"file{index:02d}.txt").write_text("x")
    (downloads / "a-folder").mkdir()
    (downloads / ".hidden").write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))

    result = execute_runtime_tool(
        "machine.list_directory",
        {"path": "~/Downloads", "count_only": True, "limit": 5},
        source_context={},
    )
    assert result.ok
    # 12 files + 1 folder, the dotfile excluded, and the limit of 5 must not cap the number
    assert "13 visible entries (1 folders, 12 files)" in result.response_text


def test_count_only_is_a_declared_argument() -> None:
    """An argument the planner sends must be in the contract or the tool rejects the call."""

    from core.runtime_tool_contracts import runtime_tool_contracts

    contract = next(c for c in runtime_tool_contracts() if c.intent == "machine.list_directory")
    assert "count_only" in contract.input_schema


# --------------------------------------------------------------------------------------
# 10. the read gate has to admit what the lane can actually serve
# --------------------------------------------------------------------------------------


from core.agent_runtime.fast_paths_machine import (
    looks_like_supported_machine_read_request,
)


@pytest.mark.parametrize(
    "text",
    [
        # 49s of cloud model, answered nothing; the folder simply is not there
        "anything in ~/Desktop/nonexistent-xyz?",
        # 64s, "I couldn't map that cleanly to a real action"; that path is outside the lane
        "Show me what /private/var/db contains",
        # 60s, "I couldn't get a usable model response in this run"; it is a directory listing
        "i cant remember what i put in the Documents dir, can you check",
    ],
)
def test_the_read_gate_admits_a_directory_question(text: str) -> None:
    assert looks_like_supported_machine_read_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # the memory-recall exclusion exists for these and must keep working
        "do you remember what i told you yesterday",
        "i cant remember what we agreed on",
        "what can you do",
        "help me write a poem",
    ],
)
def test_the_read_gate_still_stands_down_where_it_should(text: str) -> None:
    """Widening the gate must not let it claim turns that belong elsewhere."""

    assert looks_like_supported_machine_read_request(text) is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("anything in ~/Desktop/nonexistent-xyz?", "~/Desktop/nonexistent-xyz"),
        ("Show me what /private/var/db contains", "/private/var/db"),
    ],
)
def test_these_contents_questions_resolve_to_the_typed_path(text: str, expected: str) -> None:
    assert machine_path_listing_intent(text) == expected


def test_a_forgetful_preamble_alone_is_not_a_disk_question() -> None:
    """Both halves are required: the user forgot AND the sentence points at this disk."""

    from core.agent_runtime.fast_paths_machine import _user_forgot_and_asks_the_disk

    assert _user_forgot_and_asks_the_disk("i cant remember what we agreed on") is False
    assert _user_forgot_and_asks_the_disk("i forgot what is in my downloads") is True


# --------------------------------------------------------------------------------------
# 11. the phrasebook has an edge; the disk does not
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # each of these cost 60-80s and came back "I couldn't map that cleanly to a real action"
        ("whatre the files under ~/Desktop/my-lyrics-folder", "~/Desktop/my-lyrics-folder"),
        ("run a dir on ~/Desktop/my-budget-folder", "~/Desktop/my-budget-folder"),
        ("just the folders in ~/Desktop please", "~/Desktop"),
        ("whats sat in ~/Desktop/my-harbor-folder these days", "~/Desktop/my-harbor-folder"),
    ],
)
def test_a_typed_directory_plus_a_content_cue_is_a_listing(
    text: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("my-lyrics-folder", "my-budget-folder", "my-harbor-folder"):
        (tmp_path / "Desktop" / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert machine_path_listing_intent(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # a FILE is read, not listed -- this one carries a content cue and no other-intent verb,
        # so only the is-a-file check can decline it
        "run a dir on ~/Desktop/proj/chorus.txt",
        "read ~/Desktop/proj/chorus.txt",
        # these ask for something other than a listing of the same folder
        "summarize the files in ~/Desktop/proj",
        "what does the code in ~/Desktop/proj do",
        "find the config in ~/Desktop/proj",
        "delete everything in ~/Desktop/proj",
        # no path at all
        "how do i list files in linux",
        # the loose route needs a REAL directory; a path that is not there has nothing for it,
        # and the strict route above deliberately leaves this phrasing alone
        "give me an inventory of everything stored in /Users/me/Documents/pollen-index",
    ],
)
def test_the_fallback_declines_what_is_not_a_listing(
    text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback is broad on purpose, so what it must NOT claim is the load-bearing half."""

    proj = tmp_path / "Desktop" / "proj"
    proj.mkdir(parents=True)
    (proj / "chorus.txt").write_text("x")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert machine_path_listing_intent(text) is None


def test_the_view_the_sentence_asks_for_travels_with_the_path() -> None:
    """"just the folders in ~/Desktop please" was answered with every entry under the right path."""

    from core.execution.planner import listing_view_arguments

    assert listing_view_arguments("just the folders in ~/Desktop please") == {"directories_only": True}
    assert listing_view_arguments("how many files are in ~/Downloads") == {"count_only": True}
    assert listing_view_arguments("count the entries in ~/Documents") == {"count_only": True}
    # a plain listing asks for no special view at all
    assert listing_view_arguments("whatre the files under ~/Desktop/my-lyrics-folder") == {}


# --------------------------------------------------------------------------------------
# 12. a list that silently stops is a wrong answer, not a short one
# --------------------------------------------------------------------------------------


@pytest.fixture()
def crowded_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    desktop = tmp_path / "Desktop"
    desktop.mkdir(parents=True)
    for index in range(81):
        (desktop / f"folder-{index:03d}").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    return desktop


def test_a_truncated_listing_says_how_many_it_dropped(crowded_desktop: Path) -> None:
    """"just the folders in ~/Desktop please" printed 50 of 81 and said nothing about the 31."""

    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(
        "machine.list_directory", {"path": "~/Desktop", "directories_only": True}, source_context={}
    )
    assert result.ok
    assert result.status == "truncated"
    assert "…and 31 more folders (81 in total)." in result.response_text
    assert result.details["total"] == 81
    assert result.details["truncated"] is True


def test_a_listing_that_fits_claims_no_truncation(crowded_desktop: Path) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(
        "machine.list_directory",
        {"path": "~/Desktop", "directories_only": True, "limit": 200},
        source_context={},
    )
    assert result.status == "executed"
    assert "more folders" not in result.response_text
    assert result.details["total"] == 81
    assert result.details["truncated"] is False


def test_a_truncated_listing_is_still_a_grounded_verdict() -> None:
    """The new status must be in the set the fast path surfaces, or it falls through to a model."""

    assert "truncated" in fast_paths_machine._GROUNDED_LISTING_VERDICTS


def test_the_fast_path_asks_for_the_same_limit_as_the_planner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool's own default is 50, which is what printed 50 of 81 folders.

    HOME is redirected at a real Desktop, the same way the other typed-path tests in this file do
    it, because this route's warrant is a directory that EXISTS (`_typed_directory_content_ask`
    declines otherwise, deliberately). Leaning on the developer's own ~/Desktop made this pass on
    every Mac and fail on every CI run: a Linux runner's home has no Desktop, so the route correctly
    declined and `calls` came back empty. The runtime was right; the test was reading the host.
    """
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    _result, calls = _drive(
        monkeypatch,
        "just the folders in ~/Desktop please",
        ok=True,
        status="executed",
        response_text="ok",
    )
    assert calls, "the listing route did not run"
    assert calls[0][1]["limit"] == 200
    assert calls[0][1]["directories_only"] is True
