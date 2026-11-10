"""What a non-technical person reads about skills: plain words, the real page, the real ask.

Each test pins one thing a person saw go wrong on the Mac app (older build), and can fail:

1. Skills & Plugins > My add-ons > Skills showed descriptions written for skill picking
   ("Answer presentation doctrine: choose prose ...", "Drive a real, isolated browser: open a
   disposable-profile session ..."). Every skill a person can see now carries a plain `summary`
   line, the catalog serves it, and the chat list uses it.
2. The built-in group read "Native Skill Library -- The skill packages shipped with the runtime
   itself", and its Disable button wrote a setting nothing reads.
3. "what skills do you have, and how would I add one of my own?" got a generic chatbot answer:
   "how would i" sent it to the question gate before the list was considered.
4. "how do I add a skill?" had no fixed answer naming Browse or the make-a-skill ask.
5. "turn off the browser skill" asked "Which skill should I turn off?" for a skill VOOL has.
6. Answers never said which skill shaped them: the page now reads this turn's own ledger rows.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]

# Words that belong to the people who build VOOL, not to the people who use it.
DEVELOPER_WORDS = re.compile(
    r"\b(?:doctrine|bounded|untrusted|receipt|envelope|contract|runtime|seam|lane|typed|"
    r"disposable-profile|evidence-first|verbatim|XDraft)\b",
    re.IGNORECASE,
)


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


def _no_model_agent() -> SimpleNamespace:
    return SimpleNamespace(
        memory_router=None,
        _fast_path_result=lambda **kw: {"response": kw.get("response"), "reason": kw.get("reason")},
    )


def _lane(text: str, session: str = "plain-people"):
    from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request

    return maybe_handle_skill_request(
        _no_model_agent(), text, session_id=session,
        source_context={"operating_mode": "auto", "session_id": session},
    )


def _native_entry() -> dict:
    from core.plugin_catalog import read_plugin_catalog

    catalog = read_plugin_catalog()
    entries = [p for p in catalog["plugins"] if p["id"] == "native-library"]
    assert len(entries) == 1, [p["id"] for p in catalog["plugins"]]
    return entries[0]


# ---------------------------------------------------------------------------
# 1-2. The Skills page
# ---------------------------------------------------------------------------


def test_every_skill_a_person_sees_has_a_plain_summary() -> None:
    entry = _native_entry()
    assert entry["skills"], "no built-in skill is listed for a person"
    for skill in entry["skills"]:
        summary = skill.get("summary") or ""
        assert summary, f"{skill['name']} has no summary line; the page would show its picking description"
        assert len(summary) <= 140, (skill["name"], summary)
        assert not DEVELOPER_WORDS.search(summary), (skill["name"], summary)


def test_the_summary_is_display_only_and_leaves_the_description_alone() -> None:
    """Picking compares `description`; the person-facing line must not have replaced it."""
    from core.native_skill_library import skill_inventory

    by_id = {row["id"]: row for row in skill_inventory()}
    for skill in _native_entry()["skills"]:
        assert skill["description"] == by_id[skill["name"]]["description"]
        assert skill["summary"] != skill["description"]


def test_the_built_in_group_reads_plainly() -> None:
    entry = _native_entry()
    assert entry["name"] == "Built-in skills"
    assert "runtime" not in entry["description"].lower()
    assert entry["category"] == "Built in"


def test_the_page_shows_the_summary_a_plain_title_and_no_group_switch() -> None:
    source = (REPO / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
    start = source.index("function renderSkills(d, query) {")
    skills_js = source[start:source.index("async function togglePlugin(", start)]
    assert "description: s.summary || s.description" in skills_js
    assert "nm.textContent = s.title || s.name" in skills_js
    plugins_js = source[source.index("function renderPlugins(d, query) {"):start]
    # The built-in group has no off switch on the server, so the page offers none.
    assert "pl.id === 'native-library'" in plugins_js
    badge = source[source.index("function _firstPartyBadge() {"):source.index("function _skillTitle(")]
    assert "parad0x" not in badge.lower()


# ---------------------------------------------------------------------------
# 1. The chat list uses the same plain lines
# ---------------------------------------------------------------------------


def test_the_chat_list_uses_the_summary_not_the_picking_description() -> None:
    from core.runtime_execution_tools import _skill_list

    listing = _skill_list({}).response_text
    assert "Picks the clearest shape for an answer" in listing
    assert "doctrine" not in listing.lower()
    assert "disposable-profile" not in listing
    assert "Skills & Plugins, under Browse" in listing
    assert "To make your own, ask me: make me a skill that ..." in listing


# ---------------------------------------------------------------------------
# 3-5. Chat answers
# ---------------------------------------------------------------------------


def test_the_question_asked_on_the_mac_app_gets_the_real_list() -> None:
    from core.agent_runtime.fast_paths_skill import skill_request

    asked = "what skills do you have, and how would I add one of my own?"
    assert (skill_request(asked) or {}).get("action") == "list"
    reply = _lane(asked)
    assert reply is not None and reply["reason"] == "skill_list"
    assert "**Built in**" in reply["response"]
    assert "make me a skill that" in reply["response"]


@pytest.mark.parametrize(
    "asked",
    [
        "how do I add a skill?",
        "How would I make my own skill?",
        "how can I add more skills to VOOL",
        "how would i create a skill?",
    ],
)
def test_how_to_add_a_skill_has_one_plain_answer_and_authors_nothing(asked: str) -> None:
    from core.agent_runtime.fast_paths_skill import skill_request

    # Still not a skill REQUEST: nothing is drafted, saved or turned on.
    assert skill_request(asked) is None
    with mock.patch(
        "core.authorized_tool_execution.execute_authorized_runtime_tool",
        lambda *a, **k: pytest.fail("a how-to question must not run a skill tool"),
    ):
        reply = _lane(asked)
    assert reply is not None and reply["reason"] == "skill_how_to_add"
    body = reply["response"]
    assert "Skills & Plugins" in body and "Browse" in body
    assert "make me a skill that" in body
    assert "Eyebrow key" in body, "Browse needs a key; the answer must not hide that"


@pytest.mark.parametrize("asked", ["how do i make a skill that sorts my email", "how do I add a row to a table?"])
def test_other_how_questions_are_left_alone(asked: str) -> None:
    reply = _lane(asked)
    assert reply is None or reply["reason"] != "skill_how_to_add"


def test_turning_off_a_one_word_built_in_points_to_its_button() -> None:
    with mock.patch(
        "core.authorized_tool_execution.execute_authorized_runtime_tool",
        lambda *a, **k: pytest.fail("a built-in turn-off must not dispatch skill.remove"),
    ):
        reply = _lane("turn off the browser skill")
    assert reply is not None
    assert reply["response"] == "You can turn Browser on or off in Skills & Plugins, under My add-ons, Skills."


def test_an_unknown_one_word_skill_is_named_back_with_the_ones_you_have() -> None:
    reply = _lane("turn off the teleport skill")
    assert reply is not None
    assert reply["response"].startswith("I don't have a skill called Teleport.")


# ---------------------------------------------------------------------------
# 6. "Skill used" under an answer, executed under node
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


def _skills_used(events, turn_id):
    source = (REPO / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
    start = source.index("function skillsUsedFrom(events, turnId) {")
    end = source.index("function paintSkillsUsed(", start)
    script = (
        source[start:end]
        + f"\nprocess.stdout.write(JSON.stringify(skillsUsedFrom({json.dumps(events)}, {json.dumps(turn_id)})));\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _offer(turn, *rows, flattened=True):
    event = {"event_type": "tool_offer_skills", "seq": 7}
    if turn is not None:
        event["client_turn_id"] = turn
    skills = [dict(name=name, **extra) for name, extra in rows]
    if flattened:
        event["skills"] = skills
    else:
        event["details"] = {"skills": skills}
    return event


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_note_names_only_this_turns_skills_in_plain_words() -> None:
    events = [
        _offer("t-1", ("vool-browser", {"lane": "task"}), ("shopping-list-by-aisle", {"lane": ""})),
        # the second emitter repeats a row: named once
        _offer("t-1", ("vool-browser", {"lane": "task"}), flattened=False),
        # another turn, and an untagged row, never lend their skill to this answer
        _offer("t-0", ("decline-politely", {})),
        _offer(None, ("cover-letter-writer", {})),
        # a lens rides most turns; a dropped or cut body never reached the model
        _offer("t-1", ("vool-lens-ux", {"lane": "doctrine"}), ("tldr", {"complete": False}), ("budget", {"dropped": "budget"})),
        {"event_type": "tool_executed", "client_turn_id": "t-1", "skills": [{"name": "nope"}]},
    ]
    assert _skills_used(events, "t-1") == ["Browser", "Shopping list by aisle"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_no_turn_id_or_no_skill_rows_means_no_note() -> None:
    assert _skills_used([_offer("t-1", ("vool-browser", {}))], "") == []
    assert _skills_used([], "t-1") == []


def test_the_note_is_painted_when_the_answer_ends_and_when_the_chat_is_shown_again() -> None:
    source = (REPO / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
    finish = source.index("recordAssistantMessage(run, assistant, run.displayMetadata);")
    assert "noteSkillsUsed(run);" in source[finish:finish + 600]
    repaint = source.index("function paintRunState(run) {")
    assert "paintSkillsUsed(run.textEl, run.skillsUsed)" in source[repaint:repaint + 800]
