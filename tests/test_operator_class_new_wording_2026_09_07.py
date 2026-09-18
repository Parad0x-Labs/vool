"""New wording, same classes: the operator's rule for accepting a fix.

A fix is accepted only when questions the failed turn was never asked in -- different assets,
payers, verbs, directories, typos and markdown -- come out clean. The recorded sentences stay in
their own files as regression pins; THIS file is the acceptance evidence. One file, one section per
class fixed on 2026-09-07.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# --------------------------------------------------------------------------------------------- #
# class 1: a priced-asset purchase mixed with a quote, several payers offered with "or"
# --------------------------------------------------------------------------------------------- #
from core.conductor.planner import _deterministic_purchasable_amount_plan, _purchase_span_text


def _shape(plan):
    return [(c.request, c.operation) for c in plan]


@pytest.mark.parametrize(
    "wording,target,payers",
    [
        ("silver price today? and if i hold 2 eth or 0.5 btc how much silver could i get", "silver", [("2", "eth"), ("0.5", "btc")]),
        ("what does brent cost right now, and how much brent can i pick up with 3 sol or 250 usdc", "brent", [("3", "sol"), ("250", "usdc")]),
        ("give me the gold quote and tell me how much gold 1 bnb or 1 eth buys me", "gold", [("1", "bnb"), ("1", "eth")]),
    ],
)
def test_a_mixed_purchase_with_alternative_payers_is_claimed_and_derived_per_payer(wording, target, payers):
    assert _purchase_span_text(wording), "the quote unit belongs to the purchase whose target the roles resolver names"
    shape = _shape(_deterministic_purchasable_amount_plan(wording))
    quotes = {req for req, op in shape if op == "market_quote"}
    derivations = [req for req, op in shape if op == "quantitative_reasoning"]
    assert f"price of {target}" in quotes, shape
    for _quantity, payer in payers:
        assert f"price of {payer}" in quotes or payer == "usdc", shape
    assert derivations == [f"how much {target} can I buy with {q} {p}" for q, p in payers], shape


def test_a_purchase_beside_a_request_no_purchase_serves_stays_unclaimed_in_new_wording():
    assert _purchase_span_text("how much silver can i get for 2 eth, and is it raining in Vilnius right now") == ""


# --------------------------------------------------------------------------------------------- #
# class 2: the claim gate keeps markdown it did not write -- different structures than the incident
# --------------------------------------------------------------------------------------------- #
from core.claim_support import match_claims, segment_claims
from core.grounding_publication import compose_partial_truth

NOTES = [
    {"result_title": "Volkswagen Passat production history", "result_url": "https://autoarchive-fixture.org/passat-production", "origin_domain": "autoarchive-fixture.org",
     "summary": "Updated 2026-08-20. The Volkswagen Passat has been produced since 1973. Generations: B1 1973-1980, B8 2014-2023, B9 since 2023."},
    {"result_title": "Volkswagen Golf production history", "result_url": "https://autoarchive-fixture.org/golf-production", "origin_domain": "autoarchive-fixture.org",
     "summary": "Updated 2026-08-18. The Volkswagen Golf has been produced since 1974. Generations: Mk1 1974-1983, Mk8 since 2019 with a 2024 facelift."},
]
REQUEST = "compare the passat and the golf: production periods and engines"
NUMBERED = "\n".join([
    "### Production timeline",
    "1. *Passat*: production began in 1973 (e.g. the B1 generation).",
    "2. *Golf*: production began in 1974, etc.",
    "",
    "### Engine figures",
    "1. The Passat B9 offers a 2.0 TSI with 265 PS.",
    "2. The Golf R has 333 PS.",
])


def test_numbered_lists_italics_and_other_abbreviations_survive_gating():
    segments = segment_claims("1. *Passat*: production began in 1973 (e.g. the B1 generation).")
    assert segments == ["Passat: production began in 1973 (e.g. the B1 generation)."], segments
    claim_map = match_claims(answer=NUMBERED, notes=NOTES, request_text=REQUEST)
    body, withheld, _ = compose_partial_truth(NUMBERED, claim_map)
    assert "### Production timeline" in body and "### Engine figures" not in body, body
    assert "1. *Passat*: production began in 1973 (e.g. the B1 generation)." in body, body
    assert "2. *Golf*: production began in 1974, etc." in body, body
    assert not any(line.strip() in {"*", "**", "1.", "2."} for line in body.splitlines()), body
    assert any("265 PS" in w for w in withheld) and any("333 PS" in w for w in withheld), withheld


# --------------------------------------------------------------------------------------------- #
# class 3: a bare verb after a conjunction elaborates; a verb with an object opens
# --------------------------------------------------------------------------------------------- #
from core.agent_runtime.answer_coverage import demand_units
from core.agent_runtime.demand_ownership import execution_unit_spans


@pytest.mark.parametrize(
    "wording",
    [
        "show me the results in a simpler layout and summarize",
        "put the two numbers side by side and rank",
        "make that shorter and then explain",
    ],
)
def test_a_bare_verb_in_new_wording_stays_inside_the_one_request(wording):
    assert len(demand_units(wording)) == 1, [u.text for u in demand_units(wording)]
    assert len(execution_unit_spans(wording)) == 1


@pytest.mark.parametrize(
    "wording",
    [
        "show me the results in a simpler layout and summarize the risks",
        "put the two numbers side by side and rank every option",
    ],
)
def test_a_verb_with_its_own_object_still_opens_a_second_request(wording):
    assert len(demand_units(wording)) == 2, [u.text for u in demand_units(wording)]


# --------------------------------------------------------------------------------------------- #
# class 4a: a sandbox working directory outside the workspace -- other directories, other shapes
# --------------------------------------------------------------------------------------------- #
from core.effect_reconciliation import clear_in_flight_effect, set_in_flight_effect
from core.runtime_execution_tools import execute_runtime_tool


@pytest.mark.parametrize("cwd", ["/tmp", "/Library", "../../..", "/etc"])
def test_other_outside_directories_are_refused_by_name_even_mid_effect(tmp_path: Path, cwd: str):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    token = set_in_flight_effect({"tool_name": "sandbox.run_command", "logical_effect_id": "le-x", "effect_instance_id": "ei-x"})
    try:
        result = execute_runtime_tool("sandbox.run_command", {"command": "du -sh . | sort -h", "cwd": cwd}, source_context={"workspace_root": str(workspace), "workspace": str(workspace)})
    finally:
        clear_in_flight_effect(token)
    assert result is not None and result.status == "cwd_outside_workspace", (cwd, result.status, result.response_text[:160])
    assert "unknown" not in result.response_text.lower()


# --------------------------------------------------------------------------------------------- #
# class 4b: mistyped largest-file questions in other words reach the machine tool
# --------------------------------------------------------------------------------------------- #
from core.execution.constants import machine_largest_intent


@pytest.mark.parametrize(
    "wording",
    [
        "whats the bigest file on this mac?",
        "which of my folers take up the most spcae on disk?",
        "list the top 5 largets files under my home",
    ],
)
def test_other_typos_of_the_largest_file_question_reach_the_tool(wording):
    assert machine_largest_intent(wording) == "machine.find_largest", wording


@pytest.mark.parametrize("wording", ["what is the lurgest file here", "how do people usually find big files on a mac?"])
def test_substitutions_and_advice_still_do_not_claim(wording):
    assert machine_largest_intent(wording) is None, wording


# --------------------------------------------------------------------------------------------- #
# class 5: a payment-less purchase after a conversion spends the converted amount (sloppy wording, new)
# --------------------------------------------------------------------------------------------- #
from tests.test_multi_intent_served_coverage import (  # noqa: E402
    _drive,
    _fx_fetcher,
    _multi_intent_seams,  # noqa: F401  (fixture; pytest collects it from this module's namespace)
    _plan_reply,
)


def test_a_payment_less_purchase_after_a_conversion_is_derived_in_new_sloppy_wording(make_agent, monkeypatch, _multi_intent_seams):
    text = "temprature in Oslo pls, also change 50 USD into RUB and how much gold does that get me"
    split = _plan_reply(
        {"request": "temprature in Oslo pls", "operation": "weather_lookup", "depends_on": []},
        {"request": "also change 50 USD into RUB", "operation": "fx_quote", "depends_on": []},
        {"request": "how much gold does that get me", "operation": "market_quote", "depends_on": []},
    )
    result, telemetry = _drive(make_agent, monkeypatch, text=text, split=split, session_id="multi-intent-new-wording-fx-gold", fx=_fx_fetcher())
    assert result["route_reason"] == "conductor_multi_intent_plan"
    flat = result["response"].replace(",", "")
    for needle in ("Oslo", "12", "4000", "2400"):
        assert needle in flat, f"{needle!r} missing from response: {flat[:400]}"
    assert "internal fault" not in flat and "Much Gold" not in flat, flat[:500]
    assert len(telemetry["decompositions"]) == 1
