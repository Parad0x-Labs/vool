from __future__ import annotations

import contextlib
import hashlib
import shlex
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from core.agent_runtime.orchestrator import redact_tool_arguments
from core.context_scope import ContextAccessPolicy
from core.mode_permission_policy import PENDING_BATCH_CALLS_KEY
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)
from core.runtime_task_events import emit_runtime_event

_BUILDER_NUM_PREDICT = 6144
_BUILDER_NUM_CTX = 8192
_BUILDER_CALL_TIMEOUT_SECONDS = 300.0
# One build can issue 1 plan + 16 files + 3 fix rounds x 16 = up to 65 sequential generations. At the
# 300s per-call timeout that is 5.4 hours of silence with no way for the operator to tell a working
# build from a wedged one. The aggregate ceiling is what makes the worst case finite; the per-call
# timeout never could, because it applies 65 times.
_BUILDER_TOTAL_WALL_CLOCK_SECONDS = 900.0

# How much of a failure string survives onto a ledger row. Long enough to carry a real exception
# message, short enough that one bad step cannot flood the event store or the Activity panel.
_TOOL_FAILURE_TEXT_LIMIT = 400


def _tool_failure_details(execution_details: Mapping[str, Any]) -> dict[str, Any]:
    """The cause fields a tool execution recorded, ready to ride on its ledger event.

    Returns an EMPTY dict for a step that recorded no failure, so a successful `tool_executed`
    event is unchanged. Nothing here is derived or guessed: each key is copied straight from what
    `core/runtime_execution_tools.py` and `core/tool_intent_executor.py` already put on
    `execution.details`, bounded in length. A traceback is never carried -- the destination is a
    row in the Activity panel that a user pastes into a bug report.
    """

    def _text(value: Any) -> str:
        clean = " ".join(str(value or "").split()).strip()
        return clean[:_TOOL_FAILURE_TEXT_LIMIT]

    out: dict[str, Any] = {}
    reason = _text(execution_details.get("error"))
    if reason:
        out["reason"] = reason
    exception_class = _text(execution_details.get("exception_class"))
    if exception_class:
        out["exception_class"] = exception_class
    gap = execution_details.get("capability_gap")
    if isinstance(gap, Mapping):
        gap_kind = _text(gap.get("gap_kind"))
        if gap_kind:
            # A tool that exists but cannot run (disabled, unconfigured, no credential) is a
            # different investigation from one that ran and raised; without this they read alike.
            out["error_kind"] = gap_kind
    return out


@dataclass
class BuilderGenerationBudget:
    """A wall-clock ceiling shared by every generation in one build.

    Checked before each call, and each call's own timeout is clipped to what is left, so the build
    cannot overrun the ceiling by one more full-length request.
    """

    total_seconds: float = _BUILDER_TOTAL_WALL_CLOCK_SECONDS
    started_monotonic: float = field(default_factory=time.monotonic)
    calls: int = 0
    seconds_spent: float = 0.0
    exhausted_reason: str = ""
    # Why the LAST generation produced nothing. The builder's contract is "empty string means this
    # call produced nothing", which carries no reason with it -- so a pinned build that fails on
    # every generation could only report "0 files written". Recorded here so the operator is told
    # whether their model was unreachable, unauthorized or simply silent.
    last_error: str = ""

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    def remaining(self) -> float:
        return max(0.0, float(self.total_seconds) - self.elapsed())

    def exhausted(self) -> bool:
        # A hair above zero: a call granted a sub-second timeout cannot produce a usable answer and
        # would only add a failure event.
        return self.remaining() <= 1.0

    def call_timeout(self) -> float:
        return max(1.0, min(_BUILDER_CALL_TIMEOUT_SECONDS, self.remaining()))


def _audited_subject_paths(source_context: Any) -> tuple[str, ...]:
    """The files a preceding audit actually read, so a follow-up build knows its subject.

    `build_app_from_spec` receives the raw user sentence and nothing else, and `source_context` is
    mined only for the workspace root. So "prove the bug you identified" arrived carrying no
    reference to the file that had just been audited, and the model invented a subject -- measured
    2026-08-01, a request about `api/apache/liquefy_apache_repetition_v1.py` produced an unrelated
    path-traversal `app.py`.

    Two sources, in order:

    * `workspace_audit_evidence`, which `run_workspace_audit` stashes on the source_context. This is
      exact, but it is SAME-TURN ONLY -- `core/web/api/service.py` rebuilds `source_context` from the
      request body on every call, so on the turn that says "prove it" the stash is already gone.
      Measured on a real follow-up context: `()`. It stays first because when it is present it names
      what was actually read, not what was asked about.
    * the conversation history the API stamps onto every request, read newest-first through the same
      `audit_target_in` extractor the audit itself uses. One extraction rule for "which file is this
      about", so a follow-up cannot resolve to a different file than the audit did.

    Never raises: a missing or malformed key means no subject, not a broken build.
    """

    try:
        context = dict(source_context or {})
    except Exception:
        return ()
    try:
        blob = context.get("workspace_audit_evidence")
        if isinstance(blob, dict):
            paths = blob.get("inspected_paths") or blob.get("all_paths") or ()
            stashed = tuple(str(p) for p in list(paths)[:4] if str(p or "").strip())
            if stashed:
                return stashed
    except Exception:
        pass
    try:
        from core.agent_runtime.workspace_audit import audit_target_in

        history = context.get("conversation_history") or context.get("client_conversation_history")
        for message in reversed(list(history or [])[-12:]):
            if not isinstance(message, dict):
                continue
            if str(message.get("role") or "").strip().lower() != "user":
                continue
            target = str(audit_target_in(str(message.get("content") or "")) or "").strip()
            if target:
                return (target,)
    except Exception:
        pass
    return ()


def build_finished_successfully(
    *,
    files_written: list[str] | tuple[str, ...],
    tests_ran: bool,
    tests_passed: bool,
    budget_exhausted_reason: str = "",
    proof_failed: bool = False,
) -> bool:
    """Whether a build actually finished. A ceiling that stopped it means it did not.

    A named function rather than an inline expression because a source-text assertion could not
    tell a live branch from a dead one: a sabotage that changed the guard to `if False:` while
    leaving `succeeded = False` in place passed the test that was supposed to catch it.

    `proof_failed` inverts the usual reading for one case. When the operator asked for a test that
    proves a bug BY FAILING, a green suite is the failure: it means the claimed bug was not
    reproduced. Scoring that as success is how a fabricated finding gets reported as verified.
    """

    if str(budget_exhausted_reason or "").strip():
        return False
    if proof_failed:
        return False
    return bool(files_written) and (bool(tests_passed) or not bool(tests_ran))


def early_stop_note(reason: str, *, files_written: list[str] | tuple[str, ...]) -> str:
    """What the operator is told when the ceiling cut a build short. Empty when it did not."""

    reason = str(reason or "").strip()
    if not reason:
        return ""
    return (
        f"**Stopped early.** {reason}. "
        f"{len(files_written)} file(s) were written before the ceiling was reached; anything after "
        "that point was not generated. Ask me to continue and I will resume from what is on disk."
    )


def _emit_builder_model_event(
    source_context: dict[str, Any] | None,
    event_type: str,
    message: str,
    **details: Any,
) -> None:
    """Builder generations were the one model lane that emitted nothing.

    `core/memory_first_router.py` emits `model.call_started` / `.call_completed` / `.call_failed`
    for every routed call, which is what the UI renders as activity. This lane posts straight to
    `requests.post` and bypassed all of it, so up to 65 sequential local generations were invisible:
    the operator saw a spinner and could not tell which model was running, how many calls had gone,
    or whether anything was still alive.
    """

    if source_context is None:
        return
    # Telemetry must never be the reason a build fails.
    with contextlib.suppress(Exception):
        emit_runtime_event(
            source_context,
            event_type=event_type,
            message=message,
            details={k: v for k, v in details.items() if v is not None and v != ""},
        )


# Model-name markers for families that emit hidden reasoning before the answer.
THINKING_MODEL_MARKERS = ("qwen3", "nemotron", "deepseek-r", "qwq", "reason")


def is_thinking_capable_model(model_name: str) -> bool:
    """Whether this Ollama tag names a model that emits hidden reasoning.

    Contract shared with adapters/openai_compatible_adapter.py::_is_default_no_think_model, which
    selects the same family by the same rule; tests/test_agent_runtime_builder.py pins the two
    against each other so the family detection cannot drift apart. The two deliberately act on it in
    OPPOSITE directions -- see builder_chat_payload for the measurement behind the builder's choice.
    """
    name = str(model_name or "").strip().lower()
    if "nothink" in name or "no-think" in name:
        return False
    # Widened 2026-07-28 beyond qwen3. `nvidia/nemotron-3-ultra-550b-a55b` returned empty content
    # on chat turns because it reasons before answering and matched nothing here, so it was never
    # given the budget for it. deepseek-r1 and qwq are the same shape. Kept as ONE family list so
    # the adapter and the builder cannot drift apart — tests pin them against each other.
    return any(marker in name for marker in THINKING_MODEL_MARKERS)


def builder_ollama_base_url(env: Mapping[str, str]) -> str:
    """Ollama's own OLLAMA_HOST convention is a bare `host:port`, which is not a usable URL."""
    from core.ollama_endpoint import ollama_base_url

    raw = ollama_base_url(env)
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    return raw.rstrip("/")


def builder_chat_payload(prompt: str, *, model_tag: str, num_predict: int = _BUILDER_NUM_PREDICT) -> dict[str, Any]:
    """The /api/chat body for one builder generation.

    `think` is sent as True for a thinking model, never False. Measured against live Ollama 0.31.1
    with qwen3:4b, whose chat template opens `<think>` unconditionally: `think: false` does not stop
    the model reasoning, it only switches OFF Ollama's parser, so 9KB of "Okay, let me process this
    step by step..." lands in `message.content` as the answer -- and a reasoning run cut off by the
    token budget carries no closing tag to strip it by. `think: true` keeps the parser on, so the
    reasoning is quarantined in `message.thinking` and `message.content` holds only the answer.
    Non-thinking models reject the flag outright (qwen2.5:7b -> HTTP 400 "does not support
    thinking"), so it is omitted for them.
    """
    payload: dict[str, Any] = {
        "model": model_tag,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": int(num_predict), "num_ctx": _BUILDER_NUM_CTX},
    }
    if is_thinking_capable_model(model_tag):
        payload["think"] = True
    return payload


def builder_user_visible_text(text: str) -> str:
    """Last hop before a Build-mode answer reaches the user: the model's reasoning is not the answer.

    This lane words its reply through the shared chat adapter, which sends `think: false` to every
    qwen3 provider (adapters/openai_compatible_adapter.py::_ollama_thinking_disabled, plus a
    hardcoded `"think": False` in every manifest core/runtime_provider_defaults.py writes). Measured
    live on qwen3:4b, that flag switches Ollama's parser off rather than the reasoning, so the
    monologue arrives as the answer. Constraint: this only recovers a monologue that reached its
    closing tag -- a reasoning run truncated by the token budget carries no marker, and only the
    adapter sending `think: true` can quarantine that.
    """
    from core.agent_runtime.builder import app_builder

    stripped = app_builder.strip_reasoning_monologue(text).strip()
    return stripped if stripped else str(text or "")


def build_ollama_generate_fn(
    *,
    base_url: str,
    model_tag: str,
    source_context: dict[str, Any] | None = None,
    budget: BuilderGenerationBudget | None = None,
) -> Callable[[str], str]:
    """The builder's one text-generation call: prompt in, the model's ANSWER out.

    A large output budget is deliberate: a full multi-file file map exceeds the default chat output
    budget, which truncates the reply and breaks the plan. No network egress (local Ollama).

    Each call announces itself. This lane used to post to `requests.post` and emit nothing at all,
    so a build's 65 possible generations were invisible to the operator — every other model lane
    emits `model.call_started` / `.call_completed` / `.call_failed` through
    `core/memory_first_router.py`, and this one did not. `budget` bounds them in aggregate: the
    per-call 300s timeout applies 65 times over and so bounds nothing.
    """
    import requests

    from core.agent_runtime.builder import app_builder

    budget = budget if budget is not None else BuilderGenerationBudget()

    def generate_once(prompt: str, *, num_predict: int) -> tuple[str, bool]:
        if budget.exhausted():
            budget.exhausted_reason = (
                f"builder wall-clock budget of {budget.total_seconds:.0f}s spent after "
                f"{budget.calls} generation(s)"
            )
            _emit_builder_model_event(
                source_context,
                "model.call_failed",
                f"Build stopped: {budget.exhausted_reason}.",
                provider_id="ollama:builder",
                model_id=model_tag,
                locality="local",
                reason="builder_budget_exhausted",
                calls=budget.calls,
                seconds_spent=round(budget.seconds_spent, 1),
            )
            return "", False

        budget.calls += 1
        call_index = budget.calls
        timeout = budget.call_timeout()
        _emit_builder_model_event(
            source_context,
            "model.call_started",
            f"Builder generation {call_index} started on {model_tag}.",
            provider_id="ollama:builder",
            model_id=model_tag,
            locality="local",
            active_inference=True,
            cost_class="free_local",
            call_index=call_index,
            max_output_tokens=int(num_predict),
            seconds_remaining=round(budget.remaining(), 1),
        )
        started = time.monotonic()
        try:
            payload = builder_chat_payload(
                prompt,
                model_tag=model_tag,
                num_predict=num_predict,
            )
            permit = seal_direct_provider_invocation(
                provider_id="ollama:builder",
                model_id=model_tag,
                operation="builder_generation",
                payload=payload,
                request_id=(
                    "builder-"
                    + hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest()
                ),
                max_output_tokens=num_predict,
                header_names=("Content-Type",),
            )
            # The same process-wide gate the chat adapter uses. A build issues up to 65 sequential
            # generations against the one local Ollama; counted separately from ordinary chat turns
            # it would count nothing, and a build running beside four chat turns is exactly the
            # contention that made half of them time out at 60s.
            from core.local_model_admission import local_model_slot

            with local_model_slot(provider_id="ollama:builder"):
                response = requests.post(
                    f"{base_url}/api/chat",
                    json=permit.consume(),
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = dict(response.json() or {})
        except Exception as exc:
            elapsed = time.monotonic() - started
            budget.seconds_spent += elapsed
            _emit_builder_model_event(
                source_context,
                "model.call_failed",
                f"Builder generation {call_index} failed on {model_tag}.",
                provider_id="ollama:builder",
                model_id=model_tag,
                locality="local",
                # The class, not the message: an exception body can carry a URL or a payload
                # fragment, and this reaches the operator's event stream.
                reason=type(exc).__name__,
                call_index=call_index,
                seconds=round(elapsed, 1),
            )
            return "", False
        elapsed = time.monotonic() - started
        budget.seconds_spent += elapsed
        # `message.content` only. `message.thinking` is the model reasoning to itself: it is never an
        # answer, so it must reach neither the user nor the file writer, not even as a fallback when
        # content is empty.
        message = dict(payload.get("message") or {})
        content = app_builder.strip_reasoning_monologue(str(message.get("content") or ""))
        truncated = str(payload.get("done_reason") or "").strip() == "length"
        _emit_builder_model_event(
            source_context,
            "model.call_completed",
            f"Builder generation {call_index} finished on {model_tag}.",
            provider_id="ollama:builder",
            model_id=model_tag,
            locality="local",
            call_index=call_index,
            seconds=round(elapsed, 1),
            characters=len(content),
            truncated=truncated or None,
            seconds_remaining=round(budget.remaining(), 1),
        )
        return content, truncated

    def generate_fn(prompt: str) -> str:
        content, truncated = generate_once(prompt, num_predict=_BUILDER_NUM_PREDICT)
        if content.strip() or not truncated:
            return content
        # A thinking model spent the entire budget reasoning and never reached its answer. Retry once
        # on a doubled budget rather than write out an empty file.
        content, _ = generate_once(prompt, num_predict=_BUILDER_NUM_PREDICT * 2)
        return content

    generate_fn.budget = budget  # type: ignore[attr-defined]
    return generate_fn


def workspace_build_observations(
    *,
    target: dict[str, str],
    write_results: list[dict[str, Any]],
    write_failures: list[str],
    verification: dict[str, Any] | None,
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "channel": "workspace_build",
        "target": {
            "platform": str(target.get("platform") or "").strip(),
            "language": str(target.get("language") or "").strip(),
            "root_dir": str(target.get("root_dir") or "").strip(),
        },
        "written_file_count": len(write_results),
        "written_files": [str(item.get("path") or "").strip() for item in write_results[:8]],
        "write_failures": [str(item).strip() for item in write_failures[:4] if str(item).strip()],
        "verification": {
            "status": str((verification or {}).get("status") or "").strip(),
            "ok": bool((verification or {}).get("ok", False)),
            "response_text": str((verification or {}).get("response_text") or "").strip(),
        },
        "sources": [
            {
                "title": str(item.get("title") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "label": str(item.get("label") or "").strip(),
            }
            for item in list(sources or [])[:4]
        ],
    }


def workspace_build_degraded_response(
    *,
    target: dict[str, str],
    write_results: list[dict[str, Any]],
    write_failures: list[str],
    verification: dict[str, Any] | None,
) -> str:
    root_dir = str(target.get("root_dir") or "the workspace").strip()
    if write_results:
        status = str((verification or {}).get("status") or "").strip()
        if status == "executed":
            verification_line = "Verification passed." if bool((verification or {}).get("ok", False)) else (
                f"Verification failed: {str((verification or {}).get('response_text') or '').strip()}"
            )
        elif status == "skipped":
            verification_line = "Verification was skipped for this scaffold type."
        else:
            verification_line = "Verification did not run."
        failure_line = ""
        if write_failures:
            failure_line = f" {len(write_failures)} write operation(s) failed."
        return (
            f"I completed the workspace build actions under `{root_dir}`, but I couldn't produce a clean final summary. "
            f"{verification_line}{failure_line}"
        ).strip()
    if write_failures:
        return f"I attempted the workspace build actions for `{root_dir}`, but the file writes did not complete cleanly.".strip()
    return "I couldn't complete the workspace build actions cleanly in this run."


def run_bounded_builder_loop(
    agent: Any,
    *,
    task: Any,
    session_id: str,
    effective_input: str,
    task_class: str,
    source_context: dict[str, object] | None,
    initial_payloads: list[dict[str, Any]],
    plan_tool_workflow_fn: Any,
    execute_tool_intent_fn: Any,
    trust_initial_payloads: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, Any | None]:
    loop_source_context = agent._merge_runtime_source_contexts({}, dict(source_context or {}))
    executed_steps: list[dict[str, Any]] = []
    pending_payloads = [dict(item) for item in list(initial_payloads or []) if isinstance(item, dict)]
    stop_reason = ""
    failed_execution = None
    max_steps = 6

    while len(executed_steps) < max_steps:
        from_initial_payloads = bool(pending_payloads)
        if pending_payloads:
            tool_payload = dict(pending_payloads.pop(0))
        else:
            workflow_decision = plan_tool_workflow_fn(
                user_text=effective_input,
                task_class=task_class,
                executed_steps=executed_steps,
                source_context=loop_source_context,
            )
            if workflow_decision.handled and workflow_decision.stop_after:
                stop_reason = str(workflow_decision.reason or "stop_after").strip()
                break
            if not workflow_decision.handled or not workflow_decision.next_payload:
                stop_reason = str(workflow_decision.reason or "no_followup_plan").strip()
                break
            tool_payload = dict(workflow_decision.next_payload or {})

        # The marker is always removed from the payload, and is only honoured for the
        # server-generated scaffold payloads this loop was handed: a planner follow-up can never
        # promote itself to trusted-local.
        marked_trusted = bool(tool_payload.pop("trusted_local_only", False))
        trusted_local_only = marked_trusted and from_initial_payloads and trust_initial_payloads

        intent = str(tool_payload.get("intent") or "tool").strip() or "tool"
        agent._emit_runtime_event(
            loop_source_context,
            event_type="tool_selected",
            message=f"Running {intent}.",
            tool_name=intent,
        )
        execution = execute_tool_intent_fn(
            tool_payload,
            task_id=task.task_id,
            session_id=session_id,
            source_context=loop_source_context,
            hive_activity_tracker=agent.hive_activity_tracker,
            public_hive_bridge=agent.public_hive_bridge,
            trusted_local_only=trusted_local_only,
        )
        if not execution.handled:
            agent._emit_runtime_event(
                loop_source_context,
                event_type="tool_failed",
                message=f"{intent} is not available.",
                tool_name=intent,
                status="not_handled",
            )
            stop_reason = "tool_not_handled"
            break

        execution_mode = str(getattr(execution, "mode", "") or "tool_failed").strip()
        execution_details = dict(getattr(execution, "details", {}) or {})
        step_record = agent._builder_controller_step_record(
            execution=execution,
            tool_payload=tool_payload,
        )
        executed_steps.append(step_record)
        action_receipt = agent._emit_runtime_event(
            loop_source_context,
            event_type=execution_mode,
            message=str(step_record.get("summary") or getattr(execution, "status", "") or "Tool step finished."),
            tool_name=str(getattr(execution, "tool_name", "") or tool_payload.get("intent") or "unknown"),
            status=str(getattr(execution, "status", "") or "executed"),
            mode=execution_mode,
            ok=bool(getattr(execution, "ok", False)),
            summary=str(step_record.get("summary") or ""),
            tool_call_id=str(execution_details.get("tool_call_id") or ""),
            arguments=redact_tool_arguments(tool_payload.get("arguments")),
            approval=redact_tool_arguments(execution_details.get("approval") or {}),
            approval_request=redact_tool_arguments(
                execution_details.get("approval_request") or {}
            ),
            approval_id=execution_details.get("approval_id"),
            approval_required=execution_details.get("approval_required"),
            approval_requirement=execution_details.get("approval_requirement"),
            approval_state=execution_details.get("approval_state"),
            approved_by=execution_details.get("approved_by"),
            # WHY a tool step failed, not just that it did. The executor already records the cause
            # on `execution.details` (`error` = str(exc), `exception_class` = its type, and a
            # `capability_gap.gap_kind` for a tool that is present but unusable), and none of it
            # was forwarded here -- so a failed step reached the ledger, and the Activity panel,
            # carrying a bucket status ("error") and the step summary and nothing that named the
            # cause. Bounded, never a traceback: this is a row a user pastes into a bug report.
            **_tool_failure_details(execution_details),
        )
        loop_source_context = agent._append_tool_result_to_source_context(
            loop_source_context,
            execution=execution,
            tool_name=str(getattr(execution, "tool_name", "") or tool_payload.get("intent") or ""),
            receipt=action_receipt,
        )
        if str(getattr(execution, "mode", "") or "").strip() != "tool_executed":
            failed_execution = execution
            stop_reason = f"{getattr(execution, 'mode', '') or 'tool_failed'!s}:{getattr(execution, 'status', '') or 'failed'!s}"
            break

    if not stop_reason and len(executed_steps) >= max_steps:
        stop_reason = "step_budget_exhausted"
    if not stop_reason:
        stop_reason = "bounded_loop_complete"
    return executed_steps, loop_source_context, stop_reason, failed_execution


def maybe_run_builder_controller(
    agent: Any,
    *,
    task: Any,
    effective_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    web_notes: list[dict[str, Any]],
    session_id: str,
    source_context: dict[str, object] | None,
    render_capability_truth_response_fn: Any,
    load_active_persona_fn: Any,
) -> dict[str, Any] | None:
    source_context = dict(source_context or {})
    # An audit continuation is not a build. "Prove the bug you identified before fixing it." names
    # no file and no app, so the generic builder claimed it, invented a subject from conversation
    # history, and spent a bounded artifact call's whole reasoning allowance on it before reporting
    # the model unreachable. The audit lane owns any turn whose evidence is already collected —
    # including the continuation turn, which `maybe_handle_workspace_audit_request` stamps from the
    # session capsule specifically so this check can see it (AGENT_HANDOVER §1A rule 5).
    if bool(source_context.get("workspace_audit_evidence_collected")):
        return None
    access_policy = ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context=source_context,
    )
    profile = agent._builder_controller_profile(
        effective_input=effective_input,
        classification=classification,
        interpretation=interpretation,
        source_context=source_context,
        access_policy=access_policy,
    )
    if not profile.get("should_handle"):
        return None

    if not profile.get("supported"):
        report = dict(profile.get("gap_report") or {})
        return agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=render_capability_truth_response_fn(report),
            confidence=0.82 if str(report.get("support_level") or "").strip() == "partial" else 0.74,
            source_context=source_context,
            reason="builder_capability_gap",
        )

    target = dict(profile.get("target") or {})
    mode = str(profile.get("mode") or "workflow").strip()
    if mode == "model_build":
        return _run_model_build(
            agent,
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            session_id=session_id,
            source_context=source_context,
            target=target,
            load_active_persona_fn=load_active_persona_fn,
        )
    if mode == "scaffold" and not web_notes:
        web_notes = agent._collect_live_web_notes(
            task_id=task.task_id,
            query_text=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
        )
    initial_payloads, sources = agent._builder_initial_payloads(
        mode=mode,
        target=target,
        user_request=effective_input,
        web_notes=web_notes,
        initial_payloads=list(profile.get("initial_payloads") or []),
    )
    if mode == "scaffold" and not initial_payloads:
        report = agent._builder_support_gap_report(
            source_context=source_context,
            reason=(
                "That request did not resolve to a supported scaffold target. "
                "The real scaffold lane here is still limited to Telegram or Discord bot builds."
            ),
        )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=render_capability_truth_response_fn(report),
            confidence=0.78,
            source_context=source_context,
            reason="builder_capability_gap",
        )

    executed_steps, loop_source_context, stop_reason, failed_execution = agent._run_bounded_builder_loop(
        task=task,
        session_id=session_id,
        effective_input=effective_input,
        task_class=str(classification.get("task_class") or "unknown"),
        source_context=source_context,
        initial_payloads=initial_payloads,
        trust_initial_payloads=(mode == "scaffold"),
    )
    final_status = "failed" if failed_execution is not None else "completed"
    artifacts = agent._builder_controller_artifacts(
        executed_steps=executed_steps,
        stop_reason=stop_reason,
    )
    # The summary must state WHERE the work landed, absolutely. A relative-only "under
    # `finalbot`" left the operator asking where it was (measured live 2026-09-18).
    artifacts["workspace_root"] = str(
        source_context.get("workspace") or source_context.get("workspace_root") or ""
    )
    # RUNTIME-MINTED SUPPORT for the authorship publication gate. The loop above REALLY
    # executed its steps -- receipts, file writes, command output -- but without this
    # registration the turn's authorship record holds only the router's pre-generation
    # verdict, so when no certified model words the summary (a local-only chat with no
    # certification run; the cloud route declined) the gate read "the lane came back
    # empty" and REPLACED the grounded builder summary with the authorship refusal --
    # measured live 2026-09-18 (pocketbot): four files and a command executed, and the
    # operator was told "nothing this turn retrieved, computed or observed backs it".
    # Same closure the mixed-demand lane applies (see agent.py's P0 note): raise-only,
    # claims no author, the gate adjudicates the composed summary per claim against
    # exactly these rows.
    try:
        from core.final_answer_authorship import record_runtime_support

        _support_rows = [
            {
                "summary": str(step.get("response_text") or "").strip()[:400],
                "intent": f"builder_step:{str(step.get('tool_name') or '').strip()}",
                "source": "bounded_builder_loop",
            }
            for step in executed_steps
            if str(step.get("response_text") or "").strip()
        ]
        record_runtime_support(
            source_context,
            support_rows=_support_rows,
            request_text=str(effective_input or ""),
        )
    except Exception:
        pass
    observations = agent._builder_controller_observations(
        mode=mode,
        target=target,
        executed_steps=executed_steps,
        stop_reason=stop_reason,
        sources=sources,
        final_status=final_status,
        artifacts=artifacts,
    )
    degraded = agent._builder_controller_degraded_response(
        target=target,
        executed_steps=executed_steps,
        stop_reason=stop_reason,
        failed_execution=failed_execution,
        effective_input=effective_input,
        session_id=session_id,
        artifacts=artifacts,
    )
    workflow_summary = agent._builder_controller_workflow_summary(
        mode=mode,
        executed_steps=executed_steps,
        stop_reason=stop_reason,
        artifacts=artifacts,
    )
    builder_details = {
        "mode": mode,
        "step_count": len(executed_steps),
        "stop_reason": stop_reason,
        "tool_steps": [str(step.get("tool_name") or "").strip() for step in executed_steps],
        "artifacts": artifacts,
        "executed_steps": executed_steps,
        "observations": observations,
    }
    failed_mode = str(getattr(failed_execution, "mode", "") or "").strip()
    failed_status = str(getattr(failed_execution, "status", "") or "").strip()
    if failed_execution is not None and (
        failed_mode == "tool_preview" or failed_status == "pending_approval"
    ):
        # A permission preview is a PAUSE, not a failed/completed task and not material for a second
        # model call.  Keep the checkpoint resumable, suppress this provisional assistant wording
        # from the durable conversation log, and let the inline approval card carry the exact diff.
        # Once approved, the same logical turn runs again and persists its single final result.
        pending_context = dict(loop_source_context or {})
        pending_context["persist_memory"] = False
        result = agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=str(getattr(failed_execution, "response_text", "") or "Waiting for approval for the proposed action."),
            confidence=0.95,
            source_context=pending_context,
            reason="builder_controller_pending_approval",
            success=False,
            details={"builder_controller": builder_details},
            mode_override="tool_preview",
            task_outcome="pending_approval",
            workflow_summary=workflow_summary,
        )
        result["details"] = {"builder_controller": builder_details}
        return result
    direct_response = agent._builder_controller_direct_response(
        effective_input=effective_input,
        executed_steps=executed_steps,
    )
    if direct_response is not None:
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=direct_response,
            confidence=0.95,
            source_context=loop_source_context,
            reason="builder_controller_direct_response",
            runtime_event_details={"builder_controller": builder_details},
        )
        result["mode"] = "tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only")
        # A failed execution is STRUCTURED failure here too: success and the task outcome are
        # stamped on this branch exactly as the pipeline branch stamps them, so a caller
        # reading the typed fields — not just the mode — sees the turn failed.
        result["success"] = False if failed_execution is not None else None
        result["task_outcome"] = "failed" if failed_execution is not None else None
        result["workflow_summary"] = workflow_summary
        result["details"] = {"builder_controller": builder_details}
        return result
    if mode == "workflow" and executed_steps and failed_execution is None:
        response_text = agent._append_builder_artifact_citations(
            agent._render_tool_loop_response(
                final_message=degraded,
                executed_steps=executed_steps,
                include_step_summary=True,
            ),
            artifacts=artifacts,
        )
        result = agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=response_text,
            confidence=0.9,
            source_context=loop_source_context,
            reason="builder_controller_workflow_response",
            success=True,
            details={"builder_controller": builder_details},
            mode_override="tool_executed",
            task_outcome="success",
            workflow_summary=workflow_summary,
        )
        result["details"] = {"builder_controller": builder_details}
        return result
    if agent._is_chat_truth_surface(loop_source_context):
        result = agent._chat_surface_model_wording_result(
            session_id=session_id,
            user_input=effective_input,
            source_context=loop_source_context,
            persona=load_active_persona_fn(agent.persona_id),
            interpretation=interpretation,
            task_class=str(classification.get("task_class") or "integration_orchestration"),
            response_class=agent.ResponseClass.GENERIC_CONVERSATION,
            reason="builder_controller_model_wording",
            model_input=agent._chat_surface_builder_model_input(
                user_input=effective_input,
                observations=observations,
            ),
            fallback_response=degraded,
            tool_backing_sources=agent._builder_controller_backing_sources(executed_steps),
            response_postprocessor=lambda text: agent._append_builder_artifact_citations(
                builder_user_visible_text(text), artifacts=artifacts
            ),
        )
        result["mode"] = "tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only")
        # Structured failure on the chat-surface branch too: a failed execution stamps
        # success=False and task_outcome="failed" here exactly as the pipeline branch does,
        # so the typed fields — not only the mode — tell the caller the turn failed.
        result["success"] = False if failed_execution is not None else None
        result["task_outcome"] = "failed" if failed_execution is not None else None
        result["workflow_summary"] = workflow_summary
        result["details"] = {"builder_controller": builder_details}
        return result

    response_text = degraded
    if executed_steps:
        response_text = agent._append_builder_artifact_citations(
            agent._render_tool_loop_response(
                final_message=degraded,
                executed_steps=executed_steps,
                include_step_summary=True,
            ),
            artifacts=artifacts,
        )
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=effective_input,
        response=response_text,
        confidence=0.84 if executed_steps and failed_execution is None else 0.58,
        source_context=loop_source_context,
        reason="builder_controller_pipeline",
        success=bool(executed_steps) and failed_execution is None,
        details={"builder_controller": builder_details},
        mode_override="tool_failed" if failed_execution is not None else ("tool_executed" if executed_steps else "advice_only"),
        task_outcome="failed" if failed_execution is not None else ("success" if executed_steps else "advice_only"),
        workflow_summary=workflow_summary,
    )


def workspace_build_verification(
    *,
    target: dict[str, str],
    source_context: dict[str, object],
    execute_runtime_tool_fn: Any,
) -> dict[str, Any] | None:
    language = str(target.get("language") or "")
    root_dir = str(target.get("root_dir") or "").rstrip("/")
    if language != "python" or not root_dir:
        return {"status": "skipped", "ok": False, "response_text": "Verification skipped for non-Python scaffold."}
    # Build from sys.executable, not a literal "python3" (absent on Windows). shlex.join
    # quotes the interpreter path + target so the sandbox's POSIX shlex.split round-trips.
    compile_command = shlex.join([sys.executable, "-m", "compileall", "-q", f"{root_dir}/src"])
    execution = execute_runtime_tool_fn(
        "sandbox.run_command",
        {"command": compile_command},
        source_context=source_context,
        trusted_local_only=True,
    )
    if execution is None:
        return {"status": "not_run", "ok": False, "response_text": "Verification did not run."}
    return {
        "status": execution.status,
        "ok": execution.ok,
        "response_text": execution.response_text,
        "details": dict(execution.details),
    }


def workspace_build_response(
    *,
    target: dict[str, str],
    write_results: list[dict[str, Any]],
    write_failures: list[str],
    verification: dict[str, Any] | None,
    sources: list[dict[str, str]],
) -> str:
    lines = [
        f"Wrote a {target['platform']} {target['language']} scaffold under `{target['root_dir']}`."
        if target["platform"] != "generic"
        else f"Wrote a generic {target['language']} workspace starter under `{target['root_dir']}`."
    ]
    if write_results:
        lines.append("Files written:")
        lines.extend(f"- {item['path']}" for item in write_results[:8])
    if sources:
        lines.append("Sources used:")
        lines.extend(f"- {item['title']} [{item['url']}]" for item in sources[:3])
    verification_status = str((verification or {}).get("status") or "")
    verification_text = str((verification or {}).get("response_text") or "").strip()
    if verification_status == "executed":
        lines.append("Verification:")
        lines.append(f"- {verification_text}")
    elif verification_status == "skipped":
        lines.append("Verification skipped for this scaffold type.")
    if write_failures:
        lines.append("Write failures:")
        lines.extend(f"- {item}" for item in write_failures[:4])
    return "\n".join(lines)


def _run_model_build(
    agent: Any,
    *,
    task: Any,
    effective_input: str,
    classification: dict[str, Any],
    interpretation: Any,
    session_id: str,
    source_context: dict[str, object],
    target: dict[str, str],
    load_active_persona_fn: Any,
) -> dict[str, Any]:
    """Real agentic build: model -> files -> tests -> fix. Writes are workspace-confined; the report
    is truthful about whether the tests actually passed."""
    import os

    from core.agent_runtime.builder import app_builder, mutation_scope, pinned_generation
    from core.runtime_provider_defaults import default_runtime_model_tag

    ctx = dict(source_context or {})
    # Resolved HERE as well as in the profile, not threaded through it. This lane is the last thing
    # between a request and a `workspace.write_file`, and an authority boundary that only holds when
    # an upstream caller remembers to pass it is not a boundary. Both sites call the same resolver
    # on the same sentence, so they cannot disagree.
    scope = mutation_scope.resolve_mutation_scope(
        effective_input,
        workspace_root=str(ctx.get("workspace") or ctx.get("workspace_root") or ""),
    )
    if scope.is_exact:
        # The operator named the destination, or named none and meant the workspace root. A build
        # directory derived from a digest of their sentence is not a destination they asked for.
        target_rel = scope.root_dir
    else:
        target_rel = str(target.get("root_dir") or "generated/app").strip().strip("/") or "generated/app"
    base_url = builder_ollama_base_url(os.environ)
    model_tag = str(default_runtime_model_tag() or "").strip() or "qwen2.5:7b"

    # One budget for the whole build, so the 65 possible generations share a single ceiling, and one
    # source_context, so every one of them is announced on the operator's event stream.
    generation_budget = BuilderGenerationBudget()

    # WHO GENERATES THE FILES. Selecting a model changes who reasons, and a build is reasoning that
    # ends in source code -- so a pinned turn's files must come from the pinned model. The local
    # lane below hardcodes the runtime's own Ollama tag, which is right when nothing is pinned and a
    # silent substitution the moment something is.
    #
    # This used to be settled by refusing the whole turn (`_should_run_builder_controller` returned
    # False for a pinned model). Measured on the installed build, the refusal did not produce a
    # refusal: the turn fell through to the selected model's tool loop, that model did not call
    # `workspace.write_file`, and the operator got "I couldn't map that cleanly to a real action"
    # after 75.6s. Pinning a model meant you could not build.
    pinned_model = pinned_generation.requested_model_id(ctx)
    pinned_manifest = pinned_generation.resolve_pinned_manifest(agent, ctx) if pinned_model else None
    if pinned_model and pinned_manifest is None:
        # Named a model, and it resolves to no provider on this machine. Say so, name it, and offer
        # the lane that works -- never quietly hand the build to a different model.
        return agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=pinned_generation.pinned_build_refusal(
                requested_model=pinned_model,
                reason="that model is not a provider this runtime can reach for generation",
                workspace_hint=str(ctx.get("workspace") or ctx.get("workspace_root") or ""),
            ),
            confidence=0.9,
            source_context=ctx,
            reason="builder_pinned_model_unavailable",
            success=False,
            task_outcome="failed",
            mode_override="advice_only",
        )
    if pinned_manifest is not None:
        generate_fn = pinned_generation.build_pinned_generate_fn(
            agent,
            manifest=pinned_manifest,
            task=task,
            source_context=ctx,
            budget=generation_budget,
        )
    else:
        # The canonical LocalModelPolicy: the unpinned build lane generates on the runtime's
        # LOCAL Ollama. Under a disabled policy that lane cannot run at all — refuse the build
        # with the typed reason (never hand the files to some other lane silently). A pinned
        # cloud manifest above still builds.
        from core.local_model_policy import local_models_enabled

        if not local_models_enabled():
            return agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response=(
                    "I can't run local app builds on this runtime: local models are disabled by "
                    "policy. Pin a cloud model for the build and I'll use that lane."
                ),
                confidence=0.9,
                source_context=ctx,
                reason="builder_local_lane_disabled",
            )
        generate_fn = build_ollama_generate_fn(
            base_url=base_url,
            model_tag=model_tag,
            source_context=ctx,
            budget=generation_budget,
        )

    # The rest of this build's workspace plan, so the permission controller can offer ONE bounded
    # grant for it instead of a prompt per file. The build lane fills this in before its first
    # mutation (see `app_builder.build_app_from_spec`); until then it is empty and every call is
    # gated exactly as it was.
    pending_plan: list[dict[str, Any]] = []

    def announce_plan_fn(planned: list[dict[str, Any]]) -> None:
        pending_plan[:] = [dict(call) for call in planned]

    def _claim_from_plan(intent: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        """Drop this call from the plan and return what is left -- the batch it heads.

        Matched on the exact arguments the plan announced, so a call the build makes that was never
        planned (a fix-loop rewrite, a test command) heads no batch and keeps its own prompt.
        """
        for index, call in enumerate(pending_plan):
            if str(call.get("intent") or "") == intent and dict(call.get("arguments") or {}) == dict(arguments):
                del pending_plan[index]
                return [dict(item) for item in pending_plan]
        return []

    def run_tool_fn(
        intent: str, arguments: dict[str, Any], *, trusted_local_only: bool = False
    ) -> app_builder.ToolOutcome:
        agent._emit_runtime_event(
            ctx,
            event_type="tool_selected",
            message=f"Running {intent}.",
            tool_name=intent,
        )
        remaining = _claim_from_plan(intent, dict(arguments))
        # Passed per call rather than merged into `ctx`: the controller reads it while gating THIS
        # head and nothing else carries it onward. Same shape the research tool loop uses.
        gating_context = {**ctx, PENDING_BATCH_CALLS_KEY: remaining} if remaining else ctx
        result = agent._execute_tool_intent(
            {"intent": intent, "arguments": dict(arguments)},
            task_id=task.task_id,
            session_id=session_id,
            source_context=gating_context,
            hive_activity_tracker=agent.hive_activity_tracker,
            public_hive_bridge=agent.public_hive_bridge,
            trusted_local_only=trusted_local_only,
        )
        if result is None or not bool(getattr(result, "handled", False)):
            return app_builder.ToolOutcome(ok=False, response_text="")
        agent._emit_runtime_event(
            ctx,
            event_type=str(getattr(result, "mode", "") or "tool_failed"),
            message=str(getattr(result, "response_text", "") or ""),
            tool_name=intent,
            status=str(getattr(result, "status", "") or ""),
            approval_request=dict((getattr(result, "details", {}) or {}).get("approval_request") or {}),
        )
        return app_builder.ToolOutcome(
            ok=bool(getattr(result, "ok", False)),
            response_text=str(getattr(result, "response_text", "") or ""),
            details=dict(getattr(result, "details", {}) or {}),
        )

    # "Prove the bug -- write the smallest failing test and run it" wants a test that FAILS. Without
    # telling the build lane that, its fix loop spends three rounds making the failure go away, and
    # a green suite gets reported as success -- a fabricated proof. The subject travels with it
    # because the raw sentence is otherwise the builder's only channel, which is how a request about
    # an Apache codec produced an unrelated security.py.
    from core.agent_runtime.build_request_intent import looks_like_test_artifact_request

    proving_a_bug = looks_like_test_artifact_request(effective_input)
    build_report = app_builder.build_app_from_spec(
        request=effective_input,
        target_rel=target_rel,
        source_context=ctx,
        generate_fn=generate_fn,
        run_tool_fn=run_tool_fn,
        announce_plan_fn=announce_plan_fn,
        expected_test_outcome="fail" if proving_a_bug else "unspecified",
        subject_paths=_audited_subject_paths(ctx),
        scope=scope,
    )
    response = app_builder.render_app_build_response(build_report)
    # Name the absolute destination: the operator must never have to ask where a build went.
    _ws_root = str(ctx.get("workspace") or ctx.get("workspace_root") or "").strip().rstrip("/")
    _target_dir = str(build_report.target_dir or "").strip("/")
    if _ws_root and _target_dir and not _target_dir.startswith(("/", "~")):
        response = f"{response}\n\nLocation: `{_ws_root}/{_target_dir}`"
    # A build cut short by the ceiling is not a build that finished. Reporting it as success is the
    # exact failure this project keeps removing: a completion claim with nothing behind it.
    succeeded = build_finished_successfully(
        files_written=build_report.files_written,
        tests_ran=build_report.tests_ran,
        tests_passed=build_report.tests_passed,
        budget_exhausted_reason=generation_budget.exhausted_reason,
        proof_failed=build_report.proof_failed,
    )
    note = early_stop_note(
        generation_budget.exhausted_reason, files_written=build_report.files_written
    )
    if note:
        response = f"{response}\n\n{note}"
    if pinned_manifest is not None:
        if not build_report.files_written:
            # The operator's model was asked and produced nothing usable. The generic build report
            # would say "0 files"; it would not say that the MODEL CHOICE is why. Name it.
            return agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=effective_input,
                response=pinned_generation.pinned_build_refusal(
                    requested_model=pinned_model,
                    reason=pinned_generation.pinned_failure_reason(generation_budget),
                    workspace_hint=str(ctx.get("workspace") or ctx.get("workspace_root") or ""),
                ),
                confidence=0.9,
                source_context=ctx,
                reason="builder_pinned_model_produced_nothing",
                # A refused build is a FAILED build, not a completed one: the action lane's
                # failure branch closes the turn's checkpoint as failed and emits task_failed, so
                # the trace no longer shows "Completed" beside a turn that wrote nothing.
                success=False,
                task_outcome="failed",
                mode_override="advice_only",
                details={
                    "builder_pinned_refusal": {
                        "requested_model": pinned_model,
                        "provider_id": str(getattr(pinned_manifest, "provider_id", "") or ""),
                        "generation_error": str(generation_budget.last_error or ""),
                        "generations": generation_budget.calls,
                        "generation_seconds": round(generation_budget.seconds_spent, 1),
                        "provider_contacted": not str(generation_budget.last_error or "").startswith(
                            pinned_generation.AUTHORIZATION_REFUSED_PREFIX
                        ),
                        "files_written": list(build_report.files_written),
                    }
                },
            )
        # Attribution is STATED, never assumed. The whole reason a pinned build was refused before
        # is that the operator could not tell which model wrote the files.
        response = f"{response}\n\n" + pinned_generation.pinned_build_attribution(
            requested_model=pinned_model,
            provider_id=str(getattr(pinned_manifest, "provider_id", "") or ""),
            model_id=str(getattr(pinned_manifest, "model_name", "") or ""),
        )
    return agent._action_fast_path_result(
        task_id=task.task_id,
        session_id=session_id,
        user_input=effective_input,
        response=response,
        confidence=0.85 if succeeded else 0.6,
        source_context=ctx,
        reason="builder_model_build",
        success=succeeded,
        details={
            "builder_model_build": {
                "target_dir": build_report.target_dir,
                "files_written": build_report.files_written,
                # What the request actually authorized, beside what was written -- the two used to
                # be incomparable, which is why a one-file request that produced a four-file
                # scaffold left a receipt that read as a clean success.
                "mutation_scope": scope.as_dict(),
                "paths_refused": list(build_report.paths_refused),
                "commands_refused": list(build_report.commands_refused),
                "tests_ran": build_report.tests_ran,
                "tests_passed": build_report.tests_passed,
                "fix_rounds": build_report.fix_rounds,
                "generations": generation_budget.calls,
                "generation_seconds": round(generation_budget.seconds_spent, 1),
                "budget_exhausted": generation_budget.exhausted_reason or "",
            }
        },
        mode_override="tool_executed" if build_report.files_written else "advice_only",
        task_outcome="success" if succeeded else "failed",
        workflow_summary=(
            # An exact build with no named folder targets the workspace root, and `` in a summary
            # line reads as a missing value rather than as the place the files went.
            f"- model build in `{build_report.target_dir or 'the workspace root'}`: "
            f"{len(build_report.files_written)} file(s); "
            f"tests {'passed' if build_report.tests_passed else ('failed' if build_report.tests_ran else 'not run')}"
        ),
    )
