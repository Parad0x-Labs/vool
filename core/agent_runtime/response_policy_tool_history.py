from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from core.secret_redaction import redact_secrets

# How much command output the next reasoning step is allowed to see inline. Sized so an ordinary
# command (a directory listing, a short test summary) arrives whole, while a 50k-line log still
# cannot swamp a prompt that is already ~17.5k characters. stderr gets a smaller share because a
# failure message is short and its head is the useful part.
_INLINE_STDOUT_MAX_LINES = 40
# Matched to the line budget rather than picked independently: a real `ls -la` row is ~70 characters,
# so a 1,600-char cap clipped a 36-line listing the line cap had already allowed through.
_INLINE_STDOUT_MAX_CHARS = 40 * 70
_INLINE_STDERR_MAX_LINES = 16
_INLINE_STDERR_MAX_CHARS = 700


def _bounded_output_slice(text: str, *, max_lines: int, max_chars: int) -> str:
    """Head+tail excerpt of command output, with an explicit marker for what was left out.

    Keeping both ends matters: the head carries what the command started to say, and the tail carries
    the summary line and the error a runner prints last. The marker is worded so the model reports an
    excerpt as an excerpt instead of implying it saw everything.
    """
    body = redact_secrets(str(text or "")).strip("\n")
    if not body:
        return ""
    lines = body.splitlines()
    if len(lines) <= max_lines and len(body) <= max_chars:
        return body
    head_lines = max(1, (max_lines * 2) // 3)
    tail_lines = max(1, max_lines - head_lines)
    omitted = len(lines) - head_lines - tail_lines
    if omitted <= 0:
        excerpt = body[:max_chars]
        return excerpt.rstrip() + f"\n… [truncated, {len(body) - len(excerpt)} more characters in the artifact]"
    excerpt = "\n".join(
        [*lines[:head_lines], f"… [{omitted} more line(s) omitted -- full output is in the artifact]", *lines[-tail_lines:]]
    )
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars].rstrip() + "\n… [truncated -- full output is in the artifact]"
    return excerpt


def _bounded_safe_summary(text: str, *, limit: int = 500) -> str:
    compact = " ".join(redact_secrets(str(text or "")).split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


def _receipt_reference(
    *,
    execution: Any,
    tool_name: str,
    receipt: dict[str, Any] | None,
) -> dict[str, str]:
    details = dict(getattr(execution, "details", {}) or {})
    clean_tool_name = str(tool_name or getattr(execution, "tool_name", "") or "tool").strip() or "tool"
    response_text = str(getattr(execution, "response_text", "") or "").strip()
    status = str(getattr(execution, "status", "") or "executed").strip() or "executed"
    safe_result = _bounded_safe_summary(response_text or status)
    safe_summary = _bounded_safe_summary(f"{clean_tool_name}: {safe_result}")
    supplied = dict(receipt or {})
    receipt_id = str(
        supplied.get("receipt_id")
        or details.get("receipt_id")
        or details.get("receipt_key")
        or details.get("tool_call_id")
        or ""
    ).strip()
    if not receipt_id:
        digest_input = json.dumps(
            {"tool_name": clean_tool_name, "status": status, "safe_summary": safe_summary},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        receipt_id = f"tool-receipt-{hashlib.sha256(digest_input.encode('utf-8')).hexdigest()}"
    return {"receipt_id": receipt_id, "safe_summary": safe_summary}


def _redact_observation_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact_observation_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact_observation_value(item) for key, item in value.items()}
    return value


def append_tool_result_to_source_context(
    agent: Any,
    source_context: dict[str, Any] | None,
    *,
    execution: Any,
    tool_name: str,
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = dict(source_context or {})
    history = list(updated.get("conversation_history") or [])
    observation_message = tool_history_observation_message(
        agent,
        execution=execution,
        tool_name=tool_name,
        receipt=receipt,
    )
    observation_payload = tool_history_observation_payload(
        execution=execution,
        tool_name=tool_name,
        receipt=receipt,
    )
    observations = [
        dict(item)
        for item in list(updated.get("runtime_tool_observations") or [])
        if isinstance(item, dict)
    ]
    if not observations or observations[-1] != observation_payload:
        observations.append(observation_payload)
    # This typed same-turn channel is consumed directly by prompt assembly.  Conversation history
    # alone is insufficient because canonical structured dialogue may outrank the caller-provided
    # history and hide freshly executed tool results from the next model step.
    # 12 until 2026-08-03, from when one tool ran per model round. A batched round now appends up
    # to 8, so a 29-tool turn evicted its early reads and the model re-requested files it had
    # already been given - measured live: README.md read three times in one 9-round turn.
    #
    # The real bound is the CHARACTER budget in `_runtime_tool_observation_message`, which shares
    # the window fairly, clips what will not fit and names what it drops. This cap only decides how
    # many candidates that budget gets to choose between, so a generous number costs nothing: 32
    # ordinary reads render in ~6k of the 12k window, and 32 large ones are clipped by the same
    # rule that already handles 12.
    # The store's own eviction is COUNTED, because the renderer cannot infer it. The renderer names
    # what IT withholds, but a result dropped here never reaches it, so a turn that ran 96 tools
    # would deliver 32 and report nothing missing.
    #
    # That shape is now reachable: the loop allows 12 model rounds and a round can append 8 batch
    # members. Without this counter, raising the round budget silently multiplies exactly the loss
    # every other fix today exists to remove.
    evicted = max(0, len(observations) - 32)
    if evicted:
        updated["runtime_tool_observations_evicted"] = int(
            updated.get("runtime_tool_observations_evicted") or 0
        ) + evicted
    updated["runtime_tool_observations"] = observations[-32:]
    if history and history[-1] == observation_message:
        updated["conversation_history"] = history[-12:]
        return updated
    history.append(observation_message)
    updated["conversation_history"] = history[-12:]
    return updated


def normalize_tool_history_message(agent: Any, item: dict[str, Any]) -> dict[str, str]:
    role = str(item.get("role") or "").strip().lower()
    content = str(item.get("content") or "").strip()
    if role != "assistant" or not content.startswith("Real tool result from `"):
        return {"role": role, "content": content}
    match = re.match(r"^Real tool result from `([^`]+)`:\s*(.*)$", content, re.DOTALL)
    if not match:
        return {"role": role, "content": content}
    tool_name = str(match.group(1) or "").strip() or "tool"
    response_text = str(match.group(2) or "").strip()
    safe_summary = _bounded_safe_summary(f"{tool_name}: {response_text or 'No tool output returned.'}")
    digest_input = json.dumps(
        {"tool_name": tool_name, "safe_summary": safe_summary},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    receipt_reference = {
        "receipt_id": f"tool-receipt-{hashlib.sha256(digest_input.encode('utf-8')).hexdigest()}",
        "safe_summary": safe_summary,
    }
    return {
        "role": "user",
        "content": agent._tool_history_observation_prompt(receipt_reference),
    }


def tool_surface_for_history(tool_name: str) -> str:
    lowered = str(tool_name or "").strip().lower()
    if lowered.startswith("web.") or lowered.startswith("browser."):
        return "web"
    if lowered.startswith("workspace."):
        return "workspace"
    if lowered.startswith("sandbox."):
        return "sandbox"
    if lowered.startswith("operator."):
        return "local_operator"
    if lowered.startswith("hive."):
        return "hive"
    return "runtime_tool"


#: What a model must still see of a code task result when its observation line has to be clipped: the outcome,
#: where the task stands and which approved repairs wait, the step's own evidence, then the lawful next actions.
_CODE_TASK_OBSERVATION_PRIORITY = (
    "intent", "ok", "status", "stage", "pending_repairs", "evidence", "reason", "next",
    "verification_failed", "unit_in_progress", "step_id", "task_id",
)
#: Bulk an evidence summary never carries: file bytes, raw command output, previews and receipts.
_EVIDENCE_SKIP = frozenset({
    "schema", "intent", "tool_surface", "ok", "status", "mode", "observation", "content", "lines", "stdout",
    "stderr", "stdout_excerpt", "stderr_excerpt", "response_preview", "receipt_id", "safe_summary",
    "artifact_refs", "artifacts",
})
_EVIDENCE_TEXT_LIMIT = 240
_EVIDENCE_ITEMS_LIMIT = 8


def _bounded_evidence(observation: dict[str, Any]) -> dict[str, Any]:
    """The facts an inner tool chose to report about its own result, bounded: long text is shortened and
    long lists are cut to their first items with the rest counted, so a summary never grows with the output."""

    def bound(value: Any) -> Any:
        if isinstance(value, str):
            text = " ".join(value.split())
            return text if len(text) <= _EVIDENCE_TEXT_LIMIT else text[:_EVIDENCE_TEXT_LIMIT] + "…"
        if isinstance(value, list):
            items = [bound(item) for item in value[:_EVIDENCE_ITEMS_LIMIT]]
            if len(value) > _EVIDENCE_ITEMS_LIMIT:
                items.append(f"…{len(value) - _EVIDENCE_ITEMS_LIMIT} more")
            return items
        if isinstance(value, dict):
            return {str(key): bound(item) for key, item in value.items() if key not in _EVIDENCE_SKIP}
        return value

    return {
        str(key): bound(value)
        for key, value in observation.items()
        if key not in _EVIDENCE_SKIP and value not in (None, "", [], {})
    }


def tool_history_observation_payload(
    *,
    execution: Any,
    tool_name: str,
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details = dict(getattr(execution, "details", {}) or {})
    observation = details.get("observation")
    if isinstance(observation, dict) and observation:
        payload = dict(observation)
    else:
        response_text = str(getattr(execution, "response_text", "") or "").strip()
        payload = {
            "schema": "tool_observation_v1",
            "intent": str(tool_name or getattr(execution, "tool_name", "") or "tool").strip() or "tool",
            "tool_surface": tool_surface_for_history(str(tool_name or getattr(execution, "tool_name", "") or "tool")),
            "ok": bool(getattr(execution, "ok", False)),
            "status": str(getattr(execution, "status", "") or "executed").strip() or "executed",
            "response_preview": response_text[:1800] if response_text else "No tool output returned.",
        }
    payload.setdefault("mode", str(getattr(execution, "mode", "") or "").strip())
    intent = str(payload.get("intent") or tool_name or "").strip().lower()
    if intent.startswith("code.task."):
        # The task wrapper owns state, but its inner tool owns evidence formatting.
        # Preserve both: a success label alone hides the source bytes and next stage.
        for key, value in details.items():
            if key not in {"observation", "permission", "tool_result", "receipts", "intent"}:
                payload[key] = value
        payload["observation_priority"] = list(_CODE_TASK_OBSERVATION_PRIORITY)
        inner_intent = str(details.get("intent") or "").strip()
        inner_result = details.get("tool_result")
        if isinstance(inner_result, dict) and inner_intent and not inner_intent.startswith("code.task."):
            from types import SimpleNamespace

            inner_execution = SimpleNamespace(
                ok=bool(getattr(execution, "ok", False)),
                status=str(getattr(execution, "status", "")),
                response_text=str(getattr(execution, "response_text", "")),
                details={"observation": {
                    **{key: value for key, value in inner_result.items() if key != "observation"},
                    "intent": inner_intent,
                    "ok": bool(getattr(execution, "ok", False)),
                    "status": str(getattr(execution, "status", "")),
                }},
            )
            payload["tool_result"] = tool_history_observation_payload(
                execution=inner_execution, tool_name=inner_intent,
            )
            inner_observation = inner_result.get("observation")
            evidence = _bounded_evidence(inner_observation if isinstance(inner_observation, dict) else inner_result)
            if evidence:
                payload["evidence"] = evidence
    # A huge test log must not dominate context or get copied verbatim into chat -- but dropping the
    # output ENTIRELY left the model unable to answer the very question that caused the call. Measured
    # live: "run the command: ls -la" executed fine (exit 0, 36 lines of stdout) and the observation
    # said only "stdout 36 line(s); full output saved in artifact(s): command_output", so the reply was
    # "I'm ready to help. Let me check the current directory for you." -- the listing existed and the
    # model never saw it. A bounded head/tail slice keeps the context guarantee and restores the
    # answer; the artifact still holds the full text for anything longer.
    if intent == "sandbox.run_command" or intent in {
        "workspace.run_tests",
        "workspace.run_lint",
        "workspace.run_formatter",
    }:
        stdout = str(payload.pop("stdout", "") or "")
        stderr = str(payload.pop("stderr", "") or "")
        artifact_refs = [
            str(item.get("artifact_id") or item.get("artifact_type") or "").strip()
            for item in list(details.get("artifacts") or [])
            if isinstance(item, dict) and str(item.get("artifact_id") or item.get("artifact_type") or "").strip()
        ]
        facts = [f"status {payload.get('status') or getattr(execution, 'status', '')}"]
        if payload.get("returncode") is not None:
            facts.append(f"exit code {payload.get('returncode')}")
        if stdout:
            facts.append(f"stdout {len(stdout.splitlines())} line(s)")
        if stderr:
            facts.append(f"stderr {len(stderr.splitlines())} line(s)")
        failure = " ".join(str(payload.get("failure_summary") or "").split()).strip()
        if failure:
            facts.append(f"failure summary: {failure[:360]}")
        if artifact_refs:
            payload["artifact_refs"] = artifact_refs[:8]
            facts.append(f"full output saved in artifact(s): {', '.join(artifact_refs[:3])}")
        stdout_slice = _bounded_output_slice(stdout, max_lines=_INLINE_STDOUT_MAX_LINES, max_chars=_INLINE_STDOUT_MAX_CHARS)
        stderr_slice = _bounded_output_slice(stderr, max_lines=_INLINE_STDERR_MAX_LINES, max_chars=_INLINE_STDERR_MAX_CHARS)
        if stdout_slice:
            payload["stdout_excerpt"] = stdout_slice
            facts.append(f"stdout:\n{stdout_slice}")
        if stderr_slice:
            payload["stderr_excerpt"] = stderr_slice
            facts.append(f"stderr:\n{stderr_slice}")
        payload["response_preview"] = "; ".join(facts)
    elif intent in {"workspace.list_files", "workspace.list_tree", "machine.list_directory"}:
        key = "paths" if isinstance(payload.get("paths"), list) else "entries"
        entries = list(payload.pop(key, []) or [])
        # This payload rides in tool history on every subsequent turn, so it is a RECURRING cost
        # and stays bounded — unlike the one-shot tool result, which now carries the full listing.
        # What was missing was never the sample size; it was the total. 12 of 682 rendered as
        # "Listed 12 item(s)" and the model had no way to know a tree existed behind it.
        sample = [str(item) for item in entries[:60]]
        if sample:
            payload["sample_paths"] = sample
        count = int(payload.get("count") or len(entries))
        payload["count"] = count
        total = payload.get("total")
        total = int(total) if isinstance(total, (int, float)) else None
        # "Results were truncated" does not tell a model what it is missing, and the count it sat
        # next to was the PAGE, not the tree. Named totals plus an explicit ban on absence claims
        # is the difference between a model that pages and a model that concludes "no tests exist".
        payload["response_preview"] = (
            (f"Listed {count} of {total} item(s)" if total and total > count else f"Listed {count} item(s)")
            + (f" under {payload.get('path')}" if payload.get("path") else "")
            + (f". Sample: {', '.join(sample)}" if sample else ".")
            + (
                f" TRUNCATED — {total - count} item(s) not shown. This is an alphabetical prefix, "
                "not the whole set; it cannot support any conclusion that something is ABSENT."
                if payload.get("truncated") and total and total > count
                else (" Results were truncated." if payload.get("truncated") else "")
            )
        )[:4000]
    elif not payload.get("response_preview"):
        response_text = str(getattr(execution, "response_text", "") or "").strip()
        if response_text:
            payload["response_preview"] = response_text[:1800]
    reference = _receipt_reference(execution=execution, tool_name=tool_name, receipt=receipt)
    redacted_payload = _redact_observation_value(payload)
    redacted_payload["receipt_id"] = reference["receipt_id"]
    redacted_payload["safe_summary"] = reference["safe_summary"]
    return redacted_payload


def tool_history_observation_message(
    agent: Any,
    *,
    execution: Any,
    tool_name: str,
    receipt: dict[str, Any] | None = None,
) -> dict[str, str]:
    observation = _receipt_reference(
        execution=execution,
        tool_name=tool_name,
        receipt=receipt,
    )
    return {
        "role": "user",
        "content": agent._tool_history_observation_prompt(observation),
    }
