"""Guard: destructive git must never run unattended.

`destructive_git` used to be an ALLOWLIST of destructive verbs, so anything absent from it ran with
no approval -- and `branch` sat in the read-only set, classifying `git branch -D <name>` as a safe
read. An audit destroyed uncommitted work on a scratch repo with exactly that set (stash cleared and
reflog expired, so unrecoverable). Classification is now deny-by-default: not provably read-only
means destructive, so an unknown or newly-added git verb fails closed.
"""
from __future__ import annotations

import shlex

import pytest

from core.execution_gate import ExecutionGate


def _classify(command: str) -> tuple[bool, bool]:
    argv = shlex.split(command)
    return (
        ExecutionGate._is_read_only_command(argv[0], argv),
        ExecutionGate._is_destructive_command(argv[0], argv, command.lower()),
    )


# The exact commands that destroyed the audit's scratch repo, plus the rest of the unattended set.
@pytest.mark.parametrize(
    "command",
    [
        "git stash",
        "git stash clear",
        "git branch -D feature/keepme",
        "git branch --delete feature/keepme",
        "git branch -m old new",
        "git reflog expire --expire=now --all",
        "git push --force origin main",
        "git push origin main",
        "git update-ref -d refs/heads/main",
        "git worktree remove x",
        "git filter-branch --force --all",
        "git commit -am wip",
        "git switch other",
        "git rm -r --cached .",
        "git reset --hard",
        "git clean -fd",
        "git checkout other",
        "git rebase main",
        "git gc --prune=now",          # unknown-to-the-old-list verb: must fail CLOSED
        "git notes remove",            # ditto
    ],
)
def test_destructive_git_requires_approval(command):
    read_only, destructive = _classify(command)
    assert destructive is True, f"{command!r} would run unattended"
    assert read_only is False, f"{command!r} is classified read-only"


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git diff",
        "git diff --stat HEAD~1",
        "git log --oneline -5",
        "git branch",              # a listing is read-only...
        "git branch -a -v",        # ...including with listing flags
        "git rev-parse HEAD",
        "git show HEAD",
        "git ls-files",
        "git blame src/x.py",
        "git describe --tags",
        "git for-each-ref",
    ],
)
def test_read_only_git_still_runs_unattended(command):
    read_only, destructive = _classify(command)
    assert read_only is True, f"{command!r} lost read-only status -- would prompt on a plain inspection"
    assert destructive is False


def test_branch_creation_is_not_a_read():
    # `git branch newname` CREATES a branch; only a bare listing is a read.
    read_only, destructive = _classify("git branch newname")
    assert read_only is False and destructive is True


def test_gate_decision_blocks_destructive_under_the_shipped_default():
    # R2b1: an effect door denies when no turn ledger is active, so this runs
    # inside a turn scope — in production every command reaches this gate from
    # inside the agent runtime's remote_fetch_turn_scope.
    from core.remote_fetch_policy import remote_fetch_policy_scope

    with remote_fetch_policy_scope({"surface": "openclaw"}):
        assert ExecutionGate.evaluate_command("git branch -D feature/keepme")["decision"] == "advice_only"
        assert ExecutionGate.evaluate_command("git stash clear")["decision"] == "advice_only"
        assert ExecutionGate.evaluate_command("git status")["decision"] == "sandbox"


# --------------------------------------------------------------------------------------------
# `find -delete` (and the rest of find's mutating primaries) was unconditionally classified
# read-only here, independent of the `-delete`/`-exec`/`-ok` flags actually present. Combined
# with a permissive autonomy mode, this deleted real files with no approval anywhere in the
# command-layer gate. `core.mode_permission_policy` already classified this correctly; the fix
# makes this gate defer to it (`command_is_read_only`) instead of carrying a second, divergent
# flag list. Confirmed red-team finding, 2026-08-04.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "find important_data -delete",
        "find . -name '*.log' -delete",
        "find . -exec rm {} \\;",
        "find . -execdir rm {} \\;",
        "find . -ok rm {} \\;",
    ],
)
def test_find_with_mutating_primary_is_neither_read_only_nor_unattended(command):
    read_only, destructive = _classify(command)
    assert read_only is False, f"{command!r} is classified read-only"
    assert destructive is True, f"{command!r} would run unattended"
    # `-exec ... \;` also trips the compound-shell-syntax guard (a literal `;` in the string),
    # which blocks even earlier than the find-specific classification -- either outcome refuses
    # unattended execution, which is what this test actually guards.
    assert ExecutionGate.evaluate_command(command)["decision"] in {"advice_only", "blocked"}


@pytest.mark.parametrize(
    "command",
    [
        "find .",
        "find . -name '*.py'",
        "find . -type f -mtime +7",
    ],
)
def test_find_without_a_mutating_primary_still_runs_unattended(command):
    read_only, destructive = _classify(command)
    assert read_only is True, f"{command!r} lost read-only status -- would prompt on a plain search"
    assert destructive is False


def test_find_delete_is_blocked_end_to_end_through_the_sandbox_runner_under_auto_autonomy(tmp_path):
    """Reproduces the exact red-team scenario: `find -delete` under Auto autonomy.

    Before the fix this deleted real files with zero approval anywhere in the stack. After the
    fix the sandbox runner refuses to execute it and the files survive.
    """
    from core.execution_gate import reset_request_autonomy_override, set_request_autonomy_override
    from core.remote_fetch_policy import remote_fetch_policy_scope
    from sandbox.sandbox_runner import SandboxRunner

    target_dir = tmp_path / "important_data"
    target_dir.mkdir()
    victim = target_dir / "keep_me.txt"
    victim.write_text("real user data", encoding="utf-8")

    token = set_request_autonomy_override("auto")
    try:
        # R2b1: the gate consults the turn's ledger, as it always does in
        # production (the agent runtime scopes every turn).
        with remote_fetch_policy_scope({"surface": "openclaw"}):
            runner = SandboxRunner(ExecutionGate(), str(tmp_path))
            result = runner.run_command("find important_data -delete", cwd=str(tmp_path))
    finally:
        reset_request_autonomy_override(token)

    assert result.get("status") == "user_action_required", result
    assert victim.exists(), "the attack deleted a real file with no approval"
