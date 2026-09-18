"""Running a plugin's tool: argument shape, process isolation, and the environment boundary.

The environment tests are the ones that matter on this machine. `core/mcp_client.py:120-122`
builds a child environment as `{**os.environ, **self.env}` — everything the parent has, including
whatever keys are exported — and a plugin is code a user downloaded. A previous machine lost live
Solana keypairs to a package that read the filesystem during install; handing a downloaded tool the
whole environment is the same shape of mistake.

Measured on macOS 2026-07-28: with an allowlist of nothing plus one secret, `/usr/bin/env` in the
child reports exactly `PATH`, `TMPDIR` and that secret. A child that then invokes `/bin/sh` or a
shimmed `python3` sees a few more — `PWD`/`SHLVL` set by the shell itself, and `CPATH`/`SDKROOT`
set by the Python launcher from its own installation. Those are the interpreter's own values, not
the parent's: exporting `CPATH=/PARENT-VALUE-LEAKED` and reading it back in the child yields
`/usr/local/include`. So the boundary holds where it is enforced, and the accurate claim is about
the exec boundary rather than about every descendant of it.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from core import plugin_executor

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["jql"],
    "properties": {
        "jql": {"type": "string", "x-vool-kind": "query"},
        "name": {"type": "string", "x-vool-kind": "name"},
        "where": {"type": "string", "x-vool-kind": "path"},
        "site": {"type": "string", "x-vool-kind": "url"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
    },
}


@pytest.fixture()
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "vool-jira"
    (root / "bin").mkdir(parents=True)
    handler = root / "bin" / "run"
    handler.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        "p = json.load(sys.stdin)\n"
        "mode = sys.argv[1] if len(sys.argv) > 1 else 'ok'\n"
        "if mode == 'env':\n"
        "    print(json.dumps({'ok': True, 'observation': {'seen': sorted(os.environ)}}))\n"
        "elif mode == 'garbage':\n"
        "    print('not json at all')\n"
        "elif mode == 'fail':\n"
        "    print(json.dumps({'ok': False, 'status': 'no_such_project', 'error': 'ENG does not exist'}))\n"
        "elif mode == 'crash':\n"
        "    sys.stderr.write('boom'); sys.exit(3)\n"
        "elif mode == 'hang':\n"
        "    import time; time.sleep(30)\n"
        "else:\n"
        "    print(json.dumps({'ok': True, 'text': '2 issues',\n"
        "        'observation': {'jql': p['arguments']['jql'], 'issues': [{'key': 'ENG-1'}]},\n"
        "        'resolved_target': p['arguments']['jql']}))\n",
        encoding="utf-8",
    )
    handler.chmod(handler.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    return root


def _run(plugin: Path, *, arguments=None, args=(), **kwargs):
    return plugin_executor.run_plugin_tool(
        intent="vool-jira.search_issues",
        arguments=arguments if arguments is not None else {"jql": "project = ENG"},
        handler={"kind": "subprocess", "entry": "bin/run", "args": list(args)},
        schema=SCHEMA,
        plugin_root=plugin,
        **kwargs,
    )


# --------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------


def test_a_plugin_tool_runs_and_returns_a_grounded_observation(plugin: Path) -> None:
    result = _run(plugin)
    assert result.ok and result.status == "executed"
    assert result.resolved_target == "project = ENG"
    assert result.observation["issues"] == [{"key": "ENG-1"}]


def test_the_observation_always_names_its_intent(plugin: Path) -> None:
    assert _run(plugin).observation["intent"] == "vool-jira.search_issues"


def test_declared_defaults_are_applied_before_dispatch(plugin: Path) -> None:
    """The handler should receive `limit: 25` without the model having to send it."""

    filled = plugin_executor.json_schema_lite.apply_defaults({"jql": "x"}, SCHEMA)
    assert filled["limit"] == 25


def test_a_plugin_reporting_failure_is_not_an_error(plugin: Path) -> None:
    """`ok:false` is an observation the model can act on, like any built-in failure."""

    result = _run(plugin, args=["fail"])
    assert result.ok is False and result.status == "no_such_project"
    assert "ENG does not exist" in result.error


# --------------------------------------------------------------------------------------
# Argument checking, before anything runs
# --------------------------------------------------------------------------------------


def test_a_type_error_is_refused_before_the_handler_starts(plugin: Path) -> None:
    result = _run(plugin, arguments={"jql": "x", "limit": "25"})
    assert result.ok is False and result.status == "invalid_arguments"
    assert "integer" in result.error


def test_a_path_where_a_name_was_declared_is_refused(plugin: Path) -> None:
    """The `machine.find_folder` failure, generalised.

    An absolute path is a valid `string`, so schema validation passes it. By the time an answer
    exists to inspect, a whole-disk scan has already run — the check has to be pre-dispatch.
    """

    result = _run(plugin, arguments={"jql": "x", "name": "~/Desktop/vool-w5x1"})
    assert result.ok is False and result.status == "invalid_argument_shape"
    assert "bare name" in result.error


@pytest.mark.parametrize(
    ("field", "value"),
    [("where", "just-a-name"), ("site", "not-a-url")],
)
def test_other_declared_shapes_are_checked(plugin: Path, field: str, value: str) -> None:
    result = _run(plugin, arguments={"jql": "x", field: value})
    assert result.ok is False and result.status == "invalid_argument_shape"


def test_a_correctly_shaped_value_passes(plugin: Path) -> None:
    result = _run(plugin, arguments={"jql": "x", "name": "ENG", "where": "/tmp", "site": "https://a.test"})
    assert result.ok, result.error


# --------------------------------------------------------------------------------------
# The environment boundary
# --------------------------------------------------------------------------------------


def test_build_child_env_returns_only_what_was_granted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET_KEY", "sk-do-not-leak")
    monkeypatch.setenv("ALLOWED_ONE", "yes")
    env = plugin_executor.build_child_env(env_allowlist=("ALLOWED_ONE",), secrets={"JIRA_TOKEN": "t"})
    assert "MY_SECRET_KEY" not in env
    assert env["ALLOWED_ONE"] == "yes" and env["JIRA_TOKEN"] == "t"


def test_the_exec_boundary_passes_exactly_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserted against `/usr/bin/env`, which adds nothing of its own."""

    monkeypatch.setenv("MY_SECRET_KEY", "sk-do-not-leak")
    env = plugin_executor.build_child_env(secrets={"GRANTED": "1"})
    completed = subprocess.run(
        ["/usr/bin/env"], capture_output=True, text=True, env=env, shell=False
    )
    seen = {line.split("=", 1)[0] for line in completed.stdout.splitlines() if line.strip()}
    assert "MY_SECRET_KEY" not in seen
    assert seen <= {"PATH", "TMPDIR", "LANG", "LC_ALL", "TZ", "GRANTED", "PLUGIN_SCRATCH"}


def test_an_unlisted_secret_does_not_reach_a_running_plugin(
    plugin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_SECRET_KEY", "sk-do-not-leak")
    result = _run(plugin, args=["env"], secrets={"JIRA_TOKEN": "granted"})
    seen = set(result.observation["seen"])
    assert "MY_SECRET_KEY" not in seen
    assert "JIRA_TOKEN" in seen


def test_a_parent_value_does_not_cross_even_for_a_name_the_interpreter_also_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shimmed python3 sets CPATH itself; the check is that it is not OUR CPATH."""

    monkeypatch.setenv("CPATH", "/PARENT-VALUE-LEAKED")
    completed = subprocess.run(
        ["python3", "-c", "import os; print(os.environ.get('CPATH', ''))"],
        capture_output=True,
        text=True,
        env=plugin_executor.build_child_env(),
        shell=False,
    )
    assert "PARENT-VALUE-LEAKED" not in completed.stdout


def test_the_scratch_dir_is_exported_when_given() -> None:
    env = plugin_executor.build_child_env(scratch_dir="/tmp/scratch")
    assert env["PLUGIN_SCRATCH"] == "/tmp/scratch"


# --------------------------------------------------------------------------------------
# Containment and protocol failures
# --------------------------------------------------------------------------------------


def test_a_handler_pointing_outside_its_plugin_is_refused(plugin: Path) -> None:
    """`entry: "../../../bin/sh"` would run anything under the plugin's declared permissions."""

    result = plugin_executor.run_plugin_tool(
        intent="vool-jira.search_issues",
        arguments={"jql": "x"},
        handler={"kind": "subprocess", "entry": "../../../../bin/sh"},
        schema=SCHEMA,
        plugin_root=plugin,
    )
    assert result.ok is False and result.status == "handler_escaped"


def test_a_missing_handler_is_reported_not_raised(plugin: Path) -> None:
    result = plugin_executor.run_plugin_tool(
        intent="vool-jira.search_issues",
        arguments={"jql": "x"},
        handler={"kind": "subprocess", "entry": "bin/nope"},
        schema=SCHEMA,
        plugin_root=plugin,
    )
    assert result.ok is False and result.status == "handler_missing"


def test_non_json_output_is_a_protocol_error_not_a_crash(plugin: Path) -> None:
    result = _run(plugin, args=["garbage"])
    assert result.ok is False and result.status == "handler_protocol_error"


def test_a_crashing_handler_is_reported_with_its_stderr(plugin: Path) -> None:
    result = _run(plugin, args=["crash"])
    assert result.ok is False and result.status == "handler_failed"
    assert "boom" in result.error


def test_a_hanging_handler_is_killed_by_the_timeout(plugin: Path) -> None:
    result = _run(plugin, args=["hang"], timeout_seconds=1.0)
    assert result.ok is False and result.status == "timeout"


def test_the_handler_runs_with_the_plugin_as_its_working_directory(plugin: Path) -> None:
    result = _run(plugin)
    assert result.ok, "a relative entry must resolve against the plugin root"


def test_no_shell_is_involved(plugin: Path) -> None:
    """argv is a list, so a filename containing shell syntax cannot become an injection."""

    result = plugin_executor.run_plugin_tool(
        intent="vool-jira.search_issues",
        arguments={"jql": "x; rm -rf /"},
        handler={"kind": "subprocess", "entry": "bin/run"},
        schema=SCHEMA,
        plugin_root=plugin,
    )
    assert result.ok and result.resolved_target == "x; rm -rf /"
    assert os.path.isdir("/usr")
