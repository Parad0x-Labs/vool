"""Read the kernel journal (+ optional live event side-channel) into Turns."""
from __future__ import annotations

import json
import os
from pathlib import Path

from vool_visualizer.models import Effect, Obligation, Turn


def load_journal(path: str | Path) -> list[Turn]:
    """Parse $NULLA_HOME/repl_sessions.jsonl (or any journal path) into Turns."""
    rows: list[Turn] = []
    p = Path(path)
    if not p.exists():
        return rows
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows.append(_row_to_turn(row))
    rows.sort(key=lambda t: t.turn_index)
    return rows


def _row_to_turn(row: dict) -> Turn:
    tape = row.get("tape") or []
    effects: list[Effect] = []
    for i, e in enumerate(tape):
        if not isinstance(e, dict):
            continue
        effects.append(Effect(
            step=i + 1,
            effect_id=str(e.get("effect_id", "")),
            args_hash=str(e.get("args_hash", "")),
            result=e.get("result"),
            error=e.get("error"),
        ))
    obs = [
        Obligation(id=str(o.get("id", "")), lane=str(o.get("lane", "")),
                   status=str(o.get("status", "")), description=str(o.get("description", "")))
        for o in (row.get("terminal_state") or [])
    ]
    return Turn(
        turn_index=int(row.get("turn_index") or 0),
        question=str(row.get("question", "")),
        answer=str(row.get("answer", "")),
        answer_present=bool(row.get("answer_present")),
        answer_sha256=str(row.get("answer_sha256", "")),
        build=str(row.get("build", "")),
        model=str(row.get("model", "")),
        run_id=str(row.get("run_id", "")),
        ts=str(row.get("ts", "")),
        stamps=dict(row.get("stamps") or {}),
        effects=effects,
        terminal_state=obs,
        transcript=str(row.get("transcript", "")),
        crashed=bool(row.get("crashed")),
        crash_exception=row.get("crash_exception"),
    )


def turns_to_json(turns: list[Turn]) -> list[dict]:
    """Serializable projection for the UI (no huge transcript by default)."""
    out = []
    for t in turns:
        out.append({
            "turn_index": t.turn_index,
            "question": t.question,
            "answer": t.answer,
            "answer_present": t.answer_present,
            "answer_sha256": t.answer_sha256,
            "build": t.build,
            "model": t.model,
            "run_id": t.run_id,
            "ts": t.ts,
            "stamps": t.stamps,
            "commit": t.commit,
            "crashed": t.crashed,
            "crash_exception": t.crash_exception,
            "web_torn": t.web_torn,
            "machine_torn": t.machine_torn,
            "terminal_state": [{"id": o.id, "lane": o.lane, "status": o.status,
                                "status_label": o.label} for o in t.terminal_state],
            "effects": [
                {"step": e.step, "effect_id": e.effect_id, "ok": e.ok,
                 "error": e.error, "result_preview": _preview(e.result)}
                for e in t.effects
            ],
        })
    return out


def _preview(value: object, limit: int = 280) -> str:
    if isinstance(value, str):
        s = value
    elif isinstance(value, (list, dict)):
        s = json.dumps(value, ensure_ascii=False)
    else:
        s = str(value)
    return (s[:limit] + "…") if len(s) > limit else s


def read_live_events(path: str | Path) -> list[dict]:
    """Read the optional event side-channel written by vis_events.py (env-gated)."""
    p = Path(path)
    if not p.exists():
        return []
    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events
