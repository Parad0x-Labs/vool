from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest import mock

import pytest

from core.agent_runtime.turn_frontdoor import _currency_reply
from core.fresh_data.fx import FxQuote, FxQuoteStatus, resolve_fx_quote
from core.remote_fetch_policy import (
    remote_fetch_attempt_count,
    remote_fetch_policy_scope,
)

SET5_24 = (
    '"I will TRY to MOP the floor, but I am MAD." How much is 100 TRY + 100 MOP in MAD? '
    "Just kidding, do NOT calculate forex rates. Just list the three countries that use those "
    "official currency codes."
)


def _fresh_payload(rate: str = "0.025") -> dict[str, str]:
    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "rate": rate,
    }


def _event_capture() -> tuple[list[dict[str, object]], object]:
    events: list[dict[str, object]] = []

    def capture(_context, *, event_type, message, details=None):
        events.append(
            {
                "event_type": event_type,
                "message": message,
                **dict(details or {}),
            }
        )

    return events, capture


@pytest.mark.parametrize(
    ("prompt", "base", "quote"),
    (
        ("1000 TRY to EUR", "TRY", "EUR"),
        ("Convert 25 GBP to USD", "GBP", "USD"),
        ("What is the current EUR to USD rate?", "EUR", "USD"),
        ("Exchange 400 CAD into JPY", "CAD", "JPY"),
    ),
)
def test_allowed_direct_fx_retrieval_is_counted_and_receipted(
    prompt: str,
    base: str,
    quote: str,
) -> None:
    fetcher = mock.Mock(return_value=_fresh_payload())
    source_context: dict[str, object] = {
        "surface": "api",
        "platform": "api",
        "runtime_session_id": "fx-observability-allowed",
        "allow_remote_fetch": True,
        "fx_fetch_json": fetcher,
    }
    events, capture = _event_capture()

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        mock.patch("core.runtime_task_events.emit_runtime_event", side_effect=capture),
        remote_fetch_policy_scope(source_context),
    ):
        reply = _currency_reply(
            prompt,
            session_id="fx-observability-allowed",
            source_context=source_context,
        )
        web_calls = remote_fetch_attempt_count()

    assert reply is not None
    assert reply["grounded"] == "live_rate"
    assert web_calls == 1
    fetcher.assert_called_once()
    assert [event["event_type"] for event in events] == [
        "fx_retrieval_started",
        "fx_retrieval_completed",
    ]
    started, completed = events
    assert started["schema"] == "vool.fx_retrieval_receipt.v1"
    assert started["base"] == completed["base"] == base
    assert started["quote"] == completed["quote"] == quote
    assert started["direction"] == completed["direction"] == f"{base}_to_{quote}"
    assert started["retrieval_id"] == completed["retrieval_id"]
    assert completed["status"] == FxQuoteStatus.AVAILABLE.value
    assert completed["rate"] == "0.025"
    assert completed["provider_attempt_count"] == 1
    assert completed["observed_at"]
    assert completed["retrieved_at"]
    receipts = source_context["fresh_data_retrieval_receipts"]
    assert receipts == [
        {
            key: completed[key]
            for key in (
                "schema",
                "retrieval_id",
                "kind",
                "base",
                "quote",
                "direction",
                "status",
                "source_providers",
                "source",
                "rate",
                "provider_attempt_count",
                "started_at",
                "completed_at",
                "observed_at",
                "retrieved_at",
                "failure_class",
            )
        }
    ]


@pytest.mark.parametrize(
    "prompt",
    (
        "Get current TRY/EUR without using the web.",
        "Convert 100 TRY to EUR, but do not retrieve anything externally.",
        "1000 TRY to EUR at the supplied rate of 0.025.",
    ),
)
def test_forbidden_or_closed_world_fx_turn_has_zero_call_and_zero_receipt(prompt: str) -> None:
    fetcher = mock.Mock(side_effect=AssertionError("forbidden retrieval reached provider"))
    source_context: dict[str, object] = {
        "surface": "api",
        "platform": "api",
        "runtime_session_id": "fx-observability-forbidden",
        "allow_remote_fetch": True,
        "fx_fetch_json": fetcher,
    }
    events, capture = _event_capture()

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        mock.patch("core.runtime_task_events.emit_runtime_event", side_effect=capture),
        remote_fetch_policy_scope(source_context),
    ):
        reply = _currency_reply(
            prompt,
            session_id="fx-observability-forbidden",
            source_context=source_context,
        )
        web_calls = remote_fetch_attempt_count()

    assert reply is not None
    assert web_calls == 0
    assert not [event for event in events if event["event_type"].startswith("fx_retrieval_")]
    assert "fresh_data_retrieval_receipts" not in source_context
    fetcher.assert_not_called()


def test_fx_provider_failure_is_counted_terminal_and_cannot_author_a_rate() -> None:
    secret = "sk-provider-secret-123456789"
    fetcher = mock.Mock(side_effect=OSError(f"upstream offline; api_key={secret}"))
    source_context: dict[str, object] = {
        "surface": "api",
        "platform": "api",
        "runtime_session_id": "fx-observability-failed",
        "allow_remote_fetch": True,
        "fx_fetch_json": fetcher,
    }
    events, capture = _event_capture()

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        mock.patch("core.runtime_task_events.emit_runtime_event", side_effect=capture),
        remote_fetch_policy_scope(source_context),
    ):
        reply = _currency_reply(
            "1000 TRY to EUR",
            session_id="fx-observability-failed",
            source_context=source_context,
        )
        web_calls = remote_fetch_attempt_count()

    assert reply is not None
    assert reply["grounded"] == "no_rate_declined"
    assert "25" not in reply["response"]
    assert web_calls == 1
    assert [event["event_type"] for event in events] == [
        "fx_retrieval_started",
        "fx_retrieval_failed",
    ]
    failed = events[-1]
    assert failed["status"] == FxQuoteStatus.UNAVAILABLE.value
    assert failed["failure_class"] == "fx_unavailable"
    assert failed["rate"] == ""
    assert secret not in repr(events)
    assert secret not in repr(source_context["fresh_data_retrieval_receipts"])


def test_multiple_sources_count_every_provider_boundary() -> None:
    class StaticProvider:
        def __init__(self, name: str, rate: str) -> None:
            self.name = name
            self.rate = Decimal(rate)

        def quote(self, base: str, quote: str, *, timeout_s: float = 8.0) -> FxQuote:
            now = datetime.now(timezone.utc).isoformat()
            return FxQuote(
                base=base,
                quote=quote,
                status=FxQuoteStatus.AVAILABLE,
                rate=self.rate,
                observed_at=now,
                retrieved_at=now,
                source=self.name,
            )

    providers = (StaticProvider("one", "0.025"), StaticProvider("two", "0.0251"))
    source_context: dict[str, object] = {}
    with remote_fetch_policy_scope(source_context):
        from core.fresh_data.fx import retrieve_fx_quote

        quote = retrieve_fx_quote(
            "TRY",
            "EUR",
            providers=providers,
            source_context=source_context,
            authorized=True,
        )
        web_calls = remote_fetch_attempt_count()

    assert quote.available
    assert web_calls == 2
    assert source_context["fresh_data_retrieval_receipts"][0]["source_providers"] == [
        "one",
        "two",
    ]


def test_poisoned_receipt_container_cannot_hide_a_completed_retrieval() -> None:
    fetcher = mock.Mock(return_value=_fresh_payload())
    source_context: dict[str, object] = {
        "surface": "api",
        "allow_remote_fetch": True,
        "fx_fetch_json": fetcher,
        "fresh_data_retrieval_receipts": "caller-poisoned-container",
    }

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        remote_fetch_policy_scope(source_context),
    ):
        reply = _currency_reply(
            "1000 TRY to EUR",
            session_id="fx-poisoned-receipt-container",
            source_context=source_context,
        )

    assert reply is not None and reply["grounded"] == "live_rate"
    receipts = source_context["fresh_data_retrieval_receipts"]
    assert isinstance(receipts, list)
    assert len(receipts) == 1
    assert receipts[0]["status"] == "available"


def test_observability_wrapper_is_load_bearing_sabotage() -> None:
    fetcher = mock.Mock(return_value=_fresh_payload())
    source_context: dict[str, object] = {
        "surface": "api",
        "platform": "api",
        "allow_remote_fetch": True,
        "fx_fetch_json": fetcher,
    }

    def bypass(base, quote, *, providers, source_context, authorized, timeout_s):
        del source_context, authorized
        return resolve_fx_quote(base, quote, providers=providers, timeout_s=timeout_s)

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        mock.patch("core.fresh_data.fx.retrieve_fx_quote", side_effect=bypass),
        remote_fetch_policy_scope(source_context),
    ):
        reply = _currency_reply(
            "1000 TRY to EUR",
            session_id="fx-observability-sabotage",
            source_context=source_context,
        )
        web_calls = remote_fetch_attempt_count()

    assert reply is not None and reply["grounded"] == "live_rate"
    assert web_calls == 0
    assert "fresh_data_retrieval_receipts" not in source_context


def test_web_runtime_trace_carries_retrieval_receipt_and_count(make_agent, tmp_path) -> None:
    from core.web.api.runtime import RuntimeServices, run_agent

    agent = make_agent()
    runtime = RuntimeServices(agent=agent, runtime_home=str(tmp_path))
    fetcher = mock.Mock(return_value=_fresh_payload())
    captured, capture = _event_capture()

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        mock.patch("core.runtime_task_events.emit_runtime_event", side_effect=capture),
        mock.patch("core.web.api.runtime.schedule_memory_extraction"),
    ):
        result = run_agent(
            runtime,
            "1000 TRY to EUR",
            session_id="fx-observability-runtime",
            source_context={
                "surface": "api",
                "platform": "api",
                "allow_remote_fetch": True,
                "fx_fetch_json": fetcher,
            },
            workspace_root_provider=lambda: str(tmp_path),
        )

    assert result["web_calls"] == 1
    assert "web" not in result["route_skips"]
    trace = next(event for event in captured if event["event_type"] == "turn.trace_completed")
    assert trace["web_calls"] == 1
    assert len(trace["fresh_data_retrieval_receipts"]) == 1
    receipt = trace["fresh_data_retrieval_receipts"][0]
    terminal = next(event for event in captured if event["event_type"] == "fx_retrieval_completed")
    assert receipt == {
        key: terminal[key]
        for key in receipt
    }
    assert receipt["base"] == "TRY"
    assert receipt["quote"] == "EUR"
    assert receipt["status"] == "available"
    assert receipt["rate"] == "0.025"


def test_set5_24_cancelled_forex_turn_has_zero_call_and_zero_receipt(
    make_agent,
    tmp_path,
) -> None:
    """Exact incident control: the reviewed country-list answer preempts direct FX retrieval."""

    from core.web.api.runtime import RuntimeServices, run_agent

    runtime = RuntimeServices(agent=make_agent(), runtime_home=str(tmp_path))
    fetcher = mock.Mock(side_effect=AssertionError("cancelled forex reached provider"))
    captured, capture = _event_capture()

    with (
        mock.patch("core.policy_engine.allow_web_fallback", return_value=True),
        mock.patch("core.runtime_task_events.emit_runtime_event", side_effect=capture),
        mock.patch("core.web.api.runtime.schedule_memory_extraction"),
    ):
        result = run_agent(
            runtime,
            SET5_24,
            session_id="set5-24-retrieval-control",
            source_context={
                "surface": "api",
                "platform": "api",
                "allow_remote_fetch": True,
                "fx_fetch_json": fetcher,
            },
            workspace_root_provider=lambda: str(tmp_path),
        )

    assert result["web_calls"] == 0
    assert "web" in result["route_skips"]
    assert not [
        event for event in captured if event["event_type"].startswith("fx_retrieval_")
    ]
    trace = next(event for event in captured if event["event_type"] == "turn.trace_completed")
    assert trace["web_calls"] == 0
    assert trace["fresh_data_retrieval_receipts"] == []
    fetcher.assert_not_called()


def test_direct_fx_receipts_are_durable_and_secret_redacted(tmp_path) -> None:
    from core.runtime_task_events import (
        configure_runtime_event_store,
        list_runtime_session_events,
        reset_runtime_event_state,
    )
    from storage.migrations import run_migrations

    db_path = tmp_path / "fx-retrieval-events.db"
    run_migrations(db_path=db_path)
    configure_runtime_event_store(str(db_path))
    reset_runtime_event_state()
    secret = "sk-provider-name-secret-123456789"

    class SecretNamedProvider:
        name = f"institutional source {secret}"

        def quote(self, base: str, quote: str, *, timeout_s: float = 8.0) -> FxQuote:
            now = datetime.now(timezone.utc).isoformat()
            return FxQuote(
                base=base,
                quote=quote,
                status=FxQuoteStatus.AVAILABLE,
                rate=Decimal("1.15"),
                observed_at=now,
                retrieved_at=now,
                source=self.name,
            )

    context: dict[str, object] = {
        "runtime_session_id": "fx-durable-receipt",
        "cancel_turn_id": "turn-fx-durable",
    }
    try:
        from core.fresh_data.fx import retrieve_fx_quote

        with remote_fetch_policy_scope(context):
            quote = retrieve_fx_quote(
                "EUR",
                "USD",
                providers=(SecretNamedProvider(),),
                source_context=context,
                authorized=True,
            )
        events = list_runtime_session_events("fx-durable-receipt", after_seq=0, limit=10)
    finally:
        reset_runtime_event_state()
        configure_runtime_event_store(None)

    assert quote.available
    assert [event["event_type"] for event in events] == [
        "fx_retrieval_started",
        "fx_retrieval_completed",
    ]
    assert all(event["client_turn_id"] == "turn-fx-durable" for event in events)
    assert secret not in repr(events)
    assert "[redacted-api-key]" in repr(events)
