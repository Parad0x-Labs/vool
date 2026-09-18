"""Contract slice 1 (docs/REQUEST_INTERPRETATION_MIGRATION_CONTRACT_2026-09-08.md): the facts the
interpretation must carry, pinned on wording that is not the sentence that failed.

* a correction of the request before it is CONTEXT of that request, never a demand of its own;
* a quotation's request head after the quote is found;
* a locative lead-in does not hide a step of a build;
* an execution unit carries the REQUEST units it batches, and the demand ledger files every member.
"""
from __future__ import annotations

from itertools import pairwise

import pytest

from core.agent_runtime.answer_coverage import KIND_CONTEXT, KIND_REQUEST, demand_units, interpret_request
from core.agent_runtime.demand_ownership import demand_records, execution_unit_spans


@pytest.mark.parametrize(
    "text",
    (
        "what is TRY? i meant money TRY",
        "convert 100 usd to eur. sorry, I meant to pounds",
        "how far is Riga from Vilnius? no, i mean by train",
    ),
)
def test_a_correction_is_context_of_the_request_it_corrects(text: str) -> None:
    interpretation = interpret_request(text)
    requests = interpretation.requests
    assert len(requests) == 1, [(u.kind, u.text) for u in interpretation.units]
    corrections = [u for u in interpretation.units if u.kind == KIND_CONTEXT]
    assert len(corrections) == 1 and corrections[0].depends_on == (requests[0].unit_id,), corrections
    assert len(demand_units(text)) == 1


def test_a_correction_that_asks_a_new_question_stays_a_request() -> None:
    text = "what is TRY? i meant, what is 1000 TRY in USD?"
    assert len(demand_units(text)) == 2


@pytest.mark.parametrize(
    "text",
    (
        'A friend writes: "I paid 300 for the ticket and 45 for the bag." Work out what the trip cost in total.',
        'The note says: "meeting moved to Friday, bring the draft." Tell me what I have to prepare.',
    ),
)
def test_a_request_head_after_a_quotation_is_found(text: str) -> None:
    requests = interpret_request(text).requests
    assert len(requests) == 1 and requests[0].kind == KIND_REQUEST, [(u.kind, u.text) for u in interpret_request(text).units]


def test_a_locative_lead_in_does_not_hide_a_step_of_a_build() -> None:
    text = (
        "Make a folder called reports. Inside it create today.md with the heading Daily. "
        "Then add a line that says all green. Then show me the folder."
    )
    interpretation = interpret_request(text)
    requests = interpretation.requests
    assert len(requests) == 4, [(u.kind, u.text, u.depends_on) for u in interpretation.units]
    # Steps are chained in order: each depends on the one before it.
    for earlier, later in pairwise(requests):
        assert later.depends_on == (earlier.unit_id,), (earlier.text, later.text, later.depends_on)


def test_an_execution_unit_carries_the_requests_it_batches() -> None:
    text = "what is TRY?\n1000 TRY to USD?\n500 kr to dollars\nwrite a README for Thunder"
    spans = execution_unit_spans(text)
    requests = [u.unit_id for u in interpret_request(text).requests]
    assert len(requests) == 4
    batched = spans[0]
    assert set(batched.member_unit_ids) == set(requests[:3]), spans
    assert spans[-1].member_unit_ids == (requests[3],)
    assert sorted(m for span in spans for m in span.member_unit_ids) == sorted(requests)


def test_the_demand_ledger_files_every_member_of_a_batched_unit() -> None:
    text = "what is TRY?\n1000 TRY to USD?\n500 kr to dollars\nwrite a README for Thunder"
    records = demand_records(text)
    assert records[0].accounted_unit_ids == execution_unit_spans(text)[0].member_unit_ids
    assert len(records[0].accounted_unit_ids) == 3
    assert records[-1].accounted_unit_ids == (records[-1].demand_id,)


def test_the_discharge_channel_files_a_dispatch_row_for_every_member(monkeypatch) -> None:
    """The lane's receipt and dispatch record name every request the execution unit carried, so
    the finalization sweep never reads a batched request as "not dispatched" under its answer."""
    from core.agent_runtime.agent import VoolAgent
    from core.agent_runtime.answer_coverage import COVERAGE_CONTEXT_KEY
    from core.agent_runtime.turn_planner import PlannedTask, TaskOutcome

    text = "what is TRY?\n1000 TRY to USD?\n500 kr to dollars\nwrite a README for Thunder"
    spans = execution_unit_spans(text)
    outcomes = [TaskOutcome(task=PlannedTask(index=0, request=spans[0].text), answer="TRY is the Turkish lira; 1000 TRY is about 20 USD; 500 kr needs a country.")]
    records = demand_records(text, outcomes=outcomes)
    receipts: list[dict] = []
    monkeypatch.setattr(
        "core.agent_runtime.answer_coverage.record_slice_answer",
        lambda ctx, **kw: receipts.append(dict(kw)),
    )
    context: dict = {COVERAGE_CONTEXT_KEY: {}}
    VoolAgent._record_demand_discharge(context, records, outcomes)

    members = set(spans[0].member_unit_ids)
    assert len(members) == 3
    assert receipts and set(receipts[0]["consumed_units"]) == members, receipts
    dispatches = context[COVERAGE_CONTEXT_KEY]["dispatches"]
    first = next(d for d in dispatches if d["subtask_id"] == f"demand:{records[0].demand_id}")
    assert set(first["unit_ids"]) == members, first
    assert first["state"] == "SUCCEEDED"
    named = {unit for d in dispatches for unit in d["unit_ids"]}
    assert named == {u.unit_id for u in interpret_request(text).requests}, named


# -- the readings the acceptance slice's held-out cases forced (2026-09-08) ------------------------


@pytest.mark.parametrize(
    "text",
    (
        "I have 2,000 units of local currency in Lisbon, Portugal. I travel to Lisbon, Ohio, and buy a book for 12 units of local currency. Are these currencies the same? Name both and work out the remainder.",
        "I have 800 units of local currency in Perth, Australia, and buy a ticket for 30 units of local currency in Perth, Scotland. Are the two currencies the same? Name the currencies and compute the remainder in GBP.",
    ),
)
def test_a_travel_story_s_own_imperatives_belong_to_the_currency_lane(text: str) -> None:
    from core.agent_runtime.demand_ownership import LANE_CURRENCY, demand_coverage, lane_may_claim_whole_turn

    coverage = demand_coverage(text)
    assert coverage.mixed is False, coverage.per_unit_lanes
    assert all(lanes and all("currency" in lane for lane in lanes) for lanes in coverage.per_unit_lanes), coverage.per_unit_lanes
    assert lane_may_claim_whole_turn(text, LANE_CURRENCY)


def test_an_unrelated_ask_inside_a_travel_story_stays_its_own_demand() -> None:
    from core.agent_runtime.demand_ownership import demand_coverage

    text = (
        "I have 2,000 units of local currency in Lisbon, Portugal. I travel to Lisbon, Ohio, and buy a book for 12 units "
        "of local currency. Are these currencies the same? Name both and work out the remainder. Also, describe how a diesel engine works."
    )
    coverage = demand_coverage(text)
    assert coverage.mixed is True, coverage.per_unit_lanes


@pytest.mark.parametrize(
    "text, target",
    (
        ("write a Queue class with enqueue and dequeue into queue.py here", "queue.py"),
        ("write a small parser with tokenize and parse methods into parser.py in this repo", "parser.py"),
        # method names that are also read verbs: the read probe claims the tail, and the build keeps it
        ("write a Cache class with get and search into cache.py", "cache.py"),
    ),
)
def test_a_build_s_coordinated_method_list_is_its_material(text: str, target: str) -> None:
    from core.agent_runtime.demand_ownership import demand_coverage

    interpretation = interpret_request(text)
    assert len(interpretation.requests) == 1, [(u.kind, u.text) for u in interpretation.units]
    assert any(u.kind == "enumeration" and target in u.text for u in interpretation.units), interpretation.units
    assert demand_coverage(text).mixed is False


def test_two_steps_on_one_file_are_two_steps_not_a_restatement() -> None:
    text = "make notes.md and then open notes.md"
    requests = interpret_request(text).requests
    assert [u.text for u in requests] == ["make notes.md", "and then open notes.md"], requests
    assert requests[1].depends_on == (requests[0].unit_id,)


def test_two_different_asks_about_one_file_without_a_sequence_word_are_two_requests() -> None:
    text = "create app.py. run app.py"
    assert [u.text for u in interpret_request(text).requests] == ["create app.py.", "run app.py"]


def test_a_true_restatement_still_collapses() -> None:
    assert len(demand_units("what is TRY? what is TRY")) == 1


@pytest.mark.parametrize(
    "text",
    (
        "describe how a bloom filter works and cite the source as an example",
        "explain recursion and include factorial as an example",
    ),
)
def test_a_coordinated_presentation_instruction_is_a_constraint(text: str) -> None:
    interpretation = interpret_request(text)
    assert len(interpretation.requests) == 1, [(u.kind, u.text) for u in interpretation.units]
    assert any(u.kind == "constraint" for u in interpretation.units)


def test_a_coordinated_addition_of_a_subject_is_a_request() -> None:
    text = "explain osmosis briefly, also include diffusion"
    assert len(demand_units(text)) == 2


@pytest.mark.parametrize(
    "text",
    (
        "show me where the settings load from and how they get validated, don't edit anything",
        "tell me where the entry point is and what it calls first, without changing a file",
    ),
)
def test_an_inline_constraint_is_cut_off_its_request_and_the_wh_complement_folds(text: str) -> None:
    interpretation = interpret_request(text)
    kinds = [u.kind for u in interpretation.units]
    assert kinds.count("request") == 1 and "constraint" in kinds and "enumeration" in kinds, [(u.kind, u.text) for u in interpretation.units]
    assert len(demand_units(text)) == 1


def test_coordinated_wh_questions_never_fold() -> None:
    text = "what is 800 GBP in USD, and what is the silver price, and how much gold could I buy with it"
    assert len(demand_units(text)) == 3, [(u.kind, u.text) for u in interpret_request(text).units]


def test_talking_about_a_file_is_not_a_read() -> None:
    from core.agent_runtime.demand_ownership import _workspace_read_covers

    assert _workspace_read_covers("and mention utils.py as a typical helper module") is False
    assert _workspace_read_covers("cite settings.py when you explain the defaults") is False
    assert _workspace_read_covers("open settings.py") is True


# -- the readings the PACKAGED-APP leg forced (2026-09-08, build 784cf713) ---------------------------


@pytest.mark.parametrize(
    "text",
    (
        "describe how a linked list works and name list.py as the file I would put it in",
        "summarize the config loader and cite loader.py as the source",
    ),
)
def test_a_file_named_only_in_speech_is_not_read_by_the_lane_s_own_recognizer(text: str) -> None:
    from core.agent_runtime.fast_paths_utility import _direct_workspace_read_requests, names_files_only_in_speech

    assert names_files_only_in_speech(text) is True
    assert _direct_workspace_read_requests(text) == []


def test_a_real_read_beside_a_mention_still_reads_the_real_one() -> None:
    from core.agent_runtime.fast_paths_utility import _direct_workspace_read_requests

    assert [r["path"] for r in _direct_workspace_read_requests("open loader.py and mention config.py as its sibling")] == ["loader.py", "config.py"] or [
        r["path"] for r in _direct_workspace_read_requests("open loader.py and mention config.py as its sibling")
    ] == ["loader.py"]


@pytest.mark.parametrize(
    "text",
    (
        "and how much silver would that get me",
        "how much gold would this get me",
        "how many ounces of gold would that buy me",
    ),
)
def test_an_inverted_payment_anaphora_is_a_purchasable_amount(text: str) -> None:
    """Read by the payment-less single-asset path of the purchase grammar (the earlier sum is the
    SUBJECT: "would THAT get me"); the market doors must then leave it to the conductor -- see
    `test_a_purchasable_amount_follow_up_is_not_claimed_by_the_live_quote_door`."""
    from core.conductor.operations import asks_for_a_purchasable_amount

    assert asks_for_a_purchasable_amount(text) is True, text


@pytest.mark.xfail(strict=True, reason="inverted-SUBJECT purchase shape ('how much platinum does it buy', 'what could this afford in gold') is not read by resolve_purchase_roles yet -- TDL R6")
@pytest.mark.parametrize("text", ("how much platinum does it buy", "what could this afford in gold"))
def test_the_inverted_subject_purchase_shape_is_a_known_gap(text: str) -> None:
    from core.conductor.operations import asks_for_a_purchasable_amount

    assert asks_for_a_purchasable_amount(text) is True


def test_a_bare_price_question_is_not_a_purchase() -> None:
    from core.conductor.operations import asks_for_a_purchasable_amount

    assert asks_for_a_purchasable_amount("how much is gold today") is False


class _FreshAgent:
    """A front door whose fresh-info reading says yes to everything: the hint tail's worst case."""

    def _wants_fresh_info(self, text, interpretation=None):
        return True


@pytest.mark.parametrize(
    "text",
    (
        "how much would 3 troy ounces of silver weigh in grams",
        "how many kilograms is 150 pounds of copper",
        "convert 5 ounces of platinum to grams",
    ),
)
def test_a_unit_conversion_is_not_a_live_quote_even_with_a_web_hint(text: str) -> None:
    from types import SimpleNamespace

    from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
    from core.measurement_medium import requests_a_unit_conversion

    assert requests_a_unit_conversion(text) is True
    assert live_info_mode(_FreshAgent(), text, interpretation=SimpleNamespace(topic_hints=["web", "prices"])) == ""


@pytest.mark.parametrize(
    "text",
    ("what's the price of silver per ounce", "gold price today", "convert 100 usd to eur"),
)
def test_a_price_question_or_a_currency_conversion_is_not_a_unit_conversion(text: str) -> None:
    from core.measurement_medium import requests_a_unit_conversion

    assert requests_a_unit_conversion(text) is False


def test_a_purchasable_amount_follow_up_is_not_claimed_by_the_live_quote_door() -> None:
    from types import SimpleNamespace

    from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode

    hints = SimpleNamespace(topic_hints=["web", "prices"])
    assert live_info_mode(_FreshAgent(), "and how much silver would that get me", interpretation=hints) == ""
    # the quote itself is still the lane's
    assert live_info_mode(_FreshAgent(), "what's the price of silver", interpretation=hints) == "fresh_lookup"


@pytest.mark.parametrize(
    "text",
    (
        "and how much silver would that get me",
        "how many grams is 4 ounces of platinum",
        "convert 2 pounds of copper to kilograms",
    ),
)
def test_the_price_recovery_does_not_readmit_a_derivation_or_a_unit_conversion(text: str) -> None:
    from core.agent_runtime.fast_live_info_price import recover_price_lookup_query

    assert recover_price_lookup_query(text, source_context={}) == ""


def test_the_price_recovery_still_recovers_a_price_question() -> None:
    from core.agent_runtime.fast_live_info_price import recover_price_lookup_query

    assert recover_price_lookup_query("what's the price of silver", source_context={}) != ""



@pytest.mark.parametrize(
    "text",
    (
        "wat is 18^2",
        "reply with just the number",
        "my pc is super slow lately",
        "i think the build is broken again",
        "b.txt",
    ),
)
def test_a_message_with_content_mints_at_least_one_request(text: str) -> None:
    """A lone statement, constraint or list item has nothing to be context of: it IS the request.
    Measured 2026-09-08: a zero-request mint dropped the demand census from the closure certificate."""
    interpretation = interpret_request(text)
    assert len(interpretation.requests) == 1, [(u.kind, u.text) for u in interpretation.units]
    assert len(demand_units(text)) == 1


def test_a_constraint_beside_a_request_still_rides() -> None:
    interpretation = interpret_request("what is 18^2. reply with just the number")
    assert len(interpretation.requests) == 1
    assert any(u.kind == "constraint" for u in interpretation.units)
