"""An audit that read 193 of 493 files and says neither number is claiming to have read them all.

Measured live 2026-08-03 against a 333-crate Rust workspace:

    source files present : 493
    files the audit read : 193
    findings reported    : 1
    coverage stated      : nothing

The operator asked "can you run check audit on this and report if all is sound", got one unproven
candidate, and replied "why? i asked you to audit all project? so why not?" — then re-ran the same
audit three more times, each returning the identical finding, because nothing in the reply told
them what had and had not been looked at.

The bound itself is fine: a whole-project pass samples. CLAUDE.md's rule is that a bounded pass
must SAY what it bounded — silent truncation reads as "covered everything" when it did not. Same
rule as the second filename, the second price, the deferred tools and the starved observations.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime.audit_verdict import (
    CANDIDATE_UNPROVEN,
    NO_FINDING,
    AuditVerdict,
    VerdictFinding,
    render_audit_report,
)
from core.agent_runtime.stepped_audit import audit_coverage_note


def _evidence(read: int, available: int, incomplete: int = 0):
    return SimpleNamespace(
        sources={f"crates/c{i}/src/lib.rs": "code" for i in range(read)},
        all_paths=tuple(f"crates/c{i}/src/lib.rs" for i in range(available)),
        incomplete_files=tuple(f"crates/c{i}/src/lib.rs" for i in range(incomplete)),
    )


# --------------------------------------------------------------------------------------
# The sentence.
# --------------------------------------------------------------------------------------


def test_the_measured_run_states_both_numbers() -> None:
    note = audit_coverage_note(_evidence(read=193, available=493, incomplete=12))

    assert "193" in note and "493" in note
    assert "12 of them only in part" in note


def test_a_single_file_audit_does_not_pretend_to_be_a_survey() -> None:
    """`review pdf_rebuild.py` opened one file on purpose. "1 of 1" would read as a shortfall."""

    note = audit_coverage_note(_evidence(read=1, available=1))

    assert note == "Opened 1 source file in this pass."


def test_partial_reads_are_distinguished_from_unread_files() -> None:
    """Read is not read-to-the-end.

    A citation past the end of a truncated file is a gap in OUR evidence, not a fabrication by the
    model, and the two must not be reported as the same thing.
    """

    assert "only in part" in audit_coverage_note(_evidence(read=10, available=10, incomplete=3))
    assert "only in part" not in audit_coverage_note(_evidence(read=10, available=10))


def test_an_audit_that_opened_nothing_says_nothing() -> None:
    """A coverage line on an audit with no evidence would be noise, and slightly dishonest."""

    assert audit_coverage_note(_evidence(read=0, available=40)) == ""


# --------------------------------------------------------------------------------------
# The wiring. The sentence is worthless if the report never carries it.
# --------------------------------------------------------------------------------------


def _finding() -> VerdictFinding:
    return VerdictFinding(
        title="select_coolest ignores heat_score",
        file="crates/account-fee-heatmap/src/lib.rs",
        line_start=62, line_end=74,
        cited_line_text="if pid > len(pat_list): break",
        failure_scenario="A corrupt id silently truncates the output.",
    )


def test_the_report_carries_the_coverage_line() -> None:
    report = render_audit_report(
        AuditVerdict(
            state=CANDIDATE_UNPROVEN,
            finding=_finding(),
            target_path="crates/account-fee-heatmap/src/lib.rs",
            coverage_note=audit_coverage_note(_evidence(read=193, available=493)),
        )
    )

    assert "193 of 493" in report


def test_a_no_finding_report_carries_it_too() -> None:
    """This is where it matters MOST.

    "No verified bug found" after reading 39% of a project is a very different statement from the
    same sentence after reading all of it, and the operator cannot tell them apart without this.
    """

    report = render_audit_report(
        AuditVerdict(
            state=NO_FINDING,
            target_path="crates/",
            coverage_note=audit_coverage_note(_evidence(read=193, available=493)),
        )
    )

    assert "193 of 493" in report


def test_no_coverage_line_when_there_is_nothing_to_say() -> None:
    report = render_audit_report(
        AuditVerdict(state=CANDIDATE_UNPROVEN, finding=_finding(), target_path="x.py")
    )

    assert "Opened" not in report


@pytest.mark.parametrize("state", [CANDIDATE_UNPROVEN, NO_FINDING])
def test_the_line_survives_every_state_the_operator_sees(state: str) -> None:
    report = render_audit_report(
        AuditVerdict(
            state=state,
            finding=_finding() if state == CANDIDATE_UNPROVEN else None,
            target_path="x.py",
            coverage_note="Opened 5 of 40 source files in this pass.",
        )
    )

    assert "5 of 40" in report


# --------------------------------------------------------------------------------------
# Through the real audit. Every test above constructs an AuditVerdict by hand, so removing
# `coverage_note=audit_coverage_note(evidence)` from the runtime left all of them GREEN.
# --------------------------------------------------------------------------------------


def test_the_real_audit_populates_the_coverage_note() -> None:
    """Drives `run_stepped_audit` and asserts on the report it produces.

    This is the assertion the hand-built verdicts cannot make. Sabotaging the wiring - replacing
    `coverage_note=audit_coverage_note(evidence)` with `coverage_note=""` - passed all nine tests
    above; only this one fails.
    """

    from tests import test_an_audit_obeys_the_permission_it_was_given as harness

    decision, _router, _tools = harness._drive(
        [harness.TRUNCATION_FINDING, harness.TRUNCATION_SUPPORTED], session_id="audit-coverage"
    )
    report = harness._report(decision)

    assert "Opened" in report and "source file" in report, (
        "the audit produced a report that never says how much of the project it opened"
    )
