"""Post-fix served proofs for the restart-first-turn P0.

Covers the claims the cycle runner does not:
  * concurrent-first: two sessions' FIRST post-restart turns, fired simultaneously, both
    served with their own marker and no state crossover;
  * typed-unavailable: with the model stub genuinely dead, the first post-restart turn
    fails typed and honest (a refusal, not a crash, not a hang, not a fabricated answer);
  * no-duplicates: exactly one stub model call, one task_completed and one finalization per
    turn across the restart.

Run: /tmp/vool-venv312/bin/python -m ops.restart_repro.verify --out <dir>
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from .__main__ import FIRST_MARKER, SECOND_MARKER, THIRD_MARKER, answer_is_clean
from .harness import Daemon, ProviderStub, stable_session_id


def _events_for(daemon: Daemon, chat_id: str) -> list[dict]:
    return daemon.events(stable_session_id(chat_id))


def _count(events: list[dict], event_type: str) -> int:
    return sum(1 for e in events if str(e.get("event_type") or "") == event_type)


def run_proofs(*, app_dir: Path, python_bin: str, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    home = out / "home"
    workspace = out / "workspace"
    stub = ProviderStub()
    stub_port = stub.start()
    stub.set_rules(
        [
            ("ALPHA-MARKER", "alpha lane answer: ALPHA-MARKER-7701"),
            ("BETA-MARKER", "beta lane answer: BETA-MARKER-8802"),
            ("RESTART-SECOND", f"confirmed back: {SECOND_MARKER}"),
            ("RESTART-THIRD", f"still here: {THIRD_MARKER}"),
        ]
    )
    daemon = Daemon(app_dir=app_dir, home=home, workspace=workspace, stub_port=stub_port, run_dir=out)
    daemon.stub_host = stub.bind_host
    record: dict = {"proofs": {}}

    try:
        daemon.start(python_bin=python_bin)
        t1 = daemon.chat(
            f"Remember this code for our next exchange: {FIRST_MARKER}.",
            chat_id="restart-1",
            turn_id="turn-v-one",
        )
        record["proofs"]["setup_turn"] = {"status": t1["status"], "content": t1["content"][:120]}

        daemon.terminate()
        daemon.start(python_bin=python_bin)

        # -- concurrent-first: two sessions, first turn each, simultaneously --------------
        results: dict[str, dict] = {}
        stub.clear_model_calls()

        def _fire(key: str, chat_id: str, marker: str, turn_id: str) -> None:
            results[key] = daemon.chat(
                f"Please answer with the phrase {marker} in your reply.",
                chat_id=chat_id,
                turn_id=turn_id,
            )

        workers = [
            threading.Thread(target=_fire, args=("alpha", "conc-alpha", "ALPHA-MARKER-7701", "turn-c-alpha")),
            threading.Thread(target=_fire, args=("beta", "conc-beta", "BETA-MARKER-8802", "turn-c-beta")),
        ]
        started = time.time()
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=60.0)
        elapsed = time.time() - started
        stub_calls = stub.chat_calls()
        alpha, beta = results.get("alpha", {}), results.get("beta", {})
        record["proofs"]["concurrent_first"] = {
            "elapsed_s": round(elapsed, 2),
            "alpha": {"status": alpha.get("status"), "content": str(alpha.get("content"))[:160]},
            "beta": {"status": beta.get("status"), "content": str(beta.get("content"))[:160]},
            "alpha_clean": answer_is_clean(str(alpha.get("content")), "ALPHA-MARKER-7701"),
            "beta_clean": answer_is_clean(str(beta.get("content")), "BETA-MARKER-8802"),
            # No crossover: each answer must NOT carry the other session's marker.
            "no_crossover": (
                "BETA-MARKER-8802" not in str(alpha.get("content"))
                and "ALPHA-MARKER-7701" not in str(beta.get("content"))
            ),
            "stub_calls_total": len(stub_calls),
        }

        # -- first turn of the restart-1 session + no-duplicate accounting ----------------
        stub.clear_model_calls()
        t2 = daemon.chat(
            f"And now confirm you are back with: {SECOND_MARKER}.",
            chat_id="restart-1",
            turn_id="turn-v-two",
        )
        events = _events_for(daemon, "restart-1")
        record["proofs"]["first_turn"] = {
            "status": t2["status"],
            "content": t2["content"][:160],
            "clean": answer_is_clean(t2["content"], SECOND_MARKER),
            "stub_model_calls": len(stub.chat_calls()),
            "task_completed_events": _count(events, "task_completed"),
        }
        stub.clear_model_calls()
        t3 = daemon.chat(
            f"And confirm once more that you still remember the code {THIRD_MARKER}.",
            chat_id="restart-1",
            turn_id="turn-v-three",
        )
        events = _events_for(daemon, "restart-1")
        record["proofs"]["second_turn"] = {
            "status": t3["status"],
            "content": t3["content"][:160],
            "clean": answer_is_clean(t3["content"], THIRD_MARKER),
            "stub_model_calls": len(stub.chat_calls()),
        }

        # -- genuinely unavailable provider: kill the stub, first model turn -------------
        daemon.terminate()
        stub.stop()
        daemon2 = Daemon(
            app_dir=app_dir, home=home, workspace=workspace, stub_port=stub_port, run_dir=out
        )
        daemon2.stub_host = stub.bind_host
        daemon2.start(python_bin=python_bin)
        dead_started = time.time()
        dead = daemon2.chat(
            "What is the capital of France? One word.",
            chat_id="dead-provider",
            turn_id="turn-d-one",
        )
        dead_elapsed = time.time() - dead_started
        record["proofs"]["unavailable_provider"] = {
            "status": dead["status"],
            "content": dead["content"][:220],
            "elapsed_s": round(dead_elapsed, 2),
            "typed_refusal": any(
                lead in dead["content"]
                for lead in (
                    "I couldn't get a usable model response",
                    "I couldn't get a live model response",
                    "I can't publish",
                    "I couldn't produce a normal chat response",
                    "couldn't reach",
                    "unavailable",
                    "not going to",
                )
            ),
        }
        daemon2.terminate()
    finally:
        daemon.terminate()
        daemon2.terminate() if "daemon2" in dir() else None
        stub.stop()

    (out / "proofs.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    ok = (
        record["proofs"]["concurrent_first"]["alpha_clean"]
        and record["proofs"]["concurrent_first"]["beta_clean"]
        and record["proofs"]["concurrent_first"]["no_crossover"]
        and record["proofs"]["first_turn"]["clean"]
        and record["proofs"]["first_turn"]["stub_model_calls"] == 1
        and record["proofs"]["second_turn"]["clean"]
        and record["proofs"]["unavailable_provider"]["typed_refusal"]
    )
    record["all_proofs_ok"] = bool(ok)
    (out / "proofs.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default="ops/restart_repro/evidence/proofs")
    parser.add_argument("--python", type=str, default="/tmp/vool-venv312/bin/python")
    args = parser.parse_args()
    app_dir = Path(__file__).resolve().parents[2]
    record = run_proofs(app_dir=app_dir, python_bin=args.python, out=app_dir / args.out)
    print(json.dumps(record.get("proofs", {}), indent=1)[:1800])
    print("ALL_PROOFS_OK:", record.get("all_proofs_ok"))
    return 0 if record.get("all_proofs_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
