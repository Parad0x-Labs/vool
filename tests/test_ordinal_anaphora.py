from __future__ import annotations

import uuid

import pytest

from core.human_input_adapter import _resolve_reference_targets, adapt_user_input
from core.persistent_memory import append_conversation_event


def _record_list_response(*, session_id: str, assistant_output: str) -> None:
    first_prompt = "Show me the largest files on C drive."
    adapt_user_input(first_prompt, session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input=first_prompt,
        assistant_output=assistant_output,
    )


@pytest.mark.parametrize(
    ("followup", "expected_target"),
    [
        ("what about the first one?", "alpha.iso — 9.2 GB [file]"),
        ("what about the second one?", "beta.zip — 4.8 GB [file]"),
        ("show me the third item", "gamma.bin — 2.1 GB [file]"),
        ("open the last file", "gamma.bin — 2.1 GB [file]"),
    ],
)
def test_ordinal_followup_resolves_latest_bulleted_assistant_item(
    followup: str,
    expected_target: str,
) -> None:
    session_id = f"ordinal-{uuid.uuid4().hex}"
    _record_list_response(
        session_id=session_id,
        assistant_output=(
            "Largest files on C:\\ (measured, largest first):\n"
            "- alpha.iso — 9.2 GB [file]\n"
            "- beta.zip — 4.8 GB [file]\n"
            "- gamma.bin — 2.1 GB [file]"
        ),
    )

    interpreted = adapt_user_input(followup, session_id=session_id)

    assert interpreted.reference_targets == [expected_target]
    assert "ambiguous_reference" not in interpreted.quality_flags
    assert interpreted.reconstructed_text == interpreted.normalized_text
    assert "Context subject:" not in interpreted.reconstructed_text
    assert interpreted.needs_clarification is False


def test_ordinal_followup_resolves_numbered_result() -> None:
    session_id = f"ordinal-{uuid.uuid4().hex}"
    _record_list_response(
        session_id=session_id,
        assistant_output="Search results:\n1. First result\n2) Second result\n3. Third result",
    )

    interpreted = adapt_user_input("use the second result", session_id=session_id)

    assert interpreted.reference_targets == ["Second result"]
    assert "ambiguous_reference" not in interpreted.quality_flags


def test_next_followup_advances_from_previous_ordinal_selection() -> None:
    session_id = f"ordinal-{uuid.uuid4().hex}"
    _record_list_response(
        session_id=session_id,
        assistant_output="Search results:\n1. First result\n2. Second result\n3. Third result",
    )
    first = adapt_user_input("use the first result", session_id=session_id)

    interpreted = adapt_user_input("use the next result", session_id=session_id)

    assert first.reference_targets == ["First result"]
    assert interpreted.reference_targets == ["Second result"]
    assert "ambiguous_reference" not in interpreted.quality_flags


@pytest.mark.parametrize(
    "followup",
    [
        "what about the third one?",
        "use the next result",
    ],
)
def test_unresolvable_ordinal_followup_is_explicitly_ambiguous(followup: str) -> None:
    session_id = f"ordinal-{uuid.uuid4().hex}"
    _record_list_response(
        session_id=session_id,
        assistant_output="Available files:\n- alpha.txt\n- beta.txt",
    )

    interpreted = adapt_user_input(followup, session_id=session_id)

    assert interpreted.reference_targets == []
    assert "ambiguous_reference" in interpreted.quality_flags
    assert "Context subject:" not in interpreted.reconstructed_text
    assert interpreted.needs_clarification is True


def test_negated_ordinal_does_not_select_prior_item() -> None:
    session_id = f"ordinal-{uuid.uuid4().hex}"
    _record_list_response(
        session_id=session_id,
        assistant_output="Available files:\n- alpha.txt\n- beta.txt",
    )

    interpreted = adapt_user_input("not the second one", session_id=session_id)

    assert interpreted.reference_targets == []
    assert "ambiguous_reference" in interpreted.quality_flags
    assert "Context subject:" not in interpreted.reconstructed_text


def test_existing_pronoun_resolution_still_uses_session_subject() -> None:
    session_id = f"ordinal-{uuid.uuid4().hex}"
    adapt_user_input(
        "Thomas keeps the knowledge shard for telegram bot routing.",
        session_id=session_id,
    )

    interpreted = adapt_user_input("what about that one?", session_id=session_id)

    assert "knowledge shard" in interpreted.reference_targets
    assert "ambiguous_reference" not in interpreted.quality_flags


def test_run_once_passes_resolved_ordinal_target_to_runtime(make_agent, monkeypatch) -> None:
    session_id = f"ordinal-{uuid.uuid4().hex}"
    _record_list_response(
        session_id=session_id,
        assistant_output="Available files:\n- alpha.txt\n- beta.txt\n- gamma.txt",
    )
    captured: dict[str, object] = {}
    agent = make_agent()

    def capture_frontdoor(**kwargs):
        captured.update(kwargs)
        return {
            "result": {
                "response": "Captured the resolved follow-up.",
                "confidence": 1.0,
                "mode": "advice_only",
            }
        }

    monkeypatch.setattr(agent, "_handle_turn_frontdoor", capture_frontdoor)

    result = agent.run_once(
        "what about the second one?",
        session_id_override=session_id,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["response"] == "Captured the resolved follow-up."
    assert captured["effective_input"] == "what about the second one?"
    assert captured["interpreted"].reference_targets == ["beta.txt"]


_LIST_TURN = {
    "speaker_role": "assistant",
    "raw_input": "Here are the largest:\n1. alpha.iso - 9.2 GB\n2. beta.zip - 4.8 GB\n3. gamma.bin - 2.1 GB",
}


def _context(gap_exchanges: int = 0) -> list[dict[str, object]]:
    turns: list[dict[str, object]] = [dict(_LIST_TURN)]
    for index in range(gap_exchanges):
        turns = [
            {"speaker_role": "assistant", "raw_input": f"unrelated answer {index}"},
            {"speaker_role": "user", "raw_input": f"unrelated question {index}"},
            *turns,
        ]
    return turns


def _resolve(text: str, *, gap_exchanges: int = 0) -> tuple[list[str], bool]:
    targets, flags = _resolve_reference_targets(
        text,
        current_topics=[],
        session_state={},
        recent_turns=[],
        context_turns=_context(gap_exchanges),
    )
    return targets, "ambiguous_reference" in flags


@pytest.mark.parametrize(
    "text, expected",
    [
        ("what about the 2nd one?", "beta.zip - 4.8 GB"),
        ("the 3rd one", "gamma.bin - 2.1 GB"),
        ("what about the second entry?", "beta.zip - 4.8 GB"),
        ("the fourth option", None),
    ],
)
def test_ordinal_vocabulary_beyond_the_first_three_words(text: str, expected: str | None) -> None:
    """Digit forms and wider nouns resolve; an unstocked position is not silently ignored."""
    targets, ambiguous = _resolve(text)
    if expected is None:
        assert targets == []
        assert ambiguous is True
    else:
        assert targets == [expected]
        assert ambiguous is False


@pytest.mark.parametrize("text", ["the fifth one", "the 9th one", "what about the fourth one?"])
def test_out_of_range_ordinal_is_flagged_rather_than_answered_confidently(text: str) -> None:
    """An ordinal past the end of the list must never clamp to a neighbouring item."""
    targets, ambiguous = _resolve(text)
    assert targets == []
    assert ambiguous is True


def test_ordinal_does_not_resolve_against_a_stale_list() -> None:
    """A list the user moved on from must not be answered about with full confidence."""
    fresh_targets, fresh_ambiguous = _resolve("what about the second one?", gap_exchanges=0)
    assert fresh_targets == ["beta.zip - 4.8 GB"]
    assert fresh_ambiguous is False

    stale_targets, stale_ambiguous = _resolve("what about the second one?", gap_exchanges=4)
    assert stale_targets == []
    assert stale_ambiguous is True
