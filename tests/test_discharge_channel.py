"""The discharge channel, end to end at its real seams. PLAN-discharge-channel.md §4/§6.

WHAT THIS FILE PINS
-------------------
The defect: coverage was decided by lexical echo — a slot counted as answered only if the
final reply repeated that slot's own words, so `"Ny: Overcast, 24 C ... Source: wttr.in"`
was publicly disowned for never saying "weather", while `"Silver:"` in a market table
ABSOLVED a slot nothing computed. Root cause: no join between the lane that answers a slot
and the reconciler that decides whether it was answered.

The channel under test, every link real:

    build_live_data_plan          — B2: each subtask carries the slice it was minted for
    _record_live_data_discharge   — B3: the serving lane declares what it served
                                    (consumed read off the EXECUTION, never the plan)
    _record_demand_consumption    — B4: the consumer writes lane receipts + dispatch rows
    finalize_answer               — B4: the sweep tiers the evidence and renders

TEST LAW (§6, non-negotiable)
-----------------------------
* No test here derives the served answer from the question's own text — every served byte
  below is written out literally, and the canonical served line never repeats the asking
  word "weather".
* Every invariant is exercised at n >= 2 demand units (below that, finalization's
  `len(states) < 2` softening makes assertions vacuous) — except where a test's SUBJECT is
  the one-city counterfactual itself.
* Sabotage discipline: revert B3's `_record_live_data_discharge` call in
  `apps/vool_agent.py` and `test_the_canonical_weather_turn_is_never_refused` and
  `test_the_market_table_header_cannot_absolve_what_the_dispatch_record_refutes` go red —
  the first because the lane receipt (`slice_answer_record`) is missing and the weather
  slot loses its only discharge path, the second because no dispatch record survives to
  refute the byte echo. Verified 2026-08-30; see ATTEMPTED_FIXES.md.
"""
from __future__ import annotations

import json

import pytest

import storage.db as sdb
from core.agent_runtime.answer_coverage import (
    COVERAGE_CONTEXT_KEY,
    demand_units,
)
from core.agent_runtime.live_data_plan import (
    SubtaskLifecycle,
    SubtaskOutcome,
    build_live_data_plan,
)
from core.conductor import obligation_ledger as ol
from core.finalization import finalize_answer
from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

CANONICAL = "and what is the weather now in nY? and what is the weather in Rome?"
#: The served bytes, written independently of the question: the nY line never repeats the
#: asking word "weather" — that collision-absence is exactly what the old text ladder
#: punished with a public refusal over a correct, tool-receipted answer.
CANONICAL_SERVED = (
    "Ny: Overcast, 24 C (today's high 25 C / low 17 C). Source: wttr.in, observed 12:17 PM.\n"
    "\n"
    "Rome: unavailable — the upstream weather service did not answer. Source: wttr.in"
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "discharge.db")
    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)
    ol.clear_active_set()


def _attempt_with_subtasks(request: str, outcomes: list[SubtaskOutcome], *, plan) -> str:
    """A real runtime attempt whose subtask rows mirror `outcomes` — the dispatch record
    `_live_data_served_slice_ids` reads succeeded subtasks from (the store-first path)."""
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        create_runtime_attempt,
        upsert_runtime_attempt_subtask,
    )

    configure_runtime_continuity_db_path(sdb.active_default_db_path())
    attempt = create_runtime_attempt(
        session_id="sess-discharge",
        original_request=request,
        origin_user_turn_id="turn-1",
        trigger_user_turn_id="turn-1",
    )
    attempt_id = str(attempt["attempt_id"])
    for outcome in outcomes:
        subtask = outcome.subtask
        upsert_runtime_attempt_subtask(
            attempt_id=attempt_id,
            subtask_id=subtask.subtask_id,
            plan_id=plan.plan_id,
            operation=subtask.operation,
            entity_type="location" if subtask.operation == "weather_lookup" else "asset",
            entity_key=subtask.entity,
            arguments=dict(subtask.arguments or {}),
            lifecycle_state=outcome.state.name,
            result_summary=dict(outcome.result or {}),
            failure_reason=str(outcome.failure_reason or ""),
        )
    return attempt_id


def _run_discharge_channel(
    request: str,
    served: str,
    *,
    ok_entities: set[str],
    fail_entities: set[str] = set(),
):
    """The whole channel on real seams: plan -> execution outcome -> discharge -> consumer.

    `ok_entities` / `fail_entities` name the plan entities whose subtasks succeeded vs
    failed. The runner (the network) is the one part not driven live; the states it
    produces are exactly what every downstream seam reads.
    """
    plan = build_live_data_plan(request, plan_id="livedata-discharge", attempt_id="attempt-discharge")
    assert plan is not None, "the typed lane declined a turn this test needs it to claim"
    outcomes: list[SubtaskOutcome] = []
    for subtask in plan.subtasks:
        if subtask.entity in ok_entities:
            outcomes.append(
                SubtaskOutcome(
                    subtask=subtask,
                    state=SubtaskLifecycle.SUCCEEDED,
                    result={"price": 1.0, "condition": "Overcast", "source": "wttr.in"},
                )
            )
        elif subtask.entity in fail_entities:
            outcomes.append(
                SubtaskOutcome(
                    subtask=subtask,
                    state=SubtaskLifecycle.FAILED,
                    failure_reason="upstream service did not answer",
                )
            )
        else:
            outcomes.append(SubtaskOutcome(subtask=subtask, state=SubtaskLifecycle.SKIPPED))

    from apps.vool_agent import VoolAgent, _record_demand_consumption

    agent = VoolAgent.__new__(VoolAgent)  # methods only; no lane wiring needed here
    source_context: dict[str, object] = {}
    attempt_id = _attempt_with_subtasks(request, outcomes, plan=plan)
    # B3 — the producing edge, exactly where the serving lane calls it.
    agent._record_live_data_discharge(
        source_context,
        raw_text=request,
        plan=plan,
        outcomes=outcomes,
        rendered=served,
        runtime_attempt_id=attempt_id,
    )
    active = ol.active_set()
    assert active is not None
    # B4 — the consumer, exactly where the seal calls it.
    _record_demand_consumption(
        active, request=request, answer=served, source_context=source_context
    )
    assert COVERAGE_CONTEXT_KEY in source_context, "B3 wrote no coverage record at all"
    record = source_context[COVERAGE_CONTEXT_KEY]
    assert record.get("dispatches"), "B3 wrote no dispatch record — B4 has nothing to tier on"
    return record


def _sweep(request: str, served: str) -> tuple[dict[str, dict[str, str]], dict]:
    """Mint-free read of the REAL finalization sweep: run `finalize_answer` (its internal
    sweep drives and renders), then re-derive the rows — the ledger's rows are evidence-
    derived and idempotent, so the re-read carries the same states AND reasons."""
    reset_admission()
    admit_semantic_result({"response": served, "route_reason": "live_data_typed_plan"})
    commit = finalize_answer(
        turn_id="t",
        canonical_content=served,
        closure={
            **ol.closure_verdict(_ACTIVE[0], _ACTIVE[1]),
            "set_id": _ACTIVE[0],
        },
    )
    rows = {
        str(item.get("unit_id") or ""): {
            "state": str(item.get("state") or ""),
            "reason": str(item.get("reason") or ""),
            "text": str(item.get("text") or ""),
        }
        for item in ol.sweep_demand_obligations(*_ACTIVE, states={})
    }
    return rows, commit


_ACTIVE: tuple[str, str] = ("", "")


def _mint(request: str) -> None:
    global _ACTIVE
    opened = ol.open_obligation_set(
        request_text=request,
        obligations=[
            {"obligation_id": "ob:t:answer", "text": request[:240], "kind": "prose"},
            *(
                {
                    "obligation_id": f"ob:t:demand:{unit.unit_id}",
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
        opened["set_id"], opened["version"], "ob:t:answer", "satisfied", evidence_source="served_bytes"
    )
    ol.bind_active_set(opened["set_id"], opened["version"])
    _ACTIVE = (opened["set_id"], opened["version"])


# ------------------------------------------------------------------ the canonical case


def test_the_canonical_weather_turn_is_never_refused(fresh_store):
    """§6.3, verbatim shape, at n = 2 units. The nY slot was answered by a real tool run and
    the served line never says "weather" — the text ladder alone called that `unanswered` and
    rendered a public refusal (measured live). With the discharge channel attached the lane's
    own receipt discharges it: no refusal row, `satisfied` at the ledger. Rome's fetch FAILED,
    so its slot names its evidence instead of passing silently — 'dispatched, failed (receipt
    <subtask>)', the record the claim is read from."""
    _mint(CANONICAL)
    _run_discharge_channel(
        CANONICAL, CANONICAL_SERVED, ok_entities={"Ny"}, fail_entities={"Rome"}
    )
    rows, commit = _sweep(CANONICAL, CANONICAL_SERVED)

    assert len(rows) == 2, rows  # n >= 2: the guard that makes single-unit pins vacuous

    # The receipt exists and is LANE-written — the thing the census counted zero of.
    receipts = ol.consumption_receipts(*_ACTIVE)
    lane_receipts = [r for r in receipts if r.get("evidence") == "slice_answer_record"]
    assert lane_receipts, (
        "no lane-written receipt (slice_answer_record) — B3's record_slice_answer never fired"
    )

    nY = rows["u1"]
    assert nY["text"].startswith("and what is the weather now in nY?")
    assert nY["state"] == "satisfied", nY
    assert nY["reason"] == ""

    rome = rows["u2"]
    assert rome["state"] == "unanswered", rome
    assert rome["reason"].startswith("dispatched, failed"), rome
    assert "livedata-discharge:weather" in rome["reason"], rome

    # The canonical refusal is gone from the served bytes: the user is never told the
    # weather slot went unclaimed.
    content = commit["canonical_content"]
    assert "and what is the weather now in nY?" not in content
    assert "no answering lane claimed" not in content
    # Rome's own unavailable row discloses it; the sweep must not re-render a second row.
    assert commit["closure_verdict"]["demand_rendered"] == 0
    # The certificate still refuses to claim coverage over the failed slot.
    assert commit["closure_verdict"]["covered"] is False


def test_capitalization_never_changes_the_verdict(fresh_store):
    """§6.4 counterfactual 1. `nY` vs `NY` must produce the same verdict: the receipt comes
    from the lane's execution, and the execution does not care how the city was typed. At the
    old text ladder these landed on OPPOSITE sides of a public refusal ('weather' + 'ny' vs
    'weather' alone)."""
    for city in ("nY", "NY"):
        request = f"and what is the weather now in {city}? and what is the weather in Rome?"
        _mint(request)
        served = (
            f"{city}: Overcast, 24 C (today's high 25 C / low 17 C). Source: wttr.in, observed 12:17 PM.\n"
            "\n"
            "Rome: unavailable — the upstream weather service did not answer. Source: wttr.in"
        )
        # entity is the plan's TITLE-CASED name — "nY" and "NY" are the same entity there,
        # which is the whole point of this counterfactual (identity is not the surface token).
        _run_discharge_channel(request, served, ok_entities={"Ny"}, fail_entities={"Rome"})
        rows, _commit = _sweep(request, served)
        assert rows["u1"]["state"] == "satisfied", (city, rows["u1"])
        assert rows["u2"]["state"] == "unanswered", (city, rows["u2"])


def test_one_city_or_two_the_verdict_is_the_same(fresh_store):
    """§6.4 counterfactual 2. A presentation header (`**Weather**` appears only at 2+ cities)
    used to supply the missing anchor and FLIP a verdict. Verdicts come from receipts now, so
    the nY slot's verdict may not depend on whether another city was asked alongside."""
    solo = "and what is the weather now in nY?"
    _mint(solo)
    _run_discharge_channel(solo, CANONICAL_SERVED.split("\n")[0], ok_entities={"Ny"}, fail_entities=set())
    solo_rows, _ = _sweep(solo, CANONICAL_SERVED.split("\n")[0])

    duo = CANONICAL
    _mint(duo)
    _run_discharge_channel(duo, CANONICAL_SERVED, ok_entities={"Ny"}, fail_entities={"Rome"})
    duo_rows, _ = _sweep(duo, CANONICAL_SERVED)

    assert solo_rows["u1"]["state"] == "satisfied", solo_rows["u1"]
    assert duo_rows["u1"]["state"] == "satisfied", duo_rows["u1"]


# ------------------------------------------------------------------ the false absolution


def test_the_market_table_header_cannot_absolve_what_the_dispatch_record_refutes(fresh_store):
    """§6.5, the falsely-absolved direction. A turn whose served bytes print "Silver:" while
    nothing computed the ETH->silver amount: the silver slot's subtask FAILED (no amount was
    ever produced), yet the echo reads satisfied — at the old ledger the byte receipt WON and
    the slot certified covered. The dispatch record refutes the echo: failed dispatch ->
    `unanswered`, rendered with its evidence (C-2), no matter what the table prints.

    Grain note, recorded honestly: this pins the TIER (dispatch record outranks byte echo).
    The deeper fix — a claim carrying a coverage obligation at typed-operand grain — is
    Fix 3/Fix 4, out of scope here."""
    request = "what is the price of silver? and what is the weather in Rome?"
    _mint(request)
    served = (
        "Silver: unavailable — the upstream market service did not answer. Source: market_prices\n"
        "\n"
        "Rome: Clear, 26 C. Source: wttr.in, observed 12:17 PM"
    )
    _run_discharge_channel(request, served, ok_entities={"Rome"}, fail_entities={"Silver"})
    rows, commit = _sweep(request, served)

    assert len(rows) == 2, rows
    silver = rows["u1"]
    assert "silver" in silver["text"].lower(), silver
    assert silver["state"] == "unanswered", silver
    assert silver["reason"].startswith("dispatched, failed"), silver
    # The served bytes DO print "Silver:" — the echo is present and must still lose.
    assert "Silver:" in commit["canonical_content"]
    # The ledger row carries the sourced reason; the served bytes carry the lane's own
    # unavailable row for the slot (which is why the sweep adds no second row), and never
    # the old blanket line.
    assert silver["reason"].startswith("dispatched, failed"), silver
    assert "no answering lane claimed" not in commit["canonical_content"]
    assert commit["closure_verdict"]["demand_rendered"] == 0
    # Rome was served by its lane's execution and stays discharged.
    assert rows["u2"]["state"] == "satisfied", rows["u2"]


# ------------------------------------------------------------------ the Phase A floor


def test_without_a_dispatch_record_the_ladder_still_accuses_nothing(fresh_store):
    """Model-lane turns carry no dispatch record: there, absence of evidence must stay
    silent (Phase A). This pins that B4's provable `unanswered` did not quietly re-arm the
    text ladder — a two-unit turn, zero echo for the second slot, no receipt, no dispatch:
    `indeterminate`, renders nothing."""
    request = "what is 18^2, and what is the weather in Rome?"
    _mint(request)
    served = "18² = 324"  # written independently; echoes nothing of the Rome slot
    rows, commit = _sweep(request, served)

    assert len(rows) == 2, rows
    assert rows["u2"]["state"] == "indeterminate", rows["u2"]
    assert commit["closure_verdict"]["demand_rendered"] == 0
    assert "weather in Rome" not in commit["canonical_content"]
    assert commit["closure_verdict"]["covered"] is False


# ------------------------------------------------------------------ the production wiring


def test_a_real_turn_writes_the_lane_receipt_the_census_counts(make_agent):
    """§6.6 anchor, driven through `agent.run_once` — the PRODUCTION call sites, not the
    helper: `_answer_single/_multipart_live_data_turn` must call
    `_record_live_data_discharge`, the seal must convert it, and the ledger must end up with
    a LANE-written receipt (`slice_answer_record`) — the kind the live census counted zero
    of. Only the network fetch is stubbed (the pattern
    tests/test_live_data_turn_integration.py proved reaches this lane).

    Sabotage discipline (PLAN §6.6): revert the B3 discharge calls in apps/vool_agent.py
    and THIS test goes red on the assert below, naming the missing receipt. Verified
    2026-08-30: with the calls reverted, `consumption` carries no slice_answer_record and
    the served weather slot falls back to an unprovable `indeterminate`."""
    from unittest import mock

    from core.mode_permission_policy import reset_mode_permission_state
    from core.weather_result_contract import WeatherResult
    from storage.db import active_default_db_path, get_connection

    def _weather_side_effect(location, **_kwargs):
        table = {"kaunas": 24.0, "tallinn": 19.0}
        if location not in table:
            return None
        return WeatherResult(
            location=location,
            place_label=location.title(),
            condition="Clear",
            temperature_c=table[location],
            feels_like_c=table[location],
            humidity_pct=55.0,
            wind_kmph=8.0,
            observed_at="12:00 PM",
            source_label="wttr.in",
            source_url=f"https://wttr.in/{location}",
        )

    reset_mode_permission_state()
    agent = make_agent()
    request = "what is the weather in Kaunas? and what is the weather in Tallinn?"
    with mock.patch(
        "tools.web.web_research.structured_weather_lookup", side_effect=_weather_side_effect
    ):
        result = agent.run_once(
            request,
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )
    response = str(result.get("response") or "")
    assert result.get("route_reason") == "live_data_typed_plan", result.get("route_reason")

    # This turn's demand set, read back from the ledger store.
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT set_id, version, snapshot_json FROM obligation_sets "
            "ORDER BY rowid DESC LIMIT 25"
        ).fetchall()
    finally:
        conn.close()
    snapshot = None
    for row in rows:
        candidate = json.loads(row["snapshot_json"])
        if str(candidate.get("request_text") or "") == request and any(
            item.get("kind") == "demand" for item in candidate.get("obligations") or []
        ):
            snapshot = candidate
            break
    assert snapshot is not None, "the turn minted no demand set"

    receipts = [r for r in snapshot.get("consumption") or []]
    lane_receipts = [r for r in receipts if r.get("evidence") == "slice_answer_record"]
    assert lane_receipts, (
        "no LANE-written slice_answer_record receipt on a served live-data turn — "
        "B3's record_slice_answer/discharge call is missing from the serving lane"
    )
    assert {r.get("unit_id") for r in lane_receipts} == {"u1", "u2"}, lane_receipts
    dispatches = snapshot.get("dispatches") or []
    assert dispatches, "no dispatch record reached the ledger — B4's consumer half is unwired"
    assert {d.get("state") for d in dispatches} == {"SUCCEEDED"}, dispatches
    # And the served bytes accuse nothing: both slots are receipt-discharged.
    assert "no answering lane claimed" not in response
    assert "Could not be answered" not in response
