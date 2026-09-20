"""Conductor registrations for FX, place discovery, and field-complete research.

The model may name these operations, but the runtime still admits them through Turn IR kind and a
domain parser.  Each runner consumes a configured provider from ``NodeContext.source_context``;
absence is a structured unavailable result, never a workspace search and never model invention.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from core.conductor.capabilities import (
    REMOTE_FETCH_EFFECT_CLASS,
    OperationCapability,
    OperationEffect,
)
from core.conductor.node import ConductorNode
from core.conductor.registry import NodeContext, OperationSpec
from core.currency_intent import fx_conversion_intent, fx_rate_lookup_intent
from core.fresh_data.fx import (
    FrankfurterFxProvider,
    resolve_fx_quote,
    unavailable_fx_quote,
)
from core.fresh_data.places import (
    PlaceSearchReport,
    PlaceSearchRequest,
    parse_place_search_request,
    unavailable_place_search,
)
from core.fresh_data.research import (
    StructuredResearchRequest,
    parse_structured_research_request,
    render_research_coverage,
    run_structured_research,
)
from core.retrieval_constraints import analyze_retrieval_constraints
from core.turn_ir import ClauseKind, TurnClause


def _fx_request(text: str) -> Any | None:
    return fx_conversion_intent(text) or fx_rate_lookup_intent(text)


def _fx_accepts(clause: TurnClause) -> bool:
    request = _fx_request(clause.request_text)
    return request is not None and getattr(request, "supplied_rate", None) is None


def _is_iso_currency_code(code: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z]{3}", str(code or "").strip()))


def _fx_expand(request_text: str) -> list[dict[str, Any]]:
    request = _fx_request(request_text)
    if request is None:
        return []
    if hasattr(request, "amount"):
        if request.supplied_rate is not None:
            # User-supplied arithmetic belongs to the deterministic currency/calculation lane.
            # It is not a live observation and must not acquire web authority here.
            return []
        if not request.source.code or not request.target.code:
            return []
        # ISO discipline (measured live: a "10 bnb to gold" reading of a purchasable-
        # amount ask minted an fx node that could only die as an internal fault).
        # fx_quote serves three-letter fiat codes; crypto and commodities belong to
        # market_quote. A code carrying digits or spaces is a misread frame, and a
        # code that resolves as a MARKET asset (btc/eth/bnb are three letters too —
        # measured live as a phantom "BTC/BNB" fx frame dying on a 422) is not fiat.
        from core.agent_runtime.live_data_plan import _resolve_price_alias

        if (
            not _is_iso_currency_code(request.source.code)
            or not _is_iso_currency_code(request.target.code)
            or _resolve_price_alias(request.source.code.strip().lower()) is not None
            or _resolve_price_alias(request.target.code.strip().lower()) is not None
        ):
            return []
        return [
            {
                "entity": f"{request.source.code}/{request.target.code}",
                "base": request.source.code,
                "quote": request.target.code,
                "amount": str(request.amount),
                "request_kind": "conversion",
            }
        ]
    return [
        {
            "entity": f"{request.base.code}/{request.quote.code}",
            "base": request.base.code,
            "quote": request.quote.code,
            "amount": "",
            "request_kind": "rate_lookup",
        }
    ]


def _configured_fx_providers(ctx: NodeContext) -> tuple[Any, ...]:
    from core.remote_fetch_policy import remote_fetch_allowed_by_context

    source = dict(ctx.source_context or {})
    configured = source.get("fx_providers")
    if isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)):
        return tuple(item for item in configured if item is not None)
    single = source.get("fx_provider")
    if single is not None:
        return (single,)
    # The caller-side authority helper applies the established live-surface default. An explicit
    # ``allow_remote_fetch=False`` remains a hard operator/privacy veto, and untrusted library or
    # CLI contexts stay denied when the flag is absent.
    if remote_fetch_allowed_by_context(source):
        fetcher = source.get("fx_fetch_json")
        now = source.get("fx_now")
        return (
            FrankfurterFxProvider(
                fetch_json=fetcher if callable(fetcher) else None,
                **({"now": now} if callable(now) else {}),
            ),
        )
    return ()


def _runtime_retrieval_allowed(ctx: NodeContext) -> bool:
    from core import policy_engine
    from core.remote_fetch_policy import remote_fetch_allowed_by_context

    return bool(
        remote_fetch_allowed_by_context(dict(ctx.source_context or {}))
        and policy_engine.allow_web_fallback()
    )


def _turn_retrieval_constraints(node: ConductorNode, ctx: NodeContext):
    """Constraints for the WHOLE turn, not just the clause this node was split from.

    A prohibition binds the turn it was stated in. The conductor splits a message into clauses and
    then evaluated each clause's constraints against its OWN text, so a ban stated in one sentence
    never reached the node built from another -- and the runtime fetched under an explicit refusal.

    Measured live 2026-08-18, `route=conductor_multi_intent_plan`, `model_ran=False`:

        "Tell me the current ambient temperature in Helsinki, Finland. I strictly forbid you from
         utilizing any external data retrieval, web functions, or weather tools."

        clause 1 -> weather node, own text carries no prohibition  -> FETCHED wttr.in
        clause 2 -> "I strictly forbid you from ..." planned as its own REQUEST, then reported
                    "could not be answered"

    The turn-level analysis was already correct; it simply was not what this lane read.

    Scoped negation still works, because the scoping lives in the domain mapping rather than in
    which text is analysed: "don't look up the weather in Vilnius, but get me the gold price"
    yields a negative clause whose domain is `weather` alone, so the market node is untouched.
    """
    original = ""
    shared = getattr(ctx, "shared_context", None)
    if shared is not None:
        original = str(getattr(shared, "original_request", "") or "").strip()
    return analyze_retrieval_constraints(original or node.request_text)


def _fx_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    base = str(node.arguments.get("base") or "")
    quote = str(node.arguments.get("quote") or "")
    constraints = _turn_retrieval_constraints(node, ctx)
    if constraints.forbids_all_tools or constraints.forbids_external_retrieval:
        result = unavailable_fx_quote(
            base,
            quote,
            "current-rate verification requires retrieval, which this turn explicitly forbids",
        )
    elif not _runtime_retrieval_allowed(ctx):
        result = unavailable_fx_quote(
            base,
            quote,
            "current-rate retrieval is not enabled for this turn",
        )
    else:
        providers = _configured_fx_providers(ctx)
        result = (
            resolve_fx_quote(base, quote, providers=providers, timeout_s=ctx.timeout_s)
            if providers
            else unavailable_fx_quote(base, quote)
        )
    payload = result.to_dict()
    amount = str(node.arguments.get("amount") or "").strip()
    # CHAINED CONVERSION (Test Pack V2 L02: later obligations consume earlier
    # results): a leg with no amount of its own converts what its dependency leg
    # produced — "10000 eur to gbp and then to eth" leg 2 runs on leg 1's
    # converted amount. Only a declared dependency may supply it.
    if not amount:
        for dep_result in dict(ctx.dependency_results or {}).values():
            if not isinstance(dep_result, Mapping) or not dep_result.get("converted_amount"):
                continue
            try:
                Decimal(str(dep_result["converted_amount"]))
            except Exception:
                continue
            amount = str(dep_result["converted_amount"])
            break
    payload["amount"] = amount
    payload["converted_amount"] = ""
    if amount and result.available:
        payload["converted_amount"] = str(result.convert(Decimal(amount)))
    return payload


def _fx_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    pair = f"{result.get('base')}/{result.get('quote')}"
    if result.get("status") == "available" and result.get("rate"):
        source = str(result.get("source") or "")
        observed = str(result.get("observed_at") or result.get("retrieved_at") or "")
        if result.get("converted_amount"):
            return (
                f"{pair}: {result.get('amount')} {result.get('base')} × {result.get('rate')} = "
                f"{result.get('converted_amount')} {result.get('quote')} "
                f"(source: {source}; observed: {observed})."
            )
        return (
            f"{pair}: 1 {result.get('base')} = {result.get('rate')} {result.get('quote')} "
            f"(source: {source}; observed: {observed})."
        )
    reason = str(result.get("failure_reason") or "no verified quote was returned")
    return f"{pair}: current FX rate unavailable — {reason}. No rate was guessed."


def _place_accepts(clause: TurnClause) -> bool:
    return parse_place_search_request(clause.request_text) is not None


def _place_expand(request_text: str) -> list[dict[str, Any]]:
    request = parse_place_search_request(request_text)
    if request is None:
        return []
    return [
        {
            "entity": f"{request.service} near {request.location}",
            "service": request.service,
            "location": request.location,
        }
    ]


def _place_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    request = PlaceSearchRequest(
        service=str(node.arguments.get("service") or ""),
        location=str(node.arguments.get("location") or ""),
    )
    constraints = _turn_retrieval_constraints(node, ctx)
    if constraints.forbids_all_tools or constraints.forbids_external_retrieval:
        report = unavailable_place_search(
            request,
            "place verification requires retrieval, which this turn explicitly forbids",
            source="policy",
        )
    elif not _runtime_retrieval_allowed(ctx):
        report = unavailable_place_search(
            request,
            "place retrieval is not enabled for this turn",
            source="policy",
        )
    else:
        provider = dict(ctx.source_context or {}).get("place_provider")
        if provider is None:
            from retrieval.place_provider import ConfiguredWebPlaceProvider

            provider = ConfiguredWebPlaceProvider()
        try:
            report = provider.search(request, timeout_s=ctx.timeout_s)
        except Exception as exc:
            report = unavailable_place_search(
                request,
                f"{type(exc).__name__}: {exc}"[:200],
                source=str(getattr(provider, "name", type(provider).__name__)),
            )
    if not isinstance(report, PlaceSearchReport):
        raise TypeError("place provider must return PlaceSearchReport")
    return report.to_dict()


def _place_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    service = str(result.get("service") or node.arguments.get("service") or "service")
    location = str(result.get("location") or node.arguments.get("location") or "the requested area")
    rows = [item for item in (result.get("results") or []) if isinstance(item, Mapping)]
    if result.get("status") == "available" and rows:
        rendered = [
            f"{service} near {location} — sourced web candidates "
            f"(search: {result.get('source')}):"
        ]
        for item in rows[:5]:
            details = str(
                item.get("address") or item.get("evidence_excerpt") or ""
            ).strip()
            source_url = str(item.get("source_url") or "").strip()
            suffix = f": {details}" if details else ""
            if source_url:
                suffix += f" — {source_url}"
            rendered.append(f"- {item.get('name')}{suffix}")
        rendered.append(
            "These are search-discovered candidates, not guaranteed-current business records; "
            "verify details before travelling."
        )
        return "\n".join(rendered)
    reason = str(result.get("failure_reason") or "no matching places were returned")
    return f"{service} near {location}: unavailable — {reason}. No local result was invented."


def _research_expand(request_text: str) -> list[dict[str, Any]]:
    request = parse_structured_research_request(request_text)
    if request is None:
        return []
    return [
        {
            "entity": request.subject,
            "subject": request.subject,
            "fields": list(request.fields),
        }
    ]


def _research_run(node: ConductorNode, ctx: NodeContext) -> dict[str, Any]:
    request = StructuredResearchRequest(
        subject=str(node.arguments.get("subject") or ""),
        fields=tuple(
            str(item)
            for item in (node.arguments.get("fields") or [])
            if str(item).strip()
        ),
    )
    constraints = _turn_retrieval_constraints(node, ctx)
    provider = None
    if (
        not constraints.forbids_all_tools
        and not constraints.forbids_external_retrieval
        and _runtime_retrieval_allowed(ctx)
    ):
        provider = dict(ctx.source_context or {}).get("research_field_provider")
    report = run_structured_research(request, provider=provider, timeout_s=ctx.timeout_s)
    payload = report.to_dict()
    if constraints.forbids_all_tools or constraints.forbids_external_retrieval:
        for field in payload["fields"]:
            field["failure_reason"] = (
                "verification requires retrieval, which this turn explicitly forbids"
            )
    elif not _runtime_retrieval_allowed(ctx):
        for field in payload["fields"]:
            field["failure_reason"] = "research retrieval is not enabled for this turn"
    elif provider is None:
        for field in payload["fields"]:
            field["failure_reason"] = (
                "structured field verification requires a configured research_field_provider; "
                "the built-in web search returns candidate-only notes and cannot promote snippets "
                "to verified field values"
            )
    return payload


def _research_render(node: ConductorNode, result: Mapping[str, Any]) -> str:
    from core.fresh_data.research import (
        ResearchCoverageReport,
        ResearchFieldCoverage,
        ResearchFieldStatus,
    )

    fields = tuple(
        ResearchFieldCoverage(
            field=str(item.get("field") or ""),
            status=ResearchFieldStatus(str(item.get("status") or "unavailable")),
            value=str(item.get("value") or ""),
            source=str(item.get("source") or ""),
            source_url=str(item.get("source_url") or ""),
            retrieved_at=str(item.get("retrieved_at") or ""),
            failure_reason=str(item.get("failure_reason") or ""),
        )
        for item in (result.get("fields") or [])
        if isinstance(item, Mapping)
    )
    return render_research_coverage(
        ResearchCoverageReport(
            subject=str(result.get("subject") or node.arguments.get("subject") or "research"),
            fields=fields,
            retrieved_at=str(result.get("retrieved_at") or ""),
        )
    )


FRESH_DATA_OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        name="fx_quote",
        description="current directed FX rate or conversion for an explicit currency pair",
        expand_arguments=_fx_expand,
        run=_fx_run,
        render=_fx_render,
        required_result_fields=(
            "base", "quote", "direction", "status", "retrieved_at", "source", "failure_reason"
        ),
        tool_intent="web.research",
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="foreign exchange",
            # TRANSFORM is the kind a conversion states itself as when the user opens with what
            # they hold ("i have 100 usd, convert to rub" -- Turn IR reads the clause TRANSFORM):
            # converting an amount IS that transform, so the fx capability serves it, exactly as
            # it serves the question and observation wordings of the same work.
            accepted_kinds=frozenset(
                {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN, ClauseKind.TRANSFORM}
            ),
            effect_class=REMOTE_FETCH_EFFECT_CLASS,
            accepts_clause=_fx_accepts,
        ),
    ),
    OperationSpec(
        name="place_search",
        description="find a named local service in or near an explicit location",
        expand_arguments=_place_expand,
        run=_place_run,
        render=_place_render,
        required_result_fields=(
            "service", "location", "status", "results", "source", "retrieved_at", "failure_reason"
        ),
        tool_intent="web.research",
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="local place discovery",
            accepted_kinds=frozenset({ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}),
            effect_class=REMOTE_FETCH_EFFECT_CLASS,
            accepts_clause=_place_accepts,
        ),
    ),
    OperationSpec(
        name="structured_research",
        description=(
            "research an explicit subject and report every requested field as verified or "
            "unavailable"
        ),
        expand_arguments=_research_expand,
        run=_research_run,
        render=_research_render,
        required_result_fields=(
            "subject", "fields", "verified_count", "unavailable_count", "retrieved_at"
        ),
        tool_intent="web.research",
        capability=OperationCapability(
            effect=OperationEffect.LIVE_OBSERVATION,
            domain="field-complete research",
            accepted_kinds=frozenset({ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}),
            effect_class=REMOTE_FETCH_EFFECT_CLASS,
            # Turn IR can legitimately split "research X and tell me A, B, C" into two clauses
            # while the model keeps it as one atomic research request.  The strict research-frame
            # parser in ``expand_arguments`` sees the model's complete clause and is the domain
            # admission check; applying it to only the first aligned Turn IR clause would reject
            # the valid request before expansion.
        ),
    ),
)


__all__ = ["FRESH_DATA_OPERATIONS"]
