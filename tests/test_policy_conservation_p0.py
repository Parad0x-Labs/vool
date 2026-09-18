"""P0 — CHILD DEMANDS CAN NEVER WIDEN USER PROHIBITIONS.

THE MEASURED DEFECT (base 389beea5, hermetic, socket sentinel)
--------------------------------------------------------------
    "No web. Tell me the current ETH price."

`analyze_retrieval_constraints` reads the prohibition at the whole-turn boundary
and every whole-turn lane declines the fetch. But the message mints TWO demand
units — "No web." and "Tell me the current ETH price." — so the demand-owned
executor claims the turn and dispatches the price unit as a child sub-turn whose
text is the clean slice. The prohibition words live only in the parent text, the
child re-parses ITS OWN text, sees no prohibition, and fetches CoinGecko: 12
outbound socket attempts measured at base (`api.coingecko.com`, then
`api.search.brave.com`), with the demand ledger recording the fetch as
`executed`.

Delegation narrowed the turn's understanding of what it may do. A child may be
given LESS authority than its parent; it may never be given MORE.

THE CONTRACT UNDER TEST
-----------------------
* The parent's prohibitions are FROZEN on the canonical TurnRequest at ingress
  (typed families + toolsets + reason codes + a digest), and every child
  sub-turn runs under that same request object, so inheritance is by
  construction — request/turn/session identity and the constraint reason codes
  are preserved, never re-derived from the slice.
* Child policy is the INTERSECTION: parent authority ∩ child capability. A
  prohibited live demand ends in a typed REFUSED terminal state without
  dispatch; unrelated permitted demands in the same turn still execute and the
  turn reports partial failure honestly.
* The quoted-text control: a prohibition that appears only inside quotation
  marks is somebody else's words, not an instruction to this runtime, and must
  not refuse anything (the canonical mint is quote-aware).
* No ETH/weather words are special-cased anywhere; the prohibition families are
  the retrieval-constraint authority's own vocabulary.

THE STAND-INS (named)
---------------------
`render_response` plus the router's `ModelExecutionDecision` pin the general
lane to a fixed text (the seam `test_mixed_demand_execution_p0` uses), and the
ETH quote mock pins CoinGecko to a canned LiveQuoteResult for the control that
must still fetch. The socket sentinel blocks and counts every outbound attempt:
the hermetic proofs here never touch the network.
"""
from __future__ import annotations

import contextlib
import socket
from unittest import mock

import pytest

from core.agent_runtime.demand_ownership import (
    DEMAND_EXECUTED,
    demand_coverage,
)
from core.turn_contract import TURN_DEMAND_LEDGER_KEY, TURN_REQUEST_KEY, TurnRequest
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

_LINE_ONE = "ALPHA-LINE-ONE the ledger was reconciled"
_LINE_TWO = "BRAVO-LINE-TWO she jogged to the market"

_JUDGEMENT = "JUDGEMENT-STAND-IN: a prohibition clause is not a demand."

#: The incident message: one prohibition, one live price demand.
_INCIDENT = "No web. Tell me the current ETH price."
#: The partial-turn message: one permitted local read, one prohibited lookup.
_PARTIAL = "No web. Read notes.txt and give me the current ETH price."
#: Prohibition AFTER the demand, and phrased differently — same contract.
_REORDERED = "Tell me the current ETH price. Don't search the web."
#: A child that asks for the web IN ITS OWN WORDS still cannot widen the parent.
_WIDENING = "No web. Read notes.txt and also fetch the current ETH price online."

_ETH_PRICE_TEXT = "3451.07"
_ETH_STAND_IN = "ETH-STAND-IN: 3451.07 USD (canned CoinGecko answer)"


# ============================================================ the sentinel


class _SocketSentinel:
    """Block and count every outbound socket attempt (connect + DNS)."""

    def __init__(self) -> None:
        self.attempts: list[object] = []
        self._stack: contextlib.ExitStack | None = None

    def _blocked(self, *_args, **_kwargs):
        raise OSError("SENTINEL: outbound socket blocked")

    def __enter__(self) -> _SocketSentinel:
        stack = contextlib.ExitStack()
        sentinel = self

        def _connect(sock, address):
            sentinel.attempts.append(("connect", address))
            raise OSError("SENTINEL: outbound connect blocked")

        def _getaddrinfo(host, *args, **kwargs):
            sentinel.attempts.append(("dns", host))
            raise OSError("SENTINEL: outbound dns blocked")

        stack.enter_context(
            mock.patch.object(socket.socket, "connect", _connect)
        )
        stack.enter_context(mock.patch.object(socket, "getaddrinfo", _getaddrinfo))
        self._stack = stack
        return self

    def __exit__(self, *exc) -> None:
        assert self._stack is not None
        self._stack.close()

    def hosts(self) -> list[str]:
        return [str(entry[-1]) for entry in self.attempts]


def _model_stand_in(agent, text: str = _JUDGEMENT):
    """Deterministic general answers through the REAL model lane."""
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model",
        task_hash="p0-policy-conservation",
        provider_id="p0-policy-conservation",
        provider_name="P0 stand-in",
        model_name="p0-policy-conservation",
        output_text=text,
        confidence=0.9,
        trust_score=0.9,
        used_model=True,
    )
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch.object(agent.memory_router, "resolve", return_value=decision)
    )
    stack.enter_context(
        mock.patch("core.agent_runtime.agent.render_response", return_value=text)
    )
    return stack


def _eth_quote_mock():
    """A canned CoinGecko answer so the CONTROL demand can really execute."""

    def fake_crypto(coin_ids, **_kwargs):
        from core.live_quote_contract import LiveQuoteResult

        return [
            LiveQuoteResult(
                asset_key="ethereum",
                asset_name="Ethereum",
                symbol="ETH",
                value=3451.07,
                currency="USD",
                as_of="2026-09-01 10:00 UTC",
                source_label="CoinGecko",
                source_url="https://x",
                kind="crypto",
                change_percent=0.21,
            )
        ]

    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch(
            "tools.web.web_research._crypto_price_fallback_multi",
            side_effect=fake_crypto,
        )
    )
    return stack


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "notes.txt").write_text(f"{_LINE_ONE}\n{_LINE_TWO}\n", encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-pc-p0-{request.node.name[:40]}")
    try:
        yield h
    finally:
        h.close()


def _turn(harness: _Harness, text: str, workspace: str) -> tuple[str, dict]:
    """One real turn in a real workspace. Returns (answer, the context it ran under)."""
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with _model_stand_in(harness.agent):
        result = harness.agent.run_once(
            text, source_context=context, session_id_override=harness.session_id
        )
    return str(result.get("response") or ""), context


def _ledger(context: dict) -> list[dict]:
    return [dict(row) for row in (context.get(TURN_DEMAND_LEDGER_KEY) or [])]


def _refused_state() -> str:
    """The typed refused terminal state. Falls back to the literal value at
    base so the CONTROL tests can run there (absence of the state is their
    pass condition); the incident tests go red on behavior either way."""
    try:
        from core.agent_runtime.demand_ownership import DEMAND_REFUSED

        return DEMAND_REFUSED
    except ImportError:
        return "refused"


def _dispatches(context: dict) -> list[dict]:
    from core.agent_runtime.answer_coverage import COVERAGE_CONTEXT_KEY

    coverage = context.get(COVERAGE_CONTEXT_KEY) or {}
    return [dict(row) for row in (coverage.get("dispatches") or [])]


# ============================================= 1. the incident: zero retrieval


def test_1_the_incident_makes_zero_outbound_calls(harness, workspace):
    """"No web. Tell me the current ETH price." — the parent forbade retrieval,
    so no child of that turn may open a socket for it. Zero outbound attempts,
    and the price demand is refused by name."""
    with _SocketSentinel() as sentinel:
        answer, context = _turn(harness, _INCIDENT, workspace)

    assert sentinel.attempts == [], (
        f"the parent prohibition was not conserved to the child; outbound "
        f"attempts: {sentinel.attempts[:6]}"
    )
    rows = _ledger(context)
    price_rows = [row for row in rows if "ETH" in row["request"] or "price" in row["request"]]
    assert price_rows, f"no ledger row for the price demand: {rows}"
    assert all(row["terminal_state"] == _refused_state() for row in price_rows), rows
    assert any(
        "forbid" in answer.lower() or "refus" in answer.lower() for _ in [answer]
    ) or any(row["terminal_state"] == _refused_state() for row in price_rows), (
        f"the refusal is neither named in the answer nor recorded: {answer!r}"
    )


def test_1_the_whole_turn_gate_is_untouched_for_pure_shapes(harness, workspace):
    """CONTROL. A message whose prohibition rides the SAME slice as its demand
    ("Tell me the current ETH price. Don't search the web.") mints ONE unit and
    keeps its whole-turn refusal — the lane reads the prohibition in its own
    text. Conservation adds child discipline; it may not disturb this path."""
    with _SocketSentinel() as sentinel:
        answer, _context = _turn(harness, _REORDERED, workspace)

    assert sentinel.attempts == [], sentinel.attempts[:6]
    assert _ETH_PRICE_TEXT not in answer


# ================================ 2. permitted demand executes, prohibited refuses


def test_2_permitted_demand_executes_prohibited_refuses_turn_is_partial(harness, workspace):
    """"No web. Read notes.txt and give me the current ETH price." — the local
    read is unrelated to the prohibition and MUST still execute; the price
    demand ends typed REFUSED; the turn reports both honestly."""
    with _SocketSentinel() as sentinel:
        answer, context = _turn(harness, _PARTIAL, workspace)

    assert sentinel.attempts == [], sentinel.attempts[:6]
    assert _LINE_ONE in answer and _LINE_TWO in answer, (
        f"the permitted file demand did not execute: {answer!r}"
    )
    rows = _ledger(context)
    by_demand = {row["demand_id"]: row for row in rows}
    states = {row["request"][:30]: row["terminal_state"] for row in rows}
    assert any(row["terminal_state"] == DEMAND_EXECUTED for row in rows), states
    refused = [row for row in rows if row["terminal_state"] == _refused_state()]
    assert refused, f"no typed REFUSED row for the prohibited demand: {states}"
    assert any("price" in row["request"] or "ETH" in row["request"] for row in refused), states
    # The refusal is NAMED in the composed answer — partial failure is never silent.
    assert "could not answer" in answer.lower(), answer
    _ = by_demand


def test_2_the_refused_demand_never_records_retrieval_success(harness, workspace):
    """Receipts/Activity: the refused demand's dispatch row is REFUSED with the
    prohibition named as its reason, and no answer receipt exists for it."""
    with _SocketSentinel() as sentinel:
        _answer, context = _turn(harness, _PARTIAL, workspace)

    assert sentinel.attempts == []
    dispatches = _dispatches(context)
    refused_dispatches = [row for row in dispatches if row.get("state") == "REFUSED"]
    assert refused_dispatches, f"no REFUSED dispatch row: {dispatches}"
    assert all(
        "prohibit" in str(row.get("failure_reason") or "").lower()
        or "forbid" in str(row.get("failure_reason") or "").lower()
        or "conserv" in str(row.get("failure_reason") or "").lower()
        for row in refused_dispatches
    ), dispatches
    # And the executed read keeps its own honest state.
    assert any(row.get("state") == "SUCCEEDED" for row in dispatches), dispatches


# =================================================== 3. ordering independence


def test_3_prohibition_after_the_demand_conserves_identically(harness, workspace):
    """The prohibition may trail the demand; conservation is a property of the
    turn, not of clause order."""
    with _SocketSentinel() as sentinel:
        answer, _context = _turn(harness, _REORDERED, workspace)

    assert sentinel.attempts == [], sentinel.attempts[:6]
    assert _ETH_PRICE_TEXT not in answer


def test_3_a_differently_worded_prohibition_conserves(harness, workspace):
    """CONTROL. "without using the web" trailing the demand rides the SAME
    slice, so the child's own text still carries it and no socket opens — the
    vocabulary is the constraint authority's, and every form of it conserves.
    (The incident shape is the prohibition sitting in a DIFFERENT slice.)"""
    with _SocketSentinel() as sentinel:
        answer, _context = _turn(
            harness,
            "Read notes.txt and give me the current ETH price without using the web.",
            workspace,
        )

    assert sentinel.attempts == [], sentinel.attempts[:6]
    assert _ETH_PRICE_TEXT not in answer


# ================================================ 4. planner child / retry / resume


def test_4_a_planner_child_inherits_the_parent_prohibitions(harness, workspace):
    """A child dispatched by the PLANNER machinery (the same run_one the
    conductor and planned turns use) runs under the parent's canonical request;
    its lanes must intersect, not re-derive policy from the clean slice."""
    from core.agent_runtime.turn_planner import PlannedTask
    from core.agent_runtime.turn_planner_hook import build_planner_run_one

    parent_text = "No web. Tell me the current ETH price."
    request = TurnRequest.from_ingress(
        user_text=parent_text,
        source_context=dict(_SOURCE_CONTEXT),
        request_id="req-pc-planner",
        turn_id="turn-pc-planner",
        session_id=harness.session_id,
    )
    context = dict(_SOURCE_CONTEXT)
    context[TURN_REQUEST_KEY] = request
    context["workspace"] = workspace
    run_one = build_planner_run_one(
        harness.agent, session_id=harness.session_id, source_context=context
    )
    task = PlannedTask(index=0, request="Tell me the current ETH price.")

    with _SocketSentinel() as sentinel:
        with _model_stand_in(harness.agent):
            child_result = run_one(task, {})

    assert sentinel.attempts == [], sentinel.attempts[:6]
    child_payload = child_result if isinstance(child_result, dict) else {}
    child_text = str(child_payload.get("response") or child_result or "")
    assert _ETH_PRICE_TEXT not in str(child_text), (
        f"the planner child fetched a live value the parent forbade: {child_text!r}"
    )


def test_4_a_rerun_of_the_same_turn_conserves_again(harness, workspace):
    """Retry: the same turn text re-run conserves identically — the mint is
    derived from the request text, so re-entry cannot lose it."""
    for _round in range(2):
        with _SocketSentinel() as sentinel:
            answer, context = _turn(harness, _INCIDENT, workspace)
        assert sentinel.attempts == [], sentinel.attempts[:6]
        assert any(
            row["terminal_state"] == _refused_state() for row in _ledger(context)
        ), f"round {_round}: {answer!r}"


def test_4_a_resumed_request_carries_the_original_prohibitions(harness, workspace):
    """Resume: "continue" re-runs a STORED request. The literal message carries
    no prohibition, so the restored request's constraints must be re-derived
    from the restored text and published on the canonical request the children
    inherit."""
    from core.turn_prohibitions import prohibitions_from_text

    restored_text = _INCIDENT
    # The seam the resume arm uses: the canonical request minted for the literal
    # "continue" must be re-frozen with the RESTORED request's prohibitions,
    # keeping every identity field.
    literal = TurnRequest.from_ingress(
        user_text="continue",
        source_context=dict(_SOURCE_CONTEXT),
        request_id="req-pc-resume",
        turn_id="turn-pc-resume",
        session_id="sess-pc-resume",
    )
    from core.agent_runtime.agent import conserve_request_for_resume

    resumed = conserve_request_for_resume(literal, restored_text)

    assert resumed.prohibitions == prohibitions_from_text(restored_text)
    assert resumed.prohibitions.families, "the restored prohibition was lost on resume"
    # Identity conservation: same request, same turn, same session.
    assert (resumed.request_id, resumed.turn_id, resumed.session_id) == (
        literal.request_id,
        literal.turn_id,
        literal.session_id,
    )


# =========================================== 5. quoted text is not an instruction


def test_5_quoted_no_web_creates_no_false_prohibition(harness, workspace):
    """CONTROL (guards the new machinery). The sign said "no web" — quoted words
    are evidence about the world, not an instruction to this runtime. The ETH
    demand beside them must still execute (against the canned quote mock)."""
    message = (
        'The sign said "no web". Read notes.txt and give me the current ETH price.'
    )
    with _SocketSentinel():
        with _eth_quote_mock():
            answer, context = _turn(harness, message, workspace)

    rows = _ledger(context)
    assert not any(row["terminal_state"] == _refused_state() for row in rows), rows
    assert _LINE_ONE in answer, f"the permitted read did not execute: {answer!r}"


# ============================================= 6. a child cannot widen the parent


def test_6_a_child_demanding_web_cannot_widen_the_parent(harness, workspace):
    """The child slice ASKS for the web in its own words ("fetch ... online").
    The intersection is parent authority ∩ child capability — the child's own
    request grants nothing the parent forbade."""
    with _SocketSentinel() as sentinel:
        answer, context = _turn(harness, _WIDENING, workspace)

    assert sentinel.attempts == [], sentinel.attempts[:6]
    rows = _ledger(context)
    refused = [row for row in rows if row["terminal_state"] == _refused_state()]
    assert refused, rows
    assert any("price" in row["request"] or "ETH" in row["request"] for row in refused), rows
    assert _ETH_PRICE_TEXT not in answer
    # The permitted read beside it still executed.
    assert _LINE_ONE in answer, answer


def test_6_toolset_grain_refuses_only_what_the_parent_froze(harness, workspace):
    """A market-prices-ONLY prohibition (not the whole web family): the price
    child is refused at toolset grain, while the unrelated local read in the
    same turn executes — conservation narrows exactly what the parent froze."""
    message = (
        "Don't look up market prices. Read notes.txt. "
        "Also tell me the current ETH price."
    )
    with _SocketSentinel() as sentinel:
        answer, context = _turn(harness, message, workspace)

    assert sentinel.attempts == [], sentinel.attempts[:6]
    rows = _ledger(context)
    refused = [row for row in rows if row["terminal_state"] == _refused_state()]
    assert refused and any(
        "price" in row["request"] or "ETH" in row["request"] for row in refused
    ), rows
    assert _LINE_ONE in answer, f"the permitted read did not execute: {answer!r}"
    assert _ETH_PRICE_TEXT not in answer


def test_6_toolset_grain_does_not_over_block_other_families(harness, workspace):
    """The other half of toolset grain: under a market-prices-only prohibition
    a WEATHER child still runs (canned observation) — the intersection refuses
    the frozen family, never the child's whole capability."""
    from tests.test_turn_attempt_chain import _incident_mocks

    message = (
        "Don't look up market prices. Read notes.txt. "
        "Also tell me the current weather in Kaunas."
    )
    with _SocketSentinel() as sentinel:
        with _incident_mocks():
            answer, context = _turn(harness, message, workspace)

    assert sentinel.attempts == [], sentinel.attempts[:6]
    assert "28" in answer, f"the weather demand did not execute: {answer!r}"
    assert not any(
        row["terminal_state"] == _refused_state() for row in _ledger(context)
    ), _ledger(context)


# ============================================ 7. children share the parent digest


def test_7_children_inherit_identical_constraint_digest_and_identity(harness, workspace):
    """Every child of one turn runs under the SAME canonical request: identical
    prohibition digest, identical request/turn/session identity. Parallel
    children cannot disagree about what the parent forbade."""
    from core.turn_prohibitions import prohibitions_from_text

    parent_text = "No web. Return exactly the first line of notes.txt. Calculate 37 x 19."
    observed: list[tuple[str, TurnRequest]] = []
    real_inner = harness.agent._run_once_inner

    def _capture_inner(user_input, *, session_id_override=None, source_context=None, **kwargs):
        context = source_context or {}
        if context.get("planned_subturn"):
            observed.append(("child", kwargs.get("turn_request")))
        return real_inner(
            user_input,
            session_id_override=session_id_override,
            source_context=source_context,
            **kwargs,
        )

    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with mock.patch.object(harness.agent, "_run_once_inner", _capture_inner):
        with _model_stand_in(harness.agent):
            harness.agent.run_once(
                parent_text,
                source_context=context,
                session_id_override=harness.session_id,
            )

    assert len(observed) >= 2, f"expected the turn to dispatch at least two children: {observed}"
    digests = {req.prohibitions.digest for _kind, req in observed}
    identities = {
        (req.request_id, req.turn_id, req.session_id) for _kind, req in observed
    }
    assert len(digests) == 1, f"children saw different parent constraints: {digests}"
    assert len(identities) == 1, f"children lost the canonical identity: {identities}"
    # And that digest is the one minted from the PARENT text, not the slices.
    expected = prohibitions_from_text(parent_text)
    assert digests == {expected.digest}
    assert expected.families, "the mint found no prohibition in the parent text"


def test_7_the_digest_is_semantic_not_verbal(harness, workspace):
    """"No web." and "Don't search the web." are the same constraint and must
    carry the same digest — the digest identities the AUTHORITY, not the
    wording."""
    from core.turn_prohibitions import prohibitions_from_text

    assert (
        prohibitions_from_text("No web.").digest
        == prohibitions_from_text("Don't search the web.").digest
        == prohibitions_from_text("You are strictly forbidden from using the internet.").digest
    )


# ================================== the canonical mint and the intersection unit tests


def test_the_mint_freezes_families_toolsets_and_reason_codes():
    from core.turn_prohibitions import prohibitions_from_text

    frozen = prohibitions_from_text("No web. Tell me the current ETH price.")
    assert frozen.families == frozenset({"web"}), frozen
    assert "explicit_retrieval_prohibition" in frozen.reason_codes, frozen
    assert frozen.clauses, "the user's own negative clause is the evidence"


def test_the_mint_is_quote_aware():
    from core.turn_prohibitions import prohibitions_from_text

    quoted = prohibitions_from_text('He said "no web" and left.')
    assert not quoted.families, quoted
    assert not quoted.prohibited_toolsets, quoted


def test_the_mint_keeps_contractions_real_prohibitions():
    """"don't" and "it's" are not quote delimiters; a real prohibition spoken
    with contractions still freezes."""
    from core.turn_prohibitions import prohibitions_from_text

    assert "web" in prohibitions_from_text(
        "I don't want the web, so don't search the web for it. It's fine."
    ).families


def test_the_intersection_never_widens():
    """The law, at the unit seam: parent ∪ child-text constraints — a child can
    add prohibitions, never remove the parent's."""
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.turn_prohibitions import conserve_retrieval_constraints, prohibitions_from_text

    parent = prohibitions_from_text("No web. Tell me the current ETH price.")
    child_text = "Tell me the current ETH price."
    context = {TURN_REQUEST_KEY: TurnRequest(
        request_id="r", turn_id="t", session_id="s", user_text="No web. Tell me the current ETH price.",
        prohibitions=parent,
    )}
    conserved = conserve_retrieval_constraints(
        analyze_retrieval_constraints(child_text), context
    )
    assert conserved.forbids_external_retrieval, (
        "the intersection dropped the parent's web prohibition"
    )
    # Idempotent on the parent's own text: conserving the parent changes nothing.
    own = analyze_retrieval_constraints("No web. Tell me the current ETH price.")
    assert conserve_retrieval_constraints(own, context).forbids_external_retrieval


def test_child_policy_refuses_every_family_a_parent_can_freeze():
    """The monotonic law for all five families: web, tools, write, spend,
    local_model. A child demand needing a frozen family's capability is
    refused; the refusal carries the parent's reason codes plus the
    conservation marker."""
    from core.turn_prohibitions import (
        FAMILY_LOCAL_MODEL,
        FAMILY_SPEND,
        FAMILY_TOOLS,
        FAMILY_WEB,
        FAMILY_WRITE,
        REASON_CONSERVED,
        child_demand_policy,
        prohibitions_from_text,
    )

    web = prohibitions_from_text("No web. " + "Tell me the current ETH price.")
    tools = prohibitions_from_text("Do not use any tools. Tell me the weather in Kaunas.")
    write = prohibitions_from_text("No writes. Read notes.txt and append DONE.")
    spend = prohibitions_from_text("No spending. Translate this paragraph.")
    local = prohibitions_from_text("No local model. Summarize notes.txt.")

    assert FAMILY_WEB in web.families
    refused = child_demand_policy(web, "live_data", "Tell me the current ETH price.")
    assert not refused.allowed and REASON_CONSERVED in refused.reason_codes, refused

    assert FAMILY_TOOLS in tools.families
    assert not child_demand_policy(tools, "live_data", "Tell me the weather in Kaunas.").allowed
    assert not child_demand_policy(tools, "currency", "Convert 100 USD to EUR.").allowed

    assert FAMILY_WRITE in write.families
    assert not child_demand_policy(write, "workspace_write", "Append DONE to notes.txt.").allowed

    assert FAMILY_SPEND in spend.families
    assert not child_demand_policy(spend, "paid_cloud", "Translate this paragraph.").allowed

    assert FAMILY_LOCAL_MODEL in local.families
    assert not child_demand_policy(local, "model_reasoning", "Summarize notes.txt.").allowed

    # And the capabilities the prohibition does NOT touch stay free.
    assert child_demand_policy(web, "workspace_read", "Read notes.txt.").allowed
    assert child_demand_policy(web, "arithmetic", "Calculate 37 x 19.").allowed


def test_child_policy_honours_toolset_grain_for_live_data():
    """A market-prices-only prohibition refuses the price demand and leaves a
    weather demand free — conservation narrows exactly what the parent froze."""
    from core.turn_prohibitions import child_demand_policy, prohibitions_from_text

    prices_only = prohibitions_from_text(
        "Don't look up market prices. Tell me the weather in Kaunas."
    )
    assert not prices_only.prohibits_family("web"), prices_only
    assert not child_demand_policy(
        prices_only, "live_data", "Tell me the current ETH price."
    ).allowed
    assert child_demand_policy(
        prices_only, "live_data", "Tell me the current weather in Kaunas."
    ).allowed


def test_the_canonical_request_freezes_prohibitions_at_ingress():
    """from_ingress mints the frozen constraint set from the whole user text —
    the single constructor every surface shares is the propagation seam."""
    request = TurnRequest.from_ingress(
        user_text="No web. Tell me the current ETH price.",
        source_context={"surface": "openclaw"},
        request_id="r",
        turn_id="t",
        session_id="s",
    )
    assert request.prohibitions.families == frozenset({"web"})
    assert request.prohibitions.digest


def test_the_projection_roundtrip_ignores_derived_prohibitions():
    """The ingress-compat projection stays identity-shaped: prohibitions are
    derived truth frozen on the typed object, and `TurnRequest(**to_dict())`
    must still equal the original."""
    request = TurnRequest.from_ingress(
        user_text="No web. Tell me the current ETH price.",
        source_context={},
        request_id="r",
        turn_id="t",
        session_id="s",
    )
    assert TurnRequest(**request.to_dict()) == request


def test_a_sanitized_request_copy_keeps_the_frozen_prohibitions():
    """The secret-intake sanitized copy replaces user_text only; the parent's
    frozen constraints ride along — redaction may not widen authority."""
    from dataclasses import replace

    request = TurnRequest.from_ingress(
        user_text="No web. cloud key sk-live-123 and give me the ETH price.",
        source_context={},
        request_id="r",
        turn_id="t",
        session_id="s",
    )
    sanitized = replace(request, user_text="and give me the ETH price.")
    assert sanitized.prohibitions is request.prohibitions


def test_requirements_for_intersects_the_parent_prohibitions():
    """The requirements authority is the lane gate: a clean child slice under a
    prohibited parent must read as retrieval-prohibited — the same typed
    contract the whole-turn boundary produces, reason codes conserved."""
    from core.execution_requirements import requirements_for

    parent = TurnRequest.from_ingress(
        user_text="No web. Tell me the current ETH price.",
        source_context={},
        request_id="r",
        turn_id="t",
        session_id="s",
    )
    conserved = requirements_for(
        "Tell me the current ETH price.",
        source_context={TURN_REQUEST_KEY: parent},
    )
    assert conserved.answer_mode != "LIVE_DATA", conserved
    assert not conserved.allowed_toolsets, conserved
    assert "explicit_retrieval_prohibition" in conserved.reason_codes, conserved

    # The same slice WITHOUT the parent reads as the live demand it is.
    free = requirements_for("Tell me the current ETH price.", source_context={})
    assert free.answer_mode == "LIVE_DATA", free


def test_the_fetch_door_intersects_the_parent_prohibitions():
    """remote_fetch_allowed_by_context is the caller-side fetch boundary; a
    child of a prohibited parent may not pass it, whatever lane asks."""
    from core.remote_fetch_policy import remote_fetch_allowed_by_context

    parent = TurnRequest.from_ingress(
        user_text="No web. Tell me the current ETH price.",
        source_context={"surface": "openclaw"},
        request_id="r",
        turn_id="t",
        session_id="s",
    )
    assert remote_fetch_allowed_by_context(
        {"surface": "openclaw", TURN_REQUEST_KEY: parent}
    ) is False
    # Without the prohibition the same surface is fetch-permitted (live lanes work).
    assert remote_fetch_allowed_by_context({"surface": "openclaw"}) is True


# ============================================ coverage sanity for the incident shapes


def test_the_incident_shapes_are_mixed_turns():
    """CONTROL. The incident messages mint multiple units with a retrieval
    claimant — they are exactly the shape the demand-owned executor claims, so
    the conservation seam above is the one that serves them."""
    for text in (
        _INCIDENT,
        _PARTIAL,
        _WIDENING,
        "Don't look up market prices. Read notes.txt. Also tell me the current ETH price.",
    ):
        coverage = demand_coverage(text)
        assert coverage.unit_count >= 2, (text, coverage.units)
        assert any(lanes for lanes in coverage.per_unit_lanes), (
            text,
            coverage.per_unit_lanes,
        )
