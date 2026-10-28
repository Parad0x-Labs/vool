"""Contract tests for the leaner clean local loop: the shorter prompt keeps every rule, and
deterministic read asks get their evidence staged before the first model call, so one call
answers them. The staging tests stay red until the look-first pre-step lands in tools."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.clean_loop import tools
from core.clean_loop.loop import run_loop
from core.clean_loop.prompt import build_system_prompt
from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import install_builtin_tools

OWNER = {"surface": "cli"}

#: One plain-word fragment per rule the prompt must never lose, whatever its wording.
RULE_FRAGMENTS = (
    "never state", "not seen", "look first", "read the file", "before answering", "never guess",
    "two or three words", "name files exactly", "never repeat", "failed", "say so",
    "one short question", "never delete", "do not mention", "rules",
)

NEVER_STAGES = (
    "how much do i owe the plumber?", "whats on my to do list",
    "im looking for that boarding pass thing, cant remember what i named it",
    "where did i put my cv? the 2026 one",
    "make me a shopping list file on the desktop: milk, bread",
    "can you move the cat photo from Pictures to my Desktop?", "yes please", "ok", "undo",
    "how big is my desktop folder", "whats a dmg file? my downloads is full of them",
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = (tmp_path / "home").resolve()
    for folder in ("Desktop", "Downloads", "Documents", "Pictures"):
        (root / folder).mkdir(parents=True)
    (root / "Desktop" / "todo.txt").write_text("1. renew car insurance\n", encoding="utf-8")
    (root / "Downloads" / "plumber_invoice.txt").write_text("Plumber invoice\nTotal due: EUR 140.00\n", encoding="utf-8")
    (root / "Downloads" / "boarding-pass-VNO-LIS.pdf").write_bytes(b"%PDF-1.4 boarding pass")
    (root / "Documents" / "flat viewing notes.md").write_text("Flat A 950, Flat B 1020\n", encoding="utf-8")
    (root / "Documents" / "CV_2026.pdf").write_bytes(b"%PDF-1.4 cv")
    (root / "Pictures" / "cat_on_sofa.jpg").write_bytes(b"jpg")
    monkeypatch.setenv("HOME", str(root))
    return root


def _registry() -> Registry:
    registry = Registry()
    install_builtin_tools(registry)
    return registry


def _ctx(home: Path) -> ToolContext:
    return ToolContext(home=home, source_context=dict(OWNER))


def _look_first():
    """The look-first pre-step the way the loop reaches it: the module symbol once it lands,
    else the non-privacy pre-step install_builtin_tools registers."""
    step = getattr(tools, "look_first_pre_step", None)
    if step is None:
        step = next((run for name, run in _registry().pre_steps() if name != "privacy"), None)
    assert step is not None, "no look-first pre-step is registered"
    return step


def _scripted(replies: list[str]):
    seen: list[list[dict[str, str]]] = []

    def brain(messages):
        seen.append([dict(m) for m in messages])
        return replies[len(seen) - 1] if len(seen) <= len(replies) else replies[-1]

    brain.seen = seen  # type: ignore[attr-defined]
    return brain


# --- the prompt keeps every rule while it shrinks ------------------------------------------------


def test_the_shorter_prompt_still_carries_every_rule():
    lowered = build_system_prompt(_registry()).lower()
    missing = [fragment for fragment in RULE_FRAGMENTS if fragment not in lowered]
    assert not missing, missing
    assert any(phrase in lowered for phrase in ("cannot change anything", "change nothing yourself"))
    assert any(word in lowered for word in ("approve", "approves", "approval"))
    assert "propose_change" in lowered


def test_the_prompt_is_unchanged_by_the_speed_work():
    # The speed job's prompt edits (a new rule line, a shorter change card) moved two answers to
    # confident-wrong on the Mac, reproducibly (re-measure 2026-10-28: base 26/30 twice, head 24/30);
    # the prompt stays byte for byte as it was, and only the staged evidence makes it faster.
    first = build_system_prompt(_registry())
    assert first == build_system_prompt(_registry()) and len(first) == 2336


# --- what the look-first pre-step stages and what it must never stage ------------------------------


@pytest.mark.parametrize("ask", NEVER_STAGES)
def test_no_evidence_is_staged_for_writes_approvals_and_read_the_file_asks(ask, home):
    assert _look_first()(ask, _ctx(home)) is None


@pytest.mark.parametrize(
    ("ask", "marker"),
    [
        ("whats on my desktop?", "todo.txt"),
        ("whats sitting in my downloads folder rn?", "plumber_invoice.txt"),
    ],
)
def test_a_deterministic_read_ask_stages_its_evidence_before_the_model(ask, marker, home):
    staged = _look_first()(ask, _ctx(home))
    assert isinstance(staged, str) and marker in staged


@pytest.mark.parametrize(
    "ask",
    [
        "how full is my computers storage rn?", "how much ram does this thing have?",
        "whats running on my computer right now?", "whats the biggest file in my downloads?",
    ],
)
def test_a_machine_state_ask_stages_the_measurement(ask, home):
    staged = _look_first()(ask, _ctx(home))
    assert isinstance(staged, str) and staged.strip()


# --- one model call when the staged evidence already holds the answer -----------------------------


def test_a_staged_listing_is_answered_in_one_model_call(home):
    brain = _scripted(['{"answer": "You have todo.txt."}'])
    result = run_loop("whats on my desktop?", brain=brain, registry=_registry(), ctx=_ctx(home))
    assert result.answer == "You have todo.txt." and result.model_calls == 1
    first_user = brain.seen[0][-1]["content"]
    assert "whats on my desktop?" in first_user and "todo.txt" in first_user


def test_an_ask_that_needs_the_file_itself_reaches_the_model_bare(home):
    brain = _scripted(
        [
            '{"tool": "read_file", "path": "Downloads/plumber_invoice.txt"}',
            '{"answer": "You owe EUR 140.00."}',
        ]
    )
    result = run_loop("how much do i owe the plumber?", brain=brain, registry=_registry(), ctx=_ctx(home))
    assert brain.seen[0][-1]["content"] == "how much do i owe the plumber?"
    assert result.answer == "You owe EUR 140.00." and result.model_calls == 2
