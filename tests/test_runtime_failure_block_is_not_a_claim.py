"""The runtime's own account of a failed part is kept word for word by the claim gate.

Measured on the first-day sweep (Nov 21), no model running, "hello whats 2+2 and tell me a joke
please": the part that failed carried the runtime's reason ("no local model is running on this
computer ..."). The gate judged that reason as an unsupported claim, withheld its first sentence
and served "I could not answer this part of your message:" over "- Add a cloud key ...)".
Only a block the runtime registered is exempt: the same words written by a model are judged.
"""
from __future__ import annotations

import uuid

from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import publication_verdict, register_runtime_failure_block
from core.memory_grounding import memory_record_rows

RECORDS = ("<retrieved_context>\n- user said (stated 2022-05-01): Session date: 1 May, 2022\n"
           "James: At only $10 per class, it's very cheap! Also, I made meringue there.\n</retrieved_context>")
ANSWER = "James pays $10 per class."


def _block() -> str:
    # A fresh reason each run, so no earlier registration can stand in for this one.
    tag = uuid.uuid4().hex[:6]
    return ("I could not answer this part of your message:\n"
            f"- and tell me a joke {tag} (no local model is running on this computer and no cloud "
            "model is set up. Add a cloud key in Settings, or start a local model.)")


def _verdict(content: str):
    lifecycle = GroundingLifecycle(lifecycle_id="failure-block-" + uuid.uuid4().hex[:6], identity=TurnIdentity(),
                                   request_text="How much does James pay per class, and tell me a joke",
                                   model_authored=True, memory_records=tuple(memory_record_rows(RECORDS)))
    return publication_verdict(lifecycle, content)


def test_a_registered_failure_block_follows_the_gated_answer_unchanged() -> None:
    block = _block()
    register_runtime_failure_block(block)
    verdict = _verdict(ANSWER + "\n\n" + block)
    assert verdict.content.endswith(block), verdict.content
    assert "$10" in verdict.content


def test_the_same_words_from_a_model_are_still_judged() -> None:
    block = _block()
    unregistered = _verdict(ANSWER + "\n\n" + block)
    assert not unregistered.content.endswith(block), unregistered.content
