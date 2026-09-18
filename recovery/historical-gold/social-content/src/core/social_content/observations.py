"""Truthful metrics ingestion, interpretation and campaign review (Checkpoint 7).

No scraper farm: observations arrive operator-entered, file-imported or
connector-observed, each labelled. Interpretation laws:

- missing data stays missing (no invented impressions/clicks/demographics);
- missing denominators are named;
- only compatible windows/definitions compare;
- views/likes/follows never establish causality;
- a hypothesis is supported/contradicted/inconclusive/expired — never
  "proven" from one post;
- no automatic operator-profile or PB01 promotion from raw engagement:
  reusable procedures stay with core.learning under operator review.
"""

from __future__ import annotations

from typing import Any

# metric-name normalization: definitions that are NOT comparable
_DEFINITION_FAMILIES = {
    "impressions": "exposure",
    "views": "exposure_views",          # views != impressions by definition
    "reach": "exposure_reach",
    "likes": "engagement",
    "reposts": "engagement",
    "replies": "engagement",
    "clicks": "action",
    "link_clicks": "action",
    "conversions": "outcome",
    "signups": "outcome",
    "revenue": "outcome",
}


def import_metric_batch(
    store, *, project_id: str, rows: list[dict[str, Any]],
    source: str = "file_import", provenance: str = "",
) -> dict[str, Any]:
    """Import a batch of metric rows. Malformed rows are refused by name and
    NOTHING from them is stored; good rows import labelled by source."""
    imported, refused = [], []
    for i, row in enumerate(rows):
        try:
            name = str(row.get("metric_name", "")).strip().lower()
            if not name:
                raise ValueError("metric_name is required")
            if not _DEFINITION_FAMILIES.get(name):
                # unknown metric names import as-is but never silently re-map
                pass
            value = row.get("value")
            float(value)
            doc = store.add_metric_observation(
                project_id=project_id, metric_name=name, value=float(value),
                unit=str(row.get("unit", "count")),
                content_ref=row.get("content_ref") or {},
                window_start=row.get("window_start"), window_end=row.get("window_end"),
                source=source, provenance=provenance or f"row {i}",
                denominator_known=bool(row.get("denominator_known", False)),
                raw_fields=row.get("raw_fields") or {},
            )
            imported.append(doc["observation_id"])
        except Exception as exc:
            refused.append({"row": i, "reason": str(exc)})
    return {"imported": imported, "refused": refused,
            "summary": f"{len(imported)} imported, {len(refused)} refused (labelled)"}


def compare_observations(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Compare two observations for compatibility. Views vs impressions,
    mismatched windows and missing denominators are named, never smoothed."""
    problems: list[str] = []
    fa = _DEFINITION_FAMILIES.get(str(a.get("metric_name", "")).lower(), "unknown")
    fb = _DEFINITION_FAMILIES.get(str(b.get("metric_name", "")).lower(), "unknown")
    if fa != fb:
        problems.append(
            f"definition mismatch: {a.get('metric_name')} ({fa}) vs "
            f"{b.get('metric_name')} ({fb}) — different definitions do not compare"
        )
    wa = (a.get("window_start"), a.get("window_end"))
    wb = (b.get("window_start"), b.get("window_end"))
    if wa != wb:
        problems.append(f"window mismatch: {wa} vs {wb}")
    comparable = not problems
    if comparable and not (a.get("denominator_known") and b.get("denominator_known")):
        problems.append("missing denominator: rates cannot be computed")
        comparable = False
    return {"comparable": comparable, "problems": problems}


def update_hypothesis(
    store, *, hypothesis_id: str, project_id: str,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Close a hypothesis from labelled observations. One post never proves
    anything; support needs compatible, denominator-known evidence. The
    hypothesis must belong to the given project (no cross-project writes)."""
    hyp = None
    for h in store.list_hypotheses(project_id, limit=200):
        if h["hypothesis_id"] == hypothesis_id:
            hyp = h
            break
    if hyp is None:
        return {"ok": False, "code": "unknown_hypothesis"}
    usable = [o for o in observations if o.get("source") in
              ("operator_entered", "file_import", "connector_observed")]
    if not usable:
        outcome, confidence, disposition = (
            "inconclusive", "low", "no labelled observations available"
        )
    else:
        supported = [
            o for o in usable
            if o.get("denominator_known") and _DEFINITION_FAMILIES.get(
                str(o.get("metric_name", "")).lower())
        ]
        if len(supported) >= 2:
            pairs_ok = all(
                compare_observations(supported[i], supported[i + 1])["comparable"]
                for i in range(len(supported) - 1)
            )
            if pairs_ok:
                outcome, confidence, disposition = (
                    "supported", "medium",
                    "multiple compatible denominator-known observations align; "
                    "correlation only — causality is NOT established"
                )
            else:
                outcome, confidence, disposition = (
                    "inconclusive", "low",
                    "observations disagree on definition or window"
                )
        else:
            outcome, confidence, disposition = (
                "inconclusive", "low",
                "fewer than two compatible denominator-known observations — "
                "one post never proves a hypothesis"
            )
    closed = store.close_hypothesis(
        hypothesis_id, outcome=outcome, disposition=disposition, confidence=confidence
    )
    return {"ok": True, "hypothesis": closed}


def campaign_review(store, *, project_id: str) -> dict[str, Any]:
    """Compact campaign review: what shipped/was handed off, measured
    observations, what cannot be known, hypothesis updates, next experiments."""
    drafts = store.list_drafts(project_id, limit=200)
    decisions = store.list_decisions(project_id, limit=200)
    metrics = store.list_metric_observations(project_id, limit=200)
    hypotheses = store.list_hypotheses(project_id, limit=200)
    shipped = [d for d in drafts if store.approval_for(
        draft_id=d["draft_id"], version=d["version"],
        content_hash=d["content_hash"], platform=d["platform"],
    )]
    no_posts = [d for d in decisions if d["decision"] == "NO_POST"]
    cannot_known: list[str] = [
        "impressions/clicks/conversions are only what was imported; nothing is "
        "inferred for content without observations",
    ]
    if any(not m.get("denominator_known") for m in metrics):
        cannot_known.append("some observations lack denominators — rates unknown")
    if any(m.get("source") == "operator_entered" for m in metrics):
        cannot_known.append("operator-entered metrics are self-reported labels")
    return {
        "what_was_approved": [
            {"draft_id": d["draft_id"], "version": d["version"],
             "platform": d["platform"]} for d in shipped
        ],
        "no_post_decisions": len(no_posts),
        "measured_observations": [
            {"metric": m["metric_name"], "value": m["value"], "source": m["source"],
             "denominator_known": m["denominator_known"]} for m in metrics
        ],
        "what_cannot_be_known": cannot_known,
        "hypothesis_updates": [
            {"id": h["hypothesis_id"], "outcome": h["outcome"],
             "confidence": h["confidence"]} for h in hypotheses
        ],
        "next_experiments": [
            "add denominator-bearing observations before comparing rates",
            "keep one variable per experiment; correlation is not causality",
        ][:2],
        "pb01_note": (
            "no procedure promotion happens automatically from engagement; "
            "reusable procedures go through core.learning under operator review"
        ),
    }
