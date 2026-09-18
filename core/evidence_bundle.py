from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.context_manifest import ContextManifest, build_context_manifest


@dataclass(frozen=True)
class EvidenceBundle:
    manifest: ContextManifest
    evidence_items: list[Any]


def build_evidence_bundle(
    *,
    task_id: str,
    trace_id: str,
    summary: str,
    abstract_inputs: list[str],
    constraints: list[str],
    environment_tags: dict[str, str],
) -> EvidenceBundle:
    evidence_items: list[Any] = [summary, *list(abstract_inputs), *list(constraints), environment_tags]
    manifest = build_context_manifest(
        task_id=task_id,
        trace_id=trace_id,
        evidence_items=evidence_items,
        source_metadata=[
            {"kind": "summary", "count": 1},
            {"kind": "abstract_inputs", "count": len(abstract_inputs)},
            {"kind": "constraints", "count": len(constraints)},
            {"kind": "environment_tags", "count": len(environment_tags)},
        ],
        redaction_markers=["strict_task_capsule"],
        truncation_markers=["task_capsule_summary_1024", "task_capsule_inputs_6"],
    )
    return EvidenceBundle(manifest=manifest, evidence_items=evidence_items)
