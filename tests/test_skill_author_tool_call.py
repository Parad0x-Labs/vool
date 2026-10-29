"""A skill author can call a tool in a throwaway profile and always gets a plain result.

An outside author tried `operator.schedule_calendar_event` on a fresh profile with
`execute_runtime_tool(...)` and read `.status` off the result: AttributeError, 'NoneType'
object has no attribute 'status'. That door returns None for every tool another lane owns
(all `operator.*` tools, `web.search`). `vool-community call` makes the call a real turn
makes, `execute_tool_intent`, behind the Manual/Auto approval check, and prints one JSON line.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENT = {"title": "Dentist", "start_iso": "2026-11-03T10:00:00", "duration_minutes": 60}


def _call(tmp_path, *argv: str) -> tuple[int, dict]:
    env = {**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(ROOT)}
    env.pop("VOOL_HOME", None)
    done = subprocess.run([sys.executable, "-m", "tools.community_skills", "call", *argv],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=240)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    assert lines, (done.stdout, done.stderr)
    return done.returncode, json.loads(lines[-1])


def test_the_inner_door_returns_none_for_an_operator_tool():
    # Why authors must not use it: the None is by design ("not this door"), so the docs
    # point at execute_tool_intent and the call command instead.
    from core.runtime_execution_tools import execute_runtime_tool

    assert execute_runtime_tool("operator.list_tools", {}) is None


def test_schedule_on_a_fresh_profile_is_a_plain_approval_request(tmp_path):
    code, report = _call(tmp_path, "operator.schedule_calendar_event", "--args", json.dumps(EVENT))
    assert code == 0
    assert report["status"] == "pending_approval" and report["approval_needed"] is True
    assert "Manual mode requires approval for this exact action." in report["reply"]


def test_allow_once_runs_it_and_auto_mode_still_asks(tmp_path):
    _, approved = _call(tmp_path, "operator.schedule_calendar_event", "--args", json.dumps(EVENT), "--approve")
    assert approved["status"] == "executed" and approved["approved_once"] is True
    _, auto = _call(tmp_path, "operator.schedule_calendar_event", "--args", json.dumps(EVENT), "--mode", "auto")
    assert auto["status"] == "pending_approval"
    assert "Auto mode requires approval for this exact action." in auto["reply"]


def test_web_lookup_off_and_unknown_tool_are_plain_refusals(tmp_path):
    _, web = _call(tmp_path, "web.search", "--args", '{"query": "tea"}')
    assert web["ok"] is False and "Web lookups are off." in web["reply"]
    code, unknown = _call(tmp_path, "workspace.writefile")
    assert code == 1 and "no tool named 'workspace.writefile'" in unknown["reason"]


def test_a_write_lands_in_the_throwaway_workspace_never_the_authors_folder(tmp_path):
    _, report = _call(tmp_path, "workspace.write_file", "--args", '{"path": "a.txt", "content": "x"}',
                      "--mode", "auto")
    assert report["status"] == "executed"
    assert not (tmp_path / "a.txt").exists()


def test_seed_file_puts_a_file_in_the_throwaway_downloads(tmp_path):
    source = tmp_path / "invoice.txt"
    source.write_text("total 42\n", encoding="utf-8")
    code, report = _call(tmp_path, "machine.read_file", "--args", json.dumps({"path": "~/Downloads/invoice.txt"}),
                         "--seed-file", f"{source}:~/Downloads/invoice.txt")
    assert code == 0 and report["ok"], report
    assert "total 42" in report["reply"]
    assert not (tmp_path / "Downloads").exists(), "the author's own home is untouched"


def test_the_allowed_folders_exist_empty_and_bad_seeds_are_refused(tmp_path):
    code, report = _call(tmp_path, "machine.list_directory", "--args", json.dumps({"path": "~/Documents"}))
    assert code == 0 and report["ok"], report
    code, report = _call(tmp_path, "machine.read_file", "--args", "{}", "--seed-file", f"{tmp_path}/missing.pdf:~/Downloads/a.pdf")
    assert code == 1 and report["status"] == "refused" and "is not a file" in report["reason"]
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    code, report = _call(tmp_path, "machine.read_file", "--args", "{}", "--seed-file", f"{tmp_path}/a.pdf:/etc/a.pdf")
    assert code == 1 and report["status"] == "refused"


def test_a_home_startup_file_refusal_names_the_home_path(tmp_path):
    code, report = _call(tmp_path, "machine.write_file", "--args", json.dumps({"path": "~/.bashrc", "content": "x"}),
                         "--mode", "auto")
    assert report["status"] == "blackbox_high_risk_refused", report
    assert "/~/" not in report["reply"] and "/home/.bashrc (startup)" in report["reply"]
