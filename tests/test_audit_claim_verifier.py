"""An audit claim the evidence contradicts must not reach the operator unchallenged.

A cloud-model audit of a real project on 2026-07-29 produced an excellently structured report whose
load-bearing statements were false. Checked against the source afterwards:

    "The repository has zero tests"   -> 48 test files, a tests/ tree, a Makefile `test:` target
    "No external dependencies"        -> the file opens with `import zstandard as zstd`
    "decode() streams records"        -> no `decode` exists anywhere in that file

An independent review scored it: structure 8/10, technical correctness 4/10, verification 0/10 —
and named the danger precisely, that polished formatting raises operator confidence in proportion to
how wrong the content is.

None of the three needed a second model to catch. Each is decidable from evidence the audit had
already gathered. These tests pin that, and they pin the harder half: the verifier must stay silent
when it cannot settle a claim, because one that fires on ambiguity gets ignored.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.audit_claim_verifier import (
    AuditEvidence,
    verify_audit_claims,
)

SOURCE_WITH_THIRD_PARTY = (
    "import time\n"
    "import socket\n"
    "import zstandard as zstd\n"
    "import struct\n"
    "from collections import defaultdict\n"
    "\n"
    "def decompress(self, blob):\n"
    "    return zstd.ZstdDecompressor().decompress(blob)\n"
)


@pytest.fixture
def audited() -> AuditEvidence:
    """The real shape of the audit that produced the false claims."""

    return AuditEvidence(
        inspected_paths=("api/apache/liquefy_apache_repetition_v1.py",),
        all_paths=(
            "api/apache/liquefy_apache_repetition_v1.py",
            "tests/test_contracts.py",
            "tests/test_crypto.py",
            "Makefile",
        ),
        sources={"api/apache/liquefy_apache_repetition_v1.py": SOURCE_WITH_THIRD_PARTY},
    )


# --------------------------------------------------------------------------------------
# The three claims that were actually made, and were actually false
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "The repository has zero tests.",
        "No automated tests were discovered.",
        "There is no test coverage at all.",
        "Tests are entirely absent from this project.",
        "The project lacks tests.",
    ],
)
def test_a_no_tests_claim_is_refused_when_tests_exist(claim: str, audited) -> None:
    annotated, defects = verify_audit_claims(f"## Audit\n\n{claim}", audited)

    assert [d.kind for d in defects] == ["tests_exist"]
    assert "tests/test_contracts.py" in annotated, "the contradiction must name the evidence"


@pytest.mark.parametrize(
    "claim",
    [
        "The module has no external dependencies.",
        "It is dependency-free.",
        "This uses only the standard library.",
        "No third-party imports are present.",
    ],
)
def test_a_no_dependencies_claim_is_refused_when_a_third_party_import_exists(claim, audited) -> None:
    annotated, defects = verify_audit_claims(f"## Audit\n\n{claim}", audited)

    assert [d.kind for d in defects] == ["dependencies_exist"]
    assert "zstandard" in annotated


def test_a_claim_about_a_symbol_that_does_not_exist_is_refused(audited) -> None:
    """The report described the behaviour of a `decode()` that was never in the file."""

    annotated, defects = verify_audit_claims(
        "Its `decode()` method streams records one at a time.", audited
    )

    assert [d.kind for d in defects] == ["symbol_absent"]
    assert "does not appear in any file this audit read" in annotated


def test_all_three_real_claims_are_caught_together(audited) -> None:
    answer = (
        "## Audit\n\nThe repository has zero tests. The module has no external dependencies. "
        "Its `decode()` method streams records one at a time."
    )
    _annotated, defects = verify_audit_claims(answer, audited)

    assert {d.kind for d in defects} == {"tests_exist", "dependencies_exist", "symbol_absent"}


# --------------------------------------------------------------------------------------
# Silence when the evidence does not settle it — the half that keeps the gate credible
# --------------------------------------------------------------------------------------


def test_a_true_no_tests_claim_is_left_alone() -> None:
    """When there really are no tests, saying so is correct and must not be flagged."""

    evidence = AuditEvidence(
        all_paths=("src/app.py", "README.md"),
        sources={"src/app.py": "import os\n"},
    )
    _annotated, defects = verify_audit_claims("No automated tests were discovered.", evidence)
    assert defects == ()


def test_a_true_no_dependencies_claim_is_left_alone() -> None:
    evidence = AuditEvidence(
        all_paths=("src/app.py",),
        sources={"src/app.py": "import os\nimport json\nfrom pathlib import Path\n"},
    )
    _annotated, defects = verify_audit_claims("The module has no external dependencies.", evidence)
    assert defects == (), "a stdlib-only file genuinely has no external dependencies"


def test_a_symbol_that_does_exist_is_left_alone(audited) -> None:
    _annotated, defects = verify_audit_claims(
        "Its `decompress()` method returns a single bytes value.", audited
    )
    assert defects == ()


def test_nothing_is_checked_when_no_source_was_read() -> None:
    """With no evidence, everything is unverifiable — and flagging all of it would be noise."""

    evidence = AuditEvidence(all_paths=("a.py",), sources={})
    _annotated, defects = verify_audit_claims(
        "Its `decode()` method streams records.", evidence
    )
    assert defects == ()


def test_an_answer_with_no_checkable_claims_is_returned_unchanged(audited) -> None:
    answer = "The exception handling is broad; consider narrowing it to the expected error types."
    annotated, defects = verify_audit_claims(answer, audited)

    assert defects == ()
    assert annotated == answer, "an unflagged answer must not gain a footer"


def test_an_empty_answer_is_returned_unchanged(audited) -> None:
    assert verify_audit_claims("", audited) == ("", ())


# --------------------------------------------------------------------------------------
# Shape of the annotation
# --------------------------------------------------------------------------------------


def test_the_model_analysis_is_preserved_not_deleted(audited) -> None:
    """The operator asked a model for analysis and is entitled to see what it said.

    Silently removing the false claim would leave a report reading as though the mistake was never
    made, which is a different kind of dishonesty from the one being fixed.
    """

    answer = "## Audit\n\nThe repository has zero tests.\n\nP1: bare except handlers at line 63."
    annotated, _defects = verify_audit_claims(answer, audited)

    assert answer in annotated
    assert "P1: bare except handlers at line 63." in annotated


def test_a_repeated_claim_is_listed_once(audited) -> None:
    answer = "There are zero tests. As noted, there are zero tests in this repository."
    annotated, _defects = verify_audit_claims(answer, audited)

    assert annotated.count("the inventory lists") == 1


def test_the_stdlib_list_does_not_treat_a_local_package_as_third_party() -> None:
    """A relative or first-party import is not an external dependency either."""

    evidence = AuditEvidence(
        all_paths=("api/x.py",),
        sources={"api/x.py": "import os\nfrom collections import defaultdict\nimport struct\n"},
    )
    assert evidence.third_party_imports() == ()


# --------------------------------------------------------------------------------------
# Wired, not merely written
# --------------------------------------------------------------------------------------


def test_the_verifier_runs_on_a_real_turn_not_only_in_this_file(audited) -> None:
    """The failure this project keeps repeating is a correct detector with no production caller.

    `_asks_which_workspace` existed and worked, and its only caller was the workflow planner, which a
    chat turn never reaches — so its unit test proved a regex and nothing about the product. This
    asserts the verifier is reached through the real final-output path.
    """

    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    result = enforce_final_action_honesty(
        {"response": "The repository has zero tests.", "confidence": 0.9},
        user_input="audit this project",
        session_id="wired-check",
        source_context={
            "workspace_audit_evidence": {
                "all_paths": list(audited.all_paths),
                "inspected_paths": list(audited.inspected_paths),
                "sources": dict(audited.sources),
            }
        },
    )

    assert result.get("audit_claims_contradicted"), "the verifier never ran on the real path"
    assert "tests/test_contracts.py" in result["response"]


def test_a_turn_without_audit_evidence_is_untouched() -> None:
    """Ordinary turns must not pay for this."""

    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    result = enforce_final_action_honesty(
        {"response": "The repository has zero tests.", "confidence": 0.9},
        user_input="hi",
        session_id="no-evidence",
        source_context={},
    )
    assert "audit_claims_contradicted" not in result


def test_a_verifier_fault_never_costs_the_operator_their_answer() -> None:
    """Malformed evidence must degrade to the plain answer, not raise."""

    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    result = enforce_final_action_honesty(
        {"response": "Findings: bare except at line 63.", "confidence": 0.9},
        user_input="audit",
        session_id="broken-evidence",
        source_context={"workspace_audit_evidence": {"all_paths": None, "sources": "not-a-dict"}},
    )
    assert "bare except at line 63" in result["response"]


# --------------------------------------------------------------------------------------
# What the gate missed on its first live run
# --------------------------------------------------------------------------------------


class TestTheGapsTheFirstLiveRunExposed:
    """The gate shipped, then let two false claims straight through on a real audit.

    A review reproduced both. Each is a hole in the checker, not in the idea:

    * the model wrote "No external deps beyond stdlib" — the long form `dependencies` was covered
      and the shorthand `deps` was not, so the claim the gate existed to catch walked past it;
    * it wrote "zero test files in repo" on a repository with 48 of them, and the gate AGREED,
      because the audit inventory is capped at 200 paths and the tests directory fell outside the
      cap. A truncated listing is not evidence of absence.
    """

    @pytest.mark.parametrize(
        "claim",
        [
            "No external deps beyond stdlib.",
            "no external dep required",
            "There are no third-party deps here.",
        ],
    )
    def test_the_deps_shorthand_is_caught_like_the_long_form(self, claim: str) -> None:
        evidence = AuditEvidence(
            all_paths=("a.py",), sources={"a.py": "import zstandard as zstd\n"}
        )
        _annotated, defects = verify_audit_claims(claim, evidence)
        assert [d.kind for d in defects] == ["dependencies_exist"]

    def test_a_capped_inventory_is_not_read_as_proof_that_tests_are_absent(self, tmp_path) -> None:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_real.py").write_text("def test_x(): pass\n", encoding="utf-8")
        (tmp_path / "app.py").write_text("import os\n", encoding="utf-8")

        # The inventory reached only app.py — exactly the live shape, where the cap stopped short of
        # the tests tree.
        evidence = AuditEvidence(
            all_paths=("app.py",),
            sources={"app.py": "import os\n"},
            workspace_root=str(tmp_path),
        )
        _annotated, defects = verify_audit_claims("The repo has zero test files.", evidence)

        assert [d.kind for d in defects] == ["tests_exist"]

    def test_a_project_that_truly_has_no_tests_is_still_not_flagged(self, tmp_path) -> None:
        """The fallback must not turn into a claim that every project has tests."""

        (tmp_path / "app.py").write_text("import os\n", encoding="utf-8")
        evidence = AuditEvidence(
            all_paths=("app.py",), sources={"app.py": "import os\n"}, workspace_root=str(tmp_path)
        )
        _annotated, defects = verify_audit_claims("No automated tests were discovered.", evidence)
        assert defects == ()

    def test_an_unreadable_workspace_root_does_not_raise(self) -> None:
        evidence = AuditEvidence(
            all_paths=("app.py",), sources={"app.py": "import os\n"},
            workspace_root="/nonexistent/path/that/is/not/there",
        )
        _annotated, defects = verify_audit_claims("There are zero tests.", evidence)
        assert defects == ()


class TestACitedLocationIsDecidable:
    """`file:line` was fully checkable from evidence already gathered, and nothing checked it.

    `AuditEvidence.sources` holds VERBATIM text — the audit issues its reads with
    `"verbatim": True` — so `splitlines()[n - 1]` really is line n. A report citing
    `api/x.py:900` for a 120-line file was passed through unchallenged, and a polished citation is
    exactly the kind of detail that raises operator confidence.

    The other half is not flagging our own gaps: a citation past the end of a file the audit read
    only PART of is a hole in the evidence, not a fabrication by the model. `incomplete_files` is
    forwarded from the audit for that distinction alone.
    """

    SOURCE = "\n".join(f"line {index}" for index in range(1, 121))

    def _evidence(self, *, incomplete: bool = False) -> AuditEvidence:
        return AuditEvidence(
            all_paths=("api/x.py",),
            inspected_paths=("api/x.py",),
            sources={"api/x.py": self.SOURCE},
            incomplete_files=("api/x.py",) if incomplete else (),
        )

    @pytest.mark.parametrize(
        "claim",
        ["bare except at api/x.py:900", "see `x.py` line 900", "at line 900 of api/x.py"],
    )
    def test_a_line_past_the_end_of_a_fully_read_file_is_refused(self, claim: str) -> None:
        _annotated, defects = verify_audit_claims(claim, self._evidence())

        assert [d.kind for d in defects] == ["line_absent"]
        assert "120 line(s)" in defects[0].contradiction

    @pytest.mark.parametrize("claim", ["bare except at api/x.py:63", "at line 12 of api/x.py"])
    def test_a_line_that_exists_is_left_alone(self, claim: str) -> None:
        _annotated, defects = verify_audit_claims(claim, self._evidence())
        assert defects == ()

    def test_the_boundary_is_exact(self) -> None:
        """The last line is valid and the next one is not.

        Added after a sabotage survived: every fixture cited either line 12 or line 900, so an
        off-by-one (`line > total + 1`) passed the whole suite. A citation check that is one line
        loose is a check that clears the most common fabrication there is.
        """

        _annotated, defects = verify_audit_claims("api/x.py:120 is fine", self._evidence())
        assert defects == (), "line 120 of a 120-line file exists"

        _annotated, defects = verify_audit_claims("api/x.py:121 is not", self._evidence())
        assert [d.kind for d in defects] == ["line_absent"]

        _annotated, defects = verify_audit_claims("api/x.py:0 is not", self._evidence())
        assert [d.kind for d in defects] == ["line_absent"]

    def test_a_partially_read_file_is_never_flagged(self) -> None:
        """Our gap, not the model's. Flagging it would be the verifier lying about the model."""

        _annotated, defects = verify_audit_claims(
            "bare except at api/x.py:900", self._evidence(incomplete=True)
        )
        assert defects == ()

    def test_a_file_this_audit_never_read_is_not_judged(self) -> None:
        _annotated, defects = verify_audit_claims(
            "bare except at some/other.py:900", self._evidence()
        )
        assert defects == ()

    def test_an_ambiguous_citation_resolves_to_nothing(self) -> None:
        """Flagging a line against the wrong file would be its own fabrication."""

        evidence = AuditEvidence(
            all_paths=("a/utils.py", "b/utils.py"),
            sources={"a/utils.py": "x = 1\n", "b/utils.py": "y = 2\n"},
        )
        _annotated, defects = verify_audit_claims("see `utils.py:900`", evidence)
        assert defects == ()

    def test_a_repeated_citation_is_listed_once(self) -> None:
        annotated, _defects = verify_audit_claims(
            "api/x.py:900 is wrong, and api/x.py:900 again", self._evidence()
        )
        assert annotated.count("does not exist") == 1

    def test_line_at_reads_the_verbatim_line(self) -> None:
        """If this were off by one, every citation check would be wrong in the same direction."""

        evidence = self._evidence()
        assert evidence.line_at("api/x.py", 1) == "line 1"
        assert evidence.line_at("api/x.py", 120) == "line 120"
        assert evidence.line_at("api/x.py", 121) == ""
        assert evidence.line_at("api/x.py", 0) == ""


class TestASymbolMustBeDefinedWhereTheReportSaysItIs:
    """`defines_symbol` was written, tested, and never called by anything.

    `mentions_symbol` only asks whether the name occurs in the source at all, which an IMPORTED
    helper satisfies without the audited file owning any of its behaviour. So "its `decode()`
    method streams records one at a time" passed whenever `decode` appeared anywhere — including on
    the `from lib import decode` line.
    """

    EVIDENCE = AuditEvidence(
        all_paths=("api/x.py",),
        inspected_paths=("api/x.py",),
        sources={"api/x.py": "from lib import decode\n\n\ndef go():\n    return decode(1)\n"},
    )

    @pytest.mark.parametrize(
        "claim",
        [
            "Its `decode()` method streams records one at a time.",
            "The `decode()` method in this file streams records.",
            "This module's `decode()` returns a generator.",
        ],
    )
    def test_a_borrowed_symbol_is_not_this_file_s_behaviour(self, claim: str) -> None:
        _annotated, defects = verify_audit_claims(claim, self.EVIDENCE)

        assert [d.kind for d in defects] == ["symbol_not_defined_here"]
        assert "not defined in it" in defects[0].contradiction

    def test_a_symbol_the_file_really_defines_is_left_alone(self) -> None:
        _annotated, defects = verify_audit_claims("Its `go()` method returns.", self.EVIDENCE)
        assert defects == ()

    def test_merely_calling_a_symbol_is_not_a_claim_of_ownership(self) -> None:
        """The check must fire on attribution, not on any mention of a function."""

        _annotated, defects = verify_audit_claims("It calls `decode()` from lib.", self.EVIDENCE)
        assert defects == ()

    def test_an_absent_symbol_is_still_reported_once_not_twice(self) -> None:
        """`symbol_absent` already covers a name that is nowhere in the source."""

        _annotated, defects = verify_audit_claims(
            "Its `nowhere()` method streams records.", self.EVIDENCE
        )
        assert [d.kind for d in defects] == ["symbol_absent"]

    def test_defines_symbol_is_no_longer_dead(self) -> None:
        """The failure this project keeps repeating is a correct check with no production caller."""

        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "core" / "agent_runtime" / "audit_claim_verifier.py"
        ).read_text(encoding="utf-8")
        assert source.count("defines_symbol") >= 2, "written but still never called"


def test_the_audit_forwards_incomplete_files_into_the_evidence() -> None:
    """The verifier's distinction is worth nothing if the producer never sends the field."""

    from pathlib import Path

    audit = (
        Path(__file__).resolve().parents[1] / "core" / "agent_runtime" / "workspace_audit.py"
    ).read_text(encoding="utf-8")
    validator = (
        Path(__file__).resolve().parents[1]
        / "core" / "agent_runtime" / "action_honesty_validator.py"
    ).read_text(encoding="utf-8")

    assert '"incomplete_files": tuple(incomplete_files),' in audit
    assert 'incomplete_files=tuple(evidence_blob.get("incomplete_files") or ()),' in validator
