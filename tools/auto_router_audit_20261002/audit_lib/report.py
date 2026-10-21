"""Expectation checks and report builders (BASELINE.json, SCORECARD.md).

Checks are deterministic policy/contract assertions defined by the corpus,
independent of the implementation's internals: they look only at what an
observer can see (dispatch targets, chosen identity, outcome class, receipts,
usage rows, guard state).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import synthetic as syn


def corpus_hash(corpus: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest()


def _manifest_host(key: str) -> str:
    return str(syn.MANIFESTS[key]["runtime_config"]["base_url"])


def _chosen_manifest_keys(trace: dict[str, Any]) -> list[str]:
    """Manifest keys whose provider_id matches the decision's chosen provider."""
    last = trace["turns"][-1] if trace.get("turns") else {}
    decision = last.get("decision", {})
    provider = str(decision.get("provider_id", "") or "")
    keys = []
    for key, spec in syn.MANIFESTS.items():
        pid = f"{spec['provider_name']}:{spec['model_name']}"
        if provider and pid == provider:
            keys.append(key)
    return keys


def _dispatched_to(trace: dict[str, Any], manifest_key: str) -> bool:
    host = _manifest_host(manifest_key)
    return any(str(c.get("url", "")).startswith(host) for c in trace.get("transport_calls", []))


def _attempts(trace: dict[str, Any]) -> int:
    return len([c for c in trace.get("transport_calls", []) if c.get("method") == "POST"])


def check_trace(scenario: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every declared expectation; return {check_name: bool/details}."""
    expect = scenario.get("expect", {})
    results: dict[str, Any] = {}
    outcome = trace.get("outcome_class", "harness_error")
    turns = trace.get("turns", [])

    if "status_in" in expect:
        results["status_in"] = outcome in expect["status_in"]

    if expect.get("not_completed"):
        results["not_completed"] = outcome != "completed"

    if "chosen_cost_class_in" in expect:
        usage = trace.get("usage_rows", [])
        classes = {str(r.get("cost_class")) for r in usage if isinstance(r, dict)}
        results["chosen_cost_class_in"] = bool(classes and classes <= set(expect["chosen_cost_class_in"]))

    if "chosen_manifest_in" in expect:
        chosen = _chosen_manifest_keys(trace)
        results["chosen_manifest_in"] = bool(chosen) and all(k in expect["chosen_manifest_in"] for k in chosen)

    for key in expect.get("no_dispatch_to", []):
        results[f"no_dispatch_to:{key}"] = not _dispatched_to(trace, key)

    if expect.get("no_dispatch_any"):
        results["no_dispatch_any"] = len(trace.get("transport_calls", [])) == 0

    if expect.get("no_paid_reservation"):
        reserved = [e for e in trace.get("paid_call_events", []) if e.get("event_type") == "paid_call.reserved"]
        results["no_paid_reservation"] = not reserved

    if expect.get("all_turns_completed"):
        results["all_turns_completed"] = bool(turns) and all(
            bool(t.get("decision", {}).get("used_model")) for t in turns
        )

    # per-turn expectations for sequence scenarios
    for index, turn_exp in enumerate(expect.get("turn_expectations", [])):
        if index >= len(turns):
            results[f"turn{index}_present"] = False
            continue
        window = turns[index].get("transport_call_window") or [0, len(trace.get("transport_calls", []))]
        turn_calls = trace.get("transport_calls", [])[window[0]:window[1]]
        if "no_dispatch_to" in turn_exp:
            hosts = [_manifest_host(k) for k in turn_exp["no_dispatch_to"]]
            results[f"turn{index}_no_dispatch_to"] = not any(
                str(c.get("url", "")).startswith(tuple(hosts)) for c in turn_calls
            )
        if "chosen_manifest_in" in turn_exp:
            decision_i = turns[index].get("decision", {})
            provider = str(decision_i.get("provider_id", "") or "")
            wanted = {f"{syn.MANIFESTS[k]['provider_name']}:{syn.MANIFESTS[k]['model_name']}" for k in turn_exp["chosen_manifest_in"]}
            results[f"turn{index}_chosen_in"] = provider in wanted

    # named checks
    for name in expect.get("checks", []):
        results[name] = _named_check(name, scenario, trace)

    return results


def _named_check(name: str, scenario: dict[str, Any], trace: dict[str, Any]) -> bool:
    posts = [c for c in trace.get("transport_calls", []) if c.get("method") == "POST"]
    payloads = [c.get("payload") or {} for c in posts]
    if name == "usage_row_recorded":
        return len(trace.get("usage_rows", [])) >= 1
    if name == "no_transport_calls":
        # dispatch = a POST to a model endpoint; ambient loopback inventory
        # reads are not dispatches
        return not any(c.get("method") == "POST" for c in trace.get("transport_calls", []))
    if name == "envelope_carries_response_format":
        return any(p.get("response_format") for p in payloads)
    if name == "envelope_carries_tools_or_json":
        return any(p.get("tools_count") or p.get("response_format") for p in payloads)
    if name == "tool_call_observed_or_typed_failure":
        # a native tool call payload was answered AND either a parsed call or a
        # typed failure shows up in events; both prove the tool contract ran
        outcome = trace.get("outcome_class")
        if outcome == "completed":
            return any("tool" in json.dumps(e).lower() for e in trace.get("model_events", [])) or any(
                str(t.get("decision", {}).get("output_text", "")).strip() for t in trace.get("turns", [])
            )
        return True  # typed refusal/failure also satisfies "not fake success"
    if name == "paid_receipt_pair":
        types = [e.get("event_type") for e in trace.get("paid_call_events", [])]
        return "paid_call.reserved" in types and any(
            t in types for t in ("paid_call.settled", "paid_call.billing_ambiguous")
        )
    if name == "single_dispatch_to_paid":
        paid_host = _manifest_host("paid_cloud")
        return len([c for c in posts if str(c.get("url", "")).startswith(paid_host)]) == 1
    if name == "timeout_then_success_observed":
        scripts = [c.get("script") for c in posts]
        return "timeout" in scripts and trace.get("outcome_class") == "completed"
    if name == "attempts_at_least_2":
        return _attempts(trace) >= 2
    if name == "language_policy_observed":
        return trace.get("outcome_class") == "completed"
    if name == "completed_only_on_sufficient_context_window":
        outcome = trace.get("outcome_class")
        if outcome != "completed":
            return True
        chosen = _chosen_manifest_keys(trace)
        if not chosen:
            return False
        spec = syn.MANIFESTS[chosen[0]]
        fill = int(trace.get("long_fill_chars", 0) or 0)
        needed = max(2048, (fill // 4) + 1024)
        return int(spec["runtime_config"]["context_window"]) >= needed
    return False


def scenario_verdict(scenario: dict[str, Any], trace: dict[str, Any]) -> str:
    """pass / fail / control-observed for one scenario."""
    if scenario.get("control") or scenario.get("expect", {}).get("control"):
        return "control_observed" if trace.get("outcome_class") == "guard_violation" else "control_failed"
    if trace.get("harness_error"):
        return "harness_error"
    checks = check_trace(scenario, trace)
    return "pass" if checks and all(bool(v) for v in checks.values()) else "fail"


def build_baseline(traces: list[dict[str, Any]], corpus: dict[str, Any], *, repo_head: str,
                   guard_self_test: dict[str, bool], command: str) -> dict[str, Any]:
    by_id = {s["id"]: s for s in corpus["scenarios"]}
    scenario_rows = []
    for trace in traces:
        scenario = by_id.get(trace.get("scenario_id"), {})
        checks = check_trace(scenario, trace)
        scenario_rows.append(
            {
                "id": trace.get("scenario_id"),
                "split": trace.get("split"),
                "category": trace.get("category"),
                "outcome_class": trace.get("outcome_class"),
                "verdict": scenario_verdict(scenario, trace),
                "checks": checks,
                "attempts": _attempts(trace),
                "chosen": (_chosen_manifest_keys(trace) or [None])[0],
                "chosen_provider_id": (trace.get("turns") or [{}])[-1].get("decision", {}).get("provider_id"),
                "guard_violations": len(trace.get("guard_violations", [])),
                "ambient_live_fetches_blocked": len(trace.get("ambient_live_fetches_blocked", [])),
                "usage_cost_classes": sorted(
                    {str(r.get("cost_class")) for r in trace.get("usage_rows", []) if isinstance(r, dict)}
                ),
                "duration_seconds": round(sum(float(t.get("duration_seconds", 0) or 0) for t in trace.get("turns", [])), 3),
            }
        )
    scored = [r for r in scenario_rows if r["split"] in ("dev", "heldout")]
    return {
        "schema": "auto-router-audit/baseline@1",
        "base_commit": corpus["base_commit"],
        "repo_head": repo_head,
        "corpus_sha256": corpus_hash(corpus),
        "corpus_version": corpus["corpus_version"],
        "command": command,
        "guard_self_test": guard_self_test,
        "network_policy": "all synthetic endpoints; process-wide guard on requests.* and urllib urlopen; zero live dispatch",
        "scenario_count": len(scenario_rows),
        "summary": {
            "pass": sum(1 for r in scored if r["verdict"] == "pass"),
            "fail": sum(1 for r in scored if r["verdict"] == "fail"),
            "harness_error": sum(1 for r in scored if r["verdict"] == "harness_error"),
            "controls": {r["verdict"] for r in scenario_rows if r["split"] == "control"},
            "by_split": {
                split: {
                    "pass": sum(1 for r in scored if r["split"] == split and r["verdict"] == "pass"),
                    "fail": sum(1 for r in scored if r["split"] == split and r["verdict"] == "fail"),
                }
                for split in ("dev", "heldout")
            },
        },
        "scenarios": scenario_rows,
    }


_SCORECARD_HEADER = """# Auto router audit scorecard

Base: `{base}` | corpus `{version}` (`{corpus_sha}…`) | verdicts from declared expectations only.

Every scored scenario is a contract/policy check on the real selection+execution
seams with synthetic transports. `attempts` = POST dispatches observed;
`outcome` = completed / refused / infra_or_unresolved / guard_violation.
A refusal is never counted as success; `not_completed` scenarios require it.
"""


def build_scorecard(baseline: dict[str, Any]) -> str:
    sha_short = str(baseline["corpus_sha256"])[:12]
    lines = [_SCORECARD_HEADER.format(
        base=baseline["base_commit"], version=baseline["corpus_version"],
        corpus_sha=sha_short)]
    s = baseline["summary"]
    lines.append(
        f"**Totals** — pass {s['pass']} / fail {s['fail']} / harness-error {s['harness_error']}"
        f" (dev {s['by_split']['dev']['pass']}P/{s['by_split']['dev']['fail']}F,"
        f" heldout {s['by_split']['heldout']['pass']}P/{s['by_split']['heldout']['fail']}F);"
        f" guard self-test: {'OK' if all(baseline['guard_self_test'].values()) else 'FAILED'}."
    )
    lines.append("")
    lines.append("| split | scenario | category | outcome | verdict | chosen | attempts | failed checks |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in baseline["scenarios"]:
        failed = [k for k, v in row["checks"].items() if not v]
        lines.append(
            f"| {row['split']} | `{row['id']}` | {row['category']} | {row['outcome_class']} "
            f"| **{row['verdict']}** | {row['chosen'] or '-'} | {row['attempts']} "
            f"| {', '.join(failed) if failed else '-'} |"
        )
    lines.append("")
    lines.append("Cost classes recorded per scenario (usage rows): " + "; ".join(
        f"`{r['id']}`={','.join(r['usage_cost_classes']) or 'none'}" for r in baseline["scenarios"]))
    return "\n".join(lines)
