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


# ---------------------------------------------------------------------------
# Job C: the no-model draft reads plainly, a colon after the place or the word
# "skill" reads like "that", and the sharing replies carry no engineering.
# ---------------------------------------------------------------------------

SHOPPING_REQUEST = (
    "Make me a skill for all of VOOL: whenever I give you a shopping list or a "
    "grocery list, sort it by supermarket aisle."
)


def _lane(session: str, text: str, context: dict | None = None, agent: SimpleNamespace | None = None):
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    return maybe_handle_skill_request(
        agent or _no_model_agent(),
        text,
        session_id=session,
        source_context=context or {"operating_mode": "auto", "session_id": session},
    )


def _trigger_words(reply: str) -> list[str]:
    match = re.search(r"\*\*Trigger words:\*\* (?P<line>[^\n]+)", reply)
    return [part.strip() for part in (match.group("line") if match else "").split(",") if part.strip()]


class TestTheNoModelDraftReadsPlainly:
    def test_when_it_applies_is_a_when_you_sentence(self) -> None:
        ask = _lane("jobc-words", SHOPPING_REQUEST)
        assert ASK_FOR_NAME in ask["response"]
        made = _lane("jobc-words", "Shopping list by aisle")
        assert '**When it applies:** When you give a shopping list or a grocery list.' in made["response"]

    def test_the_author_note_is_plain(self) -> None:
        _lane("jobc-note", SHOPPING_REQUEST)
        made = _lane("jobc-note", "Shopping list by aisle")
        assert "(written from your words, no AI model was connected)" in made["response"]
        assert "no model router" not in made["response"]

    def test_the_request_is_quoted_whole(self) -> None:
        _lane("jobc-quote", SHOPPING_REQUEST)
        made = _lane("jobc-quote", "Shopping list by aisle")
        assert f"> {SHOPPING_REQUEST}" in made["response"]
        assert "for :" not in made["response"], "scope words must not be cut out of the quote"

    def test_triggers_are_the_content_noun_phrases(self) -> None:
        _lane("jobc-triggers", SHOPPING_REQUEST)
        made = _lane("jobc-triggers", "Shopping list by aisle")
        assert set(_trigger_words(made["response"])) == {"shopping list", "grocery list", "supermarket aisle"}
        assert all("whenever" not in t and "give" not in t and "you" not in t for t in _trigger_words(made["response"]))

    def test_no_trigger_is_cut_across_a_comma_or_or(self) -> None:
        request = "make me a skill: when I paste a rehearsal schedule or a set list, build a run sheet"
        _lane("jobc-chunks", request)
        made = _lane("jobc-chunks", "Run sheet builder")
        words = _trigger_words(made["response"])
        assert words, "a draftable request still carries triggers"
        assert all(" or " not in t and "," not in t for t in words)
        assert all("when" not in t and "paste" not in t for t in words)

    def test_an_if_lead_also_reads_as_when_you(self) -> None:
        request = "make me a skill: if I send a rehearsal schedule, build a run sheet"
        _lane("jobc-if", request)
        made = _lane("jobc-if", "Run sheet builder")
        assert "**When it applies:** When you send a rehearsal schedule." in made["response"]

    def test_your_keeps_the_possessive(self) -> None:
        request = "make me a skill: whenever my sister sends her birthday list, total the gifts"
        _lane("jobc-your", request)
        made = _lane("jobc-your", "Birthday totals")
        assert "**When it applies:** When your sister sends her birthday list." in made["response"]

    def test_a_purpose_without_a_lead_keeps_the_use_when_shape(self) -> None:
        _lane("jobc-nolead", "make me a skill that reminds me to water my plants")
        made = _lane("jobc-nolead", "Plant care")
        assert re.search(r"\*\*When it applies:\*\* Use when the user asks to ", made["response"])

    def test_a_lone_content_chunk_contributes_no_trigger(self) -> None:
        request = "make me a skill: whenever I paste a phone number, format it"
        _lane("jobc-lone", request)
        made = _lane("jobc-lone", "Phone formatter")
        assert _trigger_words(made["response"]) == ["phone number"]


class TestAColonAfterThePlaceOrTheSkillWord:
    def test_a_colon_after_this_project_reads_like_that(self) -> None:
        from core.agent_runtime.fast_paths_skill import skill_request
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace("chat-jobc-project", project_id="project-jobc")
        request = skill_request("Make me a skill for this project: whenever I push a fix, update the changelog")
        assert request == {"action": "create", "name": "", "scope": "project"}

    def test_a_colon_after_the_skill_word_reads_like_that(self) -> None:
        from core.agent_runtime.fast_paths_skill import skill_request

        request = skill_request("Make me a skill: whenever I give you a shopping list, sort it by aisle")
        assert request == {"action": "create", "name": ""}

    def test_a_colon_after_this_chat_reads_like_that(self) -> None:
        from core.agent_runtime.fast_paths_skill import skill_request
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace("chat-jobc-chat")
        request = skill_request("make me a skill for this chat: whenever I paste a link, save it for later")
        assert request == {"action": "create", "name": "", "scope": "chat"}

    def test_this_project_colon_without_a_project_keeps_the_refusal(self) -> None:
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace("chat-jobc-noproject")  # a chat with no project bound
        first = _lane("chat-jobc-noproject", "Make me a skill for this project: whenever I push a fix, update the changelog")
        second = _lane("chat-jobc-noproject", "Changelog keeper")
        combined = (first or {}).get("response", "") + "\n" + (second or {}).get("response", "")
        assert "I can't keep a skill for this project" in combined

    def test_a_dash_after_the_skill_word_reads_like_that(self) -> None:
        from core.agent_runtime.fast_paths_skill import skill_request

        request = skill_request("make me a skill - whenever I send an invoice, log the total")
        assert request == {"action": "create", "name": ""}

    def test_an_em_dash_after_a_place_reads_like_that(self) -> None:
        from core.agent_runtime.fast_paths_skill import skill_request

        request = skill_request("make me a skill for all of VOOL — whenever I send an invoice, log the total")
        assert request == {"action": "create", "name": "", "scope": "global"}

    def test_a_colon_elsewhere_is_not_a_purpose_split(self) -> None:
        from core.agent_runtime.fast_paths_skill import skill_request

        request = skill_request("hey, quick one: make me a skill that sorts my lists")
        assert (request or {}).get("action") == "create"

    def test_a_thin_tail_after_the_colon_still_needs_the_name_and_purpose(self) -> None:
        from core.agent_runtime.fast_paths_skill import skill_request

        assert skill_request("make me a skill: something") == {"action": "needs_name", "name": ""}


def _two_versions_installed(slug: str = "shopping-list-by-aisle") -> None:
    _draft(slug, "Use when you give a shopping list or a grocery list.", "Sort the items by supermarket aisle.")
    _install_reply({"path": slug})
    _draft(slug, "Use when you give a shopping list or a grocery list.", "Sort the items by aisle; frozen goods last.")
    _install_reply({"path": slug, "overwrite": True})


class TestTheSharingRepliesReadPlainly:
    def test_the_rollback_reply(self) -> None:
        from core.runtime_execution_tools import _skill_rollback

        _two_versions_installed()
        result = _skill_rollback({"skill": "shopping-list-by-aisle", "version": 1})
        assert result.status == "ok", result.response_text
        assert result.response_text == (
            'Restored version 1 of the skill "Shopping list by aisle". It is used from your next message.'
        )

    def test_the_version_list_reply(self) -> None:
        _two_versions_installed()
        reply = _lane("jobc-versions", "roll back the shopping-list-by-aisle skill")
        assert reply["response"] == 'The skill "Shopping list by aisle" has versions 1, 2. Which one should I restore?'

    def test_the_export_reply(self) -> None:
        from core.runtime_execution_tools import _skill_share_tool

        _two_versions_installed()
        result = _skill_share_tool("skill.export", {"name": "shopping-list-by-aisle"}, {"session_id": "jobc-export"})
        assert result.status == "ok", result.response_text
        path = Path(str(result.details.get("path") or ""))
        assert result.response_text == (
            f'Saved the skill "Shopping list by aisle" as the file {path.name}, '
            f"in this folder: {path.parent}. Send that file to share it."
        )

    def test_the_import_reply(self) -> None:
        from core.runtime_execution_tools import _skill_share_tool

        _two_versions_installed()
        zipped = _skill_share_tool("skill.export", {"name": "shopping-list-by-aisle"}, {"session_id": "jobc-import"})
        assert zipped.status == "ok", zipped.response_text
        imported = _skill_share_tool(
            "skill.import", {"path": str(zipped.details.get("path"))}, {"session_id": "jobc-import"}
        )
        assert imported.status == "ok", imported.response_text
        assert imported.response_text == (
            'This file holds the skill "Shopping list by aisle", ready for all of VOOL. '
            "It is not on yet: say turn it on."
        )

    def test_turn_it_on_after_an_import_still_installs(self, tmp_path) -> None:
        from core.mode_permission_policy import PermissionAction, grant_internal_authority
        from core.runtime_execution_tools import _skill_share_tool

        _two_versions_installed()
        zipped = _skill_share_tool("skill.export", {"name": "shopping-list-by-aisle"}, {"session_id": "jobc-follow"})
        zip_path = str(zipped.details.get("path"))
        imported = _lane("jobc-follow", f"import this skill {zip_path} for all of VOOL")
        assert imported is not None and 'holds the skill "Shopping list by aisle"' in imported["response"]
        assert "turn it on" in imported["response"].lower()
        token = grant_internal_authority(
            label="jobc-follow",
            actions=[
                PermissionAction.CREATE_FILES,
                PermissionAction.MODIFY_FILES,
                PermissionAction.OVERWRITE_EXISTING_FILES,
                PermissionAction.CHANGE_SETTINGS,
            ],
            intents=["skill.install"],
            session_id="jobc-follow",
            duration_seconds=300,
        )
        on = _lane(
            "jobc-follow", "turn it on",
            {"operating_mode": "manual", "session_id": "jobc-follow", "internal_authority_token": token},
        )
        # The zip carried version history, so turning it on over the v1+v2 install says Updated.
        assert on is not None and 'Updated the skill "Shopping list by aisle"' in on["response"]

    def test_turn_it_on_after_a_restart_reads_the_new_offer_from_history(self) -> None:
        from core.agent_runtime import fast_paths_skill
        from core.runtime_execution_tools import _skill_share_tool

        _two_versions_installed()
        zipped = _skill_share_tool("skill.export", {"name": "shopping-list-by-aisle"}, {"session_id": "jobc-restart"})
        zip_path = str(zipped.details.get("path"))
        imported = _lane("jobc-restart", f"import this skill {zip_path} for all of VOOL")
        assert imported is not None and 'ready for all of VOOL' in imported["response"]
        fast_paths_skill._PENDING.clear()  # a restart: only the words on screen remain
        from core.mode_permission_policy import PermissionAction, grant_internal_authority

        token = grant_internal_authority(
            label="jobc-restart",
            actions=[
                PermissionAction.CREATE_FILES,
                PermissionAction.MODIFY_FILES,
                PermissionAction.OVERWRITE_EXISTING_FILES,
                PermissionAction.CHANGE_SETTINGS,
            ],
            intents=["skill.install"],
            session_id="jobc-restart",
            duration_seconds=300,
        )
        context = {
            "operating_mode": "manual",
            "session_id": "jobc-restart",
            "internal_authority_token": token,
            "conversation_history": [{"role": "assistant", "content": imported["response"]}],
        }
        on = _lane("jobc-restart", "turn it on", context)
        assert on is not None and 'Updated the skill "Shopping list by aisle"' in on["response"]

    def test_turning_off_a_built_in_says_it_comes_with_vool(self) -> None:
        with mock.patch(
            "core.authorized_tool_execution.execute_authorized_runtime_tool",
            lambda *a, **k: pytest.fail("a built-in turn-off must not dispatch skill.remove"),
        ):
            reply = _lane("jobc-builtin", "turn off the answer-presentation skill")
        assert reply is not None
        assert reply["response"] == "Answer presentation comes with VOOL and cannot be turned off here."

    def test_turning_off_a_skill_you_made_still_turns_it_off(self) -> None:
        from core.mode_permission_policy import PermissionAction, grant_internal_authority

        _draft("decline-politely", "Use when the user wants to say no to an invitation.", "Decline kindly.")
        _install_reply({"path": "decline-politely"})
        # Auto mode itself gates skill.remove behind an approval; one grant stands in for it, the
        # same way the repo's own lane tests drive the remove lane.
        token = grant_internal_authority(
            label="jobc-remove",
            actions=[PermissionAction.MODIFY_FILES, PermissionAction.CHANGE_SETTINGS, PermissionAction.DELETE_FILES],
            intents=["skill.remove"],
            session_id="jobc-remove",
            duration_seconds=300,
        )
        reply = _lane(
            "jobc-remove",
            "turn off the decline-politely skill",
            {"operating_mode": "manual", "session_id": "jobc-remove", "internal_authority_token": token},
        )
        assert reply is not None and "Turned off" in reply["response"]

    def test_the_rollback_reply_holds_no_slug_or_engineering(self) -> None:
        from core.runtime_execution_tools import _skill_rollback

        _two_versions_installed()
        result = _skill_rollback({"skill": "shopping-list-by-aisle", "version": 1})
        assert "`" not in result.response_text
        assert "byte for byte" not in result.response_text and "loader" not in result.response_text.lower()

    def test_an_import_for_this_chat_says_its_scope(self) -> None:
        from core.context_namespace import ensure_chat_namespace
        from core.runtime_execution_tools import _skill_share_tool

        _two_versions_installed()
        ensure_chat_namespace("chat-jobc-scope")
        zipped = _skill_share_tool("skill.export", {"name": "shopping-list-by-aisle"}, {"session_id": "chat-jobc-scope"})
        imported = _skill_share_tool(
            "skill.import",
            {"path": str(zipped.details.get("path")), "scope": "chat"},
            {"session_id": "chat-jobc-scope"},
        )
        assert imported.status == "ok", imported.response_text
        assert imported.response_text == (
            'This file holds the skill "Shopping list by aisle", ready for this chat. '
            "It is not on yet: say turn it on."
        )


# ---------------------------------------------------------------------------
# Job C: the whole guide, walked in its own order, in-process, no model
# ---------------------------------------------------------------------------


def test_the_guide_walk_from_making_to_sharing(tmp_path, monkeypatch) -> None:
    """make, name, allow, turn on, try, list, change, turn on, roll back, share, turn off,
    import the zip in a second profile, turn on — one conversation, the real tools, Manual mode."""
    from core import skill_tools
    from core.agent_runtime import fast_paths_skill
    from core.mode_permission_policy import PermissionAction, grant_internal_authority, resolve_approval
    from core.plugin_catalog import invalidate_storage_listing
    from core.tool_offer_assembly import skill_guidance_for

    chat = "guide-walk"
    agent = _no_model_agent()

    def turn(text: str, context: dict) -> dict:
        outcome = fast_paths_skill.maybe_handle_skill_request(agent, text, session_id=chat, source_context=context)
        assert outcome is not None, text
        return outcome

    # 1-2. make + name: the ask, then the answer raises the approval the guide's "allow" resolves.
    manual = {"session_id": chat, "operating_mode": "manual"}
    ask = turn(SHOPPING_REQUEST, manual)
    assert ASK_FOR_NAME in ask["response"]
    pending = turn("Shopping list by aisle", manual)
    approval = dict(pending.get("approval_request") or {})
    assert approval.get("approval_id"), pending["response"]

    # 3. allow: the operator resolves it and the same answer replays with the token.
    assert resolve_approval(str(approval["approval_id"]), decision="allow") is not None
    replay = dict(manual, mode_approval_token=str(approval["approval_id"]))
    drafted = turn("Shopping list by aisle", replay)
    assert "(written from your words, no AI model was connected)" in drafted["response"]

    # One grant covers the remaining mutations the walk performs, like the guide's "keep going".
    token = grant_internal_authority(
        label="guide-walk",
        actions=[
            PermissionAction.READ_FILES,
            PermissionAction.CREATE_FILES,
            PermissionAction.MODIFY_FILES,
            PermissionAction.OVERWRITE_EXISTING_FILES,
            PermissionAction.CHANGE_SETTINGS,
            PermissionAction.DELETE_FILES,
        ],
        intents=["skill.create", "skill.install", "skill.rollback", "skill.export", "skill.import", "skill.remove"],
        session_id=chat,
        duration_seconds=600,
    )
    manual = {"session_id": chat, "operating_mode": "manual", "internal_authority_token": token}

    # 4. turn on.
    on = turn("turn it on", manual)
    assert on["response"].startswith('Added the skill "Shopping list by aisle"'), on["response"]

    # 5. try: the live prompt seam carries the skill's instruction.
    served = skill_guidance_for("here is my shopping list: milk, bread, peas", source_context={"session_id": chat})
    assert "supermarket aisle" in served.text

    # 6. list.
    listing = turn("which skills do I have?", manual)
    assert listing["response"].count("Shopping list by aisle") == 1
    assert "- Shopping list by aisle — on —" in listing["response"]

    # 7-8. change, turn on again.
    turn("change the shopping-list-by-aisle skill: also flag anything frozen", manual)
    on_again = turn("turn it on", manual)
    assert on_again["response"].startswith('Updated the skill "Shopping list by aisle"'), on_again["response"]

    # 9. roll back to version 1.
    restored = turn("roll back the shopping-list-by-aisle skill to version 1", manual)
    assert restored["response"] == (
        'Restored version 1 of the skill "Shopping list by aisle". It is used from your next message.'
    )

    # 10. share.
    shared = turn("export the shopping-list-by-aisle skill", manual)
    assert shared["response"].startswith('Saved the skill "Shopping list by aisle" as the file ')
    zip_path = Path(str((shared.get("details") or {}).get("path") or ""))
    assert zip_path.is_file()

    # 11. turn off.
    off = turn("turn off the shopping-list-by-aisle skill", manual)
    assert "Turned off" in off["response"]
    # Off reads through the live seam; the staged revision the change step left is kept by design.
    gone = skill_guidance_for("here is my shopping list: milk, bread, peas", source_context={"session_id": chat})
    assert "supermarket aisle" not in gone.text

    # 12. import the zip in a second temp profile.
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "profile2-plugins"))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile2-voolhome"))
    invalidate_storage_listing()
    imported = fast_paths_skill.maybe_handle_skill_request(
        agent, f"import this skill {zip_path} for all of VOOL", session_id="profile2-chat",
        source_context={"operating_mode": "auto", "session_id": "profile2-chat"},
    )
    assert imported is not None, "the import lane must claim the zipped skill"
    assert 'This file holds the skill "Shopping list by aisle", ready for all of VOOL.' in imported["response"]

    # 13. turn on, in the second profile.
    token2 = grant_internal_authority(
        label="guide-walk-profile2",
        actions=[
            PermissionAction.CREATE_FILES,
            PermissionAction.MODIFY_FILES,
            PermissionAction.OVERWRITE_EXISTING_FILES,
            PermissionAction.CHANGE_SETTINGS,
        ],
        intents=["skill.install"],
        session_id="profile2-chat",
        duration_seconds=300,
    )
    on2 = fast_paths_skill.maybe_handle_skill_request(
        agent, "turn it on", session_id="profile2-chat",
        source_context={
            "operating_mode": "manual", "session_id": "profile2-chat", "internal_authority_token": token2,
        },
    )
    assert on2 is not None and on2["response"].startswith('Added the skill "Shopping list by aisle"'), on2["response"]
