"""P0 — a composite turn's backed blocks must survive the authorship publication gate.

THE MEASURED DEFECT (base 84bf8b6a + the M2 milestone, Served Reality bench)
---------------------------------------------------------------------------
The demand-owned seam executed the bench incident's units — the clock read, the
conversion attempt — recorded them satisfied (census minted 3 / satisfied 2),
composed the reply… and the SERVED bytes were the authorship refusal:

    "I can't publish this answer: nothing this turn retrieved, computed or
     observed backs it… `vllm-local:served-reality-stub` is not certified to
     author… the call was never made…"

Three breaks stacked:

1. The parent turn's authorship record was written BEFORE any unit ran (the
   router's pre-call block), so it described a lane that came back empty.
2. `AuthorshipRecord.must_refuse` refused on exactly that stale premise — its
   own docstring says backed bytes are "decided per claim by
   `authored_publication_verdict`, not here", but the third branch never looked
   at `supported_by_runtime`.
3. `gate_authored_content` only offered the claim-adjudicated path to
   MODEL-AUTHORED records, so runtime-composed backed bytes could not reach
   the adjudicator at all.

THE CONTRACT UNDER TEST
-----------------------
A composite turn whose units the runtime executed mints one support row per
executed unit; a pre-generation block may not refuse bytes those rows back; the
per-claim adjudicator decides what ships, line by line. A blocked lane with NO
runtime support keeps the pre-existing refusal — the change may not launder
genuinely empty turns.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from core import final_answer_authorship as authorship
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

_MIXED = (
    "Three things: what time is it in Tokyo? Convert 100 US dollars to euros. "
    "And finish with a two-word joke."
)

_CREATIVE = "CREATIVE-STAND-IN: rest, tiny joke."

#: The turn identity the record indexes under; the publication gate presents
#: the same turn id finalization hands it.
_TURN_ID = "openclaw:auth-p0-turn"
_REQUEST_ID = "req:auth-p0-turn"


def _gate_context(tmp_path) -> dict:
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = str(tmp_path)
    context["turn_id"] = _TURN_ID
    context["request_id"] = _REQUEST_ID
    return context


def _model_stand_in(agent, text: str = _CREATIVE):
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model",
        task_hash="p0-authorship",
        provider_id="p0-authorship",
        provider_name="P0 stand-in",
        model_name="p0-authorship",
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


def _blocked_manifest():
    """The uncertified loopback candidate the router was about to call.

    A real manifest with loopback runtime metadata so `certification_applies`
    reads True and no certification run exists for it — the same shape
    `tests/test_final_answer_authorship_served_p0.py` seeds its uncertified
    probe with. `provider_id` is whatever the router would pass on.
    """
    from storage.model_provider_manifest import ModelProviderManifest

    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name="probe-uncertified:2b",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize"],
        runtime_config={"base_url": "http://127.0.0.1:9", "timeout_seconds": 5},
        metadata={
            "runtime_family": "ollama",
            "cost_class": "free_local",
            "model_digest": "sha256:stub",
            "chat_template_hash": "tmpl",
            "quantization": "q4_K_M",
            "parameter_billions": 2.0,
        },
    )


def _record_precall_block(source_context) -> None:
    """Exactly what the router does when the only candidate may not author."""
    manifest = _blocked_manifest()
    verdict = authorship.precall_author_verdict(
        manifest=manifest,
        source_context=source_context,
        request_text=_MIXED,
    )
    assert verdict is not None and not verdict.eligible
    authorship.record_authorship_decision(
        source_context,
        verdict,
        blocked_model=str(getattr(manifest, "provider_id", "") or ""),
    )


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-p0auth-{request.node.name[:38]}")
    try:
        yield h
    finally:
        h.close()


def _turn(harness: _Harness, workspace: str) -> tuple[str, dict]:
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with _model_stand_in(harness.agent):
        result = harness.agent.run_once(
            _MIXED, source_context=context, session_id_override=harness.session_id
        )
    return str(result.get("response") or ""), context


def test_backed_composite_blocks_publish_beside_a_precall_block(harness, tmp_path):
    """THE INCIDENT. The router blocked the only author BEFORE any unit ran; the
    seam then executed the units. The publication gate must not refuse the
    composed reply on the stale 'lane came back empty' premise — the backed
    blocks ship, per-claim adjudicated."""
    authorship.reset_for_tests()
    context = _gate_context(tmp_path)
    # The router's pre-call block, exactly as the router records it.
    _record_precall_block(context)
    record = authorship.authorship_record_for_publication(turn_id=_TURN_ID)
    assert record is not None and record.must_refuse, (
        "control failed: the pre-call block alone must refuse (pre-existing law)"
    )

    with _model_stand_in(harness.agent):
        result = harness.agent.run_once(
            _MIXED, source_context=context, session_id_override=harness.session_id
        )
    composed = str(result.get("response") or "")
    assert "JST" in composed, f"the composed reply lost the clock unit: {composed!r}"

    published, payload = authorship.gate_authored_content(composed, turn_id=_TURN_ID)
    assert "JST" in published, (
        f"the publication gate refused the backed clock block: {published!r} "
        f"({payload.get('publication')})"
    )
    assert payload.get("publication") != "refused", payload


def test_a_blocked_lane_with_no_runtime_support_still_refuses(harness, tmp_path):
    """The negative control. The same pre-call block on a turn the runtime did
    NOT back (no executed units, no support rows) keeps the refusal — the
    repair may not launder a genuinely empty lane's composition."""
    authorship.reset_for_tests()
    context = _gate_context(tmp_path)
    _record_precall_block(context)

    published, payload = authorship.gate_authored_content(
        "Some composed text.", turn_id=_TURN_ID
    )
    assert payload.get("publication") == "refused", payload
    assert "not certified to author" in published


def test_record_runtime_support_is_raise_only_and_claims_no_author(harness, tmp_path):
    """The seam's helper writes rows and support; it may not flip an authorship
    decision or mint a record where no authorship question exists."""
    authorship.reset_for_tests()
    context = _gate_context(tmp_path)
    # No record exists: nothing to defend, nothing minted.
    assert authorship.record_runtime_support(
        context, support_rows=[{"summary": "x"}]
    ) is False
    _record_precall_block(context)
    before = authorship.authorship_record_for_publication(turn_id=_TURN_ID)
    decision_before = before.decision
    assert authorship.record_runtime_support(
        context, support_rows=[{"summary": "Current time in Tokyo is 01:31 JST."}]
    )
    after = authorship.authorship_record_for_publication(turn_id=_TURN_ID)
    assert after.decision is decision_before, "the helper changed the decision"
    assert after.supported_by_runtime and after.support_rows
    assert not after.must_refuse, (
        "backed rows present but the record still refuses wholesale"
    )
