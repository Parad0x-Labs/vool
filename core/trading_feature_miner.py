from __future__ import annotations

from typing import Any


def mine_exported_trading_features(research_packet: dict[str, Any]) -> dict[str, Any]:
    trading = dict(research_packet.get("trading_feature_export") or {})
    if not trading:
        return {"feature_rows": [], "heuristic_candidates": [], "script_ideas": []}

    heuristic_candidates: list[dict[str, Any]] = []
    for edge in list(trading.get("hidden_edges") or [])[:6]:
        metric = str(edge.get("metric") or edge.get("id") or "").strip()
        support = int(edge.get("support") or 0)
        score = float(edge.get("score") or 0.0)
        if not metric:
            continue
        heuristic_candidates.append(
            {
                "candidate_kind": "heuristic",
                "candidate_id": f"heuristic::{metric}",
                "label": f"Watch {metric} as an early filter signal",
                "rule_text": f"Investigate {metric} as a leading filter or ranking feature before entry.",
                "support": support,
                "score": score,
                "source_kind": "trading_hidden_edges",
                "evaluation": {
                    "support": support,
                    "score": score,
                    "backtest": None,
                },
            }
        )

    for item in list(trading.get("flow_reason_counts") or [])[:4]:
        reason = str(item.get("reason") or "").strip()
        count = int(item.get("count") or 0)
        if not reason:
            continue
        heuristic_candidates.append(
            {
                "candidate_kind": "heuristic",
                "candidate_id": f"flow::{reason.lower()}",
                "label": f"Audit flow reason: {reason}",
                "rule_text": f"Review whether tokens rejected for {reason} are causing false negatives or missed mooners.",
                "support": count,
                "score": min(0.75, 0.35 + (0.04 * count)),
                "source_kind": "trading_live_flow",
                "evaluation": {
                    "support": count,
                    "score": min(0.75, 0.35 + (0.04 * count)),
                    "backtest": None,
                },
            }
        )

    script_ideas = []
    for candidate in heuristic_candidates[:3]:
        label = str(candidate.get("label") or "").strip()
        if not label:
            continue
        script_ideas.append(
            {
                "candidate_kind": "script",
                "candidate_id": str(candidate.get("candidate_id") or "") + "::script",
                "label": f"Prototype script for {label}",
                "rule_text": f"Build a bounded analysis script that exports and scores the feature behind: {label}.",
                "support": int(candidate.get("support") or 0),
                "score": float(candidate.get("score") or 0.0),
                "source_kind": str(candidate.get("source_kind") or "trading_feature"),
                "evaluation": {
                    "backtest": None,
                },
            }
        )

    feature_rows = []
    for edge in list(trading.get("hidden_edges") or [])[:10]:
        feature_rows.append(
            {
                "feature_name": str(edge.get("metric") or edge.get("id") or "").strip(),
                "feature_kind": "hidden_edge",
                "score": float(edge.get("score") or 0.0),
                "support": int(edge.get("support") or 0),
            }
        )
    for item in list(trading.get("flow_reason_counts") or [])[:10]:
        feature_rows.append(
            {
                "feature_name": str(item.get("reason") or "").strip(),
                "feature_kind": "flow_reason",
                "score": min(1.0, float(item.get("count") or 0) / 10.0),
                "support": int(item.get("count") or 0),
            }
        )

    return {
        "feature_rows": feature_rows,
        "heuristic_candidates": heuristic_candidates,
        "script_ideas": script_ideas,
    }
