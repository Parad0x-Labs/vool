"""One readable timeline of everything VOOL did on a turn, joined from the sources it already keeps.

Three records already exist per turn and are stored apart, so answering "why did it do that?" meant
opening three things and correlating by hand:

* the **runtime event ledger** (`runtime_session_events`) -- routing, classification, model lane, tool
  selection, execution, planner steps, cancellation;
* the **prompt capture** (`prompt_debug.jsonl`, `VOOL_DEBUG_PROMPT=1`) -- the payload each model call
  actually received, redacted;
* the **tool receipts** -- what a tool was given and what it returned.

This module joins them into one time-ordered trace. It only READS; it adds no new storage, no new
event type, and nothing to the hot path, so it cannot change what it is measuring.

    VOOL_DEBUG_PROMPT=1 <run vool>
    python -m core.turn_trace                     # the most recent session
    python -m core.turn_trace --session <id>
    python -m core.turn_trace --session <id> --full   # untruncated payloads

Nothing here is a substitute for driving the product: the trace explains a turn that happened, it
does not assert that the turn was correct.
"""
from __future__ import annotations

import json
from typing import Any

_PROMPT_LOG = "prompt_debug.jsonl"

# Ledger events worth a line of their own, mapped to how they read in a timeline. Anything not listed
# still prints -- an unknown event is exactly what you want to SEE, not hide.
_STAGE_LABEL = {
    "task_received": "REQUEST",
    "task_classified": "CLASSIFY",
    "scope_resolved": "SCOPE",
    "model_lane_proof": "MODEL",
    "mode_changed": "MODE",
    "permission_approved": "PERMISSION",
    "tool_selected": "TOOL →",
    "tool_executed": "TOOL ✓",
    "tool_failed": "TOOL ✗",
    "tool_repeat_blocked": "TOOL ⊘",
    "tool_fallback_to_research": "FALLBACK",
    "tool_synthesizing": "SYNTHESIS",
    "tool_loop_completed": "LOOP END",
    "audit_step": "AUDIT CALL",
    "audit_budget_refused": "AUDIT ⊘",
    "audit_claim_executed": "CLAIM RUN",
    "workflow_planner_step": "PLAN STEP",
    "workflow_planner_stop": "PLAN STOP",
    "stale_result_rejected": "STALE ⊘",
    "task_interrupted": "INTERRUPTED",
    "task_cancelled": "CANCELLED",
    "task_completed": "DONE",
    # Which gate got to decide this turn, and whether the model ever saw the message. The event's
    # own message carries the summary ("N gates consulted, preempted by X"); the full receipt is a
    # detail, so it shows under --full rather than inline -- a `resolution_receipt_v1` printed on
    # every line would bury the timeline it is meant to explain.
    "semantic_resolution_receipt": "RESOLUTION",
}

# Detail keys worth showing inline per event. Everything else stays in --full.
#
# ``error_kind`` earns its place next to ``reason``: an audit turn failed on three provider lanes and
# each `model.call_failed` carried reason=prompt_budget_exceeded WITH error_kind=prompt_shape. Only
# the reason printed inline, so the class of failure -- is this the prompt's shape or the provider's
# health? -- needed a second run with --full to read.
#
# The same argument extends to the rest of the cause fields. A turn's failure names itself in a
# different key per family -- `model_lane_failed` carries `fallback_reason` and no `reason` at all,
# `model_routing_failed` carries `rejection_reason`, a retrieval receipt carries `failure_class` /
# `failure_reason`, and the typed `error_class` (PROVIDER_TIMEOUT vs MALFORMED_TOOL_CALL) is the one
# token that separates a transport failure from a model-output defect. Printing only `reason` and
# `error` meant the trace showed a blank cause for exactly the families that were failing.
_INTERESTING = (
    "intent", "tool_name", "status", "ok", "returncode", "provider_id", "model",
    "model_name", "lane", "task_class", "output_mode", "reason", "error", "error_kind",
    "error_class", "exception_class", "fallback_reason", "rejection_reason",
    "failure_class", "failure_reason", "retryable", "locality", "attempt_seconds",
    "client_turn_id", "step_count", "path", "query", "command", "decision", "effect",
    # The stepped audit's own per-call receipt: which step, what it was handed, what came
    # back, and why another call was needed. Without these the trace shows four calls to one
    # model and nothing about what any of them were for.
    "call", "purpose", "context_source", "result", "why_another_call", "verdict",
    "input_tokens", "output_tokens",
)


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _prompt_records(limit: int = 400) -> list[dict[str, Any]]:
    """Captured model calls, oldest first. Empty when VOOL_DEBUG_PROMPT was never on."""
    try:
        from pathlib import Path

        from core.runtime_paths import active_data_dir

        path = Path(active_data_dir()).parent / "logs" / _PROMPT_LOG
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except Exception:
        return []
    records: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        try:
            records.append(json.loads(raw))
        except Exception:
            continue
    return records


def _latest_session_id() -> str:
    """Most recently active session, so the no-argument call does the obvious thing."""
    try:
        from core.runtime_continuity import _conn

        conn = _conn()
        try:
            row = conn.execute(
                "SELECT session_id FROM runtime_session_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return str(row["session_id"]) if row else ""
    except Exception:
        return ""


def collect_turn_trace(session_id: str = "", *, event_limit: int = 200) -> dict[str, Any]:
    """Raw joined material for a session: ledger events, model calls, tool receipts."""
    from core.runtime_continuity import list_runtime_session_events, list_runtime_tool_receipts

    session = str(session_id or "").strip() or _latest_session_id()
    if not session:
        return {"session_id": "", "events": [], "model_calls": [], "receipts": []}
    events = list_runtime_session_events(session, limit=event_limit)
    receipts = list_runtime_tool_receipts(session, limit=64)
    return {
        "session_id": session,
        "events": events,
        # Prompt capture is global, not per-session: the payload record has no session id. Everything
        # captured is shown, and each line carries its own timestamp so it lines up by eye.
        "model_calls": _prompt_records(),
        "receipts": receipts,
    }


def render_turn_trace(session_id: str = "", *, full: bool = False, max_calls: int = 12) -> str:
    """Human-readable timeline. Returns a message rather than raising when there is nothing to show."""
    data = collect_turn_trace(session_id)
    session = data["session_id"]
    if not session:
        return "No runtime events recorded yet. Drive a turn first."

    width = 200 if full else 110
    lines: list[str] = [f"═══ TURN TRACE — session {session} ═══", ""]

    events = data["events"]
    if not events:
        lines.append("  (no ledger events for this session)")
    for event in events:
        kind = str(event.get("event_type") or "")
        label = _STAGE_LABEL.get(kind, kind.upper())
        detail_bits = [
            f"{key}={_clip(event[key], 60)}"
            for key in _INTERESTING
            if key in event and event[key] not in (None, "", [], {})
        ]
        stamp = str(event.get("created_at") or "")[11:19]
        lines.append(f"  {stamp}  {label:<12} {_clip(event.get('message'), width)}")
        if detail_bits:
            lines.append(f"              {'':<12} · {'  '.join(detail_bits)}")
        if full:
            extra = {k: v for k, v in event.items() if k not in {*_INTERESTING, "session_id", "seq", "event_type", "message", "created_at"}}
            if extra:
                lines.append(f"              {'':<12} · {json.dumps(extra, default=str)[:1200]}")

    calls = data["model_calls"][-max_calls:]
    lines += ["", f"── MODEL CALLS ({len(data['model_calls'])} captured, showing {len(calls)}) ──"]
    if not calls:
        lines.append("  none captured — set VOOL_DEBUG_PROMPT=1 to record what each model was sent")
    for record in calls:
        extra = record.get("extra") or {}
        stamp = str(record.get("ts") or "")[11:19]
        lines.append(
            f"  {stamp}  {record.get('model', '?'):<14} "
            f"{record.get('message_count', 0)} msg / {record.get('total_chars', 0)} chars  "
            f"mode={extra.get('output_mode', '?')} json={extra.get('force_json')}"
        )
        for index, message in enumerate((record.get("payload") or {}).get("messages") or []):
            body = str(message.get("content") or "")
            lines.append(f"                  [{index}] {message.get('role')!s:<9} {len(body):>6} chars  {_clip(body, width)}")
            if full:
                lines.append("                      " + body.replace("\n", "\n                      "))

    receipts = data["receipts"]
    lines += ["", f"── TOOL RECEIPTS ({len(receipts)}) ──"]
    if not receipts:
        lines.append("  none")
    for receipt in receipts:
        lines.append(
            f"  {receipt.get('tool_name') or receipt.get('intent') or '?'!s:<26} "
            f"ok={receipt.get('ok')}  status={receipt.get('status', '')}  "
            f"{_clip(receipt.get('response_preview') or receipt.get('summary') or '', width)}"
        )
    return "\n".join(lines)


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Show what VOOL actually did on a turn.")
    parser.add_argument("--session", default="", help="session id (default: most recent)")
    parser.add_argument("--full", action="store_true", help="untruncated prompts and event details")
    parser.add_argument("--calls", type=int, default=12, help="how many model calls to show")
    args = parser.parse_args()
    print(render_turn_trace(args.session, full=args.full, max_calls=args.calls))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["collect_turn_trace", "render_turn_trace"]
