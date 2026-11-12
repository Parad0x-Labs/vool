"""Skills, as a person using VOOL for everyday things meets them.

Found on 0.7.5 by a fresh-eyes tester, and re-measured on the 0.7.7 delivery:

1. "which skills are installed" listed the skills VOOL is BUILT with (release-gate, ci-repair,
   code-review ...). They are not a person's skills: the list leaves them out unless the
   developer setting (VOOL_DEVELOPER_SKILLS=1) is on, and a workspace that is VOOL's own
   program folder does not add its skill source files as "Made by you".
2. An everyday letter ("polite complaint to my landlord, heating broken") is classed as
   debugging, and that alone seated three developer skills into the reply and pushed an
   installed letter skill out. A developer skill now needs the words to ask for what it does.
3. A skill asked for by a name VOOL does not have gets "I don't have a skill called X" and the
   ones the person has, never a brush-off; a Browse skill that is not added says so and where
   to add it; a turn-off of a skill that was never added says nothing changed.
4. "make me a skill" walks the person through it in plain words, with no file names.
5. My add-ons > Skills shows each skill's on/off state with a button that uses the one
   disable store.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    from core.plugin_catalog import invalidate_storage_listing

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins-root"))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "voolhome"))
    monkeypatch.delenv("VOOL_DEVELOPER_SKILLS", raising=False)
    invalidate_storage_listing()
    yield home
    invalidate_storage_listing()


def _listing(workspace: str = "") -> str:
    from core.runtime_execution_tools import _skill_list

    result = _skill_list({"workspace": workspace})
    assert result.status == "ok", result.response_text
    return result.response_text


def _ask(text: str) -> dict | None:
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    agent = SimpleNamespace(
        _fast_path_result=lambda **kw: {"response": kw.get("response"), "reason": kw.get("reason")}
    )
    return maybe_handle_skill_request(
        agent, text, session_id="people-skills", source_context={"session_id": "people-skills"}
    )


# --------------------------------------------------------------------------- 1. the list


DEVELOPER_ROWS = ("Release gate", "Ci repair", "Code review", "Root cause repair", "Security audit")


def test_the_list_does_not_show_the_skills_vool_is_built_with() -> None:
    listing = _listing()

    for row in DEVELOPER_ROWS:
        assert f"- {row} —" not in listing, f"{row} is a developer skill: {listing}"
    assert "- X editorial studio —" in listing, "an everyday built-in stays listed"
    assert "Browse" in listing, "the list says where more skills are"
    assert "/" not in listing and ".md" not in listing


def test_the_developer_setting_brings_them_back(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_DEVELOPER_SKILLS", "1")

    listing = _listing()

    assert "- Release gate —" in listing
    assert "- Ci repair —" in listing


def test_vools_own_program_folder_as_the_workspace_adds_nothing_as_made_by_you() -> None:
    listing = _listing(str(REPO))

    assert "**Made by you**" not in listing, listing[:400]
    assert "Vool release gate" not in listing and "Vool ci repair" not in listing


def test_a_skill_in_the_persons_own_workspace_is_still_listed(tmp_path) -> None:
    project = tmp_path / "my-project" / "skills" / "tidy-receipts"
    project.mkdir(parents=True)
    (project / "SKILL.md").write_text(
        "---\nname: tidy-receipts\ndescription: Sort my receipts by month.\n---\nSort them.\n",
        encoding="utf-8",
    )

    listing = _listing(str(tmp_path / "my-project"))

    assert "- Tidy receipts —" in listing


def test_each_row_reads_as_one_short_phrase() -> None:
    for line in _listing().splitlines():
        if line.startswith("- "):
            assert len(line) < 220, line


# --------------------------------------------------------------------------- 2. the picker


LETTER = "i need to write a polite complaint to my landlord, heating broken 3 weeks"


def _picked(text: str, task_class: str) -> list[str]:
    from core.tool_offer_assembly import skill_guidance_for

    guidance = skill_guidance_for(text, task_class=task_class, source_context={"session_id": "pick"})
    return [str(row.get("id") or row.get("name")) for row in guidance.skills if row.get("complete", True)]


def test_an_everyday_letter_classed_as_debugging_seats_no_developer_skill() -> None:
    from core.native_skill_library import is_developer_skill

    picked = _picked(LETTER, "debugging")

    assert not [name for name in picked if is_developer_skill(name)], picked


def test_with_the_developer_setting_the_same_turn_seats_them_again(monkeypatch) -> None:
    from core.native_skill_library import is_developer_skill

    monkeypatch.setenv("VOOL_DEVELOPER_SKILLS", "1")

    assert [name for name in _picked(LETTER, "debugging") if is_developer_skill(name)]


def test_words_that_ask_for_test_runs_still_reach_a_developer_skill() -> None:
    from core.native_skill_library import is_developer_skill

    picked = _picked("run the test suite in my project and tell me which tests fail", "shell_guidance")

    assert [name for name in picked if is_developer_skill(name)], picked


DEVELOPER_IDS = {
    "ci-repair", "code-review", "cumulative-testing", "database", "feature-build", "migration",
    "performance", "project-archaeology", "release-gate", "repo-onboarding", "root-cause-repair",
    "security-audit",
}


def test_the_skills_vool_is_built_with_are_marked_for_developers_in_their_own_files() -> None:
    from core.native_skill_library import is_developer_skill, load_native_library

    contracts = {contract.id: contract for contract in load_native_library().contracts}
    marked = {skill_id for skill_id, contract in contracts.items() if contract.audience == "developer"}
    assert marked == DEVELOPER_IDS, sorted(marked ^ DEVELOPER_IDS)
    assert all(is_developer_skill(skill_id) for skill_id in DEVELOPER_IDS)
    assert not is_developer_skill("x-editorial-studio")
    assert not is_developer_skill("media-studio")


@pytest.mark.parametrize(
    "text",
    [
        "A unit test in my project fails after my change. Diagnose the root cause.",
        "audit this module for security issues",
    ],
)
def test_a_developers_own_words_still_reach_the_developer_skills(text: str) -> None:
    from core.native_skill_library import is_developer_skill

    assert [name for name in _picked(text, "debugging") if is_developer_skill(name)]


# --------------------------------------------------------------------------- 3. by name


def test_a_skill_that_does_not_exist_is_named_and_the_real_ones_offered() -> None:
    turn = _ask("use the teleport-to-mars skill please")

    assert turn is not None, "a made-up skill name must not reach the model as a brush-off"
    reply = str(turn["response"])
    assert reply.startswith("I don't have a skill called Teleport to mars.")
    assert "Here are the ones you have:" in reply and "X editorial studio" in reply
    assert "Release gate" not in reply
    assert "can't assist" not in reply


def test_turning_off_a_browse_skill_that_was_never_added_claims_nothing() -> None:
    turn = _ask("turn off the cover letter writer skill")

    assert turn is not None
    reply = str(turn["response"])
    assert "isn't added" in reply and "Nothing changed" in reply
    assert "now turned off" not in reply


def test_using_a_browse_skill_that_is_not_added_says_where_to_add_it() -> None:
    turn = _ask("use the bill explainer skill on this: standing charge 17.14, total 109.29")

    assert turn is not None
    assert "Bill explainer isn't added yet" in str(turn["response"])
    assert "Browse" in str(turn["response"])


def test_a_skill_the_person_has_goes_on_to_be_used() -> None:
    assert _ask("use the x editorial studio skill to write a post about my cafe") is None


def test_turning_a_built_in_off_by_name_points_to_its_button() -> None:
    turn = _ask("turn off the lens ux skill")

    assert turn is not None
    assert "My add-ons" in str(turn["response"])
    assert "turned off" not in str(turn["response"])


@pytest.mark.parametrize(
    "text",
    [
        "use the skills you have to plan a trip",
        "which skill should i use for a letter?",
        "can you use the right skill for this",
    ],
)
def test_sentences_that_only_mention_skills_are_left_alone(text: str) -> None:
    assert _ask(text) is None


# --------------------------------------------------------------------------- 4. making one


def test_make_me_a_skill_walks_through_it_in_plain_words() -> None:
    turn = _ask("make me a skill")

    assert turn is not None
    reply = str(turn["response"])
    assert "in your own words" in reply
    assert "turn it on" in reply
    assert "`" not in reply
    assert not re.search(r"\w+\.(?:md|py|json)\b", reply), reply
    assert "/" not in reply


# --------------------------------------------------------------------------- 5. the add-ons page


def test_the_add_ons_catalogue_leaves_out_developer_skills() -> None:
    from core.native_skill_library import is_developer_skill
    from core.plugin_catalog import _native_library_entry

    entry = _native_library_entry()
    assert entry is not None
    names = [skill["name"] for skill in entry["skills"]]
    assert names and not [name for name in names if is_developer_skill(name)], names


def test_an_installed_skill_carries_its_on_off_state(tmp_path) -> None:
    from core.native_skill_library import set_skill_enabled
    from core.plugin_catalog import _plugin_entry
    from core import skill_tools

    created = skill_tools.create_skill(
        name="decline-politely", description="Use when saying no to an invitation.", body="Decline kindly."
    )
    assert created["status"] == "ok", created
    assert skill_tools.install_skill(created["path"])["status"] == "ok"
    pack = skill_tools.plugins_root() / "plugins" / "local-skills"

    def state() -> bool:
        entry = _plugin_entry(pack)
        assert entry is not None
        return next(s for s in entry["skills"] if s["name"] == "decline-politely")["enabled"]

    assert state() is True
    entry = _plugin_entry(pack)
    assert entry is not None and '"' not in str(entry["skills"][0]["name"]), "the name is shown without quotes"
    assert set_skill_enabled("decline-politely", False)["status"] == "ok"
    try:
        assert state() is False
    finally:
        set_skill_enabled("decline-politely", True)


def test_the_skills_panel_has_an_on_off_button_that_uses_the_skill_store() -> None:
    page = (REPO / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
    render = page.split("function renderSkills(d, query) {", 1)[1].split("\nfunction ", 1)[0]
    assert "toggleSkill(s.name, off, tg, st)" in render
    assert "p-toggle" in render
    # The button speaks the person's language and names its skill for a screen reader.
    assert "pageT('plugins.enable', 'Enable')" in render and "pageT('plugins.disable', 'Disable')" in render
    assert "aria-label" in render
    toggle = page.split("async function toggleSkill(id, enable, btn, status) {", 1)[1].split("\n}\n", 1)[0]
    assert "'/api/skills/enable'" in toggle
    # While it works the button is held and the card says so; a failure is said, never silent.
    assert "btn.disabled = true" in toggle and "toast(pageT('addons.request_failed'" in toggle


def test_the_shipped_database_pack_does_not_list_its_developer_skill(monkeypatch) -> None:
    from core.plugin_catalog import _plugin_entry

    pack = REPO / "plugins" / "vool-database"
    entry = _plugin_entry(pack, bundled=True)
    assert entry is not None and entry["skills"] == [], entry["skills"]
    monkeypatch.setenv("VOOL_DEVELOPER_SKILLS", "1")
    entry = _plugin_entry(pack, bundled=True)
    assert entry is not None and [s["name"] for s in entry["skills"]] == ["vool-database"]
    # An external pack cannot hide a skill from the person with the developer mark.
    monkeypatch.delenv("VOOL_DEVELOPER_SKILLS")
    entry = _plugin_entry(pack, bundled=False)
    assert entry is not None and [s["name"] for s in entry["skills"]] == ["vool-database"]


@pytest.mark.parametrize(
    "text",
    [
        "what skills do you have? and how do i add or turn one off",
        "which skills are installed? how can i get more",
    ],
)
def test_the_list_answers_a_question_that_also_asks_how_to_add_or_turn_off(text: str) -> None:
    from core.agent_runtime.fast_paths_skill import skill_request

    assert (skill_request(text) or {}).get("action") == "list"


@pytest.mark.parametrize("text", ["how do i add a skill", "what skills do you have and how do i make a cake"])
def test_other_how_questions_are_not_taken_for_the_list(text: str) -> None:
    from core.agent_runtime.fast_paths_skill import skill_request

    assert (skill_request(text) or {}).get("action") != "list"
