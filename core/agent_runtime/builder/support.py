from __future__ import annotations

import shlex
import sys
from collections.abc import Callable
from typing import Any

from core.agent_runtime.orchestrator import redact_tool_arguments
from core.context_scope import ContextAccessPolicy

# Workflow intents the builder controller can execute as a bounded step. Read-only
# inspection intents (search/symbol/list/git) are included so a builder-scoped
# inspect->read->synthesize request runs as a workflow instead of falling through to
# the "unsupported" branch, which surfaces as advice_only with zero tool calls.
_WORKFLOW_EXECUTABLE_INTENTS = frozenset(
    {
        "workspace.read_file",
        "workspace.write_file",
        "workspace.ensure_directory",
        "orchestration.execute_envelope",
        "sandbox.run_command",
        "hive.create_topic",
        "workspace.search_text",
        "workspace.symbol_search",
        "workspace.list_files",
        "workspace.git_status",
        "workspace.git_summary",
    }
)


def support_gap_report(
    *,
    source_context: dict[str, object] | None,
    reason: str,
    write_enabled: bool,
) -> dict[str, Any]:
    workspace_available = bool(
        str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    )
    if not workspace_available:
        gap_reason = "I need an active workspace before I can run a real bounded builder loop."
    elif not write_enabled:
        gap_reason = "Workspace writes are disabled on this runtime, so I cannot run a real bounded builder loop."
    else:
        gap_reason = reason
    return {
        "requested_capability": "workspace.build_scaffold",
        "requested_label": "builder controller",
        "support_level": "unsupported",
        "claim": (
            "I can run a bounded local builder loop in the active workspace: create starter folders/files, write narrow Telegram or Discord bot scaffolds, "
            "inspect files, apply explicit replacements, and run bounded local commands."
        ),
        "partial_reason": (
            "This is still a bounded local builder loop, not a full autonomous research -> build -> debug -> test system "
            "for arbitrary products or stacks."
            if workspace_available and write_enabled
            else ""
        ),
        "reason": gap_reason,
        "nearby_alternatives": [
            "Ask me to inspect the repo or read specific files in the workspace.",
            "Ask for a starter scaffold or a Telegram or Discord bot scaffold in the active workspace.",
            "Ask me to create a starter folder or first files in a concrete workspace path.",
            "Give me an exact replacement to apply in a file and I can run it locally.",
            "Give me a bounded local command or test to run in the workspace.",
        ],
    }


def controller_profile(
    agent: Any,
    *,
    effective_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    source_context: dict[str, object] | None,
    access_policy: ContextAccessPolicy | None = None,
    plan_tool_workflow_fn: Callable[..., Any],
    looks_like_workspace_bootstrap_request_fn: Callable[[str], bool],
) -> dict[str, Any]:
    source_context = dict(source_context or {})
    if not agent._should_run_builder_controller(
        effective_input=effective_input,
        classification=classification,
        source_context=source_context,
    ):
        return {"should_handle": False}
    workspace_root = str(source_context.get("workspace") or source_context.get("workspace_root") or "").strip()
    target = agent._workspace_build_target(
        query_text=effective_input,
        interpretation=interpretation,
        access_policy=access_policy,
        workspace_root=workspace_root,
    )
    # How much of the workspace this request authorized changing, decided BEFORE anything is
    # planned. A request that names its files must not be handed a build target the operator never
    # asked for: `generated/file-called-notes-txt-b92eec/` was invented for "create a file called
    # notes.txt", and every file written under it inherited that invented authority. See
    # builder/mutation_scope.py.
    from core.agent_runtime.builder import mutation_scope as mutation_scope_module

    scope = mutation_scope_module.resolve_mutation_scope(effective_input, workspace_root=workspace_root)
    build_target = {**target, "root_dir": scope.root_dir} if scope.is_exact else target
    workflow_probe = plan_tool_workflow_fn(
        user_text=effective_input,
        task_class=str(classification.get("task_class") or "unknown"),
        executed_steps=[],
        source_context=source_context,
    )
    workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
    workflow_supported_request = agent._supports_bounded_builder_workflow_request(
        effective_input=effective_input,
        task_class=str(classification.get("task_class") or "unknown"),
        source_context=source_context,
    )
    explicit_file_request = agent._looks_like_explicit_workspace_file_request(effective_input)
    generic_bootstrap_request = agent._looks_like_generic_workspace_bootstrap_request(str(effective_input or "").lower())
    from core.agent_runtime.builder import small_project_plan
    from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request

    # A bounded small-project request belongs to the same lane, and reaches it here rather than
    # through the agentic-build gate because that gate wants an imperative verb: "small python app
    # folder, readme + app only" has none, names its whole file set, and would otherwise fall
    # through every branch to the capability-gap report.
    small_project = small_project_plan.resolve_small_project_plan(
        effective_input, workspace_root=workspace_root
    )
    # P0 SIMPLE-FILE-WRITE — THE typed literal-versus-brief authority, consulted once here.
    # A LITERAL write demand ("create notes.txt containing hello") is one honest file
    # operation: it becomes workspace.write_file payloads executed through the bounded
    # workflow loop, where decide_tool_call, the effect ledger and workspace confinement all
    # apply — with ZERO model calls. It must never reach the model-driven build lanes below,
    # which generate content with a local model and fail wherever that lane is unreachable.
    # A BRIEF demand ("... containing a two-line summary of what a linter does") is a
    # description of the file, not the file: the builder owns it under the EXACT scope
    # resolved above, exactly as before.
    from core.execution.write_demand import resolve_write_demand, verbatim_request_text

    # Content resolution reads the VERBATIM request: routing normalization collapses
    # whitespace, and a multiline literal content would land on disk collapsed.
    write_demand = resolve_write_demand(
        verbatim_request_text(source_context, effective_input) or effective_input,
        workspace_root=workspace_root,
    )
    literal_write_payloads = (
        write_demand.write_payloads() if write_demand is not None and write_demand.is_literal else []
    )
    if literal_write_payloads:
        return {
            "should_handle": True,
            "supported": True,
            "mode": "workflow",
            "target": build_target,
            "mutation_scope": scope.as_dict(),
            "initial_payloads": literal_write_payloads,
        }
    if str(target.get("platform") or "").strip() in {"telegram", "discord"}:
        # The platform TEMPLATE is for a request that asks for the platform's app scaffold. It
        # must not win over the request's own bounded plan: measured live 2026-09-18, "create a
        # folder xxx with exactly bot.py, requirements.txt and README.md — do not build the bot
        # yet" mentioned Telegram as the FUTURE purpose, the platform probe read "telegram", and
        # the request was answered with the application template (src/bot.py, .env.example, an
        # unsolicited compile). The bounded plan and the literal write above outrank the
        # template; the template keeps every request that does not carry one.
        if small_project is None:
            # NOR may the template win over a request that STATES ITS OWN COMMANDS
            # ("/start -> `Ready`", "/save <word>"). Measured across the beta (finalbot,
            # pocketbot, stashbot): the template's generic /start + /help + echo bot shipped
            # in place of the requested behavior every single time. A stated spec is
            # generated from the request on the model-build lane, which is exactly the lane
            # that produced spec-following code whenever it ran.
            from core.agent_runtime.build_request_intent import request_specifies_command_behavior

            if request_specifies_command_behavior(effective_input):
                return {
                    "should_handle": True,
                    "supported": True,
                    "mode": "model_build",
                    "target": target,
                }
            return {
                "should_handle": True,
                "supported": True,
                "mode": "scaffold",
                "target": target,
            }
    if looks_like_agentic_build_request(effective_input) or small_project is not None:
        return {
            "should_handle": True,
            "supported": True,
            "mode": "model_build",
            "target": build_target,
            "mutation_scope": scope.as_dict(),
        }
    if (
        workflow_supported_request
        and workflow_probe.handled
        and workflow_probe.next_payload
        and workflow_intent in _WORKFLOW_EXECUTABLE_INTENTS
        and (explicit_file_request or not generic_bootstrap_request)
    ):
        return {
            "should_handle": True,
            "supported": True,
            "mode": "workflow",
            "target": target,
            "initial_payloads": [dict(workflow_probe.next_payload or {})],
        }
    if generic_bootstrap_request:
        return {
            "should_handle": True,
            "supported": True,
            "mode": "scaffold",
            "target": target,
        }
    if (
        workflow_supported_request
        and workflow_probe.handled
        and workflow_probe.next_payload
        and workflow_intent in _WORKFLOW_EXECUTABLE_INTENTS
    ):
        return {
            "should_handle": True,
            "supported": True,
            "mode": "workflow",
            "target": target,
            "initial_payloads": [dict(workflow_probe.next_payload or {})],
        }
    # An instruction that names its FILES rather than its product -- "create qa2/greet.py containing
    # a greet(name) function" -- is a build this runtime performs, and it used to end here with "I do
    # not have a real bounded builder path for that request on this runtime." It does: the same
    # model-driven lane above writes the file, generates a test and runs it. The request was
    # artifact-scope, the lane was gated on project scope, and the literal-write lane correctly
    # declined because the content was a brief rather than text to copy. See builder/named_file_build.py.
    from core.agent_runtime.builder.named_file_build import (
        looks_like_named_file_build_request,
        named_file_build_root,
    )

    if looks_like_named_file_build_request(effective_input, workspace_root=workspace_root):
        named_root = named_file_build_root(effective_input, workspace_root=workspace_root)
        # The folder the user stated wins. `target["root_dir"]` is only a default when they named no
        # destination, and building under a default while reporting it as done is the relocation
        # this lane exists to avoid.
        if named_root and not scope.is_exact:
            build_target = {**build_target, "root_dir": named_root}
        return {
            "should_handle": True,
            "supported": True,
            "mode": "model_build",
            "target": build_target,
            "mutation_scope": scope.as_dict(),
        }
    # "Write the failing test that reproduces this, and run it" is a build this lane performs —
    # generate a file, write it, run the tests — but it names no product to scaffold and no file
    # path, so it used to fall through to the refusal below. Adding `test` to the build nouns made
    # the predicate say yes and simply moved the failure here, from "I couldn't map that cleanly to
    # a real action" to "I do not have a real bounded builder path". Both are refusals of a request
    # this runtime can serve.
    #
    # Worth the extra branch because prove-it-before-you-fix-it is the only real defence against a
    # model that invents bugs. Twice in one session a cloud model produced a confidently-cited
    # defect that did not exist, and one of its proposed fixes made every archive undecompressable.
    # A fabricated bug does not survive being asked to reproduce itself.
    from core.agent_runtime.build_request_intent import looks_like_test_artifact_request

    if looks_like_test_artifact_request(effective_input):
        return {
            "should_handle": True,
            "supported": True,
            "mode": "model_build",
            "target": build_target,
            "mutation_scope": scope.as_dict(),
        }
    # An artifact-shaped instruction that names NO destination -- "write me a snake game i can run
    # in the browser, one html file", "write a python script that renames my photos by date" -- is
    # not a build this lane performs: nothing authorized a folder, and inventing one is the
    # relocation `mutation_scope` exists to prevent. It IS a request the runtime serves: the model
    # writes the code in the answer, exactly as it does for "lets build a tetris game in python",
    # which this lane never claimed. Declining hands the turn to that lane. Measured 2026-09-07:
    # the snake request ended in the canned refusal below while the tetris request was answered.
    if not scope.is_exact:
        return {"should_handle": False, "declined": "artifact_without_destination"}
    # A real "build this app in a folder, with tests" request: generate the files from the model,
    # write them, run the tests, and iterate on failure (see builder/app_builder.py). This is the
    # path that replaces the old capability gap for agentic builds.
    return {
        "should_handle": True,
        "supported": False,
        "mode": "unsupported",
        "target": target,
        "gap_report": agent._builder_support_gap_report(
            source_context=source_context,
            reason=(
                "I do not have a real bounded builder path for that request on this runtime. "
                "I can handle bounded workspace starters, narrow bot scaffolds, or explicit inspect/edit/run flows in the active workspace."
            ),
        ),
    }


def controller_step_record(
    agent: Any,
    *,
    execution: Any,
    tool_payload: dict[str, Any],
) -> dict[str, Any]:
    tool_name = str(getattr(execution, "tool_name", "") or tool_payload.get("intent") or "unknown").strip()
    details = redact_tool_arguments(dict(getattr(execution, "details", {}) or {}))
    response_text = str(redact_tool_arguments(str(getattr(execution, "response_text", "") or "")) or "")
    return {
        "tool_name": tool_name,
        "status": str(getattr(execution, "status", "") or "executed"),
        "mode": str(getattr(execution, "mode", "") or ""),
        "response_text": response_text,
        "arguments": redact_tool_arguments(tool_payload.get("arguments")),
        "observation": redact_tool_arguments(dict((getattr(execution, "details", {}) or {}).get("observation") or {})),
        "details": details,
        "artifacts": redact_tool_arguments(
            [dict(item) for item in list((getattr(execution, "details", {}) or {}).get("artifacts") or []) if isinstance(item, dict)]
        ),
        "summary": agent._tool_step_summary(
            response_text,
            fallback=str(getattr(execution, "status", "") or "executed"),
        ),
    }


def workspace_build_verification_payload(*, target: dict[str, str]) -> dict[str, Any] | None:
    language = str(target.get("language") or "").strip().lower()
    root_dir = str(target.get("root_dir") or "").strip().rstrip("/")
    if language != "python" or not root_dir:
        return None
    # sys.executable, not a literal "python3" (absent on Windows); shlex.join quotes the
    # interpreter path + target for the sandbox's POSIX shlex.split.
    compile_command = shlex.join([sys.executable, "-m", "compileall", "-q", f"{root_dir}/src"])
    # The trust marker sits beside `arguments`, never inside it: `arguments` is the dict a model
    # can populate, and the executor strips underscore-prefixed keys out of it before dispatch.
    return {
        "intent": "sandbox.run_command",
        "arguments": {"command": compile_command},
        "trusted_local_only": True,
    }


def initial_payloads(
    agent: Any,
    *,
    mode: str,
    target: dict[str, str],
    user_request: str,
    web_notes: list[dict[str, Any]],
    initial_payloads: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if mode == "workflow":
        return [dict(item) for item in list(initial_payloads or []) if isinstance(item, dict)], []
    sources = agent._workspace_build_sources(web_notes)
    file_map = agent._workspace_build_file_map(
        target=target,
        user_request=user_request,
        web_notes=web_notes,
    )
    payloads = [
        {
            "intent": "workspace.write_file",
            "arguments": {"path": path, "content": content},
        }
        for path, content in file_map.items()
    ]
    verification_payload = agent._workspace_build_verification_payload(target=target)
    if verification_payload is not None:
        payloads.append(verification_payload)
    return payloads, sources


def controller_backing_sources(executed_steps: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for step in list(executed_steps or []):
        tool_name = str(step.get("tool_name") or "").strip()
        if tool_name.startswith("workspace."):
            source = "workspace"
        elif tool_name.startswith("sandbox."):
            source = "sandbox"
        elif tool_name.startswith("web."):
            source = "web_lookup"
        else:
            continue
        if source in seen:
            continue
        seen.add(source)
        sources.append(source)
    return sources


def controller_observations(
    *,
    mode: str,
    target: dict[str, str],
    executed_steps: list[dict[str, Any]],
    stop_reason: str,
    sources: list[dict[str, str]],
    final_status: str,
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "channel": "bounded_builder",
        "builder_mode": str(mode or "").strip(),
        "target": {
            "platform": str(target.get("platform") or "").strip(),
            "language": str(target.get("language") or "").strip(),
            "root_dir": str(target.get("root_dir") or "").strip(),
        },
        "step_count": len(executed_steps),
        "stop_reason": str(stop_reason or "").strip(),
        "final_status": str(final_status or "").strip(),
        "artifacts": dict(artifacts or {}),
        "sources": [
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "label": str(item.get("label") or "").strip(),
            }
            for item in list(sources or [])[:4]
        ],
        "executed_steps": [
            {
                "tool_name": str(step.get("tool_name") or "").strip(),
                "status": str(step.get("status") or "").strip(),
                "mode": str(step.get("mode") or "").strip(),
                "summary": str(step.get("summary") or "").strip(),
                "arguments": dict(step.get("arguments") or {}),
                "observation": dict(step.get("observation") or {}),
            }
            for step in list(executed_steps or [])[:8]
        ],
    }


def retry_history(executed_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    retries: list[dict[str, Any]] = []
    for index, step in enumerate(list(executed_steps or []), start=1):
        tool_name = str(step.get("tool_name") or "").strip()
        if tool_name != "sandbox.run_command":
            continue
        observation = dict(step.get("observation") or {})
        command = str(observation.get("command") or dict(step.get("arguments") or {}).get("command") or "").strip()
        if not command:
            continue
        key = (tool_name, command)
        if key not in seen:
            seen[key] = {
                "command": command,
                "attempts": 1,
                "step_indexes": [index],
                "returncodes": [int(observation.get("returncode") or 0)],
            }
            continue
        seen[key]["attempts"] = int(seen[key].get("attempts") or 1) + 1
        seen[key]["step_indexes"] = [*list(seen[key].get("step_indexes") or []), index]
        seen[key]["returncodes"] = [*list(seen[key].get("returncodes") or []), int(observation.get("returncode") or 0)]
    for entry in seen.values():
        if int(entry.get("attempts") or 0) <= 1:
            continue
        retries.append(
            {
                "command": str(entry.get("command") or "").strip(),
                "attempts": int(entry.get("attempts") or 0),
                "step_indexes": [int(item) for item in list(entry.get("step_indexes") or [])],
                "returncodes": [int(item) for item in list(entry.get("returncodes") or [])],
            }
        )
    return retries


def controller_artifacts(
    *,
    executed_steps: list[dict[str, Any]],
    stop_reason: str,
) -> dict[str, Any]:
    file_diffs: list[dict[str, Any]] = []
    command_outputs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, step in enumerate(list(executed_steps or []), start=1):
        for artifact in [dict(item) for item in list(step.get("artifacts") or []) if isinstance(item, dict)]:
            artifact_type = str(artifact.get("artifact_type") or "").strip()
            record = {"step_index": index, **artifact}
            if artifact_type == "file_diff":
                file_diffs.append(record)
            elif artifact_type == "command_output":
                command_outputs.append(record)
            elif artifact_type == "failure":
                failures.append(record)
    return {
        "file_diffs": file_diffs[:8],
        "command_outputs": command_outputs[:8],
        "failures": failures[:6],
        "retry_history": retry_history(executed_steps),
        "stop_reason": str(stop_reason or "").strip(),
    }


def artifact_citation_block(agent: Any, artifacts: dict[str, Any]) -> str:
    payload = dict(artifacts or {})
    lines = ["Artifacts:"]
    file_diffs = [dict(item) for item in list(payload.get("file_diffs") or []) if isinstance(item, dict)]
    if file_diffs:
        files = ", ".join(
            f"`{str(item.get('path') or '').strip()}`" for item in file_diffs[:4] if str(item.get("path") or "").strip()
        )
        if files:
            lines.append(f"- changed files: {files}")
        diff_preview = str(file_diffs[0].get("diff_preview") or "").strip()
        if diff_preview:
            lines.append(f"- diff preview: `{agent._runtime_preview(diff_preview, limit=180)}`")
    command_outputs = [dict(item) for item in list(payload.get("command_outputs") or []) if isinstance(item, dict)]
    if command_outputs:
        command_bits = []
        for item in command_outputs[:3]:
            command = str(item.get("command") or "").strip()
            returncode = int(item.get("returncode") or 0)
            if command:
                command_bits.append(f"`{command}` (exit {returncode})")
        if command_bits:
            lines.append(f"- commands: {', '.join(command_bits)}")
    failures = [dict(item) for item in list(payload.get("failures") or []) if isinstance(item, dict)]
    if failures:
        failure_bits = []
        for item in failures[:2]:
            summary = str(item.get("summary") or "").strip()
            if summary:
                failure_bits.append(f"`{agent._runtime_preview(summary, limit=140)}`")
        if failure_bits:
            lines.append(f"- failures seen: {', '.join(failure_bits)}")
    retries = [dict(item) for item in list(payload.get("retry_history") or []) if isinstance(item, dict)]
    if retries:
        retry_bits = []
        for item in retries[:2]:
            command = str(item.get("command") or "").strip()
            attempts = int(item.get("attempts") or 0)
            if command and attempts > 1:
                retry_bits.append(f"`{command}` x{attempts}")
        if retry_bits:
            lines.append(f"- retries: {', '.join(retry_bits)}")
    humanized_stop = _humanize_builder_stop_reason(str(payload.get("stop_reason") or "").strip())
    if humanized_stop and len(lines) > 1:
        lines.append(f"- completion state: {humanized_stop}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def append_artifact_citations(agent: Any, text: str, *, artifacts: dict[str, Any]) -> str:
    message = str(text or "").strip()
    citation_block = artifact_citation_block(agent, artifacts)
    if not citation_block.strip():
        return message
    if not message:
        return citation_block
    return f"{message}\n\n{citation_block}".strip()


def _humanize_builder_stop_reason(stop_reason: str) -> str:
    normalized = str(stop_reason or "").strip()
    if normalized == "workspace_stop_after_directory_bootstrap":
        return "stopped after the directory was created"
    if normalized == "workspace_stop_after_write":
        return "stopped after the file write completed"
    if normalized == "workspace_stop_after_read":
        return "stopped after the file read completed"
    if normalized == "workspace_stop_after_list":
        return "stopped after the path listing completed"
    if normalized == "command_stop_after_success":
        return "stopped after the command completed"
    return ""


def _last_builder_step_path(executed_steps: list[dict[str, Any]]) -> str:
    if not executed_steps:
        return ""
    last_step = dict(executed_steps[-1] or {})
    observation = dict(last_step.get("observation") or {})
    path = str(observation.get("path") or dict(last_step.get("arguments") or {}).get("path") or "").strip()
    return path


def controller_degraded_response(
    agent: Any,
    *,
    target: dict[str, str],
    executed_steps: list[dict[str, Any]],
    stop_reason: str,
    failed_execution: Any | None,
    effective_input: str,
    session_id: str,
    workspace_root: str = "",
) -> str:
    root_dir = str(target.get("root_dir") or "the workspace").strip()

    def _located(relative: str) -> str:
        """`path (at /absolute/path)` -- the operator must never have to ask WHERE a
        build landed. Measured live 2026-09-18: "I completed 5 bounded builder steps under
        `finalbot`" answered nothing about location, and the follow-up question got a
        whole-disk folder search. Relative-only summaries are the defect."""
        base = str(workspace_root or "").strip().rstrip("/")
        if not base or relative.startswith(("/", "~")) or relative == "the workspace":
            return f"`{relative}`"
        return f"`{relative}` (at `{base}/{relative.strip('/')}`)"

    if failed_execution is not None:
        failure_text = agent._tool_failure_user_message(
            execution=failed_execution,
            effective_input=effective_input,
            session_id=session_id,
        )
        if executed_steps:
            return (
                f"I completed {len(executed_steps)} bounded builder step"
                f"{'' if len(executed_steps) == 1 else 's'} under {_located(root_dir)}, "
                f"but the loop stopped at `{getattr(failed_execution, 'tool_name', '') or 'tool'!s}`. {failure_text}"
            ).strip()
        return failure_text
    if executed_steps:
        last_step = dict(executed_steps[-1] or {})
        last_tool_name = str(last_step.get("tool_name") or "").strip()
        last_path = _last_builder_step_path(executed_steps)
        if last_tool_name == "workspace.ensure_directory" and last_path:
            return f"I created {_located(last_path)}.".strip()
        if last_tool_name == "workspace.write_file" and last_path:
            return f"I wrote {_located(last_path)}.".strip()
        humanized_stop = _humanize_builder_stop_reason(stop_reason)
        if humanized_stop:
            return (
                f"I completed {len(executed_steps)} bounded builder step"
                f"{'' if len(executed_steps) == 1 else 's'} under {_located(root_dir)} and {humanized_stop}."
            ).strip()
        return (
            f"I completed {len(executed_steps)} bounded builder step"
            f"{'' if len(executed_steps) == 1 else 's'} under {_located(root_dir)} and stopped after the last safe local action."
        ).strip()
    return f"I could not start a bounded builder loop for `{root_dir}` on this run.".strip()


def controller_direct_response(
    agent: Any,
    *,
    effective_input: str,
    executed_steps: list[dict[str, Any]],
) -> str | None:
    if not executed_steps:
        return None
    last_step = dict(executed_steps[-1] or {})
    last_tool_name = str(last_step.get("tool_name") or "").strip()
    normalized_input = f" {' '.join(str(effective_input or '').split()).strip().lower()} "
    explicit_exact_read_request = bool(
        any(marker in normalized_input for marker in (" read ", " open ", " quote "))
        and any(marker in normalized_input for marker in (" exact", " verbatim"))
    )
    if (
        last_tool_name in {"workspace.read_file", "machine.read_file"}
        and (agent._looks_like_exact_workspace_readback_request(effective_input) or explicit_exact_read_request)
    ):
        response_text = str(last_step.get("response_text") or "").strip()
        if response_text:
            return response_text
    return None


def controller_workflow_summary(
    agent: Any,
    *,
    mode: str,
    executed_steps: list[dict[str, Any]],
    stop_reason: str,
    artifacts: dict[str, Any],
) -> str:
    lines = [
        f"- bounded builder controller executed {len(executed_steps)} real step{'s' if len(executed_steps) != 1 else ''}",
        f"- builder mode: `{mode}`",
    ]
    if executed_steps:
        chain = " -> ".join(str(step.get("tool_name") or "tool").strip() for step in list(executed_steps or [])[:8])
        if chain:
            lines.append(f"- tool chain: `{chain}`")
    if stop_reason:
        lines.append(f"- stop reason: `{stop_reason}`")
    file_diffs = [dict(item) for item in list((artifacts or {}).get("file_diffs") or []) if isinstance(item, dict)]
    if file_diffs:
        lines.append(
            "- changed files: "
            + ", ".join(f"`{str(item.get('path') or '').strip()}`" for item in file_diffs[:4] if str(item.get("path") or "").strip())
        )
    command_outputs = [dict(item) for item in list((artifacts or {}).get("command_outputs") or []) if isinstance(item, dict)]
    if command_outputs:
        lines.append(
            "- commands: "
            + ", ".join(
                f"`{str(item.get('command') or '').strip()}` (exit {int(item.get('returncode') or 0)})"
                for item in command_outputs[:3]
                if str(item.get("command") or "").strip()
            )
        )
    failures = [dict(item) for item in list((artifacts or {}).get("failures") or []) if isinstance(item, dict)]
    if failures:
        lines.append(
            "- failures seen: "
            + ", ".join(
                f"`{agent._runtime_preview(str(item.get('summary') or ''), limit=120)}`"
                for item in failures[:2]
                if str(item.get("summary") or "").strip()
            )
        )
    retries = [dict(item) for item in list((artifacts or {}).get("retry_history") or []) if isinstance(item, dict)]
    if retries:
        lines.append(
            "- retries: "
            + ", ".join(
                f"`{str(item.get('command') or '').strip()}` x{int(item.get('attempts') or 0)}"
                for item in retries[:2]
                if str(item.get("command") or "").strip() and int(item.get("attempts") or 0) > 1
            )
        )
    return "\n".join(lines)
