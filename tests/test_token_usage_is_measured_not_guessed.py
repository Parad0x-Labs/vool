"""A token count the operator is given must have been measured by something.

Asked on 2026-08-01 — "Report the cloud-input token count and which context components used most
of it." — the runtime answered "Cloud-input token count: ~2,800 tokens" plus a prose guess at the
components. The number was invented. The turn ran on `openrouter-byok:nvidia/…`, which returns a
`usage` block on every completion, and `PromptAssemblyReport` had already itemised the context.

These drive the three seams that were disconnected: the provider's measurement reaching the
runtime's own event ledger, that measurement reaching the object the answering path holds, and the
answering path composing the reply from it instead of asking the model.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from core.prompt_assembly_report import ContextItem, PromptAssemblyReport
from core.token_usage_receipt import (
    SOURCE_NOT_REPORTED,
    SOURCE_PROVIDER_REPORTED,
    TOKEN_USAGE_HEADING,
    append_token_usage_receipt,
    measured_call_usage,
    render_token_usage_receipt,
    token_usage_question,
)

# The verbatim shape OpenRouter returned for the turns in question.
OPENROUTER_USAGE = {
    "prompt_tokens": 12431,
    "completion_tokens": 284,
    "total_tokens": 12715,
    "cost": 0.0,
    "is_byok": False,
}


def _report_with(items: list[tuple[str, str, str]]) -> PromptAssemblyReport:
    """A real report, filled through the real `ContextItem.to_record` path.

    Building the records by hand would let the test agree with itself about a field name the
    assembler does not actually emit.
    """
    report = PromptAssemblyReport(
        task_id="task-token-usage",
        trace_id="trace-token-usage",
        total_context_budget=8192,
        bootstrap_budget=2594,
        relevant_budget=4096,
        cold_budget=1502,
    )
    for item_id, layer, content in items:
        item = ContextItem(
            item_id=item_id,
            layer=layer,
            source_type=f"{layer}_block",
            title=item_id,
            content=content,
        )
        report.items_included.append(item.to_record(included=True, reason="admitted"))
    return report


# --------------------------------------------------------------------------------------
# The measurement itself: reported, absent, and the 0 in between
# --------------------------------------------------------------------------------------


def test_the_openrouter_usage_block_is_read_as_a_measurement() -> None:
    usage = measured_call_usage(
        OPENROUTER_USAGE, provider_id="openrouter-byok", model_id="nvidia/nemotron-3-ultra-550b-a55b:free"
    )
    assert usage["input_tokens"] == 12431
    assert usage["output_tokens"] == 284
    assert usage["source"] == SOURCE_PROVIDER_REPORTED


@pytest.mark.parametrize(
    "block, expected_in, expected_out",
    [
        ({"prompt_eval_count": 903, "eval_count": 77}, 903, 77),  # Ollama
        ({"prompt_tokens": 12431, "completion_tokens": 284}, 12431, 284),  # OpenAI / OpenRouter
        ({"input_tokens": 5000, "output_tokens": 120}, 5000, 120),  # Anthropic
    ],
)
def test_every_provider_dialect_this_runtime_speaks_is_read(block, expected_in, expected_out) -> None:
    usage = measured_call_usage(block)
    assert (usage["input_tokens"], usage["output_tokens"]) == (expected_in, expected_out)


def test_a_lane_that_reports_nothing_is_recorded_as_missing_not_as_zero() -> None:
    """A local model with no usage object is the case the brief names: say which figure is gone."""

    usage = measured_call_usage({}, provider_id="ollama:qwen3-8b", model_id="qwen3:8b")
    assert usage["input_tokens"] is None
    assert usage["output_tokens"] is None
    assert usage["source"] == SOURCE_NOT_REPORTED


def test_a_reported_zero_is_a_measurement_not_a_hole() -> None:
    """`_record_response_usage` reads `a or b or 0`, which cannot tell these two apart.

    It does not need to — both bill nothing. This module does: one of them prints a number and the
    other prints "not available".
    """

    reported_zero = measured_call_usage({"prompt_tokens": 0, "completion_tokens": 0})
    assert reported_zero["input_tokens"] == 0
    assert reported_zero["source"] == SOURCE_PROVIDER_REPORTED
    assert measured_call_usage({"prompt_tokens": None})["source"] == SOURCE_NOT_REPORTED


def test_a_malformed_usage_block_never_raises() -> None:
    for junk in (None, "usage", 7, ["prompt_tokens", 12], {"prompt_tokens": "many"}):
        assert measured_call_usage(junk)["source"] == SOURCE_NOT_REPORTED


def test_a_boolean_is_never_printed_as_a_token_count() -> None:
    """`bool` is an `int` in Python, so `True` would render as a measured `1`."""

    assert measured_call_usage({"prompt_tokens": True})["source"] == SOURCE_NOT_REPORTED
    rendered = render_token_usage_receipt(
        token_usage={"input_tokens": True, "output_tokens": False, "provider_id": "p"},
        report=_report_with([]),
    )
    assert "| Model input (prompt) | not available |" in rendered
    assert "| 1 |" not in rendered and "| 0 |" not in rendered


# --------------------------------------------------------------------------------------
# Is the operator asking?
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "asked",
    [
        # The verbatim question from the incident.
        "Report the cloud-input token count and which context components used most of it.",
        # Phrasings this code has never seen.
        "how many tokens did that use?",
        "whats my token usage for this turn",
        "How much of the context window did we spend?",
        "give me the context breakdown",
        "which context components used the most tokens",
        "what was in the context for that answer",
        "how many input tokens went to the cloud model",
        "token count please",
        "what went into the context on this one",
        "how much did this turn cost in tokens",
    ],
)
def test_a_usage_question_is_recognised(asked: str) -> None:
    assert token_usage_question(asked) is True


@pytest.mark.parametrize(
    "asked",
    [
        "",
        "   ",
        "write a tokenizer that counts tokens in a string",
        "build me a token usage dashboard",
        "what is a token?",
        "explain how tokens work in an LLM",
        "how does tokenization handle emoji",
        "give me some context on the Apache codec",
        "in the context of the Windows lane, how much work is left",
        "how many files are in this folder",
        "what did that cost me in dollars",
    ],
)
def test_something_that_is_not_a_usage_question_is_left_alone(asked: str) -> None:
    assert token_usage_question(asked) is False


def test_the_existing_cloud_usage_command_still_owns_its_own_turn() -> None:
    """`cloud usage [today|week|month|all]` is a deterministic fast path that already answers from
    the spend ledger. It must keep claiming its turn, and must not also get a receipt stapled on."""

    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_usage_command

    assert maybe_handle_cloud_usage_command("cloud usage today", owner_local=True) is not None
    assert token_usage_question("cloud usage today") is False


def test_no_deterministic_family_claims_the_question_before_the_model_lane() -> None:
    """Traced rather than assumed: the receipt only helps if the turn reaches the lane it lives in.

    Every phrasing below returns zero claims, which is precisely why the incident turn was handed
    to the model — and why it could invent an answer.
    """

    from core.agent_runtime.intent_claims import is_ambiguous, near_miss, probe_claims

    for asked in (
        "Report the cloud-input token count and which context components used most of it.",
        "how many tokens did that use?",
        "which context components used the most tokens",
    ):
        claims = probe_claims(asked)
        assert not claims and not is_ambiguous(claims) and not near_miss(asked, claims), asked


def test_a_build_request_that_mentions_a_build_later_still_reads_as_a_question() -> None:
    """The build denylist is anchored to the opening imperative, not searched anywhere."""

    assert token_usage_question("how many tokens did that build use?") is True


# --------------------------------------------------------------------------------------
# The rendered receipt
# --------------------------------------------------------------------------------------


def _incident_receipt() -> str:
    return render_token_usage_receipt(
        token_usage=measured_call_usage(
            OPENROUTER_USAGE,
            provider_id="openrouter-byok",
            model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
        ),
        report=_report_with(
            [
                ("bootstrap-self-knowledge", "bootstrap", "x" * 2344),  # 586 tokens
                ("bootstrap-persona", "bootstrap", "x" * 196),  # 49 tokens
                ("workspace-audit-evidence", "relevant", "x" * 4000),  # 1000 tokens
                ("bootstrap-safety", "bootstrap", "x" * 92),  # 23 tokens
            ]
        ),
    )


def test_the_measured_input_is_printed_with_the_provider_that_reported_it() -> None:
    receipt = _incident_receipt()
    assert "| Model input (prompt) | 12,431 | measured — reported by openrouter-byok" in receipt
    assert "| Model output (completion) | 284 | measured — reported by openrouter-byok" in receipt


def test_the_lane_is_named_once_not_twice() -> None:
    """`provider_id` is `name:model`, so appending the model again reads as two separate lanes."""

    composed = render_token_usage_receipt(
        token_usage=measured_call_usage(
            OPENROUTER_USAGE,
            provider_id="openrouter-byok:nvidia/nemotron-3-ultra-550b-a55b:free",
            model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
        ),
        report=_report_with([]),
    )
    assert "reported by openrouter-byok:nvidia/nemotron-3-ultra-550b-a55b:free |" in composed
    assert "a55b:free (nvidia" not in composed
    # A provider whose id does NOT already carry the model still names both.
    local = render_token_usage_receipt(
        token_usage=measured_call_usage({}, provider_id="ollama:qwen3-8b", model_id="qwen3:8b"),
        report=_report_with([]),
    )
    assert "ollama:qwen3-8b (qwen3:8b) reported no usage" in local


def test_the_components_are_ranked_by_cost_with_a_share_of_the_context() -> None:
    receipt = _incident_receipt()
    # 4000 chars -> 1000 estimated tokens, of 1,658 itemised in total.
    rows = [line for line in receipt.splitlines() if line.startswith("| workspace-audit-evidence")]
    assert rows == ["| workspace-audit-evidence | relevant | 1,000 | 60.3% |"]
    # Largest first, so "which used most of it" is answered by reading down.
    ordered = [
        line.split("|")[1].strip()
        for line in receipt.splitlines()
        if line.startswith("| ") and line.count("|") == 5 and "Layer" not in line and "---" not in line
    ]
    assert ordered == [
        "workspace-audit-evidence",
        "bootstrap-self-knowledge",
        "bootstrap-persona",
        "bootstrap-safety",
    ]


def test_the_estimate_is_never_dressed_up_as_a_measurement() -> None:
    receipt = _incident_receipt()
    assert "| Assembled context | 1,658 | runtime estimate at assembly" in receipt
    # And the gap between the two is explained rather than subtracted into a fake number.
    assert "not measured the same way" in receipt
    assert "12,147" not in receipt  # 12,431 - 284: a subtraction across provenances


def test_a_lane_with_no_usage_object_names_the_missing_figure_and_prints_no_number() -> None:
    receipt = render_token_usage_receipt(
        token_usage=measured_call_usage({}, provider_id="ollama:qwen3-8b", model_id="qwen3:8b"),
        report=_report_with([("bootstrap-persona", "bootstrap", "x" * 196)]),
    )
    assert "| Model input (prompt) | not available | ollama:qwen3-8b (qwen3:8b) reported no usage" in receipt
    assert "| Model output (completion) | not available |" in receipt
    # The component ledger is local, so it still answers half the question.
    assert "| bootstrap-persona | bootstrap | 49 | 100.0% |" in receipt
    # And with nothing measured to compare against, the cross-provenance note is not printed.
    assert "not measured the same way" not in receipt


def test_a_turn_with_no_itemised_context_says_so_instead_of_inventing_components() -> None:
    receipt = render_token_usage_receipt(
        token_usage=measured_call_usage(OPENROUTER_USAGE, provider_id="openrouter-byok"),
        report=PromptAssemblyReport(
            task_id="t", trace_id="t", total_context_budget=0, bootstrap_budget=0,
            relevant_budget=0, cold_budget=0,
        ),
    )
    assert "| Assembled context | not available |" in receipt
    assert "the context assembler recorded no included items" in receipt
    assert "| Model input (prompt) | 12,431 |" in receipt  # the half that IS measured still lands


def test_a_long_context_states_what_it_did_not_list() -> None:
    """A truncated table reads as the whole context — the same defect as the 200-file listing."""

    report = _report_with([(f"item-{index:02d}", "relevant", "x" * (400 - index * 4)) for index in range(20)])
    receipt = render_token_usage_receipt(token_usage=measured_call_usage(OPENROUTER_USAGE), report=report)
    assert "**Context components** — 20 item(s)" in receipt
    assert "…and 8 further component(s) totalling" in receipt


def test_a_component_title_cannot_break_the_table_it_sits_in() -> None:
    import re as _re

    report = _report_with([("evil | title\nwith a newline", "relevant", "x" * 400)])
    receipt = render_token_usage_receipt(token_usage=measured_call_usage({}), report=report)
    rows = [line for line in receipt.splitlines() if line.startswith("| evil")]
    assert len(rows) == 1, "a newline in a title split the row in two"
    # Four cells, so the pipe inside the title is escaped rather than acting as a delimiter.
    assert len(_re.findall(r"(?<!\\)\|", rows[0])) == 5
    assert r"evil \| title with a newline" in rows[0]


# --------------------------------------------------------------------------------------
# The router: the measurement reaches the ledger and the decision
# --------------------------------------------------------------------------------------


def _manifest():
    """The REAL manifest type, not a hand-rolled double.

    A `SimpleNamespace` shaped like a manifest passed `_invoke_manifest` and then died inside
    `output_trust_score`, which reads fields the double had never heard of — a double that agrees
    with the test and not with the runtime.
    """
    from storage.model_provider_manifest import ModelProviderManifest

    return ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name="nvidia/nemotron-3-ultra-550b-a55b:free",
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="proprietary",
        license_reference="https://openrouter.ai/terms",
        runtime_config={"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    )


class _Adapter:
    """Stands in for the transport only. Everything downstream of it is the real router."""

    def __init__(self, manifest: Any, usage: dict[str, Any]) -> None:
        self.manifest, self._usage = manifest, usage

    def health_check(self) -> dict[str, Any]:
        return {"ok": True}

    def supports_streaming(self) -> bool:
        return False

    def get_license_metadata(self) -> dict[str, Any]:
        return {"provider_name": self.manifest.provider_name}

    def run_text_task(self, request):
        from adapters.base_adapter import ModelResponse

        return ModelResponse(
            output_text="The audit found three issues in the codec.",
            usage=dict(self._usage),
            provider_id=self.manifest.provider_id,
            model_name=self.manifest.model_name,
        )


def _invoke(usage: dict[str, Any]) -> tuple[list[dict[str, Any]], Any]:
    """Drive the router's real `_invoke_manifest` and capture what the operator's ledger receives."""

    from adapters.base_adapter import ModelRequest
    from core.memory_first_router import MemoryFirstRouter

    manifest = _manifest()
    seen: list[dict[str, Any]] = []

    def _emit(source_context, *, event_type, message, details=None):
        seen.append({"event_type": event_type, **(details or {})})

    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    router.registry = SimpleNamespace(build_adapter=lambda _manifest: _Adapter(manifest, usage))

    with mock.patch("core.memory_first_router.emit_runtime_event", side_effect=_emit), mock.patch(
        "core.memory_first_router.should_probe_health", return_value=False
    ), mock.patch("core.memory_first_router.circuit_is_open", return_value=False), mock.patch(
        "core.memory_first_router._paid_call_authorization", return_value=None
    ), mock.patch(
        "core.memory_first_router.provider_cost_class", return_value="free_cloud"
    ), mock.patch(
        "core.memory_first_router.reported_cost_class", return_value="free_cloud"
    ):
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(task_kind="chat", prompt="audit the codec"),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="task-1"),
            source_context={"surface": "api"},
        )
    assert error is None, error
    return seen, response


def test_the_completed_call_event_carries_what_the_provider_measured() -> None:
    """`prompt_budget` was `{}` on every row of the incident and stays `{}` here — correctly, since
    the fitter never runs on a cloud lane. The measurement rides beside it, under its own name."""

    seen, _response = _invoke(OPENROUTER_USAGE)
    completed = [event for event in seen if event["event_type"] == "model.call_completed"]
    assert len(completed) == 1
    assert completed[0]["token_usage"] == {
        "input_tokens": 12431,
        "output_tokens": 284,
        "source": SOURCE_PROVIDER_REPORTED,
        # The composed id, exactly as the incident's ledger rows carry it.
        "provider_id": "openrouter-byok:nvidia/nemotron-3-ultra-550b-a55b:free",
        "model_id": "nvidia/nemotron-3-ultra-550b-a55b:free",
    }


def test_a_lane_that_reported_nothing_still_says_so_on_the_event() -> None:
    seen, _response = _invoke({})
    completed = next(event for event in seen if event["event_type"] == "model.call_completed")
    assert completed["token_usage"]["source"] == SOURCE_NOT_REPORTED
    assert completed["token_usage"]["input_tokens"] is None


def test_the_decision_the_answering_path_holds_carries_the_measurement() -> None:
    """The event is the ledger; THIS is the carrier the reply is composed from, and it is separate.

    Written because the first sabotage pass proved it was not pinned: emptying `details["token_usage"]`
    in the router broke nothing, since the end-to-end test below builds its own decision. The real
    `_decision_from_response` runs here — only the candidate-store write is stubbed.
    """

    from core.memory_first_router import MemoryFirstRouter

    manifest = _manifest()
    _seen, response = _invoke(OPENROUTER_USAGE)
    router = MemoryFirstRouter.__new__(MemoryFirstRouter)
    context_result = SimpleNamespace(
        retrieval_confidence_score=0.0,
        report=SimpleNamespace(retrieval_confidence="low"),
    )

    with mock.patch("core.memory_first_router.record_candidate_output", return_value="candidate-1"):
        decision = router._decision_from_response(
            manifest=manifest,
            adapter=_Adapter(manifest, OPENROUTER_USAGE),
            response=response,
            task_hash="hash-1",
            task=SimpleNamespace(task_id="task-1"),
            classification={"task_class": "research"},
            context_result=context_result,
            task_kind="chat",
            output_mode="plain_text",
            provider_role="auto",
            ranked_manifests=[manifest],
            attempted=[manifest.provider_id],
            failover_used=False,
            source="provider",
        )

    assert decision.details["token_usage"]["input_tokens"] == 12431
    assert decision.details["token_usage"]["output_tokens"] == 284
    assert decision.details["token_usage"]["source"] == SOURCE_PROVIDER_REPORTED


def test_the_measurement_survives_the_event_projection_to_the_client() -> None:
    """A field the projection does not read does not exist for the UI — that is how the last one
    was lost (core/web/api/runtime.py `_MODEL_EVENT_PREFIXES`)."""

    from core.web.api.runtime import format_runtime_event_text

    seen, _response = _invoke(OPENROUTER_USAGE)
    completed = next(event for event in seen if event["event_type"] == "model.call_completed")
    text = format_runtime_event_text({**completed, "message": "Model call completed."})
    assert '"token_usage":{"input_tokens":12431,"output_tokens":284,"source":"provider_reported"}' in text


# --------------------------------------------------------------------------------------
# The answering path
# --------------------------------------------------------------------------------------


def test_the_grounded_turn_answers_the_question_from_measured_values(make_agent) -> None:
    """The real `execute_grounded_turn`, with a model that gives the WRONG answer on purpose.

    This is the incident reproduced: the model's own wording is a fabricated number. The reply the
    operator receives must carry the measured one regardless of what the model said.
    """

    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    report = _report_with(
        [
            ("bootstrap-self-knowledge", "bootstrap", "x" * 2344),
            ("workspace-audit-evidence", "relevant", "x" * 4000),
        ]
    )
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.7,
        assembled_context=lambda: "",
        context_snippets=lambda: [],
        report=report,
    )
    decision = ModelExecutionDecision(
        source="provider",
        task_hash="h",
        provider_id="openrouter-byok",
        model_name="nvidia/nemotron-3-ultra-550b-a55b:free",
        used_model=True,
        output_text="Cloud-input token count: ~2,800 tokens.",
        details={
            "token_usage": measured_call_usage(
                OPENROUTER_USAGE,
                provider_id="openrouter-byok",
                model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
            )
        },
    )
    response = _drive_grounded_turn(
        agent,
        context_result=context_result,
        decision=decision,
        asked="Report the cloud-input token count and which context components used most of it.",
    )

    assert TOKEN_USAGE_HEADING in response
    assert "12,431" in response, "the measured input never reached the operator"
    assert "| workspace-audit-evidence | relevant | 1,000 |" in response
    # The model's invented figure is still visible above it — this appends truth, it does not
    # silently rewrite what the model said.
    assert "~2,800" in response


def test_an_ordinary_turn_gets_no_token_table(make_agent) -> None:
    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.7,
        assembled_context=lambda: "",
        context_snippets=lambda: [],
        report=_report_with([("bootstrap-persona", "bootstrap", "x" * 196)]),
    )
    decision = ModelExecutionDecision(
        source="provider",
        task_hash="h",
        provider_id="openrouter-byok",
        used_model=True,
        output_text="The codec repeats its header on every frame.",
        details={"token_usage": measured_call_usage(OPENROUTER_USAGE)},
    )
    response = _drive_grounded_turn(
        agent, context_result=context_result, decision=decision, asked="why does the codec repeat?"
    )
    assert TOKEN_USAGE_HEADING not in response


def test_an_audit_turn_never_gets_the_multi_table_breakdown_even_when_asked_about_tokens(
    make_agent,
) -> None:
    """Finding E point 9, 2026-08-04: `token_usage_question()` fires on cost/context-brushing
    wording with no regard for whether the SAME turn also carried a stepped-audit finding. An
    audit response already ends with its own compact one-line `usage_line` footer and a pointer to
    Activity for full detail (`audit_usage_sentence`, composed in `audit_verdict.py`) -- the
    multi-table breakdown must not glue itself onto the same reply just because the wording brushed
    "token"/"context", degrading a clean audit report into token-telemetry soup."""

    from core.memory_first_router import ModelExecutionDecision

    agent = make_agent()
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.7,
        assembled_context=lambda: "",
        context_snippets=lambda: [],
        report=_report_with([("bootstrap-persona", "bootstrap", "x" * 196)]),
    )
    decision = ModelExecutionDecision(
        source="provider",
        task_hash="h",
        provider_id="openrouter-byok",
        used_model=True,
        output_text="**Status: Confirmed** — `app.py`\n\n## Findings\nBare exception handler.",
        details={
            "token_usage": measured_call_usage(OPENROUTER_USAGE),
            "stepped_audit": {"terminal_state": "proven", "model_calls": 3},
        },
    )
    # Wording that DOES pass `token_usage_question()` on its own -- the exclusion must be keyed on
    # the turn carrying a stepped-audit result, never on re-parsing the operator's own phrasing.
    asked = "audit app.py and tell me the token cost / context usage for this run"
    assert token_usage_question(asked), "test assumption broken: this phrasing must trip the gate"

    response = _drive_grounded_turn(
        agent, context_result=context_result, decision=decision, asked=asked
    )
    assert TOKEN_USAGE_HEADING not in response


def _drive_grounded_turn(agent, *, context_result, decision, asked: str) -> str:
    """Run the real grounded turn, stubbing only what needs a network or a database."""

    from core.identity_manager import load_active_persona

    adaptive = SimpleNamespace(
        enabled=False, tool_gap_note="", admitted_uncertainty=False, notes=[],
        reason="not_needed", strategy="none", actions_taken=[], queries_run=0,
        to_dict=lambda: {"enabled": False, "reason": "not_needed"},
    )
    task = SimpleNamespace(
        task_id="task-token-usage", task_summary=asked, environment_os="darwin",
        environment_shell="zsh", environment_runtime="python", environment_version_hint="3.12",
    )
    classification = {"task_class": "research"}

    agent.context_loader.load = mock.Mock(return_value=context_result)
    agent._should_frontload_curiosity = mock.Mock(return_value=False)
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive)
    agent.memory_router.resolve = mock.Mock(return_value=decision)
    agent.media_pipeline.analyze = mock.Mock(
        return_value=SimpleNamespace(
            used_provider=False, provider_id="", candidate_id="", reason="no_media",
            evidence_items=[], analysis_text="",
        )
    )
    agent._collect_live_web_notes = mock.Mock(return_value=[])
    agent._web_note_plan_candidates = mock.Mock(return_value=[])
    agent._default_gate = mock.Mock(
        return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False)
    )
    agent._maybe_publish_public_task = mock.Mock(return_value={})
    agent._store_local_shard = mock.Mock()
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()
    # The decoration is the real thing's job; here it must pass the text through so the assertion
    # is about the receipt, not about the decorator.
    agent._decorate_chat_response = mock.Mock(side_effect=lambda result, **_: str(result.text or ""))

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch("core.agent_runtime.agent.build_media_context_snippets", return_value=[]), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.72, evidence_sources=[])
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch("core.agent_runtime.agent.feedback_engine.apply", return_value=None):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input=asked,
            classification=classification,
            interpreted=__import__(
                "core.human_input_adapter", fromlist=["adapt_user_input"]
            ).adapt_user_input(asked, session_id="token-usage-session"),
            persona=load_active_persona(agent.persona_id),
            session_id="token-usage-session",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    return str(result["response"])


# --------------------------------------------------------------------------------------
# The seam never costs an answer
# --------------------------------------------------------------------------------------


def test_the_receipt_does_not_change_how_the_reply_is_scored() -> None:
    """It is appended AFTER decoration, so every downstream reader sees it.

    `_quality_backed_remote_shard` inspects the delivered text and feeds shard-reuse accounting;
    `looks_like_internal_payload` is the guard that refuses scaffolding as an answer. A markdown
    table arriving late must move neither.
    """

    from core.reasoning_engine import inspect_user_response_shape
    from core.tool_call_dialects import looks_like_internal_payload

    answer = "The audit found three issues in the codec."
    with_receipt = append_token_usage_receipt(
        answer,
        user_input="how many tokens did that use?",
        model_execution=SimpleNamespace(details={"token_usage": measured_call_usage(OPENROUTER_USAGE)}),
        report=_report_with([("workspace-audit-evidence", "relevant", "x" * 4000)]),
    )
    assert TOKEN_USAGE_HEADING in with_receipt

    def _shape(text: str) -> tuple[bool, bool]:
        metrics = inspect_user_response_shape(
            text, surface="openclaw", rendered_via="model_final_wording"
        )
        return bool(metrics.get("planner_leakage")), bool(metrics.get("template_fallback_hit"))

    assert _shape(with_receipt) == _shape(answer) == (False, False)
    assert looks_like_internal_payload(with_receipt) is False


def test_a_broken_receipt_never_swallows_the_reply() -> None:
    broken = SimpleNamespace(items_included=property(lambda self: 1 / 0))
    assert (
        append_token_usage_receipt(
            "Here is the answer.",
            user_input="how many tokens did that use?",
            model_execution=SimpleNamespace(details={"token_usage": {}}),
            report=broken,
        )
        is not None
    )


def test_the_receipt_is_appended_once_not_per_pass() -> None:
    once = append_token_usage_receipt(
        "Answer.",
        user_input="how many tokens did that use?",
        model_execution=SimpleNamespace(details={"token_usage": measured_call_usage(OPENROUTER_USAGE)}),
        report=_report_with([("bootstrap-persona", "bootstrap", "x" * 196)]),
    )
    twice = append_token_usage_receipt(
        once,
        user_input="how many tokens did that use?",
        model_execution=SimpleNamespace(details={"token_usage": measured_call_usage(OPENROUTER_USAGE)}),
        report=_report_with([("bootstrap-persona", "bootstrap", "x" * 196)]),
    )
    assert twice == once
    assert once.count(TOKEN_USAGE_HEADING) == 1


def test_an_empty_reply_is_left_empty() -> None:
    assert (
        append_token_usage_receipt(
            "  ",
            user_input="how many tokens did that use?",
            model_execution=SimpleNamespace(details={}),
            report=_report_with([]),
        )
        == "  "
    )
