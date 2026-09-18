"""RSS pass 2 — the three defects that only appeared over HTTP. AUD-20260829-003.

Pass 1 landed demand accounting and was verified in-process. Run as a live daemon and driven
over `/api/chat`, three things were wrong that no in-process drive had shown. Each is pinned
here by the exact string that produced it.

R1 — THE KILLER SHAPE MINTED ONE UNIT FOR A FOUR-SLOT REQUEST.

    "1000 EUR to RUB, gold with it, weather in Rome, baltic sea water temp"
        served: the FX leg alone
        closure: {demand_minted: 1, demand_satisfied: 1, covered: true}

    Internally consistent and completely wrong — the root defect one layer down, with the
    accounting layer now telling the same lie the coverage layer used to. Every fragment is a
    bare noun phrase: no terminal "?", no clause verb, no demand head, so segmentation that
    waits for punctuation or interrogative form sees one unit. `_opens_a_phrase` reads STRUCTURE
    instead: a multi-token fragment after a comma is its own request; a single-token fragment is
    a list item ("price of xmr, xlm, bch" stays one ask, "Rome, Italy" stays one place).

R2 — TRUTHFUL COUNTING, SILENT BODY.

    the operator's verbatim evening turn
        closure: {demand_minted: 4, demand_satisfied: 1, demand_unanswered: 3,
                  demand_rendered: 0, covered: true}

    Two defects in one line. `demand_rendered: 0` because the render was gated on a registered
    family ALSO reading the slot — so the counting was honest and the served body was still just
    the FX line, which is the user experience the audit exists to end. Counting privately is not
    disclosure. And `covered: true` beside `demand_unanswered: 3` is a certificate contradicting
    its own census. Now: every unanswered slot renders, and `covered` is a claim about the
    REQUEST while `open_count` stays the structural signal that permits finalization — so the
    turn still ships. Fail-visible, never fail-dead.

R3 — SATISFACTION CREDITED FROM REFUSAL ROWS.

    the canonical four-slot prompt
        body: carries its own "Could not be answered:" section naming the gold and Baltic slots
        closure: {demand_minted: 4, demand_satisfied: 4, demand_unanswered: 0}

    The consumption reading looked for each unit's words in the served bytes and could not tell
    an answer from a refusal that quotes the question back. A refusal naming a slot is the
    strongest evidence the slot was NOT served. `answering_body` cuts the disclosure section off
    before the reading, so the count now matches what the body says about itself.

Deterministic environmental assertions throughout: segmentation, ledger state, certificate
counts, committed bytes. No expected prose, no prompt matching.
"""
from __future__ import annotations

import json

import pytest

import storage.db as sdb
from core.agent_runtime.answer_coverage import (
    answering_body,
    demand_units,
    unit_is_disclosed_in,
    units_present_in_answer,
)
from core.conductor import obligation_ledger as ol
from core.finalization import finalize_answer
from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

KILLER = "1000 EUR to RUB, gold with it, weather in Rome, baltic sea water temp"

OPERATOR_EVENING = (
    "ok so u think u so cool heh? ok what about 1500 eur to usd? then how much and how much  "
    "god i can buy iwth it? and thne tell me the water tempperature in baltc sea and in  "
    "berling now :D"
)

CANONICAL_FOUR_SLOT = (
    "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in Rome, "
    "and what is the water temperature in the Baltic Sea?"
)

FX_ONLY_ANSWER = (
    "1,000 EUR (euro) x 99.8 = 99,800 RUB (Russian ruble), using a live rate as of "
    "2026-08-29T00:00:00+00:00."
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "rss2.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)
    ol.clear_active_set()


def _serve(
    request: str,
    answer: str,
    *,
    receipts: tuple[str, ...] = (),
    lane_served: tuple[str, ...] = (),
    tag: str = "t",
) -> dict:
    """Mint, receipt, finalize — the whole accounting spine through the real seam."""
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
    for unit_id in receipts:
        ol.record_slice_consumption(
            opened["set_id"], opened["version"], unit_id=unit_id, evidence="test_receipt"
        )
    # WHAT THE PRODUCING LANE WRITES, for a unit it actually served: a lane-attested receipt
    # and a SUCCEEDED dispatch row. Post-B4 a provable `unanswered` comes only from the record
    # -- a unit the record shows was never dispatched, on a turn that dispatched something.
    # With no dispatch row anywhere the sweep cannot accuse at all, so a turn built from
    # receipts alone can never produce the disclosure this file is about: the count is 0 by
    # construction rather than by behaviour.
    for unit_id in lane_served:
        ol.record_slice_consumption(
            opened["set_id"],
            opened["version"],
            unit_id=unit_id,
            family="test_lane",
            evidence="slice_answer_record",
        )
        ol.record_slice_dispatch(
            opened["set_id"],
            opened["version"],
            unit_id=unit_id,
            subtask_id=f"{tag}:{unit_id}",
            operation="test_lane",
            state="SUCCEEDED",
        )
    ol.record_disposition(
        opened["set_id"],
        opened["version"],
        f"ob:{tag}:answer",
        "satisfied",
        evidence_source="served_bytes",
    )
    reset_admission()
    admit_semantic_result({"response": answer, "route_reason": "probe"})
    return finalize_answer(
        turn_id=tag,
        canonical_content=answer,
        closure={
            **ol.closure_verdict(opened["set_id"], opened["version"]),
            "set_id": opened["set_id"],
        },
    )


# ------------------------------------------------------------------ R1: segmentation


def test_the_killer_shape_mints_one_unit_per_slot():
    """R1, the exact served string. Four requests, no terminal '?', no clause verb anywhere."""
    units = demand_units(KILLER)
    assert len(units) == 4, [unit.text for unit in units]
    assert [unit.text for unit in units] == [
        "1000 EUR to RUB",
        "gold with it",
        "weather in Rome",
        "baltic sea water temp",
    ]


@pytest.mark.parametrize(
    "text,expected",
    (
        # I7 clean paraphrases: same four-slot demand, none of them interrogative, none with a
        # clause-ending mark before the very end.
        ("1000 EUR to RUB, gold with that, weather in Rome, water temp in the Baltic", 4),
        ("convert 1000 EUR to RUB, gold with it, the weather in Rome", 3),
        ("500 USD in GBP, silver with it, weather in Berlin", 3),
        ("1000 EUR to RUB, the gold price, Rome weather, Baltic water temperature", 4),
        ("100 CHF to JPY, gold with it, Tokyo weather, sea temperature in Okinawa", 4),
        ("2000 GBP to EUR, gold with that, weather in Paris, water temp in the Channel", 4),
    ),
)
def test_comma_run_paraphrases_mint_every_slot(text, expected):
    assert len(demand_units(text)) == expected, [u.text for u in demand_units(text)]


@pytest.mark.parametrize(
    "text,minimum",
    (
        # I7 sloppy variants in the operator's register: typos, no capitals, no punctuation.
        ("1000 eur to rub, gold wth it, wether in rome, baltc sea watr temp", 4),
        ("1500eur to usd, gold with it, temp in berling", 3),
        ("pls 1000 eur to rub, gold with it, weather rome", 3),
        ("1000 eur to rub, how much gold, whats the weather in rome", 3),
        ("1000 eur to rub, gold with it, baltc sea water temp pls", 3),
    ),
)
def test_sloppy_comma_runs_still_mint_every_slot(text, minimum):
    assert len(demand_units(text)) >= minimum, [u.text for u in demand_units(text)]


@pytest.mark.parametrize(
    "text",
    (
        # NEGATIVE CONTROLS. A comma inside ONE request must never split it.
        "what is the price of btc, eth, sol",
        "what is the weather in Rome, Italy",
        "1,000 EUR to RUB",
        "how much is 1,500.50 USD in EUR",
    ),
)
def test_enumerations_and_numbers_stay_one_unit(text):
    assert len(demand_units(text)) == 1, [u.text for u in demand_units(text)]


def test_a_tag_question_does_not_open_a_new_slot():
    """ADVERSARIAL NEAR-MISS: a comma followed by seven tokens that continue the SAME request."""
    assert len(demand_units("100 EUR to USD at 1.10, don't you think that's fair?")) == 1


def test_a_quoted_run_is_never_split_by_its_own_commas():
    """ADVERSARIAL: the traveler fixture is one clause by frozen contract and is full of commas."""
    traveler = (
        'A traveler says: "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore and '
        'came home with 2,100 kr." Explain whether the exchange was good or bad compared with '
        "the normal exchange rate."
    )
    assert len(demand_units(traveler)) == 1


# ------------------------------------------------------------------ R2: disclosure


def test_every_unserved_slot_is_accounted_but_not_accused_from_the_ladder(fresh_store):
    """R2, restated by Phase A (PLAN-discharge-channel.md §3, 2026-08-30). The operator's
    verbatim evening turn: the counting is still honest and every unserved slot still reaches
    a terminal state — but the RENDER is gone, because the zero-echo verdict that drove it
    also publicly disowned the correctly-served weather turn measured live. The certificate
    tells the truth; the reply carries no accusation. Naming real drops returns with the
    dispatch record (B4)."""
    commit = _serve(OPERATOR_EVENING, FX_ONLY_ANSWER, receipts=("u1",))
    verdict = commit["closure_verdict"]
    body = commit["canonical_content"]

    assert verdict["demand_indeterminate"] >= 2
    assert verdict["demand_unanswered"] == 0
    assert verdict["demand_rendered"] == 0
    assert "Could not be answered:" not in body
    assert body.startswith(FX_ONLY_ANSWER)


def test_the_certificate_does_not_claim_coverage_over_unserved_slots(fresh_store):
    """R2's second half: `covered: true` beside a census of unserved slots is a certificate
    contradicting its own census. Phase A: the unserved slots are `indeterminate` — the
    no-coverage claim is unchanged."""
    verdict = _serve(OPERATOR_EVENING, FX_ONLY_ANSWER, receipts=("u1",))["closure_verdict"]
    assert verdict["demand_indeterminate"] > 0
    assert verdict["demand_unanswered"] == 0
    assert verdict["covered"] is False


def test_the_turn_still_ships_when_nothing_is_answered(fresh_store):
    """FAIL-VISIBLE, NEVER FAIL-DEAD. `covered: false` must not become a refusal: structural
    terminality (open_count) is what permits finalization, and the sweep drives every slot
    terminal before that check is read."""
    commit = _serve(KILLER, FX_ONLY_ANSWER)
    assert commit["canonical_content"].startswith(FX_ONLY_ANSWER)
    assert commit["closure_verdict"]["open_count"] == 0
    assert commit["closure_verdict"]["covered"] is False
    assert commit["closure_verdict"]["demand_satisfied"] == 1  # the FX leg, from its own bytes
    # Phase A: the three unserved slots are `indeterminate` — terminal, honest in the
    # certificate, rendering nothing. Fail-visible, never fail-dead, and no accusation.
    assert commit["closure_verdict"]["demand_indeterminate"] == 3
    assert commit["closure_verdict"]["demand_rendered"] == 0


def test_a_fully_answered_turn_still_certifies_covered(fresh_store):
    """The other direction, so `covered` is not simply pinned false. `covered: true` is a
    STRONG claim after pass 3 — every anchor of every slot appears in the answering body."""
    answer = (
        "1,000 EUR to RUB = 99,800 RUB. Gold with it: 2,510 USD/oz. "
        "Weather in Rome: 28C. Baltic sea water temp: 18C."
    )
    commit = _serve(KILLER, answer)
    assert commit["closure_verdict"]["covered"] is True
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    assert commit["closure_verdict"]["demand_indeterminate"] == 0
    assert "Could not be answered:" not in commit["canonical_content"]


@pytest.mark.parametrize(
    "text",
    (
        # NEGATIVE CONTROLS for the render. Multi-unit single-domain turns whose trailing units
        # name no thing to look up: they ride along and must never produce an unavailable row.
        "Audit this project. Give me a verdict.",
        "Audit this repo. Give me a short summary.",
        "1000 TRY to USD? just the number please",
    ),
)
def test_answer_shaping_units_ride_along_and_never_render(text):
    """Contract 2026-09-08: the trailing shaping fragment is a CONSTRAINT attached to the ask, not a
    minted demand; it therefore cannot produce an unavailable row. The real ask is the one request."""
    from core.agent_runtime.answer_coverage import interpret_request

    fragments = interpret_request(text).units
    assert len(fragments) > 1, [(u.kind, u.text) for u in fragments]
    units = demand_units(text)
    assert len(units) == 1, [u.text for u in units]
    riders = [u for u in fragments if u.kind == "constraint"]
    assert len(riders) >= 1 and all(r.depends_on == (units[0].unit_id,) for r in riders), [(u.kind, u.text, u.depends_on) for u in fragments]
    assert units[0].unit_id not in {r.unit_id for r in riders}  # the real ask is never a rider


def test_conversational_banter_is_not_minted_at_all():
    """The operator's own opener. A unit made entirely of social/opinion tokens is an aside;
    minting it produced a slot no lane could ever discharge."""
    assert demand_units("ok so u think u so cool heh?") == ()
    assert demand_units("wow you are pretty clever") == ()
    # ... but an opinion question with a real object still mints.
    assert len(demand_units("what do you think about the EUR rate?")) == 1


# ------------------------------------------------------------------ R3: refusal is not an answer


def test_a_refusal_row_never_credits_the_slot_it_names(fresh_store):
    """R3, the exact served body. Four slots, two answered, and the body says so itself."""
    body = (
        "EUR/RUB: 1000 EUR x 99.8 = 99800.0 RUB (source: Frankfurter).\n"
        "Rome, Italy: Mainly clear, 28.3C (source: open-meteo.com)\n"
        "\n"
        "Could not be answered:\n"
        "- How much gold can I buy with 1000 EUR? - the runtime hit an internal fault\n"
        "- What is the water temperature in the Baltic Sea? - the available answer path "
        "does not safely match this request\n"
    )
    commit = _serve(CANONICAL_FOUR_SLOT, body)
    verdict = commit["closure_verdict"]
    assert verdict["demand_minted"] == 4
    # The invariant under attack: a refusal naming a slot must never CREDIT it. The two slots
    # the body refuses are NOT satisfied — and since Phase A they are `indeterminate` rather
    # than accused: answering_body cuts the disclosure section, the ladder sees zero echo, and
    # zero echo is no longer evidence of absence. Rome stays `indeterminate` (named, but no
    # weather reading in words). Nothing flipped to satisfied; covered stays false.
    assert verdict["demand_unanswered"] == 0
    assert verdict["demand_satisfied"] == 1
    assert verdict["demand_indeterminate"] == 3
    assert verdict["covered"] is False


def test_answering_body_cuts_at_the_disclosure_header():
    answer = "A: 1.\nB: 2.\n\nCould not be answered:\n- gold\n- baltic sea water temperature\n"
    body = answering_body(answer)
    assert body.startswith("A: 1.")
    assert "gold" not in body
    assert "baltic" not in body.lower()


def test_the_gold_slot_is_not_credited_by_its_own_refusal():
    """The mechanism, isolated: the same unit flips from credited to not-credited purely because
    the words naming it live under the disclosure header."""
    answered = "Gold is trading at 2,510.40 USD per troy ounce."
    refused = "EUR/RUB: 99800.\n\nCould not be answered:\n- how much gold can I buy with it\n"
    assert "u2" in units_present_in_answer(CANONICAL_FOUR_SLOT, answered)
    assert "u2" not in units_present_in_answer(CANONICAL_FOUR_SLOT, refused)


def test_a_slot_the_composer_already_named_is_not_rendered_twice(fresh_store):
    """The composer writes its own unserved rows for plan nodes. The sweep must add the slots
    it did NOT name and stay silent about the ones it did — matched on content tokens, because
    the composer writes 'What is the water temperature in the Baltic Sea?' for a unit the
    splitter produced as 'and what is the water temperature in the Baltic Sea?'."""
    body = (
        "EUR/RUB: 1000 EUR x 99.8 = 99800.0 RUB.\n"
        "Rome, Italy: Mainly clear, 28.3C\n"
        "\n"
        "Could not be answered:\n"
        "- how much gold can I buy with it - was attempted and did not come back\n"
        "- What is the water temperature in the Baltic Sea? - not something this runtime "
        "can look up\n"
    )
    commit = _serve(CANONICAL_FOUR_SLOT, body)
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    assert commit["closure_verdict"]["demand_indeterminate"] == 3
    assert commit["closure_verdict"]["demand_rendered"] == 0
    assert commit["canonical_content"] == body
    assert commit["canonical_content"].count("water temperature in the Baltic Sea") == 1


def test_disclosure_match_is_token_based_not_substring():
    disclosed = "- What is the water temperature in the Baltic Sea? - not available"
    assert unit_is_disclosed_in(
        "and what is the water temperature in the Baltic Sea?", disclosed
    )
    assert not unit_is_disclosed_in("what is the weather in Rome", disclosed)


def test_the_three_served_shapes_end_to_end(fresh_store):
    """One assertion block over all three reported defects, so a regression in any of them
    fails a test that names which."""
    r1 = _serve(KILLER, FX_ONLY_ANSWER, tag="r1")["closure_verdict"]
    assert (r1["demand_minted"], r1["demand_satisfied"], r1["covered"]) == (4, 1, False)

    # r2 carries a DISPATCH RECORD for the slot the lane served, which is what production
    # writes and what makes `unanswered` provable for its siblings. Built with receipts alone
    # this arm asserted a count the post-B4 sweep can never produce: the text ladder is capped
    # at `indeterminate`, so with no dispatch row the answer is 0 by construction. Repaired
    # rather than relaxed -- the assertion is unchanged and now measures the sweep.
    r2 = _serve(
        OPERATOR_EVENING, FX_ONLY_ANSWER, lane_served=("u1",), tag="r2"
    )["closure_verdict"]
    assert r2["demand_rendered"] == r2["demand_unanswered"] > 0
    assert r2["covered"] is False

    refusal_body = (
        "EUR/RUB: 1000 EUR x 99.8 = 99800.0 RUB.\nRome, Italy: 28.3C\n\n"
        "Could not be answered:\n"
        "- how much gold can I buy with it - internal fault\n"
        "- What is the water temperature in the Baltic Sea? - no safe path\n"
    )
    # r3 has the same shape as r2 and the same repair: the two slots the lane served carry a
    # dispatch record, so the two it did not are provably unanswered from the record rather
    # than from reading its own refusal prose.
    r3 = _serve(
        CANONICAL_FOUR_SLOT, refusal_body, lane_served=("u1", "u3"), tag="r3"
    )["closure_verdict"]
    # Two slots served by a lane, two the record shows were never dispatched. The old numbers
    # (1 satisfied) were read off the PROSE, before the dispatch record existed; the record is
    # the stronger statement and is what the sweep now grades from.
    assert r3["demand_satisfied"] == 2 and r3["demand_unanswered"] == 2
    assert json.loads(json.dumps(r3))["covered"] is False
