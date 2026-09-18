"""An attempt is not an observation: the evidence-sufficiency boundary, at every writer it guards.

Reproduced at eca76ff9 through the real `/api/chat` seam: a weather follow-up whose lookup FAILED
shipped `Porto: Cloudy, high 22 C, low 17 C right now. Source: wttr.in, observed <the previous
turn's timestamp>`. Every record of what happened was correct -- the receipt said
`status='failed' source_count=0 failure_class='no observation returned'` -- and both predicates that
read those records to answer "did this turn observe?" said yes, because they counted the ENTRY and
never read the outcome inside it. The end-to-end proof of the whole conversation lives in
`tests/gauntlet/test_a_failed_retrieval_never_authorizes_a_fact.py`; this file pins the boundary
itself, against the shapes the writers really produce.

Every receipt and observation in here is built by CALLING the writer that owns it -- the live-data
receipt lane, the generic web-retrieval lane, the FX lane, the tool-history lane, the execution-record
store -- rather than by hand-writing a dict that looks like its output. A hand-written fixture is a
second opinion about a format, and the first time a lane changes its own shape the guard silently
stops recognising failures while every test stays green.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.live_data_plan import LiveDataSubtask, SubtaskLifecycle, SubtaskOutcome
from core.live_data_retrieval_receipts import receipt_for_outcome
from core.model_output_guard import turn_ran_observations
from core.observation_evidence import (
    channel_has_a_usable_observation,
    records_a_usable_observation,
    usable_observations,
)
from core.retrieval_observability import finish_web_retrieval
from core.unsourced_current_claim import inspect_unsourced_current_claim, turn_has_current_evidence

# --------------------------------------------------------------------------------- real fixtures


def _subtask(entity: str, operation: str = "weather_lookup") -> LiveDataSubtask:
    return LiveDataSubtask(
        subtask_id=f"st-{entity.lower()}",
        entity=entity,
        operation=operation,
        arguments={"location": entity} if operation == "weather_lookup" else {"asset_key": entity.lower()},
        required_result_fields=("temperature_c",),
        tool="weather" if operation == "weather_lookup" else "market_prices",
        tool_intent="web.research",
    )


def live_data_receipt(entity: str, *, ok: bool, operation: str = "weather_lookup") -> dict:
    """A receipt from the real live-data lane, for a subtask that really succeeded or really failed."""
    if ok:
        outcome = SubtaskOutcome(
            subtask=_subtask(entity, operation),
            state=SubtaskLifecycle.SUCCEEDED,
            result={"temperature_c": 17.0, "condition": "Clear", "source": "wttr.in",
                    "observed_at": "2026-08-16T09:00:00Z"},
        )
    else:
        outcome = SubtaskOutcome(
            subtask=_subtask(entity, operation),
            state=SubtaskLifecycle.FAILED,
            failure_reason="no observation returned",
        )
    receipt = receipt_for_outcome(outcome, plan_id="livedata-test")
    assert receipt is not None, "the live-data lane must receipt an executed remote subtask"
    return receipt


def web_receipt(*, ok: bool, raised: bool = False) -> dict:
    """A terminal receipt from the real generic web-retrieval lane."""
    started = {
        "schema": "vool.web_retrieval_receipt.v1", "retrieval_id": "web-retrieval-test",
        "kind": "web_search", "action": "search", "task_id": "t", "query_hash": "h",
        "status": "started", "source_count": 0, "source_domains": [],
        "started_at": "", "completed_at": "", "failure_class": "",
    }
    if ok:
        return finish_web_retrieval(None, started, notes=[{"summary": "a real result", "origin_domain": "example.org"}])
    if raised:
        return finish_web_retrieval(None, started, failure=TimeoutError("read timed out"))
    return finish_web_retrieval(None, started, notes=[])  # reached the host, returned nothing


def tool_observation(*, ok: bool) -> dict:
    """The tool loop's own same-turn observation payload, from the real builder."""
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload

    class _Execution:
        def __init__(self, good: bool) -> None:
            self.ok = good
            self.status = "executed" if good else "failed"
            self.response_text = "42 C" if good else "connection refused"
            self.details = {}
            self.mode = "tool"
            self.tool_name = "live_data.weather_lookup"

    return tool_history_observation_payload(
        execution=_Execution(ok), tool_name="live_data.weather_lookup", receipt=None
    )


# ------------------------------------------------------------- the invariant, at the entry level


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(live_data_receipt("Porto", ok=False), id="live-data-weather-failed"),
        pytest.param(live_data_receipt("Gold", ok=False, operation="market_quote"), id="live-data-market-failed"),
        pytest.param(web_receipt(ok=False, raised=True), id="web-retrieval-raised"),
        pytest.param(web_receipt(ok=False), id="web-retrieval-returned-nothing"),
        pytest.param(tool_observation(ok=False), id="tool-observation-failed"),
    ],
)
def test_an_entry_that_records_its_own_failure_is_not_an_observation(entry):
    assert records_a_usable_observation(entry) is False
    assert channel_has_a_usable_observation([entry]) is False
    assert usable_observations([entry]) == []


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(live_data_receipt("Vilnius", ok=True), id="live-data-weather-ok"),
        pytest.param(live_data_receipt("Bitcoin", ok=True, operation="market_quote"), id="live-data-market-ok"),
        pytest.param(web_receipt(ok=True), id="web-retrieval-ok"),
        pytest.param(tool_observation(ok=True), id="tool-observation-ok"),
    ],
)
def test_an_entry_that_records_success_is_still_an_observation(entry):
    """The negative control on the repair itself: nothing that DID observe may lose its standing."""
    assert records_a_usable_observation(entry) is True
    assert channel_has_a_usable_observation([entry]) is True


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param("the user pasted this paragraph", id="plain-string-note"),
        pytest.param({"filename": "budget.csv", "bytes": 2048}, id="user-attachment"),
        pytest.param({"summary": "a note with no lifecycle fields at all"}, id="unsignalled-note"),
    ],
)
def test_an_entry_with_no_outcome_signal_still_counts(entry):
    """Material that cannot fail keeps the meaning it always had -- the safe unknown direction.

    Deliberate: an attachment has no `ok`, no `failure_class` and no `source_count` because nothing
    about it can fail. Reading "no signal" as failure would convict turns that really were evidenced
    by what the user handed over, which is a defect invented by the fix rather than closed by it.
    """
    assert records_a_usable_observation(entry) is True


@pytest.mark.parametrize("entry", [None, "", "   ", {}, []])
def test_nothing_is_not_an_observation(entry):
    assert records_a_usable_observation(entry) is False


# --------------------------------------------------------- the invariant, at the two authorities


#: The channels each authority actually reads. They overlap but are NOT the same set, and
#: parametrizing both predicates over one merged list made half the assertions vacuous: a
#: `runtime_tool_observations` entry is invisible to `turn_has_current_evidence`, so asserting False
#: there passed no matter what the entry said. Each authority is pinned over its own channels.
GUARD_CHANNELS = [
    "runtime_tool_observations",
    "web_retrieval_receipts",
    "fresh_data_retrieval_receipts",
    "external_evidence",
]
CLAIM_CHANNELS = [
    "web_retrieval_receipts",
    "fresh_data_retrieval_receipts",
]


@pytest.mark.parametrize("channel", GUARD_CHANNELS)
def test_the_output_guard_does_not_count_a_failed_entry_as_an_observation(channel):
    context = {channel: [live_data_receipt("Porto", ok=False)]}
    assert turn_ran_observations(context) is False


@pytest.mark.parametrize("channel", GUARD_CHANNELS)
def test_the_output_guard_still_counts_a_success_beside_the_failure(channel):
    context = {channel: [live_data_receipt("Porto", ok=False), live_data_receipt("Vilnius", ok=True)]}
    assert turn_ran_observations(context) is True


@pytest.mark.parametrize("channel", CLAIM_CHANNELS)
def test_the_current_claim_guard_does_not_count_a_failed_receipt_as_evidence(channel):
    context = {channel: [live_data_receipt("Porto", ok=False)]}
    assert turn_has_current_evidence(source_context=context) is False


@pytest.mark.parametrize("channel", CLAIM_CHANNELS)
def test_the_current_claim_guard_still_counts_a_success_beside_the_failure(channel):
    context = {channel: [live_data_receipt("Porto", ok=False), live_data_receipt("Vilnius", ok=True)]}
    assert turn_has_current_evidence(source_context=context) is True


def test_both_authorities_agree_on_the_channel_they_share():
    """The measured failure needed only ONE of them to say yes, so the shared channel is pinned twice."""
    failed = {"web_retrieval_receipts": [live_data_receipt("Porto", ok=False)]}
    assert turn_ran_observations(failed) is False
    assert turn_has_current_evidence(source_context=failed) is False


def test_a_failed_note_does_not_evidence_a_current_claim():
    """`notes` is the adaptive-research channel, and a failed note carries a summary of the failure."""
    failed = {"summary": "search failed", "ok": False, "failure_class": "TimeoutError"}
    assert turn_has_current_evidence(notes=[failed]) is False
    assert turn_has_current_evidence(notes=[{"summary": "a real finding"}]) is True


def test_an_unsuccessful_execution_record_does_not_evidence_the_turn():
    """`execution_records` already reads `.ok` everywhere else; this branch did not."""
    from core import execution_records

    session, turn = "evidence-session-ok-filter", "turn-1"
    execution_records.clear(session)
    try:
        execution_records.record(
            session_id=session, intent="live_data.weather_lookup", ok=False,
            status="failed", turn_id=turn,
        )
        assert turn_has_current_evidence(session_id=session, turn_id=turn) is False

        execution_records.record(
            session_id=session, intent="live_data.weather_lookup", ok=True,
            status="executed", turn_id=turn,
        )
        assert turn_has_current_evidence(session_id=session, turn_id=turn) is True
    finally:
        execution_records.clear(session)


# --------------------------------------------------------- the verdict the measured failure needed


def test_the_measured_answer_is_convicted_on_a_failed_lookup():
    """The exact shipped string from the reproduction, against the guard that owns it."""
    measured = (
        "Porto: Cloudy, high 22 C, low 17 C right now. "
        "Source: wttr.in, observed 2026-08-07T12:00:00Z."
    )
    context = {"web_retrieval_receipts": [live_data_receipt("Porto", ok=False)]}
    verdict = inspect_unsourced_current_claim(
        answer=measured, requires_current=True, source_context=context
    )
    assert verdict.has_evidence is False
    assert verdict.asserts_measured_value is True
    assert verdict.attributes_source is True
    assert verdict.unsupported is True


def test_the_same_answer_survives_when_the_lookup_actually_succeeded():
    """The negative control for the conviction: a real observation still licenses a real answer."""
    measured = (
        "Vilnius: Clear, 17 C right now. Source: wttr.in, observed 2026-08-16T09:00:00Z."
    )
    context = {"web_retrieval_receipts": [live_data_receipt("Vilnius", ok=True)]}
    verdict = inspect_unsourced_current_claim(
        answer=measured, requires_current=True, source_context=context
    )
    assert verdict.has_evidence is True
    assert verdict.unsupported is False


def test_a_turn_that_needed_no_current_reading_is_untouched_by_any_of_this():
    """A static fact carries a measured value and needs no observation -- conjunct 1 protects it."""
    verdict = inspect_unsourced_current_claim(
        answer="Water freezes at 0 C at standard atmospheric pressure.",
        requires_current=False,
        source_context={"web_retrieval_receipts": [live_data_receipt("Porto", ok=False)]},
    )
    assert verdict.unsupported is False


# ------------------------------------------------ the other half: a lane that observes must say so


def test_the_live_info_fast_path_records_the_retrieval_it_performed():
    """A guard can only respect evidence it can see, so the lane that fetched has to record it.

    The live-info fast path really reaches the network and composes `Bitcoin: USD 63,791.00 ...
    Source: CoinGecko` -- and recorded that on no channel any evidence-sufficiency authority reads.
    Caught in the full gate by `tests/test_every_asset_asked_for_is_answered.py`, whose fully
    resolvable `BTC PRICE?` control had its real answer replaced with "I didn't run any live lookup
    on this turn": a lane calling its own real work a fabrication.

    Both directions are pinned. A retrieval that produced results makes the turn observed; one that
    produced none is recorded truthfully and is worth nothing, which is this file's whole subject.
    """
    from core.agent_runtime.fast_live_info_runtime_flow import record_live_info_observation

    observed: dict = {}
    assert record_live_info_observation(observed, [{"summary": "BTC 63,791 USD", "url": "https://x"}]) is True
    assert turn_ran_observations(observed) is True

    empty: dict = {}
    assert record_live_info_observation(empty, []) is True
    assert empty["runtime_tool_observations"], "a failed lookup must still be RECORDED"
    assert turn_ran_observations(empty) is False, "...and must still not count as an observation"


# ------------------------------------------------------------------------ distance from the domain


def test_the_boundary_is_not_weather_or_market_shaped():
    """The same rule, on a lane with no cities, no assets and no currentness vocabulary.

    A workspace file read is not live data and is nothing like the reported failure, but it writes
    the same `ok` field, so the invariant either lives in the boundary or it lives in the weather
    lane's vocabulary. Domain-distance check per the anti-overfit rule: if this needs its own branch,
    the repair was a Porto patch wearing a general name.
    """
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload

    class _Read:
        def __init__(self, good: bool) -> None:
            self.ok = good
            self.status = "executed" if good else "failed"
            self.response_text = "def main():" if good else "FileNotFoundError: no such file"
            self.details = {}
            self.mode = "tool"
            self.tool_name = "workspace.read_file"

    failed = tool_history_observation_payload(
        execution=_Read(False), tool_name="workspace.read_file", receipt=None
    )
    succeeded = tool_history_observation_payload(
        execution=_Read(True), tool_name="workspace.read_file", receipt=None
    )
    assert records_a_usable_observation(failed) is False
    assert records_a_usable_observation(succeeded) is True
    assert turn_ran_observations({"runtime_tool_observations": [failed]}) is False
    assert turn_ran_observations({"runtime_tool_observations": [succeeded]}) is True


def test_the_fx_lane_reports_its_failure_in_a_field_this_boundary_reads():
    """A third receipt shape, written by a lane with no `ok` and no `source_count` at all.

    `core.fresh_data.fx` records `failure_class` and nothing else this boundary understands. Pinned
    against the real dataclass rather than a hand-built dict, because a lane whose failure field the
    boundary cannot see goes back to counting attempts as evidence, silently.
    """
    from core.fresh_data.fx import FxRetrievalReceipt

    failed = FxRetrievalReceipt(
        retrieval_id="fx-1", base="EUR", quote="USD", status="unavailable",
        source_providers=("ecb",), source="", rate="", provider_attempt_count=1,
        started_at="", completed_at="", observed_at="", retrieved_at="",
        failure_class="fx_unavailable",
    ).to_dict()
    available = FxRetrievalReceipt(
        retrieval_id="fx-2", base="EUR", quote="USD", status="available",
        source_providers=("ecb",), source="ecb", rate="1.09", provider_attempt_count=1,
        started_at="", completed_at="", observed_at="", retrieved_at="",
        failure_class="",
    ).to_dict()

    assert "failure_class" in failed, "the fx receipt must keep the field this boundary reads"
    assert records_a_usable_observation(failed) is False
    assert records_a_usable_observation(available) is True
    assert turn_has_current_evidence(source_context={"fresh_data_retrieval_receipts": [failed]}) is False
    assert turn_has_current_evidence(source_context={"fresh_data_retrieval_receipts": [available]}) is True
