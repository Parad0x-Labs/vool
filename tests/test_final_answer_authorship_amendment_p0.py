"""P0 AMENDMENT — authorship must fail CLOSED, and it must decide BEFORE the spend.

The first pass installed the authority and the publication backstop. Four things were left
open, and each of them is a way the boundary can be walked past:

1. **The identity path failed OPEN.** A served model response whose provider id resolved to no
   configured manifest was waved through with the typed reason `author_identity_unresolved`.
   The reasoning was availability -- a registry read that fails must not refuse every answer in
   the process. But "we could not tell who wrote this" is the one state that must never publish
   model-written bytes, and a deterministic answer has a POSITIVE way to say what it is. It does
   not need to borrow the unknown-identity exit.

2. **Eligibility was decided AFTER the call.** The refusal was correct and the input was already
   spent: an uncertified model wrote a full answer, and only then was it told it may not. The
   decision belongs in front of the provider call, at the seam every invocation crosses.

3. **A tool result blessed the whole answer.** `supported_by_runtime` asked whether the turn
   observed anything, not whether THESE bytes follow from it -- so one supported line let an
   unsupported sentence ride out beside it.

4. **No served proof.** Everything was in-process. The HTTP door was asserted, never driven.

This pack is (1), (2) and (3) through the real turn spine. (4) is
`tests/test_final_answer_authorship_served_p0.py`, which drives a daemon over the wire.
"""
from __future__ import annotations

from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from tests.test_final_answer_author_eligibility_p0 import (
    _RUNTIME_IDENTITY,
    _certify,
    _cloud_manifest,
    _local_manifest,
    _register,
    _serve,
)

DIRECT_REQUEST = "Explain in two sentences how a Bloom filter can report a false positive."

MODEL_ANSWER = (
    "A Bloom filter reports a false positive when every bit position a query key hashes to was "
    "already set by other insertions. The structure therefore admits false positives but never "
    "false negatives."
)
MODEL_FINGERPRINT = "never false negatives"


@pytest.fixture(autouse=True)
def _clean_authorship_state():
    from core.final_answer_authorship import reset_for_tests
    from storage.db import get_connection

    def _clear() -> None:
        reset_for_tests()
        try:
            conn = get_connection()
        except Exception:
            return
        try:
            for statement in (
                "DELETE FROM model_provider_manifests",
                "DELETE FROM local_model_tool_certification_runs",
            ):
                try:
                    conn.execute(statement)
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    _clear()
    with mock.patch(
        "adapters.openai_compatible_adapter.OpenAICompatibleAdapter"
        ".tool_certification_runtime_identity",
        return_value=dict(_RUNTIME_IDENTITY),
    ):
        yield
    _clear()


def _agent() -> VoolAgent:
    from core.runtime_continuity import reset_runtime_continuity_state

    reset_runtime_continuity_state()
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


class _StubAdapter:
    def __init__(self, manifest):
        self.manifest = manifest

    def get_license_metadata(self) -> dict:
        return {
            "license_name": str(getattr(self.manifest, "license_name", "") or ""),
            "license_reference": str(getattr(self.manifest, "license_reference", "") or ""),
        }


class _Spend:
    """Counts what actually reached a provider, per manifest.

    The point of the pre-call fence is that an ineligible author is refused BEFORE its adapter is
    built and before the execution boundary is entered. Both are counted, because building the
    adapter is already a health probe against the endpoint.
    """

    def __init__(self, by_provider: dict[str, str] | None = None, default: str = MODEL_ANSWER):
        self.built: list[str] = []
        self.invoked: list[str] = []
        self.by_provider = dict(by_provider or {})
        self.default = default

    def build_adapter(self, manifest):
        self.built.append(str(getattr(manifest, "provider_id", "") or ""))
        return _StubAdapter(manifest)

    def boundary(self, adapter, method, *args, **kwargs):
        provider_id = str(getattr(adapter.manifest, "provider_id", "") or "")
        if method == "health_check":
            return {"ok": True}
        self.invoked.append(provider_id)
        from adapters.base_adapter import ModelResponse
        from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

        text = self.by_provider.get(provider_id, self.default)
        request = args[0] if args else kwargs.get('request')
        if AMBIGUITY_SYSTEM_PROMPT in str(getattr(request, 'system_prompt', '') or ''):
            text = '{"ambiguous": false, "referents": [], "clarification": ""}'

        return ModelResponse(
            output_text=text,
            confidence=0.82,
            usage={"prompt_eval_count": 120, "eval_count": 48},
            provider_id=provider_id,
            model_name=str(getattr(adapter.manifest, "model_name", "") or ""),
            output_mode="plain_text",
            provider_attested_model=str(getattr(adapter.manifest, "model_name", "") or ""),
            finish_reason="stop",
        )


def _drive_through_the_real_seam(
    session_id: str,
    *,
    request: str = DIRECT_REQUEST,
    candidates: list,
    spend: _Spend | None = None,
    context_extra: dict | None = None,
) -> tuple[dict, dict, _Spend]:
    """One real turn with `_invoke_manifest` INTACT.

    The eligibility fence lives inside `_invoke_manifest`, beside the Local Only and paid-call
    gates, so a rig that replaces that method cannot see it. Only the two things past the fence
    are replaced here -- building the adapter and entering the execution boundary -- which is
    also exactly what "was the call spent?" means.
    """

    agent = _agent()
    spend = spend or _Spend()
    with (
        mock.patch(
            "core.model_registry.ModelRegistry.build_adapter", side_effect=spend.build_adapter
        ),
        mock.patch(
            "core.memory_first_router.invoke_provider_execution_boundary",
            side_effect=spend.boundary,
        ),
        mock.patch(
            "core.memory_first_router.rank_provider_candidates", return_value=list(candidates)
        ),
    ):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": f"turn-{session_id}",
            "request_id": f"req-{session_id}",
            **(context_extra or {}),
        }
        result = agent.run_once(request, source_context=context, session_id_override=session_id)
    return result, context, spend


# --------------------------------------------------------------- A. fail CLOSED on identity


def test_a_served_model_response_with_no_resolvable_manifest_refuses():
    """The fail-open path, closed. An answer whose author cannot be named is an answer nobody
    can vouch for, and it may not ship as though someone had."""

    from core.final_answer_authorship import (
        UNCERTIFIED_AUTHOR_NOTICE_LEAD,
        authorship_record_for_publication,
    )
    from core.memory_first_router import ModelExecutionDecision
    from core.turn_model_call_ledger import record_provider_call, record_served_usage

    agent = _agent()

    def _resolve(**kwargs):
        call_context = kwargs.get("source_context")
        record_provider_call(
            call_context, provider_id="ghost-provider", model_id="ghost", cost_class="free_local"
        )
        record_served_usage(call_context, {"provider_id": "ghost-provider", "model_id": "ghost"})
        return ModelExecutionDecision(
            source="model",
            task_hash="ghost",
            provider_id="ghost-provider",
            provider_name="ghost",
            model_name="ghost",
            output_text=MODEL_ANSWER,
            confidence=0.8,
            trust_score=0.8,
            used_model=True,
        )

    with mock.patch.object(agent.memory_router, "resolve", side_effect=_resolve):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": "turn-ghost",
            "request_id": "req-ghost",
        }
        result = agent.run_once(
            DIRECT_REQUEST, source_context=context, session_id_override="sess-ghost"
        )
    served = _serve(context, result)

    assert MODEL_FINGERPRINT not in served, (
        "an answer by an unnameable model shipped: " + served[:400]
    )
    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in served, served[:400]
    record = authorship_record_for_publication(turn_id="turn-ghost")
    assert record is not None, "the turn recorded no authorship decision"
    payload = record.to_dict()
    assert payload["eligible"] is False, payload
    # The REASON, not just the refusal. Restoring the fail-open branch still leaves this turn
    # refused further down (an author with no manifest is also an author with no certification),
    # so a test that only asserts "it refused" does not name the branch it exists for and a
    # sabotage of that branch walks past it green.
    assert payload["reason"] == "author_identity_unresolved", payload
    assert payload["selected_model"] == "", payload


def test_a_registry_failure_refuses_rather_than_publishing():
    """Availability was the argument for failing open. It is the wrong trade here: a storage
    error is exactly when the runtime knows least about who is writing."""

    from core.final_answer_authorship import decide_final_answer_author

    local = _local_manifest()
    _register(local)
    with mock.patch(
        "core.model_registry.ModelRegistry.list_manifests",
        side_effect=RuntimeError("registry unavailable"),
    ):
        from core.turn_model_call_ledger import _served_manifest

        assert _served_manifest(local.provider_id) is None

    decision = decide_final_answer_author(
        request_text=DIRECT_REQUEST, requested_manifest=None, requested_model=local.provider_id
    )
    assert decision.eligible is False, decision
    assert decision.reason == "author_identity_unresolved", decision


def test_a_served_response_with_no_provider_id_at_all_refuses():
    """Ambiguity is not a licence either. An empty identity is the same unknown as a wrong one."""

    from core.final_answer_authorship import decide_final_answer_author

    decision = decide_final_answer_author(
        request_text=DIRECT_REQUEST, requested_manifest=None, requested_model=""
    )
    assert decision.eligible is False, decision


def test_deterministic_output_publishes_through_an_explicit_role_not_through_the_unknown_exit():
    """A deterministic answer says what it IS. It must not reach the wire by being mistaken for
    a model whose identity could not be read -- those are different facts and only one of them
    is safe."""

    from core.final_answer_authorship import AuthorRole, decide_final_answer_author

    decision = decide_final_answer_author(
        request_text="What is 37 * 19?",
        author_role=AuthorRole.DETERMINISTIC,
        requested_manifest=None,
        requested_model="",
    )
    assert decision.eligible is True, decision
    assert decision.author_role == str(AuthorRole.DETERMINISTIC.value), decision
    assert decision.reason != "author_identity_unresolved", decision


def test_a_deterministic_turn_still_ships_end_to_end():
    """The control for the rule above, through the real spine."""

    local = _local_manifest()
    _register(local)
    result, context, spend = _drive_through_the_real_seam(
        "sess-amend-det", request="What is 37 * 19?", candidates=[local]
    )
    served = _serve(context, result)
    assert not spend.invoked, f"an arithmetic turn spent a model call: {spend.invoked}"
    assert "703" in served, served[:300]


# ------------------------------------------------------- B. decide BEFORE the model is called


def test_an_ineligible_author_is_refused_before_its_adapter_is_ever_built():
    """The whole point of the amendment. No adapter, no health probe, no generation -- the
    prohibited call is not made, not made-and-then-discarded."""

    local = _local_manifest()
    _register(local)
    _result, _context, spend = _drive_through_the_real_seam(
        "sess-amend-precall", candidates=[local]
    )
    assert local.provider_id not in spend.invoked, (
        f"the prohibited generation was spent: {spend.invoked}"
    )
    assert local.provider_id not in spend.built, (
        f"an ineligible author still had its adapter built: {spend.built}"
    )


def test_the_certified_author_is_the_one_that_is_called():
    """Escalation before spend: the uncertified candidate is refused at the seam and the
    certified one writes, in the same turn, without the first call happening."""

    uncertified = _local_manifest(model_name="probe-uncertified:8b")
    certified = _local_manifest(model_name="probe-certified:8b")
    _register(uncertified, certified)
    _certify(certified, state="verified")
    certified_answer = (
        "A Bloom filter can report a member that was never inserted, because independent keys "
        "share bit positions. It cannot report a non-member for a key it did hold."
    )
    spend = _Spend(by_provider={certified.provider_id: certified_answer})

    result, context, spend = _drive_through_the_real_seam(
        "sess-amend-escalate", candidates=[uncertified, certified], spend=spend
    )
    served = _serve(context, result)

    assert uncertified.provider_id not in spend.invoked, (
        f"the uncertified author was still called: {spend.invoked}"
    )
    assert certified.provider_id in spend.invoked, (
        f"the certified author was never called: {spend.invoked}"
    )
    assert "never inserted" in served, served[:300]

    from core.final_answer_authorship import authorship_record_for_publication

    payload = authorship_record_for_publication(
        turn_id=str(context["cancel_turn_id"])
    ).to_dict()
    assert payload["selected_model"] == certified.provider_id, payload
    assert payload["eligible"] is True, payload


def test_no_eligible_author_refuses_without_spending_anything():
    """Local Only, one uncertified local model, a configured cloud model that may not be
    reached. The turn refuses and NOTHING was called."""

    local = _local_manifest()
    cloud = _cloud_manifest()
    _register(local, cloud)
    _certify(cloud, state="verified")
    result, context, spend = _drive_through_the_real_seam(
        "sess-amend-nospend",
        candidates=[local],
        context_extra={"local_only_mode": True},
    )
    served = _serve(context, result)

    assert not spend.invoked, f"a refused turn still spent a call: {spend.invoked}"
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in served, served[:400]


def test_finalization_remains_an_independent_backstop():
    """Two fences, not one. With the pre-call fence disabled, the publication gate must still
    refuse -- otherwise the amendment has traded a backstop for a front door."""

    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    local = _local_manifest()
    _register(local)
    with mock.patch(
        "core.final_answer_authorship.precall_author_verdict", return_value=None
    ):
        result, context, spend = _drive_through_the_real_seam(
            "sess-amend-backstop", candidates=[local]
        )
        served = _serve(context, result)
    assert spend.invoked, "the fence was not actually disabled; this proves nothing"
    assert MODEL_FINGERPRINT not in served, (
        "with the pre-call fence off, the backstop let the bytes through: " + served[:400]
    )
    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in served, served[:400]


# ------------------------------------- C. a tool result does not bless the prose beside it


def test_a_supported_tool_result_does_not_carry_an_unsupported_sentence_with_it():
    """`supported_by_runtime` was a whole-answer verdict taken from a per-turn fact. The
    existing claim-to-source authority is per CLAIM, and that is the one that must decide:
    the line the observation supports ships, the invented one beside it does not."""

    from core.final_answer_authorship import (
        AuthorRole,
        authored_publication_verdict,
    )

    observations = [
        {
            "intent": "workspace.read_file",
            "summary": "config.toml sets retries = 5 and timeout_seconds = 30.",
            "source_label": "config.toml",
        }
    ]
    content = (
        "config.toml sets retries = 5 and timeout_seconds = 30.\n"
        "It also sets max_connections = 512 against the eu-west-2 pool."
    )
    verdict = authored_publication_verdict(
        content,
        request_text="what retry settings are in config.toml?",
        observations=observations,
        author_role=AuthorRole.FINAL_ANSWER,
    )
    assert verdict is not None, "the authority was never consulted for a tool-backed turn"
    # The withheld line is NAMED in the notice, which is the point of the notice -- so the body
    # is checked separately from it, exactly as the M3 pack does.
    from core.grounding_publication import UNSUPPORTED_WORK_NOTICE_LEAD

    body, _, notice = verdict.content.partition(UNSUPPORTED_WORK_NOTICE_LEAD)
    assert "retries = 5" in body, body[:300]
    assert "max_connections = 512" not in body, (
        "an unsupported sentence rode out beside a supported one: " + body[:300]
    )
    assert "max_connections = 512" in notice, (
        "the answer was quietly shortened instead of saying what was withheld: " + notice[:300]
    )
    assert any("max_connections" in claim for claim in verdict.withheld_claims), verdict

    # And when the fabrication shares a LINE with the reading, the authority takes both down and
    # refuses rather than guessing which half of the sentence was real. Either way the answer
    # cannot fully publish, which is the invariant; which of the two happens is the authority's
    # own conservative call and is inherited, not re-decided here.
    same_line = content.replace("\n", " ")
    strict = authored_publication_verdict(
        same_line,
        request_text="what retry settings are in config.toml?",
        observations=observations,
        author_role=AuthorRole.FINAL_ANSWER,
    )
    assert strict is not None and strict.content != same_line, strict
    strict_body, _, _strict_notice = strict.content.partition(UNSUPPORTED_WORK_NOTICE_LEAD)
    assert "max_connections = 512" not in strict_body, strict_body[:300]


def test_a_fully_supported_tool_backed_answer_is_untouched():
    """The control. The authority may only ever narrow; a answer that follows from what the
    turn observed ships byte-identically."""

    from core.final_answer_authorship import AuthorRole, authored_publication_verdict

    observations = [
        {
            "intent": "workspace.read_file",
            "summary": "config.toml sets retries = 5 and timeout_seconds = 30.",
            "source_label": "config.toml",
        }
    ]
    content = "config.toml sets retries = 5 and timeout_seconds = 30."
    verdict = authored_publication_verdict(
        content,
        request_text="what retry settings are in config.toml?",
        observations=observations,
        author_role=AuthorRole.FINAL_ANSWER,
    )
    assert verdict is not None
    assert verdict.content == content, verdict.content[:300]


def test_a_deterministic_tool_answer_is_not_claim_matched_at_all():
    """Runtime-composed bytes are not a generation, and the existing authority already says so
    (`_runtime_composed`). Reused, not re-decided."""

    from core.final_answer_authorship import AuthorRole, authored_publication_verdict

    observations = [{"intent": "workspace.read_file", "summary": "retries = 5"}]
    content = "config.toml sets retries = 5 and timeout_seconds = 30."
    verdict = authored_publication_verdict(
        content,
        request_text="what retry settings are in config.toml?",
        observations=observations,
        author_role=AuthorRole.DETERMINISTIC,
    )
    assert verdict is None or verdict.content == content, verdict


def test_the_authority_only_adjudicates_what_it_can_anchor():
    """The boundary of C, recorded rather than implied.

    `core.claim_support` adjudicates claims that carry an anchor -- a number, a date, an entity,
    a quote. Prose that asserts nothing testable is `no_claims` by that authority's own design,
    and reusing the authority means inheriting that boundary rather than quietly widening it with
    a second predicate. So an UNANCHORED sentence beside a supported reading still ships, and
    that is a real limit of this repair, not an oversight in it.
    """

    from core.final_answer_authorship import AuthorRole, authored_publication_verdict

    observations = [
        {
            "intent": "workspace.read_file",
            "summary": "config.toml sets retries = 5 and timeout_seconds = 30.",
            "source_label": "config.toml",
        }
    ]
    unanchored = (
        "config.toml sets retries = 5 and timeout_seconds = 30. "
        "The service is generally considered robust under load."
    )
    verdict = authored_publication_verdict(
        unanchored,
        request_text="what retry settings are in config.toml?",
        observations=observations,
        author_role=AuthorRole.FINAL_ANSWER,
    )
    assert verdict is not None
    assert "generally considered robust" in verdict.content, (
        "this test exists to record that unanchored prose is NOT withheld; if it now is, the "
        "boundary moved and the gap note in the evidence should be updated"
    )
