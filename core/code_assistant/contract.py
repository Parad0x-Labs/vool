"""THE canonical proposal contract for the native coding assistant.

One typed proposal shape — minted identically by every model dialect (cloud native tool calls,
local-model text tool calls, deterministic drivers) — is the ONLY thing the coding lane executes,
and it executes in exactly one place: as a contracted ``code.task.*`` call across the production
door (``core.runtime_execution_tools.execute_runtime_tool``), where the registry, the mode/permission
matrix, the effect gateway, confinement and the Blackbox flight recorder all stand. This module is
the contract's validation half: it decides what may be PROPOSED and refuses, with typed reasons and
before any authority is consulted, what no boundary should ever have to see. It executes nothing --
the former in-process ``execute_proposal`` door was the second execution path and is retired; see
``core.code_assistant.task_runtime`` for the one runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The tool families a coding assistant may propose. Every intent already exists in the runtime
# contract registry; this package owns orchestration, not a parallel tool set. ``code.review_evidence``
# is this package's own read-only projection intent, registered at the runtime dispatch seam.
FAMILY_INTENTS: dict[str, tuple[str, ...]] = {
    "workspace_list_read_search": (
        "workspace.list_tree",
        "workspace.list_files",
        "workspace.search_text",
        "workspace.symbol_search",
        "workspace.read_file",
        "workspace.identity",
    ),
    "file_edit_create": (
        "workspace.write_file",
        "workspace.replace_in_file",
        "workspace.apply_unified_diff",
        "workspace.ensure_directory",
    ),
    "shell_command": ("sandbox.run_command",),
    "git": ("workspace.git_status", "workspace.git_diff", "workspace.git_summary"),
    "tests_lint_types": ("workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"),
    "browser_smoke": ("browser.render",),
    "review_evidence": ("code.review_evidence",),
    "rollback": ("workspace.rollback_last_change",),
}

INTENT_FAMILY: dict[str, str] = {
    intent: family for family, intents in FAMILY_INTENTS.items() for intent in intents
}

READ_ONLY_FAMILIES = frozenset(
    {"workspace_list_read_search", "git", "review_evidence", "browser_smoke"}
)

# Arguments that name filesystem locations. The runtime re-jails every one of these at resolution
# time against the trusted workspace root; the contract refuses the obvious escapes BEFORE any
# authority is consulted so a malicious proposal never spends a permission decision.
_PATH_ARGUMENT_KEYS = ("path", "cwd", "destination_path", "source_path")

REASON_UNKNOWN_INTENT = "unknown_intent"
REASON_NOT_IN_SCOPE = "intent_not_in_code_assistant_scope"
REASON_FORGED_TRUST_KEY = "forged_trust_key"
REASON_UNCONFINED_PATH = "unconfined_path"
REASON_INVALID_ARGUMENTS = "invalid_arguments"
REASON_EMPTY_COMMAND = "empty_command"


class ContractRefused(Exception):
    """A proposal that will not cross the boundary, with the typed reason it will not."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class CodeAssistantProposal:
    """The dialect-neutral unit of work: the MODEL proposes one of these; the runtime validates.

    The proposal's ``(intent, arguments)`` pair IS the canonical tool call: every dialect compiles
    down to the same pair (see ``core.code_assistant.dialects``), and the pair is what a
    ``code.task.propose`` / ``code.task.step`` call carries across the production door.
    """

    intent: str
    arguments: dict[str, Any] = field(default_factory=dict)
    stage: str = "reproduce"
    rationale: str = ""
    dialect: str = "canonical"
    origin: str = ""

    def with_stage(self, stage: str, rationale: str = "") -> CodeAssistantProposal:
        return CodeAssistantProposal(
            intent=self.intent,
            arguments=dict(self.arguments),
            stage=str(stage),
            rationale=str(rationale or self.rationale),
            dialect=self.dialect,
            origin=self.origin,
        )

    def canonical_call(self) -> dict[str, Any]:
        """The dialect-free ``(intent, arguments)`` pair, as the door receives it."""
        return {"intent": str(self.intent), "arguments": dict(self.arguments)}


def _refuse_unconfined(value: str) -> None:
    text = str(value or "").strip()
    if not text:
        return
    if text.startswith(("/", "~")) or text.startswith("\\\\"):
        raise ContractRefused(
            REASON_UNCONFINED_PATH,
            f"`{text}` is absolute or home-relative; coding-assistant paths must be workspace-relative.",
        )
    parts = [part for part in text.replace("\\", "/").split("/")]
    if any(part == ".." for part in parts):
        raise ContractRefused(
            REASON_UNCONFINED_PATH,
            f"`{text}` escapes the workspace with `..`; refused before any authority was consulted.",
        )


def validate_proposal(proposal: CodeAssistantProposal) -> str:
    """Validate one proposal against the contract. Returns the family; raises ``ContractRefused``.

    Fail-closed rules, in order: the intent must exist and belong to a coding-assistant family;
    arguments must be a plain mapping without underscore-prefixed keys (forged internal trust
    flags); path-like arguments must be relative and stay inside the workspace; command-bearing
    tools must carry a non-empty command. Validation happens AT MINT -- a dialect may never emit a
    proposal the contract would refuse -- so the refusal reason reaches the model as part of the
    feedback loop instead of as a post-execution surprise.
    """
    intent = str(proposal.intent or "").strip()
    if not intent:
        raise ContractRefused(REASON_UNKNOWN_INTENT, "A proposal must name a tool intent.")
    family = INTENT_FAMILY.get(intent)
    if family is None:
        known = ", ".join(sorted(INTENT_FAMILY))
        raise ContractRefused(
            REASON_NOT_IN_SCOPE,
            f"`{intent}` is not a coding-assistant tool. In scope: {known}.",
        )
    arguments = proposal.arguments
    if not isinstance(arguments, dict):
        raise ContractRefused(REASON_INVALID_ARGUMENTS, "Tool arguments must be a mapping.")
    for key in arguments:
        if str(key).startswith("_"):
            raise ContractRefused(
                REASON_FORGED_TRUST_KEY,
                f"Argument `{key}` is an internal trust flag; model proposals may never set one.",
            )
    for key in _PATH_ARGUMENT_KEYS:
        if key in arguments:
            _refuse_unconfined(arguments[key])
    if intent == "sandbox.run_command" and not str(arguments.get("command") or "").strip():
        raise ContractRefused(REASON_EMPTY_COMMAND, "`sandbox.run_command` needs a non-empty `command`.")
    return family


__all__ = [
    "FAMILY_INTENTS",
    "INTENT_FAMILY",
    "READ_ONLY_FAMILIES",
    "REASON_EMPTY_COMMAND",
    "REASON_FORGED_TRUST_KEY",
    "REASON_INVALID_ARGUMENTS",
    "REASON_NOT_IN_SCOPE",
    "REASON_UNCONFINED_PATH",
    "REASON_UNKNOWN_INTENT",
    "CodeAssistantProposal",
    "ContractRefused",
    "validate_proposal",
]
