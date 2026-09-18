"""RSS pass 3 — evidence, not word-collision. AUD-20260829-003, RED-1 NEW-1/NEW-2/NEW-4 + D4.

Pass 2 removed the render gate so every unanswered slot would be disclosed. RED-1 attacked the
result and found the failure direction had INVERTED rather than resolved: under-mint 20 -> 0,
over-mint 6 -> 12, and two outcomes worse for a reader than the original silent drop, because
the runtime now contradicted itself inside one reply.

NEW-1 (P0) — THE RUNTIME ANSWERED, THEN DENIED IT.

    what is 18^2   ->   "18² = 324"
                        "Could not be answered:  * what is 18^2 — no answering lane claimed …"
                        {demand_satisfied: 0, covered: false}

    Structural, not a tuning miss. Discharge kept only tokens passing `len >= 3 and not a stop
    word`; for `what is 18^2` that leaves the EMPTY SET, so no answer could ever discharge it.
    A number is the most identifying token a request can carry and it was being dropped for
    being short. Two rules fix the class: numbers anchor at any length, and a unit with NO
    anchors is satisfied — it names nothing to look up, so nothing can have gone missing.
    (`18²` also needed digit folding: SUPERSCRIPT TWO is a digit-valued character `[0-9]` does
    not match, so the answer and the question tokenised differently.)

NEW-2 (P0) — THE RAW-OUTPUT CONTRACT BROKEN IN SERVED BYTES.

    "Print comma-separated tokens (A1, B2, C3). Only the list."
        -> a correct `A1, B2, C3` with FOUR denial rows glued underneath it.

    The turn's contract is literally "Only the list." The sweep overrode a shipped contract
    (`b148d2cc`, "enforce raw output-only contracts"). Read the contract from the module that
    owns it, never from a phrase list: under a literal-output contract the bytes ARE the
    deliverable, so nothing is appended and nothing is claimed unanswered.

NEW-4 (P1) — THE LADDER RE-CREATED THE DEFECT IT REPLACED.

    "1000 EUR to USD, and the water temperature in the United States"
        -> `Kanona, United States of America: Sunny, 36.0°C`
           {covered: true, demand_satisfied: 2}

    One slot's WRONG-LOCATION answer discharged a DIFFERENT slot through the shared tokens
    `united`/`states`, and the false-coverage certificate — the whole reason this audit exists
    — came back through the new mechanism. Token overlap is not evidence of answering. Evidence
    now requires EVERY anchor of a unit; a partial match is `indeterminate`, which claims
    nothing, renders nothing, and does not count as covered.

    NEW-1 and NEW-4 are one seam read from two sides: absence of evidence was read as evidence
    of absence, and any evidence as evidence of completeness. Neither holds.

D4 — the sweep sits centrally in `finalize_answer`, but was armed by a mint with exactly one
caller, so the second entrance certified nothing. The entrance now arms itself.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.agent_runtime.answer_coverage import (
    DEMAND_INDETERMINATE,
    DEMAND_SATISFIED,
    DEMAND_UNANSWERED,
    demand_units,
    unit_anchors,
    unit_answer_evidence,
)
from core.conductor import obligation_ledger as ol
from core.finalization import finalize_answer
from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "rss3.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)
    ol.clear_active_set()


def _serve(request: str, answer: str, *, tag: str = "t") -> dict:
    opened = ol.open_obligation_set(
        request_text=request,
        obligations=[
            {"obligation_id": f"ob:{tag}:answer", "text": request[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:{tag}:demand:{unit.unit_id}",
                    "text": unit.text,
                    "kind": "demand",
                    "unit_id": unit.unit_id,
                    "slice_id": unit.slice_id,
                }
                for unit in demand_units(request)
            ),
        ],
    )
    ol.record_disposition(
        opened["set_id"], opened["version"], f"ob:{tag}:answer",
        "satisfied", evidence_source="served_bytes",
    )
    reset_admission()
    admit_semantic_result({"response": answer, "route_reason": "probe"})
    return finalize_answer(
        turn_id=tag,
        canonical_content=answer,
        closure={**ol.closure_verdict(opened["set_id"], opened["version"]),
                 "set_id": opened["set_id"]},
    )


# ------------------------------------------------------------------ NEW-1


@pytest.mark.parametrize(
    "prompt,answer",
    (
        ("what is 18^2", "18² = 324"),                                  # the reported string
        ("what is 2+2?", "2 + 2 = 4."),
        ("say ok", "Ok."),
        ("18^2", "324"),                                                # paraphrase, no verb
        ("calculate 18^2 please", "18^2 = 324"),
        ("whats 18 squared", "18 squared is 324."),                     # sloppy, no apostrophe
        ("2+2", "4"),
        ("what is 7 x 6?", "7 × 6 = 42."),
        ("wat is 18^2", "18² = 324"),                                   # sloppy typo
        ("say ok pls", "Ok."),
    ),
)
def test_an_answered_turn_is_never_denied(fresh_store, prompt, answer):
    """I7 family for NEW-1. A correct answer must never be published with a denial of itself."""
    commit = _serve(prompt, answer)
    verdict = commit["closure_verdict"]
    assert commit["canonical_content"] == answer, "the sweep appended to a correct answer"
    assert verdict["demand_unanswered"] == 0, "a served answer was called a dropped slot"
    assert verdict["demand_rendered"] == 0
    # `covered` is a STRONG claim — every anchor of every slot present in the answer. A terse
    # value-only reply ("324") earns `indeterminate`, not `covered`, and that is the honest
    # reading: the runtime cannot show the bytes addressed the request. What it must never do
    # is turn that into an accusation, which is what `demand_unanswered == 0` pins.
    assert verdict["covered"] is (verdict["demand_indeterminate"] == 0)
    reset_admission()


def test_a_number_anchors_however_short_it_is():
    """The structural cause: `len(token) >= 3` dropped every number in the request."""
    assert unit_anchors("what is 18^2") == ("18", "2")
    assert unit_answer_evidence("what is 18^2", "18² = 324") == {"u1": DEMAND_SATISFIED}


def test_a_unit_that_names_nothing_is_not_an_accusation():
    """NEGATIVE CONTROL / the second half of NEW-1: no anchors means nothing can be missing.

    RESTATED FOR C7 (2026-09-05). The invariant here has always been that a unit naming
    nothing may never be ACCUSED -- it is the guard against the runtime denying work it did.
    It previously carried that by grading such a unit `satisfied`, which quietly asserted the
    opposite claim: that the unit had been answered. Measured live, that let "how hot is the
    sun" discharge itself against an empty answer.

    The invariant is unchanged and is asserted directly. What is gone is the discharge it used
    to ride on -- naming nothing to check buys silence, never coverage.
    """
    assert unit_anchors("say ok") == ()
    assert unit_anchors("C.") == ()
    assert unit_answer_evidence("say ok", "Ok.")["u1"] != DEMAND_UNANSWERED
    assert unit_answer_evidence("say ok", "")["u1"] != DEMAND_UNANSWERED
    # ... and it no longer claims to have been answered either.
    assert unit_answer_evidence("say ok", "")["u1"] == DEMAND_INDETERMINATE


def test_a_genuinely_dropped_slot_is_not_accused_from_the_ladder_alone(fresh_store):
    """ADVERSARIAL NEAR-MISS, restated by Phase A (PLAN-discharge-channel.md §3, 2026-08-30).

    This slot IS genuinely dropped — only 18^2 was answered — and this test used to pin the
    refusal row that said so. The same zero-echo verdict publicly disowned the correctly-
    served weather turn (`what is the weather now in nY?` → answered from wttr.in, accused
    anyway, measured live), because absence of lexical echo is not evidence of absence. The
    ladder's floor is now `indeterminate`: no row, no accusation, no coverage claim. Naming a
    real drop again is Phase B4's job, sourced from the dispatch record — never from this
    ladder.
    """
    commit = _serve(
        "what is 18^2, and what is the weather in Rome",
        "18² = 324",
    )
    assert "weather in Rome" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    assert commit["closure_verdict"]["demand_indeterminate"] == 1
    assert commit["closure_verdict"]["covered"] is False


# ------------------------------------------------------------------ NEW-2


@pytest.mark.parametrize(
    "prompt,answer",
    (
        ("Print comma-separated tokens (A1, B2, C3). Only the list.", "A1, B2, C3"),
        ("Output only the list.", "red, green, blue"),
        ("Answer with JSON only.", '{"ok": true}'),
        ("say one thousand fifty six time twenty five result in number only", "26400"),
        ("reply with just the number", "42"),
        ("what time is it in berlin Output exactly one word. asap", "03:12"),
    ),
)
def test_a_literal_output_contract_receives_no_appended_rows(fresh_store, prompt, answer):
    """I7 family for NEW-2. Under a literal-output contract the bytes ARE the deliverable."""
    commit = _serve(prompt, answer)
    assert commit["canonical_content"] == answer
    assert "Could not be answered" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_rendered"] == 0
    # And no accusation either: a literal-output turn is not decomposed into dropped slots.
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    reset_admission()


def test_the_contract_is_read_from_the_module_that_owns_it():
    """Not a phrase list here: the same predicate the runtime enforces the contract with."""
    from core.finalization import _under_literal_output_contract

    assert _under_literal_output_contract(
        "Print comma-separated tokens (A1, B2, C3). Only the list."
    ) is True
    assert _under_literal_output_contract("Answer with JSON only.") is True
    # NEGATIVE CONTROLS — the multi-slot shapes this whole repair exists for are NOT contracts.
    assert _under_literal_output_contract(
        "1000 EUR to RUB, gold with it, weather in Rome, baltic sea water temp"
    ) is False
    assert _under_literal_output_contract("1000 TRY to USD?") is False


def test_an_ordinary_turn_still_gets_terminal_accounting(fresh_store):
    """ADVERSARIAL, restated by Phase A (PLAN-discharge-channel.md §3, 2026-08-30).

    The contract exemption must not become a general silencer — the sweep still runs on an
    ordinary turn, still mints, still drives every slot terminal, and the certificate still
    tells the truth about the unserved slot. What it may no longer do is ACCUSE from the
    text ladder: zero echo for the Rome slot used to render a refusal row here, and the same
    verdict disowned the correctly-served weather turn measured live. The unserved slot is
    `indeterminate`; naming real drops returns with the dispatch record in Phase B4."""
    commit = _serve("1000 EUR to RUB, weather in Rome", "1000 EUR = 99,800 RUB.")
    assert "weather in Rome" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    assert commit["closure_verdict"]["demand_indeterminate"] == 1
    assert commit["closure_verdict"]["demand_open"] == 0
    assert commit["closure_verdict"]["covered"] is False


# ------------------------------------------------------------------ NEW-4


NEW4_PROMPT = "1000 EUR to USD, and the water temperature in the United States"
NEW4_ANSWER = (
    "EUR/USD: 1000 EUR × 1.1652 = 1165.2000 USD (source: Frankfurter).\n"
    "Kanona, United States of America: Sunny, 36.0°C (source: wttr.in)"
)


def test_a_shared_word_cannot_discharge_a_different_slot(fresh_store):
    """NEW-4, the reported string. `united`/`states` came from ANOTHER slot's wrong-location
    answer; the water-temperature slot must not be credited by them, and the certificate must
    not go back to claiming coverage."""
    evidence = unit_answer_evidence(NEW4_PROMPT, NEW4_ANSWER)
    assert evidence["u2"] != DEMAND_SATISFIED
    commit = _serve(NEW4_PROMPT, NEW4_ANSWER)
    verdict = commit["closure_verdict"]
    assert verdict["demand_satisfied"] == 1
    assert verdict["demand_indeterminate"] == 1
    assert verdict["covered"] is False


def test_partial_evidence_claims_nothing_and_renders_nothing(fresh_store):
    """`indeterminate` is a refusal to guess in BOTH directions: no credit, and no accusation."""
    commit = _serve(NEW4_PROMPT, NEW4_ANSWER)
    assert "Could not be answered" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_rendered"] == 0
    assert commit["closure_verdict"]["demand_unanswered"] == 0


def test_the_control_with_no_shared_token_still_renders(fresh_store):
    """RED-1's own isolating control: no shared word, so the drop is provable and disclosed.

    PHASE A (PLAN-discharge-channel.md §3, 2026-08-30) changes what the sweep may claim from
    the text ladder alone. This turn WAS genuinely only half-served — the population slot got
    nothing — but the ladder cannot PROVE that (absence of echo is not evidence of absence:
    the same zero-echo verdict publicly disowned the correctly-served weather answer, measured
    live). The accusation now needs the dispatch record (Phase B4). The control pins the
    Phase A contract: the unserved slot renders NOTHING, claims nothing, and the certificate
    stops asserting coverage.
    """
    commit = _serve(
        "convert 100 EUR to GBP and tell me the population of Europe",
        "100 EUR × 0.85 = 85.00 GBP (source: Frankfurter).",
    )
    assert "population of Europe" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    assert commit["closure_verdict"]["demand_indeterminate"] == 1
    assert commit["closure_verdict"]["covered"] is False
    assert commit["closure_verdict"]["covered"] is False


@pytest.mark.parametrize(
    "answer,expected",
    (
        # Every anchor present -> answered.
        ("water temperature in the United States: 14°C", DEMAND_SATISFIED),
        # Some anchors, not all -> the runtime cannot tell, and says so by saying nothing.
        ("Kanona, United States of America: Sunny, 36.0°C", DEMAND_INDETERMINATE),
        ("The water is warm today.", DEMAND_INDETERMINATE),
        # No anchor at all -> PHASE A (PLAN-discharge-channel.md §3): the ladder may no
        # longer accuse. Zero echo used to read `unanswered` and render a public refusal
        # (the "weather"-anchor case, measured live); absence of lexical echo is not
        # evidence of absence, so the ladder's floor is `indeterminate`. Provable
        # `unanswered` returns only via the dispatch record (Phase B4).
        ("1000 EUR = 1165.20 USD.", DEMAND_INDETERMINATE),
    ),
)
def test_the_evidence_ladder_is_three_valued(answer, expected):
    assert unit_answer_evidence(NEW4_PROMPT, answer)["u2"] == expected


def test_covered_requires_evidence_for_every_slot(fresh_store):
    """`covered: true` is asserted from evidence, never from the absence of a denial."""
    full = _serve(
        NEW4_PROMPT,
        "1000 EUR = 1165.20 USD. Water temperature in the United States: 14°C.",
    )
    assert full["closure_verdict"]["covered"] is True
    assert full["closure_verdict"]["demand_indeterminate"] == 0


# ------------------------------------------------------------------ D4


def test_the_entrance_arms_its_own_demand_set():
    """D4. The sweep is central, but a sweep with no bound set is a no-op — and the set was
    minted in one place with one caller. The entrance now mints its own."""
    from apps.vool_agent import _arm_demand_set_for_entrance, _release_entrance_demand_set

    assert ol.active_set() is None
    armed = _arm_demand_set_for_entrance("1000 EUR to RUB, weather in Rome")
    try:
        assert armed is not None
        assert ol.active_set() == (armed["set_id"], armed["version"])
        assert len(ol.demand_obligations(armed["set_id"], armed["version"])) == 2
    finally:
        _release_entrance_demand_set()
    assert ol.active_set() is None


def test_the_entrance_never_re_arms_a_turn_that_already_has_a_set(fresh_store):
    """One turn, one demand set: the `run_once` spine's mint must win."""
    from apps.vool_agent import _arm_demand_set_for_entrance

    opened = ol.open_obligation_set(request_text="x", obligations=[])
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        assert _arm_demand_set_for_entrance("1000 EUR to RUB, weather in Rome") is None
    finally:
        ol.clear_active_set()
        del token


def test_the_entrance_stays_transparent_to_a_bare_lane_result():
    """`_handle_turn_frontdoor` returns the lane's own object and frozen tests pin it exactly.
    A result with no lane markers is handed back untouched."""
    from apps.vool_agent import _certify_entrance_turn

    outcome = {"result": {"response": "saved"}}
    _certify_entrance_turn(outcome, {"set_id": "s", "version": "v"})
    assert outcome == {"result": {"response": "saved"}}


# ------------------------------------------------------------------ NEW-5


def test_a_model_quoting_the_disclosure_header_is_not_a_disclosure(fresh_store):
    """NEW-5. Asked what "Could not be answered:" means, the model answers by quoting it —
    and `answering_body` cut the whole correct answer away, leaving no evidence and a denial.
    A header OPENS a line; a quotation mark in front of it is the difference between making a
    disclosure and talking about one."""
    from core.agent_runtime.answer_coverage import answering_body

    prose = '"Could not be answered:" in a status report means the data was missing.'
    assert answering_body(prose) == prose
    # A real disclosure section is still cut off.
    assert answering_body("A: 1.\nCould not be answered:\n- gold") == "A: 1.\n"

    commit = _serve(
        'What does the phrase "Could not be answered:" mean in a status report?', prose
    )
    assert commit["canonical_content"] == prose
    assert commit["closure_verdict"]["demand_unanswered"] == 0


def test_no_unit_in_the_attack_corpus_is_undischargeable():
    """The NEW-1 class, closed by construction over RED-1's own corpus: every unit must be
    dischargeable by SOME answer. A unit that fails this could never be discharged at all,
    which is what made the runtime deny work it had done.

    RESTATED FOR C7 (2026-09-05). The discharge used to be the unit's own text handed back as
    the answer -- and that is precisely the shape a prose refusal has, which is how "The water
    temperature in the Baltic Sea is not available." discharged its own slot. Restating a
    question is not answering it.

    So each unit is now answered the way an answer actually looks: its own words plus an
    object -- a named source and a value. The invariant under test is unchanged and the bar is
    higher, not lower: every unit in the corpus that names something must still be
    dischargeable, and now only by something that carries a result.

    A unit that names NOTHING is held to the other half of the same law: it can never be
    accused, and it can never be claimed either. Both halves are asserted, so neither can
    regress silently.
    """
    import tests.red_d_sweep_attack as red_d

    corpus = [
        getattr(entry, "prompt", None) or getattr(entry, "text", "") for entry in red_d.CORPUS
    ]
    undischargeable = []
    accused = []
    for text in [item for item in corpus if item]:
        units = demand_units(text)
        answer = "\n".join(f"{unit.text} Reported by Vool as 42." for unit in units)
        evidence = unit_answer_evidence(text, answer)
        for unit in units:
            verdict = evidence.get(unit.unit_id)
            if verdict == DEMAND_UNANSWERED:
                accused.append(unit.text)
            if unit_anchors(unit.text) and verdict != DEMAND_SATISFIED:
                undischargeable.append(unit.text)
    assert undischargeable == []
    assert accused == [], "the text ladder may never accuse (Phase A)"


@pytest.mark.parametrize(
    "text",
    (
        # NEW-3: a prohibition can only ever be reported unmet. It is not a demand.
        "Do not send any money. Just tell me the EUR/USD rate.",
        "Don't guess. What is 100 EUR in USD?",
        "Never use a cached rate. What is 100 EUR in USD?",
    ),
)
def test_a_prohibition_is_not_minted_as_a_demand(text):
    units = demand_units(text)
    assert len(units) == 1, [unit.text for unit in units]


def test_a_restated_slot_is_minted_once():
    """The minted set is a SET: a second obligation for one slot can never be discharged."""
    assert len(demand_units("100 EUR to USD, and again 100 EUR to USD")) == 1
    # ... but two DIFFERENT conversions are still two slots.
    assert len(demand_units("100 EUR to USD, and 500 CHF to JPY")) == 2


# =================================================================================================
# C7 — "failed slot marked unavailable with a reason, NEVER silently dropped"
#
# FINAL_SIGNOFF grades C7 FAIL on three independent live reproductions. These pin the three
# mechanisms as laws, so a regression fails a test that names which one came back.
# =================================================================================================


def test_a_unit_that_names_nothing_is_never_satisfied_over_an_empty_answer():
    """MECHANISM (i). Measured at F0: `unit_answer_evidence("how hot is the sun", "")`
    returned `{"u1": "satisfied"}` — a slot the runtime never touched, discharged by the
    absence of anything to check.

    "Nothing to look up" and "answered" are different claims. The first cannot buy the
    second, least of all over an empty answer. L5: satisfaction needs non-empty evidence
    bound to the slot.
    """
    assert unit_answer_evidence("how hot is the sun", "") != {"u1": DEMAND_SATISFIED}
    assert unit_answer_evidence("how hot is the sun", "")["u1"] == DEMAND_INDETERMINATE
    # And inside a real two-slot turn: the no-anchor slot must not self-discharge while its
    # sibling correctly reads indeterminate.
    both = unit_answer_evidence("what is 1000 EUR to RUB, and how hot is the sun", "")
    assert DEMAND_SATISFIED not in both.values(), both


def test_a_lowercase_currency_code_anchors_like_an_uppercase_one():
    """MECHANISM (ii), first half. `_CODE_RE` matched uppercase only, and a 3-letter code
    falls under the 4-character content-word floor — so an ordinary lowercase ask anchored
    on its digits alone. Measured at F0: `("100",)` for a two-currency conversion.
    """
    assert unit_anchors("convert 100 eur to gbp") == ("100", "eur", "gbp")
    # Unchanged for the uppercase form, which always worked.
    assert unit_anchors("convert 100 EUR to USD") == ("100", "eur", "usd")


def test_a_second_ask_is_minted_even_when_its_numbers_match_the_first():
    """MECHANISM (ii), second half. Mint dedup compared ANCHOR SETS, so two different asks
    that happened to share their anchors collapsed to one. With lowercase codes invisible,
    "100 eur to usd" and "100 gbp to usd" both anchored to `("100",)` and the GBP ask was
    annihilated at mint — a slot that was never minted cannot be preserved or reported.
    """
    assert len(demand_units("convert 100 eur to usd and convert 100 gbp to usd")) == 2
    assert len(demand_units("convert 100 EUR to USD and convert 100 GBP to USD")) == 2
    # The restatement law it must not break: the SAME ask twice is still one slot.
    assert len(demand_units("100 EUR to USD, and again 100 EUR to USD")) == 1


def test_a_prose_refusal_never_discharges_its_own_slot():
    """MECHANISM (iii). Measured at F0: a turn answering one slot and REFUSING the other in
    prose graded BOTH satisfied — the refusal restates the slot, and a lexical ladder reads
    its own words coming back as evidence it answered.

    The law is the one the tree already applies to requests: an echo that adds no object of
    its own is a restatement, not an answer. A refusal names the slot and supplies nothing.
    """
    text = "What is 1000 EUR to RUB, and what is the water temperature in the Baltic Sea?"
    refusing = (
        "1000 EUR = 99,800 RUB.\n"
        "The water temperature in the Baltic Sea is not available."
    )
    verdicts = unit_answer_evidence(text, refusing)
    assert verdicts["u1"] == DEMAND_SATISFIED, "the answered slot must stay answered"
    assert verdicts["u2"] != DEMAND_SATISFIED, verdicts
    # A real reading of the same slot, in the same shape, still discharges it: the law is
    # about the absence of an object, never about the words a refusal happens to use.
    answering = (
        "1000 EUR = 99,800 RUB.\n"
        "Water temperature in the Baltic Sea: 17.2 °C (source: open-meteo.com (marine))."
    )
    assert unit_answer_evidence(text, answering)["u2"] == DEMAND_SATISFIED


def test_a_literal_turn_counts_the_rows_it_withheld(fresh_store):
    """MECHANISM (iii), the accounting half. L6: under an output-shape clause the served
    bytes are not decorated AND the certificate records every withheld demand truthfully.

    A MATCHED PAIR, so the count is measured against a known drop rather than asserted in a
    vacuum: the same two-slot request served the same partial answer, once plainly and once
    under a literal-output contract. The plain turn discloses the dropped slot; the literal
    turn must withhold the ROW and still say how many rows it withheld.

    Measured at F0: `demand_render_withheld: 0` on the literal arm, beside an in-source
    comment claiming 'the certificate still tells the truth'. Withholding a row and reporting
    that nothing was withheld is the silent drop C7 is about, moved into the certificate.
    """
    plain = _serve(
        "What is the weather in Rome, and what is the water temperature in the Baltic Sea?",
        "Rome: Sunny, 31 °C (source: wttr.in).",
        tag="pair-plain",
    )["closure_verdict"]
    reset_admission()
    literal = _serve(
        "What is the weather in Rome, and what is the water temperature in the Baltic Sea? "
        "Answer with JSON only.",
        '{"rome": "31C"}',
        tag="pair-literal",
    )["closure_verdict"]
    reset_admission()

    # The control: the plain turn knows a slot went unserved.
    assert plain["demand_minted"] >= 2, plain
    assert plain["covered"] is False, plain

    # The literal turn withholds the ROW -- that part already worked and must not regress.
    assert literal["demand_rendered"] == 0, literal
    # ... and it must say how many it withheld. This is the pin.
    assert literal["demand_render_withheld"] >= 1, literal
    assert literal["covered"] is False, literal


EVENING = (
    "ok so u think u so cool heh? ok what about 1500 eur to usd? then how much and how much  "
    "god i can buy iwth it? and thne tell me the water tempperature in baltc sea and in  "
    "berling now :D"
)


def test_a_mistyped_connective_is_not_a_content_anchor():
    """C8, measured on a real served turn. The operator's `thne` -- one transposition from the
    connective `then` -- was minted as a CONTENT anchor of the water slot.

    An anchor is a token a truthful answer would have to mention. No answer about Baltic water
    temperature contains "thne". Carrying it made the slot unmatchable against its own answer:
    the runtime served "Water temperature at Jurmala, Latvia (Baltic Sea): 17.8°C" and then
    listed that same slot under "Could not be answered", because the disclosure check needs
    every anchor present and this one can never be.

    This module already tolerates the operator's typos for selectors and demand heads, with a
    closed edit budget (`_near_miss`: adjacent transposition or one inserted/deleted character,
    never a substitution). The stop-token check is held to the same tolerance.
    """
    anchors = unit_anchors("and thne tell me the water tempperature in baltc sea")
    assert "thne" not in anchors, anchors
    # The operator's typos of REAL content words still anchor -- the tolerance runs one way.
    assert "tempperature" in anchors and "baltc" in anchors, anchors


def test_a_served_slot_is_not_also_listed_as_unanswered(fresh_store):
    """C8 -- "no slot listed as unanswered when it was answered" -- on the evening turn's own
    shape: the answer carries the Baltic reading, so the slot must not also be disclosed."""
    from core.agent_runtime.answer_coverage import unit_is_disclosed_in

    unit = "and thne tell me the water tempperature in baltc sea"
    served = (
        "Water temperature at Jurmala, Latvia (Baltic Sea): 17.8°C (64.0°F) "
        "(source: open-meteo.com (marine))"
    )
    assert unit_is_disclosed_in(unit, served), "the served answer does not match its own slot"


def test_a_mistyped_connective_does_not_swallow_the_request_behind_it():
    """C5/C7/C8, root cause of the evening turn's certificate disagreeing with its own bytes.

    "and thne tell me the water tempperature in baltc sea" opens a genuinely fresh request
    whose HEAD is "tell". The leader skip is spelling-exact while the head test is
    typo-tolerant, so the loop met "thne" -- one transposition from the leader "then" -- did
    not recognise it as a leader, tested it as a HEAD, and returned False.

    The fragment was then fused into the preceding EXECUTION unit, and because execution units
    are renumbered into the same `uN` namespace the mint uses, the fused group's id aliased a
    different obligation. Measured consequence on the served turn: a water reading the runtime
    had just served was filed against the GOLD obligation, gold certified satisfied with no
    gold bytes, and the water slot was listed as "not dispatched" three lines under its own
    answer.
    """
    from core.agent_runtime.answer_coverage import opens_a_fresh_request
    from core.agent_runtime.demand_ownership import execution_unit_spans

    assert opens_a_fresh_request("and thne tell me the water tempperature in baltc sea")
    # Correctly spelled, this always worked -- the pin is that the typo agrees with it.
    assert opens_a_fresh_request("and then tell me the water temperature in the Baltic sea")

    spans = execution_unit_spans(EVENING)
    texts = [u.text for u in spans]
    assert any("water tempperature" in t for t in texts), texts
    # The water ask is its OWN execution unit, not glued onto the gold ask.
    fused = [t for t in texts if "god i can buy" in t and "water tempperature" in t]
    assert not fused, f"the water ask was fused into the gold unit: {fused}"
