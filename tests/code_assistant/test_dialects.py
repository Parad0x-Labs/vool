"""Dialect normalization: cloud-native and local-text tool dialects compile to the ONE canonical
call, and twin engagements -- the same intent spoken in each dialect -- drive the ONE runtime to
identical journal states and identical bytes."""
from __future__ import annotations

import json

import pytest

from core.code_assistant.contract import CodeAssistantProposal, ContractRefused, validate_proposal
from core.code_assistant.dialects import (
    DIALECT_CLOUD,
    DIALECT_LOCAL,
    canonical_call,
    parse_model_output,
    proposal_from_cloud_call,
    proposal_from_local_text,
)


def test_cloud_and_local_mint_identical_proposals():
    cloud = parse_model_output(
        {
            "tool_calls": [
                {
                    "call_id": "cloud-1",
                    "name": "workspace.read_file",
                    "arguments": {"path": "stats.py"},
                    "rationale": "read the owner",
                }
            ]
        },
        stage="identify_owner",
    )
    local = parse_model_output(
        "Reading the owner.\n"
        + json.dumps(
            {
                "tool": "workspace.read_file",
                "arguments": {"path": "stats.py"},
                "rationale": "read the owner",
                "call_id": "local-1",
            }
        ),
        stage="identify_owner",
    )
    assert cloud.dialect == DIALECT_CLOUD and local.dialect == DIALECT_LOCAL
    assert cloud.canonical_call() == local.canonical_call() == {
        "intent": "workspace.read_file",
        "arguments": {"path": "stats.py"},
    }
    assert canonical_call(
        {"tool_calls": [{"call_id": "c", "name": "workspace.read_file", "arguments": {"path": "stats.py"}}]},
        stage="identify_owner",
    ) == canonical_call(
        json.dumps({"tool": "workspace.read_file", "arguments": {"path": "stats.py"}}), stage="identify_owner"
    )


def test_canonical_call_is_a_code_task_payload():
    """The normalized pair is exactly what a code.task.propose / code.task.step call carries."""
    call = canonical_call(
        {
            "tool_calls": [
                {
                    "name": "workspace.replace_in_file",
                    "arguments": {"path": "stats.py", "old_text": "a", "new_text": "b"},
                }
            ]
        },
        stage="fix",
        rationale="sort before indexing",
    )
    assert set(call) == {"intent", "arguments"}
    assert validate_proposal(CodeAssistantProposal(**call, stage="fix", rationale="sort before indexing"))


def test_local_dialect_tolerates_fenced_prose():
    proposal = proposal_from_local_text(
        "```json\n" + json.dumps({"tool": "workspace.read_file", "arguments": {"path": "stats.py"}}) + "\n```"
    )
    assert proposal.intent == "workspace.read_file"


def test_local_text_without_a_tool_object_is_refused():
    with pytest.raises(ContractRefused):
        proposal_from_local_text("I think the defect is in stats.py, let me fix it.")


def test_unknown_tool_in_any_dialect_is_refused():
    with pytest.raises(ContractRefused):
        proposal_from_cloud_call({"name": "machine.reformat_disk", "arguments": {}})
    with pytest.raises(ContractRefused):
        proposal_from_local_text(json.dumps({"tool": "machine.reformat_disk", "arguments": {}}))


def test_escape_paths_are_refused_in_both_dialects():
    for mint in (
        lambda: proposal_from_cloud_call({"name": "workspace.write_file", "arguments": {"path": "../x", "content": "y"}}),
        lambda: proposal_from_local_text(json.dumps({"tool": "workspace.write_file", "arguments": {"path": "../x", "content": "y"}})),
    ):
        with pytest.raises(ContractRefused) as caught:
            mint()
        assert caught.value.reason == "unconfined_path"


def test_parallel_cloud_calls_are_refused_one_at_a_time():
    with pytest.raises(ContractRefused):
        parse_model_output(
            {
                "tool_calls": [
                    {"name": "workspace.read_file", "arguments": {"path": "a"}},
                    {"name": "workspace.read_file", "arguments": {"path": "b"}},
                ]
            }
        )


def test_string_arguments_in_cloud_envelope_are_parsed():
    proposal = proposal_from_cloud_call(
        {"name": "workspace.read_file", "arguments": json.dumps({"path": "stats.py"})}
    )
    assert proposal.arguments == {"path": "stats.py"}


# ---------------------------------------------------------------- twin engagements


def test_twin_engagements_produce_identical_journals_and_bytes(tmp_path, monkeypatch):
    """The dialect a model speaks may not change what executes: two disposable workspaces, the
    SAME root-cause journey proposed once per dialect through the production door, and the two
    task journals and the two trees land byte-identical (module docstrings aside)."""
    import hashlib

    from core.blackbox import store as store_module
    from core.code_assistant.fixture import (
        DEFECT_OLD_TEXT,
        DEFECT_STATS_PY,
        FIX_NEW_TEXT,
        NARROW_TEST_COMMAND,
        OWNER_PATH,
        REGRESSION_COMMAND,
    )
    from core.mode_permission_policy import reset_mode_permission_state, set_active_mode

    summaries: list[dict] = []
    for dialect, _raw in (
        ("cloud", {"tool_calls": None}),  # placeholder; calls built below
        ("local", None),
    ):
        root = tmp_path / f"twin-{dialect}"
        root.mkdir()
        (root / OWNER_PATH).write_text(DEFECT_STATS_PY, encoding="utf-8")
        (root / "conftest.py").write_text("# twin repo\n", encoding="utf-8")
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_stats.py").write_text(
            "from stats import median\n\n\ndef test_median():\n    assert median([5, 1, 3]) == 3\n",
            encoding="utf-8",
        )
        import os
        import subprocess as sp

        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"]):
            completed = sp.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
                timeout=30,
            )
            assert completed.returncode == 0, completed.stderr
        store = tmp_path / f"store-{dialect}"
        monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(store))
        monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(store / "code_tasks"))
        store_module.reset_default_store()
        reset_mode_permission_state()
        set_active_mode("twin", "auto")
        ctx = {
            "workspace": str(root),
            "workspace_root": str(root),
            "session_id": "twin",
            "surface": "api",
            "operating_mode": "auto",
        }

        from core.runtime_execution_tools import execute_runtime_tool

        opened = execute_runtime_tool("code.task.open", {"objective": "twin"}, source_context=ctx)
        task_id = opened.details["task_id"]

        def raw_call(step_id: str, intent: str, arguments: dict, *, dialect=dialect):
            """The same proposal, emitted in THIS dialect's wire shape."""
            if dialect == "cloud":
                return {"tool_calls": [{"call_id": step_id, "name": intent, "arguments": arguments}]}
            return json.dumps({"tool": intent, "arguments": arguments, "call_id": step_id})

        def propose_step(step_id: str, intent: str, arguments: dict, *, needs_approval: bool = False, dialect=dialect, task_id=task_id, ctx=ctx) -> None:
            """The same proposal, emitted in THIS dialect, normalized, then proposed, approved
            (mutations only) and executed as a step -- the full canonical path."""
            call = canonical_call(raw_call(step_id, intent, arguments), stage="fix", rationale="dialect twin")
            from core.runtime_execution_tools import execute_runtime_tool

            if needs_approval:
                proposal = execute_runtime_tool(
                    "code.task.propose",
                    {
                        "task_id": task_id,
                        "proposal_id": step_id,
                        "intent": call["intent"],
                        "arguments": call["arguments"],
                        "rationale": f"Owner {OWNER_PATH}: median indexes the midpoint without sorting.",
                    },
                    source_context=ctx,
                )
                assert proposal.ok, (dialect, step_id, proposal.response_text)
                approved = execute_runtime_tool(
                    "code.task.approve", {"task_id": task_id, "proposal_id": step_id}, source_context=ctx
                )
                assert approved.ok, (dialect, step_id, approved.response_text)
            result = execute_runtime_tool(
                "code.task.step",
                {"task_id": task_id, "step_id": step_id, "intent": call["intent"], "arguments": call["arguments"]},
                source_context=ctx,
            )
            assert result.ok, (dialect, step_id, result.response_text)

        # reproduce
        repro = canonical_call(
            (
                {"tool_calls": [{"call_id": "r", "name": "workspace.run_tests", "arguments": {"command": NARROW_TEST_COMMAND}}]}
                if dialect == "cloud"
                else json.dumps({"tool": "workspace.run_tests", "arguments": {"command": NARROW_TEST_COMMAND}})
            ),
            stage="reproduce",
        )
        result = execute_runtime_tool(
            "code.task.step",
            {"task_id": task_id, "step_id": "r", "intent": repro["intent"], "arguments": repro["arguments"]},
            source_context=ctx,
        )
        assert result.details["tool_result"]["success"] is False
        execute_runtime_tool(
            "code.task.identify",
            {"task_id": task_id, "path": OWNER_PATH, "line": 6, "reason": "unsorted"},
            source_context=ctx,
        )
        propose_step("read", "workspace.read_file", {"path": OWNER_PATH})
        propose_step(
            "mutate",
            "workspace.replace_in_file",
            {
                "path": OWNER_PATH,
                "old_text": DEFECT_OLD_TEXT,
                "new_text": FIX_NEW_TEXT,
            },
            needs_approval=True,
        )
        for step_id, command in (("narrow", NARROW_TEST_COMMAND), ("cumulative", REGRESSION_COMMAND)):
            call = canonical_call(
                (
                    {"tool_calls": [{"call_id": step_id, "name": "workspace.run_tests", "arguments": {"command": command}}]}
                    if dialect == "cloud"
                    else json.dumps({"tool": "workspace.run_tests", "arguments": {"command": command}})
                ),
                stage="verify",
            )
            result = execute_runtime_tool(
                "code.task.step",
                {"task_id": task_id, "step_id": step_id, "intent": call["intent"], "arguments": call["arguments"]},
                source_context=ctx,
            )
            assert result.details["tool_result"]["success"] is True, (dialect, step_id)
        diff_call = canonical_call(
            (
                {"tool_calls": [{"call_id": "diff", "name": "workspace.git_diff", "arguments": {}}]}
                if dialect == "cloud"
                else json.dumps({"tool": "workspace.git_diff", "arguments": {}})
            ),
            stage="report",
        )
        diff = execute_runtime_tool(
            "code.task.step",
            {"task_id": task_id, "step_id": "diff", "intent": diff_call["intent"], "arguments": diff_call["arguments"]},
            source_context=ctx,
        )
        assert diff.ok and diff.details["stage"] == "report", (dialect, diff.status)
        report = execute_runtime_tool("code.task.report", {"task_id": task_id}, source_context=ctx)
        assert report.details["verdict"] == "completed", (dialect, report.details["unresolved"])
        summaries.append(
            {
                "verdict": report.details["verdict"],
                "files_changed": report.details["files_changed"],
                "stage": json.loads(
                    (store / "code_tasks" / f"{task_id}.json").read_text(encoding="utf-8")
                )["stage"],
                "bytes": hashlib.sha256((root / OWNER_PATH).read_bytes()).hexdigest(),
                "commands": [c["returncode"] for c in report.details["commands_run"]],
            }
        )
    reset_mode_permission_state()
    store_module.reset_default_store()
    assert summaries[0]["verdict"] == summaries[1]["verdict"] == "completed"
    assert summaries[0]["files_changed"] == summaries[1]["files_changed"] == [OWNER_PATH]
    assert summaries[0]["stage"] == summaries[1]["stage"] == "report"
    assert summaries[0]["bytes"] == summaries[1]["bytes"]
    assert summaries[0]["commands"] == summaries[1]["commands"]
