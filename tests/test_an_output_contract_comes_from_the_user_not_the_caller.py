"""An output contract is derived from the user's own turn, never accepted from the request body.

Three sibling keys are built the same way in `apps/vool_agent.py` -- `response_language_policy`,
`response_constraint`, `raw_output_contract` -- and two of them are popped first, with comments
saying "never accepted from an API caller" and "never trusted from caller-supplied metadata". The
middle one had no such pop, and is not in `core.request_trust.RESERVED_TRUST_KEYS`, so a body value
survived the strip and nothing cleared it.

Measured on c6eed761 against the live runtime::

    POST /api/chat  {"messages":[...ordinary question...],
                     "source_context":{"response_constraint":{"max_words":3}}}

    without the injected key -> 43 words
    with it                  -> 3 words, "A hash table"

The pop also bounds the value's LIFETIME. A checkpoint resume merges the stored context forward with
`dict.update()`, which cannot remove a key the fresh turn simply does not have -- so a constraint
recorded on one turn could outlive it.
"""

from __future__ import annotations

from apps.vool_agent import VoolAgent


def _agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="contract-test", persona_id="default")


def _prepared_context(user_text: str, inbound: dict) -> dict:
    """Run the front door far enough to see what it recorded, whatever it answers with."""

    agent = _agent()
    context = dict(inbound)
    context.setdefault("surface", "api")
    agent.run_once(user_text, session_id_override="contract-test-session", source_context=context)
    return context


def test_a_caller_supplied_constraint_is_not_honoured() -> None:
    """The measured injection: a body-supplied constraint governed a turn that never asked for one."""

    context = _prepared_context(
        "explain in a couple of sentences what a hash table is",
        {"response_constraint": {"max_words": 3}},
    )

    assert context.get("response_constraint") != {"max_words": 3}, (
        "a caller-supplied output contract survived into the turn context"
    )


def test_a_caller_supplied_constraint_is_cleared_entirely_when_the_user_stated_none() -> None:
    context = _prepared_context(
        "what is a hash table",
        {"response_constraint": {"exact_words": 5, "max_sentences": 1}},
    )

    assert not context.get("response_constraint")


def test_the_users_own_words_still_produce_a_constraint() -> None:
    """Negative control: the pop must clear the caller's value, never the user's own request."""

    from core.response_constraints import parse_response_constraint

    parsed = parse_response_constraint("answer in exactly three words: what is a hash table")

    assert parsed is not None, "the user's own stated contract must still parse"


def test_every_derived_contract_key_is_cleared_before_it_is_rebuilt() -> None:
    """All three siblings, one rule. Behavioural: inject all three, assert none survives.

    Asserted together because the defect was an ASYMMETRY -- two keys guarded, one not -- and a
    per-key test would have passed on the two that were already right while the third shipped.
    """

    injected = {
        "response_constraint": {"max_words": 3},
        "raw_output_contract": {"raw_only": True},
        "response_language_policy": {"language": "de", "compliant": False},
    }
    context = _prepared_context("what is a hash table", injected)

    for key, planted in injected.items():
        assert context.get(key) != planted, f"caller-supplied {key} survived into the turn"
