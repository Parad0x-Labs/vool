"""COUNCIL TRIAL #3 — does Council earn its cost?

Blind arms per task: V4 alone vs Council (laguna+ling answering, v4 judge).
Plus two scripted false-consensus attacks (deterministic seats + real judge).

Incremental persistence; resumable via state file. Raw first-run results only.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import trial3_tasks  # noqa: E402
from core.council import (  # noqa: E402
    CapsuleMaterial,
    ContextCapsule,
    CouncilRuntime,
    CouncilSpec,
    EscalationPolicy,
    Role,
    SeatSpec,
    TaskInput,
    Tier,
)
from core.council.openrouter_adapter import OpenRouterSeat  # noqa: E402
from core.council.runtime import LiveSeatError  # noqa: E402

OUT = Path(__file__).with_name("council_trial3_results.json")
V4 = "deepseek/deepseek-v4-flash-0731"
UNCERTAINTY_MARKERS = (
    "cannot determine", "cannot be determined", "insufficient", "not enough",
    "cannot say", "undetermined", "cannot establish", "unrecorded",
    "no way to know", "impossible to determine", "missing",
)


class LiveSeat:
    def __init__(self, model_id: str, max_tokens: int = 2500) -> None:
        self.inner = OpenRouterSeat(model_id, max_tokens=max_tokens)
        self.capsules: list[ContextCapsule] = []

    def respond(self, capsule: ContextCapsule):
        self.capsules.append(capsule)
        hint = {
            "R1_BLIND": ("Answer in 2-5 short declarative sentences. Give the exact "
                         "answer explicitly (numbers exactly). If the data is insufficient, "
                         "say exactly that and name the missing quantity."),
            "BALLOT": "Rank candidates best-first by correctness. LAST line: BALLOT: cand-N,...",
            "CHALLENGE": "One line per brief: CHALLENGE: <dispute_id> <supports_a|supports_b|neither> <short ref>",
            "ADJUDICATE": ("For EACH dispute exactly one line:\n"
                           "DISPUTE <id>: UPHELD_A evidence=<ref>\n"
                           "DISPUTE <id>: UPHELD_B evidence=<ref>\n"
                           "DISPUTE <id>: INSUFFICIENT_EVIDENCE"),
        }.get(capsule.phase)
        if hint:
            capsule = ContextCapsule(
                capsule.council_name, capsule.seat_id, capsule.role, capsule.phase,
                capsule.task_text, capsule.kernel_facts,
                (*capsule.materials, CapsuleMaterial("instructions", "fmt", hint)),
            )
        return self.inner.respond(capsule)

    @property
    def model(self):
        return self.inner.model


def load_state() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text())
    return {"solo": {}, "council": {}, "traps": {}}


def save(state: dict) -> None:
    OUT.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------- solo arm

def run_solo(task: dict, state: dict) -> None:
    tid = task["id"]
    if tid in state["solo"]:
        return
    seat = OpenRouterSeat(V4, max_tokens=1800)
    cap = ContextCapsule(
        "trial3-solo", "v4", Role.JUDGE, "SOLO",
        task_text=task["prompt"] + "\n\nYou are the only model consulted. Answer directly.",
    )
    t0 = time.monotonic()
    try:
        out = seat.respond(cap)
        state["solo"][tid] = {
            "text": out.text, "tokens": [out.prompt_tokens, out.completion_tokens],
            "cost": out.total_cost, "latency_ms": out.latency_ms,
        }
    except Exception as exc:
        state["solo"][tid] = {"error": str(exc)[:200]}
    print(f"  solo {tid}: {state['solo'][tid].get('error') or 'ok'}", flush=True)


# ---------------------------------------------------------------- council arm

def run_council(task: dict, state: dict) -> None:
    tid = task["id"]
    if tid in state["council"]:
        return
    spec = CouncilSpec(
        name=f"t3-{tid}",
        seats=(
            SeatSpec("laguna", "LAGUNA", Role.CHALLENGER, Tier.FREE_CLOUD),
            SeatSpec("ling", "LING", Role.VOTER, Tier.FREE_CLOUD),
            SeatSpec("v4", "V4 JUDGE", Role.JUDGE, Tier.PAID),
        ),
        final_judge_seat_id="v4",
        policy=EscalationPolicy(paid_budget=2, judge_batch_size=3),
    )
    models = {
        "laguna": LiveSeat("poolside/laguna-s-2.1"),
        "ling": LiveSeat("inclusionai/ling-3.0-flash"),
        "v4": LiveSeat(V4, max_tokens=1200),
    }
    rt = CouncilRuntime(spec, TaskInput(task_text=task["prompt"]), models)
    t0 = time.monotonic()
    rec: dict = {}
    try:
        receipt = rt.run()
        rec.update({
            "status": receipt.committed_status,
            "answer": receipt.final_answer,
            "sha": receipt.final_sha256,
            "paid_calls": receipt.paid_calls,
            "n_disputes": len(receipt.disputes) + len(receipt.gaps),
            "n_contested": len(receipt.disputes),
            "kinds": sorted({d.kind for d in receipt.disputes}),
            "unresolved": list(receipt.unresolved),
            "verdicts": [(v.dispute_id, v.resolution) for v in receipt.verdicts],
            "seat_failures": [list(f) for f in receipt.seat_failures],
            "call_records": [
                {k: v for k, v in c.items()} for c in receipt.call_records
            ],
            "tokens": sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0)
                          for c in receipt.call_records),
            "cost": sum(c.get("total_cost") or 0 for c in receipt.call_records
                        if isinstance(c.get("total_cost"), (int, float))),
            "wall_s": round(time.monotonic() - t0, 1),
        })
    except Exception as exc:
        rec = {"error": f"{type(exc).__name__}: {exc}", "wall_s": round(time.monotonic() - t0, 1)}
    state["council"][tid] = rec
    print(f"  council {tid}: {rec.get('status') or rec.get('error','?')[:60]}", flush=True)


# ---------------------------------------------------------------- scripted traps

def scripted_trap(name: str, wrong_text: str, right_text: str, prompt: str, state: dict) -> None:
    """Two deterministic seats assert the WRONG answer; real laguna answers R1;
    real v4 judges. Does the minority/right side survive?"""
    if name in state["traps"]:
        return

    class Fixed:
        def __init__(self, text: str) -> None:
            self.text = text

        def respond(self, capsule):
            if capsule.phase == "R1_BLIND":
                return self.text
            if capsule.phase == "BALLOT":
                return "BALLOT: cand-1,cand-2,cand-3"
            return ""

    spec = CouncilSpec(
        name=name,
        seats=(
            SeatSpec("wrong-a", "Wrong A", Role.VOTER, Tier.LOCAL),
            SeatSpec("wrong-b", "Wrong B", Role.CHALLENGER, Tier.LOCAL),
            SeatSpec("laguna", "LAGUNA", Role.ADVISOR, Tier.FREE_CLOUD),
            SeatSpec("v4", "V4 JUDGE", Role.JUDGE, Tier.PAID),
        ),
        final_judge_seat_id="v4",
        policy=EscalationPolicy(paid_budget=2, judge_batch_size=3),
    )
    models = {
        "wrong-a": Fixed(wrong_text),
        "wrong-b": Fixed(wrong_text),
        "laguna": LiveSeat("poolside/laguna-s-2.1"),
        "v4": LiveSeat(V4, max_tokens=1200),
    }
    rt = CouncilRuntime(spec, TaskInput(task_text=prompt), models)
    rec: dict = {}
    try:
        receipt = rt.run()
        rec.update({
            "status": receipt.committed_status,
            "answer": receipt.final_answer,
            "r1_by_cand": {rt._anon_by_seat.get(sid, sid): txt[:80]
                           for sid, txt in receipt.r1_answers},
            "ballots": [(b.voter_seat_id, b.ranked) for b in receipt.ballots],
            "verdicts": [(v.dispute_id, v.resolution) for v in receipt.verdicts],
            "unresolved": list(receipt.unresolved),
            "paid_calls": receipt.paid_calls,
        })
    except Exception as exc:
        rec = {"error": str(exc)[:200]}
    state["traps"][name] = rec
    print(f"  trap {name}: {rec.get('status') or rec.get('error')}", flush=True)


def main() -> int:
    parts = sys.argv[1:] or ["all"]
    state = load_state()
    run_all = "all" in parts

    if run_all or "solo" in parts:
        print("== SOLO ARM ==", flush=True)
        for task in trial3_tasks.TASKS:
            run_solo(task, state)
            save(state)

    if run_all or "council" in parts:
        print("== COUNCIL ARM ==", flush=True)
        for task in trial3_tasks.TASKS:
            run_council(task, state)
            save(state)
            time.sleep(8)  # pacing: free/paid lanes rate-limit under rapid fire

    if run_all or "traps" in parts:
        print("== SCRIPTED FALSE-CONSENSUS TRAPS ==", flush=True)
        # majority WRONG on the classic widget trap (truth: 5 minutes)
        scripted_trap(
            "majority_wrong_widgets",
            wrong_text="100 machines take 100 minutes to make 100 widgets.",
            right_text="100 machines take 5 minutes to make 100 widgets.",
            prompt="If 5 machines take 5 minutes to make 5 widgets, how long do 100 machines take to make 100 widgets?",
            state=state,
        )
        save(state)
        # inverted: majority CORRECT, one contrarian wrong (truth: day 47)
        scripted_trap_inverted(state)
        save(state)

    print("done")
    return 0


def scripted_trap_inverted(state: dict) -> None:
    if "minority_wrong_halffull" in state["traps"]:
        return

    class Fixed:
        def __init__(self, text: str) -> None:
            self.text = text

        def respond(self, capsule):
            if capsule.phase == "R1_BLIND":
                return self.text
            if capsule.phase == "BALLOT":
                return "BALLOT: cand-1,cand-2"
            return ""

    spec = CouncilSpec(
        name="minority_wrong",
        seats=(
            SeatSpec("right-a", "Right A", Role.VOTER, Tier.LOCAL),
            SeatSpec("right-b", "Right B", Role.CHALLENGER, Tier.LOCAL),
            SeatSpec("noise", "Contrarian", Role.ADVISOR, Tier.LOCAL),
            SeatSpec("v4", "V4 JUDGE", Role.JUDGE, Tier.PAID),
        ),
        final_judge_seat_id="v4",
        policy=EscalationPolicy(paid_budget=2, judge_batch_size=3),
    )
    models = {
        "right-a": Fixed("The warehouse was half full on day 47."),
        "right-b": Fixed("Half full occurs on day 47."),
        "noise": Fixed("The warehouse was half full on day 24."),
        "v4": LiveSeat(V4, max_tokens=1200),
    }
    rt = CouncilRuntime(
        spec,
        TaskInput(task_text=("A warehouse order doubles every day. The warehouse is exactly full "
                             "on day 48. On which day was it half full?")),
        models,
    )
    try:
        receipt = rt.run()
        state["traps"]["minority_wrong_halffull"] = {
            "status": receipt.committed_status,
            "answer": receipt.final_answer,
            "verdicts": [(v.dispute_id, v.resolution) for v in receipt.verdicts],
            "unresolved": list(receipt.unresolved),
        }
    except Exception as exc:
        state["traps"]["minority_wrong_halffull"] = {"error": str(exc)[:200]}
    print(f"  trap inverted: done", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
