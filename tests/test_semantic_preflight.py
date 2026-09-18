from __future__ import annotations

import unicodedata
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.intent_claims import FAMILY_LIST_DIRECTORY, probe_claims
from core.agent_runtime.turn_frontdoor import (
    _execute_arbitrated_family,
    _stable_category_error_admitted,
    closed_semantic_contract_covers_turn,
)
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.semantic.canonical_text import CanonicalText
from core.semantic.preflight import (
    RAW_USER_TEXT_REPRESENTATION,
    AdmissionReason,
    DeterministicRouteCandidate,
    Establishment,
    SemanticFrame,
    SemanticProof,
    SemanticRequirement,
    StructuralIssueCode,
    admit_deterministic_candidate,
    authoritative_whole_turn_candidate,
    semantic_preflight,
)
from core.tool_intent_executor import execute_tool_intent

SET4_12 = (
    "Which of PNG, TXT, GIF, and PDF is the format defined by ISO/IEC 15948? "
    "List only actual members of that requested standard; do not infer membership merely because "
    "every item is a file format."
)
SET4_15 = (
    "In a puzzle’s stipulated rules, a “square circle” is a purple token that moves diagonally. "
    "If I move the square circle once, what object moved and how? Respect the explicit game "
    "definition rather than applying literal geometry."
)


def test_preflight_preserves_exact_raw_text_and_raw_v1_span_offsets() -> None:
    raw = "  Cafe\u0301 calls it\n“purple token” 👋  "
    assert unicodedata.normalize("NFC", raw) != raw

    result = semantic_preflight(raw)

    assert result.raw.representation == RAW_USER_TEXT_REPRESENTATION
    assert result.raw.text == raw
    assert result.turn_ir.source_text == raw
    assert result.full_span.resolve(result.raw) == raw
    quoted = next(frame for frame in result.frames if frame.frame is SemanticFrame.QUOTED)
    assert quoted.scope.resolve(result.raw) == "purple token"


@pytest.mark.parametrize(
    ("text", "expected", "effects_safe"),
    (
        ("What is the current exact population of Mars?", SemanticFrame.REAL, True),
        ("Assume X = 3. What is X?", SemanticFrame.STIPULATED, False),
        (
            "In my board game, oxygen is a purple metal. Based only on that fictional rule, what is the key?",
            SemanticFrame.FICTIONAL,
            False,
        ),
        (
            "In a geometry fable, the author calls it “a square circle.” Explain the phrase.",
            SemanticFrame.QUOTED,
            False,
        ),
        ("What does this mean?", SemanticFrame.UNKNOWN, True),
        ('"what is my name?"', SemanticFrame.UNKNOWN, True),
        ('Search the web for "database is on fire".', SemanticFrame.UNKNOWN, True),
    ),
)
def test_scoped_frame_classification_is_conservative(
    text: str,
    expected: SemanticFrame,
    effects_safe: bool,
) -> None:
    result = semantic_preflight(text)

    assert result.dominant_frame is expected
    assert result.effect_routes_safe is effects_safe


def test_explicit_frame_exit_creates_separate_real_scope() -> None:
    result = semantic_preflight(
        "Assume X = 3. Back to reality, delete the real scratch file."
    )

    assert result.dominant_frame is SemanticFrame.UNKNOWN
    assert [scope.frame for scope in result.frames] == [
        SemanticFrame.STIPULATED,
        SemanticFrame.REAL,
    ]
    assert result.frames[0].scope.end <= result.frames[1].scope.start
    assert result.effect_routes_safe is True


def test_quoted_evidence_does_not_cancel_a_separate_explicit_real_action() -> None:
    result = semantic_preflight(
        'The error says "missing comma." Fix the real source file.'
    )

    assert result.dominant_frame is SemanticFrame.QUOTED
    assert result.effect_routes_safe is True


def test_only_narrow_structural_conflicts_are_reported() -> None:
    conflict = semantic_preflight(
        "Assume X = 3. Assume X = 4. Answer in exactly 2 words and exactly 5 words."
    )
    codes = {issue.code for issue in conflict.issues}

    assert StructuralIssueCode.CONFLICTING_LITERAL_ASSIGNMENT in codes
    assert StructuralIssueCode.CONFLICTING_RESPONSE_CONSTRAINT in codes
    # Two properties are not structurally exclusive without ontology knowledge.
    compatible = semantic_preflight("Assume the token is purple and metallic.")
    assert compatible.issues == ()


def test_unterminated_quote_and_typed_reference_ambiguity_are_structural_issues() -> None:
    quote = semantic_preflight('Explain the phrase "dry water')
    ambiguous = semantic_preflight("Do that to it.", ambiguous_reference=True)

    assert [issue.code for issue in quote.issues] == [StructuralIssueCode.UNTERMINATED_QUOTE]
    assert [issue.code for issue in ambiguous.issues] == [StructuralIssueCode.AMBIGUOUS_REFERENCE]


def test_deterministic_admission_fails_when_any_semantic_proof_is_absent() -> None:
    preflight = semantic_preflight("What does dry water mean?")
    span = preflight.full_span
    candidate = DeterministicRouteCandidate(
        route_id="test.reference",
        allowed_frames=frozenset({SemanticFrame.UNKNOWN}),
        required=frozenset(SemanticRequirement),
        proofs=(
            SemanticProof(
                SemanticRequirement.ENTITY_IDENTITY,
                Establishment.AUTHORITATIVE_REGISTRY,
                span,
                source_id="test.registry.v1",
            ),
            SemanticProof(
                SemanticRequirement.RELATION_IDENTITY,
                Establishment.AUTHORITATIVE_REGISTRY,
                span,
                source_id="test.registry.v1",
            ),
        ),
        scope=span,
    )

    admission = admit_deterministic_candidate(preflight, candidate)

    assert not admission.admitted
    assert not admission.deterministic_route_safe
    assert AdmissionReason.PROOF_ABSENT in admission.reasons


def test_registry_proof_must_bind_to_the_same_raw_text() -> None:
    preflight = semantic_preflight("What is the current exact population of Mars?")
    other = CanonicalText.of("same length but wrong turn", representation=RAW_USER_TEXT_REPRESENTATION)
    candidate = DeterministicRouteCandidate(
        route_id="test.bad-span",
        allowed_frames=frozenset({SemanticFrame.REAL}),
        required=frozenset({SemanticRequirement.ENTITY_IDENTITY}),
        proofs=(
            SemanticProof(
                SemanticRequirement.ENTITY_IDENTITY,
                Establishment.AUTHORITATIVE_REGISTRY,
                other.span(0, other.length),
            ),
        ),
    )

    admission = admit_deterministic_candidate(preflight, candidate)

    assert not admission.admitted
    assert AdmissionReason.PROOF_WRONG_TEXT in admission.reasons


def test_reviewed_category_route_admits_real_or_unknown_but_not_fiction_or_quote() -> None:
    real = semantic_preflight("Who is the current reigning CEO of the Atlantic Ocean?")
    unknown = semantic_preflight("Who is the CEO of the Atlantic Ocean?")
    fictional = semantic_preflight(
        "In a fictional game, the Atlantic Ocean is a company. Who is its CEO?"
    )
    quoted = semantic_preflight(
        'The failing prompt says "Who is the CEO of the Atlantic Ocean?" Explain the error.'
    )

    assert _stable_category_error_admitted(real)
    assert _stable_category_error_admitted(unknown)
    assert not _stable_category_error_admitted(fictional)
    assert not _stable_category_error_admitted(quoted)


def test_category_coverage_is_gated_by_semantic_frame_without_changing_domain_resolver() -> None:
    real = "Who is the current reigning CEO of the Atlantic Ocean? Explain why this makes no sense."
    fictional = (
        "In a fictional game, the Atlantic Ocean is a company. "
        "Who is the current reigning CEO of the Atlantic Ocean?"
    )

    assert closed_semantic_contract_covers_turn(real, session_id="preflight-real", source_context={})
    assert not closed_semantic_contract_covers_turn(
        fictional,
        session_id="preflight-fictional",
        source_context={},
    )


def test_effectful_candidate_is_rejected_inside_stipulated_frame_even_with_registry_proof() -> None:
    preflight = semantic_preflight("Assume this is a game. Open the fictional vault.")
    candidate = authoritative_whole_turn_candidate(
        preflight,
        route_id="test.effect",
        source_id="test.effect.registry.v1",
        allowed_frames=frozenset({SemanticFrame.STIPULATED, SemanticFrame.FICTIONAL}),
    )
    effect_candidate = DeterministicRouteCandidate(
        route_id=candidate.route_id,
        allowed_frames=candidate.allowed_frames,
        required=candidate.required,
        proofs=candidate.proofs,
        scope=candidate.scope,
        effectful=True,
    )

    admission = admit_deterministic_candidate(preflight, effect_candidate)

    assert not admission.admitted
    assert AdmissionReason.EFFECT_FRAME_FORBIDDEN in admission.reasons


def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(
        config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)
    )


def test_set4_list_only_membership_request_cannot_authorize_machine_directory_listing() -> None:
    preflight = semantic_preflight(SET4_12)
    claims = probe_claims(SET4_12)

    assert preflight.dominant_frame is SemanticFrame.UNKNOWN
    assert not any(claim.family == FAMILY_LIST_DIRECTORY for claim in claims)
    execution = execute_tool_intent(
        {"intent": "machine.list_directory", "arguments": {"path": "~/Desktop"}},
        task_id="set4-12",
        session_id="set4-12",
        source_context={
            "action_policy": "allowed",
            "_semantic_machine_list_directory_admitted": False,
        },
        hive_activity_tracker=_tracker(),
    )

    assert not execution.ok
    assert execution.status == "blocked_by_semantic_preflight"
    assert execution.details["executed"] is False


def test_set4_list_only_arbiter_pick_declines_before_any_direct_tool_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.runtime_execution_tools.execute_runtime_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unproved directory intent reached the direct executor")
        ),
    )
    result = _execute_arbitrated_family(
        VoolAgent(backend_name="test-backend", device="semantic-preflight"),
        decision=SimpleNamespace(family=FAMILY_LIST_DIRECTORY, argument="~/Desktop"),
        effective_input=SET4_12,
        session_id="set4-12-direct",
        source_surface="web",
        source_context={"_semantic_machine_list_directory_admitted": False},
    )

    assert result is None


@pytest.mark.parametrize(
    "text",
    (
        "Which file format is defined by this standard? List only actual members.",
        "Which image formats support transparency?",
        "Are PDF and TXT document types?",
    ),
)
def test_semantic_file_types_are_not_machine_tool_near_misses(text: str) -> None:
    from core.agent_runtime.intent_claims import near_miss, semantic_file_type_request
    from core.execution.planner import should_attempt_tool_intent

    assert not near_miss(text, probe_claims(text))
    assert semantic_file_type_request(text)
    assert not should_attempt_tool_intent(
        text,
        task_class="unknown",
        source_context={"surface": "api"},
    )


def test_file_format_conversion_request_does_not_get_misclassified_as_semantic_membership() -> None:
    from core.agent_runtime.intent_claims import semantic_file_type_request

    assert not semantic_file_type_request("Convert this image format to PNG and save the result.")


def test_set4_stipulated_move_cannot_authorize_operator_move_effect() -> None:
    preflight = semantic_preflight(SET4_15)

    assert preflight.dominant_frame in {SemanticFrame.STIPULATED, SemanticFrame.FICTIONAL}
    assert preflight.effect_routes_safe is False
    execution = execute_tool_intent(
        {
            "intent": "operator.move_path",
            "arguments": {"source_path": "/tmp/source", "destination_path": "/tmp/destination"},
        },
        task_id="set4-15",
        session_id="set4-15",
        source_context={"action_policy": "forbidden"},
        hive_activity_tracker=_tracker(),
    )

    assert not execution.ok
    assert execution.status == "blocked_by_action_policy"
    assert execution.details["executed"] is False


@pytest.mark.parametrize("checkpoint_state", ("resumed", "retried"))
def test_runtime_computes_preflight_from_reinterpreted_checkpoint_request(
    checkpoint_state: str,
) -> None:
    """The literal `continue` must never become the semantic authority for resumed work."""

    agent = VoolAgent(
        backend_name="test-backend",
        device=f"semantic-preflight-{checkpoint_state}-test",
        persona_id="default",
    )
    initial = SimpleNamespace(
        raw_text="continue",
        normalized_text="continue",
        turn_id="turn-continue",
        quality_flags=[],
    )
    restored = SimpleNamespace(
        raw_text=SET4_15,
        normalized_text=SET4_15,
        turn_id="turn-restored",
        quality_flags=[],
    )
    context: dict[str, object] = {"surface": "api", "platform": "api"}
    checkpoint = {
        "state": checkpoint_state,
        "effective_input": SET4_15,
        "source_context": {
            "surface": "api",
            "platform": "api",
            "runtime_checkpoint_id": f"checkpoint-{checkpoint_state}",
        },
    }
    observed: dict[str, object] = {}

    class ReachedClosedContractError(RuntimeError):
        pass

    def stop_at_closed_contract(text, **kwargs):
        observed["text"] = text
        observed["preflight"] = kwargs["preflight"]
        observed["source_context"] = dict(kwargs["source_context"])
        raise ReachedClosedContractError

    with mock.patch(
        "core.agent_runtime.agent.adapt_user_input",
        side_effect=(initial, restored),
    ) as adapt, mock.patch.object(
        agent,
        "_prepare_runtime_checkpoint",
        return_value=checkpoint,
    ), mock.patch.object(
        agent,
        "_maybe_answer_attempt_followup_turn",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_emit_runtime_event",
        return_value=None,
    ), mock.patch(
        "core.agent_runtime.agent.prune_stale_hive_interaction_state",
        return_value=None,
    ), mock.patch(
        "core.currency_value_contract.maybe_answer_currency_value",
        return_value=None,
    ), mock.patch(
        "core.agent_runtime.turn_frontdoor.closed_semantic_contract_covers_turn",
        side_effect=stop_at_closed_contract,
    ):
        with pytest.raises(ReachedClosedContractError):
            from core.turn_contract import TurnRequest

            agent._run_once_inner(
                "continue",
                session_id_override=f"semantic-{checkpoint_state}",
                source_context=context,
                # ARCH-TRUTH-R1: the inner runtime now mechanically requires the
                # canonical typed request — the direct-call seam builds it the
                # one way every surface does.
                turn_request=TurnRequest.from_ingress(
                    user_text="continue",
                    source_context=context,
                    request_id="req-semantic-preflight",
                    turn_id="turn-semantic-preflight",
                    session_id=f"semantic-{checkpoint_state}",
                ),
            )

    assert [call.args[0] for call in adapt.call_args_list] == ["continue", SET4_15]
    assert observed["text"] == SET4_15
    preflight = observed["preflight"]
    assert preflight.raw.text == SET4_15
    assert preflight.dominant_frame in {SemanticFrame.STIPULATED, SemanticFrame.FICTIONAL}
    assert observed["source_context"]["action_policy"] == "forbidden"
