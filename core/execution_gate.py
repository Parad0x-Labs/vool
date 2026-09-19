import contextlib
import contextvars
import shlex
from dataclasses import dataclass
from typing import Any

from core import policy_engine
from core.user_preferences import load_preferences

_AUTONOMY_LEVELS = {"auto", "balanced", "strict", "hands_off"}

# A per-TURN autonomy the request can set to override the persisted preference for THIS turn only:
# the "Auto" composer mode sets it. "auto" means the owner has opted in, for this turn, to run routine
# LOCAL actions without a prompt -- outward-facing / privacy-sensitive actions still confirm (see
# _requires_explicit_approval). Approvals themselves are controller-owned and fingerprinted
# (core.mode_permission_policy); nothing here grants one.
# It is a contextvar, so it is scoped to the turn's async context and never leaks across requests.
_REQUEST_AUTONOMY_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "vool_request_autonomy_override", default=None
)


def set_request_autonomy_override(value: str | None) -> contextvars.Token:
    """Set the per-turn autonomy override (call in a try/…/finally reset around the turn)."""
    norm = str(value or "").strip().lower()
    return _REQUEST_AUTONOMY_OVERRIDE.set(norm if norm in _AUTONOMY_LEVELS else None)


def reset_request_autonomy_override(token: contextvars.Token) -> None:
    with contextlib.suppress(Exception):
        _REQUEST_AUTONOMY_OVERRIDE.reset(token)


def effective_autonomy_mode() -> str:
    """The autonomy in force for this turn: the per-turn override if set, else the saved preference."""
    override = _REQUEST_AUTONOMY_OVERRIDE.get()
    if override in _AUTONOMY_LEVELS:
        return override
    return str(getattr(load_preferences(), "autonomy_mode", "hands_off") or "hands_off").strip().lower()


@dataclass
class GateDecision:
    mode: str                     # blocked, advice_only, simulate_only, sandbox, execute
    reason: str
    requires_user_approval: bool
    allowed_actions: list[str]

# Git subcommands that genuinely change nothing on disk or in refs. Everything else is treated as
# destructive (see _is_destructive_command), so an unknown or newly-added git verb fails CLOSED.
_GIT_READ_ONLY_SUBCOMMANDS = frozenset({
    "status", "diff", "show", "log", "rev-parse", "grep", "ls-files", "ls-tree", "ls-remote",
    "for-each-ref", "describe", "blame", "cat-file", "shortlog", "count-objects", "whatchanged",
    "rev-list", "name-rev", "check-ignore", "version", "help",
})
# Flags that turn `git branch` from a listing into a mutation (delete / rename / move / force).
_GIT_BRANCH_MUTATING_FLAGS = frozenset({
    "-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force",
    "--set-upstream-to", "--unset-upstream", "--edit-description",
})


def _git_is_read_only(argv: list[str]) -> bool:
    """True only for git invocations that provably do not mutate the repository.

    `branch` was previously listed as unconditionally read-only, which classified
    `git branch -D <name>` as a safe read -- it deleted a branch with no approval prompt.
    """
    if len(argv) < 2:
        return False
    subcommand = str(argv[1] or "").strip().lower()
    tokens = [str(item or "").strip() for item in argv[2:] if str(item or "").strip()]
    if subcommand == "branch":
        # A listing (`git branch`, `git branch -a -v`) is read-only. A mutating flag, or any
        # positional argument (which creates or targets a branch), is not.
        if any(token in _GIT_BRANCH_MUTATING_FLAGS for token in tokens):
            return False
        return all(token.startswith("-") for token in tokens)
    return subcommand in _GIT_READ_ONLY_SUBCOMMANDS


class ExecutionGate:
    """
    V2: The Hard Wall.
    No execution passes without explicitly fulfilling the 9 safety checks.
    """

    @staticmethod
    def _contains_blocked_risk(risk_flags: list[str]) -> bool:
        blocked = {
            "destructive_command",
            "privileged_action",
            "persistence_attempt",
            "exfiltration_hint",
            "shell_injection_risk",
            "raw_remote_instruction"
        }
        return any(flag in blocked for flag in risk_flags)

    @staticmethod
    def _actions_within_workspace(actions: list[dict]) -> bool:
        # In V2, we enforce a basic path traversal check here, augmented by filesystem_guard later
        for act in actions:
            cmd = act.get("cmd", "")
            if "cd /" in cmd or "cd \\" in cmd or "../.." in cmd:
                return False
        return True

    @staticmethod
    def _has_valid_capability(cap: str) -> bool:
        # For MVP, capabilities are disabled by default
        return False

    @staticmethod
    def local_action_guardrails(
        action_name: str,
        *,
        destructive: bool,
    ) -> dict[str, bool]:
        normalized = str(action_name or "").strip().lower()
        outward_facing = normalized in {"discord_post", "telegram_send"}
        privacy_sensitive = normalized in {"discord_post", "telegram_send"}
        return {
            "destructive": bool(destructive),
            "outward_facing": outward_facing,
            "privacy_sensitive": privacy_sensitive,
        }

    @staticmethod
    def evaluate_local_action(
        action_name: str,
        *,
        destructive: bool,
        user_approved: bool,
        reads_workspace: bool = False,
        writes_workspace: bool = False,
    ) -> GateDecision:
        guardrails = ExecutionGate.local_action_guardrails(
            action_name,
            destructive=destructive,
        )
        if not policy_engine.get("execution.allow_safe_local_actions", True):
            return GateDecision(
                mode="advice_only",
                reason="Safe local actions are disabled by policy.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        allowed = set(policy_engine.get("execution.allowed_safe_local_actions", []) or [])
        if action_name not in allowed:
            return GateDecision(
                mode="blocked",
                reason=f"Action '{action_name}' is not in the allowed local action set.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        if (
            (
                guardrails["destructive"]
                or guardrails["outward_facing"]
                or guardrails["privacy_sensitive"]
            )
            and policy_engine.get("execution.require_explicit_user_approval_for_execution", True)
            and not user_approved
            and ExecutionGate._requires_explicit_approval(
                action_name,
                destructive=guardrails["destructive"],
                outward_facing=guardrails["outward_facing"],
                privacy_sensitive=guardrails["privacy_sensitive"],
            )
        ):
            if guardrails["outward_facing"] or guardrails["privacy_sensitive"]:
                reason = "Outward-facing or privacy-sensitive action requires explicit user approval."
            else:
                reason = "Execution requires explicit user approval."
            return GateDecision(
                mode="advice_only",
                reason=reason,
                requires_user_approval=True,
                allowed_actions=[],
            )

        if writes_workspace and not destructive and not policy_engine.get("filesystem.allow_write_workspace", False):
            return GateDecision(
                mode="advice_only",
                reason="Write action blocked by filesystem policy.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        if reads_workspace and not policy_engine.get("filesystem.allow_read_workspace", True):
            return GateDecision(
                mode="blocked",
                reason="Read action blocked by filesystem policy.",
                requires_user_approval=False,
                allowed_actions=[],
            )

        return GateDecision(
            mode="execute",
            reason="Bounded local action allowed.",
            requires_user_approval=False,
            allowed_actions=[action_name],
        )

    @staticmethod
    def _requires_explicit_approval(
        action_name: str,
        *,
        destructive: bool = False,
        outward_facing: bool = False,
        privacy_sensitive: bool = False,
    ) -> bool:
        autonomy_mode = effective_autonomy_mode()
        # Outward-facing (posting / DMs) and privacy-sensitive actions ALWAYS confirm -- even under
        # "auto". A raised autonomy is for the owner's own LOCAL actions; it must never silently send
        # on their behalf or touch private data. (email.send and wallet.spend carry their own approve
        # flags on top of this.)
        if outward_facing or privacy_sensitive:
            return True
        if autonomy_mode == "auto":
            return False  # owner opted in for this turn/chat -> routine local actions run without a prompt
        if autonomy_mode == "strict":
            return True
        if autonomy_mode == "balanced":
            return destructive or action_name in {
                "cleanup_temp_files",
                "move_path",
                "schedule_calendar_event",
                "discord_post",
                "telegram_send",
            }
        return action_name in {"cleanup_temp_files", "move_path", "discord_post", "telegram_send"}

    @staticmethod
    def evaluate_command(cmd: str) -> dict[str, str]:
        # M5 SLICE 3 — every command outcome emits a typed EffectReceipt onto
        # the turn's one ledger: sandbox -> allowed; blocked/advice_only ->
        # denied; simulate_only -> simulated (a third, mechanically distinct
        # outcome — not executed, not refused). The decision logic is
        # unchanged; the receipt is additive.
        #
        # R2: this door CONSUMES the turn's one frozen policy rather than
        # re-deriving turn state of its own, and a policy it cannot consult
        # denies the command instead of letting it through.
        from core.effect_gateway import (
            DECISION_ALLOWED,
            DECISION_DENIED,
            DECISION_SIMULATED,
            EFFECT_COMMAND,
            EffectReceipt,
            consume_turn_policy,
            record_effect_receipt,
        )
        from core.remote_fetch_policy import _utcnow_iso

        def _receipted(
            verdict: str, result: dict[str, str], *, mode: str = ""
        ) -> dict[str, str]:
            record_effect_receipt(
                EffectReceipt(
                    effect_class=EFFECT_COMMAND,
                    decision=verdict,
                    reason=str(result.get("reason") or ""),
                    host="",
                    mode=mode,
                    decided_by="execution_gate.evaluate_command",
                    recorded_at=_utcnow_iso(),
                )
            )
            return result

        try:
            policy = consume_turn_policy("execution_gate.evaluate_command")
        except Exception as exc:
            return _receipted(
                DECISION_DENIED,
                {
                    "decision": "blocked",
                    "reason": (
                        "The turn's permission policy could not be consulted, so the "
                        f"command failed closed: {type(exc).__name__}: {exc}"
                    ),
                },
            )
        mode = policy.mode

        text = str(cmd or "").strip()
        if not text:
            return _receipted(
                DECISION_DENIED,
                {"decision": "blocked", "reason": "Empty command."},
                mode=mode,
            )
        lowered = text.lower()
        for marker in ("rm -rf", "del /f", "format ", "shutdown", "reboot", "mkfs", "powershell -enc"):
            if marker in lowered:
                return _receipted(
                    DECISION_DENIED,
                    {"decision": "blocked", "reason": "Command contains blocked destructive markers."},
                    mode=mode,
                )
        if any(marker in text for marker in ("&&", "||", ";", "|", "`", "$(", "\n")):
            return _receipted(
                DECISION_DENIED,
                {"decision": "blocked", "reason": "Compound shell syntax is not allowed in sandbox commands."},
                mode=mode,
            )
        try:
            argv = shlex.split(text, posix=True)
        except ValueError:
            return _receipted(
                DECISION_DENIED,
                {"decision": "blocked", "reason": "Command could not be parsed safely."},
                mode=mode,
            )
        if not argv:
            return _receipted(
                DECISION_DENIED,
                {"decision": "blocked", "reason": "Empty command."},
                mode=mode,
            )
        base_cmd = ExecutionGate._base_command(argv)
        destructive = ExecutionGate._is_destructive_command(base_cmd, argv, lowered)
        read_only = ExecutionGate._is_read_only_command(base_cmd, argv)
        writes_workspace = not read_only
        if not policy_engine.get("execution.allow_sandbox_execution", False):
            if policy_engine.get("execution.allow_simulation", True):
                return _receipted(
                    DECISION_SIMULATED,
                    {
                        "decision": "simulate_only",
                        "reason": "Sandbox disabled; simulation allowed.",
                        "base_command": base_cmd,
                        "destructive": str(destructive).lower(),
                        "read_only": str(read_only).lower(),
                    },
                    mode=mode,
                )
            return _receipted(
                DECISION_DENIED,
                {
                    "decision": "advice_only",
                    "reason": "Execution disabled by policy.",
                    "base_command": base_cmd,
                    "destructive": str(destructive).lower(),
                    "read_only": str(read_only).lower(),
                },
                mode=mode,
            )
        if writes_workspace and not policy_engine.get("filesystem.allow_write_workspace", False):
            return _receipted(
                DECISION_DENIED,
                {
                    "decision": "advice_only",
                    "reason": "Workspace writes are disabled by filesystem policy.",
                    "base_command": base_cmd,
                    "destructive": str(destructive).lower(),
                    "read_only": str(read_only).lower(),
                },
                mode=mode,
            )
        if read_only and not policy_engine.get("filesystem.allow_read_workspace", True):
            return _receipted(
                DECISION_DENIED,
                {
                    "decision": "blocked",
                    "reason": "Workspace reads are disabled by filesystem policy.",
                    "base_command": base_cmd,
                    "destructive": str(destructive).lower(),
                    "read_only": str(read_only).lower(),
                },
                mode=mode,
            )
        requires_approval = ExecutionGate._command_requires_approval(
            base_cmd,
            read_only=read_only,
            destructive=destructive,
            autonomy_mode=policy.autonomy_mode,
        )
        if not requires_approval and not read_only:
            # Operating-mode authority has the final word on side effects. The turn's
            # mode (Manual/Review-edits by default) requires approval for
            # side-effecting commands in MODE_PERMISSION_MATRIX; the autonomy
            # preference's hands_off convenience (for interpreters, builds, anything
            # absent from a destructive-marker list) must not bypass that promise.
            # "Hands-off — ask before acting" now means what it says: only proven
            # read-only commands run unprompted outside Auto mode.
            from core.mode_permission_policy import (
                MODE_PERMISSION_MATRIX,
                OperatingMode,
                PermissionEffect,
                _command_actions,
            )

            try:
                operating_mode = OperatingMode(str(getattr(policy, "mode", "") or "").strip().lower())
            except ValueError:
                operating_mode = OperatingMode.MANUAL
            actions = _command_actions(text)
            if PermissionEffect.REQUIRE_APPROVAL in [
                MODE_PERMISSION_MATRIX[operating_mode][action] for action in actions
            ] or PermissionEffect.DENY in [
                MODE_PERMISSION_MATRIX[operating_mode][action] for action in actions
            ]:
                requires_approval = True
        if requires_approval:
            return _receipted(
                DECISION_DENIED,
                {
                    "decision": "advice_only",
                    "reason": "Command requires explicit user approval.",
                    "base_command": base_cmd,
                    "destructive": str(destructive).lower(),
                    "read_only": str(read_only).lower(),
                },
                mode=mode,
            )
        return _receipted(
            DECISION_ALLOWED,
            {
                "decision": "sandbox",
                "reason": "Sandbox execution allowed.",
                "base_command": base_cmd,
                "destructive": str(destructive).lower(),
                "read_only": str(read_only).lower(),
            },
            mode=mode,
        )

    @staticmethod
    def _base_command(argv: list[str]) -> str:
        if not argv:
            return ""
        first = str(argv[0] or "").strip().lower()
        if first == "env":
            index = 1
            while index < len(argv):
                token = str(argv[index] or "").strip()
                if "=" in token and not token.startswith("-"):
                    index += 1
                    continue
                return token.lower()
        return first

    @staticmethod
    def _is_read_only_command(base_cmd: str, argv: list[str]) -> bool:
        readonly_bases = {
            "ls",
            "dir",
            "pwd",
            "cat",
            "type",
            "head",
            "tail",
            "wc",
            "rg",
            "grep",
        }
        if base_cmd in readonly_bases:
            return True
        if base_cmd == "find":
            # `find` is read-only ONLY when none of its mutating primaries (-delete, -exec,
            # -ok, ...) are present. This used to be unconditionally read-only here, which let
            # `find <dir> -delete` run with no approval at this layer -- defer to the classifier
            # in mode_permission_policy, which already gets this right, instead of maintaining a
            # second flag list that can drift out of sync with it (confirmed red-team finding,
            # 2026-08-04).
            from core.mode_permission_policy import command_is_read_only

            return command_is_read_only(shlex.join(argv))
        if base_cmd == "sed":
            return "-i" not in argv and not any(str(item or "").startswith("-i") for item in argv)
        if base_cmd == "git":
            return _git_is_read_only(argv)
        if base_cmd in {"pytest", "python", "python3", "node", "nodejs", "npm", "pnpm", "yarn", "cargo"}:
            return False
        return False

    @staticmethod
    def _is_destructive_command(base_cmd: str, argv: list[str], lowered: str) -> bool:
        if base_cmd in {"rm", "del", "format", "mkfs", "shutdown", "reboot"}:
            return True
        if base_cmd == "find":
            # `find ... -delete`/`-exec rm {} \;`/etc. deletes or arbitrarily mutates real files,
            # same as `rm` -- it must always require approval (see `_command_requires_approval`,
            # which lets `destructive=True` override every autonomy mode), not just fall through
            # to whatever the current autonomy mode happens to allow unattended.
            from core.mode_permission_policy import FIND_MUTATING_FLAGS

            return any(str(item or "").lower() in FIND_MUTATING_FLAGS for item in argv[1:])
        if base_cmd == "git":
            # DENY BY DEFAULT. This used to be an allowlist of destructive verbs, so everything absent
            # from it ran unattended: `git stash`, `git stash clear`, `git branch -D`, `git reflog
            # expire`, `git update-ref -d`, `git worktree remove`, `git filter-branch`, `git push
            # --force`. An audit destroyed uncommitted work on a scratch repo with exactly that set --
            # stash cleared and reflog expired, so it was unrecoverable. Anything that is not provably
            # read-only is destructive, which also makes new/unknown git verbs fail closed.
            return not _git_is_read_only(argv)
        destructive_markers = ("sudo ", "launchctl", "systemctl", "crontab", "defaults write", "diskutil", "fdisk")
        return any(marker in lowered for marker in destructive_markers)

    @staticmethod
    def _command_requires_approval(
        base_cmd: str,
        *,
        read_only: bool,
        destructive: bool,
        autonomy_mode: str = "",
    ) -> bool:
        # R2: the autonomy in force is the TURN's, handed down from the one
        # frozen policy. This read the persisted preference directly and so
        # ignored the per-turn override entirely -- the same door that
        # `_requires_explicit_approval` honours -- which meant the composer's
        # autonomy applied to local actions and not to commands. Outside a turn
        # there is no frozen policy, and the live effective value is used.
        autonomy_mode = str(autonomy_mode or "").strip().lower() or effective_autonomy_mode()
        if autonomy_mode == "strict":
            return not read_only
        if destructive:
            return True
        if autonomy_mode == "balanced":
            return False
        if autonomy_mode == "hands_off":
            return False
        return not read_only

    @staticmethod
    def evaluate_machine_effect(
        *,
        effect_type: str,
        resolved_path: str,
    ) -> dict:
        """
        Evaluate whether a machine-level effect (e.g. ``machine.write_file``) targeting
        the exact canonical resource identity is authorized.

        This is the canonical pre-execution seam for typed file mutations.
        It does NOT own the write mechanism; it only decides AUTHORIZED or REFUSED.

        Args:
            effect_type: The typed effect intent (``"machine.write_file"``).
            resolved_path: The canonical absolute path of the target resource after
                           resolution by the mechanism layer.

        Returns:
            A dict with keys:
              ``decision`` — ``"authorized"`` or ``"refused"``.
              ``reason``   — human-readable explanation.
        """
        # M5 SLICE 2 — the typed EFFECT RECEIPT rides this seam: authorized AND
        # refused are first-class recorded facts (the refusal previously vanished
        # into the caller's blocked response with no per-effect record). The
        # decision logic is unchanged; the receipt is additive onto the turn's
        # channel (core.effect_gateway), which the fetch scope opens per turn.
        from core.effect_gateway import (
            DECISION_ALLOWED,
            DECISION_DENIED,
            EffectReceipt,
            consume_turn_policy,
            record_effect_receipt,
        )
        from core.remote_fetch_policy import _utcnow_iso

        mode = ""

        def _receipt(decision: str, reason: str) -> None:
            record_effect_receipt(
                EffectReceipt(
                    effect_class=f"filesystem:{effect_type}",
                    decision=decision,
                    reason=reason,
                    host="",
                    mode=mode,
                    decided_by="execution_gate.evaluate_machine_effect",
                    recorded_at=_utcnow_iso(),
                )
            )

        # R2: this door consumes the turn's one frozen policy, and a policy it
        # cannot consult refuses the write instead of authorizing it.
        try:
            mode = consume_turn_policy("execution_gate.evaluate_machine_effect").mode
        except Exception as exc:
            reason = (
                "The turn's permission policy could not be consulted, so the machine "
                f"effect '{effect_type}' failed closed: {type(exc).__name__}: {exc}"
            )
            _receipt(DECISION_DENIED, reason)
            return {"decision": "refused", "reason": reason}

        # The canonical policy flag for machine-scoped effects.
        allowed = policy_engine.get("execution.allow_machine_writes", True)
        if not allowed:
            reason = (
                f"Machine effect '{effect_type}' on '{resolved_path}' is not "
                "authorized by execution policy."
            )
            _receipt(DECISION_DENIED, reason)
            return {"decision": "refused", "reason": reason}
        reason = f"Machine effect '{effect_type}' on '{resolved_path}' authorized."
        _receipt(DECISION_ALLOWED, reason)
        return {"decision": "authorized", "reason": reason}

    @staticmethod
    def evaluate(plan: Any, task: dict, persona: Any) -> GateDecision:
        """
        Evaluates the plan against the exact 9-step constraint list from the V2 Spec.
        """
        # 1. Global default: advice-only

        risk_flags = plan.risk_flags if hasattr(plan, 'risk_flags') else []
        safe_actions = plan.safe_actions if hasattr(plan, 'safe_actions') else []
        confidence = plan.confidence if hasattr(plan, 'confidence') else 0.5
        task_class = task.get("task_class", "")

        # 2. Never allow execution if plan contains blocked risk flags
        if ExecutionGate._contains_blocked_risk(risk_flags):
            return GateDecision(
                mode="blocked",
                reason="Plan contains blocked risk flags.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 3. Never allow if task is system-sensitive
        if task_class in {"privileged_system_change", "persistence_setup", "unknown_binary_action"}:
            return GateDecision(
                mode="advice_only",
                reason="System-sensitive task forced to advice-only.",
                requires_user_approval=True,
                allowed_actions=[]
            )

        # 4. If sandbox execution disabled, stay advice-only
        if not policy_engine.get("execution.allow_sandbox_execution", False):
            if policy_engine.get("execution.allow_simulation", True):
                return GateDecision(
                    mode="simulate_only",
                    reason="Sandbox disabled; simulation allowed.",
                    requires_user_approval=False,
                    allowed_actions=["simulate"]
                )
            return GateDecision(
                mode="advice_only",
                reason="Execution disabled by policy.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 5. Check confidence threshold
        if confidence < 0.85:
            return GateDecision(
                mode="advice_only",
                reason="Confidence below execution threshold.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 6. Check required capabilities (omitted MVP tokens - failing closed)
        if hasattr(plan, 'reads_workspace') and plan.reads_workspace and not ExecutionGate._has_valid_capability("READ_WORKSPACE"):
            return GateDecision(
                mode="advice_only",
                reason="Missing capability token: READ_WORKSPACE",
                requires_user_approval=True,
                allowed_actions=[]
            )

        # 7. Check file scope
        if not ExecutionGate._actions_within_workspace(safe_actions):
            return GateDecision(
                mode="blocked",
                reason="Action escapes allowed workspace.",
                requires_user_approval=False,
                allowed_actions=[]
            )

        # 8. Final user approval gate
        if policy_engine.get("execution.require_explicit_user_approval_for_execution", True):
            return GateDecision(
                mode="advice_only",
                reason="Execution requires explicit user approval.",
                requires_user_approval=True,
                allowed_actions=[]
            )

        # 9. Last resort: sandbox only
        return GateDecision(
            mode="sandbox",
            reason="Passed safety checks; sandbox allowed.",
            requires_user_approval=False,
            allowed_actions=["sandbox_execute"]
        )
