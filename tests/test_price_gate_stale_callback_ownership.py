"""The price gate's async save callbacks belong to the review that started them.

The gate is one long-lived overlay; a save is asynchronous; the operator can close or replace
the review before the reply lands. The contract this pins (measured broken in the browser
2026-09-16: a superseded save resolved ``true``, so its handler toasted and called
``closeGate(true)`` — approving and closing whatever NEWER review was open — and a superseded
FAILURE painted its error into the newer dialog and re-enabled its buttons):

* the save's completion/error callbacks re-check liveness (``activeResolve === resolve``) after
  EVERY async hop, and answer with a typed outcome; ``superseded`` means the owning review is
  gone;
* BOTH confirmation buttons return immediately on ``superseded`` — an obsolete callback never
  approves, closes, repaints or re-enables another review;
* a live failure still keeps the draft (error shown, buttons re-enabled, dialog open).

Source-pinned like the repo's other JS-contract pins (see
``tests/test_empty_completion_failover.py``): the behavioural proof is the browser drive recorded
in the mission's DELIVERY.md; this keeps the shape from silently regressing.
"""
from __future__ import annotations

from core.price_safety_fragment import _GATE_JS


def test_the_save_rechecks_liveness_after_every_hop_and_answers_typed_outcomes() -> None:
    assert "function live(){ return activeResolve === resolve; }" in _GATE_JS
    # The liveness check must NOT special-case null: an escaped review (activeResolve === null)
    # is exactly as superseded as a replaced one.
    assert "activeResolve !== null && activeResolve !== resolve" not in _GATE_JS
    for hop in ("if (!live()) return { superseded: true };",):
        assert _GATE_JS.count(hop) >= 3, "liveness must be re-checked after the reply, after the bounds refresh, and in catch"


def test_both_confirmation_buttons_do_nothing_on_a_superseded_outcome() -> None:
    # Exactly the two button handlers consume saveReviewedMaxima; both must return before ANY
    # UI effect when the outcome is superseded.
    assert _GATE_JS.count("if (outcome.superseded) return;") == 2
    # And neither handler may touch the dialog before that guard: the guard is the first
    # statement inside each .then.
    import re

    handlers = re.findall(r"saveReviewedMaxima\(ctx, maxima\)\.then\(function\(outcome\)\{(.*?)\}\);", _GATE_JS, re.S)
    assert len(handlers) == 2, "both confirmation buttons must own their save's outcome"
    for body in handlers:
        first = body.strip().splitlines()[0].strip()
        assert first.startswith("if (outcome.superseded) return;"), first


def test_the_refusal_action_carries_plain_attributes_not_json() -> None:
    """The wedge the browser drive found: the Review price limits button packed its target as
    JSON inside a quoted attribute; the panel's re-serialization unescaped it, the attribute
    truncated at the first inner quote, JSON.parse threw, and the click silently did nothing.
    The action now travels as plain provider/id/label attributes read individually."""
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert "data-vool-price-review-id=" in html
    assert "data-vool-price-review-provider=" in html
    assert "JSON.parse(button.getAttribute('data-vool-price-review')" not in html


def test_a_live_failure_still_keeps_the_draft() -> None:
    assert "if (!outcome.saved) { setGateBusy(false); showGateError(outcome.error); return; }" in _GATE_JS
    assert _GATE_JS.count("showGateError(outcome.error)") == 2
