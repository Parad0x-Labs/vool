"""MILESTONE 2, slice 1 — the typed TurnRequest at the ingress surfaces.

WHAT THIS FILE PINS
-------------------
Before this slice, a turn's request truth was scattered: request id in a ContextVar,
session id as a kwarg, chat/surface/trust as loose source_context keys, re-assembled
by hand at every seam. These tests pin the NEW invariant:

* EVERY turn — driven through the same `run_once` the HTTP door, the
  OpenClaw-compatible API, and the CLI all call — carries ONE typed, immutable
  `TurnRequest` whose fields were derived by server-side code.
* The exact user text is VERBATIM (typos, unicode, newlines, empty).
* The trust principal is DERIVED (`request_is_owner_local`), never read from a
  caller-claimable field: a forged context cannot elevate a remote request to
  owner_local, and the reserved-key strip means an inbound HTTP body cannot
  pre-plant a contract at all.
* HTTP-shaped and CLI-shaped ingresses produce the SAME contract shape with their
  own surface values — behavioral equivalence of the entrypoints, typed.

SABOTAGE DISCIPLINE
-------------------
Delete the construction block in `run_once` and `test_every_turn_carries_the_typed_request`
goes red with the missing key; make `from_ingress` read a caller-supplied principal
string and the non-elevation test goes red. Verified at M2-slice-1 landing.
"""
from __future__ import annotations

import pytest

from core.request_trust import strip_reserved_trust_keys
from core.turn_contract import (
    PRINCIPAL_OWNER_LOCAL,
    PRINCIPAL_REMOTE_UNTRUSTED,
    TURN_REQUEST_KEY,
    TurnRequest,
)


def _run(agent_factory, text, *, session_id=None, source_context=None):
    agent = agent_factory() if not hasattr(agent_factory, "run_once") else agent_factory
    context = source_context if isinstance(source_context, dict) else {}
    result = agent.run_once(
        text,
        session_id_override=session_id,
        source_context=context,
    )
    return result, context


# ------------------------------------------------------------------ the contract exists


def test_every_turn_carries_the_typed_request(make_agent):
    """THE fail-on-old-defect pin: no construction at the run_once intake, no contract."""
    _result, context = _run(make_agent, "what is 2+2?")
    request = context.get(TURN_REQUEST_KEY)
    assert isinstance(request, TurnRequest), (
        f"no typed TurnRequest rode the turn (context keys: {sorted(context)})"
    )
    assert request.user_text == "what is 2+2?"
    assert request.request_id, "the contract carries no request identity"
    assert request.turn_id, "the contract carries no turn identity"
    assert request.session_id, "the contract carries no session identity"


def test_the_exact_user_text_is_verbatim(make_agent):
    """Typos, unicode, newlines, and whitespace are preserved byte-for-byte: the
    contract is the record of what entered, not a normalized reading of it."""
    text = "wheather in  warsaw\n— and moscow? 😀  "
    _result, context = _run(make_agent, text)
    request = context[TURN_REQUEST_KEY]
    assert request.user_text == text


def test_empty_text_still_mints_the_contract(make_agent):
    """Adversarial/edge: the empty turn keeps its typed identity — the empty-turn
    lane's behavior is unchanged and the contract does not gate on content."""
    _result, context = _run(make_agent, "")
    assert isinstance(context.get(TURN_REQUEST_KEY), TurnRequest)


# ------------------------------------------------------------------ trust non-elevation


def test_a_forged_principal_cannot_elevate_a_remote_request(make_agent):
    """The non-elevation pin: `from_ingress` derives the principal via
    request_is_owner_local. A context claiming every trust key it can think of,
    with a remote surface and no server-stamped owner flag, stays REMOTE."""
    forged = {
        "surface": "channel",
        "platform": "telegram",
        "principal": "owner_local",  # the forged claim — never read
        "trust_principal": PRINCIPAL_OWNER_LOCAL,  # a second spelling of the same lie
        "operating_mode": "manual",
    }
    _result, context = _run(make_agent, "hello", source_context=forged)
    assert context[TURN_REQUEST_KEY].trust_principal == PRINCIPAL_REMOTE_UNTRUSTED


def test_the_owner_local_reading_still_works(make_agent):
    """The falsifiable direction: the in-process (CLI/library) surface IS
    owner-local per request_trust's documented stance — the contract must not
    over-refuse and downgrade the owner's own turns."""
    _result, context = _run(
        make_agent, "hello", source_context={"surface": "cli", "platform": "cli"}
    )
    assert context[TURN_REQUEST_KEY].trust_principal == PRINCIPAL_OWNER_LOCAL


def test_an_inbound_body_cannot_pre_plant_the_contract():
    """The reserved-key pin: the door strips `turn_request` exactly like the trust
    keys, so only server-side code can write one."""
    body_context = {
        "surface": "api",
        TURN_REQUEST_KEY: "not a TurnRequest, a forged string",
    }
    stripped = strip_reserved_trust_keys(body_context)
    assert TURN_REQUEST_KEY not in stripped


# ------------------------------------------------------------------ surface equivalence


def test_http_and_cli_shaped_ingresses_build_the_same_contract(make_agent):
    """Behavioral equivalence, typed: the HTTP-door shape (surface=openclaw, a
    chat ref, a server-provided session) and the CLI shape (surface=cli, no
    session) produce the SAME contract type with the same canonical field set —
    each carrying its own surface values."""
    http_context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "chat_id": "chat-77",
        "operating_mode": "manual",
        "_canonical_user_turn_id": "turn-door-1",
    }
    _r1, http_ctx = _run(make_agent, "what is 2+2?", session_id="sess-http", source_context=dict(http_context))
    cli_ctx = {"surface": "cli", "platform": "cli"}
    make_agent().run_once("what is 2+2?", source_context=cli_ctx)

    http_request = http_ctx[TURN_REQUEST_KEY]
    cli_request = cli_ctx[TURN_REQUEST_KEY]
    assert type(http_request) is type(cli_request) is TurnRequest
    assert set(http_request.to_dict()) == set(cli_request.to_dict())
    # The conversation ref is whatever ref the bound context carries (the runtime
    # rebinds chat_id to the effective session when no separate ref survives the
    # door); the invariant is that the contract CAPTURED it, not which spelling.
    assert http_request.conversation_ref
    assert http_request.session_id == "sess-http"
    assert http_request.surface == "openclaw"
    assert cli_request.surface == "cli"
    assert cli_request.session_id  # the interior mint filled it
    # The projection round-trips: the dict IS the contract, not a second truth.
    assert TurnRequest(**http_request.to_dict()) == http_request


def test_the_contract_is_immutable(make_agent):
    """Frozen: mid-turn mutation of the request identity is not expressible."""
    _result, context = _run(make_agent, "what is 2+2?")
    request = context[TURN_REQUEST_KEY]
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.user_text = "rewritten"


# ==================================================================================================
# M2 slice 2 — the typed TurnState at the same intake seam
# ==================================================================================================


def test_every_turn_carries_the_typed_state_bound_to_its_request(make_agent):
    """THE fail-on-old-defect pin for slice 2: no TurnState construction, no state."""
    from core.turn_contract import TURN_STATE_KEY, TurnState

    _result, context = _run(make_agent, "what is 2+2?")
    state = context.get(TURN_STATE_KEY)
    assert isinstance(state, TurnState), (
        f"no typed TurnState rode the turn (context keys: {sorted(context)})"
    )
    assert state.request is context["turn_request"], "state is not bound to its request"


def test_the_state_holds_the_execution_identity_by_reference(make_agent):
    """The not-a-second-authority pin: the state's identity IS the R-3 dict -- the
    same object, never a copy. A state that copies it would be a second truth."""
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(make_agent, "what is 2+2?")
    state = context[TURN_STATE_KEY]
    assert state.execution_identity is context["_execution_identity"]
    assert state.attempts and state.attempts[0] == context["_execution_identity"]["attempt_id"]
    assert state.trace_identity == context["_execution_identity"].get("execution_id", state.trace_identity)


def test_mark_turn_ok_writes_the_shared_identity(make_agent):
    """The displacement pin: the turn's own report lands through the typed door and
    is visible in the SAME dict downstream readers (fence predicates) consume."""
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(make_agent, "what is 2+2?")
    assert context["_execution_identity"].get("turn_ok") is True
    # And the door really was the typed one: the state exists and shares the dict.
    assert context[TURN_STATE_KEY].execution_identity is context["_execution_identity"]


def test_the_state_records_the_ledger_binding_ids_only(make_agent):
    """The obligation-set binding is captured as (set_id, version) plus obligation
    IDS -- and the ledger still owns the rows: demanding them back must work and
    carry the same ids (no copy, a reference to the authority)."""
    from core.conductor import obligation_ledger as ol
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(make_agent, "what is the price of gold?")
    state = context[TURN_STATE_KEY]
    assert state.obligation_set is not None, "the turn's ledger binding was not recorded"
    set_id, version = state.obligation_set
    rows = ol.demand_obligations(set_id, version)
    assert tuple(str(item.get("obligation_id") or "") for item in rows) == state.canonical_obligations


def test_an_inbound_body_cannot_pre_plant_the_state():
    """The reserved-key pin for the state half."""
    body_context = {"surface": "api", "turn_state": "forged"}
    stripped = strip_reserved_trust_keys(body_context)
    assert "turn_state" not in stripped


def test_state_record_lists_start_empty_never_guessed(make_agent):
    """The honesty pin: fields whose writers arrive in later milestones are EMPTY,
    not populated by inference -- the contract never guesses state."""
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(make_agent, "what is 2+2?")
    state = context[TURN_STATE_KEY]
    assert state.effects == []
    assert state.evidence == []
    assert state.pending_approvals == []
    assert state.failures == []


# ==================================================================================================
# M2 slice 3 — the typed LaneProposal at the live-data seam
# ==================================================================================================


def test_a_live_data_turn_records_its_typed_claim(make_agent):
    """THE fail-on-old-defect pin for slice 3: no proposal recording, no claim."""
    from core.turn_contract import TURN_STATE_KEY, LaneProposal

    _result, context = _run(
        make_agent,
        "what is the price of gold and silver?",
        source_context={"surface": "cli", "platform": "cli"},
    )
    state = context[TURN_STATE_KEY]
    claims = [p for p in state.proposals if isinstance(p, LaneProposal) and p.claimed]
    assert claims, f"the live-data lane recorded no typed claim (proposals: {state.proposals})"
    proposal = claims[0]
    assert proposal.lane_id == "live_data_typed_plan"
    assert proposal.obligations_claimed, "the claim names no demand units"
    assert proposal.effects_requested, "the claim requests no effects"
    assert proposal.evidence_expected, "the claim expects no evidence fields"
    assert proposal.confidence == 0.9  # the lane's own long-standing constant


def test_the_route_metadata_comes_from_the_proposal(make_agent):
    """The displacement pin: the served result's route identity is the PROPOSAL's,
    and the lane-id literal no longer appears at the agent's call sites (its one
    home is the lane constant)."""
    from core.turn_contract import TURN_STATE_KEY

    result, context = _run(
        make_agent,
        "what is the price of gold and silver?",
        source_context={"surface": "cli", "platform": "cli"},
    )
    proposals = context[TURN_STATE_KEY].proposals
    proposal = next(p for p in proposals if p.lane_id == "live_data_typed_plan")
    assert result.get("route_reason") == proposal.lane_id
    import pathlib

    agent_source = pathlib.Path("core/agent_runtime/agent.py").read_text(encoding="utf-8")
    assert "live_data_typed_plan" not in agent_source, (
        "the lane-id literal leaked back into the agent module -- it lives in "
        "core.live_data_plan.LIVE_DATA_LANE_ID"
    )


def test_a_live_data_decline_records_its_typed_refusal(make_agent):
    """The decline shape M4 builds on: the lane that cannot plan says WHY, typed,
    instead of returning None silently."""
    from core.turn_contract import TURN_STATE_KEY

    # A true decline: classified LIVE_DATA, but the recognizers name no entity the
    # typed plan can build from (verified: requirements=LIVE_DATA, plan=None).
    _result, context = _run(
        make_agent,
        "current weather forecast only",
        source_context={"surface": "cli", "platform": "cli"},
    )
    state = context[TURN_STATE_KEY]
    declines = [p for p in state.proposals if not p.claimed]
    assert declines, f"no typed decline recorded (proposals: {state.proposals})"
    assert declines[0].refusal_reason
    assert declines[0].terminal_eligibility == "none"


def test_a_proposal_cannot_carry_answer_bytes():
    """THE no-visible-bytes law, structural: the proposal type has exactly the
    work fields and NOTHING that can hold rendered answer content. Adding a
    content/answer/response field makes this red -- final bytes belong to the
    finalizer (M6), never to a proposal (M2/M4)."""
    import dataclasses

    from core.turn_contract import LaneProposal

    fields = {f.name for f in dataclasses.fields(LaneProposal)}
    assert fields == {
        "lane_id",
        "obligations_claimed",
        "required_capabilities",
        "effects_requested",
        "evidence_expected",
        "confidence",
        "refusal_reason",
        "unclaimed_obligations",
        "terminal_eligibility",
    }, fields


def test_an_inbound_body_cannot_pre_plant_proposals():
    body_context = {"surface": "api", "turn_proposals": ["forged"]}
    stripped = strip_reserved_trust_keys(body_context)
    assert "turn_proposals" not in stripped


# ==================================================================================================
# M2 slice 4 — the typed TurnResult at the finalization seam
# ==================================================================================================


from core.turn_contract import TurnResult  # (section-scoped, slice 4)


@pytest.fixture()
def fresh_ledger(tmp_path):
    """A fresh ledger store with a bound demand set and a `mint` helper that
    writes the production receipt/dispatch shape (the register-contract way)."""
    import storage.db as sdb
    from core.agent_runtime.answer_coverage import demand_units
    from core.conductor import obligation_ledger as ol
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "turn-contract-ledger.db")
    run_migrations()

    class _Ctx:
        def __init__(self, request_text: str) -> None:
            opened = ol.open_obligation_set(
                request_text=request_text,
                obligations=[
                    {"obligation_id": "ob:t:answer", "text": request_text[:240], "kind": "prose"},
                    *(
                        {
                            "obligation_id": f"ob:t:demand:{u.unit_id}",
                            "text": u.text,
                            "kind": "demand",
                            "unit_id": u.unit_id,
                            "slice_id": u.slice_id,
                        }
                        for u in demand_units(request_text)
                    ),
                ],
            )
            ol.record_disposition(
                opened["set_id"], opened["version"], "ob:t:answer", "satisfied",
                evidence_source="served_bytes",
            )
            ol.bind_active_set(opened["set_id"], opened["version"])
            self.set_id = opened["set_id"]
            self.version = opened["version"]
            self._text = request_text

        @property
        def closure(self) -> dict:
            return {**ol.closure_verdict(self.set_id, self.version), "set_id": self.set_id}

        def mint(self, served_entities: set[str]) -> None:
            """Receipt+dispatch the way the producing lane writes them, for every
            demand unit whose text names a served entity."""
            for unit in demand_units(self._text):
                if not any(entity.lower() in unit.text.lower() for entity in served_entities):
                    continue
                ol.record_slice_consumption(
                    self.set_id, self.version, unit_id=unit.unit_id,
                    family="live_info", evidence="slice_answer_record", excerpt="served",
                )
                ol.record_slice_dispatch(
                    self.set_id, self.version, unit_id=unit.unit_id,
                    subtask_id=f"t:{unit.unit_id}", operation="weather_lookup",
                    state="SUCCEEDED",
                )

    yield _Ctx
    ol.clear_active_set()
    sdb.configure_default_db_path(None)


def _finalize(content: str, *, closure: dict) -> dict:
    from core.finalization import finalize_answer

    return finalize_answer(
        turn_id="t-contract",
        canonical_content=content,
        closure=closure,
        status="ANSWER_PRESENT",
    )


def test_every_commit_carries_the_typed_result(fresh_ledger):
    """THE fail-on-old-defect pin for slice 4: no typed construction, no result."""
    from core.turn_contract import TurnResult

    ctx = fresh_ledger("2 + 2 = 4")
    commit = _finalize("2 + 2 = 4", closure=ctx.closure)
    result = TurnResult(**commit["turn_result"]) if isinstance(commit.get("turn_result"), dict) else commit.get("turn_result")
    assert isinstance(result, TurnResult), f"commit keys: {sorted(commit)}"
    assert result.committed_answer == commit["canonical_content"]
    assert result.final_content_hash == commit["content_hash"]
    assert result.terminal_state == commit["status"]


def test_the_hash_is_over_the_swept_bytes(fresh_ledger):
    import hashlib

    ctx = fresh_ledger("2 + 2 = 4")
    commit = _finalize("2 + 2 = 4", closure=ctx.closure)
    result = TurnResult(**commit["turn_result"])
    assert (
        result.final_content_hash
        == "sha256:" + hashlib.sha256(commit["canonical_content"].encode("utf-8")).hexdigest()
    )


def test_the_buckets_follow_the_ledger_census(fresh_ledger):
    """fulfilled/unresolved/refused are the LEDGER census's own three values —
    projected, not re-derived (M6 builds the real taxonomy on this seam)."""
    ctx = fresh_ledger(
        "what is weather in london and what is the price of unobtainium?"
    )
    ctx.mint({"london"})
    commit = _finalize(
        "London: Clear, 22 C.\n\nCould not be answered:\n* what is the price of unobtainium? — not dispatched",
        closure=ctx.closure,
    )
    result = TurnResult(**commit["turn_result"])
    verdict = commit["closure_verdict"]
    assert result.fulfilled_obligations == verdict["demand_satisfied"]
    assert result.refused_obligations == verdict["demand_unanswered"] == 1
    assert result.unresolved_obligations == verdict["demand_indeterminate"]


def test_undeclared_slots_stay_undeclared(fresh_ledger):
    """The honesty pin: evidence/attribution/usage/retry slots with no writer yet
    are empty/None — the typed result never invents a value."""
    ctx = fresh_ledger("2 + 2 = 4")
    commit = _finalize("2 + 2 = 4", closure=ctx.closure)
    result = TurnResult(**commit["turn_result"])
    assert tuple(result.evidence_references) == ()
    assert tuple(result.effect_receipts) == ()
    assert result.provider_attribution == ""
    assert result.token_usage_complete is None
    assert result.retry_classification == ""


# ==================================================================================================
# M3 slice 1 — the live-data lane consumes the canonical obligation set
# ==================================================================================================


INCIDENT_3_M3 = (
    "what is the average diesel and petrol price in Wurope now, what is weather in london "
    "and how far is riga from vilnius?"
)


def _plan_for(text):
    from core.agent_runtime.answer_coverage import demand_units
    from core.execution_requirements import requirements_for
    from core.live_data_plan import build_live_data_plan, lane_proposal_for_plan

    units = demand_units(text)
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a", canonical_units=units)
    if plan is None:
        return units, None, None
    return units, plan, lane_proposal_for_plan(
        plan, requirements_for(text, source_context=None)
    )


def test_unservable_demands_are_named_not_silent():
    """THE fail-on-old-defect pin for M3 slice 1 (the Incident-3 class): a demand
    the lane cannot bind is recorded as UNCLAIMED on the plan and the proposal —
    never silently absent."""
    _units, plan, proposal = _plan_for(INCIDENT_3_M3)
    assert plan is not None
    assert plan.unclaimed_unit_ids == ("u1", "u2", "u4"), plan.unclaimed_unit_ids
    assert proposal.unclaimed_obligations == ("u1", "u2", "u4")


def test_conservation_claimed_plus_unclaimed_covers_the_canonical_set():
    """The conservation law: claimed + unclaimed == the minted set, no overlap,
    no loss — for the incident AND its family shapes."""
    for text in (
        INCIDENT_3_M3,
        "what is weather in warsaw and moscow? which one is warmer?",
        "what is the price of gold and silver?",
        "price of bnb and arb please?",
    ):
        units, _plan, proposal = _plan_for(text)
        covered = sorted(proposal.obligations_claimed + proposal.unclaimed_obligations)
        assert covered == sorted(u.unit_id for u in units), (text, covered)
        assert not set(proposal.obligations_claimed) & set(proposal.unclaimed_obligations)


def test_the_claim_demand_census_reads_zero_divergence():
    """The acceptance instrument (M3's written threshold: ZERO divergence)."""
    from core.agent_runtime.answer_coverage import demand_units
    from core.execution_requirements import requirements_for
    from core.live_data_plan import build_live_data_plan, lane_proposal_for_plan

    family = (
        INCIDENT_3_M3,
        "weather warsaw and moscow which is warmer",
        "wheather in warsaw and moscow which one is warmr?",
        "what is the price of gold and silver? also btc price",
        "current weather forecast only",
    )
    for text in family:
        units = demand_units(text)
        plan = build_live_data_plan(
            text, plan_id="p", attempt_id="a", canonical_units=units
        )
        if plan is None:
            continue  # a declined lane claims nothing; the census covers claims only
        proposal = lane_proposal_for_plan(
            plan, requirements_for(text, source_context=None)
        )
        minted = {u.unit_id for u in units}
        divergence = minted - (
            set(proposal.obligations_claimed) | set(proposal.unclaimed_obligations)
        )
        assert not divergence, (text, divergence)


def test_a_lane_handed_no_units_keeps_legacy_shape():
    """Adversarial/compat: the consuming seam is additive — a caller that hands no
    canonical set (every existing direct-call test) gets the same plan, with an
    EMPTY unclaimed record rather than a guess."""
    from core.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(
        "what is the price of gold and silver?", plan_id="p", attempt_id="a"
    )
    assert plan is not None
    assert plan.unclaimed_unit_ids == ()


# ==================================================================================================
# M3 slice 2 — the frontdoor live-info fast path consumes the canonical set
# ==================================================================================================


def test_the_frontdoor_live_info_fast_path_records_its_claim(make_agent):
    """THE fail-on-old-defect pin for slice 2: the frontdoor fast path's claim is
    typed against the canonical set — claimed units for what it answers, unclaimed
    for the rest — instead of an implicit whole-turn grab. Driven at the flow seam
    (the recording site) with the REAL agent, so the claim fires on the news mode
    the typed lane does not claim."""
    from core.agent_runtime.fast_live_info_runtime_flow import (
        maybe_handle_live_info_fast_path,
    )
    from core.turn_contract import TURN_PROPOSALS_KEY

    agent = make_agent()
    context: dict = {"surface": "cli"}
    maybe_handle_live_info_fast_path(
        agent,
        "latest news about electric cars",
        session_id="sess-frontdoor",
        source_context=context,
        interpretation=None,
        response_class=agent.ResponseClass.UTILITY_ANSWER,
    )
    proposals = [p for p in context.get(TURN_PROPOSALS_KEY) or [] if p.lane_id == "live_info_fast_path"]
    assert proposals, "the frontdoor live-info fast path recorded no typed decision"
    proposal = proposals[-1]
    # Either a claim (serving runtime) or the typed disabled-decline (the conftest
    # agent's context disables live lookup) — both are typed decisions; the INVARIANT
    # is that the recording exists and conserves the canonical set either way.
    from core.agent_runtime.answer_coverage import demand_units

    units = {u.unit_id for u in demand_units("latest news about electric cars")}
    assert set(proposal.obligations_claimed) | set(proposal.unclaimed_obligations) == units
    assert proposal.claimed or proposal.refusal_reason


def test_the_frontdoor_decline_is_typed(make_agent):
    """A text the frontdoor's live-info mode does not claim records a typed
    refusal (never a silent None) when its handler is reached."""
    from core.agent_runtime.fast_live_info_runtime_flow import (
        _record_frontdoor_live_info_proposal,
    )
    from core.turn_contract import TURN_PROPOSALS_KEY

    context: dict = {"surface": "cli"}
    _record_frontdoor_live_info_proposal(
        context, user_input="gm", claimed_unit_ids=(), refusal_reason="no live-info mode for this text",
    )
    proposal = context[TURN_PROPOSALS_KEY][-1]
    assert not proposal.claimed
    assert proposal.refusal_reason == "no live-info mode for this text"


def test_frontdoor_conservation_across_a_mixed_turn(make_agent):
    """The conservation law on the frontdoor seam: for a turn whose live-info
    clause the frontdoor answers, claimed + unclaimed == the canonical set of the
    FULL effective text — the sibling clauses are named, not lost."""
    from core.agent_runtime.answer_coverage import demand_units
    from core.agent_runtime.fast_live_info_runtime_flow import (
        _record_frontdoor_live_info_proposal,
    )
    from core.turn_contract import TURN_PROPOSALS_KEY

    full_text = "whats the wheather in rome rihgt now? also remind me to call mom"
    scoped = "whats the wheather in rome rihgt now?"
    context: dict = {"surface": "cli", "effective_input": full_text}
    from core.agent_runtime.answer_coverage import demand_units as _du

    _record_frontdoor_live_info_proposal(
        context, user_input=scoped, claimed_unit_ids=tuple(u.unit_id for u in _du(scoped)), refusal_reason="",
    )
    proposal = context[TURN_PROPOSALS_KEY][-1]
    minted = {u.unit_id for u in demand_units(full_text)}
    assert set(proposal.obligations_claimed) | set(proposal.unclaimed_obligations) == minted
    assert set(proposal.unclaimed_obligations), "the sibling clause must be named"


# ==================================================================================================
# M3 slice 3 — the conductor lane consumes the canonical obligation set
# ==================================================================================================


def _stub_node(start: int, end: int):
    from types import SimpleNamespace

    return SimpleNamespace(clause_span=(start, end))


def _stub_plan(*spans):
    from types import SimpleNamespace

    return SimpleNamespace(nodes=[_stub_node(s, e) for s, e in spans])


def _units_of(text):
    from core.agent_runtime.answer_coverage import demand_units

    return demand_units(text)


def test_conductor_claim_binds_by_span_overlap():
    """The binding law: a unit is claimed when its span OVERLAPS a node's
    clause_span over the same text — geometric, never lexical."""
    from core.conductor.planner import conductor_lane_proposal

    text = "what is the price of gold and silver? also write a poem"
    units = _units_of(text)
    # A plan covering only the price clause's span (found by the units' own spans).
    price_units = [u for u in units if "gold" in u.text or "silver" in u.text]
    start = min(u.start for u in price_units)
    end = max(u.end for u in price_units)
    proposal = conductor_lane_proposal(_stub_plan((start, end)), units)
    assert set(proposal.obligations_claimed) == {u.unit_id for u in price_units}
    assert proposal.unclaimed_obligations, "the poem clause must be named"


def test_conductor_claim_conserves_the_canonical_set():
    """claimed + unclaimed == every unit, no overlap — the conservation law on
    the conductor seam, including the no-plan degenerate shape."""
    from core.conductor.planner import conductor_lane_proposal

    for spans in ((), ((0, 5),), ((0, 10**6),)):
        units = _units_of("what is the price of gold and silver? also write a poem")
        proposal = conductor_lane_proposal(_stub_plan(*spans), units)
        minted = {u.unit_id for u in units}
        assert set(proposal.obligations_claimed) | set(proposal.unclaimed_obligations) == minted
        assert not set(proposal.obligations_claimed) & set(proposal.unclaimed_obligations)


def test_a_conductor_turn_records_its_typed_decision(make_agent):
    """THE fail-on-old-defect pin: a conductor-shaped turn (Incident 1) records a
    conductor proposal — a claim when the planner plans, the typed decline when
    it cannot (the local test backend's planner declines; both are decisions)."""
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(
        make_agent,
        "what is the price of ETH? i have 1 ETH i want to sell and buy silver? how mcuh silver I will get?",
        source_context={"surface": "cli", "platform": "cli"},
    )
    proposals = [p for p in context[TURN_STATE_KEY].proposals if p.lane_id == "conductor_multi_intent_plan"]
    assert proposals, "the conductor recorded no typed decision"
    proposal = proposals[-1]
    assert proposal.claimed or proposal.refusal_reason
    units = _units_of("what is the price of ETH? i have 1 ETH i want to sell and buy silver? how mcuh silver I will get?")
    assert set(proposal.obligations_claimed) | set(proposal.unclaimed_obligations) <= {u.unit_id for u in units}


def test_the_conductor_decline_is_typed():
    from core.conductor.planner import declined_conductor_proposal

    proposal = declined_conductor_proposal("plan_conductor_turn returned no plan")
    assert not proposal.claimed
    assert proposal.refusal_reason == "plan_conductor_turn returned no plan"
    assert proposal.terminal_eligibility == "none"


# ==================================================================================================
# M3 slice 4 — the fallback route claims the unclaimed; the mint is enriched
# ==================================================================================================


def test_the_fallback_route_claims_the_unclaimed(make_agent):
    """THE fail-on-old-defect pin: a turn no deterministic lane claims (a poem
    ask — model-route territory) still gets a TYPED claim: the route that
    actually serves the turn claims the WHOLE canonical set. The implicit
    whole-turn fallback grab is now explicit and conserved."""
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(
        make_agent,
        "write me a short poem about the sea",
        source_context={"surface": "cli", "platform": "cli"},
    )
    proposals = [
        p for p in context[TURN_STATE_KEY].proposals if p.obligations_claimed
    ]
    assert proposals, "no lane (deterministic or fallback) claimed this turn"
    from core.agent_runtime.answer_coverage import demand_units

    minted = {u.unit_id for u in demand_units("write me a short poem about the sea")}
    assert minted, "fixture must mint at least one unit"
    assert set(proposals[-1].obligations_claimed) >= minted


def test_a_deterministically_served_turn_gets_no_parasitic_fallback_claim(make_agent):
    """The falsifiable direction: when a deterministic lane claims, the fallback
    recorder stays SILENT — it must never double-claim a served turn."""
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(
        make_agent,
        "what is the price of gold and silver?",
        source_context={"surface": "cli", "platform": "cli"},
    )
    claims = [p for p in context[TURN_STATE_KEY].proposals if p.obligations_claimed]
    assert claims, "expected the live-data lane's claim"
    assert len(claims) == 1, [p.lane_id for p in claims]


# ------------------------------------------------------------------ mint enrichment


def test_minted_units_carry_exact_source_spans():
    """`text == original[start:end]` for every minted unit — the span is exact,
    not approximate (the property the conductor's geometric binding relies on)."""
    from core.agent_runtime.answer_coverage import demand_units

    for text in (
        "what is the price of gold now? also write a poem.",
        "wheather in warsaw and moscow which one is warmr?",
        "what is the average diesel and petrol price in Wurope now, what is weather in london?",
    ):
        for unit in demand_units(text):
            assert text[unit.start : unit.end] == unit.text, (text, unit)


def test_freshness_comes_only_from_stated_recency_phrases():
    """A unit's freshness cue is the phrase IT states, from the marker set the
    live-info classifier already owns; units without one stay empty — never
    inferred."""
    from core.agent_runtime.answer_coverage import demand_units

    fresh = demand_units("whats the current price of gold?")[0]
    plain = demand_units("explain how tides work")[0]
    assert fresh.freshness == "current price"
    assert plain.freshness == ""


def test_prohibitions_are_preserved_not_vanished():
    """The polarity contract's preservation half: a prohibition clause mints
    NOTHING (by law) but is NAMED by `prohibited_clauses` — the reason it is
    absent is visible, not silent."""
    from core.agent_runtime.answer_coverage import demand_units, prohibited_clauses

    text = "what is the price of gold? do not send any money."
    units = demand_units(text)
    assert all(u.polarity == "request" for u in units)
    assert not any("money" in u.text for u in units), "a prohibition must not mint"
    assert prohibited_clauses(text) == ("do not send any money.",)


def test_enrichment_never_changes_cardinality():
    """Canonicalization may change representation, never cardinality (M3 law 6):
    the enriched mint produces the SAME unit ids as before enrichment."""
    from core.agent_runtime.answer_coverage import demand_units

    for text in (
        "what is the price of gold and silver? also write a poem",
        "wheather in warsaw and moscow which one is warmr?",
    ):
        ids = [u.unit_id for u in demand_units(text)]
        assert ids == [f"u{i + 1}" for i in range(len(ids))], ids


# ==================================================================================================
# M4 slice 1 — the one ordered lane registry and the kernel's claim decision
# ==================================================================================================


def test_the_kernel_decides_one_claim_per_obligation():
    """THE fail-on-old-defect pin: given proposals and units, exactly ONE
    decision exists per unit — with an owner when claimed, model_required
    under the fallback, unclaimed as the named anomaly."""
    from types import SimpleNamespace

    from core.lane_registry import (
        DECISION_CLAIMED,
        DECISION_MODEL_REQUIRED,
        decide_claims,
    )
    from core.turn_contract import LaneProposal

    units = [SimpleNamespace(unit_id=f"u{i}") for i in (1, 2, 3)]
    proposals = [
        LaneProposal(lane_id="live_data_typed_plan", obligations_claimed=("u1",)),
        LaneProposal(lane_id="fallback_route", obligations_claimed=("u1", "u2", "u3")),
    ]
    ledger = decide_claims(units, proposals)
    d1, d2, d3 = (ledger.for_unit(u.unit_id) for u in units)
    assert d1.decision == DECISION_CLAIMED and d1.owner == "live_data_typed_plan"
    assert d2.decision == DECISION_MODEL_REQUIRED
    assert d3.decision == DECISION_MODEL_REQUIRED


def test_contested_claims_resolve_by_registry_order_not_arrival():
    """The ordered registry decides contests: frontdoor ranks before the typed
    lane regardless of the proposals' arrival order; the loser is recorded as
    a claimant, not discarded."""
    from types import SimpleNamespace

    from core.lane_registry import LANE_REGISTRY, decide_claims
    from core.turn_contract import LaneProposal

    assert LANE_REGISTRY.index("live_info_fast_path") < LANE_REGISTRY.index(
        "live_data_typed_plan"
    )
    units = [SimpleNamespace(unit_id="u1")]
    proposals = [
        LaneProposal(lane_id="live_data_typed_plan", obligations_claimed=("u1",)),
        LaneProposal(lane_id="live_info_fast_path", obligations_claimed=("u1",)),
    ]
    decision = decide_claims(units, proposals).for_unit("u1")
    assert decision.owner == "live_info_fast_path"
    assert decision.claimants == ("live_info_fast_path", "live_data_typed_plan")


def test_no_proposal_at_all_is_the_named_anomaly():
    from types import SimpleNamespace

    from core.lane_registry import DECISION_UNCLAIMED, decide_claims

    units = [SimpleNamespace(unit_id="u1")]
    ledger = decide_claims(units, [])
    assert ledger.for_unit("u1").decision == DECISION_UNCLAIMED


def test_the_spine_carries_the_ledger_on_every_turn(make_agent):
    """THE wiring pin: the kernel decision is computed at the run_once spine
    from the lanes' own recorded proposals — the TurnState carries it."""
    from core.lane_registry import DECISION_CLAIMED, ClaimLedger
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(
        make_agent,
        "what is the price of gold and silver?",
        source_context={"surface": "cli", "platform": "cli"},
    )
    ledger = context[TURN_STATE_KEY].claim_ledger
    assert isinstance(ledger, ClaimLedger), "the spine computed no claim ledger"
    assert ledger.decisions, "the ledger decided nothing"
    assert all(d.decision == DECISION_CLAIMED for d in ledger.decisions), [
        (d.unit_id, d.decision) for d in ledger.decisions
    ]
    assert all(d.owner for d in ledger.decisions)


# ==================================================================================================
# M4 slice 2 — claim mediation on the decide_claims seam
# ==================================================================================================


def test_mediation_allows_the_earliest_ranked_claimant():
    """A lane consulting the kernel gets allowed=True when no earlier-ranked
    lane has claimed the units it wants — including when a LATER-ranked lane
    already recorded (arrival order is irrelevant; only registry rank is)."""
    from core.lane_registry import mediate
    from core.turn_contract import LaneProposal

    recorded = [
        LaneProposal(lane_id="conductor_multi_intent_plan", obligations_claimed=("u1",))
    ]
    verdict = mediate(recorded, "live_data_typed_plan", ("u1",))
    assert verdict.allowed, "conductor ranks AFTER live-data; live-data must be allowed"


def test_mediation_supersedes_a_lower_ranked_lane_with_named_contest():
    """The falsifiable direction: consulting a lane that ranks BELOW an already
    recorded claimant gets allowed=False, the superseder named, the contested
    units listed."""
    from core.lane_registry import mediate
    from core.turn_contract import LaneProposal

    recorded = [
        LaneProposal(lane_id="live_info_fast_path", obligations_claimed=("u1", "u2"))
    ]
    verdict = mediate(recorded, "live_data_typed_plan", ("u2", "u3"))
    assert not verdict.allowed
    assert verdict.superseded_by == "live_info_fast_path"
    assert verdict.contested == ("u2",)


def test_a_superseded_lane_records_the_typed_refusal_not_a_double_claim():
    """THE fail-on-old-defect pin: when the kernel mediates a superseded claim
    AWAY, the recorded proposal is a typed refusal naming the superseder —
    exactly ONE lane's claim survives per unit (the double-claim shape that
    made ownership ambiguous is gone)."""
    from core.agent_runtime.agent import VoolAgent
    from core.turn_contract import TURN_PROPOSALS_KEY, LaneProposal

    agent = VoolAgent.__new__(VoolAgent)
    context: dict = {}
    # The frontdoor's claim already recorded...
    agent._record_lane_proposal(
        context, LaneProposal(lane_id="live_info_fast_path", obligations_claimed=("u1",))
    )
    # ...and the typed lane consults the kernel with the same unit:
    agent._record_lane_proposal(
        context,
        LaneProposal(
            lane_id="live_data_typed_plan", obligations_claimed=("u1",), confidence=0.9
        ),
    )
    proposals = context[TURN_PROPOSALS_KEY]
    assert proposals[0].obligations_claimed == ("u1",)
    assert proposals[1].obligations_claimed == ()
    assert proposals[1].refusal_reason.startswith("superseded by live_info_fast_path")


def test_mediated_claims_decide_one_owner(make_agent):
    """The spine-level shape: with mediation at record time, the kernel ledger
    has exactly one claimant per unit on a served turn (no contest ambiguity)."""
    from core.turn_contract import TURN_STATE_KEY

    _result, context = _run(
        make_agent,
        "what is the price of gold and silver?",
        source_context={"surface": "cli", "platform": "cli"},
    )
    ledger = context[TURN_STATE_KEY].claim_ledger
    assert ledger is not None and ledger.decisions
    for decision in ledger.decisions:
        if decision.decision == "claimed":
            lane_claimants = [c for c in decision.claimants if c != decision.owner]
            assert not lane_claimants or all(
                c not in ("live_data_typed_plan", "live_info_fast_path", "conductor_multi_intent_plan")
                or c == decision.owner
                for c in lane_claimants
            ), decision


# ==================================================================================================
# M4 slice 3 — consult-before-serve on the live-data lane
# ==================================================================================================


def test_the_live_data_lane_declines_when_the_kernel_refuses():
    """THE fail-on-old-defect pin: with an earlier-ranked lane's claim already
    recorded on the units the plan binds, the live-data lane CONSULTS the
    kernel before executing and declines — the typed consult refusal is
    recorded and the lane does not execute its plan."""
    from core.agent_runtime.agent import VoolAgent
    from core.turn_contract import TURN_PROPOSALS_KEY, LaneProposal

    agent = VoolAgent.__new__(VoolAgent)
    context: dict = {
        "surface": "cli",
        # The frontdoor's claim already on the turn's units:
        TURN_PROPOSALS_KEY: [
            LaneProposal(
                lane_id="live_info_fast_path",
                obligations_claimed=("u1", "u2"),
            )
        ],
    }
    allowed, superseder = agent._kernel_allows_lane(
        context, lane_id="live_data_typed_plan", unit_ids=("u1", "u2")
    )
    assert not allowed
    assert superseder == "live_info_fast_path"
    # And the falsifiable direction — units nobody claimed:
    allowed2, _ = agent._kernel_allows_lane(
        context, lane_id="live_data_typed_plan", unit_ids=("u9",)
    )
    assert allowed2


def test_consult_before_serve_blocks_the_execute(make_agent):
    """End-to-end at the lane seam: a pre-seeded earlier claim on the plan's
    units makes the live-data turn return None (no plan executed) with the
    typed consult refusal recorded — the earlier lane's service stands."""
    from core.agent_runtime.answer_coverage import demand_units
    from core.turn_contract import TURN_PROPOSALS_KEY, LaneProposal

    text = "what is the price of gold and silver?"
    units = demand_units(text)
    context: dict = {
        "surface": "cli",
        "platform": "cli",
        TURN_PROPOSALS_KEY: [
            LaneProposal(
                lane_id="live_info_fast_path",
                obligations_claimed=tuple(u.unit_id for u in units),
            )
        ],
    }
    agent = make_agent()
    agent.run_once(text, source_context=context)
    refusals = [
        p
        for p in context.get(TURN_PROPOSALS_KEY) or []
        if p.lane_id == "live_data_typed_plan" and not p.claimed
    ]
    assert any(
        "consult-before-serve" in p.refusal_reason for p in refusals
    ), [p.refusal_reason for p in refusals]
    # The superseded lane's ORIGINAL claim was mediated away at record time
    # (slice 2), so no double claim exists:
    claims = [p for p in context[TURN_PROPOSALS_KEY] if p.obligations_claimed]
    assert all(c.lane_id == "live_info_fast_path" for c in claims)


def test_the_frontdoor_claims_turn_ids_not_slice_ids():
    """The id-space law: the frontdoor's claimed ids resolve against the
    TURN's canonical set (by unit-text containment), never a slice-local
    mint — u1-of-the-slice and u1-of-the-turn are different demands."""
    from core.agent_runtime.answer_coverage import demand_units
    from core.agent_runtime.fast_live_info_runtime_flow import (
        _frontdoor_claimed_units,
    )

    full = "what is the price of gold and silver? also write a poem"
    slice_text = "what is the price of gold and silver?"
    claimed = _frontdoor_claimed_units(slice_text)
    turn_units = {u.unit_id: u.text for u in demand_units(full)}
    assert claimed, "the covered clauses must be claimed"
    for unit_id in claimed:
        assert unit_id in turn_units
        assert turn_units[unit_id] in slice_text
    # The poem unit (u2-of-turn) is NOT claimed by the slice:
    poem_units = [uid for uid, text_ in turn_units.items() if "poem" in text_]
    for uid in poem_units:
        assert uid not in claimed


# ==================================================================================================
# M4 slice 4 — consult coverage complete: conductor + frontdoor serves
# ==================================================================================================


def test_the_conductor_declines_when_the_kernel_refuses():
    """THE fail-on-old-defect pin: an earlier-ranked claim on the units the
    conductor plan binds makes the conductor CONSULT-decline before executing
    (typed refusal recorded; the earlier lane's service stands)."""
    from core.agent_runtime.agent import VoolAgent
    from core.turn_contract import TURN_PROPOSALS_KEY, LaneProposal

    agent = VoolAgent.__new__(VoolAgent)
    context: dict = {
        "surface": "cli",
        TURN_PROPOSALS_KEY: [
            LaneProposal(
                lane_id="live_data_typed_plan",
                obligations_claimed=("u1", "u2"),
            )
        ],
    }
    allowed, superseder = agent._kernel_allows_lane(
        context,
        lane_id="conductor_multi_intent_plan",
        unit_ids=("u1", "u2"),
    )
    assert not allowed
    assert superseder == "live_data_typed_plan"
    # Falsifiable direction: an uncontested unit set allows:
    allowed2, _ = agent._kernel_allows_lane(
        context, lane_id="conductor_multi_intent_plan", unit_ids=("u9",)
    )
    assert allowed2


def test_every_serving_lane_consults():
    """The structural law, AST-pinned: all three registry lanes GUARD their
    serve on the kernel's consult — the guard's `if` test is exactly
    `not <allowed-var>` (a bare Name load), so neutralizing the guard the
    lazy way (`if False and not ...`) turns the BoolOp into a red here."""
    import ast
    import pathlib

    agent_tree = ast.parse(
        pathlib.Path("core/agent_runtime/agent.py").read_text(encoding="utf-8")
    )
    guarded_returns = 0
    for node in ast.walk(agent_tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id.endswith("_allowed")
        ):
            continue
        for child in node.body:
            if isinstance(child, ast.Return):
                guarded_returns += 1
                break
    assert guarded_returns >= 3, guarded_returns  # 2 live-data + 1 conductor
    flow_source = pathlib.Path(
        "core/agent_runtime/fast_live_info_runtime_flow.py"
    ).read_text(encoding="utf-8")
    assert "superseded by" in flow_source and "mediate(" in flow_source, (
        "the frontdoor serve must consult the kernel mediator and refuse typed"
    )


def test_the_registry_first_lane_cannot_be_superseded():
    """Registry rank 0 (frontdoor) is unsupersedeable by construction — the
    falsifiable direction of the frontdoor consult: even a recorded claim by
    every other lane leaves it allowed."""
    from core.lane_registry import mediate
    from core.turn_contract import LaneProposal

    recorded = [
        LaneProposal(lane_id="live_data_typed_plan", obligations_claimed=("u1",)),
        LaneProposal(
            lane_id="conductor_multi_intent_plan", obligations_claimed=("u1",)
        ),
    ]
    assert mediate(recorded, "live_info_fast_path", ("u1",)).allowed


# ==================================================================================================
# M2 TRUTH REPAIR (arch-truth-r1, 2026-08-31) — the request is INGRESS, not post-hoc audit
# --------------------------------------------------------------------------------------------------
# The slice-1 wiring constructed the TurnRequest AFTER `_run_once_inner` returned: the whole
# legacy runtime routed, called models/tools, and built the answer first, so the typed request
# was an audit decoration, not the ingress contract. These pins hold the repaired order: the
# ONE server-derived request exists BEFORE the inner runtime begins, rides it explicitly (a
# typed parameter, mechanically required), and is CONSUMED by the runtime's own intake.
# ==================================================================================================


def test_the_request_exists_before_the_inner_runtime_begins(make_agent):
    """THE fail-on-old-defect timing pin: at the moment `_run_once_inner` is ENTERED,
    the canonical TurnRequest is already constructed and published — the inner runtime
    is intercepted with a stub, so nothing the legacy path does can satisfy this."""
    from unittest import mock

    agent = make_agent()
    context: dict = {
        "surface": "cli",
        "platform": "cli",
        # a forged inbound contract plus forged trust claims — neither may survive:
        TURN_REQUEST_KEY: "forged inbound turn_request",
        "principal": PRINCIPAL_OWNER_LOCAL,
        "trust_principal": PRINCIPAL_OWNER_LOCAL,
    }
    captured: dict = {}

    def _inner_spy(self, user_input, **kwargs):
        captured["user_input"] = user_input
        captured["kwargs"] = dict(kwargs)
        captured["context_at_entry"] = dict(context)
        return {"success": True, "response": "stub"}

    with mock.patch.object(type(agent), "_run_once_inner", _inner_spy):
        result = agent.run_once("wheather in  warsaw 😀", source_context=context)
    assert result["response"] == "stub"

    at_entry = captured["context_at_entry"]
    assert TURN_REQUEST_KEY in at_entry, (
        "the inner runtime began without the canonical request in the turn context "
        "(post-hoc construction — the request is audit decoration, not ingress)"
    )
    request = at_entry[TURN_REQUEST_KEY]
    assert isinstance(request, TurnRequest), (
        f"the context key held {request!r} at inner entry, not a typed TurnRequest"
    )
    # the forged inbound value cannot survive ingress:
    assert not isinstance(request, str)
    # the explicit typed argument is the IDENTICAL object, not a re-read:
    assert captured["kwargs"].get("turn_request") is request, (
        "the inner runtime was not handed the canonical request explicitly"
    )
    # byte-exact user text:
    assert request.user_text == captured["user_input"] == "wheather in  warsaw 😀"
    # server-derived trust: the forged principal claims were never read:
    assert request.trust_principal == PRINCIPAL_OWNER_LOCAL
    # all three identities already bound BEFORE the inner runtime began:
    assert request.request_id, "request_id not bound at inner entry"
    assert request.turn_id, "turn_id not bound at inner entry"
    assert request.session_id, "session_id not bound at inner entry"
    # and the published contract after the turn is still that same object:
    assert context[TURN_REQUEST_KEY] is request


def test_the_inner_runtime_consumes_the_requests_identity(make_agent):
    """The load-bearing pin: `_run_once_inner` READS the typed request. The session
    the turn runs under (stamped into the context at the checkpoint intake) is the
    one the CONTRACT carries — the typed object is consumed by real intake work,
    not attached as decoration."""
    request = TurnRequest.from_ingress(
        user_text="hello",
        source_context={"surface": "cli", "platform": "cli"},
        request_id="req-ingress-pin",
        turn_id="turn-ingress-pin",
        session_id="sess-ingress-pin",
    )
    context: dict = {"surface": "cli", "platform": "cli"}
    agent = make_agent()
    agent._run_once_inner("hello", source_context=context, turn_request=request)
    assert context["session_id"] == "sess-ingress-pin", (
        "the turn's session did not come from the typed request — the contract is "
        "not load-bearing at intake"
    )
    assert context.get(TURN_REQUEST_KEY) is request
