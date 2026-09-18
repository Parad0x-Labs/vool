from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any


def prepare_runtime_checkpoint(
    agent: Any,
    *,
    session_id: str,
    raw_user_input: str,
    effective_input: str,
    source_context: dict[str, object] | None,
    allow_followup_resume: bool = True,
    latest_resumable_checkpoint_fn: Callable[[str], dict[str, Any] | None],
    resume_runtime_checkpoint_fn: Callable[..., dict[str, Any] | None],
    create_runtime_checkpoint_fn: Callable[..., dict[str, Any]],
    latest_failed_checkpoint_fn: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    from core.agent_runtime.proceed_intent_support import proceed_carries_its_own_request
    from core.agent_runtime.request_authority import (
        REQUEST_PROVENANCE_KEY,
        checkpoint_request_has_authority,
        compose_evidence_origins,
        request_provenance_for_visible_user_text,
        text_carries_no_request,
    )

    base_source_context = dict(source_context or {})
    # This field is server-owned. A transport/client-supplied lookalike never survives to storage.
    base_source_context.pop(REQUEST_PROVENANCE_KEY, None)
    # Never inherit (or accept from a caller) the resumed marker -- only the resume
    # branch below may set it, so a fresh turn can never adopt stored loop state.
    base_source_context.pop("runtime_checkpoint_resumed", None)
    base_source_context.setdefault("runtime_session_id", session_id)
    base_source_context.setdefault("session_id", session_id)
    # Every user message gets a fresh immutable turn id -- resumed or not. It rides
    # source_context into every runtime event and progress record, so a result can
    # always be traced to the turn that produced it.
    base_source_context["turn_id"] = f"turn-{uuid.uuid4().hex}"
    resumable = latest_resumable_checkpoint_fn(session_id)
    # A checkpoint that is still 'running' belongs to another in-flight turn; adopting
    # it would attach this message to concurrent work. Only interrupted/pending
    # checkpoints are resume candidates.
    if resumable and str(resumable.get("status") or "").strip() == "running":
        resumable = None
    untrusted_resumable = None
    if resumable and not checkpoint_request_has_authority(
        resumable,
        expected_session_id=session_id,
    ):
        untrusted_resumable = resumable
        resumable = None
    explicit_resume = agent._looks_like_explicit_resume_request(raw_user_input)
    # A go-ahead resumes the pending turn only when it asks for nothing of its own. Measured at cbe05fa3 through
    # VoolAgent.run_once: after 'delete my Apple note "Groceries"' asked for confirmation, 'go ahead and delete my Apple
    # note "Groceries"' resumed the question, the stored request replaced the message, and the delete was asked again.
    # A message that carries its own request is that request, read in its own words like any fresh turn. Read only when a
    # checkpoint could be resumed: without one the answer changes nothing.
    wants_resume = explicit_resume or bool(
        (resumable or untrusted_resumable)
        and allow_followup_resume
        and agent._is_proceed_message(raw_user_input)
        and not proceed_carries_its_own_request(raw_user_input)
    )
    if untrusted_resumable and wants_resume:
        stored_source_context = dict(untrusted_resumable.get("source_context") or {})
        fresh_evidence = base_source_context.get("external_evidence")
        stored_evidence = stored_source_context.get("external_evidence")
        fresh_metadata = dict(base_source_context)
        fresh_metadata.pop("external_evidence", None)
        stored_source_context.update(fresh_metadata)
        if stored_evidence is not None or fresh_evidence is not None:
            stored_source_context["external_evidence"] = compose_evidence_origins(
                stored_evidence,
                fresh_evidence,
            )
        stored_source_context["runtime_session_id"] = session_id
        stored_source_context["session_id"] = session_id
        stored_source_context["runtime_checkpoint_id"] = str(
            untrusted_resumable.get("checkpoint_id") or ""
        )
        return {
            "state": "rejected_resume",
            "checkpoint": untrusted_resumable,
            "effective_input": "",
            "source_context": stored_source_context,
        }
    same_request_retry = bool(
        resumable
        and agent._resume_request_key(effective_input)
        == agent._resume_request_key(str(resumable.get("request_text") or ""))
    )
    if resumable and (wants_resume or same_request_retry):
        resumed = resume_runtime_checkpoint_fn(
            str(resumable.get("checkpoint_id") or ""),
            source_context=base_source_context,
        )
        if resumed is not None:
            merged_source_context = dict(resumed.get("source_context") or {})
            # ``resume_runtime_checkpoint`` has already composed stored evidence first and this
            # turn's fresh evidence second under one raw-item budget. Re-applying the whole fresh
            # dictionary here would replace that composition (or append the fresh origin twice).
            fresh_metadata = dict(base_source_context)
            fresh_metadata.pop("external_evidence", None)
            merged_source_context.update(fresh_metadata)
            merged_source_context["runtime_session_id"] = session_id
            merged_source_context["session_id"] = session_id
            merged_source_context["runtime_checkpoint_id"] = str(resumed.get("checkpoint_id") or "")
            # Explicit marker: stored loop state may be adopted by this turn ONLY when
            # this flag is present (the tool loop refuses stale state without it).
            merged_source_context["runtime_checkpoint_resumed"] = True
            return {
                "state": "resumed",
                "checkpoint": resumed,
                "effective_input": str(resumed.get("request_text") or effective_input),
                "source_context": merged_source_context,
            }
    if explicit_resume and not resumable:
        # No mid-task checkpoint to resume. "try again" should retry the immediately preceding
        # unfulfilled task using its immutable request envelope -- never fall back to unrelated setup
        # instructions. Re-dispatch that request as a fresh turn when a failed task exists; else
        # report there is nothing to retry.
        failed = latest_failed_checkpoint_fn(session_id) if latest_failed_checkpoint_fn else None
        if failed and not checkpoint_request_has_authority(
            failed,
            expected_session_id=session_id,
        ):
            failed = None
        retry_request = str((failed or {}).get("request_text") or "").strip()
        if retry_request:
            retry_source_context = dict(base_source_context)
            failed_outcome = dict((failed or {}).get("outcome") or {})
            failed_checkpoint_id = str((failed or {}).get("checkpoint_id") or "")
            retry_source_context["runtime_retry_of"] = failed_checkpoint_id
            retry_source_context["runtime_retry_origin_checkpoint_id"] = str(
                failed_outcome.get("origin_checkpoint_id") or failed_checkpoint_id
            )
            retry_source_context["runtime_retry_origin_task_id"] = str(
                failed_outcome.get("origin_task_id") or (failed or {}).get("task_id") or ""
            )
            failed_source_context = (failed or {}).get("source_context")
            if isinstance(failed_source_context, dict):
                retry_source_context[REQUEST_PROVENANCE_KEY] = dict(
                    failed_source_context[REQUEST_PROVENANCE_KEY]
                )
            checkpoint = create_runtime_checkpoint_fn(
                session_id=session_id,
                request_text=retry_request,
                source_context=retry_source_context,
            )
            base_source_context.update(retry_source_context)
            base_source_context["runtime_session_id"] = session_id
            base_source_context["session_id"] = session_id
            base_source_context["runtime_checkpoint_id"] = str(checkpoint.get("checkpoint_id") or "")
            base_source_context["runtime_retry_of"] = failed_checkpoint_id
            return {
                "state": "retried",
                "checkpoint": checkpoint,
                "effective_input": retry_request,
                "source_context": base_source_context,
                "retry_failure_text": str((failed or {}).get("failure_text") or ""),
                "retry_outcome": failed_outcome,
            }
        return {
            "state": "missing_resume",
            "checkpoint": None,
            "effective_input": effective_input,
            "source_context": base_source_context,
        }
    checkpoint_source_context = dict(base_source_context)
    if not text_carries_no_request(raw_user_input):
        checkpoint_source_context[REQUEST_PROVENANCE_KEY] = (
            request_provenance_for_visible_user_text(
                effective_input,
                session_id=session_id,
            )
        )
    checkpoint = create_runtime_checkpoint_fn(
        session_id=session_id,
        request_text=effective_input,
        source_context=checkpoint_source_context,
    )
    base_source_context.update(checkpoint_source_context)
    base_source_context["runtime_session_id"] = session_id
    base_source_context["session_id"] = session_id
    base_source_context["runtime_checkpoint_id"] = str(checkpoint.get("checkpoint_id") or "")
    return {
        "state": "created",
        "checkpoint": checkpoint,
        "effective_input": effective_input,
        "source_context": base_source_context,
    }


def resolve_runtime_task(
    agent: Any,
    *,
    effective_input: str,
    session_id: str,
    source_context: dict[str, object] | None,
    get_runtime_checkpoint_fn: Callable[[str], dict[str, Any] | None],
    load_task_record_fn: Callable[[str], Any],
    create_task_record_fn: Callable[..., Any],
) -> Any:
    checkpoint_id = agent._runtime_checkpoint_id(source_context)
    if checkpoint_id:
        checkpoint = get_runtime_checkpoint_fn(checkpoint_id)
        if checkpoint:
            existing_task = load_task_record_fn(str(checkpoint.get("task_id") or ""))
            if existing_task is not None:
                return existing_task
    return create_task_record_fn(effective_input, session_id=session_id)


def update_runtime_checkpoint_context(
    source_context: dict[str, object] | None,
    *,
    task_id: str | None = None,
    task_class: str | None = None,
    update_runtime_checkpoint_fn: Callable[..., Any],
) -> None:
    checkpoint_id = runtime_checkpoint_id(source_context)
    if not checkpoint_id:
        return
    update_runtime_checkpoint_fn(
        checkpoint_id,
        task_id=task_id,
        task_class=task_class,
        source_context=dict(source_context or {}),
    )


def finalize_runtime_checkpoint(
    source_context: dict[str, object] | None,
    *,
    status: str,
    final_response: str = "",
    failure_text: str = "",
    outcome: dict[str, object] | None = None,
    finalize_runtime_checkpoint_fn: Callable[..., Any],
) -> None:
    checkpoint_id = runtime_checkpoint_id(source_context)
    if not checkpoint_id:
        return
    from core.runtime_task_outcome import fulfillment_outcome_from_source_context

    resolved_outcome = outcome or fulfillment_outcome_from_source_context(source_context)
    kwargs = {
        "status": status,
        "final_response": final_response,
        "failure_text": failure_text,
    }
    # Preserve compatibility with app-level writers and test doubles that implement the original
    # signature. Only validation-fallback turns need the new explicit outcome argument; ordinary
    # completed/failed rows are normalized by the durable writer itself.
    if resolved_outcome is not None:
        kwargs["outcome"] = resolved_outcome
    finalize_runtime_checkpoint_fn(checkpoint_id, **kwargs)


def runtime_checkpoint_id(source_context: dict[str, object] | None) -> str:
    return str((source_context or {}).get("runtime_checkpoint_id") or "").strip()


def merge_runtime_source_contexts(
    agent: Any,
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
) -> dict[str, Any]:
    from core.agent_runtime.request_authority import compose_evidence_origins

    merged = dict(primary or {})
    secondary_dict = dict(secondary or {})
    primary_evidence = merged.get("external_evidence")
    secondary_evidence = secondary_dict.get("external_evidence")
    primary_history = [item for item in list(merged.get("conversation_history") or []) if isinstance(item, dict)]
    secondary_history = [item for item in list(secondary_dict.get("conversation_history") or []) if isinstance(item, dict)]
    merged.update(secondary_dict)
    if "external_evidence" in merged or "external_evidence" in secondary_dict:
        merged["external_evidence"] = compose_evidence_origins(
            primary_evidence,
            secondary_evidence,
        )
    history: list[dict[str, Any]] = []
    for item in (primary_history + secondary_history)[-16:]:
        normalized = agent._normalize_tool_history_message(item)
        role = str(normalized.get("role") or "").strip().lower()
        content = str(normalized.get("content") or "").strip()
        if role not in {"system", "user", "assistant"} or not content:
            continue
        history.append({"role": role, "content": content[:4000]})
    merged["conversation_history"] = history[-12:]
    return merged
