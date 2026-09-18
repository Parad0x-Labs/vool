from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.contracts import ToolContract, validate_tool_contract


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    fn: Callable[..., Any]
    contract: ToolContract


TOOLS: dict[str, ToolRegistration] = {}


def register(name: str, fn: Callable[..., Any], *, contract: ToolContract | None = None) -> None:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Tool registration requires a non-empty name.")
    if contract is None:
        contract = ToolContract(
            name=clean_name,
            description=f"Tool `{clean_name}` has no explicit contract metadata.",
            input_schema={"payload": "object"},
            output_schema={"result": "any"},
            side_effect_class="unknown",
            approval_requirement="unspecified",
            timeout_policy="default",
            retry_policy="default",
            artifact_emission="unspecified",
            error_contract="raises_or_returns_unknown",
        )
    validated = validate_tool_contract(contract)
    TOOLS[clean_name] = ToolRegistration(name=clean_name, fn=fn, contract=validated)


def load_builtin_tools() -> None:
    from tools.browser.browser_render import browser_render
    from tools.web.ddg_instant import ddg_instant_answer
    from tools.web.http_fetch import http_fetch_text
    from tools.web.searxng_client import SearXNGClient
    from tools.web.web_research import web_research

    client = SearXNGClient()
    register(
        "web.search",
        client.search,
        contract=ToolContract(
            name="web.search",
            description="Search the web through the configured SearXNG endpoint.",
            input_schema={"query": "string", "max_results": "integer optional"},
            output_schema={"results": "list[search_hit]"},
            side_effect_class="network_read",
            approval_requirement="none",
            timeout_policy="policy.web.searxng_timeout_seconds",
            retry_policy="caller_managed",
            artifact_emission="none",
            error_contract="raises_network_or_returns_empty_results",
        ),
    )
    register(
        "web.ddg_instant",
        ddg_instant_answer,
        contract=ToolContract(
            name="web.ddg_instant",
            description="Fetch a lightweight DuckDuckGo instant-answer payload for a query.",
            input_schema={"query": "string", "timeout_s": "number optional"},
            output_schema={"payload": "duckduckgo_instant_answer"},
            side_effect_class="network_read",
            approval_requirement="none",
            timeout_policy="call_timeout_seconds",
            retry_policy="caller_managed",
            artifact_emission="none",
            error_contract="raises_network_errors",
        ),
    )
    register(
        "web.fetch",
        http_fetch_text,
        contract=ToolContract(
            name="web.fetch",
            description="Fetch and normalize text content from one URL.",
            input_schema={"url": "string", "timeout_s": "number optional", "max_bytes": "integer optional"},
            output_schema={"url": "string", "text": "string", "content_type": "string"},
            side_effect_class="network_read",
            approval_requirement="none",
            timeout_policy="call_timeout_seconds",
            retry_policy="none",
            artifact_emission="none",
            error_contract="raises_network_or_parse_errors",
        ),
    )
    register(
        "web.research",
        web_research,
        contract=ToolContract(
            name="web.research",
            description="Run bounded multi-source web research for a query.",
            input_schema={"query": "string", "max_hits": "integer optional", "fetch_timeout_s": "number optional"},
            output_schema={"query": "string", "hits": "list[research_hit]", "summary": "string optional"},
            side_effect_class="network_read",
            approval_requirement="none",
            timeout_policy="per-fetch and search bounded timeouts",
            retry_policy="bounded_internal_fallbacks",
            artifact_emission="none",
            error_contract="returns_partial_results_or_raises",
        ),
    )
    register(
        "browser.render",
        browser_render,
        contract=ToolContract(
            name="browser.render",
            description="Render a URL in a browser engine and return extracted text.",
            input_schema={"url": "string", "engine": "string optional", "timeout_ms": "integer optional"},
            output_schema={"url": "string", "text": "string", "title": "string optional"},
            side_effect_class="network_read",
            approval_requirement="runtime_policy",
            timeout_policy="call_timeout_ms",
            retry_policy="none",
            artifact_emission="none",
            error_contract="raises_browser_or_network_errors",
        ),
    )


def tool_contract(name: str) -> ToolContract:
    clean_name = str(name or "").strip()
    if clean_name not in TOOLS:
        raise KeyError(f"Unknown tool: {clean_name}")
    return TOOLS[clean_name].contract


def list_tool_contracts() -> list[ToolContract]:
    return [registration.contract for registration in TOOLS.values()]


def call_tool(name: str, **kwargs: Any) -> Any:
    clean_name = str(name or "").strip()
    if clean_name not in TOOLS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOLS[clean_name].fn(**kwargs)
