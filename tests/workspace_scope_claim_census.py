#!/usr/bin/env python3
"""The front-door claim census: what the workspace-scope admissions, the workspace search arm, the intent arbiter,
the live-info door and the creative-writing register claim, and who owns it.

WHY THIS EXISTS
---------------
The served Notes folder-routing repair (2026-09-15) made the two workspace-scope admissions ask the lane registry
before they claim (`core.agent_runtime.demand_ownership.registered_owner_ahead_of`, through
`fast_paths_utility._another_domain_owns_the_turn`). The residuals repair that followed (2026-09-15) made the same
question binding on three more front-door claims the registry cannot see: the workspace-runtime search arm, the intent
arbiter and the live-info door. It also added the creative-writing register (`grounded_mode.creative_writing_remainder`)
to the live readers and the folder overview. Whether those rules are safe depends on what they change. This census
measures that, and it can be re-run against any tree; a part whose rule is absent says so:

* Part A (admissions) covers every corpus sentence either admission claims when the registry is not asked (the
  pre-repair admission, reproduced by forcing the helper to report no owner). For each one it records the registered
  owner of the whole turn and whether the repaired admission now declines it. A declined sentence is a route this
  repair moved; a claimed sentence with an owner would be a route it failed to move.
* Part B (near misses) covers the sentences no probe family claims that still carry a tool-ish noun, where a
  registered lane owns the whole turn ahead of the front-door tier: the raw population the arbiter gate was sized on.
* Part C (search arm) runs the real workspace-runtime admission (planner included; tool execution recorded, never run)
  with and without the registry question. Declined sentences are routes the repair moved; claims despite an owner are
  routes it failed to move.
* Part D (arbiter) covers every sentence that carries a near-miss or ambiguity signal, and names the registered owner
  outside the arbiter menu's domains that now stops the arbiter from being asked.
* Part E (live-info door) covers every sentence the text-only live-info reading claims, and names the registered owner
  of another domain the door now yields to.
* Part F (creative register) covers every sentence the register changes: the live-info mode, the live-data
  classification and the folder-overview claim, each with and without it.

The measurements and the stage records are in `VOOL-DELIVERY/CURRENT_STATE.json`
(`served_notes_folder_scope_routing_2026_09_15`, `served_notes_precedence_residuals_2026_09_15`).

POPULATION (stated, not implied)
--------------------------------
Every distinct string literal of 5 to 200 characters with at least two words, in `tests/**/*.py` and
`ops/routing_reliability_corpus.py`. The population is re-derived from the tree on every run.

USAGE
-----
    PYTHONDONTWRITEBYTECODE=1 <tree>/.venv/bin/python tests/workspace_scope_claim_census.py [--out PATH]

The real admissions run against a recording agent, a temporary workspace and canned tool results. No model or network
is involved, and nothing is written except `--out`.
"""
from __future__ import annotations

import argparse
import ast
import collections
import inspect
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
TIER = "turn_frontdoor_deterministic"


class _RecordingAgent:
    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason):
        return {"response": response, "reason": reason}

    def _plan_tool_workflow(self, **kwargs):
        from core.execution.planner import plan_tool_workflow

        return plan_tool_workflow(**kwargs)

    def _emit_runtime_event(self, source_context, **kwargs):
        return None


def corpus(root: Path = REPO_ROOT) -> dict[str, str]:
    """Sentence -> the first file it appears in."""
    files = [*sorted((root / "tests").rglob("*.py")), root / "ops" / "routing_reliability_corpus.py"]
    seen: dict[str, str] = {}
    for path in files:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = " ".join(node.value.split())
                if 5 <= len(text) <= 200 and len(text.split()) >= 2:
                    seen.setdefault(text, str(path.relative_to(root)))
    return seen


def _claim(utility, agent, text: str, context: dict) -> str | None:
    overview = utility.maybe_handle_folder_overview_request(
        agent, text, session_id="census", source_surface="api", source_context=context
    )
    identity = utility.maybe_handle_workspace_identity_request(
        agent, text, session_id="census", source_surface="api", source_context=context
    )
    return (overview or identity or {}).get("reason")


def admission_census(sentences: dict[str, str]) -> dict:
    import core.agent_runtime.demand_ownership as ownership
    import core.agent_runtime.fast_paths_utility as utility

    rule_present = hasattr(utility, "_another_domain_owns_the_turn") and hasattr(ownership, "registered_owner_ahead_of")
    agent = _RecordingAgent()
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        workspace = Path(folder)
        (workspace / "README.md").write_text("# Census\nA workspace for the census.\n", encoding="utf-8")
        (workspace / "census_probe.py").write_text("print('census')\n", encoding="utf-8")
        context = {"workspace": str(workspace), "surface": "api"}
        identity = SimpleNamespace(ok=True, response_text=f"The workspace is set to `{workspace}`.")
        with mock.patch("core.runtime_execution_tools.execute_runtime_tool", return_value=identity):
            for text, source in sentences.items():
                repaired = _claim(utility, agent, text, context)
                if rule_present:
                    with mock.patch.object(utility, "_another_domain_owns_the_turn", return_value=False):
                        unasked = _claim(utility, agent, text, context)
                else:
                    unasked = repaired
                if unasked is None and repaired is None:
                    continue
                owner = any_domain = ""
                if rule_present:
                    owner = ownership.registered_owner_ahead_of(text, TIER, capability="workspace_read")
                    any_domain = ownership.registered_owner_ahead_of(text, TIER)
                rows.append({"text": text, "source": source, "claim_unasked": unasked, "claim_repaired": repaired,
                             "owner": owner, "owner_any_domain": any_domain})
    return {
        "rule_present": rule_present,
        "claimed_unasked": sum(1 for row in rows if row["claim_unasked"]),
        "owned_any_domain": dict(collections.Counter(row["owner_any_domain"] for row in rows if row["owner_any_domain"])),
        "declined_by_the_repair": [row for row in rows if row["claim_unasked"] and not row["claim_repaired"]],
        "claimed_despite_an_owner": [row for row in rows if row["claim_repaired"] and row["owner"]],
        "rows": rows,
    }


def near_miss_census(sentences: dict[str, str]) -> dict:
    from core.agent_runtime.demand_ownership import _COVERAGE_DOMAIN_GROUPS, registered_owner_ahead_of
    from core.agent_runtime.intent_claims import near_miss, probe_claims
    from core.lane_registry import find_spec

    near = 0
    rows = []
    for text, source in sentences.items():
        try:
            if not near_miss(text, probe_claims(text)):
                continue
        except Exception:
            continue
        near += 1
        owner = registered_owner_ahead_of(text, TIER)
        if owner:
            coverage = find_spec(owner).coverage
            rows.append({"text": text, "source": source, "owner": owner,
                         "owner_domain": _COVERAGE_DOMAIN_GROUPS.get(coverage, coverage)})
    return {
        "near_miss_sentences": near,
        "owned_ahead_of_the_tier": len(rows),
        "by_owner": dict(collections.Counter(row["owner"] for row in rows)),
        "by_owner_domain": dict(collections.Counter(row["owner_domain"] for row in rows)),
        "rows": rows,
    }


def _search_claim(utility, agent, text: str, context: dict) -> str | None:
    result = utility.maybe_handle_direct_workspace_runtime_request(
        agent, text, session_id="census", source_surface="api", source_context=context
    )
    return (result or {}).get("reason")


def search_arm_census(sentences: dict[str, str]) -> dict:
    import core.agent_runtime.fast_paths_utility as utility
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of

    source_text = inspect.getsource(utility.maybe_handle_direct_workspace_runtime_request)
    rule_present = 'lane_id="workspace_read_fast_path"' in source_text
    agent = _RecordingAgent()
    rows = []
    ran = SimpleNamespace(ok=True, response_text="census tool result", details={})
    with tempfile.TemporaryDirectory() as folder:
        context = {"workspace": folder, "surface": "api"}
        with mock.patch.object(utility, "execute_runtime_tool", return_value=ran):
            for text, source in sentences.items():
                try:
                    with mock.patch.object(utility, "_another_domain_owns_the_turn", return_value=False):
                        unasked = _search_claim(utility, agent, text, context)
                    if unasked is None:
                        continue
                    repaired = _search_claim(utility, agent, text, context) if rule_present else unasked
                    owner = (
                        registered_owner_ahead_of(text, "workspace_read_fast_path", capability="workspace_read")
                        if rule_present else ""
                    )
                except Exception:
                    continue
                rows.append({"text": text, "source": source, "claim_unasked": unasked, "claim_repaired": repaired,
                             "owner": owner})
    return {
        "rule_present": rule_present,
        "claimed_unasked": len(rows),
        "declined_by_the_repair": [row for row in rows if not row["claim_repaired"]],
        "claimed_despite_an_owner": [row for row in rows if row["claim_repaired"] and row["owner"]],
        "rows": rows,
    }


def arbiter_census(sentences: dict[str, str]) -> dict:
    import core.agent_runtime.turn_frontdoor as frontdoor
    from core.agent_runtime.intent_claims import is_ambiguous, near_miss, probe_claims

    rule_present = hasattr(frontdoor, "_registered_owner_outside_the_menu")
    signals = collections.Counter()
    rows = []
    for text, source in sentences.items():
        try:
            claims = probe_claims(text)
            signal = "near_miss" if near_miss(text, claims) else ("ambiguous" if is_ambiguous(claims) else "")
        except Exception:
            continue
        if not signal:
            continue
        signals[signal] += 1
        owner = frontdoor._registered_owner_outside_the_menu(text) if rule_present else ""
        if owner:
            rows.append({"text": text, "source": source, "signal": signal, "owner": owner})
    return {
        "rule_present": rule_present,
        "signal_sentences": dict(signals),
        "not_arbitrated": len(rows),
        "by_owner": dict(collections.Counter(row["owner"] for row in rows)),
        "rows": rows,
    }


def live_info_door_census(sentences: dict[str, str]) -> dict:
    """What a registry yield at the live-info door WOULD move. Measured, not applied: the residuals repair tried it
    and reverted it, because most of the moved sentences are live lookups whose registered owner runs earlier in the
    front door (the currency lanes) or declines the turn (the read lane on 'Node.js')."""
    import core.agent_runtime.turn_frontdoor as frontdoor
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of
    from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode

    rule_present = '"live_info_fast_path", capability="live_data"' in inspect.getsource(frontdoor.handle_turn_frontdoor)
    claimed = collections.Counter()
    rows = []
    for text, source in sentences.items():
        try:
            mode = live_info_mode(None, text, interpretation=None)
            if not mode:
                continue
            claimed[mode] += 1
            owner = registered_owner_ahead_of(text, "live_info_fast_path", capability="live_data")
        except Exception:
            continue
        if owner:
            rows.append({"text": text, "source": source, "mode": mode, "owner": owner})
    return {
        "rule_present": rule_present,
        "claimed_by_mode": dict(claimed),
        "would_yield": len(rows),
        "by_owner": dict(collections.Counter(row["owner"] for row in rows)),
        "rows": rows,
    }


def creative_register_census(sentences: dict[str, str]) -> dict:
    import core.agent_runtime.grounded_mode as grounded

    if not hasattr(grounded, "creative_writing_remainder"):
        return {"rule_present": False}
    import core.agent_runtime.fast_paths_utility as utility
    from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
    from core.execution_requirements import _live_data_classification

    agent = _RecordingAgent()
    rewritten = 0
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        workspace = Path(folder)
        (workspace / "README.md").write_text("# Census\nA workspace for the census.\n", encoding="utf-8")
        context = {"workspace": str(workspace), "surface": "api"}

        def readings(text):
            overview = utility.maybe_handle_folder_overview_request(
                agent, text, session_id="census", source_surface="api", source_context=context
            )
            return {
                "live_info_mode": live_info_mode(None, text, interpretation=None),
                "live_data": _live_data_classification(text) is not None,
                "overview": (overview or {}).get("reason"),
            }

        for text, source in sentences.items():
            try:
                if grounded.creative_writing_remainder(text) == text:
                    continue
                rewritten += 1
                with_register = readings(text)
                with mock.patch.object(grounded, "creative_writing_remainder", side_effect=lambda value: value):
                    without_register = readings(text)
            except Exception:
                continue
            if with_register != without_register:
                rows.append({"text": text, "source": source, "without": without_register, "with": with_register})
    return {"rule_present": True, "rewritten_sentences": rewritten, "changed": len(rows), "rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="front-door claim census")
    parser.add_argument("--out", help="write the full JSON record to this path")
    args = parser.parse_args(argv)
    sentences = corpus()
    admissions = admission_census(sentences)
    near_misses = near_miss_census(sentences) if admissions["rule_present"] else {"skipped": "rule absent in this tree"}
    search = search_arm_census(sentences)
    arbiter = arbiter_census(sentences)
    live_door = live_info_door_census(sentences)
    creative = creative_register_census(sentences)
    record = {"population": len(sentences), "admissions": admissions, "near_misses": near_misses, "search_arm": search,
              "arbiter": arbiter, "live_info_door": live_door, "creative_register": creative}
    if args.out:
        Path(args.out).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    summary = {
        "population": record["population"],
        "rule_present": admissions["rule_present"],
        "claimed_unasked": admissions["claimed_unasked"],
        "owned_any_domain": admissions["owned_any_domain"],
        "declined_by_the_repair": [row["text"] for row in admissions["declined_by_the_repair"]],
        "claimed_despite_an_owner": [row["text"] for row in admissions["claimed_despite_an_owner"]],
        "near_misses": {key: value for key, value in near_misses.items() if key != "rows"},
        "search_arm": {
            "rule_present": search["rule_present"],
            "claimed_unasked": search["claimed_unasked"],
            "declined_by_the_repair": [row["text"] for row in search["declined_by_the_repair"]],
            "claimed_despite_an_owner": [row["text"] for row in search["claimed_despite_an_owner"]],
        },
        "arbiter": {key: value for key, value in arbiter.items() if key != "rows"},
        "live_info_door": {**{key: value for key, value in live_door.items() if key != "rows"},
                           "yielded_texts": [row["text"] for row in live_door["rows"]]},
        "creative_register": {**{key: value for key, value in creative.items() if key != "rows"},
                              "changed_texts": [row["text"] for row in creative.get("rows", [])]},
    }
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    raise SystemExit(main())
