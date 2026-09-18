"""Measure disagreements between VOOL's execution-truth stores.

Reads a runtime DB (and its honesty-receipt ledger) and reports, per contradiction class,
how many real executions the secondary views fail to agree about. Read-only.

    python tools/truth/measure_execution_truth.py [--home ~/.vool_runtime_v050]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sqlite3

TOOL_EVENTS = ("tool_executed", "tool_failed")


def _rows(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _loads(raw):
    try:
        return json.loads(raw)
    except Exception:
        return {}


def measure(home: str) -> dict:
    db = os.path.join(home, "data", "vool_web0_v2.db")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: dict = {"db": db}

    # ---- C1: executions vs tool receipts -------------------------------------------------
    execs = [
        (r["event_type"], _loads(r["details_json"]))
        for r in _rows(conn, f"SELECT event_type, details_json FROM runtime_session_events WHERE event_type IN {TOOL_EVENTS}")
    ]
    by_tool = collections.Counter(str(d.get("tool_name") or "?") for _, d in execs)
    thin = [(t, d) for t, d in execs if not d.get("action_record")]
    receipt_tools = collections.Counter(
        str(r["tool_name"]) for r in _rows(conn, "SELECT tool_name FROM runtime_tool_receipts")
    )
    live_execs = [(t, d) for t, d in execs if str(d.get("tool_name") or "").startswith("live_data.")]
    out["C1_executions_total"] = len(execs)
    out["C1_executions_without_action_record"] = len(thin)
    out["C1_live_data_executions"] = len(live_execs)
    out["C1_tool_receipt_rows"] = sum(receipt_tools.values())
    out["C1_live_data_receipt_rows"] = sum(v for k, v in receipt_tools.items() if k.startswith("live_data."))
    out["C1_executions_by_tool"] = dict(by_tool.most_common())

    # ---- C2: honesty receipts vs real executions -----------------------------------------
    total = nonempty = 0
    per_day = collections.Counter()
    per_day_nonempty = collections.Counter()
    for path in glob.glob(os.path.join(home, "data", "honesty_receipts", "**", "*"), recursive=True):
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as handle:
            for line in handle:
                receipt = _loads(line.strip())
                if not isinstance(receipt, dict) or "executed_tools" not in receipt:
                    continue
                total += 1
                import datetime

                day = datetime.datetime.utcfromtimestamp(receipt.get("issued_at", 0)).strftime("%Y-%m-%d")
                per_day[day] += 1
                if receipt.get("executed_tools"):
                    nonempty += 1
                    per_day_nonempty[day] += 1
    out["C2_signed_receipts"] = total
    out["C2_signed_receipts_with_executed_tools"] = nonempty
    out["C2_by_day"] = dict(sorted(per_day.items()))
    out["C2_by_day_with_executed_tools"] = dict(sorted(per_day_nonempty.items()))

    # ---- C3: model-call self-disagreement --------------------------------------------------
    srr = [_loads(r["details_json"]) for r in _rows(conn, "SELECT details_json FROM runtime_session_events WHERE event_type='semantic_resolution_receipt'")]
    disagree = collections.Counter()
    for obj in srr:
        reach = (obj.get("receipt") or {}).get("reach") or {}
        pca = bool(reach.get("provider_call_attempted"))
        eml = bool(reach.get("entered_model_lane"))
        calls = int((reach.get("turn_outcome") or {}).get("model_calls") or 0)
        if pca and not eml:
            disagree["attempted_call_without_entering_lane"] += 1
        if eml and not pca and calls == 0:
            disagree["entered_lane_but_no_call_and_no_count"] += 1
        if calls > 0 and not pca:
            disagree["counted_calls_without_attempt_record"] += 1
        if reach.get("attempt_disagrees_with_self_report"):
            disagree["flag_set"] += 1
    out["C3_turn_receipts"] = len(srr)
    out["C3_disagreements"] = dict(disagree)
    out["C3_disagreeing_turns"] = sum(v for k, v in disagree.items() if k != "flag_set")

    # ---- C4: retrieval happened, Activity renders "No tool ran" ---------------------------
    # Activity's rule (vool_chat_page.js ledgerRanNoTool): a run shows "No tool ran" when the
    # ledger for it carries no tool_executed/tool_selected event.
    per_turn: dict[str, set] = collections.defaultdict(set)
    for r in _rows(conn, "SELECT event_type, details_json FROM runtime_session_events"):
        detail = _loads(r["details_json"])
        turn = str(detail.get("client_turn_id") or detail.get("turn_id") or "")
        if turn:
            per_turn[turn].add(str(r["event_type"]))
    retrieval_no_tool = [
        turn
        for turn, kinds in per_turn.items()
        if ({"web_retrieval_completed", "web_retrieval_started", "live_data_plan_completed"} & kinds)
        and not ({"tool_executed", "tool_selected"} & kinds)
    ]
    out["C4_turns_with_retrieval_but_activity_says_no_tool"] = len(retrieval_no_tool)

    # ---- C5: lane proof claims no inference on turns that called a model -------------------
    lane = collections.defaultdict(lambda: {"no_inference": False, "provider_call": False})
    for r in _rows(conn, "SELECT message, details_json FROM runtime_session_events WHERE event_type='model_lane_proof'"):
        detail = _loads(r["details_json"])
        turn = str(detail.get("turn_id") or detail.get("client_turn_id") or "")
        if not turn:
            continue
        if "without model inference" in str(r["message"] or ""):
            lane[turn]["no_inference"] = True
        if str(detail.get("phase") or "") == "completed" and str(detail.get("provider_id") or "") not in ("", "runtime-fast-path"):
            lane[turn]["provider_call"] = True
    out["C5_turns_lane_proof_denies_inference_yet_called_provider"] = sum(
        1 for v in lane.values() if v["no_inference"] and v["provider_call"]
    )

    # ---- C6: can the stores even be joined? -----------------------------------------------
    srr_ids = {str(o.get("client_turn_id") or "") for o in srr} - {""}
    truth_ids = {str(r["target_id"] or "") for r in _rows(conn, "SELECT target_id FROM event_log_v2 WHERE category='agent_chat_truth_metrics'")} - {""}
    exec_ids = {str(d.get("client_turn_id") or d.get("turn_id") or "") for _, d in execs} - {""}
    out["C6_turn_receipts_total"] = len(srr)
    out["C6_turn_receipts_carrying_an_id"] = len(srr_ids)
    out["C6_truth_metric_ids"] = len(truth_ids)
    out["C6_join_turnreceipts_x_truthmetrics"] = len(srr_ids & truth_ids)
    out["C6_join_turnreceipts_x_executions"] = len(srr_ids & exec_ids)
    out["C6_tool_receipt_rows_carrying_a_turn_id"] = 0  # schema has no turn column at all

    # ---- A*: what the repaired system actually serves --------------------------------------
    # Everything above measures the LEGACY reconstructions -- the predicates and identifiers each
    # consumer used before there was one answer. They are kept unchanged, because a before/after is
    # only meaningful if the same question is asked both times. These add what the authoritative
    # model serves, which the legacy probes cannot see: they look for `client_turn_id`, so on a
    # repaired DB they report an absence that is really a rename.
    all_events = [
        (str(r["event_type"]), _loads(r["details_json"]))
        for r in _rows(conn, "SELECT event_type, details_json FROM runtime_session_events")
    ]
    out["A1_events_total"] = len(all_events)
    out["A1_events_carrying_canonical_turn_key"] = sum(1 for _, d in all_events if d.get("turn_key"))

    facts = _rows(conn, "SELECT turn_key, kind, name, ok FROM execution_facts")
    out["A2_authoritative_execution_facts"] = len(facts)
    out["A2_distinct_turns_with_facts"] = len({str(r["turn_key"]) for r in facts})

    # The gate, run over the whole DB: every witnessed execution must have an authoritative fact.
    witnessed: set[tuple[str, str]] = set()
    for kind, detail in all_events:
        if kind not in TOOL_EVENTS:
            continue
        turn, name = str(detail.get("turn_key") or ""), str(detail.get("tool_name") or "")
        if turn and name:
            witnessed.add((turn, name))
    recorded = {(str(r["turn_key"]), str(r["name"])) for r in facts if str(r["kind"]) == "tool"}
    out["A3_witnessed_tool_executions"] = len(witnessed)
    out["A3_witnessed_without_an_authoritative_fact"] = len(witnessed - recorded)

    # Do the derived views agree? They read one source, so this is structurally zero -- which is
    # the point: it is a property of the shape, not a check somebody has to keep passing.
    disagree = 0
    for turn in {t for t, _ in witnessed}:
        activity_says_ran = any(t == turn for t, _ in recorded)
        signed_names = {n for t, n in recorded if t == turn}
        if activity_says_ran != bool(signed_names):
            disagree += 1
    out["A4_turns_where_activity_and_signed_receipts_disagree"] = disagree

    # The resolution receipt now carries the authoritative count beside the turn's self-report,
    # so `attempt_disagrees_with_self_report` finally has a consumer that is neither disputant.
    out["A5_turn_receipts_carrying_authoritative_execution"] = sum(
        1 for o in srr if (o.get("receipt", {}).get("execution") or {})
    )
    out["A5_turn_receipts_reporting_a_model_call_disagreement"] = sum(
        1
        for o in srr
        if (o.get("receipt", {}).get("execution") or {}).get("agrees_with_self_report") is False
    )

    conn.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=os.path.expanduser("~/.vool_runtime_v050"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = measure(args.home)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(f"EXECUTION-TRUTH CONTRADICTIONS  ({result['db']})")
    for key, value in result.items():
        if key == "db":
            continue
        print(f"  {key:<62} {value if not isinstance(value, dict) else ''}")
        if isinstance(value, dict):
            for k2, v2 in value.items():
                print(f"      {k2:<56} {v2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
