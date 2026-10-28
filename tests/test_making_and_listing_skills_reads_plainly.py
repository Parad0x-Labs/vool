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

    assert re.search(r"You have \d+ built-in skills on\.", listing)
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
