"""Making and listing a skill reads plainly: one line per skill, no machine paths, no junk names.

What this pins, and why each can fail:

1. A skill that was drafted and then installed is listed ONCE. The install used to leave the
   staged draft behind, so the same skill answered under "Staged (drafted, not active)" AND under
   "Installed for all of VOOL (active)", each with a full machine path.
2. Installing draft A keeps staged draft B under the drafts section: install clears only its own
   skill's staging copy, never the whole staging area.
3. The install reply holds no path and no version jargon: "Added the skill "Decline politely". It
   is on from your next message." -- with "for this chat" / "for this project" kept for scopes.
4. With no drafting model, the fallback no longer invents a name from the first words of the
   request (the "chats-whenever-give" shape): it asks for a short name and uses the answer, and
   the fallback triggers are 2-3 word phrases from the request, not single words.
5. A locally installed skill reaches the model prompt without the person's home path or user name.
6. The built-in-only listing and the empty skills page speak in plain words and point to the one
   ask that starts a skill: "make me a skill that ...".
"""
from __future__ import annotations

import getpass
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ASK_FOR_NAME = "What should I call this skill? For example: Shopping list by aisle."
EMPTY_STATE = "No skills yet. Ask VOOL in any chat: make me a skill that ..."


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """HOME, the plugins tree and the runtime home all inside this test's temp folder."""
    from core.plugin_catalog import invalidate_storage_listing

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins-root"))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "voolhome"))
    invalidate_storage_listing()
    yield home
    invalidate_storage_listing()


def _draft(name: str, description: str, body: str) -> dict:
    from core import skill_tools

    created = skill_tools.create_skill(name=name, description=description, body=body)
    assert created["status"] == "ok", created
    return created


def _list_reply() -> str:
    from core.runtime_execution_tools import _skill_list

    result = _skill_list({})
    assert result.status == "ok", result.response_text
    return result.response_text


def _install_reply(arguments: dict, source_context: dict | None = None) -> str:
    from core.runtime_execution_tools import _skill_install

    result = _skill_install(arguments, source_context)
    assert result.status == "ok", result.response_text
    return result.response_text


# ---------------------------------------------------------------------------
# 1. Drafted, installed, listed: once, in plain words
# ---------------------------------------------------------------------------


def test_an_installed_skill_is_listed_once_in_plain_words() -> None:
    created = _draft(
        "decline-politely",
        "Use when the user wants to say no to an invitation.",
        "Decline kindly and offer one other time.",
    )
    reply = _install_reply({"path": created["path"]})

    listing = _list_reply()

    assert listing.count("Decline politely") == 1, "the skill must appear exactly once"
    assert "- Decline politely — on —" in listing
    assert "**Made by you**" in listing
    assert "Staged" not in listing, "the old duplicate section name is gone"
    if "Drafts not turned on yet" in listing:
        drafts_block = listing.split("Drafts not turned on yet", 1)[1]
        assert "Decline politely" not in drafts_block, "an installed skill is not a draft"
    assert "/" not in listing, "no machine path belongs in the reply"
    assert ".md" not in listing
    assert reply == 'Added the skill "Decline politely". It is on from your next message.'


def test_installing_one_draft_keeps_the_other_draft_listed() -> None:
    from core import skill_tools

    _draft(
        "shopping-list",
        "Use when the user plans groceries.",
        "Group the items by aisle.",
    )
    installed = _draft(
        "decline-politely",
        "Use when the user wants to say no to an invitation.",
        "Decline kindly and offer one other time.",
    )
    _install_reply({"path": installed["path"]})

    listing = _list_reply()

    assert "Drafts not turned on yet" in listing
    assert "- Shopping list — off —" in listing, "the untouched draft stays listed under drafts"
    assert listing.count("Shopping list") == 1
    assert "Decline politely" not in listing.split("Drafts not turned on yet", 1)[-1]
    # On disk too: only the installed skill's staging copy is gone.
    assert not (skill_tools.staging_root() / "decline-politely").exists()
    assert (skill_tools.staging_root() / "shopping-list" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# 2. The install reply: no path, scope words kept
# ---------------------------------------------------------------------------


def test_the_install_reply_holds_no_path_for_any_scope() -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.skill_scopes import scope_keys

    ensure_chat_namespace("chat-list-plain", project_id="project-list-plain")
    keys = scope_keys({"session_id": "chat-list-plain"})
    from core import skill_tools

    for scope, slug in (("chat", "meeting-actions"), ("project", "release-notes")):
        staged_dir = skill_tools._scoped_dirs(scope, keys)[0] / slug
        staged_dir.mkdir(parents=True, exist_ok=True)
        # A distinct slug and body per scope: the name resolver searches every scoped staging
        # area, and the identity-collision guard rightly refuses one instruction set behind two
        # names -- this test is about the reply's wording, not those gates.
        (staged_dir / "SKILL.md").write_text(
            f'---\nname: "{slug}"\ndescription: "Use when the user pastes meeting notes."\n'
            f'triggers: ["meeting notes"]\n---\nDo the {"chat" if scope == "chat" else "project"} job.\n',
            encoding="utf-8",
        )
        reply = _install_reply(
            {"path": slug, "scope": scope, "overwrite": True},
            source_context={"session_id": "chat-list-plain"},
        )
        where = "for this chat" if scope == "chat" else "for this project"
        display = "Meeting actions" if scope == "chat" else "Release notes"
        assert reply == (
            f'Added the skill "{display}" {where}. It is on from your next message.'
        )
        assert "/" not in reply


# ---------------------------------------------------------------------------
# 3. No drafting model: ask for a name, use the answer, phrases as triggers
# ---------------------------------------------------------------------------


def _no_model_agent() -> SimpleNamespace:
    return SimpleNamespace(
        memory_router=None,
        _fast_path_result=lambda **kw: {"response": kw.get("response"), "reason": kw.get("reason")},
    )


def test_with_no_model_the_reply_asks_for_a_name_and_creates_nothing() -> None:
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[tuple] = []

    def fake(intent, arguments, **kwargs):
        calls.append((intent, dict(arguments)))
        return SimpleNamespace(
            response_text="Drafted.", status="ok", ok=True, details={"slug": arguments.get("name"), "sha256": "ab" * 32}
        )

    with mock.patch("core.authorized_tool_execution.execute_authorized_runtime_tool", fake):
        first = maybe_handle_skill_request(
            _no_model_agent(),
            "make me a skill that reminds me to water my plants",
            session_id="plain-names",
            source_context={"operating_mode": "auto"},
        )

    assert first is not None
    assert ASK_FOR_NAME in first["response"]
    assert not calls, "nothing is created before the person answers"


def test_the_given_name_becomes_the_skill_name_and_triggers_are_phrases() -> None:
    from core.agent_runtime import fast_paths_skill
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[tuple] = []

    def fake(intent, arguments, **kwargs):
        calls.append((intent, dict(arguments)))
        return SimpleNamespace(
            response_text="Drafted.", status="ok", ok=True, details={"slug": arguments.get("name"), "sha256": "ab" * 32}
        )

    with mock.patch("core.authorized_tool_execution.execute_authorized_runtime_tool", fake):
        maybe_handle_skill_request(
            _no_model_agent(),
            "make me a skill that reminds me to water my plants",
            session_id="plain-names-2",
            source_context={"operating_mode": "auto"},
        )
        second = maybe_handle_skill_request(
            _no_model_agent(),
            "Call it Plant care",
            session_id="plain-names-2",
            source_context={"operating_mode": "auto"},
        )

    assert [c[0] for c in calls] == ["skill.create"], "the answer creates exactly the skill"
    arguments = calls[0][1]
    name = str(arguments.get("name") or "")
    assert name == "plant-care"
    assert not re.search(r"^(chats|whenever|give|reminds|plants)-", name), "no first-words junk name"
    assert "chats-whenever-give" not in json.dumps(arguments)
    triggers = [str(t) for t in arguments.get("triggers") or []]
    assert triggers, "the draft must carry its phrase triggers"
    assert all(" " in trigger for trigger in triggers), f"triggers are phrases, not words: {triggers}"
    assert second is not None
    assert "plant-care" in second["response"]
    assert "turn it on" in second["response"]


def test_a_bail_out_answer_does_not_create_a_skill() -> None:
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[tuple] = []

    def fake(intent, arguments, **kwargs):
        calls.append((intent, dict(arguments)))
        return SimpleNamespace(response_text="ok", status="ok", ok=True, details={})

    with mock.patch("core.authorized_tool_execution.execute_authorized_runtime_tool", fake):
        maybe_handle_skill_request(
            _no_model_agent(),
            "make me a skill that reminds me to water my plants",
            session_id="plain-names-3",
            source_context={"operating_mode": "auto"},
        )
        second = maybe_handle_skill_request(
            _no_model_agent(),
            "never mind",
            session_id="plain-names-3",
            source_context={"operating_mode": "auto"},
        )

    assert not calls
    assert second is None


# ---------------------------------------------------------------------------
# 4. The prompt header: a local skill never carries the home path
# ---------------------------------------------------------------------------


def test_a_local_skill_header_holds_no_home_path_or_user_name(tmp_path) -> None:
    from core.tool_offer_assembly import _skill_header

    local = tmp_path / "home" / "Vool-skills-plugins" / "plugins" / "local-skills" / "skills" / "decline-politely" / "SKILL.md"
    header = _skill_header(
        SimpleNamespace(name="decline-politely", plugin_id="local-skills", path=str(local))
    )

    assert str(tmp_path) not in header
    assert "/Users/" not in header
    assert getpass.getuser() not in header
    assert "decline-politely" in header
    assert "local" in header

    repo_skill = Path(__file__).resolve().parents[1] / "core" / "skills" / "sample-skill" / "SKILL.md"
    repo_header = _skill_header(
        SimpleNamespace(name="sample-skill", plugin_id="native", path=str(repo_skill))
    )
    assert "core/skills/sample-skill" in repo_header, "a repo skill keeps its repo-relative path"
    assert str(Path(__file__).resolve().parents[1]) not in repo_header


# ---------------------------------------------------------------------------
# 5. Built-in-only listing and the empty skills page
# ---------------------------------------------------------------------------


def test_with_only_builtin_skills_the_list_reads_plainly() -> None:
    listing = _list_reply()

    assert re.search(r"You have \d+ built-in skills(, \d+ of them on)?\.", listing)
    assert "**Built in**" in listing
    assert "To make your own, ask me: make me a skill that ..." in listing
    assert "skill(s)" not in listing
    assert ".md" not in listing


def test_the_empty_skills_page_points_to_the_one_ask() -> None:
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[1]
    catalog = json.loads((repo / "core" / "i18n" / "catalogs" / "en.json").read_text(encoding="utf-8"))
    assert catalog["messages"]["skills.none_installed"] == EMPTY_STATE

    page = (repo / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
    assert EMPTY_STATE in page
    assert "No skills installed yet" not in page


# ---------------------------------------------------------------------------
# Job B (AMENDMENT 1). 6. On/Off is real: the disable store and the row's own
# availability decide, not the section the row happens to sit in.
# ---------------------------------------------------------------------------


def _built_in_rows(listing: str) -> list[str]:
    assert "**Built in**" in listing
    block = listing.split("**Built in**", 1)[1].split("**", 1)[0]
    return [line for line in block.splitlines() if line.startswith("- ")]


def test_a_built_in_the_person_turned_off_says_off() -> None:
    from core.native_skill_library import disabled_skill_ids, load_skill_contracts, set_skill_enabled

    victim = sorted(c.id for c in load_skill_contracts().contracts)[0]
    assert set_skill_enabled(victim, False)["status"] == "ok"
    assert victim in disabled_skill_ids()

    rows = _built_in_rows(_list_reply())
    row = next(line for line in rows if line.startswith(f"- {_display(victim)} —"))
    assert " — off — " in row or row.endswith(" — off"), f"the disabled skill must say off: {row}"

    set_skill_enabled(victim, True)  # leave the store as it was


def test_a_built_in_that_cannot_run_here_says_not_available_here() -> None:
    listing = _list_reply()
    row = next((line for line in _built_in_rows(listing) if line.startswith("- Media studio —")), "")
    assert row, "media-studio is one of the built-ins"
    assert "— not available here —" in row or row.endswith("— not available here"), (
        f"a fresh profile cannot run media-studio, so the row must say so: {row}"
    )


def test_every_built_in_row_matches_the_disable_store_and_availability() -> None:
    from core.native_skill_library import disabled_skill_ids, skill_inventory

    disabled = disabled_skill_ids()
    runs = {row["id"]: bool(row.get("available")) for row in skill_inventory()}
    for row in _built_in_rows(_list_reply()):
        skill_id = row[2:].split(" — ")[0].lower().replace(" ", "-")
        expected = "off" if skill_id in disabled else (
            "not available here" if runs.get(skill_id) is False else "on"
        )
        assert f" — {expected}" in row, f"{skill_id} should read {expected}: {row}"


def test_the_built_in_head_line_counts_only_what_is_on() -> None:
    from core.native_skill_library import load_skill_contracts, set_skill_enabled

    victim = sorted(c.id for c in load_skill_contracts().contracts)[0]
    set_skill_enabled(victim, False)
    try:
        listing = _list_reply()
        head = listing.splitlines()[0]
        on_rows = sum(1 for line in _built_in_rows(listing) if " — on" in line)
        match = re.match(r"You have (\d+) built-in skills(, (\d+) of them on)?\.", head)
        assert match, f"the head line must count: {head}"
        total, on = int(match.group(1)), int(match.group(3) or match.group(1))
        assert on == on_rows, f"the head line's on-count must match the rows: {head}"
        assert total > on, "with one turned off, the line can not claim them all on"
    finally:
        set_skill_enabled(victim, True)


def test_a_disabled_installed_skill_says_off_by_its_name() -> None:
    from core.native_skill_library import set_skill_enabled

    created = _draft(
        "decline-politely",
        "Use when the user wants to say no to an invitation.",
        "Decline kindly and offer one other time.",
    )
    _install_reply({"path": created["path"]})
    assert set_skill_enabled("decline-politely", False)["status"] == "ok"
    try:
        listing = _list_reply()
        assert "- Decline politely — off —" in listing
    finally:
        set_skill_enabled("decline-politely", True)


def test_off_wins_when_a_skill_is_both_disabled_and_cannot_run() -> None:
    from core.native_skill_library import set_skill_enabled, skill_inventory

    runs = {row["id"]: bool(row.get("available")) for row in skill_inventory()}
    victim = next((i for i, ok in runs.items() if not ok), "")
    if not victim:  # a machine that can run everything: the precedence still holds for any skill
        victim = sorted(runs)[0]
    set_skill_enabled(victim, False)
    try:
        row = next(
            line for line in _built_in_rows(_list_reply())
            if line.startswith(f"- {_display(victim)} —")
        )
        assert " — off — " in row or row.endswith(" — off"), f"off beats not-available: {row}"
        assert "not available here" not in row
    finally:
        set_skill_enabled(victim, True)


def test_draft_rows_stay_off_and_ignore_the_disable_store() -> None:
    _draft(
        "shopping-list",
        "Use when the user asks for a shopping list.",
        "Sort items by aisle.",
    )
    listing = _list_reply()
    assert "**Drafts not turned on yet**" in listing
    assert "- Shopping list — off —" in listing, "a draft is not turned on, whatever the store says"


def _display(slug: str) -> str:
    from core.runtime_execution_tools import _display_name

    return _display_name(slug)


# ---------------------------------------------------------------------------
# Job B. 7. A short reply is not a name: yes/thanks words, fillers and
# thinking-aloud keep the ask open; only a short noun phrase names the skill.
# ---------------------------------------------------------------------------


def _ask_then_answer(session: str, *messages: str) -> tuple[list[tuple], object]:
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    calls: list[tuple] = []

    def fake(intent, arguments, **kwargs):
        calls.append((intent, dict(arguments)))
        return SimpleNamespace(
            response_text="Drafted.", status="ok", ok=True,
            details={"slug": arguments.get("name"), "sha256": "ab" * 32},
        )

    last = None
    with mock.patch("core.authorized_tool_execution.execute_authorized_runtime_tool", fake):
        maybe_handle_skill_request(
            _no_model_agent(),
            "make me a skill that reminds me to water my plants",
            session_id=session,
            source_context={"operating_mode": "auto"},
        )
        for message in messages:
            last = maybe_handle_skill_request(
                _no_model_agent(),
                message,
                session_id=session,
                source_context={"operating_mode": "auto"},
            )
    return calls, last


@pytest.mark.parametrize("reply", ["ok", "thanks", "sure", "yes", "hmm let me think"])
def test_a_yes_thanks_or_filler_reply_is_not_taken_as_the_name(reply: str) -> None:
    calls, _ = _ask_then_answer(f"hold-{reply.replace(' ', '-')}", reply, "Water plants")
    assert [c[0] for c in calls] == ["skill.create"], (
        f"{reply!r} must not create anything; only the real name after it does: {calls}"
    )
    assert calls[0][1].get("name") == "water-plants"


@pytest.mark.parametrize("reply", ["sounds good", "got it", "will do", "let me think", "give me a sec", "maybe"])
def test_acknowledgements_and_thinking_aloud_keep_the_ask_open(reply: str) -> None:
    calls, _ = _ask_then_answer(f"hold2-{reply.replace(' ', '-')}", reply, "Plant care")
    assert [c[0] for c in calls] == ["skill.create"]
    assert calls[0][1].get("name") == "plant-care"


def test_a_filler_before_a_real_name_still_names_it() -> None:
    calls, _ = _ask_then_answer("hold3", "hmm ok call it Plant care")
    assert [c[0] for c in calls] == ["skill.create"]
    assert calls[0][1].get("name") == "plant-care"


def test_a_bare_negative_lapses_the_ask_for_good() -> None:
    calls, second = _ask_then_answer("hold4", "nope", "Plant care")
    assert not calls, "nope ends the ask; a noun phrase afterwards must not wake it up"
    assert second is None or "skill.create" not in json.dumps(getattr(second, "response", ""))


def test_the_ask_line_comes_back_after_a_non_answer() -> None:
    calls, held = _ask_then_answer("hold5", "ok")
    assert not calls
    assert held is not None and ASK_FOR_NAME in held["response"], (
        "a non-answer re-offers the ask rather than going silent"
    )


# ---------------------------------------------------------------------------
# Job B. 8. Re-installing a newer version says Updated, not Added.
# ---------------------------------------------------------------------------


def _reinstall_twice() -> tuple[str, str]:
    created = _draft(
        "decline-politely",
        "Use when the user wants to say no to an invitation.",
        "Decline kindly and offer one other time.",
    )
    first = _install_reply({"path": created["path"]})
    again = _draft(  # the staging copy was retired by the install, so draft the revision afresh
        "decline-politely",
        "Use when the user wants to say no to an invitation.",
        "Decline kindly, offer one other time, and wish them well.",
    )
    second = _install_reply({"path": again["path"], "overwrite": True})
    return first, second


def test_reinstalling_a_newer_version_says_updated() -> None:
    first, second = _reinstall_twice()
    assert first == 'Added the skill "Decline politely". It is on from your next message.'
    assert second == 'Updated the skill "Decline politely". It is on from your next message.'


def test_the_updated_reply_holds_no_path() -> None:
    _ = None
    _, second = _reinstall_twice()
    assert "/" not in second and ".md" not in second and "version" not in second.lower()


def test_a_third_install_still_says_updated() -> None:
    def revision(body: str) -> str:
        return _draft(
            "decline-politely",
            "Use when the user wants to say no to an invitation.",
            body,
        )["path"]

    _install_reply({"path": revision("Decline kindly and offer two other times.")})
    _install_reply({"path": revision("Decline kindly and offer three other times."), "overwrite": True})
    reply = _install_reply({"path": revision("Decline kindly and offer four other times."), "overwrite": True})
    assert reply.startswith('Updated the skill "Decline politely".')


def test_an_updated_chat_scope_reinstall_keeps_its_scope_words() -> None:
    from core.context_namespace import ensure_chat_namespace
    from core.skill_scopes import scope_keys

    ensure_chat_namespace("chat-updated-words", project_id="project-updated-words")
    keys = scope_keys({"session_id": "chat-updated-words"})
    from core import skill_tools

    staged_dir = skill_tools._scoped_dirs("chat", keys)[0] / "meeting-actions"
    staged_dir.mkdir(parents=True, exist_ok=True)
    (staged_dir / "SKILL.md").write_text(
        '---\nname: "meeting-actions"\ndescription: "Use when the user pastes meeting notes."\n'
        'triggers: ["meeting notes"]\n---\nDo the chat job.\n',
        encoding="utf-8",
    )
    _install_reply({"path": "meeting-actions", "scope": "chat"}, {"session_id": "chat-updated-words"})
    staged_dir.mkdir(parents=True, exist_ok=True)  # the install retired the staged copy
    (staged_dir / "SKILL.md").write_text(
        '---\nname: "meeting-actions"\ndescription: "Use when the user pastes meeting notes."\n'
        'triggers: ["meeting notes"]\n---\nDo the chat job, better.\n',
        encoding="utf-8",
    )
    reply = _install_reply(
        {"path": "meeting-actions", "scope": "chat", "overwrite": True},
        {"session_id": "chat-updated-words"},
    )
    assert reply == 'Updated the skill "Meeting actions" for this chat. It is on from your next message.'


def test_the_version_record_advances_with_the_updated_reply() -> None:
    from core import skill_tools

    _reinstall_twice()
    installed = [
        row for row in skill_tools.list_skills()["installed_skills"]
        if row.get("name") == "decline-politely"
    ]
    assert installed, "the skill is installed"
    assert skill_tools.installed_skill_version_for_path(installed[0]["path"]) == 2
