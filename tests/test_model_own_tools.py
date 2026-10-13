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

SEARCH = {"type": own.OPENROUTER_WEB_SEARCH, "parameters": {"max_results": own.SEARCH_MAX_RESULTS}}


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
#: A pinned turn the server stamped as carrying the provider's search.
SEARCHING = {**PINNED, "model_own_tools_turn": True, "model_own_tools_search": True}


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


def test_only_a_reserved_pinned_openrouter_call_gets_the_provider_search(switched_on) -> None:
    reserved = object()
    grant = own.provider_tool_grant(manifest=OPENROUTER, source_context=SEARCHING, authorized_paid_call=reserved)
    assert grant is not None and grant.tools == (SEARCH,)
    # No reservation: a search would bill the key outside the caps.
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=SEARCHING, authorized_paid_call=None) is None
    # A provider whose API offers no server tool on VOOL's endpoint.
    assert own.provider_tool_grant(manifest=ANTHROPIC, source_context=SEARCHING, authorized_paid_call=reserved) is None
    # A local model.
    assert own.provider_tool_grant(manifest=LOCAL, source_context=SEARCHING, authorized_paid_call=reserved) is None
    # Auto's sticky preference is not an owner pick.
    sticky = {**SEARCHING, "model_selection": "sticky"}
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=sticky, authorized_paid_call=reserved) is None
    # A turn the server did not stamp for search, or whose search the retrieval door withdrew.
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=PINNED, authorized_paid_call=reserved) is None
    withdrawn = dict(SEARCHING)
    own.withdraw_provider_search(withdrawn)
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=withdrawn, authorized_paid_call=reserved) is None


def test_the_switch_off_grants_nothing_even_with_a_reservation() -> None:
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=SEARCHING, authorized_paid_call=object()) is None


def test_a_verified_free_model_never_gets_a_paid_search(switched_on, monkeypatch) -> None:
    monkeypatch.setattr("core.model_selection_policy.is_verified_free_cloud_manifest", lambda manifest: True)
    assert own.provider_tool_grant(manifest=OPENROUTER, source_context=SEARCHING, authorized_paid_call=object()) is None


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
    monkeypatch.setattr("core.model_pricing.estimate_call_usd", lambda **_: SimpleNamespace(usd=0.22))
    limits = SimpleNamespace(per_call_usd=0.25, per_task_usd=1.0, daily_usd=5.0, monthly_usd=25.0)
    plain = pcr._reservation_ceiling_usd(provider_id="openrouter-byok:m", model_id="m", prompt_tokens=10, completion_tokens=10, limits=limits)
    searched = pcr._reservation_ceiling_usd(
        provider_id="openrouter-byok:m", model_id="m", prompt_tokens=10, completion_tokens=10, limits=limits,
        provider_tool_allowance_usd=own.SEARCH_ALLOWANCE_USD,
    )
    assert plain == pytest.approx(0.25)
    assert searched == pytest.approx(0.22 + own.SEARCH_ALLOWANCE_USD)
    with pytest.raises(PermissionError, match="per_call_spend_cap_exceeded"):
        reserve_spend(
            model_call_id="own-tools-over-cap", task_id="t", subtask_id="s", model_id="m", maximum_usd=searched, limits=limits,
        )


def test_the_allowance_is_held_only_for_a_stamped_turn_on_a_search_provider(switched_on) -> None:
    stamped = {own.TURN_CONTEXT_KEY: True, own.TURN_SEARCH_CONTEXT_KEY: True}
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context=stamped) == own.SEARCH_ALLOWANCE_USD
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context={}) == 0.0
    assert own.provider_tool_reservation_allowance_usd(OPENROUTER, source_context={own.TURN_CONTEXT_KEY: True}) == 0.0
    assert own.provider_tool_reservation_allowance_usd(ANTHROPIC, source_context=stamped) == 0.0


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

    context = dict(SEARCHING)
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
    assert _open(context, CURRENT) is True
    own.record_provider_search(context, _decision(SOURCES))
    assert not successful_retrieval_receipts(context)


def test_a_turn_that_is_not_current_opens_no_receipt_and_its_citations_ground_nothing() -> None:
    from core.retrieval_provenance import successful_retrieval_receipts

    context = dict(SEARCHING)
    assert _open(context, "What is a B-tree?") is True
    assert own.PROVIDER_SEARCH_RECEIPT_KEY not in context
    assert own.turn_has_provider_search(context), "the model may still search; nothing counts as evidence"
    own.record_provider_search(context, _decision(SOURCES))
    assert not successful_retrieval_receipts(context)


def test_after_the_synthesis_freeze_the_door_refuses_the_provider_search() -> None:
    from core.execution_requirements import begin_synthesis_freeze

    context = dict(SEARCHING)
    begin_synthesis_freeze(context, CURRENT)
    assert _open(context, CURRENT) is False
    assert own.PROVIDER_SEARCH_RECEIPT_KEY not in context


def test_a_user_who_forbids_browsing_gets_no_provider_search() -> None:
    text = "what is the price of bitcoin right now? Do not browse or use external tools."
    context = dict(SEARCHING)
    assert _open(context, text) is False
    own.withdraw_provider_search(context)
    assert not own.turn_has_provider_search(context)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(own, "mode_on", lambda: True)
        assert own.provider_tool_grant(manifest=OPENROUTER, source_context=context, authorized_paid_call=object()) is None


def test_a_search_that_cited_nothing_grounds_nothing() -> None:
    from core.retrieval_provenance import successful_retrieval_receipts
    from core.unsourced_current_claim import turn_has_current_evidence

    context = dict(SEARCHING)
    assert _open(context, CURRENT) is True
    own.record_provider_search(context, _decision([]))
    assert not successful_retrieval_receipts(context)
    assert turn_has_current_evidence(source_context=context) is False
