"""The self-knowledge document rides only turns that ask about the assistant itself.

Measured (D-item, first filed 2026-08-11; confirmed still ungated by the 2026-08-15 pipeline
audit): docs/VOOL_SELF_KNOWLEDGE.md was injected unconditionally -- must_keep, priority 0.97, on
every non-plain turn -- and its canned x402/self-description text leaked into answers about
unrelated topics. A weather question cannot use the runtime's self-portrait; a "what are you"
question still gets the full document.
"""

from __future__ import annotations

import pytest

from core.bootstrap_context import _turn_is_about_the_assistant


@pytest.mark.parametrize(
    "turn",
    (
        "what are you exactly",
        "what can you do",
        "who are u",
        "are you an AI model running within an orchestration framework?",
        "tell me about your capabilities and endpoints",
        "how does this app work",
        "introduce yourself",
        "does vool handle x402 payments",
        "what's your memory architecture",
    ),
)
def test_self_questions_carry_the_document(turn: str) -> None:
    assert _turn_is_about_the_assistant(turn), turn


@pytest.mark.parametrize(
    "turn",
    (
        "what is the weather in Vilnius right now",
        "write me a bash one-liner that counts .txt files",
        "summarize this text: the deploy went out cleanly on friday",
        "String X is 'VOOL'. Reverse it.",  # names the PRODUCT of a string, not the runtime? no:
        # 'VOOL' here is a quoted literal -- see the adversarial case below for the boundary.
        "my landlord raised the rent, draft a polite reply",
        "what is 25 + 20",
        "plan a 3-day trip to Rome",
    ),
)
def test_unrelated_turns_do_not_carry_the_document(turn: str) -> None:
    # The quoted-literal case is the one deliberate exception documented below.
    if "String X" in turn:
        pytest.skip("covered by the adversarial boundary test")
    assert not _turn_is_about_the_assistant(turn), turn


def test_the_product_name_is_an_accepted_overtrigger_boundary() -> None:
    """A turn NAMING the product ("is vool open source?") legitimately gets the document; the cost
    is that a quoted literal spelling the product name also does. Stated, not hidden: the failure
    mode this gate exists to close (canned x402 text in weather answers) does not contain the
    product name, and a name-bearing turn is at worst a few hundred wasted context tokens."""
    assert _turn_is_about_the_assistant("is vool open source")
    assert _turn_is_about_the_assistant("String X is 'VOOL'. Reverse it.")


def test_the_bootstrap_builder_respects_the_gate(monkeypatch) -> None:
    """Behavioral: the builder emits the self-knowledge item for a self-question and omits it for
    an unrelated turn, with the document read mocked so no docs/ file is needed."""
    from unittest import mock

    import core.bootstrap_context as bc

    def _fake_read(folder, name, max_chars=0):
        if name == "VOOL_SELF_KNOWLEDGE.md":
            return "I am VOOL. x402 lane is DISABLED."
        return ""

    class _Interp:
        raw_text = ""
        normalized_text = ""
        reconstructed_text = ""
        quality_flags: list = []
        topic_hints: list = []
        reference_targets: list = []
        working_interpretation = None

    class _Task:
        task_id = "t1"
        task_summary = ""

    def _items_for(text: str):
        interp = _Interp()
        interp.raw_text = interp.normalized_text = interp.reconstructed_text = text
        task = _Task()
        task.task_summary = text
        with mock.patch.object(bc, "_read_markdown_context", side_effect=_fake_read):
            items = bc.build_bootstrap_context(
                persona=mock.Mock(persona_id="default", display_name="VOOL"),
                task=task,
                classification={"task_class": "chat_conversation"},
                interpretation=interp,
                session_id="openclaw:selfknowledgegate",
                include_private_context=False,
                include_history_context=False,
            )
        return [getattr(item, "item_id", "") for item in (items or [])]

    self_ids = _items_for("what are you and what can you do")
    other_ids = _items_for("draft a polite reply to my landlord about the rent")
    assert "bootstrap-self-knowledge" in self_ids
    assert "bootstrap-self-knowledge" not in other_ids
