"""An audit of one file reads that file and what depends on it — not the whole repository.

Asked to audit `api/apache/liquefy_apache_repetition_v1.py`, the audit read the named file and then
whatever else fitted under a 64-file budget, and scored its coverage against EVERY source file in
the project. Two consequences, both visible to the operator:

* the report said `PARTIAL` no matter how completely the named file had been read, because every
  other source file in the repository counted as undiscovered;
* most of the read budget went on code nobody had asked about, which is also how the model ended up
  with enough unrelated context to make confident claims about files it had barely seen.

The scope that makes a single-file audit answerable needs no static analysis: the target, its tests
by conventional name, and the files that reference it — resolved through `workspace.search_text`,
which the runtime already has.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime.workspace_audit import _related_to_target

ALL_PATHS = (
    "api/apache/liquefy_apache_repetition_v1.py",
    "api/apache/liquefy_primitives.py",
    "api/apache/other_codec.py",
    "tests/test_liquefy_apache_repetition_v1.py",
    "tests/test_unrelated.py",
    "src/server.py",
    "src/ui/widget.tsx",
    "README.md",
    "Makefile",
)
TARGET = "api/apache/liquefy_apache_repetition_v1.py"


def test_the_named_file_is_always_first() -> None:
    """A budget must never be able to drop the file that was named."""

    scoped = _related_to_target(TARGET, ALL_PATHS, referencing=set())
    assert scoped[0] == TARGET


def test_its_tests_are_in_scope() -> None:
    scoped = _related_to_target(TARGET, ALL_PATHS, referencing=set())
    assert "tests/test_liquefy_apache_repetition_v1.py" in scoped


def test_a_file_that_references_it_is_in_scope() -> None:
    scoped = _related_to_target(
        TARGET, ALL_PATHS, referencing={"api/apache/liquefy_primitives.py"}
    )
    assert "api/apache/liquefy_primitives.py" in scoped


def test_the_rest_of_the_repository_is_not() -> None:
    scoped = _related_to_target(
        TARGET, ALL_PATHS, referencing={"api/apache/liquefy_primitives.py"}
    )
    for unrelated in ("api/apache/other_codec.py", "src/server.py", "src/ui/widget.tsx",
                      "tests/test_unrelated.py"):
        assert unrelated not in scoped, unrelated


def test_a_non_source_file_is_never_pulled_in() -> None:
    scoped = _related_to_target(TARGET, ALL_PATHS, referencing={"README.md", "Makefile"})
    assert "README.md" not in scoped and "Makefile" not in scoped


def test_the_order_puts_tests_before_referrers() -> None:
    """If the budget does bite, the target's own tests are the most useful thing to have read."""

    scoped = _related_to_target(
        TARGET, ALL_PATHS, referencing={"api/apache/liquefy_primitives.py"}
    )
    assert scoped.index("tests/test_liquefy_apache_repetition_v1.py") < scoped.index(
        "api/apache/liquefy_primitives.py"
    )


def test_a_target_with_no_tests_and_no_referrers_scopes_to_itself() -> None:
    assert _related_to_target("src/server.py", ALL_PATHS, referencing=set()) == ["src/server.py"]


# --------------------------------------------------------------------------------------
# The measurement that made this necessary: a complete audit reporting PARTIAL
# --------------------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "api" / "target.py").write_text(
        "import os\n\n\ndef decompress(blob):\n    return blob\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_target.py").write_text(
        "def test_decompress():\n    assert True\n", encoding="utf-8"
    )
    for index in range(30):
        (tmp_path / "api" / f"unrelated_{index}.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# project\n", encoding="utf-8")
    return tmp_path


def _audit(project, target: str, *, execute=None) -> tuple[str, list[str]]:
    """Drive the real audit against a real folder, recording which files it read."""

    from core.agent_runtime.workspace_audit import run_workspace_audit
    from core.runtime_execution_tools import execute_runtime_tool

    reads: list[str] = []

    def spy(intent, arguments=None, source_context=None, **kwargs):
        if intent == "workspace.read_file":
            reads.append(str((arguments or {}).get("path") or ""))
        if execute is not None:
            override = execute(intent, arguments or {})
            if override is not None:
                return override
        return execute_runtime_tool(intent, arguments or {}, source_context=source_context)

    report, _steps = run_workspace_audit(
        str(project),
        source_context={"workspace": str(project), "workspace_root": str(project)},
        execute_tool=spy,
        emit=lambda *a, **k: None,
        target_path=target,
    )
    return report, reads


def test_a_complete_named_file_audit_no_longer_reports_partial(project) -> None:
    """The measured symptom. 30 unrelated files made every targeted audit `PARTIAL`."""

    report, _reads = _audit(project, "api/target.py")

    assert "in scope for `api/target.py`" in report
    assert "COMPLETE" in report, report[:600]


def test_the_report_states_what_it_deliberately_did_not_read(project) -> None:
    """A narrowed scope the operator cannot see is indistinguishable from a shallow audit."""

    report, _reads = _audit(project, "api/target.py")

    assert "The rest of the project was deliberately not read." in report
    assert "its tests, and the files that reference it" in report


def test_an_unscoped_audit_still_covers_the_project(project) -> None:
    """Narrowing must apply only when a target was actually named."""

    report, _reads = _audit(project, "")

    assert "in scope for" not in report
    assert "discovered source file(s)" in report


def test_the_unrelated_files_are_not_read(project) -> None:
    """Measured on the reads themselves, not on the report's wording."""

    report, reads = _audit(project, "api/target.py")

    assert "`api/target.py`" in report
    assert any("target.py" in path for path in reads), reads
    unrelated = [path for path in reads if "unrelated_" in path]
    assert unrelated == [], f"read {len(unrelated)} file(s) nobody asked about: {unrelated[:5]}"


# --------------------------------------------------------------------------------------
# Degradation
# --------------------------------------------------------------------------------------


def test_a_failed_reference_search_degrades_to_the_target_and_its_tests(project) -> None:
    """`workspace.search_text` going missing must narrow the scope, never widen it back."""

    def broken_search(intent, arguments):
        if intent == "workspace.search_text":
            return SimpleNamespace(ok=False, status="error", response_text="", details={})
        return None

    report, reads = _audit(project, "api/target.py", execute=broken_search)

    assert "in scope for `api/target.py`" in report
    assert [path for path in reads if "unrelated_" in path] == []


def test_a_truncated_inventory_does_not_force_partial_on_a_scoped_audit(tmp_path, monkeypatch) -> None:
    """A truncated inventory is a caveat for a scoped audit, not an incompleteness.

    A whole-project audit genuinely cannot claim completeness when discovery was truncated — there
    may be source it never listed. A NAMED-FILE audit can: the target was found and read completely,
    and the bound only limits how many of its referrers were discovered. Saying `PARTIAL` there
    would mean no audit of any file in a large repository could ever report otherwise.

    Added after a sabotage survived: the smaller fixture above never reaches the bound, so nothing
    exercised this branch. The ceiling is pinned here rather than matched by fixture size — what is
    under test is the behaviour ON truncation, and it must not silently stop being exercised the
    next time the production ceiling moves.
    """

    monkeypatch.setattr("core.runtime_execution_tools._LIST_FILES_CEILING", 200)
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "target.py").write_text("import os\n\n\ndef go():\n    return 1\n", encoding="utf-8")
    for index in range(220):
        (tmp_path / "api" / f"filler_{index:03d}.py").write_text("x = 1\n", encoding="utf-8")

    report, reads = _audit(tmp_path, "api/target.py")

    assert "in scope for `api/target.py`" in report
    assert "COMPLETE" in report, report[:700]
    assert "other files may reference this one" in report, "the caveat must still be stated"
    assert [path for path in reads if "filler_" in path] == []


def test_a_truncated_inventory_still_forces_partial_on_a_whole_project_audit(tmp_path, monkeypatch) -> None:
    """The other half. Relaxing the rule for scoped audits must not relax it for everything."""

    monkeypatch.setattr("core.runtime_execution_tools._LIST_FILES_CEILING", 200)
    (tmp_path / "api").mkdir()
    for index in range(220):
        (tmp_path / "api" / f"filler_{index:03d}.py").write_text("x = 1\n", encoding="utf-8")

    report, _reads = _audit(tmp_path, "")

    assert "PARTIAL" in report
    assert "undiscovered source may exist" in report


def test_a_target_outside_the_capped_inventory_is_still_found(tmp_path) -> None:
    """A capped listing is not evidence of absence — the second time this project has learnt it.

    `workspace.list_files` is bounded at 200 paths, and `_match_target_path` only searches what the
    inventory returned. On a 221-file fixture the named `api/target.py` sorted after 200 `filler_*`
    files, so the audit found NO target and silently degraded to a whole-project sweep: it reported
    on `api/filler_000.py` through `api/filler_058.py` and never mentioned the file that was asked
    about. Found by a sabotage that needed a >200-file fixture to be meaningful.
    """

    from core.agent_runtime.workspace_audit import _match_target_path, _match_target_path_on_disk

    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "target.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    for index in range(220):
        (tmp_path / "api" / f"filler_{index:03d}.py").write_text("x = 1\n", encoding="utf-8")

    truncated_inventory = tuple(f"api/filler_{index:03d}.py" for index in range(200))
    assert _match_target_path("api/target.py", truncated_inventory) == ""
    assert _match_target_path_on_disk("api/target.py", str(tmp_path)) == "api/target.py"


def test_an_ambiguous_name_on_disk_resolves_to_nothing(tmp_path) -> None:
    """Same rule as the inventory match: auditing the wrong file confidently is the failure."""

    from core.agent_runtime.workspace_audit import _match_target_path_on_disk

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "utils.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b" / "utils.py").write_text("x = 2\n", encoding="utf-8")

    assert _match_target_path_on_disk("utils.py", str(tmp_path)) == ""
    assert _match_target_path_on_disk("a/utils.py", str(tmp_path)) == "a/utils.py"


def test_an_unreadable_root_does_not_raise(tmp_path) -> None:
    from core.agent_runtime.workspace_audit import _match_target_path_on_disk

    assert _match_target_path_on_disk("x.py", "/nonexistent/path/that/is/not/there") == ""
    assert _match_target_path_on_disk("", str(tmp_path)) == ""
