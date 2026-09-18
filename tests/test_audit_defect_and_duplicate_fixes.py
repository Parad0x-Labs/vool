"""Two adversarial-review defects in the audit-coverage fix, each reproduced by execution.

Q2 -- batch-union dedup blind spot: three worded restatements of ONE underlying bug survived as
distinct findings because their lexical containment (0.26-0.36) never crossed the 0.6
`claim_signature`/`matches_any_claim` threshold. No lexical threshold tuning fixes this reliably
(tightening it risks merging genuinely distinct findings), so the fix is generation-time
prevention: `_nominate_finding` is told, in the same style already used for `refuted_titles` and
`screened_titles`, which titles this turn has ALREADY reported via a new `already_found_titles`
parameter -- and `run_stepped_audit` feeds it the running `survey_rows` titles on every subsequent
candidate/manifest attempt. The post-hoc lexical dedup in `continuity_gate` is untouched; it stays
as a backstop for a model that ignores the instruction.

Q4 -- self-declared harm_class bypass: a candidate could self-declare `harm_class="crash"` or
`"integrity"` and skip `_finding_defect`'s content check entirely, because the final rejection only
fired when `declared not in PROVABLE_HARM_CLASSES`. `compute_severity` maps that tier straight to
High, which `compute_report_score` weights at 4.0x -- so a trivial nit ("Variable name is unclear")
could buy the same score damage as a real crash, by self-labeling alone. The fix makes the regex
match (`provable`) the sole gate for the PROVABLE_HARM_CLASSES tier: a declared crash/integrity
that the text does not actually support is downgraded to the same undeclared-inference rule, and
`candidate["harm_class"]` is rewritten to the resolved (downgraded) value on every path.

Follow-up QA round (2026-08-04) found two more gaps in that same fix:

Q4-survey-leak -- `_survey_findings` called `_finding_defect(dict(row), ...)` on a THROWAWAY copy,
so the downgrade `_finding_defect` writes back (`candidate["harm_class"] = declared`) landed on the
copy and was discarded; the `VerdictFinding` below was then built from the ORIGINAL, un-downgraded
`row.get("harm_class")`. Every survey row (the majority of findings, and the population the
batch-union fix specifically grows) still leaked the unearned "crash"/"integrity" label straight
into the rendered report and the computed score. The fix reuses the SAME dict across the defect
check and the `VerdictFinding` construction.

already_found_titles-cap -- the prompt block added for Q2 sliced `already_found_titles[:10]`, an
unexamined defensive guess. Once a batch passed 10 distinct titles (measured live after a single
nomination attempt), the slice froze on the first 10 forever and later attempts never saw titles
found after that point -- defeating the do-not-repeat instruction in exactly the multi-attempt
scenario it exists for. The fix removes the cap; these are short, bounded strings with no
context-window pressure (CLAUDE.md 4b).

Round 5 QA found the round-4 fix for the next gap (below) covered only one of two consumption
paths, and its regression test never touched production code at all:

survey-row-length-unbounded -- the PRIMARY finding is bounded at construction
(`title=...strip()[:200]`, `failure_scenario=...strip()[:900]`), but every OTHER row in the same
nomination batch (`batch_rows` -> `survey_rows`) went through no equivalent bound. Round 4 patched
only the `already_found_titles` prompt-hint echo at ITS OWN call site; the same untruncated
`survey_rows` data still flowed into `_survey_findings` with zero length cap on `title` or
`failure_scenario`, so a pathological/hallucinated row landed verbatim in the operator-facing
report table and fed unbounded pairwise `difflib.SequenceMatcher` comparisons downstream. The fix
adds `_bounded_row_text`, applies it once at the shared source -- the union-append loop in
`run_stepped_audit` where `batch_rows` rows are merged into `survey_rows` -- so both downstream
consumers inherit the bound, and again directly inside `_survey_findings` itself as
defense-in-depth at the actual report-rendering boundary. The round-4 truncation at the
`already_found_titles` call site is left in place; it is now redundant but harmless.

The round-4 regression test for this (`test_a_pathological_survey_title_is_truncated_at_the_call
_site`) was vacuous: it re-typed the `[:200]` slice expression inline instead of calling any
production function, so it kept passing even when sabotage-tested by reverting the actual
`already_found_titles` call site back to no truncation. It is replaced below with a direct unit
test of `_bounded_row_text` and an end-to-end test of the real, imported `_survey_findings`.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from core.agent_runtime.audit_claim_verifier import AuditEvidence
from core.agent_runtime.audit_verdict import compute_severity
from core.agent_runtime.stepped_audit import (
    PROVABLE_HARM_CLASSES,
    SteppedAuditBudget,
    _bounded_row_text,
    _finding_defect,
    _nominate_finding,
    _survey_findings,
)

TARGET = "api/codec.py"
_LINES = [
    "class Codec:",
    "    def decompress(self, blob):",
    "        out = bytearray()",
    "        while blob:",
    "            pid = blob[0]",
    "            out.extend(blob[:pid])",
    "            blob = blob[pid:]",
    "        return bytes(out)",
]
SOURCE = "\n".join(_LINES)


def _evidence() -> AuditEvidence:
    return AuditEvidence(inspected_paths=(TARGET,), all_paths=(TARGET,), sources={TARGET: SOURCE})


# --------------------------------------------------------------------------------------
# Q4: a self-declared harm_class="crash" cannot buy the provable tier without provable text.
# --------------------------------------------------------------------------------------


def test_a_self_declared_crash_over_an_unprovable_nit_is_rejected_and_downgraded() -> None:
    """'Variable name is unclear... harms readability', declared harm_class='crash'.

    Before the fix this passed `_finding_defect` completely unchecked -- the final rejection
    guard only fired when `declared not in PROVABLE_HARM_CLASSES`, so declaring "crash" skipped
    it outright regardless of what the claim text actually said.
    """

    candidate = {
        "title": "Variable name lacks descriptive clarity",
        "file": TARGET,
        "line_start": 5,
        "line_end": 5,
        "cited_line_text": "            pid = blob[0]",
        "failure_scenario": (
            "The short local variable name makes the function harder for later readers to "
            "follow; behavior today is unaffected."
        ),
        "harm_class": "crash",
    }

    defect = _finding_defect(candidate, _evidence(), TARGET, allow_observations=False)

    assert defect != "", (
        "a readability nit self-declared as harm_class='crash' must be rejected as the primary "
        "finding, not waved through into the tier that maps to High severity"
    )
    assert candidate["harm_class"] != "crash", (
        "the resolved (downgraded) harm_class must be written back to the candidate; a downstream "
        "reader must not still see the model's unearned 'crash' label"
    )
    assert candidate["harm_class"] not in PROVABLE_HARM_CLASSES


def test_a_genuine_crash_claim_declaring_crash_is_still_accepted() -> None:
    """No regression on the legitimate case: real provable text + a correct declared class."""

    candidate = {
        "title": "Loop never terminates on a malformed buffer",
        "file": TARGET,
        "line_start": 4,
        "line_end": 7,
        "cited_line_text": "        while blob:",
        "failure_scenario": (
            "When blob[pid:] returns the same slice because pid is 0, the while loop never "
            "terminates and the process hangs."
        ),
        "harm_class": "crash",
    }

    defect = _finding_defect(candidate, _evidence(), TARGET, allow_observations=False)

    assert defect == "", f"a genuine, regex-provable crash claim must not be rejected: {defect!r}"
    assert candidate["harm_class"] == "crash", (
        "a declared class the claim text actually supports must be kept, not downgraded"
    )


def test_survey_findings_does_not_leak_the_undowngraded_harm_class() -> None:
    """Reproduces the Q4 survey-path leak: `_survey_findings` used to run `_finding_defect` on a
    throwaway `dict(row)` copy, so the downgrade it writes back never reached the `VerdictFinding`
    built from the original `row`. A self-declared `harm_class="crash"` whose failure_scenario
    shares zero vocabulary with `_OUTPUT_INTEGRITY_HARM_RE` must come out of `_survey_findings`
    already downgraded, not still "crash"/"integrity", and must not score as High severity.
    """

    row = {
        "title": "Decompression loop scans the buffer repeatedly",
        "file": TARGET,
        "line_start": 4,
        "line_end": 7,
        "cited_line_text": "        while blob:",
        "failure_scenario": (
            "This is a slow O(n^2) scan over the buffer on every iteration; there is no "
            "correctness impact and output is always identical to the input."
        ),
        "harm_class": "crash",
    }

    findings = _survey_findings([row], _evidence(), TARGET)

    assert len(findings) == 1, "a well-formed row with a checkable citation must survive the survey"
    finding = findings[0]
    assert finding.harm_class not in PROVABLE_HARM_CLASSES, (
        f"the downgrade must be visible in the OUTPUT VerdictFinding, not just in a discarded "
        f"intermediate dict; got harm_class={finding.harm_class!r}"
    )
    assert finding.harm_class not in ("crash", "integrity")
    assert compute_severity(finding.harm_class) != "High", (
        "an unprovable claim self-declared as crash must not still buy High severity once it "
        "reaches the rendered report / computed score"
    )


# --------------------------------------------------------------------------------------
# Q2: `_nominate_finding` tells the model what has already been found this turn.
# --------------------------------------------------------------------------------------


class _PromptCapturingRouter:
    """`_invoke_manifest` that records every `ModelRequest` and always errors out immediately.

    The nomination loop's outcome is irrelevant to this test -- only the FIRST prompt it sent is.
    Erroring keeps the scripted call cheap and deterministic across `_MAX_NOMINATION_ATTEMPTS`.
    """

    def __init__(self) -> None:
        self.requests: list[Any] = []

    def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
        self.requests.append(request)
        return (None, None, "scripted_stop")


MANIFEST = SimpleNamespace(provider_id="test-provider", model_name="test-model")


def test_already_found_titles_are_told_to_the_model_as_do_not_repeat() -> None:
    """`already_found_titles` must reach the prompt in the same 'do not repeat' style as the
    existing `refuted_titles`/`screened_titles` blocks (lines 1146-1160)."""

    router = _PromptCapturingRouter()
    agent = SimpleNamespace(
        memory_router=router, hive_activity_tracker=None, public_hive_bridge=None
    )

    _nominate_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="task-prompt"),
        source_context={},
        evidence=_evidence(),
        effective_input="audit this file for defects",
        budget=SteppedAuditBudget(),
        usages=[],
        already_found_titles=("Module-level cache is shared across instances",),
    )

    assert router.requests, "the nomination call must actually be attempted"
    prompt = str(router.requests[0].prompt)
    assert "Module-level cache is shared across instances" in prompt, (
        "the already-found title must be inserted into the prompt the model actually receives"
    )
    assert "ALREADY been found this turn" in prompt, (
        "the do-not-repeat instruction text must accompany the already-found titles, matching the "
        "established refuted_titles/screened_titles pattern"
    )


def test_already_found_titles_beyond_the_old_ten_item_cap_still_reach_the_prompt() -> None:
    """Reproduces the cap-defeats-its-own-purpose gap: `already_found_titles[:10]` used to freeze
    on the first 10 titles forever once a batch passed that count, so an 11th+ title (found in a
    later nomination attempt) was silently dropped and could be re-nominated as a "new" claim. The
    fix removes the slice entirely -- every already-found title must reach the prompt.
    """

    router = _PromptCapturingRouter()
    agent = SimpleNamespace(
        memory_router=router, hive_activity_tracker=None, public_hive_bridge=None
    )
    titles = tuple(f"Distinct defect number {i}" for i in range(1, 16))
    assert len(titles) > 10

    _nominate_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="task-prompt-many-titles"),
        source_context={},
        evidence=_evidence(),
        effective_input="audit this file for defects",
        budget=SteppedAuditBudget(),
        usages=[],
        already_found_titles=titles,
    )

    assert router.requests, "the nomination call must actually be attempted"
    prompt = str(router.requests[0].prompt)
    for title in titles:
        assert title in prompt, (
            f"{title!r} (position {titles.index(title) + 1}) must reach the prompt -- the old "
            "[:10] slice silently dropped every title past the 10th"
        )


def test_no_already_found_titles_means_no_extra_block() -> None:
    """The control: an empty tuple (the default) must not inject the block at all."""

    router = _PromptCapturingRouter()
    agent = SimpleNamespace(
        memory_router=router, hive_activity_tracker=None, public_hive_bridge=None
    )

    _nominate_finding(
        agent,
        manifest=MANIFEST,
        task=SimpleNamespace(task_id="task-prompt-empty"),
        source_context={},
        evidence=_evidence(),
        effective_input="audit this file for defects",
        budget=SteppedAuditBudget(),
        usages=[],
    )

    assert router.requests, "the nomination call must actually be attempted"
    prompt = str(router.requests[0].prompt)
    assert "ALREADY been found this turn" not in prompt


def test_bounded_row_text_caps_a_pathological_length_input_and_leaves_short_input_stripped() -> None:
    """Direct unit test of the shared helper: a 20,000-char input is capped to exactly `limit`
    chars; a short input is returned stripped and otherwise unchanged."""

    pathological = "X" * 20_000
    bounded = _bounded_row_text(pathological, 200)
    assert len(bounded) == 200, f"expected exactly 200 chars, got {len(bounded)}"
    assert bounded == "X" * 200

    short = _bounded_row_text("  a short scenario  ", 900)
    assert short == "a short scenario", "a short input must come back stripped, not truncated"

    assert _bounded_row_text(None, 200) == "", "None must not raise, must come back as empty"


def test_survey_findings_bounds_title_and_failure_scenario_at_the_same_limits_as_the_primary_finding() -> None:
    """End-to-end reproduction of the round-5 finding: `_survey_findings` is the operator-facing
    report-rendering boundary, and prior to this fix it applied ZERO length cap to `title` or
    `failure_scenario` -- only the PRIMARY finding was bounded at construction. A pathological
    20,000-char title / 5,000-char failure_scenario from a hallucinating nomination reply must
    come out of the REAL, imported `_survey_findings` already bounded to the same 200/900 limits
    the primary finding has always enforced, because that is what ends up verbatim in the
    rendered markdown report table and feeds unbounded pairwise `difflib` comparisons downstream.
    """

    pathological_title = "X" * 20_000
    pathological_scenario = "Y" * 5_000
    row = {
        "title": pathological_title,
        "file": TARGET,
        "line_start": 4,
        "line_end": 7,
        "cited_line_text": "        while blob:",
        "failure_scenario": pathological_scenario,
        "harm_class": "",
    }

    findings = _survey_findings([row], _evidence(), TARGET)

    assert len(findings) == 1, "a row with a checkable citation must survive the survey"
    finding = findings[0]
    assert len(finding.title) <= 200, (
        f"expected title bounded to at most 200 chars, got {len(finding.title)}"
    )
    assert len(finding.failure_scenario) <= 900, (
        f"expected failure_scenario bounded to at most 900 chars, got {len(finding.failure_scenario)}"
    )
    assert finding.title != pathological_title
    assert finding.failure_scenario != pathological_scenario
