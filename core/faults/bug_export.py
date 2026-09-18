"""The bug-report-facing fault export: safe codes and evidence, nothing else.

A bug report should say WHICH typed failures a turn hit -- that is most of what a
reproducer needs -- and none of what the fault record internally kept to debug it.
This module is the narrow seam between the fault plane and the bug-report pipeline:

* it exports codes, severities, lifecycles, operator actions, evidence refs and ids;
* it exports NO ``context``, NO cause chains, NO user messages beyond the catalog's
  canned ones -- the reproducible what, never the internal where-with-what;
* the export FAILS CLOSED through the bug-report pipeline's own outbound scanner
  (:func:`core.bug_report.scanner.scan_text`): if the pipeline would refuse the text,
  this seam refuses first, loudly -- it never hands the pipeline something to catch.
"""
from __future__ import annotations

import json
from typing import Any

from core.faults.catalog import FAULT_SCHEMA
from core.faults.recorder import faults_for_turn


def fault_evidence_export(*, turn_key: str = "", session_id: str = "", limit: int = 20) -> dict[str, Any]:
    """The privacy-safe fault block for one turn, safe by construction and by scan."""
    turn = str(turn_key or "").strip()
    rows = faults_for_turn(turn, session_id=str(session_id or ""), limit=max(1, int(limit))) if turn else []
    export: dict[str, Any] = {
        "schema": FAULT_SCHEMA,
        "fault_count": len(rows),
        "faults": [
            {
                "fault_id": row.fault_id,
                "code": row.code,
                "schema_version": row.schema_version,
                "severity": row.severity,
                "retry": row.retry,
                "lifecycle": row.lifecycle,
                "operator_action": row.operator_action,
                "authority": row.authority,
                "evidence_refs": list(row.evidence_refs),
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }
    from core.bug_report.scanner import scan_text
    from core.bug_report.schema import UnsafeReportContentError

    findings = scan_text(json.dumps(export, sort_keys=True, ensure_ascii=False))
    if findings:
        raise UnsafeReportContentError(findings=[(finding.rule, finding.start) for finding in findings])
    return export


__all__ = ["fault_evidence_export"]
