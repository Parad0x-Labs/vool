"""A publication refusal must report the model attempts that actually happened.

Reproduces the live acceptance defect (2026-09-08, built app at 1206993d, "What is the capital
of France?" with the machine's cloud key expired): the ranked loop blocked the uncertified local
model pre-call, then CALLED the two certified free-cloud authors, and both failed before
producing a usable answer. The served refusal said

    `ollama-local:qwen2.5:7b` is not certified to author on this runtime, so the call was never
    made, and no certified model was available and permitted to write it instead.

-- which is false about the turn (two certified models WERE called and failed; the response
envelope's own provenance said ``failed_model_calls: 2``). The router's ranked loop knew the
attempt list at its exhaustion point; the authorship record had nowhere to put it and the
publication gate had no way to read it. This file pins the repaired contract end to end:
the loop records its attempts, the record carries them, and the refusal names them.
"""

from __future__ import annotations

from core.final_answer_authorship import (
    AuthorshipDecision,
    REASON_BLOCKED_BEFORE_GENERATION,
    authorship_record_for_publication,
    gate_authored_content,
    record_authorship_attempts,
    record_authorship_decision,
)

ATTEMPTED = (
    "openrouter-byok:nvidia/nemotron-3.5-lightning:free",
    "openrouter-byok:thinkingmachines/inkling-small:free",
)


def _context(turn_id: str) -> dict:
    return {"cancel_turn_id": turn_id, "request_id": f"req-{turn_id}"}


def _blocked_decision() -> AuthorshipDecision:
    return AuthorshipDecision(
        eligible=False,
        author_role="answer_generation",
        task_class="direct",
        requested_model="ollama-local:qwen2.5:7b",
        selected_model="",
        reason=REASON_BLOCKED_BEFORE_GENERATION,
    )


def test_a_refusal_with_attempts_names_them_and_claims_no_more_than_happened() -> None:
    text = _blocked_decision().refusal_text(attempted_failed=ATTEMPTED)
    assert "qwen2.5:7b" in text
    for model in ATTEMPTED:
        assert model in text, f"attempted author {model} not named in refusal"
    # The false claim this file exists to remove.
    assert "no certified model was available and permitted" not in text
    assert "each failed before producing a usable answer" in text


def test_a_refusal_without_attempts_keeps_its_exact_former_bytes() -> None:
    """The no-attempts path is the historical shape; it must not drift."""
    text = _blocked_decision().refusal_text()
    assert (
        "`ollama-local:qwen2.5:7b` is not certified to author on this runtime, so the call was "
        "never made, and no certified model was available and permitted to write it instead."
    ) in text


def test_the_gate_serves_the_attempt_aware_refusal() -> None:
    turn_id = "turn-attempts-gate"
    context = _context(turn_id)
    record_authorship_decision(
        context,
        _blocked_decision(),
        blocked_model="ollama-local:qwen2.5:7b",
        request_text="What is the capital of France?",
    )
    record_authorship_attempts(context, ATTEMPTED)
    gated, payload = gate_authored_content("Paris is the capital of France.", turn_id=turn_id)
    assert gated.startswith("I can't publish this answer:")
    for model in ATTEMPTED:
        assert model in gated
    assert "no certified model was available and permitted" not in gated
    assert payload.get("attempted_failed") == list(ATTEMPTED)


def test_attempts_are_recorded_even_when_no_decision_exists_yet() -> None:
    """The router may exhaust its candidates before any authorship decision was recorded."""
    turn_id = "turn-attempts-only"
    context = _context(turn_id)
    assert record_authorship_attempts(context, list(ATTEMPTED)) is True
    record = authorship_record_for_publication(turn_id=turn_id)
    assert record is not None
    assert record.attempted_failed == ATTEMPTED


def test_a_blocked_model_is_not_reported_as_called() -> None:
    """The ranked loop's attempted list conflates typed refusals with real calls.

    The pre-call fence refuses an uncertified author by returning an error, so the loop
    appends that model to `attempted` alongside models that really ran. Recording it as
    "called and failed" names a call that never happened -- measured on the rebuilt app:
    the refusal said the blocked local model had been called.
    """
    turn_id = "turn-attempts-blocked-filter"
    context = _context(turn_id)
    record_authorship_decision(
        context,
        _blocked_decision(),
        blocked_model="ollama-local:qwen2.5:7b",
        request_text="What is the capital of France?",
    )
    record_authorship_attempts(context, ["ollama-local:qwen2.5:7b", ATTEMPTED[0]])
    record = authorship_record_for_publication(turn_id=turn_id)
    assert record is not None
    assert "ollama-local:qwen2.5:7b" not in record.attempted_failed
    assert record.attempted_failed == (ATTEMPTED[0],)
    gated, _payload = gate_authored_content("Paris.", turn_id=turn_id)
    assert "never made" in gated
    assert ATTEMPTED[0] in gated
    assert "ollama-local:qwen2.5:7b`) each failed" not in gated
