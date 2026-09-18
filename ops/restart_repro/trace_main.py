"""Gated diagnostic boot wrapper for ONE cold served request (Milestone 2).

Launched exactly like the product server (same argv), but first installs
narrow probes around the decision seams the restart-first-turn P0 named:

  * canonical external request entry  (core.web.api.runtime.run_agent)
  * restored-history rehydration       (persistent_memory.augment_history_from_session_log)
  * the current-information authority (requirements_for / escalate_current_requirement)
  * the adaptive-research transition   (curiosity_roamer._adaptive_research_decision)
  * every explicit_heavy producer      (_explicit_heavy_requested + plan builder entry)
  * the interpreted text seam          (memory_first_router._interpreted_user_text)

Every probe prints one `[RTRACE]` line with owning function/file taken from
the live stack -- recorded values, not guesses. Active ONLY when the harness
sets VOOL_RESTART_TRACE=1; without it this module is a pass-through to the
product's own main().
"""

from __future__ import annotations

import os
import sys


def _origin(skip: int) -> str:
    import inspect

    frame = inspect.stack()[skip + 1]
    return f"{frame.function}@{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}"


def _short(value: object, limit: int = 130) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _apply() -> None:
    def p(where: str, message: str) -> None:
        print(f"[RTRACE] {where} | {message}", file=sys.stderr, flush=True)

    # 1 + 2. Canonical external entry + restored state before routing.
    import core.web.api.runtime as api_runtime

    _orig_run_agent = api_runtime.run_agent

    def traced_run_agent(runtime, user_text, **kwargs):
        session = kwargs.get("session_id") or ""
        p("run_agent", f"ENTER session={session} text={_short(user_text)!r}")
        return _orig_run_agent(runtime, user_text, **kwargs)

    api_runtime.run_agent = traced_run_agent
    # service.py imported the symbol directly at import time? It takes it as a
    # provider parameter default -- rebind the module attribute it reads from.

    import core.persistent_memory as pm

    _orig_augment = pm.augment_history_from_session_log

    def traced_augment(client_history, *, session_id, user_text):
        result = _orig_augment(client_history, session_id=session_id, user_text=user_text)
        try:
            restored = [m.get("content", "") for m in (result or [])]
        except Exception:
            restored = ["<unreadable>"]
        p(
            "augment_history",
            f"session={session_id} in_rows={len(client_history or [])} out_rows={len(restored)} "
            f"last={_short(restored[-1] if restored else '')!r}",
        )
        return result

    pm.augment_history_from_session_log = traced_augment

    # 3 + 5. Current-information authority: first decision, freezes, escalations.
    import core.execution_requirements as er

    _orig_requirements_for = er.requirements_for

    def traced_requirements_for(user_input, *, task_class="unknown", source_context=None):
        result = _orig_requirements_for(user_input, task_class=task_class, source_context=source_context)
        p(
            "requirements_for",
            f"{_origin(1)} text={_short(user_input)!r} class={task_class} "
            f"current={result.current_information_required} codes={list(result.reason_codes or ())}",
        )
        return result

    er.requirements_for = traced_requirements_for

    _orig_escalate = er.escalate_current_requirement

    def traced_escalate(source_context, *, text, source, reason_code, detail=""):
        before = None
        record = er.current_requirement_record(source_context)
        if record is not None:
            before = record.requirements.current_information_required
        widened = _orig_escalate(
            source_context, text=text, source=source, reason_code=reason_code, detail=detail
        )
        p(
            "escalate_current",
            f"{_origin(1)} source={source} code={reason_code} text={_short(text)!r} "
            f"before={before} after={widened}",
        )
        return widened

    er.escalate_current_requirement = traced_escalate

    # 4. The adaptive-research transition.
    import core.curiosity_roamer as roamer

    _orig_decision = roamer._adaptive_research_decision

    def traced_decision(*args, **kwargs):
        result = _orig_decision(*args, **kwargs)
        p(
            "adaptive_research_decision",
            f"{_origin(1)} args={[ _short(a, 60) for a in args[:3] ]} result={result!r}",
        )
        return result

    roamer._adaptive_research_decision = traced_decision

    # 6 + 7. Every explicit_heavy producer and the plan-builder entry.
    import core.local_inference_autopilot as autopilot

    _orig_heavy = autopilot._explicit_heavy_requested

    def traced_heavy(*, user_text, source_context):
        result = _orig_heavy(user_text=user_text, source_context=source_context)
        flag = bool((source_context or {}).get("autopilot_allow_heavy_model"))
        requested = str((source_context or {}).get("requested_model") or "")
        lowered = str(user_text or "").lower()
        size = autopilot._largest_parameter_size_b(requested, lowered)
        p(
            "explicit_heavy",
            f"{_origin(1)} flag={flag} requested={requested!r} size_b={size} "
            f"heavy_word={'heavy' in lowered} -> {result} text={_short(user_text)!r}",
        )
        return result

    autopilot._explicit_heavy_requested = traced_heavy

    _orig_build = autopilot.build_local_inference_autopilot_plan

    def traced_build(*args, **kwargs):
        plan = _orig_build(*args, **kwargs)
        p(
            "plan_built",
            f"{_origin(1)} task_kind={kwargs.get('task_kind')} lane={plan.lane} "
            f"explicit_heavy={plan.explicit_heavy} selected={plan.selected_provider_id} "
            f"warnings={list(plan.warnings)}",
        )
        return plan

    autopilot.build_local_inference_autopilot_plan = traced_build

    # memory_first_router imported the builder by name; rebind its reference too.
    import core.memory_first_router as mfr

    mfr.build_local_inference_autopilot_plan = autopilot.build_local_inference_autopilot_plan

    _orig_interpreted = mfr._interpreted_user_text

    def traced_interpreted(interpretation, task):
        result = _orig_interpreted(interpretation, task)
        p(
            "interpreted_text",
            f"normalized={_short(getattr(interpretation, 'normalized_text', ''))!r} "
            f"-> used={_short(result)!r}",
        )
        return result

    mfr._interpreted_user_text = traced_interpreted

    p("boot", "trace probes installed")


def main() -> int:
    if str(os.environ.get("VOOL_RESTART_TRACE") or "").strip().lower() not in {"1", "true", "yes"}:
        from apps.vool_api_server import main as product_main

        return product_main()
    sys.path.insert(0, os.getcwd())
    _apply()
    from apps.vool_api_server import main as product_main

    return product_main()


if __name__ == "__main__":
    raise SystemExit(main())
