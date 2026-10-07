"""Paged assembly exact-accounting and honest-disposition contract (F08/F09 repairs).

The receipts this module pins were measured as lies at e821457d:

1. ``proof.used_chars`` counted only the appended parts — never the ``"\\n\\n"``
   join separators nor the trailing ``"Current message: "`` wrapper — so the
   receipt under-reported the exact text the model receives.
2. ``remaining`` was computed once after the spine and never recomputed, so the
   previous-answer artifact and the cold headers budgeted against money already
   spent and the assembly shipped over ``budget_chars``.
3. The hot tier rendered ``a[:400]`` while the receipt claimed
   ``verbatim ({len(q)}+{len(a)} chars)`` — a clipped tail labelled verbatim,
   with no resolvable reference to the omitted bytes.

The law this file enforces: the receipt is measured on the exact shipped text;
mandatory spine content fits or the assembly refuses; optional content may be
partial but says so and carries an authorized, resolvable span reference.
"""
from __future__ import annotations

import pytest

from core.kernel.paged_memory import ContextUndercovered, PagedSession, recall_handles

# Fresh fixtures — bakery/observatory domain, deliberately unlike the law-file
# and audit wording: new facts, new expected values.
BAKERY_ORDER = (
    "order 17 rye loaves and 4 trays of croissants for the Friday market stall"
)
BAKERY_ANSWER_LONG = (
    "order confirmed: " + ("rye and croissant line items with batch codes " * 22)
    + "FINAL_ITEM_GATEAU9 — the last line the customer always asks about"
)  # > 400 chars, sentinel tail
OBS_QUESTION = "what aperture does the dome scheduler lock at site SVO-7?"


def _bakery_session(pager: PagedSession) -> None:
    """The long answer is admitted LAST so it sits in the hot window — the clip
    path under test is the hot tier's rendering of the newest turn."""
    for i in range(1, 8):
        pager.admit_turn(f"filler note {i}", f"ok {i}", turn_index=i)
    pager.admit_turn(BAKERY_ORDER, BAKERY_ANSWER_LONG, turn_index=8)


# -- receipt truth ------------------------------------------------------------------------


def test_receipt_counts_the_exact_shipped_text():
    """used_chars must equal len(text): separators, wrapper suffix and all."""
    pager = PagedSession()
    _bakery_session(pager)
    rows = [{"id": "OB1", "description": "calibrate dome SVO-7", "reason": "user asked"}]
    assembly = pager.assemble(OBS_QUESTION, ledger_rows=rows, budget_chars=6000)
    assert assembly.text, "a session with history and a spine must ship context"
    assert assembly.proof.used_chars == len(assembly.text)


def test_assembly_never_overspends_the_budget():
    """Every shipped assembly, tight or loose, stays within budget_chars."""
    pager = PagedSession()
    _bakery_session(pager)
    rows = [{"id": "OB1", "description": "calibrate dome SVO-7", "reason": "user asked"}]
    for budget in (400, 600, 900, 1400, 2000, 6000):
        assembly = pager.assemble(OBS_QUESTION, ledger_rows=rows, budget_chars=budget)
        assert len(assembly.text) <= budget, (
            f"budget {budget} overspent: shipped {len(assembly.text)} chars "
            f"(receipt claims {assembly.proof.used_chars})"
        )
        assert assembly.proof.used_chars == len(assembly.text)


def test_spine_exact_fit_boundary_includes_the_wrapper():
    """The wrapper suffix is part of what the model receives, so it is part of the
    budget: a spine sized exactly to the budget must REFUSE (mandatory content
    cannot ship complete), and one wrapper's worth more must ship exactly."""
    pager = PagedSession()
    rows = [{"id": "OB9", "description": "close the observatory ledger", "reason": "user asked"}]
    assembly = pager.assemble("status?", ledger_rows=rows, hot_history=[], budget_chars=6000)
    spine_len = len(assembly.text.split("\n\n", 1)[0])
    with pytest.raises(ContextUndercovered):
        pager.assemble("status?", ledger_rows=rows, hot_history=[], budget_chars=spine_len)
    fits = pager.assemble(
        "status?", ledger_rows=rows, hot_history=[], budget_chars=spine_len + 20,
    )
    assert len(fits.text) <= spine_len + 20
    assert fits.proof.used_chars == len(fits.text)
    assert fits.text.endswith("Current message: ")


# -- disposition honesty -------------------------------------------------------------------


def test_long_assistant_tail_is_delivered_or_declared_partial_with_reference():
    """The >400-char answer: either the tail reaches the model, or the receipt
    names the span partial AND the remainder is resolvable through an authorized
    recall handle that actually returns the tail bytes."""
    pager = PagedSession()
    _bakery_session(pager)
    assembly = pager.assemble(
        "what was in the Friday order?", ledger_rows=[], hot_history=[], budget_chars=6000,
    )
    tail_present = "FINAL_ITEM_GATEAU9" in assembly.text
    hot_rows = [r for r in assembly.proof.rows if r.tier == "hot"]
    partial_declared = hot_rows and any("partial" in r.detail for r in hot_rows)
    assert tail_present or partial_declared, (
        "a clipped tail must never be labelled verbatim — say partial, or ship it"
    )
    if not tail_present:
        handles = recall_handles(assembly.text)
        assert handles, "a partial hot entry needs a resolvable reference"
        assert any(
            "FINAL_ITEM_GATEAU9" in pager.recall(h) for h in handles
        ), "the referenced span must actually contain the omitted tail"


def test_no_row_claims_verbatim_when_bytes_were_clipped():
    pager = PagedSession()
    _bakery_session(pager)
    tight = pager.assemble(
        "what was in the Friday order?", ledger_rows=[], hot_history=[], budget_chars=700,
    )
    for row in tight.proof.rows:
        if row.tier == "hot":
            full_len = len(BAKERY_ORDER) + len(BAKERY_ANSWER_LONG)
            if f"verbatim ({full_len}" in row.detail or (
                "verbatim" in row.detail and "partial" not in row.detail
            ):
                shipped_answer = tight.text.split("assistant: ")[-1]
                pytest.fail(
                    f"row claims verbatim but the shipped answer is "
                    f"{len(shipped_answer)} chars of a {len(BAKERY_ANSWER_LONG)}-char original: {row.detail}"
                )


def test_hot_history_entries_get_honest_dispositions_too():
    """Caller-supplied hot_history (the REPL shape) obeys the same law: the
    receipt must distinguish full from partial renderings."""
    pager = PagedSession()
    long_answer = "x" * 900
    assembly = pager.assemble(
        "next?",
        ledger_rows=[],
        hot_history=[("q1", long_answer)],
        budget_chars=1500,
    )
    hot_rows = [r for r in assembly.proof.rows if r.tier == "hot"]
    assert hot_rows, "the hot entry was shipped; it must be receipted"
    shipped = assembly.text
    rendered_full = long_answer in shipped
    for row in hot_rows:
        if "verbatim" in row.detail:
            assert rendered_full, (
                "verbatim claim requires the full answer in the shipped text"
            )


def test_optional_content_omitted_under_tight_budget_is_stated():
    """Under a budget that fits the spine but nothing else, no rows may be
    receipted for unshipped content — and the spine still ships."""
    pager = PagedSession()
    _bakery_session(pager)
    rows = [{"id": "OB1", "description": "calibrate dome SVO-7", "reason": "user asked"}]
    roomy = pager.assemble("status?", ledger_rows=rows, hot_history=[], budget_chars=6000)
    spine_len = len(roomy.text.split("\n\n", 1)[0])
    tight = pager.assemble(OBS_QUESTION, ledger_rows=rows, budget_chars=spine_len + 40)
    assert any(r.tier == "spine" for r in tight.proof.rows)
    shipped = {r.tier for r in tight.proof.rows}
    assert shipped <= {"spine", "hot", "cold_hit"}
    assert len(tight.text) <= spine_len + 40
    assert tight.proof.used_chars == len(tight.text)


# -- the law still holds -------------------------------------------------------------------


def test_spine_refusal_is_still_fail_closed_after_accounting_repair():
    pager = PagedSession()
    rows = [{"id": "OB1", "description": "z" * 5_000, "reason": "r"}]
    with pytest.raises(ContextUndercovered):
        pager.assemble("q", ledger_rows=rows, budget_chars=600)
