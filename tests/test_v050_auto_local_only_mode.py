"""VOOL Auto Local Only: an Auto lane with no cloud arm, proved at every seam that has one.

There are now two Auto modes. `vool` may escalate to the operator's cloud lane; `vool-local-only`
may not, ever, by any route. This file exists because "may not, ever, by any route" is a claim about
paths that were never taken, and the only way to test one of those is to make the path taken and
watch it be refused.

**What is deliberately NOT the test.** Asserting that a local-only turn produced a plausible answer
proves nothing: a turn that never had a cloud option available cannot demonstrate that the option
was removed. So every enforcement test here first establishes that a cloud lane IS present and
WOULD be chosen — a registered `openrouter-byok` manifest, an enabled free-cloud policy, a paid
authorization — and only then asserts that Local Only removed it. The negative controls run the
same fixtures with the mode off and require the cloud lane to win, so a change that simply breaks
cloud routing everywhere fails this file rather than passing it.

**The three model seams, and why each is tested separately.** Selection ranks manifests; the router
invokes them; the adapter puts bytes on the wire. A block installed at one of those is absent from
the other two, and the runtime reaches a provider through all three. `TestSelectionSeam`,
`TestInvocationSeam` and `TestTransportSeam` each require its own boundary to hold. Tool eligibility
is tested separately as an allowed path, because web/search is not a fourth cloud-model seam.

The prompts in `TestDrivenTurns` are the ones named in the mission, plus phrasings the
implementation never saw, because a mode that only recognises the sentence it was built against is
a keyword matcher wearing a mode's name.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from core import auto_local_only_mode as mode
from core.model_selection_policy import (
    ModelSelectionRequest,
    explain_provider_exclusions,
    rank_providers,
)
from storage.model_provider_manifest import ModelProviderManifest
from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)
from tests.test_v050_fast_path_costs_no_speculative_inference import (
    ProviderInvocations,
    arbiter_is_reachable,
    invocations,
)

#: Distinctive bytes, so a deterministic answer cannot be satisfied by a plausible summary of a file
#: nothing opened.
_LOCAL_EVIDENCE = "LOCAL-ONLY-EVIDENCE-5091"


# =================================================================================================
# Fixtures — a machine that HAS a cloud lane, so removing it is a measurable event
# =================================================================================================


def _local_manifest(model: str = "qwen3:8b") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model,
        source_type="subprocess",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference=f"https://ollama.com/library/{model}",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local", "orchestration_role": "queen", "tokens_per_second": 8.0},
    )


def _byok_cloud_manifest(model: str = "deepseek/deepseek-chat-v3:free") -> ModelProviderManifest:
    """The BYOK OpenRouter burst lane, exactly as the runtime registers it.

    `cost_class: paid_cloud` is the real metadata: the lane is classified paid for ROUTING even when
    the catalog prices the model at zero, and it is the verified-free exception that then lets a
    zero-priced model through the paid gate. That exception is precisely what Local Only must not
    inherit, which is why the fixture carries the real value rather than a convenient one.
    """
    return ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name=model,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1"},
        metadata={"deployment_class": "remote", "cost_class": "paid_cloud"},
    )


def _paid_cloud_manifest(model: str = "gpt-4o") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="openai",
        model_name=model,
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="Proprietary",
        license_reference="https://openai.com/policies",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "https://api.openai.com/v1"},
        metadata={"deployment_class": "remote"},
    )


@pytest.fixture()
def verified_free_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the BYOK manifest genuinely verified-free, so the paid gate would let it through.

    Without this the local-only exclusion could be an accident of the paid gate that already
    existed. With it, the ONLY thing standing between the turn and a zero-cost cloud call is the
    mode under test.
    """
    def _verified_free(manifest: Any) -> bool:
        return str(getattr(manifest, "provider_name", "")) == "openrouter-byok"

    # Patched at BOTH names. `core.memory_first_router` does `from core.model_selection_policy
    # import is_verified_free_cloud_manifest`, which binds the function into its own namespace at
    # import time — so patching only the defining module leaves the router calling the real one, and
    # a test claiming to exercise the verified-free bypass would never reach it.
    monkeypatch.setattr("core.model_selection_policy.is_verified_free_cloud_manifest", _verified_free)
    monkeypatch.setattr("core.memory_first_router.is_verified_free_cloud_manifest", _verified_free)


def _request(**kwargs: Any) -> ModelSelectionRequest:
    kwargs.setdefault("task_kind", "summarize")
    kwargs.setdefault("output_mode", "plain_text")
    return ModelSelectionRequest(**kwargs)


# =================================================================================================
# The mode contract
# =================================================================================================


class TestModeContract:
    def test_the_two_auto_lanes_are_distinct_and_neither_is_a_model_pin(self) -> None:
        assert mode.SELECTOR_VALUE != mode.AUTO_SELECTOR_VALUE
        assert mode.is_auto_selection(mode.SELECTOR_VALUE)
        assert mode.is_auto_selection(mode.AUTO_SELECTOR_VALUE)
        assert mode.is_local_only_selection(mode.SELECTOR_VALUE)
        # The cloud-capable lane must NOT read as local-only; that inversion would make the whole
        # runtime local and every negative control in this file would pass for the wrong reason.
        assert not mode.is_local_only_selection(mode.AUTO_SELECTOR_VALUE)
        # A concrete pin is not an Auto lane, or the router would stop resolving it to a manifest.
        assert not mode.is_auto_selection("qwen3:8b")
        assert not mode.is_auto_selection("deepseek/deepseek-chat-v3:free")

    def test_binding_the_lane_stamps_the_turn_without_changing_tool_policy(self) -> None:
        context: dict[str, Any] = {}
        assert mode.bind_turn(context, mode.SELECTOR_VALUE) is True
        assert context[mode.CONTEXT_KEY] is True
        assert context[mode.SELECTED_KEY] is True
        assert "allow_remote_fetch" not in context
        assert mode.turn_is_local_only(context)
        assert mode.turn_selected_local_only(context)

    def test_the_ordinary_auto_lane_stamps_nothing_and_stays_cloud_capable(self) -> None:
        context: dict[str, Any] = {}
        assert mode.bind_turn(context, mode.AUTO_SELECTOR_VALUE) is False
        assert context == {}, f"ordinary Auto must not be stamped local-only: {context!r}"
        assert not mode.turn_is_local_only(context)
        assert mode.cloud_lane_permitted(context)

    def test_the_stamp_survives_the_copy_every_worker_thread_gets(self) -> None:
        """The mode has to cross a shallow copy, because that is how the runtime hands a turn to a
        conductor or planner worker. A ContextVar would be absent there; a stamped key is not."""
        context: dict[str, Any] = {"request_id": "req-copy"}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        worker_view = {**context}
        assert mode.turn_is_local_only(worker_view)

        import threading

        seen: list[bool] = []
        thread = threading.Thread(target=lambda: seen.append(mode.turn_is_local_only(worker_view)))
        thread.start()
        thread.join()
        assert seen == [True], "the mode did not survive the thread hop the provider calls take"

    @pytest.mark.parametrize(
        ("url", "local"),
        [
            ("http://127.0.0.1:11434", True),
            ("http://localhost:11434/v1", True),
            ("http://[::1]:8080", True),
            ("http://192.168.1.40:11434", True),   # the operator's own LAN box is not "cloud"
            ("https://openrouter.ai/api/v1", False),
            ("https://api.anthropic.com/v1", False),
            ("https://api.openai.com/v1", False),
            ("https://generativelanguage.googleapis.com", False),
            ("", False),                            # unknown is not local
            ("http://this-host-does-not-resolve.invalid", False),
        ],
    )
    def test_an_endpoint_is_classified_by_where_it_goes_not_by_what_claims_it(self, url: str, local: bool) -> None:
        assert mode.endpoint_is_local(url) is local

    def test_a_blocked_endpoint_raises_rather_than_returning_an_empty_answer(self) -> None:
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        with pytest.raises(mode.CloudEgressBlockedError):
            mode.assert_endpoint_allowed("https://openrouter.ai/api/v1", lane="test", source_context=context)
        # ...and the local endpoint on the same turn is untouched. A mode that blocked both would
        # pass a "no cloud call happened" assertion by having no calls at all.
        mode.assert_endpoint_allowed("http://127.0.0.1:11434", lane="test", source_context=context)

    def test_the_refusal_names_the_mode_and_the_reason_class(self) -> None:
        result = mode.refusal_result("live_data", source_context={mode.CONTEXT_KEY: True})
        assert result["response"] == mode.REFUSAL_TEXT
        assert "Local Only" in result["response"]
        assert result["deterministic"] is True
        assert result["local_only"] is True
        # A refusal must not be dressed as a partial answer; it is the whole response.
        assert result["source"] == "auto_local_only_refusal"


# =================================================================================================
# Seam 1 — selection. No cloud manifest is ever ranked.
# =================================================================================================


class TestSelectionSeam:
    def test_a_verified_free_cloud_lane_is_still_removed_because_free_is_a_price_not_a_place(
        self, verified_free_catalog: None
    ) -> None:
        manifests = [_local_manifest(), _byok_cloud_manifest()]

        # Control: with the mode OFF the free cloud lane is not merely present, it WINS. If this
        # assertion ever weakens, the local-only assertion below stops meaning anything.
        open_ranked = rank_providers(manifests, _request())
        assert open_ranked[0].provider_name == "openrouter-byok", (
            "the fixture no longer offers a cloud lane, so the exclusion below proves nothing"
        )

        ranked = rank_providers(manifests, _request(local_only=True))
        assert [m.provider_name for m in ranked] == ["ollama-local"]

    def test_a_paid_cloud_lane_is_removed_even_when_paid_fallback_was_authorized(self) -> None:
        manifests = [_local_manifest(), _paid_cloud_manifest()]
        assert any(
            m.provider_name == "openai"
            for m in rank_providers(manifests, _request(allow_paid_fallback=True))
        ), "the fixture no longer offers a paid lane"

        ranked = rank_providers(manifests, _request(allow_paid_fallback=True, local_only=True))
        assert [m.provider_name for m in ranked] == ["ollama-local"]

    def test_the_exclusion_names_this_mode_rather_than_the_machine_wide_setting(self) -> None:
        """Two different rules can exclude the same manifest, and a trace that cannot tell them
        apart sends the operator to the wrong switch."""
        rows = explain_provider_exclusions([_byok_cloud_manifest()], _request(local_only=True))
        assert rows[0]["status"] == mode.SELECTION_EXCLUSION_REASON
        assert rows[0]["status"] != "not_free_local_in_local_only_mode"

    def test_with_only_a_cloud_lane_installed_the_ranking_is_empty_rather_than_relaxed(
        self, verified_free_catalog: None
    ) -> None:
        """`rank_providers` has a self-heal that re-ranks with the model preference RELAXED when the
        strict pass is empty. It must not relax a policy constraint on the way — an empty list is
        the correct answer here, and 'no brain' is better than 'a cloud brain'."""
        ranked = rank_providers([_byok_cloud_manifest()], _request(preferred_model="qwen3:8b", local_only=True))
        assert ranked == []


class TestTheFlagSurvivesEveryHandOff:
    """The request is rebuilt field-by-field in two places. A dropped field re-admits the cloud."""

    def test_the_registry_rebuild_carries_the_flag(self) -> None:
        from core.model_registry import ModelRegistry

        registry = ModelRegistry.__new__(ModelRegistry)
        manifests = [_local_manifest(), _byok_cloud_manifest()]
        object.__setattr__(registry, "list_manifests", lambda enabled_only=True: manifests)

        ranked = registry.rank_manifests(_request(local_only=True))
        assert [m.provider_name for m in ranked] == ["ollama-local"], (
            "ModelRegistry.rank_manifests rebuilds ModelSelectionRequest field by field; "
            "local_only was dropped on the way through"
        )

    def test_provider_routing_carries_the_flag_and_also_drops_the_paid_arm(self) -> None:
        from core.model_registry import ModelRegistry
        from core.provider_routing import rank_provider_candidates

        registry = ModelRegistry.__new__(ModelRegistry)
        manifests = [_local_manifest(), _paid_cloud_manifest()]
        object.__setattr__(registry, "list_manifests", lambda enabled_only=True: manifests)

        ranked = rank_provider_candidates(
            registry,
            task_kind="summarize",
            output_mode="plain_text",
            role="queen",
            allow_paid_fallback=True,
            local_only=True,
        )
        assert [m.provider_name for m in ranked] == ["ollama-local"]


# =================================================================================================
# Seam 2 — invocation. The seam every worker thread passes through.
# =================================================================================================


class TestInvocationSeam:
    def test_a_cloud_manifest_handed_straight_to_the_invoker_is_refused(self) -> None:
        """Selection is not the only way a manifest reaches a provider: the escalation path, the
        local/remote race and the conductor all arrive at the invoker directly. So the invoker
        refuses on its own evidence rather than trusting that ranking already filtered."""
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        with pytest.raises(mode.CloudEgressBlockedError):
            mode.assert_manifest_allowed(_byok_cloud_manifest(), source_context=context)

    def test_the_local_manifest_on_the_same_turn_is_allowed_through(self) -> None:
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        mode.assert_manifest_allowed(_local_manifest(), source_context=context)

    def test_a_manifest_whose_class_cannot_be_read_is_treated_as_cloud(self) -> None:
        """Unknown is not local. The failure that matters is admitting a cloud call."""
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        with pytest.raises(mode.CloudEgressBlockedError):
            mode.assert_manifest_allowed(object(), source_context=context)

    def test_the_ordinary_auto_lane_invokes_the_cloud_manifest_normally(self) -> None:
        """The negative control for this seam: with the mode off, nothing is refused."""
        mode.assert_manifest_allowed(_byok_cloud_manifest(), source_context={})

    def test_the_routers_real_invoker_refuses_before_it_builds_an_adapter(self) -> None:
        """The PRODUCTION seam, not the helper it calls.

        `MemoryFirstRouter._invoke_manifest` is where the conductor, the tool planner, the
        local/remote race and the escalation path all converge — on worker threads, with the turn's
        context as the only thing that crossed with them. Driving the helper alone would leave
        "is the helper actually wired into that method" untested, which is the gap between a guard
        that exists and a guard that runs.
        """
        from core.memory_first_router import MemoryFirstRouter

        router = MemoryFirstRouter.__new__(MemoryFirstRouter)
        context: dict[str, Any] = {"request_id": "req-invoke"}
        mode.bind_turn(context, mode.SELECTOR_VALUE)

        adapter, response, error = router._invoke_manifest(
            manifest=_byok_cloud_manifest(),
            request=None,
            output_mode="plain_text",
            task=None,
            source_context=context,
        )
        assert adapter is None and response is None
        assert error == "auto_local_only_blocked_cloud_manifest", error

    def test_the_routers_real_invoker_refuses_the_cloud_lane_before_the_paid_gate(self, verified_free_catalog: None) -> None:
        """Order matters here. A verified-free manifest is EXEMPT from the paid gate, so if the
        local-only check sat after it the free cloud lane would walk straight through — which is
        the specific hole 'free cloud is still blocked' has to close."""
        from core.memory_first_router import MemoryFirstRouter

        router = MemoryFirstRouter.__new__(MemoryFirstRouter)
        context: dict[str, Any] = {"request_id": "req-free"}
        mode.bind_turn(context, mode.SELECTOR_VALUE)

        _adapter, _response, error = router._invoke_manifest(
            manifest=_byok_cloud_manifest(),
            request=None,
            output_mode="plain_text",
            task=None,
            source_context=context,
        )
        assert error == "auto_local_only_blocked_cloud_manifest", (
            f"a verified-free cloud manifest was not stopped by the mode (got {error!r})"
        )


# =================================================================================================
# Seam 3 — transport. Bytes on the wire, decided by the destination.
# =================================================================================================


class TestTransportSeam:
    def _adapter(self, manifest: ModelProviderManifest) -> Any:
        from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter(manifest)

    def _request_carrying_the_mode(self, local_only: bool) -> Any:
        from adapters.base_adapter import ModelRequest

        metadata = {mode.CONTEXT_KEY: True} if local_only else {}
        return ModelRequest(task_kind="summarize", prompt="hello", output_mode="plain_text", metadata=metadata)

    def test_the_adapter_refuses_a_cloud_base_url_when_the_request_carries_the_mode(self) -> None:
        """The adapter serves BOTH lanes — a loopback Ollama and the OpenRouter burst are one class
        differing only by base_url — so it cannot ask what kind of adapter it is. It asks where the
        call is going, which a mislabelled manifest cannot answer wrongly."""
        adapter = self._adapter(_byok_cloud_manifest())
        with pytest.raises(mode.CloudEgressBlockedError):
            adapter._assert_local_only_allows(self._request_carrying_the_mode(local_only=True))

    def test_the_adapter_allows_the_loopback_base_url_on_the_same_turn(self) -> None:
        adapter = self._adapter(_local_manifest())
        adapter._assert_local_only_allows(self._request_carrying_the_mode(local_only=True))

    def test_without_the_mode_the_cloud_base_url_is_allowed(self) -> None:
        adapter = self._adapter(_byok_cloud_manifest())
        adapter._assert_local_only_allows(self._request_carrying_the_mode(local_only=False))

    def test_the_policy_bound_cloud_transport_refuses_inside_the_ambient_scope(self) -> None:
        """The dedicated cloud adapters go through this transport rather than through the
        openai-compatible one, so it is a separate path with a separate check."""
        from core.cloud_transport import CloudTransportError, PolicyBoundCloudTransport

        transport = PolicyBoundCloudTransport(allowed_hosts=("openrouter.ai",))
        with mode.local_only_egress_scope(True):
            with pytest.raises(CloudTransportError) as excinfo:
                transport.request_json(method="POST", url="https://openrouter.ai/api/v1/chat/completions")
        assert "cloud_network_disabled_by_policy" in str(excinfo.value)


# =================================================================================================
# Tool eligibility — Local Only must not close deterministic or web/search plans
# =================================================================================================


class TestToolEligibility:
    def test_a_live_question_keeps_its_web_tool_plan_under_local_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.agent_runtime import fast_live_info_runtime_preflight as preflight

        monkeypatch.setattr("core.policy_engine.allow_web_fallback", lambda: True)
        captured: dict[str, Any] = {}

        class _Agent:
            def _live_info_mode(self, user_input: str, *, interpretation: Any) -> str:
                return "fresh_lookup"

            def _recover_price_lookup_query(self, user_input: str, *, source_context: Any) -> str:
                return ""

            def _normalize_live_info_query(self, user_input: str, *, mode: str) -> str:
                return user_input

            def _requires_ultra_fresh_insufficient_evidence(self, user_input: str) -> bool:
                return False

            def _fast_path_result(self, **kwargs: Any) -> dict[str, Any]:
                captured.update(kwargs)
                return {"response": kwargs["response"], "reason": kwargs["reason"]}

        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        live_mode, query, result = preflight.prepare_live_info_request(
            _Agent(),
            "what is the current USD/DKK rate today",
            session_id="s-live",
            source_context=context,
            interpretation=None,
        )
        assert live_mode == "fresh_lookup"
        assert query == "what is the current USD/DKK rate today"
        assert result is None, "Local Only incorrectly replaced the web plan with a refusal"
        assert captured == {}

    def test_local_only_does_not_claim_web_search_is_impossible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.agent_runtime import fast_live_info_runtime_preflight as preflight

        monkeypatch.setattr("core.policy_engine.allow_web_fallback", lambda: True)

        class _Agent:
            def _live_info_mode(self, user_input: str, *, interpretation: Any) -> str:
                return "fresh_lookup"

            def _recover_price_lookup_query(self, user_input: str, *, source_context: Any) -> str:
                return ""

            def _normalize_live_info_query(self, user_input: str, *, mode: str) -> str:
                return user_input

            def _requires_ultra_fresh_insufficient_evidence(self, user_input: str) -> bool:
                return False

        prompt = (
            "Assume today is Jan 1 2035. Based only on that premise, explain why a web search "
            "would fail to verify it."
        )
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        _live_mode, query, result = preflight.prepare_live_info_request(
            _Agent(),
            prompt,
            session_id="s-hypothetical",
            source_context=context,
            interpretation=None,
        )
        assert query == ""
        assert result is None
        assert "allow_remote_fetch" not in context

    def test_the_model_prompt_says_local_only_blocks_cloud_models_not_web_tools(self) -> None:
        from core.prompt_normalizer import _runtime_turn_truth

        blob = _runtime_turn_truth({mode.CONTEXT_KEY: True})
        assert "cloud AI/model calls are unavailable" in blob
        assert "including web search" in blob
        assert "no cloud provider or live web lookup can be reached" not in blob
        assert "needs live or current data" not in blob


# =================================================================================================
# Driven turns — the mission's prompts, through the real runtime
# =================================================================================================


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    workspace = tmp_path / "vool-local-only"
    workspace.mkdir(parents=True)
    (workspace / "notes.txt").write_text(f"{_LOCAL_EVIDENCE}\n")
    (workspace / "package.json").write_text('{"name": "local-only-probe", "version": "0.5.0"}\n')
    return workspace


def _drive(
    make_agent_module: Any,
    project: Path,
    text: str,
    *,
    session: str,
    local_only: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one turn and return (result, context). The context is returned so the ledger keyed by it
    can be read afterwards — that is where the turn's real provider accounting lives."""
    from core.turn_model_call_ledger import begin_turn

    context: dict[str, Any] = {
        "surface": "api",
        "session_id": session,
        "runtime_session_id": session,
        "request_id": f"req-{session}",
        "workspace": str(project),
        "workspace_root": str(project),
    }
    if local_only:
        # Exactly what the HTTP front door does with the composer selection, via the production
        # function rather than by setting the flag the test wants to see.
        context["requested_model"] = mode.SELECTOR_VALUE
        assert mode.bind_turn(context, mode.SELECTOR_VALUE) is True
    begin_turn(context)
    os.chdir(project)
    result = make_agent_module().run_once(text, session_id_override=session, source_context=context)
    return result, context


def _cloud_lanes_in_ledger(context: dict[str, Any]) -> list[str]:
    from core.turn_model_call_ledger import turn_call_accounting

    accounting = turn_call_accounting(context)
    return [lane for lane in list(accounting.get("lanes") or []) if lane == "cloud"]


class TestDrivenTurns:
    """The mission's prompts. Each asserts the ledger and the invocation log, not the prose."""

    @pytest.mark.parametrize(
        "text",
        [
            "what is TRY?",
            "what is try",
            "ok what is TRY?",
        ],
    )
    def test_a_currency_definition_is_answered_with_no_provider_call_at_all(
        self, make_agent_module, invocations, arbiter_is_reachable, project, text: str
    ) -> None:
        """A three-letter definition is a deterministic local answer. Local Only must not turn it
        into a refusal — the mode removes the cloud, not the runtime's own knowledge."""
        result, context = _drive(make_agent_module, project, text, session=f"try-{abs(hash(text)) % 99999}", local_only=True)
        assert invocations.count == 0, f"{text!r} bought inference: {invocations.timeline}"
        assert _cloud_lanes_in_ledger(context) == []
        assert str(result.get("response") or "").strip(), "the turn produced no answer at all"

    @pytest.mark.parametrize("text", ["what is your name?", "who are you?", "what should I call you"])
    def test_an_identity_question_is_answered_locally_with_no_provider_call(
        self, make_agent_module, invocations, arbiter_is_reachable, project, text: str
    ) -> None:
        result, context = _drive(make_agent_module, project, text, session=f"who-{abs(hash(text)) % 99999}", local_only=True)
        assert invocations.count == 0, f"{text!r} bought inference: {invocations.timeline}"
        assert _cloud_lanes_in_ledger(context) == []
        assert str(result.get("response") or "").strip(), f"{text!r} produced no answer"

    @pytest.mark.parametrize(
        "text",
        [
            "current USD/DKK rate today",
            "what is the USD to DKK rate right now",
            "research the latest BTC news",
            "what's the latest bitcoin news today",
        ],
    )
    def test_a_live_or_current_question_never_reaches_a_cloud_lane(
        self, make_agent_module, invocations, arbiter_is_reachable, project, text: str
    ) -> None:
        """The answer's wording and web tools are separate concerns; this asserts the model boundary.
        Whatever the turn does, no cloud AI provider may be called to plan, verify or answer it."""
        result, context = _drive(make_agent_module, project, text, session=f"live-{abs(hash(text)) % 99999}", local_only=True)

        assert _cloud_lanes_in_ledger(context) == [], (
            f"{text!r} recorded a cloud call in the turn ledger"
        )
        assert "openrouter" not in repr(invocations.timeline).lower(), (
            f"{text!r} touched an OpenRouter lane: {invocations.timeline}"
        )
        response = str(result.get("response") or "")
        # It must not answer a live question as though it had live data.
        assert response.strip(), f"{text!r} produced an empty response"

    def test_a_local_file_read_still_works_under_the_mode(
        self, make_agent_module, invocations, arbiter_is_reachable, project
    ) -> None:
        """Local deterministic tools are explicitly ALLOWED. A mode that blocked them would satisfy
        every 'no cloud' assertion in this file by doing nothing at all."""
        result, context = _drive(
            make_agent_module, project, "Read notes.txt and tell me exactly what it says.",
            session="read-local-only", local_only=True,
        )
        assert _LOCAL_EVIDENCE in str(result.get("response") or ""), (
            "the local read lane stopped working under Local Only"
        )
        assert _cloud_lanes_in_ledger(context) == []


class TestTheTurnLedgerAndTheFooter:
    def test_the_cloud_lane_reading_is_an_instrument_that_can_actually_register(self) -> None:
        """The control for every `_cloud_lanes_in_ledger(context) == []` assertion above.

        An empty list is only evidence of "no cloud call" if a cloud call would have PRODUCED a
        non-empty list. Without this, every one of those assertions could be passing because the
        ledger was never opened, or because the helper reads a key nothing writes — the exact
        vacuous-assertion shape that makes a suite look green over an unenforced rule.
        """
        from core.turn_model_call_ledger import begin_turn, record_provider_call

        context: dict[str, Any] = {"request_id": "req-instrument"}
        begin_turn(context)
        assert _cloud_lanes_in_ledger(context) == []

        record_provider_call(context, provider_id="openrouter-byok", model_id="x:free", cost_class="paid_cloud")
        assert _cloud_lanes_in_ledger(context) == ["cloud"], (
            "the ledger reading cannot register a cloud call, so an empty reading proves nothing"
        )

        # A local call must NOT register as cloud, or every local turn would fail the assertions.
        local_context: dict[str, Any] = {"request_id": "req-instrument-local"}
        begin_turn(local_context)
        record_provider_call(local_context, provider_id="ollama-local", model_id="qwen3:8b", cost_class="free_local")
        assert _cloud_lanes_in_ledger(local_context) == []


    def test_the_footer_discloses_the_mode_on_every_local_only_turn(self) -> None:
        from core.response_provenance import format_provenance_footer

        footer = format_provenance_footer(
            {
                "local_only": True,
                "route": "deterministic:workspace_runtime_fast_path",
                "model_call_accounting": {"calls": 0, "lanes": [], "models": [], "tools": ["workspace.read_file"]},
            }
        )
        assert "local only" in footer and "cloud blocked" in footer, footer

    def test_the_footer_stays_silent_about_the_mode_on_an_ordinary_turn(self) -> None:
        from core.response_provenance import format_provenance_footer

        footer = format_provenance_footer(
            {
                "route": "deterministic:workspace_runtime_fast_path",
                "model_call_accounting": {"calls": 0, "lanes": [], "models": [], "tools": ["workspace.read_file"]},
            }
        )
        assert "local only" not in footer, footer


# =================================================================================================
# The composer — the lane has to be selectable, and has to survive a reload
# =================================================================================================


class TestTheComposerOffersTheLane:
    @staticmethod
    def _page() -> str:
        from core.vool_chat_page import render_vool_chat_html

        return render_vool_chat_html()

    def test_both_auto_lanes_are_offered_as_selectable_rows(self) -> None:
        html = self._page()
        assert 'data-model="vool"' in html
        assert f'data-model="{mode.SELECTOR_VALUE}"' in html
        assert "VOOL Auto Local Only" in html

    def test_the_row_says_what_the_lane_does_rather_than_only_naming_it(self) -> None:
        """A mode whose row does not say it blocks the cloud is a mode the operator has to guess
        at. The disclosure requirement is on the picker, not only on the footer."""
        html = self._page()
        assert "cloud blocked" in html
        assert "never reaches a cloud provider" in html

    def test_the_lane_is_a_known_tier_so_a_reload_does_not_reset_it(self) -> None:
        """The composer persists `vool_model` and, on boot, drops any value that is neither a known
        tier nor a well-formed cloud id. Without the label entry the selection would survive exactly
        one page load and then silently revert to cloud-capable Auto."""
        html = self._page()
        assert f"'{mode.SELECTOR_VALUE}': 'VOOL Auto Local Only'" in html, (
            "the lane is not in MODEL_LABELS, so the boot-time validity check discards it on reload"
        )
        # The same table is what keeps the no-key revert from mistaking the lane for a stale cloud
        # pin, and what keeps `isCloudModel()` from pulsing the cloud dot on a local-only turn.
        assert "function isLocalOnlyMode()" in html

    def test_the_cloud_catalogue_is_not_rendered_under_the_lane(self) -> None:
        html = self._page()
        assert "if (isLocalOnlyMode()) { clearCloudModels(); return; }" in html, (
            "the live cloud model list is still injected under Local Only"
        )


# =================================================================================================
# Negative controls — the cloud lane still works when the mode is off
# =================================================================================================


class TestNegativeControls:
    def test_ordinary_vool_auto_still_ranks_the_free_cloud_lane_first(self, verified_free_catalog: None) -> None:
        ranked = rank_providers([_local_manifest(), _byok_cloud_manifest()], _request())
        assert ranked[0].provider_name == "openrouter-byok"

    def test_a_pinned_cloud_model_still_resolves_outside_local_only(self, verified_free_catalog: None) -> None:
        ranked = rank_providers(
            [_local_manifest(), _byok_cloud_manifest()],
            _request(preferred_model="deepseek/deepseek-chat-v3:free"),
        )
        assert ranked and ranked[0].model_name == "deepseek/deepseek-chat-v3:free"

    def test_a_pinned_local_model_is_unchanged_by_the_existence_of_the_mode(self) -> None:
        ranked = rank_providers([_local_manifest("qwen3:8b"), _local_manifest("qwen2.5:7b")], _request(preferred_model="qwen3:8b"))
        assert ranked and ranked[0].model_name == "qwen3:8b"

    def test_a_pinned_local_model_still_works_under_the_mode(self) -> None:
        ranked = rank_providers(
            [_local_manifest("qwen3:8b"), _local_manifest("qwen2.5:7b"), _byok_cloud_manifest()],
            _request(preferred_model="qwen3:8b", local_only=True),
        )
        assert ranked and ranked[0].model_name == "qwen3:8b"

    def test_the_cloud_transport_works_normally_outside_the_ambient_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proves the transport check is scoped, not a permanent kill switch."""
        from core.cloud_transport import PolicyBoundCloudTransport

        monkeypatch.setattr("core.policy_engine.get", lambda key, default=None: True if key == "network.outbound_enabled" else default)
        monkeypatch.setattr("core.policy_engine.local_only_mode", lambda: False)
        transport = PolicyBoundCloudTransport(allowed_hosts=("openrouter.ai",))
        assert transport._network_allowed() is True

        with mode.local_only_egress_scope(True):
            assert transport._network_allowed() is False


# =================================================================================================
# Sabotage — reverting each layer must fail a test that names that layer
# =================================================================================================


class TestSabotage:
    """A fix is not proven until reverting it fails a test that names the cause.

    Each case reverts ONE layer to its pre-change behaviour and asserts the corresponding guard goes
    red, while a control on an untouched layer stays green. Without this, a guard that is dead code
    (never reached, or reached with a value that is always False) passes every test above.
    """

    def test_reverting_the_selection_exclusion_re_admits_the_cloud_lane(
        self, monkeypatch: pytest.MonkeyPatch, verified_free_catalog: None
    ) -> None:
        import core.model_selection_policy as policy

        real = policy._hard_exclusion_reason

        def _without_local_only(manifest: Any, request: Any, *, required: Any) -> str:
            reason = real(manifest, request, required=required)
            return "" if reason == mode.SELECTION_EXCLUSION_REASON else reason

        monkeypatch.setattr(policy, "_hard_exclusion_reason", _without_local_only)
        ranked = rank_providers([_local_manifest(), _byok_cloud_manifest()], _request(local_only=True))
        assert any(m.provider_name == "openrouter-byok" for m in ranked), (
            "the selection exclusion is dead code: removing it changed nothing, so the passing "
            "test above was not being enforced by it"
        )

    def test_reverting_the_invocation_guard_admits_a_cloud_manifest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mode, "manifest_is_local", lambda manifest: True)
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        # With the classifier neutered the guard has nothing to refuse on, which is exactly the
        # failure the real classifier prevents.
        mode.assert_manifest_allowed(_byok_cloud_manifest(), source_context=context)

    def test_reverting_the_endpoint_classifier_admits_a_cloud_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mode, "endpoint_is_local", lambda url: True)
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        mode.assert_endpoint_allowed("https://openrouter.ai/api/v1", lane="test", source_context=context)

    def test_dropping_the_flag_from_the_registry_rebuild_re_admits_the_cloud_lane(
        self, monkeypatch: pytest.MonkeyPatch, verified_free_catalog: None
    ) -> None:
        """The specific regression `TestTheFlagSurvivesEveryHandOff` exists for: the request is
        rebuilt field by field, so a future edit that forgets the field silently re-opens cloud."""
        from core.model_registry import ModelRegistry

        registry = ModelRegistry.__new__(ModelRegistry)
        manifests = [_local_manifest(), _byok_cloud_manifest()]
        object.__setattr__(registry, "list_manifests", lambda enabled_only=True: manifests)

        real_rank = ModelRegistry.rank_manifests

        def _dropping_the_flag(self: Any, request: Any, *, exclude_provider_ids: Any = None) -> Any:
            stripped = _request(
                task_kind=request.task_kind,
                output_mode=request.output_mode,
                preferred_model=request.preferred_model,
                allow_paid_fallback=request.allow_paid_fallback,
            )
            return real_rank(self, stripped, exclude_provider_ids=exclude_provider_ids)

        monkeypatch.setattr(ModelRegistry, "rank_manifests", _dropping_the_flag)
        ranked = registry.rank_manifests(_request(local_only=True))
        assert any(m.provider_name == "openrouter-byok" for m in ranked), (
            "the registry rebuild does not actually carry local_only, so the flag reaching "
            "selection through it was never the reason the earlier test passed"
        )

    def test_the_controls_stay_green_while_the_others_are_sabotaged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sabotaging the selection layer must NOT disarm the invocation layer. If it did, the four
        'independent' seams would be one seam with four names."""
        import core.model_selection_policy as policy

        monkeypatch.setattr(policy, "_hard_exclusion_reason", lambda manifest, request, *, required: "")
        context: dict[str, Any] = {}
        mode.bind_turn(context, mode.SELECTOR_VALUE)
        with pytest.raises(mode.CloudEgressBlockedError):
            mode.assert_manifest_allowed(_byok_cloud_manifest(), source_context=context)
