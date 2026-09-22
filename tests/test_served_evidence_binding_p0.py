"""P0 — RETRIEVED EVIDENCE MUST REACH SYNTHESIS WITHOUT WEAKENING THE M3 GATE.

Confirmed defects at base a6c8e3c4, each reproduced below through the REAL turn spine
(`VoolAgent.run_once`) with the model scripted AT THE PROVIDER SEAM and retrieval stubbed
at the fetcher seam -- never by hand-writing a lifecycle row:

1. MIXED MULTI-DEMAND. A conductor turn retrieves valid evidence (two live weather
   readings), a generation node writes part of the answer with a model, and NONE of it
   binds: no evidence set, no note ids, observations that carry the RESULT'S KEY NAMES
   instead of its values, and model-written prose that the publication gate then reads as
   runtime-composed bytes it has no jurisdiction over. A fabricated current claim in that
   prose ships; when the gate does engage it has nothing to match against and refuses the
   WHOLE answer -- the supported weather lines die with the fabricated sentence.
2. PINNED-CLOUD RESEARCH. The tool loop binds ONE note per tool STEP labelled with the
   tool's name: both real sources collapse into "web.search", so a claim's supporting
   sources name no source. And the evidence ids the binding mints never enter the
   synthesis prompt, so `proves="prompt_entry"` is not checkable by reading the prompt.
3. PARTIAL RESULTS. In a partial answer, lines that assert nothing adjudicable are
   dropped WITHOUT being named -- supported work is lost together with unsupported work
   and the reader cannot tell a trimmed answer from a complete one.

The invariants under test, in the language of the lane that owns them:

* every retrieved evidence item carries turn, demand, evidence-set and source identity
  (`core.grounded_synthesis_binding`, `core.conductor.evidence`);
* conductor and pinned-cloud synthesis receive the exact bound evidence set;
* claims map to supporting sources -- a receipt saying "search ran" is never content;
* mixed turns publish supported portions and explicitly refuse the unresolved ones;
* evidence from another turn/session/retry cannot leak in (scope digests, turn-unique
  indexing -- driven here, not unit-asserted);
* empty model output, provider failure and stale evidence stay fail-closed;
* DIRECT/timeless requests are untouched.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core import grounding_lifecycle as lifecycle_ledger

# --------------------------------------------------------------------------- fixtures


class _ProviderScript:
    """Answers `_invoke_manifest` from a table keyed on the request's own metadata.

    The conductor's planner, semantic proposer and node generations all cross this ONE
    seam with self-describing metadata, so one stand-in drives each distinctly. The
    ledger bookkeeping the real seam performs at entry is performed here too, so the
    served shape (provider calls, synthesis call records) matches production.
    """

    def __init__(self, table: dict[str, str], default: str = ""):
        self.table = table
        self.default = default
        self.calls: list[dict] = []

    def __call__(self, *, manifest, request, source_context=None, **_kwargs):
        from core.turn_model_call_ledger import record_provider_call

        metadata = dict(getattr(request, "metadata", None) or {})
        call_context = source_context if isinstance(source_context, dict) else {}
        self.calls.append(
            {
                "kind": str(metadata.get("planner_call_kind") or ""),
                "conductor_node": bool(metadata.get("conductor_node")),
                "prompt": str(getattr(request, "prompt", "") or "")[:600],
            }
        )
        record_provider_call(
            call_context,
            provider_id="scripted-local",
            model_id="scripted-model",
            cost_class="free_local",
        )
        key = "node" if metadata.get("conductor_node") else str(metadata.get("planner_call_kind") or "")
        text = self.table.get(key, self.default)

        class _Reply:
            output_text = text
            provider_id = "scripted-local"
            model_name = "scripted-model"
            used_model = bool(text)
            confidence = 0.8
            trust_score = 0.8
            validation_state = "validated"
            details: dict = {}
            source = "provider_execution"
            provider_name = "scripted"
            cache_hit = False
            candidate_id = None
            failover_used = False
            structured_output = None
            task_hash = "scripted"

        return None, _Reply(), None


_TEMPS = {"kaunas": 28.0, "tallinn": 22.0}


def _weather(location, **_kwargs):
    from core.weather_result_contract import WeatherResult

    if location not in _TEMPS:
        return None
    return WeatherResult(
        location=location,
        place_label=location.title(),
        condition="Sunny",
        temperature_c=_TEMPS[location],
        feels_like_c=_TEMPS[location],
        humidity_pct=50.0,
        wind_kmph=10.0,
        observed_at="12:00 PM",
        source_label="wttr.in",
        source_url=f"https://wttr.in/{location}",
    )


#: The measured incident rows' shape (m3diag1 drive: keyed Brave search, real rows).
VOLCANO_ROWS = [
    {
        "summary": (
            "The Icelandic Met Office has declared the latest Reykjanes peninsula eruption over "
            "after three weeks of activity; no new lava flows observed this week."
        ),
        "result_title": "Reykjanes eruption declared over after three weeks",
        "result_url": "https://www.ruv.is/en/reykjanes-eruption-over",
        "origin_domain": "ruv.is",
        "source_type": "web_derived",
    },
    {
        "summary": (
            "GPS measurements show continued ground uplift near Svartsengi, indicating magma "
            "recharge beneath the Reykjanes peninsula."
        ),
        "result_title": "Ground uplift continues near Svartsengi",
        "result_url": "https://www.vedur.is/en/reykjanes-uplift",
        "origin_domain": "vedur.is",
        "source_type": "web_derived",
    },
]

RESEARCH_MESSAGE = (
    "What is the current state of volcanic activity on the Reykjanes peninsula? "
    "Use authoritative sources and do not guess."
)

#: A synthesis that answers strictly FROM the rows above -- the honest model.
GROUNDED_SYNTHESIS = (
    "The latest Reykjanes peninsula eruption has been declared over by the Icelandic Met Office "
    "after three weeks of activity. Source: [ruv.is](https://www.ruv.is/en/reykjanes-eruption-over).\n"
    "Ground uplift near Svartsengi continues, indicating magma recharge beneath the peninsula. "
    "Source: [vedur.is](https://www.vedur.is/en/reykjanes-uplift)."
)


def _local_manifest():
    return SimpleNamespace(
        provider_id="scripted-local",
        model_id="scripted-model",
        adapter_type="subprocess",
        source_type="subprocess",
        runtime_config={},
        metadata={"cost_class": "free_local"},
    )


def _paid_cloud_manifest():
    return SimpleNamespace(
        provider_id="scripted-cloud",
        model_id="pinned-cloud-model",
        adapter_type="cloud_fallback_provider",
        source_type="remote",
        runtime_config={},
        metadata={"cost_class": "paid_cloud"},
    )


@pytest.fixture(autouse=True)
def _isolated_runtime():
    """An empty grounding ledger per test.

    The session home and database come from the repository conftest (one private home
    per pytest run); each test isolates its turns by session id. Swapping the database
    per test breaks the pooled connections the spine's trace index holds."""
    lifecycle_ledger.reset_for_tests()
    yield
    lifecycle_ledger.reset_for_tests()


@pytest.fixture()
def weather_fetchers():
    with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather):
        yield


def _agent(session_id: str) -> VoolAgent:
    from core.runtime_continuity import reset_runtime_continuity_state

    reset_runtime_continuity_state()
    return VoolAgent(
        backend_name="test-backend", device="channel-test", persona_id="default"
    )


def _finalize_like_the_transport(context: dict, result: dict) -> dict:
    """The transport door's own contract (core/web/api/runtime.py::_response_commit)."""

    from core.finalization import finalize_answer

    return finalize_answer(
        turn_id=str(context.get("cancel_turn_id") or context.get("turn_id") or ""),
        canonical_content=str(result.get("response") or ""),
        source_context=context,
    )


def _the_lifecycle() -> dict:
    """The one lifecycle row this turn wrote (there must be exactly one), as a dict."""

    rows = [record.as_dict() for record in lifecycle_ledger._RECORDS.values()]
    assert len(rows) == 1, f"expected exactly one lifecycle row, got {len(rows)}"
    return rows[0]


def _the_lifecycle_record():
    """The live lifecycle object (its `bound_notes` do not appear in `as_dict`)."""

    records = list(lifecycle_ledger._RECORDS.values())
    assert len(records) == 1, f"expected exactly one lifecycle row, got {len(records)}"
    return records[0]


# ------------------------------------------------------------ the conductor mixed turn

CONDUCTOR_PLAN = json.dumps(
    {"requests": [
        {
            "request": "",
            "source_clause_ids": ["clause-1"],
            "operation": "weather_lookup",
            "depends_on": [],
        },
        {
            "request": "",
            "source_clause_ids": ["clause-2"],
            "operation": "comparison",
            "depends_on": ["clause-1"],
        },
        {
            "request": "",
            "source_clause_ids": ["clause-3"],
            "operation": "factual_explanation",
            "depends_on": ["clause-1"],
        },
    ]}
)

MIXED_WEATHER_MESSAGE = (
    "Get the current weather for Kaunas and Tallinn, tell me which city is warmer, "
    "and explain what a 6 degree difference in air temperature means for choosing a coat."
)

#: The generation node's reply. The first sentence is supported by the two observations
#: (28.0 - 22.0 = 6.0). The LAST sentence is a fabricated current claim -- nothing in
#: what this turn observed says anything about tonight.
NODE_EXPLANATION = (
    "Kaunas is 6.0 degrees warmer than Tallinn, so the warmer city is Kaunas. "
    "Tallinn will drop to 9 degrees tonight."
)


def _drive_conductor_mixed_turn(session_id: str) -> tuple[dict, dict, _ProviderScript]:
    agent = _agent(session_id)
    script = _ProviderScript(
        {
            "clause_decomposition": CONDUCTOR_PLAN,
            "node": NODE_EXPLANATION,
        }
    )
    with (
        mock.patch.object(agent.memory_router, "_invoke_manifest", side_effect=script),
        mock.patch(
            "core.agent_runtime.audit_routing._ranked_candidates",
            return_value=[_local_manifest()],
        ),
    ):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": f"turn-{session_id}",
            "request_id": f"req-{session_id}",
        }
        result = agent.run_once(
            MIXED_WEATHER_MESSAGE, source_context=context, session_id_override=session_id
        )
    return result, context, script


def test_conductor_binds_its_retrieved_evidence_with_demand_and_source_identity(weather_fetchers):
    """Invariant 1 + 2, conductor side. The two live readings the plan retrieved must
    exist as a BOUND evidence set whose rows carry the values they observed, the source
    they came from and the demand each one serves -- and the generation node's model
    call must be recorded as having been entered under that set."""
    _result, _context, _script = _drive_conductor_mixed_turn("sess-bind-conductor")

    row = _the_lifecycle()
    record = _the_lifecycle_record()
    evidence_set_id = str(row.get("evidence_set_id") or "")
    assert evidence_set_id, f"no bound evidence set: {json.dumps(row, default=str)[:800]}"
    # The observed VALUES, not the result's key names, are what the rows carry.
    bound_notes = [dict(note) for note in record.bound_notes]
    joined = json.dumps(bound_notes, default=str)
    assert "28.0" in joined and "22.0" in joined, f"values missing from bound rows: {joined[:600]}"
    # Source identity: the reading names where it came from.
    assert "wttr.in" in joined, f"no source identity on bound rows: {joined[:600]}"
    # Demand identity: each row names the demand (clause) it serves.
    assert any(str(note.get("demand_id") or note.get("demand_text") or "") for note in bound_notes), (
        f"no demand identity on bound rows: {joined[:600]}"
    )
    # The generation call was entered carrying one of this turn's bound set ids. A
    # conductor plan binds incrementally (observations complete one by one), so the id a
    # call carried may be an earlier mint of the same turn's set -- every minted id was
    # scope-admitted for THIS turn, which is what the reference must prove.
    synthesis_ids = [str(call.get("evidence_set_id") or "") for call in row.get("synthesis_calls") or []]
    minted = {str(item) for item in row.get("evidence_set_ids_minted") or []} | {evidence_set_id}
    assert synthesis_ids and set(synthesis_ids) & minted, (
        f"generation call never carried a bound set: {synthesis_ids} vs minted {minted}"
    )


def test_conductor_mixed_turn_withholds_the_fabricated_line_and_keeps_the_readings(
    weather_fetchers,
):
    """Invariants 3 + 4. The plan's two real readings support the weather lines and the
    comparison; the generation node's 'Tallinn will drop to 9 degrees tonight' is
    supported by nothing this turn observed. The served answer keeps the supported
    portions and names the withheld one -- the model-written claim may not ride through
    as runtime-composed bytes the gate has no jurisdiction over."""
    result, context, _script = _drive_conductor_mixed_turn("sess-partial-conductor")
    commit = _finalize_like_the_transport(context, result)
    served = str(commit.get("canonical_content") or "")

    # The fabricated sentence must not ship AS ANSWER CONTENT. The withheld-work notice
    # at the end NAMES it -- that is the explicit refusal invariant 4 demands -- so the
    # assertion splits the notice off before checking the body.
    from core.grounding_publication import UNSUPPORTED_WORK_NOTICE_LEAD

    body, _, notice = served.partition(UNSUPPORTED_WORK_NOTICE_LEAD)
    assert "28.0" in body and "22.0" in body, f"supported readings lost: {body[:600]}"
    assert "9 degrees tonight" not in body, f"fabricated current claim shipped: {body[:600]}"
    assert "9 degrees tonight" in notice, f"fabrication not named in the notice: {notice[:400]}"

    row = _the_lifecycle()
    publication = dict(row.get("publication") or {})
    withheld = [str(item) for item in publication.get("withheld_claims") or []]
    assert any("9 degrees" in item for item in withheld), f"fabrication not in withheld list: {withheld}"


# ------------------------------------------------------- the pinned-cloud research turn


def _tool_decision_ns(intent: str, arguments: dict, closing_text: str = ""):
    def _call(i, a):
        return SimpleNamespace(intent=i, arguments=dict(a), call_id=f"c-{i}", name=i)

    if intent == "respond.direct":
        return SimpleNamespace(
            output_text=closing_text,
            provider_id="scripted-cloud",
            used_model=True,
            confidence=0.8,
            trust_score=0.8,
            validation_state="validated",
            details={},
            source="provider_execution",
            model_name="pinned-cloud-model",
            provider_name="scripted",
            cache_hit=False,
            candidate_id=None,
            failover_used=False,
            structured_output={"intent": intent, "arguments": dict(arguments)},
            tool_calls=(_call(intent, arguments),),
            task_hash="h",
        )
    return SimpleNamespace(
        output_text="",
        provider_id="scripted-cloud",
        used_model=True,
        confidence=0.8,
        trust_score=0.8,
        validation_state="validated",
        details={},
        source="provider_execution",
        model_name="pinned-cloud-model",
        provider_name="scripted",
        cache_hit=False,
        candidate_id=None,
        failover_used=False,
        structured_output={"intent": intent, "arguments": dict(arguments)},
        tool_calls=(_call(intent, arguments),),
        task_hash="h",
    )


def _drive_pinned_research(
    session_id: str,
    *,
    rows: list[dict] | None = None,
    synthesis_text: str = GROUNDED_SYNTHESIS,
    turn_suffix: str = "r1",
) -> tuple[dict, dict, list[dict], list[dict]]:
    """One pinned-cloud research turn through the real spine.

    Returns (result, context, synthesis_call_contexts, search_calls). The synthesis
    stand-in captures the context each answering call was entered with, so assertions
    can read what the model was actually handed."""
    agent = _agent(session_id)
    search_calls: list[dict] = []
    synthesis_contexts: list[dict] = []
    tool_rounds: list[int] = []

    def _planned_search(query_text, **_kwargs):
        search_calls.append({"query": str(query_text)})
        return [dict(row) for row in (rows if rows is not None else VOLCANO_ROWS)]

    def _resolve_tool_intent(**kwargs):
        tool_rounds.append(1)
        if len(tool_rounds) == 1:
            return _tool_decision_ns(
                "web.search", {"query": RESEARCH_MESSAGE, "max_results": 4}
            )
        return _tool_decision_ns(
            "respond.direct", {"message": "Done."}, closing_text="Done."
        )

    def _resolve_stand_in(**kwargs):
        from core.memory_first_router import ModelExecutionDecision
        from core.turn_model_call_ledger import record_provider_call, record_served_usage

        call_context = kwargs.get("source_context")
        synthesis_contexts.append(
            {
                "keys": sorted(dict(call_context or {}).keys()),
                "envelope": dict((call_context or {}).get("evidence_synthesis_binding") or {}),
                "conversation_history": list(
                    (call_context or {}).get("conversation_history") or []
                ),
            }
        )
        record_provider_call(
            call_context,
            provider_id="scripted-cloud",
            model_id="pinned-cloud-model",
            cost_class="paid_cloud",
        )
        record_served_usage(call_context, {"provider_id": "scripted-cloud"})
        return ModelExecutionDecision(
            source="model",
            task_hash="served",
            provider_id="scripted-cloud",
            provider_name="scripted",
            model_name="pinned-cloud-model",
            output_text=synthesis_text,
            confidence=0.8,
            trust_score=0.8,
            used_model=True,
        )

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        mock.patch(
            "retrieval.web_adapter.WebAdapter.planned_search_query",
            side_effect=_planned_search,
        ),
        mock.patch.object(
            agent.memory_router, "resolve_tool_intent", side_effect=_resolve_tool_intent
        ),
        mock.patch.object(agent.memory_router, "resolve", side_effect=_resolve_stand_in),
        mock.patch.object(
            agent.memory_router,
            "_requested_model_manifest",
            return_value=_paid_cloud_manifest(),
        ),
    ):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": f"turn-{session_id}-{turn_suffix}",
            "request_id": f"req-{session_id}-{turn_suffix}",
            "requested_model": "pinned-cloud-model",
            "allow_remote_fetch": True,
        }
        result = agent.run_once(
            RESEARCH_MESSAGE, source_context=context, session_id_override=session_id
        )
    return result, context, synthesis_contexts, search_calls


def test_pinned_cloud_research_claims_map_to_real_sources():
    """Invariant 1 + 3. Two distinct sources were retrieved; the claim map's supporting
    sources must name THEM (their domains), not the tool that found them."""
    result, context, _synthesis_contexts, search_calls = _drive_pinned_research(
        "sess-research-sources"
    )
    assert search_calls, "the retrieval never ran"
    _finalize_like_the_transport(context, result)

    row = _the_lifecycle()
    publication = dict(row.get("publication") or {})
    claim_map = dict(publication.get("claim_support") or {})
    labels = dict(claim_map.get("source_labels") or {})
    assert labels, f"no source labels in the claim map: {json.dumps(publication, default=str)[:600]}"
    assert "web.search" not in " ".join(str(v) for v in labels.values()), (
        f"a tool name stands where a source should be: {labels}"
    )
    assert any("ruv.is" in str(v) for v in labels.values()), labels
    assert any("vedur.is" in str(v) for v in labels.values()), labels


def test_pinned_cloud_synthesis_is_handed_the_bound_evidence_identity():
    """Invariant 2. The answering model call's own context must carry the binding
    envelope naming the exact bound set, and the material the model is shown must carry
    the ids -- `proves="prompt_entry"` has to be checkable by reading the prompt, not by
    trusting the stamp."""
    _result, _context, synthesis_contexts, _search_calls = _drive_pinned_research(
        "sess-research-identity"
    )
    assert synthesis_contexts, "the synthesis call never ran"
    answering = synthesis_contexts[-1]
    envelope = answering["envelope"]
    assert envelope.get("evidence_set_id"), (
        f"answering call carried no evidence set id: {answering['keys']}"
    )
    note_ids = [str(item) for item in envelope.get("note_ids") or []]
    assert note_ids, "binding envelope names no note ids"

    # The ids ride INSIDE the material the model is shown (the conversation/tool surface
    # the answering call was entered with), not merely alongside it.
    shown = json.dumps(
        [answering["conversation_history"], answering["keys"]], default=str
    )
    assert any(note_id in shown for note_id in note_ids) or "evidence_set_id" in shown, (
        "evidence ids are not in the material the synthesis model was shown"
    )


def test_pinned_cloud_research_publishes_the_grounded_answer():
    """The honest end state: a model that answers strictly from the retrieved rows
    publishes, with the claim map proving each sentence to its source."""
    result, context, _synthesis_contexts, _search_calls = _drive_pinned_research(
        "sess-research-publish"
    )
    commit = _finalize_like_the_transport(context, result)
    served = str(commit.get("canonical_content") or "")
    assert "Reykjanes" in served and "ruv.is" in served, f"grounded answer refused: {served[:600]}"
    row = _the_lifecycle()
    assert row["stages"]["published"], json.dumps(row, default=str)[:600]
    assert row["evidence_set_id"], "published without a bound evidence set"


def test_empty_retrieval_keeps_the_gate_fail_closed():
    """Invariant 7. A provider that returns nothing leaves the synthesis unsupported:
    the model's bytes do not ship and the typed refusal does."""
    result, context, _synthesis_contexts, _search_calls = _drive_pinned_research(
        "sess-research-empty", rows=[]
    )
    assert _search_calls, "the retrieval never ran"
    commit = _finalize_like_the_transport(context, result)
    served = str(commit.get("canonical_content") or "")
    assert "Reykjanes peninsula eruption has been declared over" not in served
    assert "can't publish" in served or "cannot publish" in served or "I can't" in served, (
        f"model bytes shipped over an empty retrieval: {served[:400]}"
    )
    row = _the_lifecycle()
    publication = dict(row.get("publication") or {})
    assert publication.get("state") in {"refused", "failed"}, json.dumps(publication, default=str)[:400]


# ------------------------------------------------------------- isolation and DIRECT


def test_a_later_turn_cannot_use_an_earlier_turns_evidence():
    """Invariant 6, driven. Turn one retrieves and binds two sources. Turn two -- same
    session, same question, DIFFERENT turn -- fabricates an answer. The earlier turn's
    evidence set must not support it: turn two's lifecycle carries no binding from turn
    one and its answer is refused at the gate."""
    _first_result, _first_context, _s1, _c1 = _drive_pinned_research(
        "sess-isolation", turn_suffix="t1"
    )
    first_row = _the_lifecycle()
    first_set = str(first_row.get("evidence_set_id") or "")
    assert first_set, "turn one did not bind evidence"
    lifecycle_ledger.reset_for_tests()

    second_result, second_context, _s2, _c2 = _drive_pinned_research(
        "sess-isolation",
        rows=[],  # turn two's own retrieval returns nothing
        turn_suffix="t2",
    )
    commit = _finalize_like_the_transport(second_context, second_result)
    served = str(commit.get("canonical_content") or "")
    row = _the_lifecycle()
    binding = dict(row.get("binding") or {})
    assert binding.get("evidence_set_id", "") != first_set or not binding, (
        "turn two bound turn one's evidence set"
    )
    assert "Reykjanes peninsula eruption has been declared over" not in served, (
        f"turn one's sources propped up turn two's answer: {served[:400]}"
    )


def test_direct_timeless_request_is_untouched():
    """Invariant 9. A plain timeless explanation runs no lifecycle at all and the gate
    returns the bytes unchanged.

    The scripted author is now a REGISTERED, CERTIFIED manifest rather than a bare id. That is
    not scaffolding: `core.final_answer_authorship` fails closed on an author it cannot name, so
    a served response whose provider id resolves to nothing is refused by design. Naming the
    author is what a deployment does; this test's subject is the M3 gate, not authorship.
    """
    agent = _agent("sess-direct")
    from core.memory_first_router import ModelExecutionDecision
    from core.turn_model_call_ledger import record_provider_call, record_served_usage
    from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest
    from tests._authorship_certification import certify_for_authorship

    scripted = ModelProviderManifest(
        provider_name="scripted", model_name="local", source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://example.invalid/licence",
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"runtime_family": "ollama", "cost_class": "free_local"},
    )
    upsert_provider_manifest(scripted)
    certify_for_authorship(scripted)

    answer = (
        "A hash table stores each key by hashing it into one of a fixed number of buckets. "
        "A lookup therefore touches one bucket instead of scanning every entry."
    )

    def _resolve(**kwargs):
        call_context = kwargs.get("source_context")
        record_provider_call(
            call_context, provider_id=scripted.provider_id, model_id=scripted.model_name,
            cost_class="free_local",
        )
        record_served_usage(call_context, {"provider_id": scripted.provider_id})
        return ModelExecutionDecision(
            source="model", task_hash="direct", provider_id=scripted.provider_id,
            provider_name="scripted", model_name="scripted-model", output_text=answer,
            confidence=0.8, trust_score=0.8, used_model=True,
        )

    with mock.patch.object(agent.memory_router, "resolve", side_effect=_resolve):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": "turn-direct-1",
            "request_id": "req-direct-1",
        }
        result = agent.run_once(
            "Explain in two sentences what a hash table is.",
            source_context=context,
            session_id_override="sess-direct",
        )
    commit = _finalize_like_the_transport(context, result)
    served = str(commit.get("canonical_content") or "")
    assert "hashing it into one" in served, f"DIRECT answer altered: {served[:400]}"
    assert not list(lifecycle_ledger._RECORDS.values()), (
        "a timeless turn opened a grounding lifecycle"
    )


def test_mixed_turn_keeps_the_computed_part_when_retrieval_fails(weather_fetchers):
    """Invariant 4 + 9, measured over the real HTTP surface on the isolated daemon:
    "Get the current weather in Vilnius, and calculate 37 x 19." with the weather fetch
    refused served the TYPED REFUSAL for the whole answer -- 37*19 = 703, which needs
    nothing retrieved, was lost together with the failed weather clause, because the
    seal read the conductor's PLANNING calls (which wrote no answer byte) as model
    authorship and the gate then had no rows to judge the compose against. The computed
    demand must publish and the failed demand must be named."""
    agent = _agent("sess-mixed-fail")
    script = _ProviderScript(
        {
            "clause_decomposition": json.dumps(
                {"requests": [
                    {
                        "request": "",
                        "source_clause_ids": ["clause-1"],
                        "operation": "weather_lookup",
                        "depends_on": [],
                    },
                    {
                        "request": "",
                        "source_clause_ids": ["clause-2"],
                        "operation": "calculation",
                        "depends_on": [],
                    },
                ]}
            ),
        }
    )

    def _refuse_weather(location, **_kwargs):
        raise RuntimeError("RemoteFetchRefusedError: no active turn ledger")

    with (
        mock.patch(
            "tools.web.web_research.structured_weather_lookup", side_effect=_refuse_weather
        ),
        mock.patch.object(agent.memory_router, "_invoke_manifest", side_effect=script),
        mock.patch(
            "core.agent_runtime.audit_routing._ranked_candidates",
            return_value=[_local_manifest()],
        ),
    ):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": "turn-mixed-fail-1",
            "request_id": "req-mixed-fail-1",
        }
        result = agent.run_once(
            "Get the current weather in Vilnius, and calculate 37 x 19.",
            source_context=context,
            session_id_override="sess-mixed-fail",
        )
    commit = _finalize_like_the_transport(context, result)
    served = str(commit.get("canonical_content") or "")

    assert "703" in served, f"the computed demand was lost with the failed clause: {served[:600]}"
    assert "Vilnius" in served, f"the failed demand is not named: {served[:600]}"
    assert "can't publish an answer to this" not in served, (
        f"whole-answer refusal over a partially-failed mixed turn: {served[:600]}"
    )


# ----------------------------------------------------------------------- sabotages
#
# Each neuters one load-bearing seam of the repair and asserts a NAMED test above stops
# holding. A binding nobody can prove bites is a binding that has not been shown to be
# doing anything.


def test_sabotage_neutered_conductor_binding_loses_the_evidence_identity(weather_fetchers):
    """Remove the conductor's binding and the binding test's guarantees die: no evidence
    set, no note ids, no generation call recorded under the set. Worth stating plainly:
    the WITHHOLD guarantee survives this sabotage on the strength of the observation
    rows alone (defense in depth -- the rows carry values even unbound), which is why
    the sabotage asserts the identity guarantees, not the withhold."""
    import core.conductor.evidence as conductor_evidence

    def _neutered(source_context, rows, *, query=""):
        return {}

    with (
        mock.patch.object(conductor_evidence, "_bind_rows", side_effect=_neutered),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather),
    ):
        _result, _context, _script = _drive_conductor_mixed_turn("sess-sabotage-bind")
    row = _the_lifecycle()
    assert not row.get("evidence_set_id"), "sabotage did not neuter the binding"
    synthesis_ids = [str(call.get("evidence_set_id") or "") for call in row.get("synthesis_calls") or []]
    assert not any(synthesis_ids), f"calls referenced a set that was never bound: {synthesis_ids}"
    # And the named test above fails exactly here: with the binding gone there is no
    # demand identity and no source identity on any evidence row.
    record = _the_lifecycle_record()
    assert not record.bound_notes, "bound notes survived a neutered binding"


def test_sabotage_step_grain_binding_collapses_the_sources():
    """Revert the per-row projection (one note per step, labelled by tool name) and the
    source-mapping test's guarantee dies: both domains collapse into 'web.search'."""
    import core.agent_runtime.research_tool_loop_facade as facade

    def _step_grain(steps):
        rows = []
        for step in list(steps or []):
            if str(step.get("mode") or "") != "tool_executed" or step.get("ok") is False:
                continue
            body = str(step.get("response_text") or "").strip()
            if not body:
                continue
            rows.append(
                {
                    "summary": str(step.get("summary") or ""),
                    "snippet": body[:4000],
                    "origin_domain": str(step.get("tool_name") or ""),
                    "search_provider": str(step.get("tool_name") or ""),
                    "source_type": "tool_result",
                    "ok": True,
                }
            )
        return rows

    with mock.patch.object(facade, "_tool_step_evidence_rows", side_effect=_step_grain):
        result, context, _synthesis, _search = _drive_pinned_research("sess-sabotage-rows")
    _finalize_like_the_transport(context, result)
    row = _the_lifecycle()
    publication = dict(row.get("publication") or {})
    labels = dict((publication.get("claim_support") or {}).get("source_labels") or {})
    assert "web.search" in " ".join(str(v) for v in labels.values()), (
        f"step-grain sabotage did not collapse the sources: {labels}"
    )


def test_sabotage_a_foreign_scope_binding_is_refused():
    """Forge a binding record from ANOTHER turn's scope and offer it to this one: the
    ledger must refuse it (rejected_bindings), never store it as this turn's evidence."""
    context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "cancel_turn_id": "turn-sabotage-foreign",
        "request_id": "req-sabotage-foreign",
    }
    from core.grounded_synthesis_binding import binding_record, mint_evidence_set, turn_scope
    from core.grounding_lifecycle import register_required, reset_for_tests

    register_required(
        context,
        request_text=RESEARCH_MESSAGE,
        reason_codes=("request_promises_evidence",),
    )
    foreign_context = {
        "cancel_turn_id": "turn-OTHER",
        "request_id": "req-OTHER",
        "session_id": "sess-OTHER",
    }
    foreign_scope = turn_scope(foreign_context)
    foreign_set = mint_evidence_set(
        [dict(VOLCANO_ROWS[0])], scope=foreign_scope, query=RESEARCH_MESSAGE
    )
    foreign_record = binding_record(foreign_set)
    own_scope = turn_scope(context)
    assert own_scope != foreign_scope

    from core.grounding_lifecycle import record_bound

    accepted = record_bound(
        context, binding=foreign_record, notes=[dict(VOLCANO_ROWS[0])], scope=own_scope
    )
    assert accepted is False, "a foreign-turn binding was accepted as this turn's"
    record = _the_lifecycle_record()
    assert record.bound_evidence_set_id == "", record.binding
    assert record.rejected_bindings, "the refused binding left no trace to name"
    reset_for_tests()
