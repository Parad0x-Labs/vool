"""``code.task.*`` contracts -- the coding assistant's control plane, offered from the ONE registry.

Every schema here is closed: a caller-supplied ``result``, ``tests`` or any other claim is not
in any ``input_schema``, so the door refuses it as ``invalid_arguments`` before the runtime
sees it. That is how model prose is kept from ever becoming a tool result.
"""

from __future__ import annotations

from typing import Any

_STEP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "step_id": {
            "type": "string",
            "description": "Omit for a new action to generate a fresh id. Reuse an id only to retry the exact same intent and arguments; an identical completed step replays without execution. Different actions require a new step_id.",
        },
        "intent": {
            "type": "string",
            "description": "Inner runtime tool, e.g. workspace.read_file, workspace.run_tests, workspace.write_file. Never code.task.*; the outer function already selects the task step.",
        },
        "arguments": {"type": "object", "description": "Arguments of the inner tool: read_file takes path; run_tests takes command; write_file takes path and content. Do not add an action field."},
        "rerun_reason": {
            "type": "string",
            "description": "Declare this only to RE-RUN a check whose equivalent outcome the task journal already holds: name the purpose (a bounded flakiness/order-investigation plan, an operator request) or what changed outside the journal's byte binding (fixtures, environment). The reason is journaled as declared, not as verified fact.",
        },
    },
    "required": ["task_id", "intent"],
    "additionalProperties": False,
}


def _schema(required: tuple[str, ...], **properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def code_task_contracts(*, write_enabled: bool) -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    common = dict(
        tool_surface="code_task",
        capability_id="code.task",
        capability_claim="run a root-cause coding task: reproduce, identify, propose, approve, mutate, test, diff, report",
        supported=True,
        unsupported_reason="",
        timeout_policy="inherits_inner_tool",
        retry_policy="idempotent_by_step_id",
        artifact_emission="task_journal",
        error_contract="returns_structured_error_result",
        handler="runtime",
        renders_final_answer=True,
    )
    return [
        RuntimeToolContract(
            intent="code.task.open",
            permission_actions=("read_files",),
            description="Open a coding task for an objective; returns the task_id and the enforced root-cause plan.",
            input_schema={"objective": "string"},
            output_schema={"task_id": "string", "stage": "string", "plan": "list[stage]"},
            side_effect_class="read_only",
            approval_requirement="none",
            json_schema=_schema(("objective",), objective={"type": "string"}),
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.step",
            permission_actions=("create_files", "modify_files", "run_side_effecting_commands"),
            description="Execute one contracted tool as a step of the task. Reads run at any stage; commands run at reproduce, identify, narrow_test or cumulative; a mutation runs only at the mutate stage and only if it matches an approved proposal byte-for-byte AND every file it changes still holds the content that proposal was reviewed against (a changed file refuses as stale_base; re-read and propose again). After a repair unit lands, its focused and full checks run here before the next approved unit may.",
            input_schema={
                "task_id": "string",
                "step_id": "string optional",
                "intent": "string",
                "arguments": "object optional",
                "rerun_reason": "string optional",
            },
            output_schema={
                "executed": "boolean",
                "replayed": "boolean",
                "tool_result": "object",
                "receipts": "list[receipt]",
                "stage": "string",
            },
            side_effect_class="workspace_write",
            # The byte-for-byte approved-proposal binding is enforced by CodeTaskRuntime._admit.
            # This field names which of the permission layer's three gates applies, and
            # `approved_proposal_for_mutations` was not one of them -- so nothing downstream
            # could read it at all.
            approval_requirement="runtime_policy",
            json_schema=_STEP_SCHEMA,
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.identify",
            permission_actions=("read_files",),
            description="Record the earliest owning defect (path, line, reason) after the failure is reproduced.",
            input_schema={
                "task_id": "string",
                "path": "string",
                "line": "integer optional",
                "reason": "string optional",
            },
            output_schema={"defect": "object", "stage": "string"},
            side_effect_class="read_only",
            approval_requirement="none",
            json_schema=_schema(
                ("task_id", "path"),
                task_id={"type": "string"},
                path={"type": "string"},
                line={"type": "integer"},
                reason={"type": "string"},
            ),
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.propose",
            permission_actions=("read_files",),
            description="Preview a repair (a mutating tool call) without executing it; returns the canonical files, the reviewed base of each (the content this task read or last wrote) and the content hash the approval will bind to. The proposal must carry a rationale naming the owner and the root cause, and every existing file it changes must have been read through the boundary first; an expected_hash must be exactly what that read returned. Every proposal is its own repair unit, validated by a focused and a full check before the next unit runs. A proposal_id names one request: repeating exactly that request replays it and reports its state, while a revised repair is refused as proposal_id_conflict and needs a new proposal_id.",
            input_schema={
                "task_id": "string",
                "proposal_id": "string optional",
                "intent": "string",
                "arguments": "object",
                "rationale": "string",
                "unit": "string optional",
            },
            output_schema={"proposal_id": "string", "preview": "object", "stage": "string"},
            side_effect_class="read_only",
            approval_requirement="none",
            json_schema=_schema(
                ("task_id", "intent", "arguments", "rationale"),
                task_id={"type": "string"},
                proposal_id={"type": "string"},
                intent={"type": "string"},
                arguments={"type": "object"},
                rationale={"type": "string"},
                unit={
                    "type": "string",
                    "description": "Omit for an independent repair. Only changes that cannot work apart (for example a rename and its callers) share one unit name; they land together and are validated after all of them.",
                },
            ),
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.approve",
            permission_actions=("read_files",),
            description="Approve a previewed proposal; only the byte-identical mutation over the reviewed content may then execute, once.",
            input_schema={"task_id": "string", "proposal_id": "string"},
            output_schema={"proposal_id": "string", "stage": "string"},
            side_effect_class="read_only",
            approval_requirement="none",
            json_schema=_schema(("task_id", "proposal_id"), task_id={"type": "string"}, proposal_id={"type": "string"}),
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.cancel",
            permission_actions=("read_files",),
            description="Cancel the task: every later step is refused before any effect.",
            input_schema={"task_id": "string", "reason": "string optional"},
            output_schema={"stage": "string"},
            side_effect_class="read_only",
            approval_requirement="none",
            json_schema=_schema(("task_id",), task_id={"type": "string"}, reason={"type": "string"}),
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.rollback",
            permission_actions=("overwrite_existing_files", "delete_files"),
            description="Restore the exact pre-task bytes of every file the task mutated, from the Blackbox journal.",
            input_schema={"task_id": "string"},
            output_schema={"restored_paths": "list[string]", "rollback": "object"},
            side_effect_class="workspace_write",
            approval_requirement="runtime_policy",
            json_schema=_schema(("task_id",), task_id={"type": "string"}),
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.report",
            permission_actions=("read_files",),
            description="Evidence report assembled only from executed steps: verdict, files changed, git diff paths, commands, tests, receipts, faults, rollback state, refused and unresolved work.",
            input_schema={"task_id": "string"},
            output_schema={
                "verdict": "completed|unresolved|cancelled|rolled_back",
                "files_changed": "list[string]",
                "commands_run": "list[command]",
                "tests": "object",
                "receipts": "object",
                "faults": "list[fault]",
                "rollback": "object",
                "refused": "list[step]",
                "failed": "list[step]",
                "unresolved": "list[string]",
            },
            side_effect_class="read_only",
            approval_requirement="none",
            json_schema=_schema(("task_id",), task_id={"type": "string"}),
            **common,
        ),
        RuntimeToolContract(
            intent="code.task.pr_description",
            permission_actions=("read_files",),
            description=(
                "Prepare a reviewable pull-request title and body assembled ONLY from the task "
                "journal (defect, diff paths, executed commands and outcomes). Refuses with "
                "insufficient_evidence unless the narrow and cumulative tests are green and the "
                "diff was inspected. Preparing a description opens no pull request."
            ),
            input_schema={"task_id": "string"},
            output_schema={"title": "string", "body": "string", "evidence": "object"},
            side_effect_class="read_only",
            approval_requirement="none",
            json_schema=_schema(("task_id",), task_id={"type": "string"}),
            **common,
        ),
    ]


def with_step_argument_fields(contracts: list[Any]) -> list[Any]:
    """Project common inner fields from their owning contracts, without closing the envelope.

    An object with no declared properties gives native decoders no field vocabulary.
    Per-intent required fields still belong to step preflight, not this union of hints.
    No registry re-entry: callers pass the already constructed list.

    The same projection also publishes each envelope's lawful ``intent`` VOCABULARY as an enum,
    derived from this list: every contracted workspace/sandbox inner tool for ``code.task.step``
    (reads, evidence commands, mutations), and the mutation-capable subset for ``code.task.propose``.
    A free-form ``intent`` string told the model "e.g. workspace.read_file" and nothing more, and
    measured native runs guessed -- ``code.task.step`` nested inside itself, or a catalog call --
    because the valid names were never visible on the wire. The enum makes the accepted vocabulary
    machine-readable for native decoders and keeps admission ownership exactly where it was.
    """
    from copy import deepcopy
    from dataclasses import replace

    from core.cloud_tool_call_contract import _argument_schema

    common = {"workspace.read_file", "workspace.write_file", "workspace.replace_in_file",
              "workspace.run_tests", "sandbox.run_command"}
    fields = {}
    inner_intents: list[str] = []
    mutation_intents: list[str] = []
    for contract in contracts:
        if contract.intent.startswith(("workspace.", "sandbox.")) and getattr(contract, "supported", True):
            inner_intents.append(contract.intent)
            if contract.side_effect_class == "workspace_write":
                mutation_intents.append(contract.intent)
        if contract.intent not in common:
            continue
        for name, descriptor in contract.input_schema.items():
            schema, _ = _argument_schema(descriptor)
            fields.setdefault(name, schema)
    inner_intents.sort()
    mutation_intents.sort()
    projected = []
    for contract in contracts:
        if contract.intent in {"code.task.step", "code.task.propose"}:
            schema = deepcopy(contract.json_schema)
            schema["properties"]["arguments"].update(properties=fields, additionalProperties=True)
            vocabulary = inner_intents if contract.intent == "code.task.step" else mutation_intents
            if vocabulary:
                schema["properties"]["intent"] = {
                    **deepcopy(schema["properties"]["intent"]),
                    "enum": list(vocabulary),
                }
            contract = replace(contract, json_schema=schema)
        projected.append(contract)
    return projected
