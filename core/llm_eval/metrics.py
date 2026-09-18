from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

_HONESTY_MARKERS = (
    "can't verify",
    "cannot verify",
    "insufficient evidence",
    "not enough confidence",
    "uncertain",
    "i don't know",
    "i do not know",
    "web lookup is disabled",
)


def percentile(values: list[float], pct: float) -> float | None:
    samples = sorted(float(value) for value in values if value is not None)
    if not samples:
        return None
    if len(samples) == 1:
        return round(samples[0], 3)
    rank = (max(0.0, min(100.0, float(pct))) / 100.0) * (len(samples) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(samples[lower], 3)
    fraction = rank - lower
    interpolated = samples[lower] + (samples[upper] - samples[lower]) * fraction
    return round(interpolated, 3)


def summarize_latency_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in list(rows or []):
        request_type = str(row.get("request_type") or "unknown")
        latency = row.get("latency_seconds")
        if latency is None:
            continue
        grouped[request_type].append(float(latency))

    by_type: dict[str, Any] = {}
    all_values: list[float] = []
    for request_type, values in sorted(grouped.items()):
        all_values.extend(values)
        by_type[request_type] = {
            "samples": len(values),
            "p50": percentile(values, 50),
            "p95": percentile(values, 95),
            "p99": percentile(values, 99),
            "max": round(max(values), 3),
        }

    return {
        "samples": len(all_values),
        "overall": {
            "p50": percentile(all_values, 50),
            "p95": percentile(all_values, 95),
            "p99": percentile(all_values, 99),
            "max": round(max(all_values), 3) if all_values else None,
        },
        "by_request_type": by_type,
    }


def score_context_scenario(
    *,
    scenario_id: str,
    response_text: str,
    expected_terms: list[str],
    forbidden_terms: list[str],
    latency_seconds: float | None = None,
) -> dict[str, Any]:
    lowered = str(response_text or "").lower()
    matched_expected = [term for term in expected_terms if term.lower() in lowered]
    stale_hits = [term for term in forbidden_terms if term.lower() in lowered]
    expected_ratio = (len(matched_expected) / len(expected_terms)) if expected_terms else 1.0
    penalty = 0.5 if stale_hits else 0.0
    score = max(0.0, round(expected_ratio - penalty, 3))
    if stale_hits:
        status = "contaminated"
    elif expected_ratio >= 1.0:
        status = "correct"
    elif expected_ratio > 0.0:
        status = "partial"
    else:
        status = "failed"
    return {
        "scenario_id": scenario_id,
        "status": status,
        "score": score,
        "latency_seconds": latency_seconds,
        "matched_expected": matched_expected,
        "stale_hits": stale_hits,
        "response_excerpt": str(response_text or "")[:500],
    }


def score_research_response(
    *,
    scenario_id: str,
    response_text: str,
    expected_sources: list[str],
    must_refuse: bool = False,
    forbidden_terms: list[str] | None = None,
) -> dict[str, Any]:
    text = str(response_text or "")
    lowered = text.lower()
    source_hits = [source for source in expected_sources if source.lower() in lowered]
    urls = re.findall(r"https?://[^\s)>\]]+", text)
    stale_hits = [term for term in list(forbidden_terms or []) if term.lower() in lowered]
    honesty = any(marker in lowered for marker in _HONESTY_MARKERS)
    evidence_coverage = round((len(source_hits) / len(expected_sources)) if expected_sources else 1.0, 3)
    citation_validity = 1.0 if (urls or source_hits or (must_refuse and honesty and not expected_sources)) else 0.0
    uncertainty_honesty = 1.0 if (honesty if must_refuse else True) else 0.0
    score = round(max(0.0, ((evidence_coverage + citation_validity + uncertainty_honesty) / 3.0) - (0.5 if stale_hits else 0.0)), 3)
    if stale_hits:
        status = "contaminated"
    elif must_refuse and not honesty:
        status = "failed"
    elif score >= 0.95:
        status = "correct"
    elif score >= 0.5:
        status = "partial"
    else:
        status = "failed"
    return {
        "scenario_id": scenario_id,
        "status": status,
        "score": score,
        "evidence_coverage": evidence_coverage,
        "citation_validity": citation_validity,
        "uncertainty_honesty": uncertainty_honesty,
        "matched_sources": source_hits,
        "urls": urls,
        "stale_hits": stale_hits,
        "response_excerpt": text[:600],
    }
