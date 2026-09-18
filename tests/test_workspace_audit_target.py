"""An audit must audit the file the operator named.

From a live failure on 2026-07-28. Asked *"run audit for this - app-landing/index.html tell me if
u can spot any security issues"*, the audit ran a fixed sweep that read `README.md` and one stray
script, never opened the named file, and reported on the workspace as though nothing had been
named. Two independent causes:

* `run_workspace_audit` had **no target parameter at all** — the request text was consumed as a
  boolean ("does this look like an audit request?") and discarded.
* `.html` was absent from `SOURCE_SUFFIXES`, so a static site was a project with no auditable
  files. The one artifact the operator asked about was structurally invisible.

The excerpt matters as much as the read. The deterministic analyzers have no rules for inline
handlers, inline `<script>` blocks or CSP, so the report could truthfully say the file was read and
still hand the model nothing to reason about. The model writes the answer; the model needs the
source.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_runtime.source_audit import is_source_path
from core.agent_runtime.workspace_audit import (
    _match_target_path,
    audit_target_in,
    run_workspace_audit,
)

# The operator's verbatim request.
REQUEST = (
    "great, i need you to run audit for this - app-landing/index.html tell me if u can spot any "
    "security issues and such?"
)


@pytest.fixture()
def site(tmp_path: Path) -> Path:
    """A static landing-page project, shaped like the real one: no git, HTML/CSS/JS."""

    (tmp_path / "app-landing").mkdir()
    (tmp_path / "assets").mkdir()
    (tmp_path / "app-landing" / "index.html").write_text(
        "<html><body>\n"
        "<div onclick=\"doThing()\">go</div>\n"
        "<script>var t=localStorage.getItem('token');document.write(t);</script>\n"
        "</body></html>\n",
        encoding="utf-8",
    )
    (tmp_path / "assets" / "styles.css").write_text("body{margin:0}\n", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('hi');\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Site\n", encoding="utf-8")
    return tmp_path


def _audit(root: Path, request: str):
    from core.runtime_execution_tools import execute_runtime_tool

    reads: list[str] = []

    def spy(intent, arguments=None, source_context=None, **kwargs):
        if intent == "workspace.read_file":
            reads.append(str((arguments or {}).get("path") or ""))
        return execute_runtime_tool(intent, arguments or {}, source_context=source_context)

    report, steps = run_workspace_audit(
        str(root),
        source_context={"workspace": str(root)},
        execute_tool=spy,
        emit=lambda *a, **k: None,
        target_path=audit_target_in(request),
    )
    return report, reads, steps


# --------------------------------------------------------------------------------------
# Target extraction
# --------------------------------------------------------------------------------------


def test_the_operators_verbatim_request_yields_the_named_file() -> None:
    assert audit_target_in(REQUEST) == "app-landing/index.html"


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("audit config.py and tell me what you think", "config.py"),
        ("review `src/app.js` please", "src/app.js"),
        ("run an audit of my project", ""),
        ("audit this workspace e.g. for security", ""),
        ("check the codebase", ""),
    ],
)
def test_target_extraction(request_text: str, expected: str) -> None:
    assert audit_target_in(request_text) == expected


def test_an_ambiguous_name_resolves_to_nothing_rather_than_a_guess() -> None:
    """Auditing the wrong file and saying so confidently is the failure being prevented."""

    paths = ("app-landing/index.html", "site/index.html")
    assert _match_target_path("index.html", paths) == ""
    assert _match_target_path("app-landing/index.html", paths) == "app-landing/index.html"


# --------------------------------------------------------------------------------------
# Web files are source
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "auditable"),
    [
        ("app-landing/index.html", True),
        ("assets/styles.css", True),
        ("site/assets/site.js", True),
        ("README.md", False),
        ("data.json", False),
        ("logo.png", False),
    ],
)
def test_web_files_count_as_source(path: str, auditable: bool) -> None:
    assert is_source_path(path) is auditable


# --------------------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------------------


def test_the_named_file_is_actually_read(site: Path) -> None:
    _report, reads, _steps = _audit(site, REQUEST)
    assert any("app-landing/index.html" in path for path in reads), reads


def test_the_named_files_content_reaches_the_evidence(site: Path) -> None:
    """A report that names the file but omits its source gives the model nothing to audit."""

    report, _reads, _steps = _audit(site, REQUEST)
    assert "## Requested file — app-landing/index.html" in report
    assert "localStorage.getItem" in report
    assert 'onclick="doThing()"' in report


def test_a_request_naming_nothing_still_audits_the_workspace(site: Path) -> None:
    report, reads, _steps = _audit(site, "run a full code audit of this project")
    assert reads, "the sweep must still run"
    assert "## Requested file" not in report


def test_a_named_file_that_does_not_exist_is_reported_not_invented(site: Path) -> None:
    report, _reads, _steps = _audit(site, "audit app-landing/missing.html for security issues")
    # No match resolves to no target section rather than to a different file's contents.
    assert "## Requested file — app-landing/missing.html" not in report
    assert "localStorage.getItem" not in report.split("## Findings")[0]


def test_the_static_site_is_no_longer_an_empty_project(site: Path) -> None:
    report, _reads, _steps = _audit(site, REQUEST)
    assert "Inspected 0 source file(s)" not in report


def test_a_leading_bullet_or_dash_does_not_break_target_resolution() -> None:
    """Measured live: a request naming ONE file audited sixty-four.

    The operator wrote "-api/apache/liquefy_apache_repetition_v1.py - audit the code please". The
    extractor kept the leading hyphen, `_match_target_path` matched no inventory path, resolution
    returned "", and the audit silently fell back to the broad 64-file sweep. A leading `-`, `*` or
    `•` is how people write a list item; it is never part of a path.
    """

    from core.agent_runtime.workspace_audit import _match_target_path, audit_target_in

    inventory = ("api/apache/liquefy_apache_repetition_v1.py", "src/app.py")
    for phrasing in (
        "-api/apache/liquefy_apache_repetition_v1.py - audit the code please",
        "* api/apache/liquefy_apache_repetition_v1.py — review it",
        "• api/apache/liquefy_apache_repetition_v1.py",
        "> api/apache/liquefy_apache_repetition_v1.py please audit",
        "(api/apache/liquefy_apache_repetition_v1.py) audit this",
    ):
        target = audit_target_in(phrasing)
        assert not target.startswith(("-", "*", "•", ">", "(")), f"punctuation kept: {target!r}"
        assert _match_target_path(target, inventory) == (
            "api/apache/liquefy_apache_repetition_v1.py"
        ), f"unresolved for {phrasing!r}"


def test_a_hyphen_inside_a_path_is_preserved() -> None:
    """Stripping must not damage a real name — `app-landing/index.html` is the owner's own folder."""

    from core.agent_runtime.workspace_audit import audit_target_in

    assert audit_target_in("audit app-landing/index.html") == "app-landing/index.html"
    assert audit_target_in("- app-landing/index.html") == "app-landing/index.html"
