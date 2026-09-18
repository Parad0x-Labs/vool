"""RSS — one Requested-Slot Set per turn, minted at intake. AUD-20260829-003.

THE DEFECT (served, 2026-08-29). "…1500 eur to usd? then how much gold i can buy iwth it? and
thne tell me the water tempperature in baltc sea and in berling now" was taken whole-turn by
`currency_conversion_fast_path`, which answered the FX leg and dropped the rest with no "could
not be answered" row and a response commit recording `closure_verdict {covered: true,
open_count: 0}`. A live probe of the canonical four-slot prompt at the same HEAD served ONE slot
and certified the same thing. The certificate was not lying about its set — the set was one
prose obligation holding a 240-character prefix of the message, discharged from the existence of
served bytes. **The turn's demand set was never minted from the request.**

THE DESIGN, and why it is not the design the council first sealed. The sealed remedy was attacked
by its own council (evidence/E012) and all three load-bearing properties were damaged. What
follows is the repair, property by property; the full argument is in `DESIGN_RSS.md`.

D1 — MINT POLARITY IS INVERTED. The verdict minted on demand SHAPE (interrogative / imperative /
    request-marked). Measured over this project's own frozen fixtures, that mints ZERO on
    `100 EUR to USD at 1.10.` and on 3 of the 5 official MULTI_SLICE_CASES — bare declarative
    fragments carry no question mark, no head verb and no politeness phrase, so the remedy was
    inert on the commonest currency phrasing in the tree. The taxonomy of requests is open and
    cannot be enumerated; the taxonomy of greetings, thanks and acknowledgements is closed and
    short. So `demand_units` mints a content-bearing unit UNLESS it is recognisably
    conversational. Unknown shape mints.

    The verdict's safety claim ("single-domain multi-sentence turns mint one obligation by
    construction") is WITHDRAWN — measured, the frozen EUR/CHF case mints 2, the suite's sharpest
    control mints 3, and `I want to convert money. 1000 TRY to USD?` mints the wrong slot under a
    head-anchored reading. Cardinality was the wrong shape of claim. The replacement is about
    DISCHARGE:

        RSS-SAFE — however many units a turn mints, a turn served today still ships today's
        bytes, on the same lane, in one pass: RSS never reroutes, never changes admission, never
        raises, and never adds an unavailable row for a unit unless a REGISTERED family other
        than the served content positively reads that unit as a distinct ask.

    Positive evidence, never absence. The reverted bare-unclaimed-slice guard keyed on absence
    and rerouted 114 frozen closed-contract flows; ordinary politeness and multi-sentence
    elaboration are ownerless by design, so absence can never drive user-visible behaviour.

D2 — DISCHARGE HAS A MECHANISM NOW. It was a convention: `record_disposition` enforced evidence
    for one (kind, state) pair out of twelve, prose obligations were docstring-exempt, there was
    no obligation<->slice mapping anywhere in the tree, and `consumed` was self-asserted by the
    lane being checked. Now: the mapping is the obligation id itself
    (`ob:<attempt>:demand:<unit_id>`, plus a durable `unit_id` field); lanes write consumption
    RECEIPTS and may not disposition a demand obligation at all, for ANY of the terminal states;
    and the finalization sweep is the sole writer.

D3 — ONE ENFORCEMENT POINT, INSIDE `core/finalization.py`. Six real `finalize_answer` call sites
    exist; only four share a helper with a working closure handoff, and none catches
    `FinalizationRejected`. Retrofitting them would make some turns die with a computed answer
    discarded and let others keep the false-coverage defect. The sweep therefore lives inside the
    shared function, above the `covered` check, and locates the set from the payload when the
    ContextVar scope has exited.

D4 — the bar is the shared function, so every door is covered by construction.

Test policy: deterministic environmental assertions — what the ledger holds, what the certificate
counts, what bytes the commit carries. No expected prose, no prompt matching, no injected answers.
"""
from __future__ import annotations

import json

import pytest

import storage.db as sdb
from core.agent_runtime.answer_coverage import (
    demand_units,
    is_conversational_unit,
    units_present_in_answer,
    units_with_registered_demand,
)
from core.conductor import obligation_ledger as ol
from core.finalization import finalize_answer
from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

# --------------------------------------------------------------------------- fixtures


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "rss.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)
    ol.clear_active_set()


def _admit(text: str) -> None:
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "test_lane"})


def _mint(request: str, *, attempt: str = "att-1") -> dict[str, str]:
    """Mint a turn's obligation set exactly as `_r3_open_turn_execution` does."""
    return ol.open_obligation_set(
        request_text=request,
        obligations=[
            {"obligation_id": f"ob:{attempt}:answer", "text": request[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:{attempt}:demand:{unit.unit_id}",
                    "text": unit.text,
                    "kind": "demand",
                    "unit_id": unit.unit_id,
                    "slice_id": unit.slice_id,
                }
                for unit in demand_units(request)
            ),
        ],
    )


def _serve(request: str, answer: str, *, receipts: tuple[str, ...] = ()) -> dict:
    """Mint, receipt the named units, and finalize — the whole turn, through the real seam."""
    opened = _mint(request)
    for unit_id in receipts:
        ol.record_slice_consumption(
            opened["set_id"], opened["version"], unit_id=unit_id, evidence="test_receipt"
        )
    ol.record_disposition(
        opened["set_id"],
        opened["version"],
        "ob:att-1:answer",
        "satisfied",
        evidence_source="served_bytes",
    )
    _admit(answer)
    return finalize_answer(
        turn_id="t",
        canonical_content=answer,
        closure={
            **ol.closure_verdict(opened["set_id"], opened["version"]),
            "set_id": opened["set_id"],
        },
    )


# --------------------------------------------------------------------------- D1: the mint

# The exact frozen closed-contract texts from tests/test_the_core_authority_repairs_2026_08_19.py
# and tests/test_mixed_intent_slice_arbitration.py. These are the population the 114-flow revert
# was about; RSS is a regression the moment any of them changes behaviour.
FROZEN_SINGLE_FAMILY = (
    "100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5.",
    "Convert 50 USD to EUR at 0.92. And 75 GBP to USD at 1.27.",
    "100 EUR to USD at 1.10; 250 GBP to JPY at 190.0",
    "how much is 100 EUR in USD at 1.10? and 500 CHF in JPY at 170?",
    "100 EUR to USD at 1.10. Also 500 GBP to JPY at 190.5. And 10 CHF to EUR at 0.95.",
    "1000 TRY to USD?",
    "what is 500 GBP in EUR",
    "1000 TRY to USD at 33.4",
    "1000 EUR to USD. Also 500 GBP to JPY.",
)


@pytest.mark.parametrize(
    "text",
    (
        # E012 Fact 3: the demand-SHAPE reading mints zero on every one of these. The whole
        # remedy was inert on the commonest currency phrasing in the project's own fixtures.
        "100 EUR to USD at 1.10.",
        "Also 500 GBP to JPY at 190.5.",
        "100 EUR to USD at 1.10; 250 GBP to JPY at 190.0",
        "1000 TRY to USD at 33.4",
        "1000 EUR to USD. Also 500 GBP to JPY.",
    ),
)
def test_bare_declarative_demand_mints(text):
    """D1: a request with no question mark, no head verb and no politeness phrase is demand."""
    assert demand_units(text), f"bare declarative minted nothing: {text!r}"


@pytest.mark.parametrize(
    "text",
    (
        "hi",
        "hello there",
        "Good morning!",
        "thanks",
        "thank you very much",
        "thanks a lot",
        "appreciate it mate",
        "ok cool",
        "ALL DONE",
        "how are you?",
        "hey can you help me out",
    ),
)
def test_conversational_packaging_mints_nothing(text):
    """D1: the closed side of the denylist. E011's politeness population, verbatim."""
    assert is_conversational_unit(text) is True
    assert demand_units(text) == ()


def test_politeness_padding_does_not_change_a_frozen_contract():
    """E011's measured hole in the RIVAL remedy: greeting + thanks around a frozen contract trip
    both of its conjuncts and would be declined out of the closed lane. RSS mints exactly the
    conversion, in every padded form."""
    bare = demand_units("what is 500 GBP in EUR")
    for padded in (
        "hi there. what is 500 GBP in EUR",
        "good morning! what is 500 GBP in EUR? thank you very much",
        "what is 500 GBP in EUR? thanks a lot",
        "hey can you help me out. what is 500 GBP in EUR",
    ):
        units = demand_units(padded)
        assert len(units) == len(bare) == 1, padded
        assert "500 GBP in EUR" in units[0].text, padded


def test_the_canonical_four_slot_prompt_mints_four():
    """The audit's own acceptance prompt is ONE `turn_slices` clause — no clause punctuation
    until the final "?". A slice-grain demand set could not represent it, and 1-of-4 coverage
    would certify as a complete one-slot turn (E007, measured live)."""
    text = (
        "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in "
        "Rome, and what is the water temperature in the Baltic Sea?"
    )
    units = demand_units(text)
    assert len(units) == 4, [unit.text for unit in units]
    assert all(unit.slice_id == "s1" for unit in units)


@pytest.mark.parametrize(
    "text,expected",
    (
        # I7 clean paraphrases of the served defect turn — same demand, ordinary phrasing.
        ("Convert 1500 EUR to USD, then tell me how much gold that buys.", 2),
        ("What is 1500 EUR in USD, and what is the water temperature in the Baltic Sea?", 2),
        ("How much is 1500 EUR in USD? How much gold can I buy with that?", 2),
        ("Give me 1500 EUR in USD, then show me the weather in Berlin.", 2),
        ("What is 1500 EUR in USD, what is the gold price, and what is the weather in Rome?", 3),
    ),
)
def test_paraphrases_of_the_defect_turn_mint_every_slot(text, expected):
    assert len(demand_units(text)) == expected, [u.text for u in demand_units(text)]


@pytest.mark.parametrize(
    "text",
    (
        # I7 sloppy variants, modeled on the operator's real style in E001: typos, missing
        # punctuation, lowercase, doubled words, emoticons.
        "ok what about 1500 eur to usd? then how much god i can buy iwth it?",
        "1500 eur to usd pls and thne the water tempperature in baltc sea",
        "hey 1500eur to usd? and how much gold with it :D",
        "waht is 1500 eur in usd, and waht is the wether in berling now",
        "1500 eur to usd. also how much gold. also the temp in baltc sea",
    ),
)
def test_sloppy_variants_still_mint_more_than_one_slot(text):
    """A typo must not make demand invisible: the mint is not family recognition."""
    assert len(demand_units(text)) >= 2, [u.text for u in demand_units(text)]


@pytest.mark.parametrize("text", FROZEN_SINGLE_FAMILY)
def test_frozen_single_family_contracts_have_no_renderable_unit(text):
    """NEGATIVE CONTROL. Every unit of a frozen single-family contract is read by the SAME
    registered family, so a lane that serves the contract receipts every unit and nothing can
    ever be rendered against it. This is the 114-flow population."""
    units = demand_units(text)
    assert units, text
    registered = set(units_with_registered_demand(text))
    assert registered == {unit.unit_id for unit in units}, text


@pytest.mark.parametrize(
    "text",
    (
        # NEGATIVE CONTROLS — the shapes whose behaviour must not change. Each mints more than
        # one unit (which the withdrawn cardinality claim said was impossible), and in each the
        # trailing units are read by NO registered family, so none can ever be rendered.
        "Audit this project. Find the security vulnerabilities. Give me a verdict.",
        "Audit this repo. Find the security vulnerabilities. Give me a verdict.",
        "I want to convert money. 1000 TRY to USD?",
    ),
)
def test_elaboration_units_are_never_renderable(text):
    """The suite's own sharpest control: three sentences, one domain. Blocking it — or telling
    its user two thirds of the request went unanswered — is the regression.

    Contract 2026-09-08: the interpretation keeps every fragment ("I want to convert money." is
    CONTEXT of the conversion; "Give me a verdict." is a CONSTRAINT on the audit) while only
    REQUEST units are demands. Exactly one fragment is a renderable demand either way."""
    from core.agent_runtime.answer_coverage import interpret_request

    fragments = interpret_request(text).units
    assert len(fragments) > 1, text
    assert len(demand_units(text)) >= 1, text
    renderable = set(units_with_registered_demand(text))
    assert len(renderable) == 1, [(u.unit_id, u.kind, u.text) for u in fragments]


@pytest.mark.parametrize(
    "text",
    (
        # ADVERSARIAL NEAR-MISSES. Content-bearing non-requests in the operator's own register.
        # They MINT (RSS refuses to judge whether a question is "really" a request — that is the
        # family recognition property 1 forbids) but they can never be rendered.
        "ok so u think u so cool heh?",
        "100 EUR to USD at 1.10, don't you think that's fair?",
        "Hey there! How are you? Can you convert 100 EUR to USD at 1.10?",
    ),
)
def test_rhetorical_and_sarcastic_units_are_never_renderable(text):
    renderable = set(units_with_registered_demand(text))
    for unit in demand_units(text):
        if unit.unit_id in renderable:
            assert any(
                token in unit.text.lower() for token in ("eur", "usd", "convert")
            ), unit.text


def test_a_quoted_story_stays_one_unit():
    """ADVERSARIAL: the traveler fixture is one clause by frozen contract (`len(turn_slices) == 1`)
    and carries commas, "then" and a trailing imperative inside a quoted span."""
    traveler = (
        'A traveler says: "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore and '
        'came home with 2,100 kr." Explain whether the exchange was good or bad compared with '
        "the normal exchange rate."
    )
    assert len(demand_units(traveler)) == 1


# --------------------------------------------------------------------------- D2: the discharge


def test_a_lane_cannot_disposition_its_own_demand_slots(fresh_store):
    """DISCHARGE ABUSE. E012 T5.2: today a lane can buy closure by looping over its own open
    obligations and writing `unsupported`/`gated`/`superseded` for slots it never attempted —
    mechanically, with zero code changes, because the ledger's only evidence gate covers
    (effect, satisfied). Every one of those writes must now be refused."""
    opened = _mint("1000 EUR to RUB, what is the weather in Rome, and how much gold can I buy")
    sid, ver = opened["set_id"], opened["version"]
    demand = ol.demand_obligations(sid, ver)
    assert len(demand) >= 3

    for state in ("unsupported", "gated", "superseded", "cancelled", "failed", "satisfied"):
        for item in demand:
            assert (
                ol.record_disposition(sid, ver, item["obligation_id"], state) is False
            ), f"{state} accepted from the default evidence source"
            assert (
                ol.record_disposition(
                    sid, ver, item["obligation_id"], state, evidence_source="served_bytes"
                )
                is False
            ), f"{state} accepted from served_bytes"

    census = ol.demand_census(sid, ver)
    assert census["demand_open"] == len(demand)
    assert census["demand_satisfied"] == 0
    assert ol.closure_verdict(sid, ver)["covered"] is False


def test_only_the_sweep_writes_demand_dispositions(fresh_store):
    """The complement: the reserved evidence source, and nothing else, moves a demand slot."""
    opened = _mint("1000 TRY to USD?")
    sid, ver = opened["set_id"], opened["version"]
    obligation_id = ol.demand_obligations(sid, ver)[0]["obligation_id"]
    assert ol.record_disposition(sid, ver, obligation_id, "satisfied") is False
    assert (
        ol.record_disposition(
            sid,
            ver,
            obligation_id,
            "satisfied",
            evidence_source=ol.RSS_SWEEP_EVIDENCE,
        )
        is True
    )


def test_the_obligation_to_unit_mapping_is_durable(fresh_store):
    """D2: there was no obligation_id <-> slice_id mapping anywhere in the tree. There is now,
    frozen in the minted snapshot, so a disposition can name WHICH slot it is about."""
    text = "1000 EUR to RUB, and what is the weather in Rome?"
    opened = _mint(text)
    minted = ol.demand_obligations(opened["set_id"], opened["version"])
    units = {unit.unit_id: unit for unit in demand_units(text)}
    assert {item["unit_id"] for item in minted} == set(units)
    for item in minted:
        assert item["obligation_id"].endswith(f":demand:{item['unit_id']}")
        assert item["text"] == units[item["unit_id"]].text
        assert item["slice_id"] == units[item["unit_id"]].slice_id


def test_a_receipt_alone_closes_nothing(fresh_store):
    """Receipts are facts, not verdicts: recording one leaves the slot open until the sweep."""
    opened = _mint("1000 TRY to USD?")
    sid, ver = opened["set_id"], opened["version"]
    assert ol.record_slice_consumption(sid, ver, unit_id="u1", evidence="test") is True
    assert ol.demand_census(sid, ver)["demand_open"] == 1
    assert ol.closure_verdict(sid, ver)["covered"] is False


def test_served_content_overlap_receipts_the_units_the_answer_addressed():
    """R2, the reading used when a lane recorded no per-slice coverage of its own. The served
    FX line addresses the conversion and nothing else in the operator's evening turn."""
    text = (
        "ok what about 1500 eur to usd? then how much god i can buy iwth it? and thne tell me "
        "the water tempperature in baltc sea"
    )
    answer = (
        "1,500 EUR (euro) x 1.1652 = 1,747.80 USD (United States dollar), using a live rate as "
        "of 2026-08-29T00:00:00+00:00."
    )
    present = set(units_present_in_answer(text, answer))
    units = {unit.unit_id: unit.text for unit in demand_units(text)}
    served = {unit_id for unit_id in present}
    assert len(served) == 1, {u: units[u] for u in served}
    assert "eur to usd" in units[next(iter(served))].lower()


# --------------------------------------------------------------------------- D3: the sweep


def test_an_unanswered_registered_slot_is_terminal_but_not_accused_from_the_ladder(fresh_store):
    """THE HEADLINE, restated by Phase A (PLAN-discharge-channel.md §3, 2026-08-30).

    The canonical four-slot prompt served with one slot's worth of bytes: the certificate
    still counts four minted against one satisfied and drives every open slot terminal —
    that half is unchanged. What changed is the ACCUSATION: the text ladder read zero echo
    for three slots and called them `unanswered`, which rendered a public refusal — the same
    verdict that disowned the correctly-served weather answer measured live. Absence of echo
    is not evidence of absence, so the ladder's floor is `indeterminate`: terminal, honest in
    the certificate, rendering nothing. Naming real drops again is Phase B4's job, from the
    dispatch record."""
    text = (
        "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in "
        "Rome, and what is the water temperature in the Baltic Sea?"
    )
    answer = "Gold is trading at 2,510.40 USD per troy ounce (source: metals feed)."
    commit = _serve(text, answer, receipts=("u2",))

    verdict = commit["closure_verdict"]
    assert verdict["demand_minted"] == 4
    assert verdict["demand_satisfied"] == 1
    assert verdict["demand_unanswered"] == 0
    assert verdict["demand_indeterminate"] == 3
    assert verdict["demand_open"] == 0
    # `covered` is a claim about the REQUEST. Structural terminality (open_count == 0) is what
    # permitted finalization; it must not also read as "the request was covered".
    assert verdict["covered"] is False
    assert verdict["open_count"] == 0

    content = commit["canonical_content"]
    assert "Could not be answered:" not in content
    assert verdict["demand_rendered"] == 0


def test_the_certificate_distinguishes_a_vacuous_closure_from_a_complete_turn(fresh_store):
    """Property 4. Before RSS both of these certified `{covered: true, open_count: 0}` and were
    indistinguishable by inspection — which is exactly what E007 recorded over three dropped
    slots."""
    text = (
        "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in "
        "Rome, and what is the water temperature in the Baltic Sea?"
    )
    vacuous = _serve(text, "Gold is 2,510.40 USD/oz.", receipts=("u2",))["closure_verdict"]
    reset_admission()
    complete = _serve(
        text,
        "1000 EUR = 98,400 RUB. Gold is 2,510.40 USD/oz. Rome: 24C. Baltic Sea: 18C.",
        receipts=("u1", "u2", "u3", "u4"),
    )["closure_verdict"]

    # Both are structurally terminal — that is what let each turn ship. Before RSS that was
    # the ONLY signal and the two were indistinguishable; now they differ in both directions.
    assert vacuous["open_count"] == complete["open_count"] == 0
    assert vacuous["covered"] is False and complete["covered"] is True
    assert vacuous["demand_minted"] == complete["demand_minted"] == 4
    assert vacuous["demand_satisfied"] == 1
    assert complete["demand_satisfied"] == 4
    assert complete["demand_unanswered"] == 0


def test_a_turn_with_open_demand_slots_still_ships(fresh_store):
    """FAIL-VISIBLE, NEVER FAIL-DEAD. E012 T5.3 sub-case (a): a partial implementation makes a
    turn raise `FinalizationRejected` and discard a fully computed answer one stack frame up.
    The sweep drives every demand slot terminal BEFORE the coverage check, so RSS can never be
    the reason a turn refuses."""
    text = "What is 1000 EUR to RUB, and what is the weather in Rome?"
    answer = "1000 EUR = 98,400 RUB."
    opened = _mint(text)
    ol.record_disposition(
        opened["set_id"],
        opened["version"],
        "ob:att-1:answer",
        "satisfied",
        evidence_source="served_bytes",
    )
    # No receipts at all, and the ledger says the set is wide open before finalization.
    before = ol.closure_verdict(opened["set_id"], opened["version"])
    assert before["covered"] is False and before["open_count"] >= 1

    _admit(answer)
    commit = finalize_answer(
        turn_id="t",
        canonical_content=answer,
        closure={**before, "set_id": opened["set_id"]},
    )
    assert commit["canonical_content"].startswith(answer)
    assert commit["closure_verdict"]["demand_open"] == 0
    assert commit["closure_verdict"]["open_count"] == 0  # structurally terminal: it shipped
    assert commit["closure_verdict"]["covered"] is False  # and it does not pretend otherwise


def test_the_sweep_reaches_a_door_that_passes_no_closure(fresh_store):
    """D3: five of six `finalize_answer` call sites pass no `closure=` and hold no ContextVar
    binding. The sweep is inside the shared function, so a raw call still accounts and still
    renders — no call site is retrofitted, so no call site can be forgotten."""
    text = "What is 1000 EUR to RUB, and what is the weather in Rome?"
    answer = "1000 EUR = 98,400 RUB."
    opened = _mint(text)
    ol.record_slice_consumption(
        opened["set_id"], opened["version"], unit_id="u1", evidence="test_receipt"
    )
    ol.record_disposition(
        opened["set_id"],
        opened["version"],
        "ob:att-1:answer",
        "satisfied",
        evidence_source="served_bytes",
    )
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        _admit(answer)
        # No `closure=` argument at all — the channel_gateway / service.py / presence.py shape.
        commit = finalize_answer(turn_id="t", canonical_content=answer)
    finally:
        ol.clear_active_set()
        del token
    # Phase A: the unserved slot is accounted and terminal, but the ladder may not accuse —
    # no row renders. (The sweep still runs on this raw-call door; that is the D3 property.)
    assert "weather in Rome" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_minted"] == 2
    assert commit["closure_verdict"]["demand_satisfied"] == 1
    assert commit["closure_verdict"]["demand_indeterminate"] == 1
    assert commit["closure_verdict"]["demand_open"] == 0


def test_the_commit_hash_covers_the_swept_bytes(fresh_store):
    """The swept bytes are inside the hashed, committed bytes — transport serves
    `commit["canonical_content"]` verbatim and re-hashes it at serve time, so a row appended
    after the hash would never reach a reader. Phase A: with the ladder no longer accusing,
    the swept bytes here are byte-identical to the served answer — the hash property is what
    guarantees that if a row ever IS appended (Phase B4, dispatch-proven), it cannot appear
    in served bytes unhashed."""
    import hashlib

    text = "What is 1000 EUR to RUB, and what is the weather in Rome?"
    commit = _serve(text, "1000 EUR = 98,400 RUB.", receipts=("u1",))
    expected = "sha256:" + hashlib.sha256(
        commit["canonical_content"].encode("utf-8")
    ).hexdigest()
    assert commit["content_hash"] == expected
    assert commit["canonical_content"] == "1000 EUR = 98,400 RUB."


@pytest.mark.parametrize("text", FROZEN_SINGLE_FAMILY)
def test_a_frozen_contract_answered_in_one_pass_renders_nothing(fresh_store, text):
    """RSS-SAFE, end to end. A single lane answering every unit in one pass discharges every
    unit in one sweep: no rerouting, no bricking, no rows, and bytes unchanged. This is the
    population the reverted guard broke."""
    answer = " ".join(f"[{unit.text}] answered." for unit in demand_units(text))
    commit = _serve(
        text, answer, receipts=tuple(unit.unit_id for unit in demand_units(text))
    )
    assert commit["canonical_content"] == answer
    assert "Could not be answered:" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    reset_admission()


def test_an_unregistered_slot_is_counted_but_not_accused_from_the_ladder(fresh_store):
    """PASS 2's residue, restated by Phase A (PLAN-discharge-channel.md §3, 2026-08-30).

    A demand unit in a domain no probe registers — or misspelled past probe recognition, as
    in the operator's own evening turn — used to be counted `unanswered` and rendered. The
    counting is still honest and still terminal; the RENDER is gone, because the zero-echo
    verdict that drove it also publicly disowned correctly-served turns (measured live), and
    absence of echo is not evidence of absence. The certificate tells the truth; the reply
    carries no accusation. Phase B4 restores naming from the dispatch record."""
    text = (
        "ok what about 1500 eur to usd? then how much god i can buy iwth it? and thne tell me "
        "the water tempperature in baltc sea"
    )
    answer = "1,500 EUR (euro) x 1.1652 = 1,747.80 USD (United States dollar)."
    commit = _serve(text, answer, receipts=("u1",))
    verdict = commit["closure_verdict"]
    assert verdict["demand_minted"] == 3
    assert verdict["demand_satisfied"] == 1
    assert verdict["demand_unanswered"] == 0
    assert verdict["demand_indeterminate"] == 2
    assert verdict["demand_rendered"] == 0
    assert verdict["covered"] is False
    body = commit["canonical_content"]
    assert body.startswith(answer)
    assert "Could not be answered:" not in body


def test_a_turn_that_mints_no_demand_keeps_a_byte_identical_certificate(fresh_store):
    """Blast radius. A turn with no demand obligations — every existing lane in the tree today —
    gets exactly the certificate it got before RSS, with no extra keys and no content change."""
    opened = ol.open_obligation_set(
        obligations=[{"obligation_id": "ob:x:answer", "text": "hi", "kind": "prose"}]
    )
    ol.record_disposition(
        opened["set_id"],
        opened["version"],
        "ob:x:answer",
        "satisfied",
        evidence_source="served_bytes",
    )
    _admit("Afternoon!")
    commit = finalize_answer(
        turn_id="t",
        canonical_content="Afternoon!",
        closure={
            **ol.closure_verdict(opened["set_id"], opened["version"]),
            "set_id": opened["set_id"],
        },
    )
    assert commit["canonical_content"] == "Afternoon!"
    assert set(commit["closure_verdict"]) == {"covered", "open_count", "set_version"}


def test_the_sweep_never_disposes_an_effect_obligation(fresh_store):
    """The A6 law is untouched: a pending machine effect still blocks closure after the sweep."""
    opened = _mint("What is 1000 EUR to RUB, and what is the weather in Rome?")
    sid, ver = opened["set_id"], opened["version"]
    ol.register_effect_obligation(sid, ver, "ob:effect:send", text="send the email")
    ol.record_disposition(
        sid, ver, "ob:att-1:answer", "satisfied", evidence_source="served_bytes"
    )
    ol.sweep_demand_obligations(sid, ver)
    assert ol.assert_prose_cannot_close_pending_effect(sid, ver) is True
    assert ol.closure_verdict(sid, ver)["covered"] is False


def test_the_sweep_finds_the_set_from_the_a0_request_id_alone(fresh_store):
    """D3, the last door. `channel_gateway.py`, `service.py` (x3) and `presence.py` call
    `finalize_answer` with no `closure=` and no ContextVar binding — the shape that made a
    partial rollout of this property strictly worse than doing nothing. The A0 request id IS
    readable there (finalization already reads it for its own identity fields), so the set is
    addressable from every door without touching any of them."""
    from core.invocation.ledger import accept_invocation
    from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID, set_request_context

    accepted = accept_invocation(
        external_kind="turn",
        external_value="rss-door-probe",
        principal="owner_local",
        session_binding="sess-rss-door",
    )
    request_id = accepted["request_id"]
    text = "What is 1000 EUR to RUB, and what is the weather in Rome?"
    opened = ol.open_obligation_set(
        request_text=text,
        request_id=request_id,
        obligations=[
            {"obligation_id": "ob:att-9:answer", "text": text[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:att-9:demand:{unit.unit_id}",
                    "text": unit.text,
                    "kind": "demand",
                    "unit_id": unit.unit_id,
                    "slice_id": unit.slice_id,
                }
                for unit in demand_units(text)
            ),
        ],
    )
    ol.record_slice_consumption(
        opened["set_id"], opened["version"], unit_id="u1", evidence="test_receipt"
    )
    ol.record_disposition(
        opened["set_id"],
        opened["version"],
        "ob:att-9:answer",
        "satisfied",
        evidence_source="served_bytes",
    )
    assert ol.active_set() is None
    token = set_request_context(request_id)
    try:
        _admit("1000 EUR = 98,400 RUB.")
        # No closure dict, no binding — exactly the raw call-site shape.
        commit = finalize_answer(turn_id="t", canonical_content="1000 EUR = 98,400 RUB.")
    finally:
        _CURRENT_REQUEST_ID.reset(token)
    assert "weather in Rome" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_minted"] == 2
    assert commit["closure_verdict"]["demand_unanswered"] == 0
    assert commit["closure_verdict"]["demand_indeterminate"] == 1


# ------------------------------------------------- the conductor's own discharge channel (B4)

TURN7 = (
    "Do NOT search the web for this. From memory: what is the boiling point of water at sea "
    "level in Celsius? Also, what is 10 percent of 250?"
)

#: The planner's REAL entry point, driven deterministically. The proposal and the frames are the
#: ones measured on served acceptance turn 7 (validation-logs/consolidation-continuation-20260909),
#: so the plan under test is the plan the daemon builds, not a hand-made object.
TURN7_PROPOSAL = json.dumps(
    [
        {
            "request": "what is the boiling point of water at sea level in Celsius?",
            "operation": "reviewed_safe_knowledge",
            "depends_on": [],
        },
        {"request": "what is 10 percent of 250?", "operation": "calculation", "depends_on": []},
    ]
)
TURN7_FRAMES = json.dumps(
    {
        "frames": [
            {
                "family": "quantitative_reasoning",
                "scope": "From memory: what is the boiling point of water at sea level in Celsius?",
                "predicate": "",
                "polarity": "affirmed",
                "roles": [],
            }
        ]
    }
)

TURN7_RESULTS = {
    "factual_explanation": {"text": "The boiling point of water at sea level is 100 degrees Celsius."},
    "calculation": {"statement": "10% of 250 = 25.", "value": "25"},
}


def _turn7_plan():
    from core.conductor.planner import plan_conductor_turn

    plan = plan_conductor_turn(
        TURN7,
        ask_model=lambda _s, _u: TURN7_PROPOSAL,
        plan_id="rss",
        propose_semantics=lambda _s, _u: TURN7_FRAMES,
    )
    assert plan is not None, "the conductor declined the turn this contract is about"
    return plan


def _turn7_outcomes(plan, *, succeeded: bool = True):
    """Real `NodeOutcome`s for the plan's nodes -- SUCCEEDED ones carry a contract-complete result."""
    from core.conductor.node import NodeFailureCode, NodeLifecycle, NodeOutcome

    outcomes = []
    for node in plan.nodes:
        if succeeded:
            outcomes.append(
                NodeOutcome(
                    node=node,
                    state=NodeLifecycle.SUCCEEDED,
                    result=dict(TURN7_RESULTS.get(node.operation) or {"text": "x"}),
                    rendered=str(
                        (TURN7_RESULTS.get(node.operation) or {}).get("statement")
                        or (TURN7_RESULTS.get(node.operation) or {}).get("text")
                        or ""
                    ),
                )
            )
        else:
            outcomes.append(
                NodeOutcome(
                    node=node,
                    state=NodeLifecycle.FAILED,
                    failure_code=NodeFailureCode.NODE_EXCEPTION,
                    failure_reason="ValueError: no generation seam available",
                )
            )
    return outcomes


def test_a_succeeded_node_serves_the_demand_unit_it_sits_inside():
    """GEOMETRY decides which node served which demand -- span containment, never wording.

    Measured on served turn 7 (build d6be47f9): node spans (45, 104) and (111, 137) against
    demand units u1 [32, 104) and u2 [111, 137). The unit carries the framing "From memory:"
    the node does not, so equality can never be the rule and wording never is.
    """
    from core.conductor.planner import demands_this_plan_served

    plan = _turn7_plan()
    outcomes = _turn7_outcomes(plan)
    assert all(outcome.succeeded for outcome in outcomes), (
        "the fixture results do not satisfy the nodes' own contracts, so this test would pass "
        f"without proving anything: {[(o.node.operation, o.succeeded) for o in outcomes]}"
    )
    units = demand_units(TURN7)
    assert {unit.unit_id for unit in units} == {"u1", "u2"}, [u.unit_id for u in units]

    served = dict(demands_this_plan_served(plan, outcomes, units))
    assert set(served) == {"u1", "u2"}, (
        f"a served demand was not credited to the node that ran for it: {served}; "
        f"nodes were {[(n.operation, n.clause_span) for n in plan.nodes]}"
    )
    assert "factual_explanation" in served["u1"], served
    assert "calculation" in served["u2"], served

    # A node that did not succeed attests nothing. The sweep must stay free to report the gap.
    assert demands_this_plan_served(plan, _turn7_outcomes(plan, succeeded=False), units) == ()


def test_the_conductor_files_receipts_so_a_served_demand_counts_satisfied(fresh_store):
    """The accounting gap measured served on d6be47f9, at the seam that owns it.

    Turn 7 answered BOTH demands and certified `demand_minted: 2, demand_satisfied: 0,
    demand_indeterminate: 2`, ledger snapshot `obset-6f17822a34de4f23` holding `consumption: []`.
    Nothing in the runtime was lying: the conductor never filed the receipt its own execution
    record supports, and the sweep may not promote bytes to `satisfied` on its own (C-7).
    """
    from core.agent_runtime.agent import _record_conductor_demand_receipts

    answer = "The boiling point of water at sea level is 100 degrees Celsius.\n10% of 250 = 25."
    opened = _mint(TURN7)
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        plan = _turn7_plan()
        outcomes = _turn7_outcomes(plan)
        assert all(outcome.succeeded for outcome in outcomes)
        _record_conductor_demand_receipts(plan, outcomes, request=TURN7)
    finally:
        ol.clear_active_set()
        del token

    receipts = {
        item["unit_id"]: item["evidence"]
        for item in ol.consumption_receipts(opened["set_id"], opened["version"])
    }
    assert receipts == {"u1": "slice_answer_record", "u2": "slice_answer_record"}, receipts

    ol.record_disposition(
        opened["set_id"],
        opened["version"],
        "ob:att-1:answer",
        "satisfied",
        evidence_source="served_bytes",
    )
    _admit(answer)
    commit = finalize_answer(
        turn_id="t",
        canonical_content=answer,
        closure={
            **ol.closure_verdict(opened["set_id"], opened["version"]),
            "set_id": opened["set_id"],
        },
    )
    verdict = commit["closure_verdict"]
    assert verdict["demand_minted"] == 2, verdict
    assert verdict["demand_satisfied"] == 2, (
        f"a turn that answered every demand still credits none: {verdict}"
    )
    assert verdict["demand_indeterminate"] == 0, verdict


def test_the_conductor_dispatch_site_actually_files_the_receipt(fresh_store, monkeypatch):
    """The WIRING, asserted at the caller -- the two tests above pass with the call site deleted.

    Measured while proving this repair: removing
    `_record_conductor_demand_receipts(plan, outcomes, request=effective_input)` from
    `_maybe_answer_conductor_turn` left both pins above green, because they call the recorder
    directly. A helper proven in isolation and never wired is how a repaired module ships dead.

    So this drives the REAL product method with the REAL plan and the REAL composer, stubbing only
    node EXECUTION (no model in a unit test), and asserts on the ledger the turn owns. A stub plan
    cannot stand in here: `reduce_execution_report` produces NOTHING_CAPTURED for one, the method
    declines at `if not decision.claimed` and the composition seam is never reached at all.
    """
    import core.agent_runtime.agent as agent_module
    import core.conductor as conductor_pkg
    from apps.vool_agent import VoolAgent

    plan = _turn7_plan()
    outcomes = _turn7_outcomes(plan)
    assert all(outcome.succeeded for outcome in outcomes)

    monkeypatch.setattr(agent_module, "_CONDUCTOR_TEST_HOOK", None, raising=False)
    monkeypatch.setattr(conductor_pkg, "plan_conductor_turn", lambda *a, **k: plan, raising=True)
    monkeypatch.setattr(conductor_pkg, "run_conductor_plan", lambda *a, **k: outcomes, raising=True)

    opened = _mint(TURN7)
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        agent = VoolAgent(backend_name="test-backend", device="receipt-wiring", persona_id="default")
        served = agent._maybe_answer_conductor_turn(
            effective_input=TURN7,
            raw_input=TURN7,
            session_id="receipt-wiring-session",
            source_context={"surface": "openclaw"},
        )
    finally:
        ol.clear_active_set()
        del token

    assert served is not None, "the conductor declined the turn, so the seam under test never ran"
    receipts = {
        item["unit_id"]: item["evidence"]
        for item in ol.consumption_receipts(opened["set_id"], opened["version"])
    }
    assert receipts == {"u1": "slice_answer_record", "u2": "slice_answer_record"}, (
        "the conductor's own dispatch site filed no receipt for the units its nodes served: "
        f"{receipts}; served text was {str(served.get('response'))[:120]!r}"
    )


def test_a_node_over_exactly_one_demand_credits_it_and_a_faulty_split_is_not_papered_over():
    """Two rules at once, on the turn that first showed them (build 9e437955, turn 4).

    "What is 12 times 8? Now take that result and divide it by 6." mints THREE units: the mint
    splits the second instruction at its `and` into `u2 'Now take that result'`
    (`depends_on=('u1',)`) and `u3 'and divide it by 6.'` (`depends_on=('u2',)`). The planner kept
    that instruction whole -- its own `conductor_plan_created` record shows nodes (0, 19) and
    (20, 60) -- and both succeeded.

    * `u1` IS credited: node (0, 19) sits inside it.
    * `u2` and `u3` are NOT: node (20, 60) spans BOTH of them, and a node covering two demands
      cannot say which it served. The fix for those two fragments belongs at the mint -- one
      dependent instruction is one demand -- and handing out two receipts to compensate for a bad
      split would hide the split instead of repairing it.
    """
    from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
    from core.conductor.planner import demands_this_plan_served

    text = "What is 12 times 8? Now take that result and divide it by 6."
    units = demand_units(text)
    assert [(u.unit_id, u.start, u.end) for u in units] == [
        ("u1", 0, 19), ("u2", 20, 40), ("u3", 41, 60)
    ], [(u.unit_id, u.start, u.end, u.text) for u in units]

    first = ConductorNode(
        node_id="n1", operation="calculation", request_text="What is 12 times 8?",
        required_result_fields=("value",), clause_span=(0, 19),
    )
    second = ConductorNode(
        node_id="n2", operation="quantitative_reasoning",
        request_text="Now take that result and divide it by 6.",
        required_result_fields=("value",), clause_span=(20, 60),
    )

    class _Plan:
        nodes = (first, second)

    outcomes = [
        NodeOutcome(node=first, state=NodeLifecycle.SUCCEEDED, result={"value": "96"}),
        NodeOutcome(node=second, state=NodeLifecycle.SUCCEEDED, result={"value": "16"}),
    ]
    assert all(outcome.succeeded for outcome in outcomes)

    served = dict(demands_this_plan_served(_Plan(), outcomes, units))
    assert served == {"u1": "n1"}, (
        "a node spanning two demand fragments must certify neither, and the node inside u1 must "
        f"still certify u1: {served}"
    )

    # A node that covers exactly ONE demand and some non-demand text DOES credit it: there is no
    # other demand it could have served instead. This is the shape turn 7's framing prefix makes.
    only_one = ConductorNode(
        node_id="n3", operation="calculation", request_text="What is 12 times 8?",
        required_result_fields=("value",), clause_span=(0, 20),
    )

    class _WiderPlan:
        nodes = (only_one,)

    assert dict(
        demands_this_plan_served(
            _WiderPlan(),
            [NodeOutcome(node=only_one, state=NodeLifecycle.SUCCEEDED, result={"value": "96"})],
            units,
        )
    ) == {"u1": "n3"}


def test_one_node_over_two_distinct_demands_may_not_certify_both():
    """THE NEGATIVE GATE. Span containment is possible ownership, never fulfilment.

    "What is the weather in Rome and what is the water temperature in the Baltic Sea?" mints two
    INDEPENDENT request units (`depends_on=()` on both). A single node whose clause span covers
    the whole message and which succeeded with a Rome-only result has served ONE of them. Neither
    its success flag nor its node id says anything about the other, so the binder may credit at
    most the demand it can show was served -- and with nothing distinguishing them, that is none.

    This is the case `81753273` shipped without: the docstring named the risk and the change
    credited every contained unit anyway.
    """
    from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
    from core.conductor.planner import demands_this_plan_served

    text = "What is the weather in Rome and what is the water temperature in the Baltic Sea?"
    units = demand_units(text)
    assert [(u.unit_id, u.start, u.end) for u in units] == [("u1", 0, 27), ("u2", 28, 80)], [
        (u.unit_id, u.start, u.end, u.text) for u in units
    ]
    assert all(not u.depends_on for u in units), "these two demands are independent by construction"

    wide = ConductorNode(
        node_id="wide", operation="live_data_plan", request_text=text,
        required_result_fields=("value",), clause_span=(0, 80),
    )

    class _Plan:
        nodes = (wide,)

    outcome = NodeOutcome(
        node=wide, state=NodeLifecycle.SUCCEEDED,
        result={"value": "Rome: 24 C"},  # the Baltic half was never served
    )
    assert outcome.succeeded

    served = dict(demands_this_plan_served(_Plan(), [outcome], units))
    assert served == {}, (
        "one succeeded node spanning two independent demands certified them both; success is not "
        f"per-demand fulfilment: {served}"
    )


TWO_ASKS = "What is the weather in Rome and what is the water temperature in the Baltic Sea?"


def _planned(request: str, *, index: int = 0, answer: str = "served", error: str = ""):
    from core.agent_runtime.turn_planner import PlannedTask, TaskOutcome

    return TaskOutcome(task=PlannedTask(index=index, request=request), answer=answer, error=error)


def test_the_planned_sub_turn_lane_files_receipts_for_the_slots_it_ran(fresh_store):
    """The OTHER per-unit execution contract this runtime already has, wired to the same channel.

    `turn_planner` carves the message into `PlannedTask`s and runs each one; `TaskOutcome.ok` is
    true only when that sub-turn produced an answer with no error and no pending approval. That is
    a lane running for one requested slot -- not a model asserting success, not a length, and not
    a word match against the reply. Binding goes through the same geometry authority as the
    conductor's.
    """
    from core.agent_runtime.agent import _record_planned_turn_demand_receipts

    units = demand_units(TWO_ASKS)
    assert [u.unit_id for u in units] == ["u1", "u2"], [(u.unit_id, u.text) for u in units]

    opened = _mint(TWO_ASKS)
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        _record_planned_turn_demand_receipts(
            [
                _planned("What is the weather in Rome", index=0),
                _planned("what is the water temperature in the Baltic Sea?", index=1),
            ],
            request=TWO_ASKS,
        )
    finally:
        ol.clear_active_set()
        del token

    receipts = {
        item["unit_id"]: item["evidence"]
        for item in ol.consumption_receipts(opened["set_id"], opened["version"])
    }
    assert receipts == {"u1": "slice_answer_record", "u2": "slice_answer_record"}, receipts


def test_the_planned_lane_credits_nothing_it_cannot_place(fresh_store):
    """Three refusals, one test: a sub-turn that failed, one spanning both asks, one not found.

    None of these may produce a receipt. The middle case is the same law the conductor obeys --
    covering two demands says nothing about which was served -- and the last is why there is no
    text-similarity fallback: if the carved request is not in the user's own words, exactly once,
    the lane cannot say which slot it ran.
    """
    from core.agent_runtime.agent import _record_planned_turn_demand_receipts

    opened = _mint(TWO_ASKS)
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        failed = _planned("What is the weather in Rome", index=0, answer="", error="boom")
        assert not failed.ok
        _record_planned_turn_demand_receipts(
            [
                failed,
                _planned(TWO_ASKS, index=1),                      # spans both demands
                _planned("the population of Springfield", index=2),  # not in this message
            ],
            request=TWO_ASKS,
        )
    finally:
        ol.clear_active_set()
        del token

    assert ol.consumption_receipts(opened["set_id"], opened["version"]) == (), (
        "a lane credited a demand it cannot show it served"
    )


def test_an_ambiguous_carving_credits_nothing(fresh_store):
    """A request that occurs TWICE in the message cannot say which occurrence it ran.

    "Give me the price of gold, and after that the price of gold in euros." mints two demands and
    contains "the price of gold" twice. Placing the sub-turn at the first occurrence would credit
    u1 on a task that may have served u2. The lane binds nothing rather than guess -- and the
    control in the same test shows a task that IS unique still binds, so this refuses ambiguity
    rather than refusing everything.
    """
    from core.agent_runtime.agent import _record_planned_turn_demand_receipts

    text = "Give me the price of gold, and after that the price of gold in euros."
    assert text.count("the price of gold") == 2
    units = demand_units(text)
    assert [u.unit_id for u in units] == ["u1", "u2"], [(u.unit_id, u.text) for u in units]

    opened = _mint(text)
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        _record_planned_turn_demand_receipts(
            [
                _planned("the price of gold", index=0),                    # ambiguous
                _planned("the price of gold in euros", index=1),           # unique -> u2
            ],
            request=text,
        )
    finally:
        ol.clear_active_set()
        del token

    receipts = {
        item["unit_id"]: item["excerpt"]
        for item in ol.consumption_receipts(opened["set_id"], opened["version"])
    }
    assert receipts == {"u2": "planned_task:1"}, (
        "the ambiguous carving must credit nothing while the unique one still binds: "
        f"{receipts}"
    )


def test_a_continuation_with_no_subject_of_its_own_is_not_a_second_demand():
    """The mint defect behind a served wrong answer, and the four controls that bound the fix.

    Measured served 2026-09-09 on build c9200e0c, acceptance turn 16. "Read the file /tmp/... and
    tell me its contents." split at the `and`; the second unit -- "and tell me its contents." --
    was served on its own with no object, and the model resolved "its" against the PREVIOUS TURN.
    The reply carried a file-read refusal followed by three bullets about why the sky is blue,
    which was turn 15's question.

    A fragment naming nothing of its own continues the request it follows. A fragment naming ANY
    thing of its own still opens a fresh one -- which is what keeps the frozen four-slot case at
    four demands.
    """
    bled = "Read the file /tmp/definitely_missing_file_9x7.txt and tell me its contents."
    assert [u.text for u in demand_units(bled)] == [bled], (
        "an anaphor-only continuation was minted as a demand of its own: "
        f"{[u.text for u in demand_units(bled)]}"
    )

    # CONTROL 1 -- the frozen four-slot case, the defect RSS exists to prevent.
    four = "1000 EUR to RUB, gold with it, weather in Rome, baltic sea water temp"
    assert len(demand_units(four)) == 4, [u.text for u in demand_units(four)]

    # CONTROL 2 -- a continuation that names its own thing is still a second demand.
    named = "Read the attached file and tell me exactly what the second line says."
    assert len(demand_units(named)) == 2, [u.text for u in demand_units(named)]

    # CONTROL 3 -- the documented compare case keeps opening a request.
    from core.agent_runtime.answer_coverage import _introduces_no_new_subject

    assert not _introduces_no_new_subject("and compare it with silver")
    assert not _introduces_no_new_subject("and what is the water temperature in the Baltic Sea?")
    assert _introduces_no_new_subject("and tell me its contents.")

    # CONTROL 4 -- "divide" is not classified as a demand head, so this one still splits. Recorded
    # as the remaining half of the split defect rather than papered over.
    assert len(demand_units("What is 12 times 8? Now take that result and divide it by 6.")) == 3
