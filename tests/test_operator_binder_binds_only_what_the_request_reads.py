"""The operator binder binds only the fragments the operator lane's own reading of the request uses.

THE MEASURED DEFECT (in-process through the production catalog, 2026-09-15, at 1d58a641). The operator binder,
`demand_ownership._operator_action_binds`, returned EVERY minted unit whenever `parse_operator_action_intent` claimed
the whole text, and the execution grain trusts that binding in both directions:
* 'open my Apple note "Plan", Ukraine situation' -> ONE execution unit read by operator_action_dispatch (the look-back
  law's same-family reading, `_rides_the_request_before_it`);
* 'Ukraine situation, open my Apple note "Plan"' and 'draft a reply to that, open my Apple note "Plan"' -> ONE unit (the
  forward law's binder-service path, `_leads_into_the_request_after_it`).
The operator lane could end the turn alone, and the topic or the request beside the note vanished (R1e).

THE LAW UNDER TEST. A fragment is bound only when the operator lane's reading of the whole request depends on it
(`core.operator.request_reading.fragments_the_request_reads`): blanking it changes the parsed intent, or an argument
the kind's handler reads -- a folder or account, a title, a payload, the offered slot, a due time, a window, an
occurrence date. The same binding keeps 'Personal folder, open my Apple note "Plan"' and 'option 1, propose "Project
review"' one request.

In-process: no model, no socket. The served proof is tests/pa_beta_gate/test_served_operator_binder_fragments.py.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.agent_runtime.answer_coverage import interpret_request
from core.agent_runtime.demand_ownership import (
    _mint_unit_service,
    _operator_action_binds,
    a_registered_lane_claims,
    demand_coverage,
    execution_unit_spans,
    lane_may_claim_whole_turn,
)
from core.operator.parser import parse_operator_action_intent

LANE_OPERATOR = "operator_action_dispatch"

#: (case, wording, the operator request's execution unit, the fragment that must stand as an execution unit of its own)
UNRELATED_FRAGMENTS = [
    # the reported wordings
    ("original-trailing-topic", 'open my Apple note "Plan", Ukraine situation', 'open my Apple note "Plan"', "Ukraine situation"),
    ("original-leading-topic", 'Ukraine situation, open my Apple note "Plan"', 'open my Apple note "Plan"', "Ukraine situation"),
    ("original-leading-clause", 'draft a reply to that, open my Apple note "Plan"', 'open my Apple note "Plan"', "draft a reply to that"),
    # clean paraphrases: other topics, clauses, titles, and other operator kinds
    ("paraphrase-trailing-results", 'show my Apple note "Groceries", the election results', 'show my Apple note "Groceries"',
     "the election results"),
    ("paraphrase-leading-talks-read", 'Gaza ceasefire talks, read the Apple note "Budget"', 'read the Apple note "Budget"',
     "Gaza ceasefire talks"),
    ("paraphrase-leading-request-append", 'write a haiku about autumn, append to my Apple note "Poems" with "leaves"',
     'append to my Apple note "Poems" with "leaves"', "write a haiku about autumn"),
    ("paraphrase-trailing-topic-append", 'append to my Apple note "Poems" with "leaves", chess openings',
     'append to my Apple note "Poems" with "leaves"', "chess openings"),
    ("paraphrase-leading-topic-reminder", "Lakers score, remind me to water the plants tomorrow at 9:00",
     "remind me to water the plants tomorrow at 9:00", "Lakers score"),
    ("paraphrase-trailing-topic-availability", "check Tuesday afternoon for a free 30-minute slot, the Oscars shortlist",
     "check Tuesday afternoon for a free 30-minute slot", "the Oscars shortlist"),
    ("paraphrase-trailing-topic-reminder-id", "cancel reminder 1a2b3c4d, the Oscars shortlist", "cancel reminder 1a2b3c4d",
     "the Oscars shortlist"),
    # sloppy, user-typed
    ("sloppy-spaced-comma-lowercase", 'open my apple note "Plan" , ukraine situation', 'open my apple note "Plan"', "ukraine situation"),
    ("sloppy-topic-typo", 'ukraine situaton, open my Apple note "Plan"', 'open my Apple note "Plan"', "ukraine situaton"),
    ("sloppy-typos-filler", 'yo draft a reply to taht, open my apple note "Plan" pls', 'open my apple note "Plan" pls',
     "yo draft a reply to taht"),
    ("sloppy-no-space-after-comma", 'show apple note "Groceries",election results', 'show apple note "Groceries"', "election results"),
    ("sloppy-shouted-request", 'OPEN MY APPLE NOTE "Plan", ukraine situation', 'OPEN MY APPLE NOTE "Plan"', "ukraine situation"),
    ("sloppy-shouted-leading-topic", 'UKRAINE SITUATION, OPEN MY APPLE NOTE "Plan"', 'OPEN MY APPLE NOTE "Plan"', "UKRAINE SITUATION"),
    ("sloppy-article-typo-read", 'gaza ceasfire talks, read teh Apple note "Budget"', 'read teh Apple note "Budget"', "gaza ceasfire talks"),
    ("sloppy-spaced-reminder", "lakers score , remind me to water the plants tomorrow at 9:00",
     "remind me to water the plants tomorrow at 9:00", "lakers score"),
    ("sloppy-filler-append", 'chess openings pls, append to my apple note "Poems" with "leaves"',
     'append to my apple note "Poems" with "leaves"', "chess openings pls"),
    ("sloppy-lowercase-availability", "the oscars shortlist, check tuesday afternoon for a free 30 minute slot",
     "check tuesday afternoon for a free 30 minute slot", "the oscars shortlist"),
    # a folder the read uses rides it while the topic beside them stands apart
    ("mixed-scope-rides-topic-trails", 'Recipes folder, show my Apple note "Soup", Ukraine situation',
     'Recipes folder, show my Apple note "Soup"', "Ukraine situation"),
    ("mixed-topic-leads-scope-rides", 'Ukraine situation, Recipes folder, show my Apple note "Soup"',
     'Recipes folder, show my Apple note "Soup"', "Ukraine situation"),
    # adversarial near-misses: shaped like an argument the lane takes, but its reading takes nothing from them
    ("near-miss-scope-shaped-topic", 'Personal matters, open my Apple note "Plan"', 'open my Apple note "Plan"', "Personal matters"),
    ("near-miss-scope-noun-without-a-name", 'the folder situation, open my Apple note "Plan"', 'open my Apple note "Plan"',
     "the folder situation"),
    ("near-miss-option-without-a-number", 'option pricing basics, propose "Project review"', 'propose "Project review"',
     "option pricing basics"),
]

#: (case, wording): every fragment is an argument the operator request reads, so the request stays ONE execution unit.
ARGUMENT_FRAGMENTS = [
    ("control-bare-folder", 'Personal folder, open my Apple note "Plan"'),
    ("control-inside-possessive-folder", 'Inside my Errands folder, show the Apple note "Groceries"'),
    ("control-novel-folder", 'Recipes folder, show my Apple note "Soup"'),
    ("control-spaced-sloppy-folder", 'Personal folder , show my apple note "Plan"'),
    ("control-account-before-a-rename", 'in the iCloud account, rename the Apple note "Plan" in the Work folder to "Plan v2"'),
    ("control-offered-slot", 'option 1, propose "Project review"'),
    ("control-spaced-sloppy-slot", 'option 1 , propose "Project review"'),
    ("control-offered-slot-with-duration", 'option 3, propose "Budget sync" for 45 minutes'),
    ("control-due-time", "In 45 seconds, remind me in this chat to inspect the beta export."),
    ("control-due-time-sloppy", "in 45 secs, remind me to stretch"),
    ("control-note-body-continuation", 'save a note to Apple Notes titled "Chiller alarm" with: compressor tripped at 03:40, reset at 03:55'),
    ("control-note-body-continuation-novel", 'save a note titled "Deploy log" with: build passed, tests green'),
    ("control-availability-day", "Tuesday afternoon, check my calendar for a free 30-minute slot"),
    ("control-occurrence-date", 'October 7, cancel the "Standup" event'),
    ("control-series-scope", 'the whole series, cancel the "Standup" event'),
    ("control-leading-approval", 'yes go ahead, delete my Apple note "Plan"'),
    ("control-leading-title", 'titled "Trip", save a note to Apple Notes with: pack chargers'),
]


@pytest.mark.parametrize(("case", "wording", "request_text", "fragment"), UNRELATED_FRAGMENTS, ids=[row[0] for row in UNRELATED_FRAGMENTS])
def test_a_fragment_the_request_does_not_read_stands_as_its_own_unit(case, wording, request_text, fragment):
    requests = interpret_request(wording).requests
    # The case exercises the binder: the operator parser claims the whole text, the mint cuts the fragment as a request
    # unit of its own, and no lane serves the fragment on its own.
    assert parse_operator_action_intent(wording) is not None, case
    fragment_units = [unit for unit in requests if unit.text.strip() == fragment]
    assert len(fragment_units) == 1, (case, [unit.text for unit in requests])
    assert not a_registered_lane_claims(fragment), case
    # THE SEAM: the operator binder does not bind the fragment.
    assert fragment_units[0].unit_id not in _operator_action_binds(wording, requests), case
    spans = [span.text for span in execution_unit_spans(wording)]
    assert sorted(spans) == sorted([request_text, fragment]), (case, spans)
    coverage = demand_coverage(wording)
    lanes = dict(zip((text for _unit_id, text in coverage.units), coverage.per_unit_lanes, strict=True))
    assert LANE_OPERATOR in lanes[request_text] and LANE_OPERATOR not in lanes[fragment], (case, lanes)
    assert coverage.mixed and not lane_may_claim_whole_turn(wording, LANE_OPERATOR), (case, lanes)


@pytest.mark.parametrize(("case", "wording"), ARGUMENT_FRAGMENTS, ids=[row[0] for row in ARGUMENT_FRAGMENTS])
def test_a_fragment_the_request_reads_rides_it(case, wording):
    requests = interpret_request(wording).requests
    # The mint cuts the argument as a unit of its own: the case exercises the binder.
    assert len(requests) >= 2, (case, [unit.text for unit in requests])
    assert _operator_action_binds(wording, requests) == {unit.unit_id for unit in requests}, case
    assert [span.text for span in execution_unit_spans(wording)] == [wording.strip()], case
    assert lane_may_claim_whole_turn(wording, LANE_OPERATOR), case


def test_a_reading_that_raises_keeps_the_pre_binder_grain(monkeypatch):
    """A reader that fails leaves the reading UNAVAILABLE, not empty: the grain keeps the pre-binder behaviour for the
    turn (`_mint_unit_service` reports readers_intact False), so accounting never splits an operator request on the
    strength of a reader that did not run."""
    from core.operator import request_reading

    def unavailable(text, intent):
        raise RuntimeError("the scope reader is unavailable")

    wording = 'open my Apple note "Plan", Ukraine situation'
    monkeypatch.setitem(request_reading._ARGUMENT_READERS, "apple_note_read", (unavailable,))
    interpret_request.cache_clear()
    try:
        _served, intact = _mint_unit_service(wording, interpret_request(wording).requests)
        assert intact is False
        assert [span.text for span in execution_unit_spans(wording)] == [wording]
    finally:
        interpret_request.cache_clear()


def test_every_operator_kind_has_a_declared_reading():
    """A completeness guard over DECLARATIONS, not a behaviour test: every kind the operator parser returns and every
    kind the dispatcher routes is either read through its handler's argument readers or declared intent-only, so a
    new kind cannot fall silently to a reading of its intent fields."""
    from core.operator.request_reading import read_kinds

    root = Path(__file__).resolve().parents[1]
    parser_kinds = set(re.findall(r'kind="([a-z_]+)"', (root / "core/operator/parser.py").read_text(encoding="utf-8")))
    dispatched = set(re.findall(r'intent\.kind == "([a-z_]+)"', (root / "core/local_operator_actions.py").read_text(encoding="utf-8")))
    assert parser_kinds and dispatched
    assert (parser_kinds | dispatched) - read_kinds() == set()


def test_a_connector_led_folder_without_a_preposition_rides_the_read():
    # Moved out of xfail on 2026-09-15: the Notes scope reader (`apple_notes._ScopeReader`) now reads
    # a connector-led, preposition-less folder name ('and the Work folder', 'and Work folder'), so the
    # lane's reading takes the fragment and the scope rides the Notes request it fronts.
    wording = 'and the Work folder, show me the Apple note "Soup" too'
    assert [span.text for span in execution_unit_spans(wording)] == [wording]


@pytest.mark.xfail(
    strict=True,
    reason="residual outside the operator binder: the mint reads an all-caps phrase after a request as an ENUMERATION "
    "attached to it (answer_coverage kind reading), so it never becomes a request unit; 'what is the bitcoin price, "
    "UKRAINE SITUATION' mints the same enumeration beside a live-data request",
)
def test_a_shouted_trailing_topic_stands_as_its_own_unit():
    wording = 'OPEN MY APPLE NOTE "Plan", UKRAINE SITUATION'
    assert [span.text for span in execution_unit_spans(wording)] == ['OPEN MY APPLE NOTE "Plan"', "UKRAINE SITUATION"]
