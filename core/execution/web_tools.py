from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from typing import Any

from core.execution.models import ToolIntentExecution
from core.research_evidence import truthful_evidence_strength
from core.retrieval_observability import begin_web_retrieval, finish_web_retrieval


def normalize_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    if hasattr(item, "__dict__"):
        return {
            str(key): value
            for key, value in vars(item).items()
            if not str(key).startswith("_")
        }
    return {"value": str(item)}


def execute_web_tool(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str,
    source_context: dict[str, Any] | None,
    allow_web_fallback_fn: Callable[[], bool],
    load_builtin_tools_fn: Callable[[], Any],
    planned_search_query_fn: Callable[..., list[dict[str, Any]]],
    call_tool_fn: Callable[..., Any],
    adaptive_research_fn: Callable[..., Any],
    unsupported_execution_for_intent_fn: Callable[..., ToolIntentExecution],
    tool_observation_fn: Callable[..., dict[str, Any]],
    audit_log_fn: Callable[..., Any],
) -> ToolIntentExecution:
    if not allow_web_fallback_fn():
        return unsupported_execution_for_intent_fn(intent, status="disabled")

    load_builtin_tools_fn()
    try:
        if intent == "web.search":
            query = str(arguments.get("query") or "").strip()
            limit = max(1, min(int(arguments.get("limit") or 3), 5))
            if not query:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web search needs a non-empty query.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            # ONE RECEIPT FOR THE WHOLE TYPED SEARCH, its internal rescue included.
            #
            # This handler had a rescue nothing recorded: when the planned search
            # came back empty it silently re-ran a raw `web.search` and reported
            # `status="executed"` from those rows, so a run whose primary search
            # found nothing was indistinguishable from one that worked. It also
            # published no `web_retrieval_receipt` at all, which is how a chat
            # turn that really did reach Brave left the Activity panel with a
            # `tool_executed` row naming no provider and no sources.
            #
            # The receipt spans both attempts because they are one retrieval of
            # one question, and it names whichever provider actually answered:
            # `finish_web_retrieval` reads `search_provider` off the rows, so a
            # rescue that ran keyless cannot inherit the keyed provider's name.
            retrieval_receipt = begin_web_retrieval(
                source_context,
                kind="web_search_tool",
                query=query,
                task_id=task_id,
                action="search",
            )
            try:
                rows = planned_search_query_fn(
                    query,
                    task_id=task_id,
                    limit=limit,
                    task_class="research",
                    source_label="web.search",
                )
                if not rows:
                    results = call_tool_fn("web.search", query=query, max_results=limit)
                    # The registry binds `web.search` to `SearXNGClient.search`
                    # (tools/registry.py), so the rescue's provider is known here
                    # and is stamped rather than left blank. A blank would let the
                    # receipt inherit the primary search's provider and report a
                    # keyless rescue under a keyed provider's name.
                    rows = [
                        dict(normalize_item(item), search_provider="searxng")
                        for item in list(results or [])[:limit]
                    ]
            except Exception as exc:
                finish_web_retrieval(source_context, retrieval_receipt, failure=exc)
                raise
            finish_web_retrieval(source_context, retrieval_receipt, notes=list(rows or []))
            if not rows:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="no_results",
                    response_text=f'No live search results came back for "{query}".',
                    mode="tool_executed",
                    tool_name=intent,
                    details={
                        "query": query,
                        "result_count": 0,
                        "results": [],
                        "observation": tool_observation_fn(
                            intent=intent,
                            tool_surface="web",
                            ok=True,
                            status="no_results",
                            query=query,
                            result_count=0,
                            results=[],
                        ),
                    },
                )
            observation_results = [
                {
                    "title": str(row.get("result_title") or row.get("title") or row.get("url") or "Untitled").strip(),
                    "url": str(row.get("result_url") or row.get("url") or "").strip(),
                    "snippet": str(row.get("summary") or row.get("snippet") or "").strip()[:180],
                    "source_profile_label": str(row.get("source_profile_label") or "").strip(),
                    "origin_domain": str(row.get("origin_domain") or "").strip(),
                }
                for row in rows[:limit]
            ]
            lines = [f'Search results for "{query}":']
            for row in rows:
                title = str(row.get("result_title") or row.get("title") or row.get("url") or "Untitled").strip()
                url = str(row.get("result_url") or row.get("url") or "").strip()
                snippet = str(row.get("summary") or row.get("snippet") or "").strip()
                profile_label = str(row.get("source_profile_label") or "").strip()
                line = f"- {title}"
                if url:
                    line += f" - {url}"
                if profile_label:
                    line += f" [{profile_label}]"
                lines.append(line)
                if snippet:
                    lines.append(f"  {snippet[:180]}")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text="\n".join(lines),
                mode="tool_executed",
                tool_name=intent,
                details={
                    "query": query,
                    "result_count": len(rows),
                    "results": observation_results,
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=True,
                        status="executed",
                        query=query,
                        result_count=len(rows),
                        results=observation_results,
                    ),
                },
            )

        if intent == "web.fetch":
            url = str(arguments.get("url") or "").strip()
            timeout_s = float(arguments.get("timeout_s") or 15.0)
            if not url:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web fetch needs a URL.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            result = call_tool_fn("web.fetch", url=url, timeout_s=timeout_s)
            status = str(result.get("status") or "unknown").strip()
            text = str(result.get("text") or "").strip()
            preview = text[:500] if text else ""
            lines = [f"Fetched {url}", f"- Status: {status}"]
            if preview:
                lines.append(f"- Preview: {preview}")
            return ToolIntentExecution(
                handled=True,
                ok=status == "ok",
                status="executed" if status == "ok" else status,
                response_text="\n".join(lines),
                mode="tool_executed" if status == "ok" else "tool_failed",
                tool_name=intent,
                details={
                    "url": url,
                    "fetch_status": status,
                    "text_preview": preview,
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=status == "ok",
                        status="executed" if status == "ok" else status,
                        url=url,
                        fetch_status=status,
                        text_preview=preview,
                    ),
                },
            )

        if intent == "web.research":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Web research needs a non-empty query.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            research_result = adaptive_research_fn(
                task_id=task_id,
                user_input=query,
                classification={"task_class": "research"},
                interpretation=SimpleNamespace(topic_hints=[], understanding_confidence=0.82),
                source_context=dict(source_context or {"surface": "openclaw", "platform": "openclaw"}),
            )
            observation_hits = [
                {
                    "title": str(row.get("result_title") or row.get("title") or row.get("result_url") or "Untitled").strip(),
                    "url": str(row.get("result_url") or row.get("url") or "").strip(),
                    "snippet": str(row.get("summary") or row.get("snippet") or "").strip()[:180],
                    "domain": str(row.get("origin_domain") or "").strip(),
                }
                for row in list(research_result.notes or [])[:5]
            ]
            lines = [f'Adaptive web research for "{query}":']
            if research_result.actions_taken:
                lines.append("- Actions: " + ", ".join(research_result.actions_taken))
            if research_result.queries_run:
                lines.append("- Queries: " + " | ".join(research_result.queries_run[:3]))
            for row in observation_hits[:3]:
                line = f"- {row['title']}"
                if row["url"]:
                    line += f" - {row['url']}"
                if row["domain"]:
                    line += f" [{row['domain']}]"
                lines.append(line)
                if row["snippet"]:
                    lines.append(f"  {row['snippet']}")
            if research_result.admitted_uncertainty:
                lines.append(f"- Uncertainty: {research_result.uncertainty_reason}")
            elif research_result.stop_reason:
                lines.append(f"- Stop reason: {research_result.stop_reason}")
            return ToolIntentExecution(
                handled=True,
                ok=bool(observation_hits),
                status="executed" if observation_hits else "no_results",
                response_text="\n".join(lines),
                user_safe_response_text="\n".join(lines),
                mode="tool_executed" if observation_hits else "tool_failed",
                tool_name=intent,
                details={
                    "query": query,
                    "strategy": research_result.strategy,
                    "actions_taken": list(research_result.actions_taken),
                    "queries_run": list(research_result.queries_run),
                    "evidence_strength": truthful_evidence_strength(research_result),
                    "uncertainty_reason": research_result.uncertainty_reason,
                    "hit_count": len(observation_hits),
                    "hits": observation_hits,
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=bool(observation_hits),
                        status="executed" if observation_hits else "no_results",
                        query=query,
                        strategy=research_result.strategy,
                        actions_taken=list(research_result.actions_taken),
                        queries_run=list(research_result.queries_run),
                        evidence_strength=truthful_evidence_strength(research_result),
                        admitted_uncertainty=research_result.admitted_uncertainty,
                        uncertainty_reason=research_result.uncertainty_reason,
                        stop_reason=research_result.stop_reason,
                        hit_count=len(observation_hits),
                        hits=observation_hits,
                    ),
                },
            )

        if intent == "browser.render":
            url = str(arguments.get("url") or "").strip()
            if not url:
                return ToolIntentExecution(
                    handled=True,
                    ok=False,
                    status="invalid_arguments",
                    response_text="Browser render needs a URL.",
                    mode="tool_failed",
                    tool_name=intent,
                )
            result = call_tool_fn("browser.render", url=url)
            status = str(result.get("status") or "unknown").strip()
            final_url = str(result.get("final_url") or url).strip()
            title = str(result.get("title") or "").strip()
            text = str(result.get("text") or "").strip()
            lines = [f"Rendered {final_url}", f"- Status: {status}"]
            if title:
                lines.append(f"- Title: {title}")
            if text:
                lines.append(f"- Preview: {text[:240]}")
            return ToolIntentExecution(
                handled=True,
                ok=status == "ok",
                status="executed" if status == "ok" else status,
                response_text="\n".join(lines),
                mode="tool_executed" if status == "ok" else "tool_failed",
                tool_name=intent,
                details={
                    "url": url,
                    "final_url": final_url,
                    "render_status": status,
                    "title": title,
                    "text_preview": text[:240] if text else "",
                    "observation": tool_observation_fn(
                        intent=intent,
                        tool_surface="web",
                        ok=status == "ok",
                        status="executed" if status == "ok" else status,
                        url=url,
                        final_url=final_url,
                        render_status=status,
                        title=title,
                        text_preview=text[:240] if text else "",
                    ),
                },
            )
    except Exception as exc:
        audit_log_fn(
            "tool_intent_execution_error",
            target_id=task_id,
            target_type="task",
            details={"intent": intent, "arguments": arguments, "error": str(exc)},
        )
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="execution_failed",
            response_text=f"I tried `{intent}` but the tool failed: {exc}",
            mode="tool_failed",
            tool_name=intent,
            details={"error": str(exc)},
        )

    return unsupported_execution_for_intent_fn(intent, status="unsupported")
