"""P0 - AN UNCERTIFIED MODEL MAY NOT AUTHOR THE FINAL ANSWER.

The defect, at base e9aa5855
---------------------------
`core.grounding_publication` is the one gate between a generated answer and the wire, and it
opens for exactly one class of turn: the ones `core.execution_requirements` marked
``current_information_required``. Every DIRECT turn -- an explanation, a definition, a
"how does X work" -- has no lifecycle row and the gate returns before reading a byte. That is
correct for the EVIDENCE question and silent on a different one: *was this model ever shown to
be able to write this answer at all?*

So a 0.6B local model registered by Ollama discovery, never probed, never measured, can write
the served bytes of an open-domain DIRECT turn, and nothing in the runtime asks whether it
should. `core.local_model_tool_certification` exists, records a per-fingerprint state, and its
own store stamps ``routing_effect: "none"`` on every row it returns -- the measurement was
taken and then read by nobody.

Turning local models off for a demo hides this; it does not fix it, and it contradicts the
local-first promise. The fix is the missing authority, not a prompt and not a model-name rule.

The invariant under test
------------------------
Before a model may become the FINAL-ANSWER AUTHOR, one typed authority
(`core.final_answer_authorship`) decides whether that exact model identity is certified for
that answer role. An uncertified model may still classify, plan, extract and call tools. It
may not author. When a certified configured model is available the runtime escalates to it and
records both identities; when none is, the turn returns a typed refusal instead of a fluent
guess.

Every behavioural test below drives `VoolAgent.run_once` and then the transport door's own
`finalize_answer`, with the model scripted at the PROVIDER seam (`_invoke_manifest`) so the
real router, the real ledger and the real publication path all run.
"""
from __future__ import annotations

import uuid
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest

# The backend identity the certification fingerprint is taken against. Pinned so a run written
# by a test and the status read by the runtime agree without depending on whether an Ollama
# daemon happens to be listening on this machine.
_RUNTIME_IDENTITY = {
    "backend_version": "ollama-test-0.0.0",
    "model_digest": "sha256:test-digest",
    "template_hash": "template-test",
    "quantization": "q4_K_M",
}

#: An open-domain DIRECT request: stable knowledge, no sources promised, no currency marker.
#: `answer_mode_for` classifies it DIRECT, which is precisely the class the publication
#: lifecycle does not cover.
DIRECT_REQUEST = "Explain in two sentences how a Bloom filter can report a false positive."

#: What the scripted local model writes. Fluent, specific, and backed by nothing this runtime
#: ever measured about this model.
MODEL_ANSWER = (
    "A Bloom filter reports a false positive when every bit position a query key hashes to was "
    "already set by other insertions. The structure therefore admits false positives but never "
    "false negatives."
)

#: The distinctive fragment that proves the model's own bytes reached the wire.
MODEL_FINGERPRINT = "never false negatives"


# --------------------------------------------------------------------------- manifests


def _local_manifest(
    *,
    model_name: str = "probe-uncertified:8b",
    provider_name: str = "ollama-local",
    parameter_billions: float = 8.0,
) -> ModelProviderManifest:
    """A loopback Ollama lane exactly as discovery registers one: enabled, licensed, capable
    on paper, and never certified."""

    return ModelProviderManifest(
        provider_name=provider_name,
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434", "timeout_seconds": 30},
        metadata={
            "runtime_family": "ollama",
            "cost_class": "free_local",
            "model_digest": _RUNTIME_IDENTITY["model_digest"],
            "chat_template_hash": _RUNTIME_IDENTITY["template_hash"],
            "quantization": _RUNTIME_IDENTITY["quantization"],
            "parameter_billions": parameter_billions,
        },
    )


def _cloud_manifest(model_name: str = "configured-cloud-model") -> ModelProviderManifest:
    """A remote lane the operator configured with their own key. The loopback probe cannot
    reach it by construction, so its certification comes from the configuration itself."""

    return ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name=model_name,
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="provider terms",
        license_reference="https://example.invalid/terms",
        weight_location="external",
        runtime_dependency="openai-compatible",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={
            "base_url": "https://openrouter.ai/api/v1",
            "api_path": "/chat/completions",
            "timeout_seconds": 5.0,
        },
        metadata={
            "deployment_class": "cloud",
            "cost_class": "paid_cloud",
            "runtime_family": "openai-compatible",
        },
    )


def _register(*manifests: ModelProviderManifest) -> None:
    for manifest in manifests:
        upsert_provider_manifest(manifest)


def _certify(manifest: ModelProviderManifest, *, state: str = "verified") -> str:
    """Write a completed certification run for this exact model identity, through the store's
    own public writers and at the fingerprint the runtime will read it back under."""

    from core.local_model_tool_certification import (
        certification_fingerprint,
        certification_fingerprint_payload,
    )
    from storage.model_tool_certification_store import (
        begin_certification_run,
        complete_certification_run,
    )

    payload = certification_fingerprint_payload(
        manifest,
        backend_version=str(_RUNTIME_IDENTITY["backend_version"]),
        runtime_identity=dict(_RUNTIME_IDENTITY),
    )
    fingerprint = certification_fingerprint(payload)
    run_id = f"tool-cert-{uuid.uuid4().hex}"
    begin_certification_run(
        run_id=run_id,
        fingerprint=fingerprint,
        fingerprint_payload=payload,
        provider_name=manifest.provider_name,
        model_name=manifest.model_name,
        adapter_type=str(manifest.adapter_type or "openai_compatible"),
    )
    complete_certification_run(
        run_id=run_id,
        fingerprint=fingerprint,
        state=state,
        successful=state == "verified",
        stages={"transport_acceptance": {"state": "passed"}},
        evidence={"exchange_count": 4},
        latency_ms=12.0,
    )
    return fingerprint


# ----------------------------------------------------------------------------- the rig


class _ProviderScript:
    """Answers `_invoke_manifest` so the REAL `_execute_provider_task` runs around it.

    Everything the production seam does after the provider returns -- usage accounting, the
    served-usage ledger record, the authorship recorders -- therefore happens for real; only
    the socket is replaced.
    """

    def __init__(self, text: str = MODEL_ANSWER, by_provider: dict | None = None):
        self.text = text
        self.by_provider = dict(by_provider or {})
        self.manifests: list[str] = []

    def __call__(self, *, manifest, request, source_context=None, **_kwargs):
        from adapters.base_adapter import ModelResponse
        from core.turn_model_call_ledger import record_provider_call

        provider_id = str(getattr(manifest, "provider_id", "") or "")
        self.manifests.append(provider_id)
        record_provider_call(
            source_context if isinstance(source_context, dict) else {},
            provider_id=provider_id,
            model_id=str(getattr(manifest, "model_name", "") or ""),
            cost_class=str((getattr(manifest, "metadata", None) or {}).get("cost_class") or ""),
        )
        from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
        text = self.by_provider.get(provider_id, self.text)
        if AMBIGUITY_SYSTEM_PROMPT in str(request.system_prompt or ''):
            text = '{"ambiguous": false, "referents": [], "clarification": ""}'
        response = ModelResponse(
            output_text=text,
            confidence=0.82,
            usage={"prompt_eval_count": 120, "eval_count": 48},
            provider_id=provider_id,
            model_name=str(getattr(manifest, "model_name", "") or ""),
            output_mode="plain_text",
            provider_attested_model=str(getattr(manifest, "model_name", "") or ""),
            finish_reason="stop",
        )
        return _StubAdapter(manifest), response, None


class _StubAdapter:
    """Stands in for the built adapter object only. Every routing decision around it is real."""

    def __init__(self, manifest):
        self.manifest = manifest

    def health_check(self) -> bool:
        return True

    def get_license_metadata(self) -> dict:
        return {
            "license_name": str(getattr(self.manifest, "license_name", "") or ""),
            "license_reference": str(getattr(self.manifest, "license_reference", "") or ""),
        }


@pytest.fixture(autouse=True)
def _pinned_backend_identity():
    """The certification fingerprint is taken against a fixed backend identity, so the tests do
    not depend on whether a real Ollama is listening on this machine."""

    def _clear_registry() -> None:
        """Each test starts with an EMPTY provider registry AND no certification history.

        Both are single shared tables for the whole pytest run, and this pack deliberately
        registers the same model identity with different certification states. Leaving either
        behind lets one test decide another: measured, the idempotence test PUBLISHED instead of
        refusing, because the routing-effect test above it had written a `verified` run for the
        very identity the idempotence test needs to be uncertified.
        """

        # NOT attempted here: surviving a neighbour that drops the runtime schema. Measured in a
        # 39-file group, `create_task_record` died with `no such table: task_trace_index`, and
        # re-running migrations plus clearing `core.trace_id._TABLE_READY` only moved the failure
        # to `task_state_events` -- every store memoises its own `_init_table` on a module global,
        # so once the schema goes none of them rebuild. That is a repo-wide fragility this pack
        # cannot fix from inside a fixture, and it takes the pre-existing
        # `test_served_evidence_binding_p0` pack down in the same group at BASE.
        from storage.db import get_connection

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
        except Exception:
            pass
        finally:
            conn.close()

    def _reset() -> None:
        # Tolerant on purpose: the behavioural tests below must fail on the DEFECT at base,
        # not on an import of the authority that does not exist there yet.
        try:
            from core.final_answer_authorship import reset_for_tests
        except ImportError:
            return
        reset_for_tests()

    _reset()
    _clear_registry()
    with mock.patch(
        "adapters.openai_compatible_adapter.OpenAICompatibleAdapter"
        ".tool_certification_runtime_identity",
        return_value=dict(_RUNTIME_IDENTITY),
    ):
        yield
    _reset()
    _clear_registry()


def _agent() -> VoolAgent:
    from core.runtime_continuity import reset_runtime_continuity_state

    reset_runtime_continuity_state()
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _drive(
    session_id: str,
    *,
    request: str = DIRECT_REQUEST,
    script: _ProviderScript | None = None,
    candidates: list[ModelProviderManifest] | None = None,
    context_extra: dict | None = None,
) -> tuple[dict, dict, _ProviderScript]:
    """One real turn: `VoolAgent.run_once`, model scripted at the provider seam."""

    agent = _agent()
    script = script or _ProviderScript()
    ranked = list(candidates or [])
    with (
        mock.patch.object(agent.memory_router, "_invoke_manifest", side_effect=script),
        mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=ranked,
        ),
    ):
        context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "cancel_turn_id": f"turn-{session_id}",
            "request_id": f"req-{session_id}",
            **(context_extra or {}),
        }
        result = agent.run_once(
            request, source_context=context, session_id_override=session_id
        )
    return result, context, script


def _serve(context: dict, result: dict) -> str:
    """The transport door's own contract (core/web/api/runtime.py::_response_commit)."""

    from core.finalization import finalize_answer

    commit = finalize_answer(
        turn_id=str(context.get("cancel_turn_id") or context.get("turn_id") or ""),
        canonical_content=str(result.get("response") or ""),
        source_context=context,
    )
    return str(commit.get("canonical_content") or "")


# ------------------------------------------------------------------- 1. the P0 itself


def test_an_uncertified_local_model_cannot_author_an_open_domain_direct_answer():
    """The defect, exactly. A never-probed loopback model writes a confident, specific,
    unmeasured answer to a DIRECT question and the bytes reach the wire."""

    local = _local_manifest()
    _register(local)
    result, context, script = _drive("sess-uncertified", candidates=[local])
    served = _serve(context, result)

    assert script.manifests, "the drive never reached the provider seam"
    assert MODEL_FINGERPRINT not in served, (
        "an uncertified local model authored the served answer: " + served[:400]
    )

    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in served, (
        "no typed refusal named the eligibility failure: " + served[:400]
    )


def test_a_certified_model_authors_the_same_answer_unchanged():
    """The control. The identical drive, with one thing different: a completed certification
    run exists for this exact model identity. The bytes ship untouched."""

    local = _local_manifest()
    _register(local)
    _certify(local, state="verified")
    result, context, script = _drive("sess-certified", candidates=[local])
    served = _serve(context, result)

    assert script.manifests, "the drive never reached the provider seam"
    assert MODEL_FINGERPRINT in served, (
        "a certified model's answer was withheld: " + served[:400]
    )
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD not in served, (
        "a certified author was refused: " + served[:400]
    )


def test_a_stale_or_degraded_certification_is_not_a_certification():
    """`degraded` is a measurement that ran and failed. It may not read as permission."""

    local = _local_manifest()
    _register(local)
    _certify(local, state="degraded")
    result, context, _script = _drive("sess-degraded", candidates=[local])
    served = _serve(context, result)

    assert MODEL_FINGERPRINT not in served, (
        "a degraded certification let the model author: " + served[:400]
    )


# --------------------------------------------------- 2. what stays allowed, unchanged


def test_a_deterministic_answer_still_ships_with_an_uncertified_local_model_registered():
    """The gate's jurisdiction is model-authored bytes. A turn the runtime answers itself --
    no provider call, no served model usage -- is untouched, and must stay untouched, or the
    fix has taken the local-first product down with the defect."""

    local = _local_manifest()
    _register(local)
    result, context, script = _drive(
        "sess-deterministic", request="What is 37 * 19?", candidates=[local]
    )
    served = _serve(context, result)

    assert not script.manifests, (
        "an arithmetic turn reached the model lane; this test no longer covers the "
        "deterministic path"
    )
    assert "703" in served, "a deterministic answer was withheld: " + served[:400]
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD not in served, (
        "the authorship gate claimed a turn no model authored: " + served[:400]
    )


def test_an_uncertified_model_may_still_classify_plan_and_extract():
    """The invariant is about the AUTHOR ROLE, not about the model. A model that may not write
    the final answer must still be usable for every supporting role it is fit for."""

    from core.final_answer_authorship import (
        FINAL_ANSWER_ROLE,
        AuthorRole,
        decide_final_answer_author,
    )

    local = _local_manifest()
    _register(local)

    for role in (
        AuthorRole.CLASSIFICATION,
        AuthorRole.PLANNING,
        AuthorRole.EXTRACTION,
        AuthorRole.TOOL_INTENT,
    ):
        decision = decide_final_answer_author(
            request_text=DIRECT_REQUEST,
            author_role=role,
            requested_manifest=local,
            candidates=[local],
        )
        assert decision.eligible, f"{role} was refused to an uncertified model: {decision}"

    authoring = decide_final_answer_author(
        request_text=DIRECT_REQUEST,
        author_role=FINAL_ANSWER_ROLE,
        requested_manifest=local,
        candidates=[local],
    )
    assert not authoring.eligible, f"the final-answer role was granted: {authoring}"


# ------------------------------------------------------------- 3. escalation and refusal


def test_local_only_without_an_eligible_author_refuses_honestly():
    """Local Only plus no certified local author is a real dead end. The turn says so; it does
    not quietly reach for the cloud and it does not answer anyway."""

    local = _local_manifest()
    cloud = _cloud_manifest()
    _register(local, cloud)
    _certify(cloud, state="verified")

    result, context, _script = _drive(
        "sess-local-only",
        candidates=[local],
        context_extra={"local_only_mode": True},
    )
    served = _serve(context, result)

    assert MODEL_FINGERPRINT not in served, (
        "local-only let an uncertified local model author: " + served[:400]
    )
    assert cloud.model_name not in served, (
        "a local-only turn named a cloud model as its author: " + served[:400]
    )
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in served, (
        "local-only refused without saying why: " + served[:400]
    )


def test_cloud_escalation_requires_configured_permission():
    """A configured cloud lane is not, by existing, permission to use it. With cloud
    escalation off the turn refuses; with it configured, the same turn escalates."""

    from core.final_answer_authorship import FINAL_ANSWER_ROLE, decide_final_answer_author

    local = _local_manifest()
    cloud = _cloud_manifest()
    _register(local, cloud)

    refused = decide_final_answer_author(
        request_text=DIRECT_REQUEST,
        author_role=FINAL_ANSWER_ROLE,
        requested_manifest=local,
        candidates=[local, cloud],
        cloud_escalation_permitted=False,
    )
    assert not refused.eligible, f"an unpermitted cloud lane authored: {refused}"
    assert not refused.selected_model, f"a refusal still named an author: {refused}"

    allowed = decide_final_answer_author(
        request_text=DIRECT_REQUEST,
        author_role=FINAL_ANSWER_ROLE,
        requested_manifest=local,
        candidates=[local, cloud],
        cloud_escalation_permitted=True,
    )
    assert allowed.eligible and allowed.escalated, f"no escalation was offered: {allowed}"
    assert allowed.selected_model == cloud.provider_id, allowed
    assert allowed.requested_model == local.provider_id, (
        "the escalation lost the model the turn actually asked for: " + str(allowed)
    )


def test_the_selected_author_identity_is_recorded_and_truthful():
    """Never silently switch models. Requested identity, selected identity, the reason and the
    author role are all in execution truth for the turn that was served."""

    local = _local_manifest()
    _register(local)
    _result, context, _script = _drive("sess-truthful", candidates=[local])
    _serve(context, _result)

    from core.final_answer_authorship import authorship_record_for_publication

    record = authorship_record_for_publication(turn_id=str(context["cancel_turn_id"]))
    assert record is not None, "the turn recorded no authorship decision"
    payload = record.to_dict()
    assert payload["requested_model"] == local.provider_id, payload
    assert payload["selected_model"] in ("", local.provider_id), payload
    assert payload["author_role"] == "answer_generation", payload
    assert payload["reason"], payload
    assert payload["certification_state"] in {
        "unknown",
        "probing",
        "verified",
        "degraded",
        "incompatible",
        "stale",
        "not_applicable",
    }, payload
    assert payload["eligible"] is False, payload


# ----------------------------------------------------------------- 4. cannot be escaped


def test_a_retry_of_the_same_turn_cannot_escape_the_eligibility_boundary():
    """A second attempt is a second authoring. Re-running the same request with the same
    uncertified model must be refused the same way -- the boundary is not a once-per-turn
    stamp a retry can walk past."""

    local = _local_manifest()
    _register(local)

    for attempt in range(2):
        result, context, script = _drive(f"sess-retry-{attempt}", candidates=[local])
        served = _serve(context, result)
        assert script.manifests, f"attempt {attempt} never reached the provider seam"
        assert MODEL_FINGERPRINT not in served, (
            f"retry {attempt} escaped the boundary: {served[:400]}"
        )


def test_mixed_demands_preserve_every_obligation():
    """A turn asking for two things, answered by an uncertified author, must not silently
    drop either demand. Both obligations stay accounted for and the refusal is explicit."""

    local = _local_manifest()
    _register(local)
    script = _ProviderScript(
        "A Bloom filter admits false positives but never false negatives. "
        "A skip list finds a key in expected logarithmic time."
    )
    result, context, _script = _drive(
        "sess-mixed",
        request=(
            "Explain in two sentences how a Bloom filter can report a false positive, "
            "and explain how a skip list finds a key."
        ),
        script=script,
        candidates=[local],
    )
    served = _serve(context, result)

    assert MODEL_FINGERPRINT not in served, (
        "the uncertified author's bytes shipped on a mixed turn: " + served[:400]
    )
    assert "skip list" not in served.lower() or "logarithmic" not in served.lower(), (
        "one demand shipped unauthored bytes while the other was refused: " + served[:400]
    )
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in served, (
        "a mixed turn dropped both demands without saying so: " + served[:400]
    )


def test_the_runtime_escalates_to_a_certified_author_on_the_real_chat_path():
    """When a certified model IS configured, the right outcome is that model writing the
    answer, not a refusal -- and the record must still name the model the turn started on."""

    uncertified = _local_manifest(model_name="probe-uncertified:8b")
    certified = _local_manifest(model_name="probe-certified:8b")
    _register(uncertified, certified)
    _certify(certified, state="verified")

    certified_answer = (
        "A Bloom filter can report a member that was never inserted, because independent keys "
        "share bit positions. It cannot report a non-member for a key it did hold."
    )
    script = _ProviderScript(
        by_provider={
            uncertified.provider_id: MODEL_ANSWER,
            certified.provider_id: certified_answer,
        }
    )
    result, context, script = _drive(
        "sess-escalate", script=script, candidates=[uncertified, certified]
    )
    served = _serve(context, result)

    # This fixture substitutes _invoke_manifest to challenge the publication backstop.
    # The amendment and served packs keep that seam intact and prove pre-spend refusal.
    assert uncertified.provider_id in script.manifests, script.manifests
    assert certified.provider_id in script.manifests, (
        "the turn never escalated to the certified author: " + str(script.manifests)
    )
    assert "never inserted" in served, (
        "the certified author's answer did not ship: " + served[:400]
    )
    assert MODEL_FINGERPRINT not in served, (
        "the uncertified author's bytes shipped anyway: " + served[:400]
    )

    from core.final_answer_authorship import authorship_record_for_publication

    record = authorship_record_for_publication(turn_id=str(context["cancel_turn_id"]))
    assert record is not None, "the escalation recorded nothing"
    payload = record.to_dict()
    assert payload["selected_model"] == certified.provider_id, payload
    assert payload["requested_model"] == uncertified.provider_id, (
        "the escalation erased the model the turn started on: " + str(payload)
    )
    assert payload["escalated"] is True, payload


def test_a_failed_escalation_cannot_turn_a_refusal_into_an_answer():
    """The certified model is configured but returns nothing. The turn must not fall back to
    serving what the uncertified model wrote."""

    uncertified = _local_manifest(model_name="probe-uncertified:8b")
    certified = _local_manifest(model_name="probe-certified:8b")
    _register(uncertified, certified)
    _certify(certified, state="verified")

    script = _ProviderScript(
        by_provider={
            uncertified.provider_id: MODEL_ANSWER,
            certified.provider_id: "",
        }
    )
    result, context, _script = _drive(
        "sess-escalate-fails", script=script, candidates=[uncertified, certified]
    )
    served = _serve(context, result)

    assert MODEL_FINGERPRINT not in served, (
        "a failed escalation served the uncertified author's bytes: " + served[:400]
    )
    # The absence of the model's bytes is not enough on its own: an escalation that returned
    # nothing ALSO produces no answer, and the runtime's own generic "couldn't get a model
    # response" notice contains no fingerprint either. What separates the two outcomes is
    # whether the REFUSAL still stands -- so assert the typed authorship refusal specifically,
    # and assert the record still says the author was ineligible. Without this pair, removing
    # the "the certified model actually answered" check in
    # `core.agent_runtime.turn_reasoning._escalate_to_a_certified_author` leaves this test green.
    from core.final_answer_authorship import (
        UNCERTIFIED_AUTHOR_NOTICE_LEAD,
        authorship_record_for_publication,
    )

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in served, (
        "a failed escalation lifted the authorship refusal: " + served[:400]
    )
    record = authorship_record_for_publication(turn_id=str(context["cancel_turn_id"]))
    assert record is not None, "the turn recorded no authorship decision"
    payload = record.to_dict()
    assert payload["eligible"] is False, payload
    assert payload["escalated"] is False, (
        "an escalation that produced nothing was recorded as if it had authored: " + str(payload)
    )


# ----------------------------------------------------------- 5. not a size or name rule


def test_eligibility_is_not_decided_by_model_size_or_name():
    """Two mutations that must change nothing, and one that must change everything.

    Renaming a 0.6B lane to a flagship model id does not certify it. Declaring 70B parameters
    does not certify it. Writing a certification run for the exact identity does -- and it
    does so for the SMALL, unflatteringly named one, which is what proves the verdict is read
    off the measurement rather than off the string."""

    from core.final_answer_authorship import FINAL_ANSWER_ROLE, decide_final_answer_author

    renamed = _local_manifest(model_name="gpt-5-turbo-max:latest")
    oversized = _local_manifest(model_name="huge-probe:70b", parameter_billions=70.0)
    tiny = _local_manifest(model_name="tiny-probe:0.6b")
    _register(renamed, oversized, tiny)

    for manifest in (renamed, oversized):
        decision = decide_final_answer_author(
            request_text=DIRECT_REQUEST,
            author_role=FINAL_ANSWER_ROLE,
            requested_manifest=manifest,
            candidates=[manifest],
        )
        assert not decision.eligible, (
            f"{manifest.model_name} authored on its name/size alone: {decision}"
        )

    _certify(tiny, state="verified")
    certified = decide_final_answer_author(
        request_text=DIRECT_REQUEST,
        author_role=FINAL_ANSWER_ROLE,
        requested_manifest=tiny,
        candidates=[tiny],
    )
    assert certified.eligible, (
        f"a measured, certified 0.6B model was refused: {certified}"
    )


def test_the_certification_store_no_longer_reports_a_routing_effect_of_none():
    """The store stamped ``routing_effect: "none"`` on every row it returned. Once the
    authority reads these rows that string is false, and a false receipt is the failure mode
    this project keeps paying for."""

    local = _local_manifest()
    _register(local)
    _certify(local, state="verified")

    from core.local_model_tool_certification import certification_status

    status = certification_status(local)
    assert status.get("routing_effect") != "none", status
    assert status.get("observe_only") is False, status


def test_refinalizing_a_refusal_returns_the_same_bytes():
    """Finalization admits identical duplicates and refuses different-content
    re-finalization, so the gate has to be a fixed point: gating already-gated bytes must
    return them byte-identically or a replay of a refused turn dies at the door."""

    local = _local_manifest()
    _register(local)
    result, context, _script = _drive("sess-idempotent", candidates=[local])
    first = _serve(context, result)

    from core.final_answer_authorship import gate_authored_content

    second, record = gate_authored_content(first, turn_id=str(context["cancel_turn_id"]))
    assert second == first, "the gate is not a fixed point over its own refusal"
    assert record.get("publication") == "refused_idempotent", record


def test_a_record_from_another_turn_never_decides_this_one():
    """Ids are reused in this runtime (the fast lane mints `fast:<session>:<hash>`), so a
    record found by one matching token but contradicted on another must not be consumed.
    A turn that never authored anything must publish unchanged even while a refusal for a
    different turn is still in the ledger."""

    local = _local_manifest()
    _register(local)
    refused_result, refused_context, _s1 = _drive("sess-owner", candidates=[local])
    _serve(refused_context, refused_result)

    from core.final_answer_authorship import authorship_record_for_publication

    assert authorship_record_for_publication(
        turn_id=str(refused_context["cancel_turn_id"])
    ) is not None, "the refusing turn recorded nothing"
    assert authorship_record_for_publication(turn_id="turn-some-other-turn") is None, (
        "a foreign turn id resolved to another turn's authorship verdict"
    )
