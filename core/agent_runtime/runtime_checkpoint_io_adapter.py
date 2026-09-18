from __future__ import annotations

from importlib import import_module
from typing import Any

from core.agent_runtime import checkpoints as agent_checkpoint_runtime


def _agent_module() -> Any:
    return import_module("core.agent_runtime.agent")


def prepare_runtime_checkpoint(
    agent: Any,
    *,
    session_id: str,
    raw_user_input: str,
    effective_input: str,
    source_context: dict[str, object] | None,
    allow_followup_resume: bool = True,
) -> dict[str, Any]:
    return agent_checkpoint_runtime.prepare_runtime_checkpoint(
        agent,
        session_id=session_id,
        raw_user_input=raw_user_input,
        effective_input=effective_input,
        source_context=source_context,
        allow_followup_resume=allow_followup_resume,
        latest_resumable_checkpoint_fn=_agent_module().latest_resumable_checkpoint,
        resume_runtime_checkpoint_fn=_agent_module().resume_runtime_checkpoint,
        create_runtime_checkpoint_fn=_agent_module().create_runtime_checkpoint,
        latest_failed_checkpoint_fn=_agent_module().latest_failed_checkpoint,
    )


def resolve_runtime_task(
    agent: Any,
    *,
    effective_input: str,
    session_id: str,
    source_context: dict[str, object] | None,
) -> Any:
    return agent_checkpoint_runtime.resolve_runtime_task(
        agent,
        effective_input=effective_input,
        session_id=session_id,
        source_context=source_context,
        get_runtime_checkpoint_fn=_agent_module().get_runtime_checkpoint,
        load_task_record_fn=_agent_module().load_task_record,
        create_task_record_fn=_agent_module().create_task_record,
    )


def update_runtime_checkpoint_context(
    source_context: dict[str, object] | None,
    *,
    task_id: str | None = None,
    task_class: str | None = None,
) -> None:
    agent_checkpoint_runtime.update_runtime_checkpoint_context(
        source_context,
        task_id=task_id,
        task_class=task_class,
        update_runtime_checkpoint_fn=_agent_module().update_runtime_checkpoint,
    )


def finalize_runtime_checkpoint(
    source_context: dict[str, object] | None,
    *,
    status: str,
    final_response: str = "",
    failure_text: str = "",
    outcome: dict[str, object] | None = None,
) -> None:
    agent_checkpoint_runtime.finalize_runtime_checkpoint(
        source_context,
        status=status,
        final_response=final_response,
        failure_text=failure_text,
        outcome=outcome,
        finalize_runtime_checkpoint_fn=_agent_module().finalize_runtime_checkpoint,
    )


def runtime_checkpoint_id(source_context: dict[str, object] | None) -> str:
    return agent_checkpoint_runtime.runtime_checkpoint_id(source_context)


def merge_runtime_source_contexts(
    agent: Any,
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
) -> dict[str, Any]:
    return agent_checkpoint_runtime.merge_runtime_source_contexts(agent, primary, secondary)


def agent_module_attr(name: str, *args: Any, **kwargs: Any) -> Any:
    return getattr(_agent_module(), name)(*args, **kwargs)
