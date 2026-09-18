"""A verifier verdict survives every output contract, somewhere the reader can see it.

Measured on the served surface, 10:09::

    U: ... What is the current value of Y? Output exactly one word. No punctuation.
       No markdown. No explanation.
    A: Banana

    Activity: "Review flagged -- Verifier lane flagged the primary answer"

The verifier lane did its job: it flagged the answer, and the answer WAS wrong (two swaps put
Cherry in Y). Then the wrong answer shipped as a bare word with no signal anywhere in the reply.
The gate existed (`needs_review=True`, trust capped, a draft caveat defined) -- and the caveat was
suppressed because prepending prose would break the one-word contract. Correct reasoning, wrong
resolution: the CONTRACT owns the answer bytes; it does not own the space beside them.

The invariant: suppressing the inline caveat must never suppress the verdict. When the contract
forbids inline text, the flag rides in `response_control.verifier_presentation` -- which already
reaches `display_metadata` in the response commit -- and the chat page renders it beside the
answer, outside the canonical bytes that copy/pin/history use.

Both directions are pinned: an unflagged sealed answer must NOT be badged (a false warning teaches
the reader to ignore the real ones), and an ordinary flagged answer still gets the inline caveat.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.memory_first_router import VERIFIER_DRAFT_CAVEAT, apply_verifier_draft_caveat


def _decision(needs_review: bool) -> SimpleNamespace:
    return SimpleNamespace(
        details={"needs_review": True} if needs_review else {}, trust_score=0.9
    )


REPORTED_TEXT = (
    'Variable X holds "Apple". Variable Y holds "Banana". Variable Z holds "Cherry". '
    "I swap the contents of X and Y. Then I swap the contents of Y and Z. Then I overwrite Z "
    'with "Grape". What is the current value of Y? Output exactly one word. No punctuation. '
    "No markdown. No explanation."
)


def test_the_reported_turn_records_the_verdict_despite_the_contract() -> None:
    """The measured hole: contract suppression discarded the flag entirely."""

    source_context: dict = {}
    out = apply_verifier_draft_caveat(
        "Banana",
        _decision(needs_review=True),
        user_text=REPORTED_TEXT,
        source_context=source_context,
    )

    # The answer bytes stay contract-clean -- no caveat inside them.
    assert out == "Banana"
    presentation = dict(
        dict(source_context.get("response_control") or {}).get("verifier_presentation") or {}
    )
    assert presentation.get("review_flagged") is True
    assert presentation.get("advisory_suppressed") is True


def test_preserve_exact_shape_alone_also_records_the_verdict() -> None:
    """The second suppressed path -- preserve_exact_shape with no seal -- recorded NOTHING before
    this repair. Both suppression branches must leave the same trace."""

    source_context: dict = {}
    out = apply_verifier_draft_caveat(
        "some exact rendering",
        _decision(needs_review=True),
        preserve_exact_shape=True,
        user_text="",
        source_context=source_context,
    )

    assert out == "some exact rendering"
    presentation = dict(
        dict(source_context.get("response_control") or {}).get("verifier_presentation") or {}
    )
    assert presentation.get("review_flagged") is True


def test_an_unflagged_sealed_answer_is_not_badged() -> None:
    """A false warning teaches the reader to ignore the real ones."""

    source_context: dict = {}
    apply_verifier_draft_caveat(
        "Banana",
        _decision(needs_review=False),
        user_text=REPORTED_TEXT,
        source_context=source_context,
    )

    presentation = dict(
        dict(source_context.get("response_control") or {}).get("verifier_presentation") or {}
    )
    assert not presentation.get("review_flagged")


def test_an_ordinary_flagged_answer_still_gets_the_inline_caveat() -> None:
    """Where no contract owns the bytes, the caveat belongs in the answer as before."""

    out = apply_verifier_draft_caveat(
        "The value of Y is Banana, because the swaps cancel out.",
        _decision(needs_review=True),
        user_text="walk me through what happens to Y",
        source_context={},
    )

    assert out.startswith(VERIFIER_DRAFT_CAVEAT)


import json
import pathlib
import shutil
import subprocess

NODE = shutil.which("node")


def _render(display_metadata: dict) -> dict:
    """EXECUTE the shipped renderAssistantContent in node against a DOM stub.

    The first version of this test grepped the page source for the badge strings -- and a
    sabotage that dead-coded the branch (`if (false)`) sailed through, because the strings were
    still present and unreachable. Only running the function distinguishes a live badge from a
    corpse.
    """

    src = pathlib.Path("core/vool_chat_page.py").read_text(encoding="utf-8")
    start = src.index("function renderAssistantContent(el, text, displayMetadata) {")
    end = src.index("// ---- Chat image actions", start)
    body = src[start:end]
    script = (
        "const made = [];\n"
        "function el() { const node = { className: '', textContent: '', children: [], dataset: {},\n"
        "  appendChild(c) { this.children.push(c); made.push(c); } }; return node; }\n"
        "global.document = { createElement: () => el() };\n"
        "function splitAssistantDisplayContent(text, dm) { return { content: text, display_metadata: dm || {} }; }\n"
        "function renderRichText(target, content) { target.dataset.raw = content; }\n"
        + body
        + "\nconst root = el();\n"
        + f"renderAssistantContent(root, 'Banana', {json.dumps(display_metadata)});\n"
        + "process.stdout.write(JSON.stringify({\n"
        + "  texts: root.children.map(c => String(c.className||'')).concat(made.map(c => String(c.textContent||''))),\n"
        + "  raw: root.dataset.raw,\n"
        + "}));"
    )
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_page_renders_the_flag_beside_the_answer() -> None:
    """The other half of the wiring, EXECUTED: a recorded verdict nobody renders is still silence."""

    rendered = _render({"verifier_presentation": {"review_flagged": True}})
    blob = " ".join(str(x) for x in rendered["texts"])

    assert "chat-verifier-flag" in blob, "the badge element was never created"
    assert "review flagged" in blob, "the badge carries no message"
    # The canonical bytes stay contract-clean: the badge must never leak into what copy/pin read.
    assert rendered["raw"] == "Banana"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_unflagged_answer_renders_no_badge() -> None:
    rendered = _render({"verifier_presentation": {"review_flagged": False}})

    assert "chat-verifier-flag" not in " ".join(str(x) for x in rendered["texts"])
