"""Plan step 2 — the deterministic workspace-audit executor.

Acceptance (Codex #1): an audit runs several REAL tool steps and grounds its findings in their
outputs; a top-level listing + model prose is a failure.
"""
from __future__ import annotations

from core.agent_runtime.source_audit import analyze_sources, is_test_path
from core.agent_runtime.workspace_audit import (
    looks_like_code_audit_request,
    maybe_handle_workspace_audit_request,
    run_workspace_audit,
)


class _Result:
    def __init__(self, ok, status="executed", response_text="", details=None):
        self.ok = ok
        self.status = status
        self.response_text = response_text
        self.details = dict(details or {})


def _file_result(content, *, start_line=1, max_lines=400):
    lines = content.splitlines()
    chunk = lines[start_line - 1 : start_line - 1 + max_lines]
    return _Result(
        True,
        status="executed" if chunk else "empty_slice",
        response_text="\n".join(chunk),
        details={
            "start_line": start_line,
            "line_count": len(chunk),
            "lines": [
                {"line_number": start_line + offset, "text": line}
                for offset, line in enumerate(chunk)
            ],
        },
    )


def _stub_execute(intent, arguments, *, source_context=None):
    files = {
        "README.md": "# Demo",
        "pyproject.toml": "[project]\nname = \"demo\"",
        "package.json": '{"scripts":{"lint":"eslint ."}}',
        "src/app.py": "def run():\n    return 42",
        "tests/test_app.py": "def test_run():\n    assert True",
    }
    if intent == "workspace.list_tree":
        return _Result(True, response_text="src/\n  app.py\ntests/\nREADME.md\npyproject.toml")
    if intent == "workspace.read_file":
        path = str((arguments or {}).get("path") or "")
        if path in files:
            return _file_result(
                files[path],
                start_line=int(arguments.get("start_line") or 1),
                max_lines=int(arguments.get("max_lines") or 400),
            )
        return _Result(False, status="not_found")
    if intent == "workspace.list_files":
        return _Result(
            True,
            details={"paths": list(files), "count": len(files), "truncated": False},
        )
    if intent == "workspace.git_summary":
        return _Result(True, response_text="branch main; clean")
    if intent == "workspace.run_lint":
        return _Result(True, response_text="All checks passed")
    return None


def test_audit_intent_matcher():
    for yes in (
        "do a full audit of this repo",
        "audit the codebase",
        "analyse the code",
        "code review",
        "review this code",
        "security audit",
        "I am asking for an audit, not a structure report",
        "ok i need you to run production grade audit pls- see if code is failing or have weak spots?",
        "hey lets audit thsi folder?",
    ):
        assert looks_like_code_audit_request(yes) is True, yes
    for no in ("what's in this folder", "find my dropbox folder", "how are you"):
        assert looks_like_code_audit_request(no) is False, no


def test_run_workspace_audit_runs_real_tool_steps_and_grounds_findings():
    events = []

    def _emit(ctx, *, event_type, message, details=None):
        events.append((event_type, message, details or {}))

    report, steps = run_workspace_audit(
        "/tmp/web0", source_context={"project_id": "proj_web0"},
        execute_tool=_stub_execute, emit=_emit,
    )
    ok_intents = [s["intent"] for s in steps if s["ok"]]
    assert "workspace.list_tree" in ok_intents
    assert "workspace.git_summary" in ok_intents
    assert "workspace.run_lint" in ok_intents
    assert "workspace.read_file" in ok_intents  # a manifest was read
    assert "workspace.list_files" in ok_intents
    assert len([s for s in steps if s["ok"]]) >= 4  # several REAL tool steps, not listing+prose

    assert "README.md" in report and "pyproject.toml" in report  # findings cite real files
    assert "branch main" in report and "/tmp/web0" in report
    assert "**COMPLETE**" in report
    assert "`src/app.py` — 2 line(s)" in report
    assert "`tests/test_app.py` — 2 line(s)" in report
    assert "Read 3 real artifact" not in report
    assert report.index("## Findings") < report.index("### Repository structure")

    types = [e[0] for e in events]
    assert "scope_resolved" in types
    assert types.count("tool_executed") >= 4
    lint_step = next(s for s in steps if s["intent"] == "workspace.run_lint")
    assert lint_step["label"] == "Configured package lint"


def test_audit_reports_missing_manifest_honestly():
    called = []

    def _no_manifest(intent, arguments, *, source_context=None):
        called.append((intent, arguments))
        if intent == "workspace.list_tree":
            return _Result(True, response_text="a.py\nb.py")
        if intent == "workspace.list_files":
            return _Result(
                True,
                details={"paths": ["a.py", "b.py"], "count": 2, "truncated": False},
            )
        if intent == "workspace.read_file" and arguments.get("path") in {"a.py", "b.py"}:
            return _file_result("VALUE = 1")
        if intent == "workspace.git_summary":
            return _Result(False, status="not_a_repo")
        return _Result(False, status="not_found")

    report, _steps = run_workspace_audit("/tmp/x", source_context=None, execute_tool=_no_manifest)
    assert "no standard manifest" in report.lower()
    assert "no safe, project-declared validation command" in report
    assert "**COMPLETE**" in report
    assert "No automated test source was discovered" in report
    assert not any(intent == "workspace.run_lint" for intent, _args in called)


def test_empty_bound_folder_stops_without_fake_audit_or_unrelated_tools():
    called = []

    def _empty(intent, arguments, *, source_context=None):
        called.append((intent, arguments))
        if intent == "workspace.list_tree":
            return _Result(
                True,
                status="no_results",
                response_text="No files or directories matched inside `.`.",
            )
        raise AssertionError(f"empty preflight must stop before {intent}")

    report, steps = run_workspace_audit(
        "/Users/example-user/Desktop/dna - x402",
        source_context={"project_id": "proj_dna"},
        execute_tool=_empty,
    )

    assert [intent for intent, _args in called] == ["workspace.list_tree"]
    assert len(steps) == 1
    assert steps[0]["ok"] is False
    assert "selected project folder is empty" in report
    assert "No manifest, Git, lint, or test commands were run" in report
    assert "No files were changed" in report
    assert "Read 1 real artifact" not in report


def test_rust_project_uses_cargo_format_check_not_python_ruff():
    calls = []

    def _rust(intent, arguments, *, source_context=None):
        calls.append((intent, arguments))
        if intent == "workspace.list_tree":
            return _Result(True, response_text="src/\nCargo.toml")
        if intent == "workspace.read_file":
            if arguments.get("path") == "Cargo.toml":
                return _file_result("[package]\nname = \"dna-x402\"")
            if arguments.get("path") == "src/main.rs":
                return _file_result("fn main() {}")
            return _Result(False, status="not_found")
        if intent == "workspace.list_files":
            return _Result(
                True,
                details={"paths": ["Cargo.toml", "src/main.rs"], "count": 2, "truncated": False},
            )
        if intent == "workspace.git_summary":
            return _Result(True, response_text="branch main; clean")
        if intent == "workspace.run_lint":
            return _Result(True, response_text=f"$ {arguments.get('command')}\nclean")
        raise AssertionError(intent)

    report, _steps = run_workspace_audit("/tmp/rust", source_context=None, execute_tool=_rust)
    lint_args = [args for intent, args in calls if intent == "workspace.run_lint"]
    assert lint_args == [{"command": "cargo fmt --all -- --check"}]
    assert "python" not in report.lower()
    assert "ruff" not in report.lower()


def test_python_source_audit_reports_concrete_failures_and_project_gaps():
    files = {
        "requirements.txt": "requests\npillow==10.0.0",
        "app.py": (
            "import subprocess\n"
            "def run():\n"
            "    try:\n"
            "        subprocess.run(['helper'])\n"
            "    except:\n"
            "        pass\n"
        ),
    }

    def _execute(intent, arguments, *, source_context=None):
        if intent == "workspace.list_tree":
            return _Result(True, response_text="app.py\nrequirements.txt")
        if intent == "workspace.list_files":
            return _Result(
                True,
                details={"paths": list(files), "count": len(files), "truncated": False},
            )
        if intent == "workspace.read_file":
            path = arguments.get("path")
            if path in files:
                return _file_result(
                    files[path],
                    start_line=int(arguments.get("start_line") or 1),
                    max_lines=int(arguments.get("max_lines") or 400),
                )
            return _Result(False, status="not_found")
        if intent == "workspace.git_summary":
            return _Result(False, status="not_a_repo")
        raise AssertionError(intent)

    report, _steps = run_workspace_audit("/tmp/python", source_context=None, execute_tool=_execute)

    assert "**COMPLETE**" in report
    assert "inspected 1 of 1 discovered source file(s), 6 line(s)" in report
    assert "[P1] Bare exception handler hides every failure" in report
    assert "`app.py:5`" in report
    assert "[P2] External command has no timeout" in report
    assert "`app.py:4`" in report
    assert "Dependency is not reproducibly pinned" in report
    assert "No automated test source was discovered" in report


def test_source_audit_refuses_findings_when_inventory_has_no_source():
    def _execute(intent, arguments, *, source_context=None):
        if intent == "workspace.list_tree":
            return _Result(True, response_text="README.md")
        if intent == "workspace.list_files":
            return _Result(
                True,
                details={"paths": ["README.md"], "count": 1, "truncated": False},
            )
        if intent == "workspace.read_file":
            if arguments.get("path") == "README.md":
                return _file_result("# Notes")
            return _Result(False, status="not_found")
        if intent == "workspace.git_summary":
            return _Result(False, status="not_a_repo")
        raise AssertionError(intent)

    report, _steps = run_workspace_audit("/tmp/notes", source_context=None, execute_tool=_execute)

    assert "**BLOCKED**" in report
    assert "refuses to claim a code audit without readable source evidence" in report
    assert "No code findings are claimed" in report


def test_exact_read_chunk_boundary_is_completed_by_empty_followup_read():
    source = "\n".join(f"VALUE_{index} = {index}" for index in range(400))
    read_starts = []

    def _execute(intent, arguments, *, source_context=None):
        if intent == "workspace.list_tree":
            return _Result(True, response_text="app.py")
        if intent == "workspace.list_files":
            return _Result(
                True,
                details={"paths": ["app.py"], "count": 1, "truncated": False},
            )
        if intent == "workspace.read_file":
            if arguments.get("path") == "app.py":
                start = int(arguments.get("start_line") or 1)
                read_starts.append(start)
                return _file_result(source, start_line=start, max_lines=int(arguments.get("max_lines") or 400))
            return _Result(False, status="not_found")
        if intent == "workspace.git_summary":
            return _Result(False, status="not_a_repo")
        raise AssertionError(intent)

    report, _steps = run_workspace_audit("/tmp/boundary", source_context=None, execute_tool=_execute)

    assert read_starts == [1, 401]
    assert "**COMPLETE**" in report
    assert "`app.py` — 400 line(s)" in report


def test_truncated_inventory_is_never_reported_as_complete():
    def _execute(intent, arguments, *, source_context=None):
        if intent == "workspace.list_tree":
            return _Result(True, response_text="app.py\n...")
        if intent == "workspace.list_files":
            return _Result(
                True,
                status="truncated",
                details={"paths": ["app.py"], "count": 1, "truncated": True},
            )
        if intent == "workspace.read_file":
            if arguments.get("path") == "app.py":
                return _file_result("VALUE = 1")
            return _Result(False, status="not_found")
        if intent == "workspace.git_summary":
            return _Result(False, status="not_a_repo")
        raise AssertionError(intent)

    report, _steps = run_workspace_audit("/tmp/large", source_context=None, execute_tool=_execute)

    assert "**PARTIAL**" in report
    assert "File discovery hit its 200-file bound" in report
    assert "**COMPLETE**" not in report


def test_incomplete_python_prefix_is_not_misreported_as_a_syntax_error():
    analysis = analyze_sources(
        {"large.py": "def unfinished(\n"},
        all_paths=("large.py",),
        incomplete_paths=("large.py",),
    )

    assert analysis.parse_failures == 0
    assert not any(item.rule_id == "python-syntax-error" for item in analysis.findings)


def test_nested_test_module_is_recognized_as_test_source():
    assert is_test_path("demo-scaffold/test_app.py") is True
    analysis = analyze_sources(
        {
            "demo-scaffold/app.py": "VALUE = 1",
            "demo-scaffold/test_app.py": "def test_value():\n    assert True",
        },
        all_paths=("demo-scaffold/app.py", "demo-scaffold/test_app.py"),
    )

    assert not any(item.rule_id == "tests-missing" for item in analysis.findings)


def test_default_runtime_workspace_audit_is_refused_without_project_binding():
    class _Agent:
        def _fast_path_result(self, **kwargs):
            return kwargs

    result = maybe_handle_workspace_audit_request(
        _Agent(),
        "lets audit this folder?",
        session_id="openclaw:general",
        source_surface="api",
        source_context={
            "surface": "api",
            "workspace": "/Users/example/.vool_runtime/workspace",
            "workspace_root": "/Users/example/.vool_runtime/workspace",
            "workspace_binding": "default",
            "project_id": "",
        },
    )

    assert result is not None
    assert result["reason"] == "workspace_audit_default_scope_refused"
    assert "not bound to a project folder" in result["response"]
    assert "I will not guess and audit the wrong folder" in result["response"]


def test_bound_audit_is_tool_evidence_not_a_scripted_final_answer(monkeypatch):
    class _Agent:
        def _fast_path_result(self, **kwargs):
            raise AssertionError("a successful bound audit must not become a fast-path answer")

    context = {
        "surface": "api",
        "workspace": "/tmp/novel-raven-workspace",
        "workspace_binding": "project",
        "project_id": "raven-91",
        "requested_model": "vendor/unseen-reasoner:free",
    }
    monkeypatch.setattr("core.runtime_execution_tools.execute_runtime_tool", _stub_execute)
    monkeypatch.setattr("core.runtime_task_events.emit_runtime_event", lambda *args, **kwargs: None)

    result = maybe_handle_workspace_audit_request(
        _Agent(),
        "Could you scrutinize the implementation here and give me a verdict without editing it?",
        session_id="openclaw:novel-raven",
        source_surface="api",
        source_context=context,
    )

    assert result is None
    assert context["workspace_audit_evidence_collected"] is True
    evidence = context["runtime_tool_observations"][-1]
    assert evidence["intent"] == "workspace.audit"
    assert evidence["final_answer"] is False
    assert "Code audit" in evidence["response_preview"]
    assert evidence["workspace_root"] == "/tmp/novel-raven-workspace"


def test_declared_pytest_suite_runs_and_failure_becomes_top_finding():
    files = {
        "requirements.txt": "pytest>=8.0.0",
        "app.py": "def answer():\n    return 41",
        "tests/test_app.py": "def test_answer():\n    assert False",
    }
    calls = []

    def _execute(intent, arguments, *, source_context=None):
        calls.append((intent, arguments))
        if intent == "workspace.list_tree":
            return _Result(True, response_text="app.py\ntests/test_app.py\nrequirements.txt")
        if intent == "workspace.list_files":
            return _Result(
                True,
                details={"paths": list(files), "count": len(files), "truncated": False},
            )
        if intent == "workspace.read_file":
            path = arguments.get("path")
            if path in files:
                return _file_result(
                    files[path],
                    start_line=int(arguments.get("start_line") or 1),
                    max_lines=int(arguments.get("max_lines") or 400),
                )
            return _Result(False, status="not_found")
        if intent == "workspace.git_summary":
            return _Result(True, response_text="branch main; clean")
        if intent == "workspace.run_tests":
            return _Result(
                False,
                status="failed",
                response_text="1 failed, 111 passed\nFAILED tests/test_app.py::test_answer",
            )
        raise AssertionError(intent)

    report, _steps = run_workspace_audit("/tmp/pytest-project", source_context=None, execute_tool=_execute)

    assert ("workspace.run_tests", {"command": "python3 -m pytest -q"}) in calls
    assert "**COMPLETE source coverage · validation FAILED.**" in report
    assert "### [P1] Project validation failed" in report
    assert "1 failed, 111 passed" in report
    assert report.index("Project validation failed") < report.index("### Repository structure")
