from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from core.secret_redaction import redact_secrets


def _internal_payload(text: str) -> bool:
    """Scaffolding or a bare monologue is not a synthesis. Fail-soft."""

    try:
        from core.agent_runtime.response import internal_payload_or_monologue

        return internal_payload_or_monologue(text)
    except Exception:
        return False


def apply_interaction_transition(
    agent: Any,
    session_id: str,
    result: Any,
    *,
    session_hive_state_fn: Any,
    set_hive_interaction_state_fn: Any,
) -> None:
    if not session_id:
        return
    state = session_hive_state_fn(session_id)
    payload = dict(state.get("interaction_payload") or {})
    preserve_task_context = bool(
        str(state.get("interaction_mode") or "")
        in {
            "hive_nudge_shown",
            "hive_task_selection_pending",
            "hive_task_active",
            "hive_task_status_pending",
        }
        and (
            payload.get("active_topic_id")
            or agent._interaction_pending_topic_ids(state)
            or list(state.get("pending_topic_ids") or [])
        )
    )
    if result.response_class == agent.ResponseClass.SMALLTALK:
        if preserve_task_context:
            return
        set_hive_interaction_state_fn(session_id, mode="smalltalk", payload={})
        return
    if result.response_class == agent.ResponseClass.UTILITY_ANSWER:
        if preserve_task_context:
            return
        if (
            str(state.get("interaction_mode") or "").strip().lower() == "utility"
            and str(payload.get("utility_kind") or "").strip().lower() == "time"
            and "current time" in str(result.text or "").lower()
        ):
            return
        set_hive_interaction_state_fn(session_id, mode="utility", payload={})
        return
    if result.response_class == agent.ResponseClass.GENERIC_CONVERSATION:
        if preserve_task_context:
            return
        set_hive_interaction_state_fn(session_id, mode="generic_conversation", payload={})
        return
    if result.response_class in {agent.ResponseClass.SYSTEM_ERROR_USER_SAFE, agent.ResponseClass.TASK_FAILED_USER_SAFE}:
        set_hive_interaction_state_fn(session_id, mode="error_recovery", payload={})
        return
    if result.response_class in {agent.ResponseClass.TASK_LIST, agent.ResponseClass.TASK_SELECTION_CLARIFICATION}:
        set_hive_interaction_state_fn(session_id, mode="hive_task_selection_pending", payload=payload)
        return
    if result.response_class == agent.ResponseClass.TASK_STARTED:
        set_hive_interaction_state_fn(session_id, mode="hive_task_active", payload=payload)
        return
    if result.response_class == agent.ResponseClass.TASK_STATUS:
        set_hive_interaction_state_fn(session_id, mode="hive_task_status_pending", payload=payload)


def task_workflow_summary(
    *,
    classification: dict[str, Any],
    context_result: Any,
    model_execution: dict[str, Any],
    media_analysis: dict[str, Any],
    curiosity_result: dict[str, Any],
    gate_mode: str,
) -> str:
    lines: list[str] = []
    task_class = str(classification.get("task_class") or "unknown")
    lines.append(f"- classified task as `{task_class}`")
    try:
        retrieval_conf = float(context_result.report.retrieval_confidence)
        lines.append(f"- loaded memory/context with retrieval confidence {retrieval_conf:.2f}")
    except Exception:
        pass
    provider = str((model_execution or {}).get("provider_id") or (model_execution or {}).get("source") or "none")
    used_model = bool((model_execution or {}).get("used_model", True))
    lines.append(f"- {'used' if used_model else 'skipped'} model path via `{provider}`")
    media_reason = str((media_analysis or {}).get("reason") or "").strip()
    if media_reason:
        lines.append(f"- media/web evidence status: `{media_reason}`")
    curiosity_mode = str((curiosity_result or {}).get("mode") or "").strip()
    if curiosity_mode:
        lines.append(f"- curiosity/research lane: `{curiosity_mode}`")
    lines.append(f"- execution posture: `{gate_mode}`")
    return "\n".join(lines)


def action_workflow_summary(
    *,
    operator_kind: str,
    dispatch_status: str,
    details: dict[str, Any] | None,
) -> str:
    lines = [f"- recognized operator action `{operator_kind}`", f"- action state: `{dispatch_status}`"]
    info = dict(details or {})
    action_id = str(info.get("action_id") or "").strip()
    if action_id:
        lines.append(f"- action id: `{action_id}`")
    target_path = str(info.get("target_path") or "").strip()
    if target_path:
        lines.append(f"- target: `{target_path}`")
    return "\n".join(lines)


def _git_summary_observation_message(step: dict[str, Any]) -> str:
    observation = step.get("observation")
    if not isinstance(observation, dict):
        return ""
    intent = str(observation.get("intent") or step.get("tool_name") or "").strip()
    if intent != "workspace.git_summary":
        return ""
    repo = str(observation.get("cwd") or "").strip()
    branch = str(observation.get("branch") or "").strip() or "(detached HEAD)"
    commit = str(observation.get("commit") or "").strip() or "unknown"
    dirty = "yes" if bool(observation.get("dirty")) else "no"
    local_branch_count = int(observation.get("local_branch_count") or 0)
    remote_branch_count = int(observation.get("remote_branch_count") or 0)
    total_branch_count = int(observation.get("total_branch_count") or (local_branch_count + remote_branch_count))
    today_date = str(observation.get("today_date") or "").strip()
    yesterday_date = str(observation.get("yesterday_date") or "").strip()
    timezone_label = str(observation.get("timezone_label") or "").strip()
    commit_count_scope = str(observation.get("commit_count_scope") or "").strip()
    lines = [f"Git repo summary for `{repo}`:" if repo else "Git repo summary:"]
    lines.append(f"- current branch: {branch}")
    lines.append(f"- head commit: {commit}")
    lines.append(f"- dirty: {dirty}")
    lines.append(f"- visible branches: {total_branch_count} total ({local_branch_count} local, {remote_branch_count} remote tracking)")
    if today_date:
        lines.append(f"- commits on {today_date}: {int(observation.get('today_commit_count') or 0)}")
    if yesterday_date:
        lines.append(f"- commits on {yesterday_date}: {int(observation.get('yesterday_commit_count') or 0)}")
    if commit_count_scope:
        rendered_scope = (
            "repo-wide unique commits across visible local and remote refs"
            if commit_count_scope == "all_visible_refs_unique"
            else commit_count_scope.replace("_", " ")
        )
        lines.append(f"- commit count scope: {rendered_scope}")
    if timezone_label:
        lines.append(f"- commit day boundary timezone: {timezone_label}")
    return "\n".join(lines)


def _normalized_reply_text(text: str) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def synthesis_echoes_prior_reply(output_text: str, conversation_history: list[dict[str, Any]] | None) -> bool:
    """True when a model synthesis is a copy of an earlier assistant reply rather than
    a new answer. Live incident: with every real lane down, the emergency 0.6b model
    'answered' by copying the previous assistant turn verbatim from the whitespace-
    collapsed prompt transcript, and the copy shipped as the reply to an unrelated
    request. Comparison happens on the same collapse the transcript applies, so the
    exact failure shape is caught; short/trivial texts never match (>=80 chars)."""
    candidate = _normalized_reply_text(output_text)
    if len(candidate) < 80:
        return False
    for item in list(conversation_history or []):
        if not isinstance(item, dict) or str(item.get("role") or "").strip().lower() != "assistant":
            continue
        prior = _normalized_reply_text(str(item.get("content") or ""))
        if len(prior) < 80:
            continue
        if candidate == prior or candidate in prior or prior in candidate:
            return True
    return False


def _step_succeeded(step: dict[str, Any]) -> bool:
    """A recorded attempt is not proof that its requested work completed."""
    if "ok" in step:
        return step["ok"] is True
    return str(step.get("status") or "").lower() in {"executed", "ok", "success"}



def _mailbox_observation_message(executed_steps: list[dict[str, Any]]) -> str:
    """The mail a successful read returned, rendered from its own observation, when no model wrote it.

    Measured 2026-11-10 on 0.7.8 c684e66d: email.read returned the 8 unread messages, every model
    round after it failed, and the person got "1 of 2 tool steps succeeded. Last result: 8
    message(s) matched in INBOX" -- the count of their mail, not the mail. The headers are the
    tool's own evidence (never model text), so listing them is grounded and stays on this machine.
    """
    for step in reversed(executed_steps):
        if str(step.get("tool_name") or "") not in {"email.read", "email.open"} or not _step_succeeded(step):
            continue
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        messages = [item for item in list(observation.get("messages") or []) if isinstance(item, dict)]
        count = observation.get("count")
        if not messages:
            if count == 0:
                return "The mailbox search matched no messages."
            continue
        lines = [f"Here is what the mailbox returned ({len(messages)} message(s)):"]
        for item in messages:
            sender = str(item.get("from") or "(unknown sender)").strip()
            subject = str(item.get("subject") or "(no subject)").strip()
            date = str(item.get("date") or "").strip()
            lines.append(f"- {sender} — {subject}" + (f" ({date})" if date else ""))
            body = str(item.get("body") or item.get("snippet") or "").strip()
            if body:
                lines.append(f"  {body[:400]}")
        omitted = observation.get("messages_omitted")
        if omitted:
            lines.append(f"({omitted} more message(s) matched and are not shown.)")
        return "\n".join(lines)
    return ""

#: How much of a command's own output a reply written without a model shows inline. Longer output
#: stays whole in the turn's saved command output.
_COMMAND_OUTPUT_INLINE_CHARS = 3000


def _command_output_block(text: str) -> str:
    body = str(text or "").rstrip()
    if len(body) > _COMMAND_OUTPUT_INLINE_CHARS:
        body = body[:_COMMAND_OUTPUT_INLINE_CHARS].rstrip() + "\n… (shortened here; the full output is saved with this turn)"
    return "```\n" + body.replace("```", "'''") + "\n```"


def _command_observation_message(executed_steps: list[dict[str, Any]]) -> str:
    """What the last command did and printed, from its own result, when no model wrote the reply.

    Measured 2026-11-12 (served chat, v0.8 line): `python3 build.py` ran, no model wrote the reply,
    and the person got "1 of 1 tool steps succeeded. Last result: Command executed in `.`:" -- the
    first line of the tool's report, cut at its colon, without the output they asked about. The
    command, its exit code and its output are the tool's own evidence, so they are the reply. A
    command that never started (no exit code) keeps the generic line.
    """
    last = executed_steps[-1] if executed_steps else {}
    if str(last.get("tool_name") or "") != "sandbox.run_command":
        return ""
    observation = last.get("observation")
    if not isinstance(observation, dict):
        return ""
    command = " ".join(str(observation.get("command") or "").split()).strip()
    returncode = observation.get("returncode")
    if not command or returncode is None or isinstance(returncode, bool):
        return ""
    try:
        code = int(returncode)
    except (TypeError, ValueError):
        return ""
    stdout = str(observation.get("stdout") or observation.get("stdout_excerpt") or "").rstrip()
    stderr = str(observation.get("stderr") or observation.get("stderr_excerpt") or "").rstrip()
    outcome = "It finished without errors" if code == 0 else f"It stopped with an error (exit code {code})"
    lines: list[str] = []
    if len(executed_steps) > 1:
        completed = sum(_step_succeeded(step) for step in executed_steps)
        lines.append(f"{completed} of {len(executed_steps)} steps succeeded.")
    if stdout:
        lines.append(f"I ran `{command}`. {outcome} and printed:")
        lines.append(_command_output_block(stdout))
    elif stderr:
        lines.append(f"I ran `{command}`. {outcome}.")
    else:
        lines.append(f"I ran `{command}`. {outcome} and printed nothing.")
    if stderr:
        lines.append("Its error output:")
        lines.append(_command_output_block(stderr))
    return "\n\n".join(lines)


def database_rows_answer(executed_steps: list[dict[str, Any]]) -> str:
    """The answer built from the last database query's rows ("" unless that query ran and succeeded)."""
    for step in reversed(executed_steps):
        name = str(step.get("tool_name") or "")
        if not name.startswith("vool-database."):
            continue
        if name == "vool-database.query" and _step_succeeded(step) and isinstance(step.get("observation"), dict):
            return _database_observation_message([step])
        return ""
    return ""


_CLAIM_BOLD_RE = re.compile(r"\*\*([^*\n]{2,60})\*\*|\"([^\"\n]{2,60})\"|`([^`\n]{1,60})`")
_CLAIM_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")


def database_claims_unsupported(answer: str, executed_steps: list[dict[str, Any]], question: str = "") -> bool:
    """Whether the model's answer states a value the turn's database rows do not contain.

    Measured 2026-11-12 (Database run 16e, qwen3:8b): the rows said Yara's loan was returned; the answer said
    "Yes, Yara has **"Beloved"** (book ID 5) ... value `0`" -- a title that no row of the turn held. Names, titles
    and numbers the answer puts in bold, quotes or code must be in a row (or the question); a number may also be a
    count of the rows. Only judged when the turn ran a database query that succeeded."""
    db_steps = [s for s in executed_steps if str(s.get("tool_name") or "").startswith("vool-database.query") and _step_succeeded(s)]
    if not db_steps:
        return False
    corpus_parts = [str(question or "")]
    row_total = 0
    for step in db_steps:
        observation = step.get("observation")
        if isinstance(observation, dict):
            corpus_parts.append(json.dumps(observation, default=str))
            row_total += len(list(observation.get("rows") or []))
    corpus = "\n".join(corpus_parts).lower()
    text = str(answer or "")
    for match in _CLAIM_BOLD_RE.finditer(text):
        value = next((g for g in match.groups() if g), "").strip().lower()
        if value and value not in corpus and not value.isdigit():
            return True
    body = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    for number in _CLAIM_NUMBER_RE.findall(body):
        if number.lower() in corpus:
            continue
        try:
            if float(number) <= max(row_total, 1) and float(number).is_integer():
                continue
        except ValueError:
            pass
        return True
    return False


def _database_observation_message(executed_steps: list[dict[str, Any]]) -> str:
    """The rows a database query returned, rendered from the tool's own observation, when no model wrote the answer.

    Measured 2026-11-12 (Database run 16d, qwen3:8b): the query ran and returned the person's loans, the
    synthesis round failed, and the person got "1 of 3 tool steps succeeded. Last result: Plugin
    `vool-database.query` returned output". The rows are the tool's evidence, never model text."""
    for step in reversed(executed_steps):
        name = str(step.get("tool_name") or "")
        if not name.startswith("vool-database."):
            continue
        observation = step.get("observation")
        if name == "vool-database.query" and _step_succeeded(step) and isinstance(observation, dict):
            columns = [str(c) for c in list(observation.get("columns") or [])]
            rows = [list(r) for r in list(observation.get("rows") or []) if isinstance(r, (list, tuple))]
            if not rows:
                return "The query ran and found no matching rows in the database."
            if len(rows) == 1 and len(columns) == 1:
                return f"{columns[0]}: {rows[0][0]}"
            lines = [f"The database returned {len(rows)} row(s):"]
            for row in rows[:20]:
                lines.append("- " + ", ".join(f"{col}: {val}" for col, val in zip(columns, row)))
            if observation.get("truncated"):
                lines.append("(More rows matched than are shown.)")
            return "\n".join(lines)
        if not _step_succeeded(step):
            detail = str(step.get("summary") or step.get("status") or "").strip()
            return "The database query did not work: " + (detail or "it was refused") + " Nothing was changed."
        return ""
    return ""


def tool_loop_final_message(synthesis: Any, executed_steps: list[dict[str, Any]]) -> str:
    structured = getattr(synthesis, "structured_output", None)
    if isinstance(structured, dict):
        summary = str(structured.get("summary") or structured.get("message") or "").strip()
        bullet_source = structured.get("bullets") or structured.get("steps") or []
        bullets = [str(item).strip() for item in list(bullet_source) if str(item).strip()]
        candidate = ""
        if summary and bullets:
            candidate = summary + "\n" + "\n".join(f"- {item}" for item in bullets[:6])
        elif summary:
            candidate = summary
        # A structured envelope is not proof of a synthesis: a model can stuff its monologue or a
        # fabricated payload into `summary` just as easily as into free text. Same guard as the
        # free-text branch below; a refused candidate falls through to the grounded step summary.
        if candidate and not _internal_payload(candidate):
            return candidate
    output_text = str(getattr(synthesis, "output_text", "") or "").strip()
    if output_text and not _internal_payload(output_text):
        return output_text
    if executed_steps:
        last_step = executed_steps[-1]
        observation_message = _git_summary_observation_message(last_step)
        if observation_message:
            return observation_message
        mailbox_message = _mailbox_observation_message(executed_steps)
        if mailbox_message:
            return mailbox_message
        command_message = _command_observation_message(executed_steps)
        if command_message:
            return command_message
        database_message = _database_observation_message(executed_steps)
        if database_message:
            return database_message
        completed = sum(_step_succeeded(step) for step in executed_steps)
        return (
            f"{completed} of {len(executed_steps)} tool steps succeeded. "
            f"Last result: {str(last_step.get('summary') or last_step.get('status') or 'outcome unconfirmed').strip()}"
        )
    return "I ran the available tools, but I do not have a grounded final synthesis yet."


def render_tool_loop_response(
    *,
    final_message: str,
    executed_steps: list[dict[str, Any]],
    include_step_summary: bool = True,
) -> str:
    message = str(final_message or "").strip()
    if not executed_steps or not include_step_summary:
        return message
    # One result, one user-facing answer: when the final message already restates the
    # last step's result (typical for deterministic tools), the raw step block would
    # render the same data twice -- drop it and keep the single answer.
    if message and _normalized_reply_text(str(executed_steps[-1].get("summary") or "")) and (
        _normalized_reply_text(str(executed_steps[-1].get("summary") or "")) in _normalized_reply_text(message)
    ):
        return message
    lines = ["Real steps completed:" if all(_step_succeeded(step) for step in executed_steps) else "Tool results:"]
    for step in executed_steps:
        tool_name = str(step.get("tool_name") or "tool").strip()
        summary = str(step.get("summary") or step.get("status") or "outcome unconfirmed").strip()
        lines.append(f"- {tool_name}: {summary}")
    if message:
        lines.extend(["", message])
    return "\n".join(lines).strip()


def tool_intent_loop_workflow_summary(
    *,
    executed_steps: list[dict[str, Any]],
    provider_id: str | None,
    validation_state: str,
) -> str:
    completed = sum(_step_succeeded(step) for step in executed_steps)
    lines = [f"- tool loop returned {len(executed_steps)} results; {completed} steps succeeded"]
    if executed_steps:
        step_chain = " -> ".join(str(step.get("tool_name") or "tool").strip() for step in executed_steps[:6])
        if step_chain:
            lines.append(f"- tool chain: `{step_chain}`")
    provider = str(provider_id or "").strip()
    if provider:
        lines.append(f"- tool intent provider: `{provider}`")
    validation = str(validation_state or "").strip()
    if validation:
        lines.append(f"- tool intent validation: `{validation}`")
    lines.append("- execution posture: `tool_executed`")
    return "\n".join(lines)


def tool_step_summary(response_text: str, *, fallback: str) -> str:
    for raw_line in str(response_text or "").splitlines():
        line = " ".join(raw_line.split()).strip(" -")
        if not line:
            continue
        return (line[:157] + "...") if len(line) > 160 else line
    clean_fallback = " ".join(str(fallback or "").split()).strip()
    return clean_fallback or "completed"


def runtime_preview(text: str, *, limit: int = 220) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(1, limit - 3)].rstrip() + "..."


_SENSITIVE_TOOL_ARGUMENT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "credential",
        "mnemonic",
        "passphrase",
        "password",
        "private_key",
        "secret",
        "seed",
        "token",
    }
)

_TERMINAL_TOOL_EVENT_TYPES = frozenset({"tool_executed", "tool_failed", "tool_preview"})
#: Placeholders a lane emits when it has no tool to name. A receipt keyed to one of these asserts an
#: execution that never happened, so it is refused rather than stored.
_NON_TOOL_SENTINELS = frozenset({"unknown", "none", "n/a", "-"})
_TOOL_TARGET_KEYS = (
    "target",
    "target_path",
    "path",
    "directory",
    "file",
    "filename",
    "url",
    "endpoint",
    "repo",
    "repository",
    "topic_id",
    "task_id",
)
_APPROVAL_METADATA_KEYS = frozenset(
    {
        "approval_id",
        "approval_required",
        "approval_requirement",
        "approval_state",
        "approved_by",
        "decision",
        "expires_at",
        "requested_at",
        "scope",
        "state",
    }
)


def _redact_tool_string(value: str) -> str:
    redacted = redact_secrets(str(value or ""))
    if "://" not in redacted or any(character.isspace() for character in redacted):
        return redacted
    try:
        parsed = urlsplit(redacted)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        safe_query = urlencode([(key, "[redacted]") for key, _value in parse_qsl(parsed.query, keep_blank_values=True)])
        return urlunsplit((parsed.scheme, host, parsed.path, safe_query, ""))
    except ValueError:
        return redacted.split("?", 1)[0].split("#", 1)[0]


def redact_tool_arguments(arguments: Any, *, _depth: int = 0) -> Any:
    """Bound and redact tool arguments before they enter runtime event storage."""
    if _depth > 4:
        return "[truncated]"
    if isinstance(arguments, dict):
        result: dict[str, Any] = {}
        for raw_key, value in list(arguments.items())[:64]:
            key = redact_secrets(str(raw_key or ""))[:120]
            normalized = key.lower().replace("-", "_")
            if normalized in _SENSITIVE_TOOL_ARGUMENT_KEYS or any(
                marker in normalized
                for marker in ("api_key", "authorization", "password", "private_key", "secret", "token")
            ):
                result[key] = "[redacted]"
            else:
                result[key] = redact_tool_arguments(value, _depth=_depth + 1)
        return result
    if isinstance(arguments, (list, tuple)):
        return [redact_tool_arguments(item, _depth=_depth + 1) for item in list(arguments)[:64]]
    if isinstance(arguments, str):
        value = _redact_tool_string(arguments)
        return value[:1000] + ("..." if len(value) > 1000 else "")
    if arguments is None or isinstance(arguments, (bool, int, float)):
        return arguments
    return redact_secrets(str(arguments))[:1000]


def _stable_json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_identifier(value: Any, *, limit: int = 200) -> str:
    return runtime_preview(redact_secrets(str(value or "")).strip(), limit=limit)


def _safe_tool_target(arguments: dict[str, Any], details: dict[str, Any]) -> str:
    candidate: Any = None
    for source in (arguments, details):
        for key in _TOOL_TARGET_KEYS:
            if source.get(key) not in (None, "", [], {}):
                candidate = source[key]
                break
        if candidate is not None:
            break
    if candidate is None:
        return ""
    if isinstance(candidate, (dict, list, tuple)):
        rendered = json.dumps(redact_tool_arguments(candidate), ensure_ascii=True, sort_keys=True, default=str)
    else:
        rendered = str(redact_tool_arguments(candidate) or "")
    if "://" in rendered:
        try:
            parsed = urlsplit(rendered)
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            rendered = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError:
            pass
    return runtime_preview(redact_secrets(rendered), limit=500)


def _tool_approval_metadata(details: dict[str, Any], *, event_type: str, status: str, mode: str) -> dict[str, Any]:
    approval: dict[str, Any] = {}
    nested = details.get("approval")
    sources = [nested] if isinstance(nested, dict) else []
    sources.append(details)
    for source in sources:
        for key in _APPROVAL_METADATA_KEYS:
            if key in source and source[key] not in (None, "", [], {}):
                approval[key] = redact_tool_arguments(source[key])
    pending = event_type == "tool_preview" or mode == "tool_preview" or status in {
        "approval_required",
        "pending_approval",
        "simulate_only",
        "user_action_required",
    }
    if pending:
        approval.setdefault("approval_required", True)
        approval.setdefault("approval_state", "pending")
    return approval


def build_tool_action_record(
    source_context: dict[str, Any] | None,
    *,
    event_type: str,
    message: str,
    details: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the safe, durable record attached to terminal tool runtime events."""
    normalized_event = str(event_type or "").strip().lower()
    tool_name = str(details.get("tool_name") or "").strip()
    if normalized_event not in _TERMINAL_TOOL_EVENT_TYPES or not tool_name:
        return None
    # A NAME THAT NAMES NO TOOL IS NOT A TOOL RECEIPT. The guard above rejected only the empty
    # string, so the literal sentinel walked through it. Measured 2026-08-18 on the live store: 55
    # of 92 receipts (60%) carried `tool_name='unknown'`, and every one of them was
    # `provider_did_not_answer` from `research_tool_loop_facade.py` -- emitted when the MODEL
    # provider returned no reply and no tool was ever selected. Its own message says so: "This is a
    # transport failure, not a model output defect." Those rows are model transport failures filed
    # in the tool table, and they were the majority of it.
    #
    # The Activity EVENT is untouched and still shown -- the user needs to see that the provider did
    # not answer. Only the receipt is withheld, because a receipt asserts that a named tool ran.
    # Case-folded: a sentinel is a sentinel however it is spelled, and the guard beside it
    # already normalises with .strip() for the same reason.
    if tool_name.casefold() in _NON_TOOL_SENTINELS:
        return None

    context = dict(source_context or {})
    parameters_value = details.get("arguments")
    parameters = redact_tool_arguments(parameters_value if isinstance(parameters_value, dict) else {})
    if not isinstance(parameters, dict):
        parameters = {}
    status = str(details.get("status") or normalized_event).strip() or normalized_event
    mode = str(details.get("mode") or normalized_event).strip() or normalized_event
    summary = runtime_preview(redact_secrets(str(details.get("summary") or message or status)), limit=220)
    chat_id = _safe_identifier(
        context.get("chat_id")
        or context.get("runtime_session_id")
        or context.get("session_id")
        or ""
    )
    project_id = _safe_identifier(
        context.get("_trusted_project_id") or context.get("project_id")
    )
    actor = _safe_identifier(
        context.get("actor_id") or context.get("actor") or context.get("user_id") or "local_runtime",
        limit=160,
    )
    identifiers = {
        key: _safe_identifier(value)
        for key, value in {
            "checkpoint_id": details.get("checkpoint_id") or context.get("checkpoint_id"),
            "turn_id": details.get("turn_id") or context.get("turn_id"),
            "tool_call_id": details.get("tool_call_id"),
            "request_id": details.get("request_id") or context.get("request_id"),
            "response_id": details.get("response_id") or context.get("response_id"),
        }.items()
        if _safe_identifier(value)
    }
    parameters_hash = _stable_json_hash(parameters)
    identity_payload = {
        "chat_id": chat_id,
        "project_id": project_id,
        "tool_name": tool_name,
        "parameters_hash": parameters_hash,
        "identifiers": identifiers,
    }
    action_id = _safe_identifier(details.get("action_id")) or f"tool-action-{_stable_json_hash(identity_payload)}"
    receipt_id = _safe_identifier(details.get("receipt_id") or details.get("receipt_key"))
    if not receipt_id:
        receipt_id = f"tool-receipt-{_stable_json_hash({'action_id': action_id})}"

    failed = normalized_event == "tool_failed" or mode == "tool_failed"
    outcome = "failed" if failed else "pending_approval" if normalized_event == "tool_preview" else "succeeded"
    result = {
        "outcome": outcome,
        "ok": bool(details.get("ok", not failed and outcome == "succeeded")),
        "status": status,
        "mode": mode,
        "summary": summary,
    }
    result_hash = _stable_json_hash(result)
    record: dict[str, Any] = {
        "schema": "tool_action_receipt_v1",
        "receipt_id": receipt_id,
        "action_id": action_id,
        "action_type": normalized_event,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "target": _safe_tool_target(parameters, details),
        "tool_name": tool_name,
        "parameters": parameters,
        "parameters_hash": parameters_hash,
        "result": result,
        "result_hash": result_hash,
        "failure": (
            {
                "failed": True,
                "reason": runtime_preview(redact_secrets(str(details.get("failure_reason") or status)), limit=220),
            }
            if failed
            else {"failed": False}
        ),
        "identifiers": identifiers,
        "origin": {"chat_id": chat_id, "project_id": project_id},
        "approval": _tool_approval_metadata(details, event_type=normalized_event, status=status, mode=mode),
    }
    record["record_hash"] = _stable_json_hash({key: value for key, value in record.items() if key != "occurred_at"})
    return record


def emit_runtime_event(
    agent: Any,
    source_context: dict[str, Any] | None,
    *,
    event_type: str,
    message: str,
    emit_runtime_event_fn: Any,
    **details: Any,
) -> dict[str, Any] | None:
    payload = dict(details)
    checkpoint_id = agent._runtime_checkpoint_id(source_context)
    if checkpoint_id and "checkpoint_id" not in payload:
        payload["checkpoint_id"] = checkpoint_id
    turn_id = str((source_context or {}).get("turn_id") or "").strip()
    if turn_id and "turn_id" not in payload:
        payload["turn_id"] = turn_id
    for identity_key in ("model_call_id", "response_id"):
        identity_value = str((source_context or {}).get(identity_key) or "").strip()
        if identity_value and identity_key not in payload:
            payload[identity_key] = identity_value
    normalized_event_type = str(event_type or "status").strip() or "status"
    action_record: dict[str, Any] | None = None
    if normalized_event_type.startswith("tool_"):
        safe_payload = redact_tool_arguments(payload)
        payload = safe_payload if isinstance(safe_payload, dict) else {}
        message = redact_secrets(str(message or ""))
    if normalized_event_type in _TERMINAL_TOOL_EVENT_TYPES:
        action_record = build_tool_action_record(
            source_context,
            event_type=normalized_event_type,
            message=message,
            details=payload,
        )
        if action_record:
            payload["action_record"] = action_record
            payload["receipt_id"] = action_record["receipt_id"]
            payload["action_id"] = action_record["action_id"]
            payload["record_hash"] = action_record["record_hash"]
            ledger_session_id = str(
                (source_context or {}).get("runtime_session_id")
                or (source_context or {}).get("session_id")
                or (source_context or {}).get("chat_id")
                or ""
            ).strip()
            if ledger_session_id:
                try:
                    from core.runtime_continuity import store_tool_receipt

                    store_tool_receipt(
                        receipt_key=action_record["receipt_id"],
                        session_id=ledger_session_id,
                        checkpoint_id=str(
                            payload.get("checkpoint_id") or ""
                        ),
                        tool_name=str(payload.get("tool_name") or ""),
                        idempotency_key=action_record["action_id"],
                        arguments=dict(
                            action_record.get("parameters") or {}
                        ),
                        execution={
                            "action_record": action_record,
                        },
                    )
                    payload["receipt_persistence"] = "stored"
                except Exception:
                    # The action may already have happened; retain the truthful
                    # event and make the missing durable receipt observable.
                    payload["receipt_persistence"] = "failed"
    emit_runtime_event_fn(
        source_context,
        event_type=normalized_event_type,
        message=message,
        details=payload,
    )
    return action_record


def live_runtime_stream_enabled(source_context: dict[str, Any] | None) -> bool:
    return bool(str((source_context or {}).get("runtime_event_stream_id") or "").strip())


def tool_history_observation_prompt(observation: dict[str, Any]) -> str:
    return (
        "Grounding observations for this turn. Use them as evidence, not as a template:\n"
        f"{json.dumps(dict(observation or {}), indent=2, sort_keys=True, default=str)}"
    )
