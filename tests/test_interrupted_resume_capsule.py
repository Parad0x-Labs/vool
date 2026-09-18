"""An interrupted-work resume must survive the strict task capsule, for any wording.

Measured live 2026-09-15 (RUNTIME-CONTINUITY-REPORT): the follow-up "the file was cut off
before it finished" was captured by the interrupted-task resume path, routed tool_intent, and
the turn DIED at TaskCapsule validation -- ``sanitized_context.abstract_inputs``: "Raw file
paths are not allowed in task capsules". The two sanitizers disagreed: the router's
``redact_text`` names only the sensitive absolute path prefixes, while the capsule's strict
scan rejects EVERY raw path (plus URLs, emails and token-like strings).

The repair is class-based, not wording-based: the capsule's own scan now ships with its
matching sanitizer (``core.task_capsule.sanitize_for_capsule``), and the decomposer -- the
handoff owner between resumed user text and strict capsules -- applies it to every
user-derived field (abstract inputs, lane summaries, constraint references). The capsule's
strict law itself is unchanged and still refuses raw content handed to it directly.
"""
from __future__ import annotations

import pytest

from core.task_capsule import SanitizedContext, sanitize_for_capsule
from core.task_decomposer import _split_abstract_inputs, decompose_task

RESUME_TEXT = (
    "Continue the interrupted work: finish /workspace/board.html which was cut off before it "
    "finished, keep every earlier change, and validate the whole file"
)


def test_the_interrupted_resume_text_survives_the_strict_capsule() -> None:
    parts = _split_abstract_inputs(RESUME_TEXT)
    assert "<path>" in " ".join(parts), parts
    SanitizedContext(problem_class="debugging", abstract_inputs=parts)


def test_decomposition_completes_for_the_resumed_interrupted_task() -> None:
    subtasks = decompose_task(
        parent_task_id="task-integration-c2-resume-0001",
        user_input=RESUME_TEXT,
        classification={"task_class": "debugging"},
    )
    assert subtasks, "the resumed turn must decompose, not die at capsule validation"
    for subtask in subtasks:
        # build_task_capsule already validates; assert the round shape explicitly.
        assert subtask.capsule.sanitized_context.abstract_inputs, subtask.capsule


@pytest.mark.parametrize(
    "text",
    [
        "finish /workspace/board.html which was cut off",          # workspace path (the live gap)
        "finish /Users/demo/board.html which was cut off",          # sensitive prefix (already covered)
        "see https://example.com/board which was cut off",          # url
        "email demo@example.test about the cut-off file",           # email
        "the artifact 3f2a9c1b8e7d4f0a5c6b2d1e4f7a8b9c0d3e6f7a8b9c0d3e6f7a8b9c0d3e6f7a was cut off",  # token
        "the C:\\Users\\demo\\board.html file was cut off",          # windows path
    ],
)
def test_every_strict_leak_class_is_sanitized_before_the_capsule(text: str) -> None:
    parts = _split_abstract_inputs(text)
    SanitizedContext(problem_class="debugging", abstract_inputs=parts)


def test_the_capsule_scan_itself_still_refuses_raw_content() -> None:
    """The strict law is not weakened: a builder that hands raw content to the capsule is
    still refused -- only the handoff owner's own output is sanitized."""
    with pytest.raises(ValueError, match="Raw file paths"):
        SanitizedContext(problem_class="debugging", abstract_inputs=["finish /workspace/board.html now"])
    with pytest.raises(ValueError, match="URLs are not allowed"):
        SanitizedContext(problem_class="debugging", abstract_inputs=["see https://example.com/x"])


def test_sanitize_for_capsule_preserves_the_meaning_around_placeholders() -> None:
    # The scan's own span (a path runs to the next whitespace) takes trailing punctuation
    # with it; the sentence's meaning and the placeholder boundary are what must survive.
    out = sanitize_for_capsule("Finish /workspace/board.html and keep prior changes")
    assert out == "Finish <path> and keep prior changes", out
