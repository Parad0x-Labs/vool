"""Run the restart-first-turn reproduction over N isolated cold-restart cycles.

Usage (from the worktree root):

    /tmp/vool-venv312/bin/python -m ops.restart_repro --cycles 10 --out ops/restart_repro/evidence/red

One cycle:
  1. fresh isolated home + workspace (no shared daemon, no real data)
  2. boot daemon, gate on /healthz (the product's readiness publication)
  3. turn 1: memory-remember turn (fast-path shape, mirrors the bench case)
  4. clean SIGTERM restart of the SAME home
  5. FIRST model-bound turn immediately after /healthz is ready
  6. second post-restart turn (later-turns-recover check)
  7. runtime-event attribution for the first post-restart turn
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .harness import Daemon, ProviderStub, routing_timeline, stable_session_id

FIRST_MARKER = "RESTART-FIRST-2201"
SECOND_MARKER = "RESTART-SECOND-3302"
THIRD_MARKER = "RESTART-THIRD-4403"

#: A first turn whose HTTP bytes are one of the runtime's own refusal leads did
#: NOT succeed, even when the refusal quotes the marker (the publication-gate
#: refusal names the withheld draft). Containment alone is a false positive.
REFUSAL_LEADS = (
    "I couldn't get a usable model response",
    "I couldn't get a live model response",
    "I can't publish this answer",
    "I can't publish an answer",
    "I couldn't produce a normal chat response",
    "model synthesis failed",
)


def answer_is_clean(content: str, marker: str) -> bool:
    text = str(content or "")
    if marker not in text:
        return False
    return not any(text.lstrip().startswith(lead) or lead in text[:80] for lead in REFUSAL_LEADS)


def run_cycle(index: int, *, app_dir: Path, python_bin: str, root: Path, first_turn_delay_s: float = 0.0, trace: bool = False) -> dict:
    cycle_dir = root / f"cycle-{index:02d}"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    home = cycle_dir / "home"
    workspace = cycle_dir / "workspace"
    stub = ProviderStub()
    stub_port = stub.start()
    stub_host = stub.bind_host
    stub.set_rules(
        [
            ("RESTART-SECOND", f"confirmed back: {SECOND_MARKER}"),
            ("RESTART-THIRD", f"still here: {THIRD_MARKER}"),
        ]
    )
    daemon = Daemon(app_dir=app_dir, home=home, workspace=workspace, stub_port=stub_port, run_dir=cycle_dir, trace=trace)
    daemon.stub_host = stub_host
    record: dict = {"cycle": index, "ok_first_turn": False, "ok_second_turn": False, "stub_host": stub_host}
    try:
        boot1 = daemon.start(python_bin=python_bin)
        record["boot1_ready_at"] = boot1.get("started_at") or ""

        time.time()
        turn1 = daemon.chat(
            f"Remember this code for our next exchange: {FIRST_MARKER}.",
            chat_id="restart-1",
            turn_id=f"turn-c{index}-one",
        )
        record["turn1"] = {"status": turn1["status"], "content": turn1["content"][:200]}

        # Clean restart of the same home: SIGTERM, wait for exit, boot again.
        daemon.terminate()
        boot2 = daemon.start(python_bin=python_bin)
        record["boot2_ready_at"] = boot2.get("started_at") or ""

        stub.clear_model_calls()
        session_id = stable_session_id("restart-1")
        if first_turn_delay_s > 0:
            # Control experiment only: does the failure survive a post-readiness
            # delay? Distinguishes an initialization WINDOW from a first-turn
            # shape defect. The FIX must never be a sleep.
            time.sleep(first_turn_delay_s)
        first_started = time.time()
        turn2 = daemon.chat(
            f"And now confirm you are back with: {SECOND_MARKER}.",
            chat_id="restart-1",
            turn_id=f"turn-c{index}-two",
        )
        first_elapsed = time.time() - first_started
        model_calls_first = len(stub.chat_calls())
        record["first_turn_after_restart"] = {
            "status": turn2["status"],
            "content": turn2["content"][:400],
            "elapsed_s": round(first_elapsed, 2),
            "stub_model_calls_during_turn": model_calls_first,
        }
        record["ok_first_turn"] = (
            turn2["status"] == 200
            and answer_is_clean(turn2["content"], SECOND_MARKER)
            and model_calls_first >= 1
        )

        turn3 = daemon.chat(
            f"And confirm once more that you still remember the code {THIRD_MARKER}.",
            chat_id="restart-1",
            turn_id=f"turn-c{index}-three",
        )
        record["second_turn_after_restart"] = {"status": turn3["status"], "content": turn3["content"][:200]}
        record["ok_second_turn"] = (
            turn3["status"] == 200 and answer_is_clean(turn3["content"], THIRD_MARKER)
        )

        events = daemon.events(session_id)
        record["first_turn_routing_timeline"] = routing_timeline(events)
        record["all_events"] = [
            {
                "event_type": str(e.get("event_type") or ""),
                "message": str(e.get("message") or "")[:220],
                "ts": str(e.get("created_at") or ""),
                **({"detail": str(e.get("detail") or e.get("details") or "")[:200]} if e.get("detail") or e.get("details") else {}),
            }
            for e in events
        ]
    except Exception as exc:
        record["cycle_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["stub_calls_full_log"] = stub.dump()
        daemon.terminate()
        stub.stop()
    (cycle_dir / "cycle.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--out", type=str, default="ops/restart_repro/evidence/run")
    parser.add_argument("--python", type=str, default="/tmp/vool-venv312/bin/python")
    parser.add_argument("--first-turn-delay", type=float, default=0.0)
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()

    app_dir = Path(__file__).resolve().parents[2]
    out_dir = app_dir / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for index in range(1, args.cycles + 1):
        started = time.time()
        record = run_cycle(index, app_dir=app_dir, python_bin=args.python, root=out_dir, first_turn_delay_s=args.first_turn_delay, trace=args.trace)
        record["cycle_elapsed_s"] = round(time.time() - started, 1)
        records.append(record)
        first = record.get("first_turn_after_restart") or {}
        print(
            f"cycle {index:02d}: first_turn_ok={record.get('ok_first_turn')} "
            f"second_turn_ok={record.get('ok_second_turn')} "
            f"stub_calls={first.get('stub_model_calls_during_turn')} "
            f"elapsed={record.get('cycle_elapsed_s')}s "
            f"content={str(first.get('content'))[:90]!r}",
            flush=True,
        )

    failures = [r for r in records if not r.get("ok_first_turn")]
    summary = {
        "cycles": args.cycles,
        "first_turn_failures": len(failures),
        "second_turn_failures": sum(1 for r in records if not r.get("ok_second_turn")),
        "failed_cycles": [r["cycle"] for r in failures],
        "python": args.python,
        "first_turn_delay_s": args.first_turn_delay,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
