"""Cloud model knows its tools + the free-models intent no longer over-fires on chat references."""

from __future__ import annotations

import core.prompt_normalizer as pn
from core.agent_runtime import fast_command_surface as fcs


def test_capability_grounding_tells_the_model_it_has_tools() -> None:
    g = pn._capability_grounding(has_openclaw_tools=True).lower()
    assert "real tools" in g
    assert "inspect this machine" in g
    assert "never say you have no tools" in g
    # never claim a tool ran without evidence
    assert "unless its result is in this run" in g
    # off-surface (no tools wired) grounds nothing
    assert pn._capability_grounding(has_openclaw_tools=False) == ""


def test_free_models_intent_ignores_references_to_the_past_chat() -> None:
    # "the game the free ai gave us in this chat" is about the conversation, not a catalog request.
    for msg in (
        "the game that free ai gave us in this chat? cant u see the caht history!?",
        "show me the free ai code you gave us earlier",
        "that html the free model made in this conversation",
    ):
        assert fcs.maybe_handle_free_models_intent(msg) is None

    # A genuine catalog ask still fires (regex-level, no network).
    def _fires(text: str) -> bool:
        return bool(
            fcs._FREE_MODELS_INTENT_RE.search(text)
            and fcs._FREE_MODELS_VERB_RE.search(text)
            and not fcs._FREE_MODELS_CHAT_REF_RE.search(text)
        )

    assert _fires("what free ai models are available?")
    assert _fires("list the free llms")
    assert not _fires("the free ai you gave us")
