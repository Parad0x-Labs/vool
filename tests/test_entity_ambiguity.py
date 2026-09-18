"""The entity-ambiguity probe's contract (priority C — FINDINGS F45).

Served reproduction on f2b3fa6f: "What is the population of Springfield?"
answered "approximately 250,000 people" twice and "approximately 12,000
residents" once across three fresh sessions — two different invented numbers,
proving no contract existed. These tests pin the probe's own laws: strict
JSON, fail-open on garbage, a clarification that names referents, and an
eligibility gate that reaches exactly ONE plain knowledge question.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

_CTX: dict = {}

from core.entity_ambiguity import (
    AmbiguityVerdict,
    parse_ambiguity_verdict,
    render_clarification,
    single_plain_know_question,
)


class TestParse:
    def test_a_well_formed_ambiguous_verdict_parses(self) -> None:
        verdict = parse_ambiguity_verdict(
            '{"ambiguous": true, "referents": ["Springfield, Illinois", '
            '"Springfield, Massachusetts"], "clarification": "Which Springfield '
            'do you mean?"}'
        )
        assert verdict is not None and verdict.ambiguous
        assert verdict.referents == ("Springfield, Illinois", "Springfield, Massachusetts")
        assert verdict.clarification == "Which Springfield do you mean?"

    def test_an_unambiguous_verdict_parses(self) -> None:
        verdict = parse_ambiguity_verdict('{"ambiguous": false, "referents": [], "clarification": ""}')
        assert verdict is not None and not verdict.ambiguous

    def test_json_wrapped_in_prose_still_parses_strictly(self) -> None:
        verdict = parse_ambiguity_verdict(
            'Sure! {"ambiguous": true, "referents": ["A", "B"], "clarification": "Which?"}'
        )
        assert verdict is not None and verdict.ambiguous and verdict.referents == ("A", "B")

    def test_garbage_prose_fails_open_to_none(self) -> None:
        assert parse_ambiguity_verdict("The population is 250000.") is None

    def test_missing_or_non_boolean_ambiguous_fails_open(self) -> None:
        assert parse_ambiguity_verdict('{"referents": ["A"]}') is None
        assert parse_ambiguity_verdict('{"ambiguous": "yes"}') is None

    def test_an_ambiguous_verdict_with_nothing_to_ask_fails_open(self) -> None:
        # Serving "please clarify" with no referents and no question would be a
        # clarification that clarifies nothing — better to answer as before.
        assert parse_ambiguity_verdict('{"ambiguous": true, "referents": [], "clarification": ""}') is None

    def test_empty_and_none_fail_open(self) -> None:
        assert parse_ambiguity_verdict("") is None
        assert parse_ambiguity_verdict(None) is None


class TestRender:
    def test_the_clarification_names_the_referents_and_the_question(self) -> None:
        verdict = AmbiguityVerdict(
            ambiguous=True,
            referents=("Springfield, Illinois", "Springfield, Massachusetts"),
            clarification="Which Springfield do you mean?",
        )
        text = render_clarification(verdict, "What is the population of Springfield?")
        assert "Springfield, Illinois" in text and "Springfield, Massachusetts" in text
        assert "population of Springfield" in text

    def test_a_verdict_without_referents_still_renders_an_ask(self) -> None:
        verdict = AmbiguityVerdict(ambiguous=True, clarification="Which one do you mean?")
        text = render_clarification(verdict, "What is the population of Springfield?")
        assert "Which one do you mean?" in text


class TestEligibility:
    def test_the_springfield_question_is_eligible(self) -> None:
        assert single_plain_know_question("What is the population of Springfield?")

    def test_an_unambiguous_single_know_question_is_eligible_for_the_probe(self) -> None:
        # The probe's JURISDICTION is single plain questions; whether one is
        # ambiguous is the probe's judgment, not the gate's. Forcing the gate to
        # pre-judge would be a phrase list in disguise.
        assert single_plain_know_question("What is the capital of France?")
        assert single_plain_know_question("Why is the sky blue?")

    def test_multi_question_turns_are_not_eligible(self) -> None:
        assert not single_plain_know_question(
            "Answer each: What is 2+2? What color is the sky on a clear day?"
        )
        assert not single_plain_know_question(
            "What is 12 times 8? Now take that result and divide it by 6."
        )

    def test_mixed_live_turns_are_not_eligible(self) -> None:
        assert not single_plain_know_question(
            "What is the current Bitcoin price in USD and who wrote the novel 1984?"
        )

    def test_attachment_turns_are_not_eligible(self) -> None:
        assert not single_plain_know_question(
            "What is in this file?", has_attachments=True
        )

    def test_smalltalk_and_commands_are_not_eligible(self) -> None:
        assert not single_plain_know_question("I'm tired after a long day. Say something short and encouraging.")
        assert not single_plain_know_question("Delete the temp folder.")

    def test_overlong_text_is_not_eligible(self) -> None:
        assert not single_plain_know_question("What is the capital of France? " * 30)


@pytest.fixture()
def _eligible_probe_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the probe's three infra dependencies: manifests exist, one is eligible,
    and the session reads no prior events (context tests override the reader)."""
    from types import SimpleNamespace as _NS

    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.select_audit_manifests",
        lambda agent, context, routing: ([_NS(provider_id="ollama-local:qwen3:8b", metadata={"cost_class": "free_local"})], ""),
    )
    monkeypatch.setattr(
        "core.agent_runtime.audit_routing.resolve_routing_mode",
        lambda context: "auto",
    )
    monkeypatch.setattr(
        "core.final_answer_authorship.decide_final_answer_author",
        lambda **_: SimpleNamespace(eligible=True, reason="stub"),
    )
    monkeypatch.setattr(
        "core.memory.entries.recent_conversation_events",
        lambda session_id, limit=6: [],
    )


def _probe_agent():
    """A minimal agent carrying the REAL method plus a recording fast-path envelope."""
    from core.agent_runtime.agent import VoolAgent

    agent = SimpleNamespace(
        _fast_path_result=(
            lambda **kw: dict(kw, reason=kw.get("reason", ""), response=kw.get("response", ""))
        ),
    )
    agent._maybe_clarify_ambiguous_entity = lambda **kw: VoolAgent._maybe_clarify_ambiguous_entity(
        agent, **kw
    )
    # The seam's own helpers, bound to this stand-in: the recorder writes the turn's state
    # and the event emitter is a no-op here (the served rig proves the event; see F45/F47).
    agent._record_unresolved_adjudication = (
        lambda *a, **kw: VoolAgent._record_unresolved_adjudication(agent, *a, **kw)
    )
    agent._ambiguity_type_of = VoolAgent._ambiguity_type_of
    agent._emit_runtime_event = lambda *a, **kw: None
    return agent


class TestUnresolvedContract:
    """A failed adjudication is a state of its own, never 'unambiguous'.

    Measured on a65768ab run-a: the probe timed out, the fail-open path answered,
    and Springfield got an invented population. These pins hold the three-state law.
    """

    def test_the_states_exist_and_are_distinct(self) -> None:
        from core.entity_ambiguity import (
            STATE_AMBIGUOUS,
            STATE_UNAMBIGUOUS,
            STATE_UNRESOLVED,
            AmbiguityOutcome,
        )

        assert len({STATE_AMBIGUOUS, STATE_UNAMBIGUOUS, STATE_UNRESOLVED}) == 3
        outcome = AmbiguityOutcome(state=STATE_UNRESOLVED, attempts=2)
        assert outcome.verdict is None and outcome.attempts == 2

    def test_the_unresolved_reply_names_the_check_not_a_referent(self) -> None:
        from core.entity_ambiguity import render_unresolved_clarification

        text = render_unresolved_clarification("What is the population of Springfield?", attempts=2)
        assert "couldn't complete my check" in text
        assert "Springfield" in text
        assert "tried 2x" in text

    def test_the_probe_message_carries_prior_context(self) -> None:
        from core.entity_ambiguity import build_probe_user_message

        events = [
            {"user": "I'm planning a trip to Illinois.",
             "assistant": "Great — Chicago and Springfield are the classics."},
            {"user": "", "assistant": ""},
            {"role": "system", "content": "should be skipped"},
        ]
        message = build_probe_user_message("What is the population of Springfield?", events)
        assert "Illinois" in message
        assert "planning a trip" in message
        assert "should be skipped" not in message
        assert "The question to judge:" in message
        assert "What is the population of Springfield?" in message

    def test_the_probe_message_without_context_is_the_question_alone(self) -> None:
        from core.entity_ambiguity import build_probe_user_message

        message = build_probe_user_message("What is the capital of France?", None)
        assert "Recent conversation" not in message
        assert message.endswith("What is the capital of France?")

    def test_a_raised_probe_becomes_unresolved_not_an_answer(
        self, monkeypatch: pytest.MonkeyPatch, _eligible_probe_infra: None
    ) -> None:
        """The served defect's exact shape: the probe raises (deadline/provider), the
        turn must NOT fall through to the answering path as if adjudicated."""
        from types import SimpleNamespace

        calls = {"n": 0}

        def _raising_ask(system_prompt: str, prompt: str) -> str:
            calls["n"] += 1
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(
            "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
            lambda agent, context, **kwargs: _raising_ask,
        )
        monkeypatch.setattr(
            "core.memory.entries.recent_conversation_events",
            lambda session_id, limit=6: [],
        )
        agent = _probe_agent()
        result = agent._maybe_clarify_ambiguous_entity(
            effective_input="What is the population of Springfield?",
            session_id="s-unresolved",
            source_context={},
        )
        assert calls["n"] == 2, "a raised probe must be retried once, then unresolved"
        assert result is not None
        assert result.get("reason") == "ambiguity_adjudication_unresolved"
        assert "couldn't complete my check" in str(result.get("response") or result)

    def test_garbage_twice_asks_back_and_never_runs_the_answering_generation(
        self, monkeypatch: pytest.MonkeyPatch, _eligible_probe_infra: None
    ) -> None:
        """THE NON-NUMERIC GAP, closed at the seam that owns it.

        Under the earlier contract this mode returned None (= proceed to the answering
        generation) and relied on finalization withholding introduced NUMBERS. That fence is
        blind to "Springfield's mayor is John Smith": an invented ENTITY SELECTION with no
        figure in it publishes unchanged (`TestUnresolvedEnforcement` shows the fence's
        exact reach). The contract the module documents is one ask-back for every
        unresolved mode, so no generation runs and no entity-specific claim of any shape
        can be published under an unadjudicated referent.
        """
        from types import SimpleNamespace

        calls = {"n": 0}

        def _garbage_ask(system_prompt: str, prompt: str) -> str:
            calls["n"] += 1
            return "The mayor of Springfield is John Smith."

        monkeypatch.setattr(
            "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
            lambda agent, context, **kwargs: _garbage_ask,
        )
        monkeypatch.setattr(
            "core.memory.entries.recent_conversation_events",
            lambda session_id, limit=6: [],
        )
        _CTX.clear()
        agent = _probe_agent()
        result = agent._maybe_clarify_ambiguous_entity(
            effective_input="What is the population of Springfield?",
            session_id="s-garbage",
            source_context=_CTX,
        )
        assert calls["n"] == 2, "one retry, then unresolved"
        assert result is not None, "unresolved never proceeds to an answering generation"
        assert result.get("reason") == "ambiguity_adjudication_unresolved"
        response = str(result.get("response") or "")
        assert "couldn't complete my check" in response and "tried 2x" in response
        assert "John Smith" not in response, "the author's bytes are not the reply"
        assert _CTX["ambiguity_adjudication"] == {
            "state": "unresolved", "mode": "replied_without_verdict", "attempts": 2
        }

    def test_garbage_then_clean_verdict_answers(
        self, monkeypatch: pytest.MonkeyPatch, _eligible_probe_infra: None
    ) -> None:
        """The retry earns its cost: a transient first failure with a clean second
        verdict takes the normal path (None = answer)."""
        from types import SimpleNamespace

        replies = ["garbage", '{"ambiguous": false}']

        def _flaky_ask(system_prompt: str, prompt: str) -> str:
            return replies.pop(0)

        monkeypatch.setattr(
            "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
            lambda agent, context, **kwargs: _flaky_ask,
        )
        monkeypatch.setattr(
            "core.memory.entries.recent_conversation_events",
            lambda session_id, limit=6: [],
        )
        agent = _probe_agent()
        result = agent._maybe_clarify_ambiguous_entity(
            effective_input="What is the capital of France?",
            session_id="s-flaky",
            source_context={},
        )
        assert result is None

    def test_prior_context_reaches_the_probe_prompt(
        self, monkeypatch: pytest.MonkeyPatch, _eligible_probe_infra: None
    ) -> None:
        from types import SimpleNamespace

        seen = {}

        def _ask(system_prompt: str, prompt: str) -> str:
            seen["prompt"] = prompt
            return '{"ambiguous": false}'

        monkeypatch.setattr(
            "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
            lambda agent, context, **kwargs: _ask,
        )
        monkeypatch.setattr(
            "core.memory.entries.recent_conversation_events",
            lambda session_id, limit=6: [
                {"user": "I'm planning a trip to Illinois.", "assistant": ""}
            ],
        )
        agent = _probe_agent()
        agent._maybe_clarify_ambiguous_entity(
            effective_input="What is the population of Springfield?",
            session_id="s-ctx",
            source_context={},
        )
        assert "Illinois" in seen.get("prompt", "")


class TestUnresolvedEnforcement:
    """finalize_answer's second layer: introduced precise numerics are withheld under the
    legacy `unresolved_replied_without_verdict` state.

    Its reach is NUMERIC ONLY -- `test_a_nonnumeric_claim_passes_this_fence` states that
    exactly -- which is why the adjudication seam no longer proceeds to an answering
    generation on ANY unresolved mode (see `TestUnresolvedContract`). No served path records
    the legacy state any more; the fence stays as a layer for any future path that does.
    """

    @staticmethod
    def _finalize(text: str, context: dict):
        from core.finalization import finalize_answer

        return finalize_answer(
            turn_id="t-enf",
            canonical_content=text,
            source_context=context,
            status="answer_present",
            closure={"covered": True, "open_count": 0, "set_version": "v1:test"},
        )

    @staticmethod
    def _unresolved(request: str) -> dict:
        return {
            "ambiguity_adjudication": {"state": "unresolved_replied_without_verdict", "attempts": 2},
            "user_input": request,
        }

    def test_an_invented_precise_figure_cannot_publish(self) -> None:
        result = self._finalize(
            "The population is approximately 250,000 people.",
            self._unresolved("What is the population of Springfield?"),
        )
        content = str(result.get("content") or result.get("canonical_content") or "")
        assert "250,000" not in content and "250000" not in content
        assert "couldn't complete my check" in content

    def test_a_partial_answer_keeps_its_prose_and_loses_only_the_figure(self) -> None:
        result = self._finalize(
            "Springfield is a common city name with many bearers.\n"
            "One estimate puts a Springfield at approximately 250,000 residents.",
            self._unresolved("What is the population of Springfield?"),
        )
        content = str(result.get("content") or result.get("canonical_content") or "")
        assert "common city name" in content, "non-numeric prose must survive"
        assert "250,000" not in content
        assert "Withheld from this answer" in content

    def test_a_nonnumeric_answer_publishes_unchanged(self) -> None:
        text = "The capital of France is Paris."
        result = self._finalize(text, self._unresolved("What is the capital of France?"))
        content = str(result.get("content") or result.get("canonical_content") or "")
        assert content == text

    def test_a_nonnumeric_claim_passes_this_fence(self) -> None:
        """THE MEASURED REACH of the numeric fence: an invented entity selection with no
        figure in it is not withheld here. This is a statement about the layer, not a
        licence -- the seam upstream never lets such a turn reach a generation."""
        text = "The mayor of Springfield is John Smith."
        result = self._finalize(text, self._unresolved("Who is the mayor of Springfield?"))
        content = str(result.get("content") or result.get("canonical_content") or "")
        assert content == text

    def test_turns_without_the_state_are_untouched(self) -> None:
        text = "The answer is 42."
        result = self._finalize(text, {"user_input": "What is 6 x 7?"})
        content = str(result.get("content") or result.get("canonical_content") or "")
        assert content == text


class TestPrecheckHole:
    """An EXCEPTION in the eligibility pre-check is an unknown, not a clean absence.

    Measured hole while reproducing failure modes: the pre-check's except returned None
    with no state at all, so a faulted check silently authorized entity-specific
    answers. The clean no-eligible-author case stays stateless (the answer path's own
    gates refuse uncertified writers); an errored check arms the finalization
    enforcement like any other unresolved adjudication.
    """

    def test_a_precheck_exception_asks_back_instead_of_answering(
        self, monkeypatch: pytest.MonkeyPatch, _eligible_probe_infra: None
    ) -> None:
        def _boom(*a, **k):
            raise RuntimeError("routing store unavailable")

        monkeypatch.setattr(
            "core.agent_runtime.audit_routing.select_audit_manifests", _boom
        )
        ctx: dict = {}
        agent = _probe_agent()
        result = agent._maybe_clarify_ambiguous_entity(
            effective_input="What is the population of Springfield?",
            session_id="s-precheck",
            source_context=ctx,
        )
        assert result is not None, "an unknown is not an absence: the turn asks back"
        assert result.get("reason") == "ambiguity_adjudication_unresolved"
        response = str(result.get("response") or "")
        assert "couldn't run my check" in response and "Springfield" in response
        assert ctx["ambiguity_adjudication"] == {
            "state": "unresolved", "mode": "precheck_error", "attempts": 0
        }

    def test_the_recorded_state_never_arms_the_numeric_fence_on_the_ask_back(self) -> None:
        """The ask-back carries a count ("tried 2x"); the finalization fence keys on the
        legacy state string only, so the deterministic ask-back is never mangled by it."""
        from core.entity_ambiguity import render_unresolved_clarification
        from core.finalization import finalize_answer

        text = render_unresolved_clarification("What is the population of Springfield?", attempts=2)
        ctx = {
            "ambiguity_adjudication": {"state": "unresolved", "mode": "raised", "attempts": 2},
            "user_input": "What is the population of Springfield?",
        }
        final = finalize_answer(
            turn_id="t-askback",
            canonical_content=text,
            source_context=ctx,
            status="answer_present",
            closure={"covered": True, "open_count": 0, "set_version": "v1:t"},
        )
        assert str(final.get("content") or final.get("canonical_content") or "") == text

    def test_clean_no_eligible_author_stays_stateless(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "core.agent_runtime.audit_routing.select_audit_manifests",
            lambda agent, context, routing: ([], ""),
        )
        monkeypatch.setattr(
            "core.agent_runtime.audit_routing.resolve_routing_mode",
            lambda context: "auto",
        )
        ctx: dict = {}
        agent = _probe_agent()
        result = agent._maybe_clarify_ambiguous_entity(
            effective_input="What is the population of Springfield?",
            session_id="s-clean",
            source_context=ctx,
        )
        assert result is None
        assert "ambiguity_adjudication" not in ctx


@pytest.mark.parametrize("question", [
    "Also explain in one sentence how a queue differs from a stack.",
    "Explain briefly how a hash set differs from a list.",
])
def test_probe_uses_short_json_request_policy(monkeypatch, _eligible_probe_infra, question):
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
    from storage.model_provider_manifest import ModelProviderManifest

    adapter = OpenAICompatibleAdapter(ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name="nvidia/nemotron-3-ultra-550b-a55b:free",
        source_type="http", adapter_type="openai_compatible",
        runtime_config={"base_url": "https://openrouter.ai/api/v1", "supports_json_mode": True},
    ))
    monkeypatch.setattr("core.agent_runtime.audit_routing.select_audit_manifests",
                        lambda agent, context, routing: ([adapter.manifest], ""))
    requests = []

    def invoke(**kwargs):
        requests.append(kwargs["request"])
        return None, SimpleNamespace(output_text='{"ambiguous": false, "referents": [], "clarification": ""}',
                                     usage={}, finish_reason="stop", constraint_result={}), None

    agent = _probe_agent()
    agent.memory_router = SimpleNamespace(_invoke_manifest=invoke)
    result = agent._maybe_clarify_ambiguous_entity(effective_input=question, session_id="probe-contract", source_context={})
    assert result is None
    assert len(requests) == 1
    request = requests[0]
    assert request.reasoning_mode == "disabled"
    assert request.output_mode == "json_object"
    assert request.contract["json_schema"]["properties"]["ambiguous"] == {"type": "boolean"}
    wire = adapter._build_openai_payload(request, force_json=True, stream=False)
    assert wire["reasoning"] == {"enabled": False}
    assert wire["response_format"]["type"] in {"json_object", "json_schema"}

@pytest.mark.parametrize('question', [
    'what is best for llm runtime agents - Java or PY?',
    'What is the capital of France?',
])
def test_paid_pin_is_not_misreported_as_two_failed_probe_replies(monkeypatch, _eligible_probe_infra, question):
    from core.agent_runtime import turn_planner_hook
    assert single_plain_know_question(question)
    paid = SimpleNamespace(provider_id='usepod-byok:gpt-6-astra', model_name='gpt-6-astra',
                           provider_name='usepod-byok', metadata={'cost_class': 'paid_cloud'})
    monkeypatch.setattr('core.agent_runtime.audit_routing.select_audit_manifests',
                        lambda *a: ([paid], 'exact pin'))
    builds = []
    original = turn_planner_hook.build_conductor_ask_model
    def build(*a, **kw):
        builds.append(True)
        return original(*a, **kw)
    monkeypatch.setattr(turn_planner_hook, 'build_conductor_ask_model', build)
    context = {'requested_model': 'usepod:gpt-6-astra'}
    result = _probe_agent()._maybe_clarify_ambiguous_entity(
        effective_input=question, session_id='paid-probe-regression', source_context=context)
    assert result is None, 'Unavailable unpaid helper must leave the ordinary answer path reachable'
    assert builds == [], 'Do not manufacture two empty replies without a callable probe candidate'
    assert 'ambiguity_adjudication' not in context
    assert context['requested_model'] == 'usepod:gpt-6-astra'
