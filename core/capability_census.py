"""Capability census: declared → registered → selectable → permitted → executable → executed → receipted.

One table, one row per tool intent, answering for each stage whether the tool passes it and, when
it does not, WHY in a typed word. This is the instrument the audited runtime lacked: it declared
79 tools while models repeatedly saw 8, and nothing could say where the other 71 fell out.

Stages, meant exactly:

``declared``    a contract exists somewhere the runtime reads: the builtin literals, a plugin
                manifest on disk, a configured MCP server's listing.
``registered``  the contract is in `core.tool_registry` (the ONE authority everything reads).
``selectable``  the contract is supported AND the offer seam has a deterministic legal path to
                seat it (`capability_graph.legal_offer_paths_map`). Otherwise the row carries a
                typed `not_model_facing_reason`.
``permitted``   the permission controller's classification for the intent is not DENY in the
                census mode — computed from `actions_for_tool` and the mode matrix, without
                minting an approval request (a census must not leave prompts behind).
``executable``  a dispatch lane actually claims the intent: runtime handler, external lane
                (web/hive/operator/payment), confined plugin executor, or a reachable MCP server.
``executed``    execution records exist for the intent in the given session (any outcome).
``receipted``   those records carry execution truth beyond the name — a resolved target, items,
                citations, an asserted action — i.e. an answer can be bound against them.

The counts are monotone by construction (each stage requires the one before it), so a summary
line reads as a funnel. Pure and side-effect free: safe to run against a live daemon.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

STAGES: tuple[str, ...] = (
    "declared",
    "registered",
    "selectable",
    "permitted",
    "executable",
    "executed",
    "receipted",
)


class NotModelFacingReason(str, Enum):
    """Why a registered contract is not selectable. Typed, so the census cannot drift into prose."""

    POLICY_DISABLED = "policy_disabled"  # builtin contract: supported=False by runtime policy
    PLUGIN_DISABLED = "plugin_disabled_by_owner"  # owner turned the pack off
    PLUGIN_LANE_CLOSED = "plugin_lane_closed"  # `plugin_runtime_tools` flag is off
    MCP_SERVER_UNAVAILABLE = "mcp_server_unavailable"  # configured, but the server is not answering
    NOT_REGISTERED = "not_registered"  # declared on disk, refused or not loaded
    NO_OFFER_PATH = "no_offer_path"  # supported and registered, yet nothing can seat it


@dataclass(frozen=True)
class CensusRow:
    intent: str
    source: str
    surface: str
    declared: bool
    registered: bool
    supported: bool
    unsupported_reason: str
    selectable: bool
    offer_paths: tuple[str, ...]
    not_model_facing_reason: str
    permitted: bool
    permission_effect: str
    permission_actions: tuple[str, ...]
    executable: bool
    execution_lane: str
    executed: int
    receipted: int


@dataclass(frozen=True)
class Census:
    rows: tuple[CensusRow, ...]
    mode: str
    session_id: str

    def by_intent(self) -> dict[str, CensusRow]:
        return {row.intent: row for row in self.rows}

    def summary(self) -> dict[str, int]:
        return {
            "declared": sum(1 for r in self.rows if r.declared),
            "registered": sum(1 for r in self.rows if r.registered),
            "selectable": sum(1 for r in self.rows if r.selectable),
            "not_model_facing": sum(1 for r in self.rows if r.registered and not r.selectable),
            "permitted": sum(1 for r in self.rows if r.permitted),
            "executable": sum(1 for r in self.rows if r.executable),
            "executed": sum(1 for r in self.rows if r.executed > 0),
            "receipted": sum(1 for r in self.rows if r.receipted > 0),
        }

    def unsupported(self) -> tuple[CensusRow, ...]:
        return tuple(r for r in self.rows if not r.selectable)


# ---------------------------------------------------------------------------
# Stage evaluators
# ---------------------------------------------------------------------------


def _external_lane_for(intent: str) -> str:
    from core.execution.constants import (
        _HIVE_TOOL_INTENTS,
        _MUTATING_OPERATOR_INTENTS,
        _PAYMENT_TOOL_INTENTS,
        _READ_ONLY_OPERATOR_INTENTS,
        _WEB_TOOL_INTENTS,
    )

    if intent in _WEB_TOOL_INTENTS:
        return "external_lane:web"
    if intent in _HIVE_TOOL_INTENTS:
        return "external_lane:hive"
    if intent in _READ_ONLY_OPERATOR_INTENTS | _MUTATING_OPERATOR_INTENTS:
        return "external_lane:operator"
    if intent in _PAYMENT_TOOL_INTENTS:
        return "external_lane:payment"
    return ""


def _execution_lane(contract: Any) -> str:
    intent = str(getattr(contract, "intent", "") or "")
    source = str(getattr(contract, "source", "builtin") or "builtin")
    if intent == "respond.direct":
        return "direct_response"
    if source.startswith("plugin:"):
        try:
            from core.plugin_tools import plugin_root_for

            return "plugin" if plugin_root_for(intent) is not None else ""
        except Exception:
            return ""
    if source.startswith("mcp:"):
        try:
            from core.execution import mcp_bridge

            server = source[len("mcp:"):]
            return "mcp" if mcp_bridge._client_for(server) is not None else ""
        except Exception:
            return ""
    lane = _external_lane_for(intent)
    if lane:
        return lane
    if str(getattr(contract, "handler", "runtime") or "runtime") == "runtime":
        return "runtime"
    return ""


def _permission(intent: str, mode: str) -> tuple[str, tuple[str, ...]]:
    """(effect, actions) from the controller's classifier and the mode matrix. Mints nothing."""
    from core.mode_permission_policy import (
        MODE_PERMISSION_MATRIX,
        PermissionEffect,
        actions_for_tool,
        normalize_mode,
    )

    try:
        actions = actions_for_tool(intent, {}, None)
    except Exception:
        return "deny", ()
    resolved_mode = normalize_mode(mode, allow_legacy=False)
    if resolved_mode is None:
        return "deny", tuple(a.value for a in actions)
    row = MODE_PERMISSION_MATRIX.get(resolved_mode, {})
    effects = {row.get(action, PermissionEffect.DENY) for action in actions}
    if not actions:
        effect = PermissionEffect.ALLOW
    elif PermissionEffect.DENY in effects:
        effect = PermissionEffect.DENY
    elif PermissionEffect.REQUIRE_APPROVAL in effects:
        effect = PermissionEffect.REQUIRE_APPROVAL
    else:
        effect = PermissionEffect.ALLOW
    return effect.value, tuple(a.value for a in actions)


def _not_model_facing_reason(contract: Any, *, lane_open: bool) -> str:
    source = str(getattr(contract, "source", "builtin") or "builtin")
    supported = bool(getattr(contract, "supported", False))
    reason = str(getattr(contract, "unsupported_reason", "") or "")
    if not supported:
        if source.startswith("plugin:") and "disabled" in reason:
            return NotModelFacingReason.PLUGIN_DISABLED.value
        if source.startswith("mcp:"):
            return NotModelFacingReason.MCP_SERVER_UNAVAILABLE.value
        return NotModelFacingReason.POLICY_DISABLED.value
    if source.startswith("plugin:") and not lane_open:
        return NotModelFacingReason.PLUGIN_LANE_CLOSED.value
    if source.startswith("mcp:") and not lane_open:
        return NotModelFacingReason.MCP_SERVER_UNAVAILABLE.value
    return NotModelFacingReason.NO_OFFER_PATH.value


def _declared_but_unregistered(known: set[str]) -> list[tuple[str, str, str]]:
    """(intent, plugin source, reason) for manifest-declared tools that never registered."""
    rows: list[tuple[str, str, str]] = []
    try:
        from core.plugin_catalog import discovered_manifests
        from core.plugin_tools import PluginManifestError, load_manifest

        # The bounded storage probe's listing (see core.plugin_catalog): a census must not hang
        # on a plugin folder that stalls, and an inaccessible folder simply lists nothing here.
        for manifest in discovered_manifests():
            try:
                plugin = load_manifest(manifest)
            except PluginManifestError as exc:
                plugin_id = manifest.parent.parent.name
                rows.append((f"{plugin_id}.*", f"plugin:{plugin_id}", f"manifest refused: {exc}"[:200]))
                continue
            for contract in plugin.contracts:
                if contract.intent not in known:
                    rows.append((contract.intent, contract.source, "lane closed or not loaded"))
    except Exception:
        return rows
    return rows


def _records_for(session_id: str) -> dict[str, tuple[int, int]]:
    if not session_id:
        return {}
    try:
        from core import execution_records

        counts: dict[str, list[int]] = {}
        for record in execution_records.records_for(session_id):
            bucket = counts.setdefault(str(record.intent), [0, 0])
            bucket[0] += 1
            truth = bool(
                getattr(record, "resolved_target", "")
                or getattr(record, "items", ())
                or getattr(record, "citations", ())
                or getattr(record, "asserts_action", False)
            )
            if truth:
                bucket[1] += 1
        return {k: (v[0], v[1]) for k, v in counts.items()}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------


def capability_census(*, mode: str = "manual", session_id: str = "") -> Census:
    from core.capability_graph import ensure_registry_bootstrap, legal_offer_paths_map
    from core.tool_intent_executor import runtime_tool_specs
    from core.tool_registry import registered_tools

    ensure_registry_bootstrap()
    contracts = list(registered_tools())
    known = {c.intent for c in contracts}
    paths = legal_offer_paths_map()
    catalog = {str(s.get("intent") or "") for s in runtime_tool_specs()}
    records = _records_for(session_id)

    rows: list[CensusRow] = []
    for contract in contracts:
        intent = str(contract.intent)
        source = str(getattr(contract, "source", "builtin") or "builtin")
        supported = bool(getattr(contract, "supported", False))
        lane_open = intent in catalog
        offer_paths = tuple(paths.get(intent, ()))
        selectable = supported and lane_open and bool(offer_paths)
        reason = "" if selectable else _not_model_facing_reason(contract, lane_open=lane_open)
        effect, actions = _permission(intent, mode) if selectable else ("", ())
        permitted = selectable and effect in {"allow", "require_approval"}
        lane = _execution_lane(contract) if permitted else ""
        executable = permitted and bool(lane)
        executed, receipted = records.get(intent, (0, 0))
        rows.append(
            CensusRow(
                intent=intent,
                source=source,
                surface=str(getattr(contract, "tool_surface", "") or ""),
                declared=True,
                registered=True,
                supported=supported,
                unsupported_reason=str(getattr(contract, "unsupported_reason", "") or ""),
                selectable=selectable,
                offer_paths=offer_paths,
                not_model_facing_reason=reason,
                permitted=permitted,
                permission_effect=effect,
                permission_actions=actions,
                executable=executable,
                execution_lane=lane,
                executed=int(executed) if executable else 0,
                receipted=int(receipted) if executable else 0,
            )
        )
    for intent, source, why in _declared_but_unregistered(known):
        rows.append(
            CensusRow(
                intent=intent,
                source=source,
                surface="plugin",
                declared=True,
                registered=False,
                supported=False,
                unsupported_reason=why,
                selectable=False,
                offer_paths=(),
                not_model_facing_reason=NotModelFacingReason.NOT_REGISTERED.value,
                permitted=False,
                permission_effect="",
                permission_actions=(),
                executable=False,
                execution_lane="",
                executed=0,
                receipted=0,
            )
        )
    rows.sort(key=lambda r: r.intent)
    return Census(rows=tuple(rows), mode=str(mode), session_id=str(session_id or ""))


def render_census(census: Census) -> str:
    summary = census.summary()
    lines = [
        f"capability census (mode={census.mode}"
        + (f", session={census.session_id}" if census.session_id else "")
        + ")",
        "  " + " → ".join(f"{stage} {summary[stage]}" for stage in STAGES),
        f"  not model-facing: {summary['not_model_facing']}",
    ]
    for row in census.rows:
        if row.selectable:
            continue
        lines.append(f"  - {row.intent}: {row.not_model_facing_reason} ({row.unsupported_reason or 'no reason recorded'})")
    return "\n".join(lines)


def census_as_json(census: Census) -> str:
    return json.dumps(
        {"mode": census.mode, "session_id": census.session_id, "summary": census.summary(),
         "rows": [asdict(row) for row in census.rows]},
        indent=2,
        sort_keys=True,
        default=str,
    )


# ---------------------------------------------------------------------------
# Coding-family coverage
# ---------------------------------------------------------------------------

_CODING_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("filesystem_read", ("workspace.read_file", "workspace.list_files", "workspace.list_tree", "machine.read_file")),
    ("filesystem_write", ("workspace.write_file", "workspace.replace_in_file", "workspace.apply_unified_diff", "workspace.ensure_directory")),
    ("search", ("workspace.search_text", "workspace.symbol_search")),
    ("shell", ("sandbox.run_command",)),
    ("tests", ("workspace.run_tests",)),
    ("lint", ("workspace.run_lint", "workspace.run_formatter")),
    ("git_read", ("workspace.git_status", "workspace.git_diff", "workspace.git_summary")),
    ("git_mutation", ()),
)

_GIT_MUTATION_REASON = (
    "no typed git mutation contract (commit/branch/stash/push) is declared in this build; git "
    "mutations reach the runtime only through sandbox.run_command, where the permission "
    "controller classifies them as git_commit / git_push / git_merge_or_rebase / git_reset_or_clean "
    "and Manual and Auto both prompt"
)


def coding_family_coverage() -> tuple[dict[str, Any], ...]:
    """Which coding capabilities the contracted surface covers, and the one it does not, named."""
    from core.capability_graph import ensure_registry_bootstrap
    from core.tool_registry import registry_map

    ensure_registry_bootstrap()
    contracts = registry_map()
    rows: list[dict[str, Any]] = []
    for capability, intents in _CODING_FAMILIES:
        present = tuple(i for i in intents if i in contracts and contracts[i].supported)
        if not intents:
            rows.append(
                {"capability": capability, "status": "not_declared", "intents": (), "reason": _GIT_MUTATION_REASON}
            )
        elif present:
            rows.append({"capability": capability, "status": "covered", "intents": present, "reason": ""})
        else:
            rows.append(
                {
                    "capability": capability,
                    "status": "declared_unsupported",
                    "intents": (),
                    "reason": "; ".join(
                        f"{i}: {contracts[i].unsupported_reason}" for i in intents if i in contracts
                    ) or "no contract present",
                }
            )
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="VOOL capability census")
    parser.add_argument("--mode", default="manual")
    parser.add_argument("--session", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    census = capability_census(mode=args.mode, session_id=args.session)
    print(census_as_json(census) if args.json else render_census(census))
    for row in coding_family_coverage():
        print(f"  coding/{row['capability']}: {row['status']} {list(row['intents'])} {row['reason']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "STAGES",
    "Census",
    "CensusRow",
    "NotModelFacingReason",
    "capability_census",
    "census_as_json",
    "coding_family_coverage",
    "render_census",
]
