"""The permission mode must mean what its UI label says.

Security review finding (P0): with the shipped defaults (autonomy
``hands_off``, operating mode ``manual``, sandbox execution and workspace
writes enabled), any command absent from the destructive-marker list ran
unprompted — a Python ``rmtree``, ``node -e``, ``touch``/``cp`` mutations —
while the chat UI labelled the mode "Hands-off — ask before acting".

The repair lives in ``core/execution_gate.evaluate_command``: the sandbox
command lane now consumes the turn's OPERATING-MODE authority
(``MODE_PERMISSION_MATRIX``). In Manual/Review-edits/Plan only proven
read-only commands run unprompted; side-effecting commands reach the
approval gate whatever the autonomy preference says. Auto keeps the
hands_off convenience explicitly.

These are hostile regressions: interpreter-launched deletions, renamed
destructive verbs, shell indirection already blocked separately, and the
read-only lane that must keep working.
"""
from __future__ import annotations

import pytest

from core.execution_gate import ExecutionGate


def _policy(mode: str = "manual", autonomy: str = "hands_off"):
    from types import SimpleNamespace

    return SimpleNamespace(mode=mode, autonomy_mode=autonomy)


@pytest.fixture(autouse=True)
def _enable_sandbox_execution(monkeypatch):
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "get", lambda key, default=None: {
        "execution.allow_sandbox_execution": True,
        "execution.allow_simulation": True,
        "filesystem.allow_write_workspace": True,
        "filesystem.allow_read_workspace": True,
    }.get(key, default))


HOSTILE_SIDE_EFFECTS = [
    # interpreter-launched destruction — the review's exact examples
    'python3 -c "__import__(\'shutil\').rmtree(\'/tmp/victim\')"',
    'python -c "__import__(\'os\').remove(\'/tmp/x\')"',
    'node -e "require(\'fs\').rmSync(\'/tmp/x\', {recursive: true})"',
    # renamed/interpreted destructive commands
    "mv important.txt /tmp/elsewhere",
    "touch newfile.py",
    "cp secret.txt leak.txt",
    "chmod +x script.sh",
    "mkdir newdir",
    "tee output.txt",
    # builders/script-runners mutate state outside a read proof
    "npm run build",
    "cargo build",
    "make all",
]


class TestHandsOffAsksBeforeActing:
    @pytest.mark.parametrize("cmd", HOSTILE_SIDE_EFFECTS)
    def test_side_effect_commands_reach_the_approval_gate(self, cmd, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="manual", autonomy="hands_off"),
        )
        verdict = ExecutionGate.evaluate_command(cmd)
        assert verdict["decision"] == "advice_only", (cmd, verdict)
        assert "approval" in verdict["reason"].lower()

    def test_find_delete_is_destructive_and_asks(self, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="manual", autonomy="hands_off"),
        )
        verdict = ExecutionGate.evaluate_command("find /tmp -name '*.tmp' -delete")
        assert verdict["decision"] == "advice_only", verdict

    def test_shell_indirection_stays_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="manual", autonomy="hands_off"),
        )
        for cmd in (
            "cat foo; rm -rf /",
            "grep x `rm -rf /tmp`",
            "ls $(find / -delete)",
        ):
            verdict = ExecutionGate.evaluate_command(cmd)
            assert verdict["decision"] == "blocked", (cmd, verdict)
        # Redirection is not in the compound-syntax block list; it must at least
        # reach the approval gate (write side effect, unknown target policy).
        assert ExecutionGate.evaluate_command("echo hi > /etc/passwd")["decision"] == "advice_only"


class TestReadOnlyLaneStillWorks:
    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "grep -r pattern .",
        "cat notes.txt",
        "git status",
        "git diff HEAD",
        "find . -name '*.py'",
        "sed -n '1,5p' file.txt",
    ])
    def test_proven_reads_run_unprompted(self, cmd, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="manual", autonomy="hands_off"),
        )
        verdict = ExecutionGate.evaluate_command(cmd)
        assert verdict["decision"] == "sandbox", (cmd, verdict)
        assert verdict["read_only"] == "true"

    def test_destructive_reads_ask(self, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="manual", autonomy="hands_off"),
        )
        # "rm -rf" hits the hard blocked-marker list — stronger than asking.
        assert ExecutionGate.evaluate_command("rm -rf /tmp/x")["decision"] in {"advice_only", "blocked"}


class TestAutoKeepsHandsOffConvenience:
    @pytest.mark.parametrize("cmd", [
        "touch newfile.py",
        "mkdir newdir",
        "npm run build",
    ])
    def test_auto_mode_allows_routine_side_effects(self, cmd, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="auto", autonomy="hands_off"),
        )
        verdict = ExecutionGate.evaluate_command(cmd)
        assert verdict["decision"] == "sandbox", (cmd, verdict)

    def test_auto_still_asks_for_destructive(self, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="auto", autonomy="hands_off"),
        )
        assert ExecutionGate.evaluate_command("find /tmp -delete")["decision"] == "advice_only"


class TestUnknownOperatingModeFailsClosed:
    def test_garbage_mode_defaults_to_manual_asking(self, monkeypatch):
        monkeypatch.setattr(
            "core.effect_gateway.consume_turn_policy",
            lambda _seam: _policy(mode="not-a-mode", autonomy="hands_off"),
        )
        verdict = ExecutionGate.evaluate_command("touch x.txt")
        assert verdict["decision"] == "advice_only", verdict
