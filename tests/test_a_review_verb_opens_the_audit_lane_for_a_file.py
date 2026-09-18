"""Asking to review a file is asking for a review, whatever verb the operator reached for.

The audit gate accepts eight review verbs — `analyse`, `review`, `inspect`, `examine`,
`scrutinize`, `assess`, `evaluate`, `go through` — but only where they modify a generic NOUN
(`code`, `codebase`, `repo`, `project`). Where the target is a FILE it accepted one word: `audit`.

Nothing justified the asymmetry, and it shipped the same defect twice:

  2026-08-01  "lets audit api/apache/liquefy_apache_repetition_v1.py - whats the single worst real
              bug in it" missed the lane; the direct-read fast path claimed it and PRINTED THE
              FILE. Fixed by teaching this branch the word `audit`.
  2026-08-03  "pdf_rebuild.py in depth anylyse this for me" missed it again and printed the file
              again, because `analyse` was never added. Neither were `review`, `examine` or
              `assess` — each of which sent a code-quality question to a raw `cat`.

Adding one verb per incident is how a third incident gets scheduled. The file branch now shares
the verb set the noun branches already use: a filename IS a source-code noun.

The boundary that has to hold is read-vs-review, not verb-vs-verb. `read x.py` and `print x.py`
ask for the bytes; `review x.py` asks for a judgement. The second half of this file pins that.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.workspace_audit import looks_like_code_audit_request

# --------------------------------------------------------------------------------------
# A review verb against a named file reaches the audit lane.
# --------------------------------------------------------------------------------------


def test_the_measured_request_reaches_the_audit_lane() -> None:
    """The 2026-08-03 turn, minus the typo the operator actually made.

    They wrote "anylyse". No verb list reaches that spelling and this one does not try — a runtime
    that needs the word spelled correctly is a command line with extra steps. What this pins is
    that the CORRECT spelling was failing too, which is a defect on its own.
    """

    assert looks_like_code_audit_request(
        "pdf_rebuild.py in depth analyse this for me and tell me what to improve"
    )


@pytest.mark.parametrize(
    "request_text",
    [
        "review pdf_rebuild.py",
        "analyze src/calc.py",
        "please examine api/engine.py",
        "assess core/router.py for me",
        "evaluate tools/pack.py",
        "inspect storage/index.py",
        "scrutinize relay/socket.py",
        "critique apps/main.py",
        "audit pdf_rebuild.py",
    ],
)
def test_every_review_verb_works_against_a_file(request_text: str) -> None:
    assert looks_like_code_audit_request(request_text)


def test_the_verb_may_follow_the_filename() -> None:
    """Operators lead with the file as often as with the verb, and the drag-and-drop UI does too."""

    assert looks_like_code_audit_request("src/calc.py — review this please")


# --------------------------------------------------------------------------------------
# A retrieval request is still retrieval. This is the boundary the widening could have eaten.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    [
        "read pdf_rebuild.py",
        "print pdf_rebuild.py",
        "cat notes.txt",
        "open notes.txt",
        "show me config.yaml",
        "what does pdf_rebuild.py do?",
        "what is in notes.txt",
        "tail logs/api.log",
    ],
)
def test_a_read_request_is_not_an_audit(request_text: str) -> None:
    """Sending these to the stepped audit would spend a multi-call proof run on `cat`."""

    assert not looks_like_code_audit_request(request_text)


def test_a_file_named_after_the_verb_is_not_the_verb() -> None:
    """`_AUDIT_TARGET_RE` strips path tokens before the verb search, and must keep doing so.

    Widening the verb set widens this failure too: `review.py`, `analysis.py` and `inspect.py` are
    all plausible filenames, and matching the verb INSIDE one would route a mention of the file to
    a full audit.
    """

    assert not looks_like_code_audit_request("the audit.py module needs no changes")
    assert not looks_like_code_audit_request("open review.py")
    assert not looks_like_code_audit_request("read inspect.py and tell me the imports")


def test_a_review_verb_with_no_target_stays_out() -> None:
    """The branch requires a target. Without one there is nothing to audit."""

    assert not looks_like_code_audit_request("can you review that for me")


# --------------------------------------------------------------------------------------
# The wiring. The gate is only worth anything if the read lane loses the race.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    ["review pdf_rebuild.py", "pdf_rebuild.py in depth analyse this for me"],
)
def test_the_read_fast_path_no_longer_gets_these_first(request_text: str) -> None:
    """The gate above is checked at `turn_frontdoor.py:417`, the read lane at `:898`.

    Asserting only on `looks_like_code_audit_request` passes even if the read lane were moved
    ahead of it — and the read lane still claims every one of these strings when asked directly,
    because it binds on the presence of a filename. The audit gate winning the race IS the fix,
    so the ordering is what gets pinned.
    """

    import inspect as _inspect

    import core.agent_runtime.fast_paths_utility as fp
    import core.agent_runtime.turn_frontdoor as fd

    source = _inspect.getsource(fd)
    audit_at = source.index("_maybe_handle_workspace_audit_request")
    read_at = source.index("_maybe_handle_direct_workspace_runtime_request")

    assert audit_at < read_at, "the read lane would claim the turn before the audit gate sees it"
    assert looks_like_code_audit_request(request_text)
    # Documents the standing over-claim: the read lane binds on a filename alone, so it is the
    # audit gate — not the read gate — that keeps a review request off `cat`.
    assert fp._direct_workspace_read_request(request_text) is not None
