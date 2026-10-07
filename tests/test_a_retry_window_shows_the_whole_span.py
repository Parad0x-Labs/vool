"""The source window shown to the model on a retry covers the whole span, however near the top it sits.

From the live agent-team comparison, 2026-10-07: a citation near the top of a 35-line file showed lines 1-21
only, so the model was told its source was "truncated at line 21" and nominated a weaker bug.
"""
from __future__ import annotations

from pathlib import Path

from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.stepped_audit import _source_window

FIXTURE = Path(__file__).with_name("fixtures") / "multi_file_review"


def _evidence() -> AuditEvidence:
    sources = {p.name: p.read_text() for p in FIXTURE.glob("*.py")}
    return AuditEvidence(inspected_paths=tuple(sources), all_paths=tuple(sources), sources=sources,
                         workspace_root=str(FIXTURE), scoped_target="orders.py")


def test_a_citation_near_the_top_of_a_short_file_shows_the_whole_file():
    window = _source_window(_evidence(), "orders.py", 1, 1)
    numbers = [int(line.split(":", 1)[0]) for line in window.splitlines()]
    assert numbers[0] == 1 and numbers[-1] == _evidence().line_count("orders.py")
