from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core import policy_engine
from core.capability_request_addressing import runtime_asked_for_impossible_action
from core.execution.constants import (
    _CAPABILITY_QUERY_PREFIXES,
    _EMAIL_SEND_MARKERS,
    _NEARBY_CAPABILITY_IDS,
    _PARTIAL_BUILD_MARKERS,
    _SELF_TOOL_REQUEST_MARKERS,
    _SUPPORTED_OPERATOR_TOOL_IDS,
    _SWARM_DELEGATION_MARKERS,
    _WEB_TOOL_INTENTS,
)
from core.execution.mcp_bridge import mcp_tool_specs
from core.hive_activity_tracker import load_hive_activity_tracker_config
from core.local_operator_actions import list_operator_tools, operator_capability_ledger
from core.public_hive_bridge import load_public_hive_bridge_config, public_hive_write_enabled
from core.runtime_execution_tools import runtime_execution_capability_ledger, runtime_execution_tool_specs


def runtime_capability_ledger(
    *,
    allow_web_fallback_fn=policy_engine.allow_web_fallback,
    allow_browser_fallback_fn=policy_engine.allow_browser_fallback,
    hive_read_reachable_fn: Callable[[], bool | None] | None = None,
    load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
    load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
    public_hive_write_enabled_fn=public_hive_write_enabled,
    runtime_execution_capability_ledger_fn=runtime_execution_capability_ledger,
    operator_capability_ledger_fn=operator_capability_ledger,
) -> list[dict[str, Any]]:
    hive_cfg = load_hive_activity_tracker_config_fn()
    public_hive_cfg = load_public_hive_bridge_config_fn()
    hive_list_available = bool(
        (hive_cfg.enabled and hive_cfg.watcher_api_url)
        or (public_hive_cfg.enabled and public_hive_cfg.topic_target_url)
    )
    hive_public_read = bool(public_hive_cfg.enabled and public_hive_cfg.topic_target_url)
    hive_read_intents = ["hive.list_available"] if hive_list_available else []
    if hive_public_read:
        hive_read_intents.extend(
            [
                "hive.list_research_queue",
                "hive.export_research_packet",
                "hive.search_artifacts",
            ]
        )
    hive_read_intents = list(dict.fromkeys(hive_read_intents))
    hive_reachability = "unconfigured"
    if hive_read_intents:
        hive_reachability = "configured_unverified"
        if hive_read_reachable_fn is not None:
            try:
                probe_result = hive_read_reachable_fn()
            except Exception:
                probe_result = False
            if probe_result is True:
                hive_reachability = "reachable"
            elif probe_result is False:
                hive_reachability = "unreachable"
    hive_read_supported = bool(hive_read_intents) and hive_reachability == "reachable"
    hive_write_supported = bool(
        public_hive_cfg.enabled
        and public_hive_cfg.topic_target_url
        and public_hive_write_enabled_fn(public_hive_cfg)
    )
    web_enabled = bool(allow_web_fallback_fn())
    browser_enabled = bool(allow_browser_fallback_fn()) and bool(policy_engine.browser_runtime_enabled())
    web_intents = sorted(item for item in _WEB_TOOL_INTENTS if item != "browser.render")
    entries: list[dict[str, Any]] = [
        {
            "capability_id": "web.live_lookup",
            "surface": "web",
            "claim": "run live web search, fetch pages, and bounded web research",
            "supported": web_enabled,
            "support_level": "supported" if web_enabled else "unsupported",
            "availability_state": "ready" if web_enabled else "unavailable",
            "unsupported_reason": "Web lookup is opt-in; enable with VOOL_ENABLE_WEB=1",
            "intents": web_intents,
            "public_tag": "web.live_lookup",
        },
        {
            "capability_id": "web.browser",
            "surface": "web",
            "claim": "render browser-dependent pages when the browser engine and runtime policy are enabled",
            "supported": browser_enabled,
            "support_level": "supported" if browser_enabled else "unsupported",
            "availability_state": "ready" if browser_enabled else "unavailable",
            "unsupported_reason": "Browser rendering is disabled by runtime browser policy.",
            "intents": ["browser.render", "web.browser_render"],
            "dispatchable_intents": ["browser.render", "web.browser_render"] if browser_enabled else [],
            "public_tag": "web.browser",
        },
        {
            "capability_id": "hive.read",
            "surface": "hive",
            "claim": "list live Hive tasks and read research queues, packets, and artifact search results",
            "supported": hive_read_supported,
            "support_level": "partial" if hive_read_intents else "unsupported",
            "availability_state": hive_reachability if hive_read_intents else "unavailable",
            "partial_reason": (
                "A Hive read route is configured, but live reachability has not been verified in this process."
                if hive_reachability == "configured_unverified"
                else "The configured Hive read route is unreachable right now."
                if hive_reachability == "unreachable"
                else ""
            ),
            "unsupported_reason": "Live Hive read actions are not configured on this runtime.",
            "intents": [
                "hive.list_available",
                "hive.list_research_queue",
                "hive.export_research_packet",
                "hive.search_artifacts",
            ],
            "dispatchable_intents": hive_read_intents,
            "public_tag": "hive.read",
        },
        {
            "capability_id": "hive.write",
            "surface": "hive",
            "claim": "create, claim, update, and submit real Hive topics or tasks",
            "supported": hive_write_supported,
            "unsupported_reason": "Live Hive write actions are not enabled on this runtime.",
            "intents": [
                "hive.research_topic",
                "hive.create_topic",
                "hive.claim_task",
                "hive.post_progress",
                "hive.submit_result",
            ],
            "public_tag": "hive.write",
        },
        {
            "capability_id": "payment.sell_quote",
            "surface": "payment",
            "claim": "produce an unsigned simulated sell quote; settlement and recipient wallet wiring are not shipped",
            "supported": True,
            "support_level": "partial",
            "availability_state": "simulated",
            "unsupported_reason": "The sell quote is simulated and cannot settle funds in this build.",
            "intents": ["sell.quote"],
            "public_tag": "payment.sell_quote",
        },
    ]
    entries.extend(
        entry
        for entry in runtime_execution_capability_ledger_fn()
        if str(entry.get("capability_id") or "").strip() != "web.browser"
    )
    entries.extend(operator_capability_ledger_fn())
    return [_annotate_capability_entry(entry) for entry in entries]


def capability_entry_for_intent(
    intent: str,
    *,
    runtime_capability_ledger_fn=runtime_capability_ledger,
) -> dict[str, Any] | None:
    normalized = str(intent or "").strip()
    if not normalized:
        return None
    for entry in runtime_capability_ledger_fn():
        if normalized in {str(item).strip() for item in list(entry.get("intents") or []) if str(item).strip()}:
            return dict(entry)
    return None


def capability_gap_for_intent(
    intent: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
    runtime_capability_ledger_fn=runtime_capability_ledger,
) -> dict[str, Any]:
    normalized_intent = str(intent or "").strip()
    all_entries = _all_capability_entries(
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger_fn,
    )
    entry = next(
        (
            candidate
            for candidate in all_entries
            if normalized_intent in {str(item).strip() for item in list(candidate.get("intents") or []) if str(item).strip()}
        ),
        None,
    )
    if entry is not None:
        return _capability_gap_from_entry(
            entry,
            requested_label=normalized_intent,
            extra_entries=all_entries,
        )
    return {
        "requested_capability": normalized_intent or "unknown.intent",
        "requested_label": normalized_intent or "unknown action",
        "support_level": "unsupported",
        "gap_kind": _synthetic_gap_kind_for_intent(normalized_intent),
        "reason": f"`{normalized_intent}` is not wired on this runtime." if normalized_intent else "That action is not wired on this runtime.",
        "nearby_alternatives": _nearby_alternatives_for_unknown_intent(normalized_intent, all_entries),
    }


# "Can you honestly say the tests pass?" when nothing was run/inspected: the honest answer is No,
# not a tool-execution attempt that dead-ends in "I couldn't map that cleanly to a real action".
_UNVERIFIED_HONESTY_FRAME_MARKERS = (
    "honestly say", "can you confirm", "can you guarantee", "are you sure", "can you verify",
    "can you be sure", "do you know for sure", "honestly claim", "can you promise", "honestly tell me",
)
_UNVERIFIED_STATUS_MARKERS = (
    "tests pass", "test pass", "tests are passing", "are passing", "it passes", "they pass",
    "it works", "it's green", "is green", "it builds", "it built", "it's done", "it's fixed",
    "everything works", "is correct", "passing",
)


def capability_truth_for_request(
    user_text: str,
    *,
    extra_entries: list[dict[str, Any]] | None = None,
    runtime_capability_ledger_fn=runtime_capability_ledger,
) -> dict[str, Any] | None:
    text = " ".join(str(user_text or "").split()).strip()
    if not text:
        return None
    lowered = f" {text.lower()} "
    if any(frame in lowered for frame in _UNVERIFIED_HONESTY_FRAME_MARKERS) and any(
        claim in lowered for claim in _UNVERIFIED_STATUS_MARKERS
    ):
        return {
            "requested_capability": "verify.unobserved_status",
            "requested_label": "confirm a status I have not actually observed",
            "support_level": "cannot_confirm",
            "gap_kind": "no_evidence",
            "reason": "I can't honestly say that unless I actually inspected or ran it in this chat.",
            "nearby_alternatives": ["Point me at the repo or tests and I'll run/inspect them, then report the real result."],
        }
    # Naming an impossible act is not asking for one. Matching the markers against the whole
    # message refused a potion described in somebody's novel, so the markers now only locate the
    # mention and `core.capability_request_addressing` decides whose act it is.
    if runtime_asked_for_impossible_action(text):
        return {
            "requested_capability": "physical_or_impossible_action",
            "requested_label": text,
            "support_level": "impossible",
            "gap_kind": "impossible",
            "reason": "That is outside what this runtime can actually do.",
            "nearby_alternatives": ["I can still reason about it, plan it, or help write instructions for a human to carry out."],
        }
    all_entries = _all_capability_entries(
        extra_entries=extra_entries,
        runtime_capability_ledger_fn=runtime_capability_ledger_fn,
    )
    if any(marker in lowered for marker in _EMAIL_SEND_MARKERS):
        return {
            "requested_capability": "email.send",
            "requested_label": "send email",
            "support_level": "unsupported",
            "gap_kind": "unwired",
            "reason": "Email sending is not wired on this runtime.",
            "nearby_alternatives": _combine_alternative_text(
                [
                    "I can draft the email text here.",
                    *_nearby_alternatives_from_capability_ids(["operator.discord_post", "operator.telegram_send"], all_entries),
                ]
            ),
        }
    if any(marker in lowered for marker in _SWARM_DELEGATION_MARKERS):
        return {
            "requested_capability": "swarm.delegate_merge",
            "requested_label": "delegate to other agents and merge their outputs",
            "support_level": "unsupported",
            "gap_kind": "future_unsupported",
            "reason": "Real multi-agent delegation and merge synthesis are not wired on this runtime yet.",
            "nearby_alternatives": _combine_alternative_text(
                _nearby_alternatives_from_capability_ids(["hive.read", "hive.write"], all_entries)
            ),
        }
    if any(marker in lowered for marker in _SELF_TOOL_REQUEST_MARKERS):
        return {
            "requested_capability": "tooling.self_extension",
            "requested_label": "create or register new tools on the fly",
            "support_level": "partial",
            "claim": (
                "I can create task-local helper files or scripts inside the active workspace and then use them through bounded workspace writes and local commands."
            ),
            "partial_reason": (
                "I still cannot auto-register brand-new first-class runtime tools or extend the global tool registry on my own."
            ),
            "reason": "Self-extension is only partially wired on this runtime.",
            "nearby_alternatives": _combine_alternative_text(
                [
                    "Ask me to create a helper script or module in the workspace and run it locally.",
                    *_nearby_alternatives_from_capability_ids(["workspace.write", "sandbox.command"], all_entries),
                ]
            ),
        }
    if any(marker in lowered for marker in _PARTIAL_BUILD_MARKERS) and any(
        marker in lowered for marker in (" build ", " create ", " make ", " ship ", " code ", " develop ")
    ):
        build_entry = next(
            (entry for entry in all_entries if str(entry.get("capability_id") or "").strip() == "workspace.build_scaffold"),
            None,
        )
        if build_entry is not None:
            return _capability_gap_from_entry(
                build_entry,
                requested_label="build a full application end to end",
                extra_entries=all_entries,
            )
    if any(lowered.strip().startswith(prefix) for prefix in _CAPABILITY_QUERY_PREFIXES):
        return None
    return None


def render_capability_truth_response(report: dict[str, Any] | None) -> str:
    payload = dict(report or {})
    support_level = str(payload.get("support_level") or "unsupported").strip().lower()
    reason = str(payload.get("reason") or "That capability is not available on this runtime.").strip()
    claim = str(payload.get("claim") or "").strip()
    partial_reason = str(payload.get("partial_reason") or "").strip()
    if support_level == "partial":
        base = f"Partially. {claim or reason}".strip()
        if partial_reason:
            base = f"{base} {partial_reason}".strip()
    elif support_level in {"full", "supported"}:
        base = f"Yes. {claim or reason}".strip()
    elif support_level == "impossible":
        base = f"No. {reason}".strip()
    else:
        base = f"No. {reason}".strip()
    alternatives = [str(item).strip() for item in list(payload.get("nearby_alternatives") or []) if str(item).strip()]
    if not alternatives:
        return base
    if len(alternatives) == 1:
        return f"{base} Instead: {alternatives[0]}".strip()
    return f"{base} Instead: {' '.join(f'- {item}' for item in alternatives)}".strip()


def supported_public_capability_tags(
    *,
    limit: int = 16,
    runtime_capability_ledger_fn=runtime_capability_ledger,
) -> list[str]:
    tags = [
        str(entry.get("public_tag") or "").strip()
        for entry in runtime_capability_ledger_fn()
        if str(entry.get("public_tag") or "").strip() and bool(entry.get("supported"))
    ]
    return tags[: max(0, int(limit or 0))] if limit is not None else tags


def runtime_tool_specs(
    *,
    allow_web_fallback_fn=policy_engine.allow_web_fallback,
    allow_browser_fallback_fn=policy_engine.allow_browser_fallback,
    runtime_execution_tool_specs_fn=runtime_execution_tool_specs,
    mcp_tool_specs_fn=mcp_tool_specs,
    load_hive_activity_tracker_config_fn=load_hive_activity_tracker_config,
    load_public_hive_bridge_config_fn=load_public_hive_bridge_config,
    public_hive_write_enabled_fn=public_hive_write_enabled,
    list_operator_tools_fn=list_operator_tools,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = [
        {
            "intent": "respond.direct",
            "description": "No tool call. Use this when the user only needs a normal answer.",
            "read_only": True,
            "arguments": {},
        },
        {
            "intent": "operator.list_tools",
            "description": "List the actually wired runtime tools available in VOOL right now.",
            "read_only": True,
            "arguments": {},
        },
        {
            "intent": "sell.quote",
            "description": (
                "Quote VOOL's own compute for another agent over x402 (read-only, no spend, no "
                "custody). Returns the USDC amount and an unsigned quote hash; a real recipient "
                "wallet and a signing key are not configured in this build."
            ),
            "read_only": True,
            "arguments": {
                "resource": "null:// URI optional",
                "null_name": ".null name optional",
            },
        },
        {
            "intent": "pay.x402",
            "description": (
                "Settle an x402 payment for external x402-gated compute priced in USDC. THIS "
                "MOVES REAL VALUE off this machine when the spend lane is enabled. The spend lane "
                "is disabled in this build (it returns x402_spend_disabled) unless the operator "
                "enables it; the description must never read as harmless, because the tool is not."
            ),
            "read_only": False,
            "arguments": {
                "resource": "https x402 resource URL",
                "null_name": ".null name optional",
                "max_spend_usdc": "number cap, required to authorize spend",
                "allow_spend": "boolean, must be true to spend",
                "approve": "boolean, must be true to spend",
                "privacy_path": "normal|dark-null optional",
            },
        },
    ]
    if allow_web_fallback_fn():
        specs.extend(
            [
                {
                    "intent": "web.search",
                    "description": "Search the web and return live result links/snippets.",
                    "read_only": True,
                    "arguments": {"query": "string", "limit": "integer optional"},
                },
                # web.fetch is deliberately NOT declared here. runtime_execution_tool_specs already
                # emits it from its contract, and declaring it in both places advertised the tool
                # to the model twice with different argument names (`timeout_s` here,
                # `timeout_seconds` there). build_cloud_tool_definitions merges a duplicate by
                # unioning its properties, and a strict native schema puts every property in
                # `required` — so every cloud model was forced to send both spellings, one of
                # which the executor discards.
                {
                    "intent": "web.research",
                    "description": "Run bounded search plus page fetches for a query.",
                    "read_only": True,
                    "arguments": {
                        "query": "string",
                        "max_hits": "integer optional",
                        "max_pages": "integer optional",
                    },
                },
                {
                    "intent": "browser.render",
                    "description": "Render a JS-heavy URL when browser fallback is enabled.",
                    "read_only": True,
                    "arguments": {"url": "https URL"},
                },
            ]
        )
        if not (allow_browser_fallback_fn() and policy_engine.browser_runtime_enabled()):
            specs = [spec for spec in specs if spec.get("intent") != "browser.render"]
    specs.extend(runtime_execution_tool_specs_fn())
    specs.extend(mcp_tool_specs_fn())

    hive_cfg = load_hive_activity_tracker_config_fn()
    public_hive_cfg = load_public_hive_bridge_config_fn()
    if (hive_cfg.enabled and hive_cfg.watcher_api_url) or (public_hive_cfg.enabled and public_hive_cfg.topic_target_url):
        specs.append(
            {
                "intent": "hive.list_available",
                "description": "List real currently available Hive research topics from the watcher or public bridge.",
                "read_only": True,
                "arguments": {"limit": "integer optional"},
            }
        )
    if public_hive_cfg.enabled and public_hive_cfg.topic_target_url:
        specs.extend(
            [
                {
                    "intent": "hive.list_research_queue",
                    "description": "List machine-readable Hive research queue entries with execution state and suggested questions.",
                    "read_only": True,
                    "arguments": {"limit": "integer optional"},
                },
                {
                    "intent": "hive.export_research_packet",
                    "description": "Fetch the machine-readable research packet for a concrete Hive topic.",
                    "read_only": True,
                    "arguments": {"topic_id": "string"},
                },
                {
                    "intent": "hive.search_artifacts",
                    "description": "Search compressed research artifacts already packed into the local/public Hive research lane.",
                    "read_only": True,
                    "arguments": {"query": "string", "topic_id": "string optional", "limit": "integer optional"},
                },
            ]
        )
        if public_hive_write_enabled_fn(public_hive_cfg):
            specs.extend(
                [
                    {
                        "intent": "hive.research_topic",
                        "description": "Auto-claim, research, mine heuristics, gate promotion, and publish a real autonomous Hive research bundle.",
                        "read_only": False,
                        "arguments": {"topic_id": "string", "auto_claim": "boolean optional"},
                    },
                    {
                        "intent": "hive.create_topic",
                        "description": "Create a real public Hive topic with a concrete title, summary, and technical tags.",
                        "read_only": False,
                        "arguments": {
                            "title": "string",
                            "summary": "string",
                            "topic_tags": "string[] optional",
                            "status": "open|researching|disputed optional",
                        },
                    },
                    {
                        "intent": "hive.claim_task",
                        "description": "Claim a real public Hive topic/task so progress is visible instead of implied.",
                        "read_only": False,
                        "arguments": {
                            "topic_id": "string",
                            "note": "string optional",
                            "capability_tags": "string[] optional",
                        },
                    },
                    {
                        "intent": "hive.post_progress",
                        "description": "Post a real progress update into a Hive topic.",
                        "read_only": False,
                        "arguments": {
                            "topic_id": "string",
                            "body": "string",
                            "progress_state": "started|working|blocked|done optional",
                            "claim_id": "string optional",
                        },
                    },
                    {
                        "intent": "hive.submit_result",
                        "description": "Submit a real final result into a Hive topic and close or solve it.",
                        "read_only": False,
                        "arguments": {
                            "topic_id": "string",
                            "body": "string",
                            "result_status": "solved|closed|disputed optional",
                            "claim_id": "string optional",
                        },
                    },
                ]
            )

    for tool in list_operator_tools_fn():
        tool_id = str(tool.get("tool_id") or "").strip()
        if not tool_id or tool_id not in _SUPPORTED_OPERATOR_TOOL_IDS or not tool.get("available"):
            continue
        intent = f"operator.{tool_id}"
        specs.append(
            {
                "intent": intent,
                "description": str(tool.get("description") or "").strip(),
                "read_only": not bool(tool.get("destructive")),
                "arguments": _operator_argument_schema(tool_id),
            }
        )
    specs.extend(_plugin_tool_specs())
    return _deduped_by_intent(specs)


def _deduped_by_intent(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One intent, one declaration — the CONTRACT wins.

    This list is assembled from two sources: hard-coded external-lane entries above, and the
    contract-derived rows from `runtime_execution_tool_specs()` appended after them. Twelve intents
    were declared by both, and `build_cloud_tool_definitions` merges duplicates with `.update()`,
    so which description the model actually receives depended on list order rather than on anything
    declared. Measured 2026-08-29: 84 rows for 72 intents, and the two `pay.x402` declarations said
    OPPOSITE things — the hard-coded one "only returns the quote ... never moves funds", the
    contract "Settle an x402 payment. Moves real value off this machine." For the runtime's only
    funds-touching tool, that is not a cosmetic difference.

    The FIRST declaration wins. The hard-coded external-lane rows carry lane-specific wording the
    model needs ("after explicit approval", "the heaviest processes by CPU and memory") that the
    terser contract line would replace, so preferring the contract here would quietly downgrade
    six operator tools. Where a hard-coded row CONTRADICTS its contract that is a defect in the
    row, fixed at source — as `pay.x402` was — not papered over by merge order. This stops
    paying tokens to declare 12 tools twice, and makes the choice explicit and testable. The
    file's own comment above records the same defect being hand-fixed for `web.fetch` alone.
    """

    by_intent: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for spec in specs:
        intent = str(spec.get("intent") or spec.get("name") or "")
        if not intent:
            continue
        if intent in by_intent:
            continue  # first declaration wins; see the docstring
        order.append(intent)
        by_intent[intent] = spec
    return [by_intent[intent] for intent in order]


def _plugin_available(source: str) -> bool:
    """Whether the pack behind a `plugin:<id>` source is available right now."""

    plugin_id = str(source or "")[len("plugin:") :].strip()
    if not plugin_id:
        return False
    try:
        from core.plugin_lifecycle import is_available

        return bool(is_available(plugin_id))
    except Exception:
        # An unreadable lifecycle store offers nothing. The old store failed OPEN, which on a
        # permission surface is the wrong direction.
        return False


def _plugin_tool_specs() -> list[dict[str, Any]]:
    """Tools a user installed, in the same catalog as the built-in ones.

    This is the line that makes VOOL extensible in the way that matters: past here, a plugin's
    tool is indistinguishable from a built-in — same native schema, same permission derivation,
    same execution record, same answer binding. Nothing downstream branches on where it came from.

    Behind a flag and fail-soft: a broken plugin must never cost the user their built-in tools.
    """

    try:
        from core.runtime_flags import flag_enabled

        if not flag_enabled("plugin_runtime_tools"):
            return []
        from core.tool_registry import registered_tools

        return [
            {
                "intent": contract.intent,
                "description": contract.description,
                "read_only": contract.read_only,
                "arguments": dict(contract.input_schema or {}),
                "json_schema": dict(contract.json_schema or {}),
            }
            for contract in registered_tools()
            if str(contract.source or "").startswith("plugin:")
            and contract.supported
            # INSTALLED IS NOT AVAILABLE. `supported` alone was the whole gate, and `supported`
            # was true for any pack that happened to be on disk. Availability is the conjunction
            # installed AND verified AND enabled AND not revoked AND still hashing to what was
            # verified, re-checked here rather than cached, so a pack edited after verification
            # leaves the model's catalog on the next turn.
            and _plugin_available(str(contract.source or ""))
        ]
    except Exception:
        return []


def _all_capability_entries(
    *,
    extra_entries: list[dict[str, Any]] | None = None,
    runtime_capability_ledger_fn=runtime_capability_ledger,
) -> list[dict[str, Any]]:
    entries = [dict(entry) for entry in runtime_capability_ledger_fn()]
    for entry in list(extra_entries or []):
        if isinstance(entry, dict):
            entries.append(dict(entry))
    return [_annotate_capability_entry(entry) for entry in entries]


def _annotate_capability_entry(entry: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(entry or {})
    capability_id = str(enriched.get("capability_id") or "").strip()
    supported = bool(enriched.get("supported"))
    support_level = str(enriched.get("support_level") or "").strip().lower()
    partial_reason = str(enriched.get("partial_reason") or "").strip()
    if support_level not in {"full", "partial", "unsupported"}:
        if partial_reason and supported:
            support_level = "partial"
        elif supported:
            support_level = "full"
        else:
            support_level = "unsupported"
    enriched["support_level"] = support_level
    if "gap_kind" not in enriched or not str(enriched.get("gap_kind") or "").strip():
        unsupported_reason = str(enriched.get("unsupported_reason") or "").strip().lower()
        if support_level == "partial":
            enriched["gap_kind"] = "partial_support"
        elif "disabled" in unsupported_reason:
            enriched["gap_kind"] = "disabled"
        elif "missing auth" in unsupported_reason:
            enriched["gap_kind"] = "missing_auth"
        elif "not configured" in unsupported_reason:
            enriched["gap_kind"] = "not_configured"
        elif "future" in unsupported_reason:
            enriched["gap_kind"] = "future_unsupported"
        else:
            enriched["gap_kind"] = "unwired"
    if "nearby_capability_ids" not in enriched:
        enriched["nearby_capability_ids"] = list(_NEARBY_CAPABILITY_IDS.get(capability_id, []))
    return enriched


def _capability_gap_from_entry(
    entry: dict[str, Any],
    *,
    requested_label: str,
    extra_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    annotated = _annotate_capability_entry(entry)
    all_entries = _all_capability_entries(extra_entries=extra_entries)
    support_level = str(annotated.get("support_level") or "unsupported").strip().lower()
    claim = str(annotated.get("claim") or "").strip()
    reason = (
        str(annotated.get("partial_reason") or "").strip()
        if support_level == "partial"
        else str(annotated.get("unsupported_reason") or claim or "").strip()
    )
    if support_level == "full" and bool(annotated.get("supported")):
        reason = claim
    return {
        "requested_capability": str(annotated.get("capability_id") or requested_label).strip(),
        "requested_label": requested_label,
        "support_level": support_level,
        "gap_kind": str(annotated.get("gap_kind") or "unwired").strip(),
        "claim": claim,
        "partial_reason": str(annotated.get("partial_reason") or "").strip(),
        "reason": reason,
        "nearby_alternatives": _combine_alternative_text(
            _nearby_alternatives_from_capability_ids(
                [str(item).strip() for item in list(annotated.get("nearby_capability_ids") or []) if str(item).strip()],
                all_entries,
            )
        ),
    }


def _nearby_alternatives_from_capability_ids(
    capability_ids: list[str],
    entries: list[dict[str, Any]],
) -> list[str]:
    entry_map = {
        str(entry.get("capability_id") or "").strip(): dict(entry)
        for entry in list(entries or [])
        if str(entry.get("capability_id") or "").strip()
    }
    alternatives: list[str] = []
    for capability_id in list(capability_ids or []):
        entry = dict(entry_map.get(str(capability_id).strip()) or {})
        if not entry:
            continue
        support_level = str(entry.get("support_level") or "unsupported").strip().lower()
        if support_level == "unsupported" and not bool(entry.get("supported")):
            continue
        claim = str(entry.get("claim") or "").strip()
        if not claim:
            continue
        if support_level == "partial" and str(entry.get("partial_reason") or "").strip():
            alternatives.append(f"{claim} ({str(entry.get('partial_reason') or '').strip()})")
        else:
            alternatives.append(claim)
    return _combine_alternative_text(alternatives)


def _nearby_alternatives_for_unknown_intent(intent: str, entries: list[dict[str, Any]]) -> list[str]:
    normalized = str(intent or "").strip().lower()
    if normalized.startswith("workspace."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["workspace.read", "workspace.write"], entries))
    if normalized.startswith("sandbox."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["workspace.read", "sandbox.command"], entries))
    if normalized.startswith("web.") or normalized.startswith("browser."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["web.live_lookup"], entries))
    if normalized.startswith("hive."):
        return _combine_alternative_text(_nearby_alternatives_from_capability_ids(["hive.read", "hive.write"], entries))
    if normalized.startswith("operator."):
        return _combine_alternative_text(
            _nearby_alternatives_from_capability_ids(["operator.inspect_processes", "operator.inspect_disk_usage"], entries)
        )
    return []


def _synthetic_gap_kind_for_intent(intent: str) -> str:
    normalized = str(intent or "").strip().lower()
    if normalized.startswith(("web.", "browser.", "workspace.", "sandbox.", "hive.", "operator.")):
        return "unwired"
    return "unsupported"


def _combine_alternative_text(items: list[str]) -> list[str]:
    seen: set[str] = set()
    combined: list[str] = []
    for item in list(items or []):
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        combined.append(clean)
    return combined


def _operator_argument_schema(tool_id: str) -> dict[str, str]:
    if tool_id == "inspect_disk_usage":
        return {"target_path": "path optional"}
    if tool_id == "cleanup_temp_files":
        return {"target_path": "path optional"}
    if tool_id == "move_path":
        return {"source_path": "path", "destination_path": "directory path"}
    if tool_id == "schedule_calendar_event":
        return {
            "title": "string",
            "start_iso": "ISO datetime",
            "duration_minutes": "integer optional",
        }
    return {}
