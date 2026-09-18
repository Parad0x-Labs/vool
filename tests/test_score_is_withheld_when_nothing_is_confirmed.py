"""Live-testing round, 2026-08-05 (the round after `3c7fa621`, which made only a `state == PROVEN`
primary finding able to move `compute_report_score`'s numeric deduction): a run with ZERO proven
findings -- one Challenged-but-unexecuted primary, and several Unreviewed additional findings --
still rendered a bare, confident-looking `**Score: 10.0/10**` sitting directly above a table of
real, plausible, unverified High-severity candidates. The operator's own words: '10.0/10 next to
two real, unverified High-severity candidates is still a strange thing to see ... trading false
alarm for false reassurance, just with an asterisk instead of none.'

`compute_report_score`'s numeric logic is NOT touched by this fix -- it is correct and already
adversarially verified (see `test_only_proven_findings_move_the_score.py` and
`test_an_audit_reports_a_computed_score.py`). What changes is `_score_lines` (audit_verdict.py):
whether a NUMBER is the right thing to print at all, given what else is in the report.

- Zero confirmed (`PROVEN`) findings but a real, unresolved candidate present anywhere (an unproven
  `CANDIDATE_UNPROVEN` primary, an `additional_findings` row, or a `challenged_out` item whose own
  challenge never ran) withholds the number and states four counts explicitly: confirmed, likely,
  unreviewed, rejected -- none silently folded into a single misleading digit.
- A real `PROVEN` primary still earns a real number, rendered exactly as before this fix.
- Genuinely nothing in any tier -- the one case where a bare ceiling is honest, because there is
  nothing left below it to contradict it -- still renders a real `10.0/10`.
- `BLOCKED` no longer silently omits the score section; it says explicitly that the score is
  withheld because the audit never reached a verdict.
"""
from __future__ import annotations

from core.agent_runtime.audit_verdict import (
    BLOCKED,
    CANDIDATE_UNPROVEN,
    NO_FINDING,
    PROVEN,
    AuditVerdict,
    VerdictFinding,
    VerdictProof,
    _score_lines,
    compute_report_score,
    render_audit_report,
)

_SCORE_CEILING = 10.0


def _finding(title: str, *, harm_class: str = "integrity") -> VerdictFinding:
    return VerdictFinding(
        title=title,
        file="api/x.py",
        line_start=10,
        line_end=12,
        cited_line_text="pass",
        failure_scenario="A concrete input reaches the wrong outcome.",
        harm_class=harm_class,
    )


def _has_numeric_score_line(report: str) -> bool:
    return any(line.startswith("**Score:") for line in report.splitlines())


# --------------------------------------------------------------------------------------
# 1. Challenged-but-unexecuted primary + 3 Unreviewed additional findings -> WITHHELD, not 10.0.
#    This is the exact live-incident shape this round fixes.
# --------------------------------------------------------------------------------------


def test_challenged_primary_plus_unreviewed_additional_findings_withholds_the_score() -> None:
    verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=[
            _finding("Survey row A", harm_class="crash"),
            _finding("Survey row B", harm_class="integrity"),
            _finding("Survey row C", harm_class="crash"),
        ],
    )

    # The number this fix does NOT touch: still the ceiling internally (nothing proven deducts).
    assert compute_report_score(verdict) == _SCORE_CEILING

    report = render_audit_report(verdict)

    # SCALPEL fix, failure class B, 2026-08-06: `CANDIDATE_UNPROVEN` now renders the SAME concise
    # NOT-PROVEN contract as `NO_FINDING` (Challenged/Confirmed/Rejected/Unreviewed), not its own
    # WITHHELD/Likely framing — the score-withholding behavior this test exists to pin is unchanged,
    # only the words describing it.
    assert "NOT PROVEN" in report, report
    assert not _has_numeric_score_line(report), (
        "a real, unresolved candidate is present and nothing is proven -- no numeric Score line "
        "may render:\n" + report
    )
    assert "- Challenged: 0" in report, report
    assert "- Confirmed: 0" in report, report
    assert "- Rejected: 0" in report, report
    assert "- Unreviewed: 3" in report, report


# --------------------------------------------------------------------------------------
# 2. A genuine PROVEN primary -> the existing numeric rendering, unchanged. No regression.
# --------------------------------------------------------------------------------------


def test_proven_primary_still_renders_the_existing_numeric_score_line() -> None:
    verdict = AuditVerdict(
        state=PROVEN,
        finding=_finding("Malformed record silently truncates the output", harm_class="integrity"),
        proof=VerdictProof(attempted=True, test_command="pytest t.py", returncode=1),
        model_label="m",
    )

    score = compute_report_score(verdict)
    assert score == 6.0

    report = render_audit_report(verdict)

    assert "WITHHELD" not in report, (
        "a genuinely proven defect earns a real number -- it must never render as withheld:\n"
        + report
    )
    score_line = next(line for line in report.splitlines() if line.startswith("**Score:"))
    assert score_line.startswith("**Score: 6.0/10**"), score_line
    assert "1 proven defect" in score_line, score_line


# --------------------------------------------------------------------------------------
# 3. A genuinely empty NO_FINDING verdict -> still a real 10.0/10. The one legitimate bare ceiling.
# --------------------------------------------------------------------------------------


def test_genuinely_empty_no_finding_verdict_still_scores_a_real_ceiling() -> None:
    verdict = AuditVerdict(state=NO_FINDING)

    assert verdict.finding is None
    assert verdict.additional_findings == []
    assert verdict.challenged_out == []
    assert verdict.strengths == []
    assert verdict.refuted == []

    score = compute_report_score(verdict)
    assert score == _SCORE_CEILING

    report = render_audit_report(verdict)

    assert "WITHHELD" not in report, (
        "nothing at all was found in any tier -- a bare ceiling is honest here and must render:\n"
        + report
    )
    assert "**Score: 10.0/10**" in report, report


# --------------------------------------------------------------------------------------
# 4. NO_FINDING with only challenged_out items (a mix of refuted and unreviewed), no
#    additional_findings -- the exact live-incident shape from two rounds ago
#    (test_ruled_out_reflects_what_actually_happened.py). Must also withhold, and the counts must
#    correctly attribute refuted vs. unreviewed challenged_out items.
# --------------------------------------------------------------------------------------


def test_no_finding_with_only_challenged_out_items_withholds_and_attributes_counts_correctly() -> None:
    """2026-08-06: `NO_FINDING` now renders the plain NOT-PROVEN counter block (Challenged /
    Confirmed / Rejected / Unreviewed) rather than the WITHHELD Confirmed-findings/Likely-findings
    framing below -- see `_score_lines`'s own comment on why `NO_FINDING` gets its own shape.
    `CANDIDATE_UNPROVEN` (the next test down) keeps the original WITHHELD framing, since it has an
    actual live candidate to call "Likely".

    Also updated to set `attempted` explicitly and correctly per candidate, now that
    `FindingChallenge.attempted` exists: claim A's reason ("the audit's bounded call budget was
    spent") is the shape of a call the budget refused BEFORE it ever reached the model --
    `attempted=False`. Claim B's reason ("the challenge reply could not be parsed") is the shape of
    a call that DID reach the model and got text back, just not valid JSON -- a real, if wasted,
    attempt (`attempted=True`, the default `_challenge_finding` itself uses for that exact case)."""
    verdict = AuditVerdict(
        state=NO_FINDING,
        additional_findings=[],
        challenged_out=[
            {
                "title": "Genuinely disproved claim",
                "verdict": "refuted",
                "reason": "the runtime executed the operation and observed the opposite",
            },
            {
                "title": "Never actually checked claim A",
                "verdict": "uncertain",
                "reason": "the audit's bounded call budget was spent",
                "attempted": False,
            },
            {
                "title": "Challenged but unparseable claim B",
                "verdict": "uncertain",
                "reason": "the challenge reply could not be parsed",
                "attempted": True,
            },
        ],
    )

    report = render_audit_report(verdict)

    assert "**NOT PROVEN**" in report, report
    assert not _has_numeric_score_line(report), (
        "an unreviewed challenged_out item is a live, unresolved candidate -- no numeric Score "
        "line may render:\n" + report
    )
    assert "- Confirmed: 0" in report, report
    # Claim A never had a real check run (`attempted=False`) -> unreviewed. Claim B DID have a
    # real call attempted, just unparseable -> counts as challenged (attempted, unresolved), not
    # unreviewed -- the exact distinction `_challenge_never_attempted`/`_challenge_attempted_but_
    # unresolved` exist to make structured.
    assert "- Unreviewed: 1" in report, report
    # Claim B (attempted, unresolved) + the refuted claim (attempted, rejected) = 2 challenged.
    assert "- Challenged: 2" in report, report
    # One challenged_out item genuinely ran and found a contradiction ("refuted") -> rejected.
    assert "- Rejected: 1" in report, report


def test_no_finding_with_only_a_refuted_challenge_is_not_a_live_candidate_and_still_scores_a_ceiling() -> None:
    """Control for the case above: a `challenged_out` item that genuinely ran and was refuted is a
    resolved, executed disproof -- not a lingering unresolved candidate -- so it must not withhold
    the number, consistent with `_status_word` treating it as a clean pass too."""
    verdict = AuditVerdict(
        state=NO_FINDING,
        challenged_out=[
            {
                "title": "Genuinely disproved claim",
                "verdict": "refuted",
                "reason": "the runtime executed the operation and observed the opposite",
            }
        ],
    )

    report = render_audit_report(verdict)

    assert "WITHHELD" not in report, (
        "a fully refuted (executed) challenge is resolved, not a live candidate -- it must not "
        "withhold the score:\n" + report
    )
    assert "**Score: 10.0/10**" in report, report


# --------------------------------------------------------------------------------------
# 5. BLOCKED -> explicit withheld/incomplete statement, not silence.
# --------------------------------------------------------------------------------------


def test_blocked_verdict_states_the_score_is_withheld_rather_than_omitting_the_section() -> None:
    verdict = AuditVerdict(state=BLOCKED, blocked_reason="the audit evidence carried no readable source")

    assert compute_report_score(verdict) is None

    report = render_audit_report(verdict)

    assert not _has_numeric_score_line(report), "BLOCKED must never render a numeric score line"
    assert "WITHHELD" in report, (
        "BLOCKED must say explicitly that the score is withheld, not silently omit the section:\n"
        + report
    )
    assert "Incomplete" in report, (
        "BLOCKED's withheld line should match _status_word's own 'Incomplete' framing:\n" + report
    )


# --------------------------------------------------------------------------------------
# Sabotage discipline (CLAUDE.md section 6): reverting the rendering fix must fail the tests above
# by naming the actual wrong output (a numeric score where WITHHELD was expected).
# --------------------------------------------------------------------------------------


def test_sabotage_the_old_always_numeric_rendering_reproduces_the_live_incidents_bare_ceiling() -> None:
    """Restores the exact pre-fix `_score_lines` body (always prints a number, including for
    BLOCKED-as-empty-list) and proves it renders the false-reassurance shape this round fixes --
    then proves the real, fixed `_score_lines` differs from it on the exact same input."""
    from core.agent_runtime.audit_verdict import (
        BLOCKED as _BLOCKED,
    )
    from core.agent_runtime.audit_verdict import (
        PROVEN as _PROVEN,
    )
    from core.agent_runtime.audit_verdict import (
        _has_unreviewed_challenge,
    )

    def _sabotaged_score_lines(verdict: AuditVerdict) -> list[str]:
        score = compute_report_score(verdict)
        if score is None:
            return []
        confirmed_defect_count = 1 if verdict.finding is not None and verdict.state == _PROVEN else 0
        uncounted_candidate_count = (
            1 if verdict.finding is not None and verdict.state == CANDIDATE_UNPROVEN else 0
        ) + len(verdict.additional_findings)
        parts = []
        if confirmed_defect_count:
            parts.append(f"{confirmed_defect_count} proven defect(s) weighed")
        if verdict.strengths:
            parts.append(f"{len(verdict.strengths)} strength(s) credited")
        if verdict.refuted:
            parts.append(f"{len(verdict.refuted)} disproved candidate(s) credited")
        if parts:
            detail = ", ".join(parts)
        elif _has_unreviewed_challenge(verdict):
            detail = "an unreviewed candidate below was never checked, so it counts toward neither side of the score"
        else:
            detail = "nothing counted against or for it this pass"
        if uncounted_candidate_count:
            detail += f"; {uncounted_candidate_count} unproven candidate(s) below are not counted either way"
        return [f"**Score: {score:.1f}/10** — runtime gut-check from {detail}.", ""]

    live_incident_verdict = AuditVerdict(
        state=CANDIDATE_UNPROVEN,
        finding=_finding("Unproven primary", harm_class="integrity"),
        additional_findings=[
            _finding("Survey row A", harm_class="crash"),
            _finding("Survey row B", harm_class="integrity"),
            _finding("Survey row C", harm_class="crash"),
        ],
    )
    blocked_verdict = AuditVerdict(state=_BLOCKED, blocked_reason="no readable source")

    sabotaged_lines = _sabotaged_score_lines(live_incident_verdict)
    sabotaged_text = "\n".join(sabotaged_lines)
    assert sabotaged_text.startswith(f"**Score: {_SCORE_CEILING:.1f}/10**"), (
        "sabotage setup failed to reproduce the live incident -- expected the old code to print a "
        "bare confident ceiling above unverified candidates, got: " + sabotaged_text
    )
    assert "WITHHELD" not in sabotaged_text

    real_lines = _score_lines(live_incident_verdict)
    real_text = "\n".join(real_lines)
    # SCALPEL fix, 2026-08-06: CANDIDATE_UNPROVEN now renders "NOT PROVEN" (the same concise
    # contract as NO_FINDING), not "WITHHELD" — the point of this sabotage (the real code must
    # differ from a bare numeric ceiling) is unaffected by which withholding framing is used.
    assert "NOT PROVEN" in real_text, (
        "the real, fixed _score_lines must differ from the sabotaged pre-fix version on the exact "
        "live-incident shape: " + real_text
    )
    assert real_text != sabotaged_text

    sabotaged_blocked = _sabotaged_score_lines(blocked_verdict)
    assert sabotaged_blocked == [], "sabotage setup failed to reproduce BLOCKED's old silent []"
    real_blocked = _score_lines(blocked_verdict)
    assert real_blocked != [], (
        "the real, fixed _score_lines must no longer silently omit the score section for BLOCKED"
    )
    assert "WITHHELD" in "\n".join(real_blocked)
