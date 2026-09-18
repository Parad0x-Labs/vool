"""Selecting a model changes who reasons. It does not hand over the runtime.

`explicit_model_owns_semantic_turn` guarded the front door with

    if explicit_model_owns_semantic_turn(source_context):
        return {"result": None}

and `{"result": None}` means "no fast path claimed this turn". So `apps/vool_agent.py` fell
straight through to `_prepare_turn_task_bundle` and into `_maybe_run_builder_controller` — and
`builder/controller.py` posts directly to Ollama's `/api/chat` with a LOCAL model tag. The gate
written to protect a pinned turn was routing it to local `qwen3:8b`, which then performed up to 65
sequential generations attributed to the model the operator had selected.

The same early return skipped everything below it: the machine fast path that owns
`machine.inspect_specs`, the live-info/price lookup, the deterministic workspace read, the math
path, the action-history receipts and the intent arbiter. One line, four separate product failures.

The line drawn now is fact versus judgement. A machine spec, a file's contents, a live price, a
receipt and an arithmetic result are facts the runtime owns and verifies — they run for a pinned
turn exactly as for any other. A folder overview, an evaluative reply and a build are interpretation
and language, and those belong to the selected model.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core.agent_runtime.turn_frontdoor import explicit_model_owns_semantic_turn

PINNED = "nvidia/nemotron-3-ultra-550b-a55b:free"


@pytest.fixture(scope="module")
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")


def _context(*, pinned: bool, workspace: str = "/tmp/ws") -> dict[str, object]:
    ctx: dict[str, object] = {
        "workspace": workspace,
        "workspace_root": workspace,
        "operating_mode": "auto",
        "surface": "api",
    }
    if pinned:
        ctx["requested_model"] = PINNED
    return ctx


# --------------------------------------------------------------------------------------
# The builder must never silently swap the operator's model for a local one
# --------------------------------------------------------------------------------------


def test_the_gate_itself_is_unchanged() -> None:
    assert explicit_model_owns_semantic_turn({"requested_model": PINNED}) is True
    assert explicit_model_owns_semantic_turn({"requested_model": "vool"}) is False
    assert explicit_model_owns_semantic_turn({"requested_model": "vool-local-only"}) is False
    assert explicit_model_owns_semantic_turn({"requested_model": "auto"}) is False
    assert explicit_model_owns_semantic_turn({}) is False


# The builder half of this file moved to tests/test_pinned_build_uses_the_pinned_model.py.
#
# It used to assert `_should_run_builder_controller(... pinned ...) is False`, on the reasoning that
# the local Ollama lane would otherwise generate a pinned build's files. The reasoning was right; the
# remedy was not. Refusing the turn did not produce a refusal -- it fell through to the selected
# model's tool loop, which did not call `workspace.write_file`, so "i need <ws>/v5/median.py with a
# median function" answered "I couldn't map that cleanly to a real action" after 75.6s with nothing
# on disk. Pinning a model meant you could not build at all.
#
# The property being protected is unchanged and is now checked where the swap would actually happen:
# a pinned build generates through the SELECTED provider, the local lane is asserted never to be
# called on a pinned turn, and a pin that cannot be honoured is refused by name. A gate assertion
# could not have told those apart -- both readings return False.


# --------------------------------------------------------------------------------------
# What a pinned turn must still reach, and what it must not
# --------------------------------------------------------------------------------------

# Facts and executed actions: the runtime owns and verifies these, so a pinned turn gets them.
FACT_HANDLERS = (
    "_maybe_handle_direct_machine_read_request",
    "_maybe_handle_direct_machine_download_request",
    "_maybe_handle_direct_machine_write_request",
    "_maybe_handle_direct_workspace_runtime_request",
    "_maybe_handle_live_info_fast_path",
    "_maybe_handle_mission_render_request",
    "_direct_math_fast_path",
    "_action_history_honesty_fast_path",
)

# Interpretation and language: these belong to the model the operator selected.
JUDGEMENT_HANDLERS = (
    "_maybe_handle_folder_overview_request",
    "_maybe_handle_web0_builder_fast_path",
    "_evaluative_conversation_fast_path",
)


def _reached(agent, text: str, *, pinned: bool) -> set[str]:
    """Which front-door handlers a turn actually reaches. Every one is stubbed to decline.

    The real front door is driven, not a copy of its ordering: a source-position assertion proves
    where a line sits, and this proves what runs.
    """

    called: set[str] = set()
    patches = [
        mock.patch.object(
            agent,
            name,
            lambda *a, _name=name, **k: (called.add(_name), None)[1],
        )
        for name in FACT_HANDLERS + JUDGEMENT_HANDLERS
    ]
    for patch in patches:
        patch.start()
    try:
        agent._handle_turn_frontdoor(
            raw_user_input=text,
            effective_input=text,
            normalized_input=text.lower(),
            source_surface="api",
            session_id=f"pin-probe-{'pinned' if pinned else 'auto'}",
            source_context=_context(pinned=pinned),
            persona=None,
            interpreted=None,
        )
    finally:
        for patch in patches:
            patch.stop()
    return called


def test_a_pinned_turn_still_reaches_the_machine_and_live_info_paths(agent) -> None:
    """Measured on the installed build with a cloud model pinned: "what is my machine scpecs?" and
    "price of sol?" reached no tool at all, because the gate returned before either handler.
    """

    reached = _reached(agent, "what is my machine specs?", pinned=True)

    assert "_maybe_handle_direct_machine_read_request" in reached, (
        "machine.inspect_specs is a runtime fact; a pinned model does not own it"
    )
    assert "_maybe_handle_live_info_fast_path" in reached


def test_a_pinned_turn_does_not_reach_the_judgement_handlers(agent) -> None:
    """The gate's purpose survives: a fast path must not answer FOR the selected model."""

    reached = _reached(agent, "what is this project about?", pinned=True)

    for name in JUDGEMENT_HANDLERS:
        assert name not in reached, f"{name} answered instead of the pinned model"


def test_an_unpinned_turn_reaches_both(agent) -> None:
    """The baseline the pinned case is compared against."""

    reached = _reached(agent, "what is this project about?", pinned=False)

    assert "_maybe_handle_folder_overview_request" in reached
    assert "_maybe_handle_direct_workspace_runtime_request" in reached


# --------------------------------------------------------------------------------------
# The shape of the fix, so it is not undone by a later edit
# --------------------------------------------------------------------------------------


def test_the_gate_no_longer_returns_early() -> None:
    """An early return is what turned one gate into four product failures.

    `{"result": None}` reads as "nothing claimed this turn", which is exactly the value that sends
    the turn to the local builder. The gate must set a flag the judgement handlers consult, not
    abandon the front door.
    """

    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
    ).read_text(encoding="utf-8")

    assert "model_owns_judgement = explicit_model_owns_semantic_turn(source_context)" in source
    assert "if explicit_model_owns_semantic_turn(source_context):\n        return" not in source

    gate = source.index("model_owns_judgement = explicit_model_owns_semantic_turn")
    for handler in (
        "agent._maybe_handle_direct_machine_read_request(",
        "agent._maybe_handle_live_info_fast_path(",
        "agent._maybe_handle_direct_workspace_runtime_request(",
    ):
        assert source.index(handler) > gate, f"{handler} must sit below the flag, not above it"
        # ...and must NOT be guarded by it.
        window_start = source.index(handler) - 200
        assert "model_owns_judgement" not in source[window_start:source.index(handler)], (
            f"{handler} is a runtime fact and must run for a pinned turn"
        )


def test_every_judgement_handler_consults_the_flag() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "turn_frontdoor.py"
    ).read_text(encoding="utf-8")

    for handler in (
        "agent._maybe_handle_folder_overview_request(",
        "agent._maybe_handle_web0_builder_fast_path(",
        "agent._evaluative_conversation_fast_path(",
    ):
        position = source.index(handler)
        assert "model_owns_judgement" in source[position - 120:position], (
            f"{handler} would answer instead of the model the operator selected"
        )
