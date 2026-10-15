"""The failure paths and edges of "use the model's own tools" that the served proof does not reach.

The served flow (``tests/test_model_own_tools_served.py``) proves the happy path end to end on a
real daemon. These pin what must stay refused or narrow: which words switch the mode (and which
must not), which calls may carry a provider tool at all, what an adapter refuses to send, what a
paid reservation holds for the search, and that a provider search can only become evidence on a
turn the server itself stamped.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import model_own_tools as own
from core.user_preferences import load_preferences, maybe_handle_preference_command

SEARCH = {
    "type": own.OPENROUTER_WEB_SEARCH,
    "parameters": {"engine": "exa", "max_results": own.SEARCH_MAX_RESULTS, "max_uses": own.SEARCH_MAX_USES},
}


def _manifest(provider_name: str, *, base_url: str, model_name: str = "vendor/model", cost_class: str = "paid_cloud", **metadata):
    return SimpleNamespace(
        provider_name=provider_name,
        provider_id=f"{provider_name}:{model_name}",
        model_name=model_name,
        adapter_type="openai_compatible",
        source_type="http",
        metadata={"cost_class": cost_class, "runtime_family": "openai-compatible", **metadata},
        runtime_config={"base_url": base_url, "api_path": "/chat/completions", "timeout_seconds": 5.0},
    )


OPENROUTER = _manifest("openrouter-byok", base_url="https://openrouter.ai/api/v1")
ANTHROPIC = _manifest("anthropic-byok", base_url="https://api.anthropic.com/v1")
LOCAL = _manifest("ollama-local", base_url="http://127.0.0.1:11434/v1", cost_class="free_local")
PINNED = {"requested_model": "openrouter-byok:vendor/model", "model_selection": "pin"}
#: A pinned turn the server stamped as carrying the provider's search, not yet past the door.
STAMPED = {
    **PINNED,
    "surface": "openclaw",
    "platform": "web_companion",  # the chat page's surface: live fetches allowed by default
    "model_own_tools_turn": True,
    "model_own_tools_search": True,
}


def _admitted() -> dict:
    """A stamped turn whose search the retrieval door admitted (a fresh, unused admission)."""
    context = dict(STAMPED)
    own.admit_provider_search(context)
    return context


def _request(metadata: dict | None = None) -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt="q", messages=[{"role": "user", "content": "q"}], metadata=dict(metadata or {}))


# ---- the chat command -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, action",
    [
        ("use the model's own tools", "on"),
        ("Use the model’s own tools please", "on"),
        ("turn off VOOL's tools", "on"),
        ("/tools model", "on"),
        ("use VOOL's tools", "off"),
        ("turn vool tools back on", "off"),
        ("/tools vool", "off"),
        ("which tools are you using?", "status"),
        ("/tools", "status"),
        ("use your own tools", "on"),
        ("can you use the model's own tools?", "on"),
        ("let the model use its own tools", "on"),
        ("use the model's own tools for this chat", "on"),
        ("use the modelʼs own tools", "on"),
        ("ＵＳＥ the model's own tools", "on"),
        ("stop using your own tools", "off"),
        ("switch back to VOOL", "off"),
    ],
)
def test_the_switch_words_are_recognised(text: str, action: str) -> None:
    assert own.parse_tools_command(text) == action


@pytest.mark.parametrize(
    "text",
    [
        "what tools do you have?",
        "use the tools to search for flights to Rome",
        "can the model use its own tools to book a table?",
        "explain why I should use VOOL's tools instead of a browser plugin",
        "use the model's own tools and tell me the weather in Vilnius",
        "use the AI tools",
        "use the model's tools",
        "use own tools",
        "use native tools",
        "enable built-in tools",
        "use the provider tools",
        "I like your own tools",
    ],
)
def test_a_sentence_that_merely_mentions_tools_does_not_flip_the_switch(text: str) -> None:
    assert own.parse_tools_command(text) == ""
    handled, _ = maybe_handle_preference_command(text)
    assert handled is False
    assert load_preferences().model_own_tools is False


def test_the_command_saves_the_switch_and_reports_the_stored_value() -> None:
    assert load_preferences().model_own_tools is False
    handled, reply = maybe_handle_preference_command("use the model's own tools")
    assert handled and reply == own.REPLY_ON
    assert load_preferences().model_own_tools is True
    handled, reply = maybe_handle_preference_command("use the model's own tools")
    assert handled and reply == own.REPLY_ALREADY_ON
    handled, reply = maybe_handle_preference_command("use VOOL's tools")
    assert handled and reply == own.REPLY_OFF
    assert load_preferences().model_own_tools is False


def test_a_save_that_does_not_stick_is_reported_as_nothing_changed(monkeypatch) -> None:
    monkeypatch.setattr(own, "set_mode", lambda on: False)
    handled, reply = own.handle_tools_command("use the model's own tools")
    assert handled and reply == own.REPLY_FAILED


def test_an_unreadable_preference_file_reads_as_off(monkeypatch) -> None:
    def _broken():
        raise OSError("disk gone")

    monkeypatch.setattr("core.user_preferences.load_preferences", _broken)
    assert own.mode_on() is False


# ---- which calls may carry a provider tool --------------------------------------------------


@pytest.fixture
def switched_on():
    own.set_mode(True)
    yield
    own.set_mode(False)


def test_only_a_reserved_pinned_openrouter_call_gets_the_provider_search() -> None:
    reserved = object()
    grant = own.provider_tool_grant(manifest=OPENROUTER, source_context=_admitted(), authorized_paid_call=reserved)
    assert grant is not None and grant.tools == (SEARCH,)
    # No reservation: a search would bill the key outside the caps.
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=_admitted(), authorized_paid_call=None) is None
    # A provider whose API offers no server tool on VOOL's endpoint.
    assert own.provider_tool_grant(manifest=ANTHROPIC, source_context=_admitted(), authorized_paid_call=reserved) is None
    # A local model.
    assert own.provider_tool_grant(manifest=LOCAL, source_context=_admitted(), authorized_paid_call=reserved) is None
    # Auto's sticky preference is not an owner pick.
    sticky = {**_admitted(), "model_selection": "sticky"}
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=sticky, authorized_paid_call=reserved) is None
    # A turn the server did not stamp, a stamped turn the door never admitted, or one it withdrew.
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=PINNED, authorized_paid_call=reserved) is None
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=dict(STAMPED), authorized_paid_call=reserved) is None
    withdrawn = _admitted()
    own.withdraw_provider_search(withdrawn)
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=withdrawn, authorized_paid_call=reserved) is None


def test_one_answering_call_per_turn_carries_the_search() -> None:
    context = _admitted()
    copy = dict(context)  # the model's copy of the turn context shares the admission
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=context, authorized_paid_call=object()) is not None
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=copy, authorized_paid_call=object()) is None
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context=copy) == 0.0


def test_the_turn_keeps_the_value_it_started_with() -> None:
    # Switched off while the turn is in flight: the stamped, admitted turn still runs as it began
    # (the grant and the allowance read the stamp, never the switch again), so the reservation
    # that held the search allowance is the one the search rides.
    own.set_mode(False)
    context = _admitted()
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context=context) == own.SEARCH_ALLOWANCE_USD
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=context, authorized_paid_call=object()) is not None


def test_a_verified_free_model_never_gets_a_paid_search(monkeypatch) -> None:
    monkeypatch.setattr("core.model_selection_policy.is_verified_free_cloud_manifest", lambda manifest: True)
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=_admitted(), authorized_paid_call=object()) is None


def test_the_turn_stamp_is_the_servers_own_and_a_caller_copy_is_dropped(switched_on) -> None:
    context = {**PINNED, own.TURN_CONTEXT_KEY: True, own.TURN_SEARCH_CONTEXT_KEY: True}
    own.set_mode(False)
    assert own.stamp_turn(context, resolve_manifest=lambda _ctx: OPENROUTER) is False
    assert own.TURN_CONTEXT_KEY not in context and own.TURN_SEARCH_CONTEXT_KEY not in context
    own.set_mode(True)
    assert own.stamp_turn(context, resolve_manifest=lambda _ctx: OPENROUTER) is True
    assert own.turn_has_provider_search(context)
    # A cloud model without a provider search: VOOL's tools are off, no search is promised.
    context = dict(PINNED)
    assert own.stamp_turn(context, resolve_manifest=lambda _ctx: ANTHROPIC) is True
    assert own.turn_stamped(context) and not own.turn_has_provider_search(context)
    # A local pin keeps VOOL's tools.
    context = dict(PINNED)
    assert own.stamp_turn(context, resolve_manifest=lambda _ctx: LOCAL) is False


# ---- what the adapter will and will not send ------------------------------------------------


def _adapter(manifest) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(manifest)


def _granted(*tools) -> dict:
    return {own.REQUEST_METADATA_KEY: {"tools": [dict(tool) for tool in tools]}}


def test_the_granted_call_carries_the_search_tool_and_nothing_else() -> None:
    payload = _adapter(OPENROUTER)._build_openai_payload(_request(_granted(SEARCH)), force_json=False, stream=False)
    assert payload["tools"] == [SEARCH]
    assert "tool_choice" not in payload


def test_a_tool_off_the_allowlist_is_refused_before_anything_is_sent() -> None:
    wallet = {"type": "function", "function": {"name": "wallet_send"}}
    with pytest.raises(ValueError, match="allowlist"):
        _adapter(OPENROUTER)._build_openai_payload(_request(_granted(wallet)), force_json=False, stream=False)


@pytest.mark.parametrize(
    "manifest",
    [
        ANTHROPIC,
        _manifest("openrouter-byok", base_url="https://openrouter.ai/api/v1", model_name="vendor/model:free", verified_free=True),
    ],
    ids=["other-provider", "verified-free"],
)
def test_a_lane_that_cannot_carry_the_search_refuses_the_call(manifest) -> None:
    with pytest.raises(RuntimeError, match="cannot carry the model's own tools"):
        _adapter(manifest)._build_openai_payload(_request(_granted(SEARCH)), force_json=False, stream=False)


def test_the_providers_sources_reach_the_answer_and_the_metadata_on_a_stream() -> None:
    adapter = _adapter(OPENROUTER)
    citation = {"type": "url_citation", "url_citation": {"url": "https://example.org/a", "title": "A", "content": "snippet"}}
    response = mock.Mock()
    response.headers = {}
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        'data: {"choices": [{"delta": {"content": "Answer."}}]}',
        "data: " + json.dumps({"choices": [{"delta": {"annotations": [citation]}}]}),
        'data: {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "cost": 0.004}}',
        "data: [DONE]",
    ]
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        chunks = list(adapter.stream_text_task(_request(_granted(SEARCH))))
    assert post.call_args.kwargs["json"]["tools"] == [SEARCH]
    text = "".join(chunk.delta_text for chunk in chunks)
    assert text.startswith("Answer.") and "https://example.org/a" in text
    search = chunks[-1].provider_metadata[own.PROVIDER_SEARCH_METADATA_KEY]
    assert search["sources"] == [{"title": "A", "url": "https://example.org/a", "content": "snippet"}]


def test_an_ungranted_call_reports_no_provider_search() -> None:
    assert own.provider_search_metadata((), [{"url": "https://example.org"}]) == {}


# ---- money: the reservation holds the search ------------------------------------------------


def test_the_reservation_holds_the_search_and_a_call_over_the_cap_is_refused(monkeypatch) -> None:
    from core import paid_call_reservation as pcr
    from core.model_spend_ledger import reserve_spend

    monkeypatch.setattr("core.model_pricing.reservation_price_refusal", lambda *_: "")
    monkeypatch.setattr("core.model_pricing.estimate_call_usd", lambda **_: SimpleNamespace(usd=0.24))
    limits = SimpleNamespace(per_call_usd=0.25, per_task_usd=1.0, daily_usd=5.0, monthly_usd=25.0)
    plain = pcr._reservation_ceiling_usd(provider_id="openrouter-byok:m", model_id="m", prompt_tokens=10, completion_tokens=10, limits=limits)
    searched = pcr._reservation_ceiling_usd(
        provider_id="openrouter-byok:m", model_id="m", prompt_tokens=10, completion_tokens=10, limits=limits,
        provider_tool_allowance_usd=own.SEARCH_ALLOWANCE_USD,
    )
    assert plain == pytest.approx(0.25)
    assert searched == pytest.approx(0.24 + own.SEARCH_ALLOWANCE_USD)
    with pytest.raises(PermissionError, match="per_call_spend_cap_exceeded"):
        reserve_spend(
            model_call_id="own-tools-over-cap", task_id="t", subtask_id="s", model_id="m", maximum_usd=searched, limits=limits,
        )


def test_the_allowance_is_held_only_for_an_admitted_search_on_a_search_provider() -> None:
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context=_admitted()) == own.SEARCH_ALLOWANCE_USD
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context={}) == 0.0
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context=dict(STAMPED)) == 0.0
    assert own.provider_tool_reservation_allowance_usd(ANTHROPIC, source_context=_admitted()) == 0.0


# ---- evidence: the provider search passes VOOL's retrieval door ----------------------------


def _decision(sources):
    return SimpleNamespace(details={own.PROVIDER_SEARCH_METADATA_KEY: {"tools": [own.OPENROUTER_WEB_SEARCH], "sources": sources}})


SOURCES = [{"title": "A", "url": "https://example.org/a", "content": "Bitcoin is at $64,102."}]
CURRENT = "what is the price of bitcoin right now?"


def _open(context: dict, text: str) -> bool:
    from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin

    return ResearchToolLoopFacadeMixin._open_provider_search_receipt(SimpleNamespace(), context, text)


def test_a_current_turn_opens_the_receipt_before_synthesis_and_the_sources_close_it() -> None:
    from core.retrieval_provenance import successful_retrieval_receipts

    context = dict(STAMPED)
    assert _open(context, CURRENT) is True
    assert own.PROVIDER_SEARCH_RECEIPT_KEY in context
    own.record_provider_search(context, _decision(SOURCES))
    receipts = successful_retrieval_receipts(context)
    assert len(receipts) == 1 and receipts[0]["provider_id"] == "model_provider_search"
    assert receipts[0]["keyed_or_keyless"] == "keyed"
    assert own.PROVIDER_SEARCH_RECEIPT_KEY not in context


def test_an_unstamped_turn_records_nothing_whatever_the_provider_cited() -> None:
    from core.retrieval_provenance import successful_retrieval_receipts

    context: dict = {}
    assert _open(context, CURRENT) is False
    own.record_provider_search(context, _decision(SOURCES))
    assert not successful_retrieval_receipts(context)


@pytest.mark.parametrize(
    "text, extra",
    [
        ("What is a B-tree?", {}),
        ("Without using any tools, what is the price of bitcoin right now?", {}),
        ("Offline only: what's the bitcoin price today?", {}),
        ("what is the price of bitcoin right now? Do not browse or use external tools.", {}),
        (CURRENT, {"action_policy": "forbidden"}),
        (CURRENT, {"allow_remote_fetch": False}),
    ],
    ids=["not-current", "without-tools", "offline-only", "do-not-browse", "action-forbidden", "remote-fetch-veto"],
)
def test_a_turn_vool_itself_would_not_search_sends_no_provider_search(text, extra) -> None:
    from core.retrieval_provenance import successful_retrieval_receipts

    context = {**STAMPED, **extra}
    assert _open(context, text) is False
    assert own.PROVIDER_SEARCH_RECEIPT_KEY not in context
    own.withdraw_provider_search(context)
    assert not own.turn_has_provider_search(context)
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=context, authorized_paid_call=object()) is None
    own.record_provider_search(context, _decision(SOURCES))
    assert not successful_retrieval_receipts(context)


def test_after_the_synthesis_freeze_the_door_refuses_the_provider_search() -> None:
    from core.execution_requirements import begin_synthesis_freeze

    context = dict(STAMPED)
    begin_synthesis_freeze(context, CURRENT)
    assert _open(context, CURRENT) is False
    assert own.PROVIDER_SEARCH_RECEIPT_KEY not in context


def test_a_search_that_cited_nothing_grounds_nothing() -> None:
    from core.retrieval_provenance import successful_retrieval_receipts
    from core.unsourced_current_claim import turn_has_current_evidence

    context = dict(STAMPED)
    assert _open(context, CURRENT) is True
    own.record_provider_search(context, _decision([]))
    assert not successful_retrieval_receipts(context)
    assert turn_has_current_evidence(source_context=context) is False


def test_a_failed_answering_call_closes_the_receipt_as_failed() -> None:
    from core.retrieval_provenance import successful_retrieval_receipts

    context = dict(STAMPED)
    assert _open(context, CURRENT) is True
    own.close_provider_search_on_failure(context, RuntimeError("stream broke"))
    assert own.PROVIDER_SEARCH_RECEIPT_KEY not in context
    assert not successful_retrieval_receipts(context)
    (terminal,) = list(context.get("web_retrieval_receipts") or [])
    assert terminal.get("status") == "failed", terminal


# ---- money: the search fee is counted, and unpriced provider tools are never used ----------


def test_the_search_engine_is_pinned_to_a_priced_one_with_a_bounded_number_of_searches() -> None:
    (tool,) = own.provider_tools_for(OPENROUTER)
    assert tool["parameters"]["engine"] == "exa", "the default engine can hand off to a provider's own unpriced search"
    assert tool["parameters"]["max_uses"] == own.SEARCH_MAX_USES
    assert pytest.approx(own.SEARCH_MAX_USES * own.SEARCH_USD_PER_REQUEST_BOUND) == own.SEARCH_ALLOWANCE_USD


@pytest.mark.parametrize(
    "usage, fee",
    [
        ({"server_tool_use": {"web_search_requests": 1}}, 0.01),
        ({"server_tool_use": {"web_search_requests": 0}}, 0.0),
        ({"server_tool_use": {"web_search_requests": 5}}, 0.05),
        ({}, 0.02),
        ({"server_tool_use": {"web_search_requests": "lots"}}, 0.02),
    ],
    ids=["one-search", "no-search", "more-than-allowed", "unreported", "unreadable"],
)
def test_the_search_fee_is_counted_from_the_providers_count_or_at_the_full_allowance(usage, fee) -> None:
    assert own.provider_search_fee_usd(usage) == pytest.approx(fee)


def test_a_provider_total_that_left_the_search_out_cannot_settle_below_it(monkeypatch) -> None:
    monkeypatch.setattr("core.model_pricing.estimate_call_usd", lambda **_: SimpleNamespace(usd=0.001, known=True))
    low = own.searched_call_cost_estimate(
        provider_id="openrouter", model_id="vendor/model", usage={"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001}
    )
    assert low["usd"] == pytest.approx(0.001 + 0.02) and low["known"] is False
    high = own.searched_call_cost_estimate(
        provider_id="openrouter", model_id="vendor/model", usage={"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.08}
    )
    assert high["usd"] == pytest.approx(0.08) and high["basis"] == "provider_reported"
    assert own.searched_call_cost_estimate(provider_id="openrouter", model_id="vendor/model", usage={}) is None


def test_the_settled_and_shown_cost_of_a_searched_call_includes_the_search(monkeypatch) -> None:
    import core.agent_runtime.agent  # import order: the router imports the agent runtime
    from core.memory_first_router import _record_response_usage, _response_actual_usd

    monkeypatch.setattr("core.model_pricing.estimate_call_usd", lambda **_: SimpleNamespace(usd=0.001, known=True))
    body = {
        "choices": [{"message": {"content": "Answer."}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
    }
    response = mock.Mock(status_code=200, headers={})
    response.raise_for_status.return_value = None
    response.json.return_value = body
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response):
        result = _adapter(OPENROUTER).run_text_task(_request(_granted(SEARCH)))
    assert _response_actual_usd(OPENROUTER, result) == pytest.approx(0.021)
    shown = _record_response_usage(OPENROUTER, result)
    assert shown["usd_priced"] == pytest.approx(0.021) and shown["usd_priced_known"] is False


@pytest.mark.parametrize(
    "slot, model, refused",
    [
        ("groq", "groq/compound", True),
        ("groq", "compound-beta-mini", True),
        ("groq", "llama-3.3-70b-versatile", False),
        ("openai", "gpt-4o-search-preview", True),
        ("openai", "o4-mini-deep-research", True),
        ("openai", "gpt-4.1-mini", False),
        ("openrouter", "openai/gpt-4o-search-preview", False),
    ],
)
def test_models_that_run_unpriced_tools_of_their_own_are_known(slot, model, refused) -> None:
    assert own.unpriced_builtin_tools(slot, model) is refused


def test_the_adapter_never_sends_to_a_model_with_unpriced_tools_of_its_own() -> None:
    compound = _manifest("groq-byok", base_url="https://api.groq.com/openai/v1", model_name="groq/compound", credential_slot="groq")
    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post:
        with pytest.raises(RuntimeError, match="unpriced_builtin_tools"):
            _adapter(compound).run_text_task(_request())
    post.assert_not_called()


def test_such_a_model_cannot_be_pinned() -> None:
    from core import cloud_escalation_policy as cep
    from core.cloud_model_control import set_cloud_model

    before = cep.load_policy()
    ok, message, chosen = set_cloud_model("groq/compound", provider="groq", owner_local=True, confirm_paid=True)
    assert ok is False and chosen == ""
    assert "spending limits" in message
    after = cep.load_policy()
    assert (after.model, after.provider) == (before.model, before.provider)


def test_a_message_that_names_one_chat_is_told_the_switch_covers_every_chat() -> None:
    handled, reply = own.handle_tools_command("use the model's own tools for this chat")
    assert handled and reply.endswith(own.REPLY_ALL_CHATS)
    own.set_mode(False)


def test_a_search_title_can_never_open_a_link_of_its_own_and_sources_are_listed_once() -> None:
    sources = [
        {"title": "Click](https://evil.example/phish) [x", "url": "https://example.org/a"},
        {"title": "A again", "url": "https://example.org/a"},
    ]
    text = own.with_sources("Answer.", sources)
    assert text.count("https://example.org/a") == 1
    assert "](https://evil.example" not in text


def test_switching_leaves_an_audit_record(monkeypatch) -> None:
    logged = []
    monkeypatch.setattr("core.audit_logger.log", lambda event, **kw: logged.append((event, kw)))
    own.set_mode(True)
    own.set_mode(True)
    own.set_mode(False)
    assert [(event, kw["details"]["on"]) for event, kw in logged] == [
        ("model_own_tools_changed", True),
        ("model_own_tools_changed", False),
    ]


@pytest.mark.parametrize("stored", ["false", "0", 1, "true", None, [True]])
def test_only_a_stored_json_true_turns_it_on(stored) -> None:
    from core.user_preferences import _prefs_path

    path = _prefs_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["model_own_tools"] = stored
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    assert own.mode_on() is False
