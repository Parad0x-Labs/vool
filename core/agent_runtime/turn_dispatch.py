from __future__ import annotations

import re
from typing import Any

from core.operator.models import READ_ONLY_OPERATOR_KINDS, operator_step_executed

# Operator actions that only READ the machine (never mutate) -- allowed in read-only Ask/Plan mode.
# Every other operator kind (cleanup_temp_files, move_path, schedule_calendar_event, ...) mutates and
# is refused in Ask/Plan before dispatch. The set itself lives with the operator models so the memory
# lane reads the same classification (`core.persistent_memory`).
_READ_ONLY_OPERATOR_KINDS = READ_ONLY_OPERATOR_KINDS


#: The provider/note READ kinds whose finished report is an ANSWER, not a staged approval: read
#: from the operator's DECLARED effect semantics (`side_effect_class_for_intent`, the same
#: declaration `core.execution.operator_tools` already reads), never a second hand-kept list.
#: MEASURED 2026-09-15, served (`VoolAgent.run_once`, synthetic Notes runner): a Notes read that
#: executed ('open my Apple note "Groceries"', status `reported`) was filed `pending_approval`
#: because the hand list predated the Apple Notes read kinds, so both halves of a two-Notes mixed
#: turn answered "I could not answer any part" over the two bodies the bridge had already read.


def _reported_read_only(operator_kind: str) -> bool:
    """A `reported` result for a kind DECLARED read-only is a finished read, not a staged
    approval: the availability answer, the calendar list, the note body -- each is evidence
    with nothing to approve, and calling it a preview would manufacture a pending approval
    out of a completed answer. The declaration is the operator's own contract
    (`side_effect_class_for_intent`); a kind no registry declares stays a preview exactly as
    before, so an unregistered mutating kind can never sneak through as a finished answer."""
    from core.tool_argument_aliases import side_effect_class_for_intent

    return side_effect_class_for_intent(f"operator.{operator_kind}") == "read_only"


def _reported_read_stages_an_action(dispatch: Any) -> bool:
    """A `reported` read that STAGED a pending action is a preview in substance: its actionable
    outcome is the approval it now holds (the disk scan's "Safe temp cleanup preview" with a
    pending action id), not the finished read. Mapping it to tool_executed would tell the truth
    of the scan and hide the cleanup approval the turn just minted (reproduced by the lane's
    storage-gate path-words suite: 'find disk bloat in "<dir>/review/notes"' reported
    tool_executed while holding a staged cleanup)."""
    try:
        details = getattr(dispatch, "details", None) or {}
        return bool(str(details.get("pending_action_id") or "").strip())
    except Exception:
        return False


def _operator_intent_in_user_words(
    operator_intent: Any,
    *,
    effective_input: str,
    interpreted: Any,
    parse_operator_action_intent_fn: Any,
) -> Any:
    """The same operator request, carried in the words the user typed.

    Routing reads the normalized text, and should: the normalizer repairs typos in the words that
    choose a lane. An operator EFFECT persists text, though -- a note body, an event title, an action
    line -- and the normalizer rewrites content as well: `u` becomes `you`, a word near the routing
    vocabulary becomes that word (`star` -> `start`), and `with: [action]` is re-spaced to
    `with:[action ]`. Measured on the served path (revision-5 served journeys, first run): a note saved
    with `[action] supplier review Tuesday at 16:00` was written as `[action ] supplier review ...`, and
    the next turn could not find its action line. The memory lane reads the typed text for the same
    reason (see `turn_frontdoor`).

    The typed words replace the routed parse only when they are this text's source -- the
    interpretation's normalized text IS the routed text, so intake narrowing has already applied to
    them -- and they parse as the SAME action. A routed text rewritten after interpretation (a redacted
    remainder, a request substituted by a later intake step) is never swapped for words it did not come
    from, and typed words that parse as another action leave routing's reading in place.
    """
    typed = str(getattr(interpreted, "raw_text", "") or "").strip()
    routed = str(effective_input or "").strip()
    if not typed or typed == routed:
        return operator_intent
    if str(getattr(interpreted, "normalized_text", "") or "").strip() != routed:
        return operator_intent
    in_user_words = parse_operator_action_intent_fn(typed)
    if in_user_words is None or getattr(in_user_words, "kind", None) != getattr(operator_intent, "kind", None):
        return operator_intent
    return in_user_words


_EXPLICIT_NAMED_TOOL_RE = re.compile(
    r"\b(?:local\s+)?tool\s+(?:named|called)\s+[`\"']?"
    r"(?P<name>[A-Za-z][A-Za-z0-9_.-]{1,127})",
    re.IGNORECASE,
)
_UNSUPPORTED_DIRECT_FILE_DELETE_RE = re.compile(
    r"\b(?:delete|remove|wipe)\s+(?:the\s+)?(?P<target>(?:[A-Za-z]:\\[^\s\"']+|[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,16}))\b",
    re.IGNORECASE,
)


def _unsupported_direct_file_delete(user_text: str) -> str:
    match = _UNSUPPORTED_DIRECT_FILE_DELETE_RE.search(str(user_text or ""))
    if not match:
        return ""
    target = match.group("target").strip().rstrip(".,!?;:")
    lowered = str(user_text or "").lower()
    if "local tool named" in lowered or "tool called" in lowered:
        return ""
    if "temp files" in lowered or "clean all" in lowered or "cleanup" in lowered:
        return ""
    return target


def _explicit_missing_local_tool_name(user_text: str) -> str:
    match = _EXPLICIT_NAMED_TOOL_RE.search(str(user_text or ""))
    if not match:
        return ""
    requested = match.group("name").strip().rstrip(".,!?;:").lower()
    if not requested:
        return ""

    from core.operator.registry import list_operator_tools
    from core.tool_intent_executor import runtime_tool_specs

    known = {
        str(tool.get("tool_id") or "").strip().lower()
        for tool in list_operator_tools()
        if str(tool.get("tool_id") or "").strip()
    }
    known.update(f"operator.{name}" for name in tuple(known))
    known.update(
        str(spec.get("intent") or "").strip().lower()
        for spec in runtime_tool_specs()
        if str(spec.get("intent") or "").strip()
    )
    return "" if requested in known else requested


def prepare_turn_task_bundle(
    agent: Any,
    *,
    effective_input: str,
    user_input: str,
    session_id: str,
    source_context: dict[str, object] | None,
    interpreted: Any,
    classify_fn: Any,
    parse_channel_post_intent_fn: Any,
    dispatch_outbound_post_intent_fn: Any,
    parse_operator_action_intent_fn: Any,
    dispatch_operator_action_fn: Any,
) -> dict[str, Any]:
    task = agent._resolve_runtime_task(
        effective_input=effective_input,
        session_id=session_id,
        source_context=source_context,
    )
    agent._update_runtime_checkpoint_context(
        source_context,
        task_id=task.task_id,
    )
    classification_context = interpreted.as_context()
    if source_context:
        classification_context["source_context"] = dict(source_context)
        classification_context["source_surface"] = source_context.get("surface")
        classification_context["source_platform"] = source_context.get("platform")
    classification = classify_fn(effective_input, context=classification_context)
    agent._update_task_class(task.task_id, classification["task_class"])
    agent._update_runtime_checkpoint_context(
        source_context,
        task_id=task.task_id,
        task_class=str(classification.get("task_class") or "unknown"),
    )
    agent._emit_runtime_event(
        source_context,
        event_type="task_classified",
        message=f"Task classified as {classification.get('task_class') or 'unknown'!s}.",
        task_id=task.task_id,
        task_class=str(classification.get("task_class") or "unknown"),
    )

    post_intent, post_error = parse_channel_post_intent_fn(effective_input)
    if post_intent is not None:
        dispatch = dispatch_outbound_post_intent_fn(
            post_intent,
            task_id=task.task_id,
            session_id=session_id,
            source_context=source_context,
        )
        return {
            "result": agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=effective_input,
                response=dispatch.response_text,
                confidence=0.95 if dispatch.ok else 0.42,
                source_context=source_context,
                reason=f"channel_post_{dispatch.status}",
                success=dispatch.ok,
                details={
                    "platform": dispatch.platform,
                    "target": dispatch.target,
                    "record_id": dispatch.record_id,
                    "error": dispatch.error,
                },
            )
        }
    if post_error:
        return {
            "result": agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=effective_input,
                response=(
                    "I can do that, but I need the exact message text. "
                    "Use a format like: post to Discord: \"We are live tonight.\""
                ),
                confidence=0.40,
                source_context=source_context,
                reason="channel_post_missing_message",
                success=False,
                details={"error": post_error},
            )
        }

    # `effective_input` only, never raw `user_input`: intake narrowing has already removed any
    # clause the turn retracted, and reading the raw text here let a WITHDRAWN clause drive this
    # gate -- measured: "Delete temp.txt right away. WAIT, STOP. Cancel that, just tell me what a
    # tmp file is for." answered about the delete the user had taken back.
    missing_tool = _explicit_missing_local_tool_name(effective_input)
    if missing_tool:
        result = agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=(
                f"No local tool named `{missing_tool}` is available. "
                "Nothing was executed, and no files were deleted or modified."
            ),
            confidence=1.0,
            source_context=source_context,
            reason="tool_honesty_missing_tool",
            success=False,
            details={
                "requested_tool": missing_tool,
                "available": False,
                "executed": False,
                "files_deleted": False,
                "files_modified": False,
            },
            mode_override="tool_failed",
            task_outcome="failed",
        )
        result["route"] = "tool_honesty_missing_tool"
        return {
            "result": result
        }

    # Narrowed text only, for the same reason as `missing_tool` above.
    direct_file_delete = _unsupported_direct_file_delete(effective_input)
    if direct_file_delete:
        result = agent._action_fast_path_result(
            task_id=task.task_id,
            session_id=session_id,
            user_input=effective_input,
            response=(
                f"I did not delete `{direct_file_delete}`. Nothing was executed, no tool was run, "
                "and no files were deleted or modified."
            ),
            confidence=1.0,
            source_context=source_context,
            reason="action_honesty_no_execution",
            success=False,
            details={
                "target": direct_file_delete,
                "executed": False,
                "files_deleted": False,
                "files_modified": False,
            },
            mode_override="tool_failed",
            task_outcome="failed",
        )
        result["route"] = "action_honesty_no_execution"
        return {"result": result}

    from core.agent_runtime.intent_claims import ActionPolicy, action_policy_from_context

    # Narrowed text only: a withdrawn clause must not be parsed into an operator action either.
    operator_intent = parse_operator_action_intent_fn(effective_input)
    if operator_intent is not None:
        operator_intent = _operator_intent_in_user_words(
            operator_intent,
            effective_input=effective_input,
            interpreted=interpreted,
            parse_operator_action_intent_fn=parse_operator_action_intent_fn,
        )
        from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn

        # The planner owns independent siblings. A parser match anywhere in the
        # message cannot grant this action the right to consume the whole turn.
        if not lane_may_claim_whole_turn(effective_input, "operator_action_dispatch"):
            operator_intent = None
    if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
        # A conversational/no-action constraint is decided before dispatch.  Let the reasoning
        # layer answer independent explanatory siblings; constructing and then rejecting a tool
        # call records a false attempted effect and commonly drops the sibling that was answerable.
        operator_intent = None
    if operator_intent is not None:
        # Mode enforcement: Ask/Plan is a READ-ONLY posture. A mutating operator action (anything
        # that would change the system) is refused BEFORE dispatch -- nothing is written and no
        # approval is even offered -- with an honest pointer to Build/Auto. Read-only inspects still
        # run. Modes other than ask/plan (incl. no mode set, e.g. channel/api/tests) are unaffected,
        # so the existing approval flow stays authoritative there.
        _operating_mode = str((source_context or {}).get("operating_mode") or "").strip().lower()
        if _operating_mode in {"ask", "plan"} and operator_intent.kind not in _READ_ONLY_OPERATOR_KINDS:
            _blocked_tool = f"operator.{operator_intent.kind}"
            agent._emit_runtime_event(
                source_context, event_type="tool_failed",
                message=f"Blocked in {_operating_mode} mode (read-only)", tool_name=_blocked_tool,
                summary=f"Blocked in {_operating_mode} mode (read-only)",
            )
            result = agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=effective_input,
                response=(
                    f"You're in {_operating_mode.capitalize()} mode, which is read-only, so I did not run that "
                    "change and nothing was modified. Switch the mode selector to Build or Auto to let me make changes."
                ),
                confidence=1.0,
                source_context=source_context,
                reason=f"mode_read_only_blocked_{operator_intent.kind}",
                success=False,
                details={
                    "operating_mode": _operating_mode,
                    "blocked_action": operator_intent.kind,
                    "executed": False,
                    "files_modified": False,
                    "files_deleted": False,
                },
                mode_override="tool_failed",
                task_outcome="blocked",
            )
            result["route"] = "mode_read_only"
            return {"result": result}
        dispatch = dispatch_operator_action_fn(
            operator_intent,
            task_id=task.task_id,
            session_id=session_id,
        )
        # Surface the operator action as a REAL tool step so the Activity panel is authoritative:
        # the answer shows a scan/action, so Activity must show it, not "No actions yet". Mirrors the
        # machine-read fast path (U15) -- emitted before _action_fast_path_result's task_completed so
        # the UI builds a running->completed step and derives a Tool-confirmed label.
        _op_tool = f"operator.{operator_intent.kind}"
        _op_summary = next((ln.strip() for ln in str(dispatch.response_text or "").splitlines() if ln.strip()), _op_tool)
        _op_summary = (_op_summary[:157] + "...") if len(_op_summary) > 160 else _op_summary
        agent._emit_runtime_event(
            source_context, event_type="tool_selected",
            message=f"Running {_op_tool}", tool_name=_op_tool, summary=f"Running {_op_tool}",
        )
        # The step's execution record is what the lane EXECUTED, which a failed request can still
        # include (see `operator_step_executed`); the request's own outcome travels in success/mode below.
        agent._emit_runtime_event(
            source_context, event_type="tool_executed" if operator_step_executed(dispatch) else "tool_failed",
            message=_op_summary, tool_name=_op_tool, summary=_op_summary,
        )
        workflow_summary = agent._action_workflow_summary(
            operator_kind=operator_intent.kind,
            dispatch_status=dispatch.status,
            details=dispatch.details,
        )
        return {
            "result": agent._action_fast_path_result(
                task_id=task.task_id,
                session_id=session_id,
                user_input=effective_input,
                response=dispatch.response_text,
                confidence=dispatch.learned_plan.confidence if dispatch.learned_plan else (0.9 if dispatch.ok else 0.45),
                source_context=source_context,
                reason=f"operator_action_{dispatch.status}",
                success=dispatch.ok,
                details=dispatch.details,
                mode_override=(
                    "tool_executed"
                    if dispatch.status == "executed"
                    or (
                        dispatch.status == "reported"
                        and _reported_read_only(operator_intent.kind)
                        and not _reported_read_stages_an_action(dispatch)
                    )
                    else "tool_preview"
                    if dispatch.status in {"reported", "approval_required"}
                    else "tool_failed"
                ),
                task_outcome=(
                    "success"
                    if dispatch.status == "executed"
                    or (dispatch.status == "reported" and _reported_read_only(operator_intent.kind))
                    else "pending_approval"
                    if dispatch.status in {"reported", "approval_required"}
                    else "failed"
                ),
                learned_plan=dispatch.learned_plan,
                workflow_summary=workflow_summary,
            )
        }

    hive_confirm = agent._maybe_handle_hive_create_confirmation(
        effective_input,
        task=task,
        session_id=session_id,
        source_context=source_context,
    )
    if hive_confirm is not None:
        return {"result": hive_confirm}

    # Hive topic create/mutation are PRODUCT ACTIONS and are intercepted only when the request
    # names the Hive product as the operation's target (or continues a create this session
    # actually holds) -- that ownership rule lives in the matchers themselves
    # (hive_topic_draft_intents / hive_topic_mutation_detection). The `_skip_hive_for_build`
    # vocabulary blacklist that used to paper over the matchers is retired: a build request
    # whose spec says "Add new tasks" or "Delete tasks" is exactly the request that must never
    # have been routed here, and no build-vocabulary list can decide that -- the product must be
    # named, or nothing is a product operation.
    raw_hive_create_draft = agent._extract_hive_topic_create_draft(user_input)
    hive_topic_mutation = agent._maybe_handle_hive_topic_mutation_request(
        user_input if raw_hive_create_draft is not None else effective_input,
        task=task,
        session_id=session_id,
        source_context=source_context,
    )
    if hive_topic_mutation is not None:
        return {"result": hive_topic_mutation}

    hive_topic_create = agent._maybe_handle_hive_topic_create_request(
        user_input if raw_hive_create_draft is not None else effective_input,
        task=task,
        session_id=session_id,
        source_context=source_context,
    )
    if hive_topic_create is not None:
        return {"result": hive_topic_create}

    builder_fast_path = None
    if action_policy_from_context(source_context) is not ActionPolicy.FORBIDDEN:
        builder_fast_path = agent._maybe_run_builder_controller(
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpreted,
            web_notes=[],
            session_id=session_id,
            source_context=source_context,
        )
    if builder_fast_path is not None:
        return {"result": builder_fast_path}

    return {
        "task": task,
        "classification": classification,
    }
