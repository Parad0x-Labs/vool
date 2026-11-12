"""TRIAL #4 — scaling + sealed-judge hard mode.

Arms per task: SOLO(V4) / C3 / C5 / C7 — all blind, incremental persistence.
Truth lives ONLY in trial4_truth.py, consumed by the scorer after freeze.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.council import (  # noqa: E402
    CapsuleMaterial, ContextCapsule, CouncilRuntime, CouncilSpec,
    EscalationPolicy, Role, SeatSpec, TaskInput, Tier,
)
from core.council.openrouter_adapter import OpenRouterSeat  # noqa: E402

OUT = Path(__file__).with_name("council_trial4_results.json")
V4 = "deepseek/deepseek-v4-flash-0731"

H1 = """A deployment system has two identical workers, W1 and W2.

Six jobs must execute:

A: 4 minutes
B: 7 minutes
C: 5 minutes
D: 6 minutes
E: 3 minutes
F: 8 minutes

Dependencies:

C requires A.
D requires both A and B.
E requires both C and D.
F requires B.

Additional constraints:

- A and B may start at time 0.
- Each worker can execute at most one job at a time.
- Jobs cannot be preempted.
- E and F both require the same exclusive signing device, so E and F may not overlap even if different workers are free.
- The signing device is otherwise unused.
- A job may start immediately when all prerequisites and resources are free.
- There are no setup times.

Answer ALL of the following:

1. What is the minimum possible completion time for all six jobs?
2. Give one valid schedule achieving that minimum.
3. Identify the critical dependency/resource bottleneck that proves a shorter schedule is impossible.
4. State whether the optimum is unique. If uniqueness cannot be established from the supplied information, say so rather than guessing.

Do not merely give a plausible schedule. You must justify optimality."""

H2 = """Server A recorded 18 errors.
Server B recorded 24 errors.
Server C recorded 15 errors.

A was observed for 3 hours.
B's observation duration is missing.
C was observed for 2 hours.

During A's third hour, monitoring was offline for 20 minutes.
The 18 recorded errors for A count only periods when monitoring was online.

No information is supplied about errors during the missing 20 minutes.

Answer:

1. Which server is proven to have the highest error rate?
2. Which pairwise rate comparisons are actually justified?
3. Give every rate that can be computed from the supplied evidence.
4. Explicitly identify anything that cannot be determined.

Do not infer an observation duration for B.
Do not infer unobserved errors for A."""

H3 = """Repository R has PR #91.

At 10:00: base SHA = B0, PR head = H1.
At 10:05: all required CI checks for H1 pass.
At 10:08: Reviewer Alice approves H1.
At 10:10: the contributor pushes H2.
At 10:12: local tests for H2 pass.
At 10:14: the required unit CI check for H2 passes.
At 10:15: the required integration check for H2 is SKIPPED because its runner is unavailable.
At 10:16: the required security check for H2 is still PENDING.
At 10:17: Alice comments: "Looks good after the fix."
No new formal review is submitted.
At 10:18: the base branch moves from B0 to B1.
At 10:19: a provider API response cached from 10:09 still says: mergeable=true.
At 10:20 the user asks: "Is PR #91 proven safe and ready to merge right now?"

Answer with READY / NOT_READY / UNKNOWN, then list every evidence item that is stale, insufficient, or still blocking.

Do not treat local PASS as remote CI PASS, SKIPPED as PASS, a comment as a formal review, or old mergeability evidence as current evidence."""

TASKS = {
    "H1_scheduling": H1,
    "H2_epistemic": H2,
    "H3_ci_state": H3,
    "G1_makespan": ("Three workers process five jobs: J1=6m, J2=4m, J3=9m, J4=3m, J5=5m. "
                    "J3 requires J1 finished. Jobs may not be preempted; any worker may take "
                    "any ready job. What is the minimum makespan in minutes? Show the assignment."),
    "G2_budget": ("You need VMs at $0.05/hour each. Peak load needs 14 VMs at all times for "
                  "the 720-hour month, plus you must keep 2 spare VMs running continuously. "
                  "A reserved plan cuts a VM to $0.03/hour but must be bought for the whole "
                  "month and covers exactly one VM. Reserves cost the same whether used or not. "
                  "What is the minimum monthly cost in dollars using reserves optimally?"),
    "G3_deadline": ("Pipeline stages: S1 takes 20 min on machine M1 only; S2 takes 30 min on "
                    "M1 or M2; S3 takes 25 min on M2 only. S2 starts only after S1 ends; S3 "
                    "after S2. Both machines available at t=0. Can both pipelines for TWO "
                    "independent items finish item 1 fully then item 2 fully within 150 minutes "
                    "total? Answer yes/no with the completion time."),
    "G4_epistemic_rate": ("Fleet report: car P drove 400 km using 32 L fuel. Car Q drove an "
                          "unrecorded distance using 28 L. Car R drove 250 km using 18 L. "
                          "Which car has the best verified liters-per-100km figure, and which "
                          "comparisons are impossible? Do not estimate Q's distance."),
    "G5_epistemic_survey": ("Survey: 60% of RESPONDENTS prefer option A. 500 people were sent "
                            "the survey; response count is not recorded. Nonresponse "
                            "preferences unknown. Can you conclude most USERS prefer A? Answer "
                            "yes/no and name exactly what is missing."),
    "G6_logic": ("Premises: (1) Every service with retries eventually succeeds under transient "
                 "faults. (2) Service X lacks retries. (3) Transient faults are occurring. "
                 "Conclusion: 'X will eventually succeed.' Valid or invalid? One word plus one sentence."),
    "G7_causal": ("Log table:\n"
                  "cache-flush at 09:00, latency spike at 09:01\n"
                  "deploy at 11:00, no spike\n"
                  "cache-flush at 12:00, latency spike at 12:01\n"
                  "no other events precede spikes\n"
                  "Which event does this data implicate for latency spikes, and what control is missing "
                  "to prove causation?"),
    "G8_code_state": ("def tick(state):\n"
                      "    if state == 'idle':\n        return 'running'\n"
                      "    if state == 'running':\n        return 'done'\n"
                      "    return state\n\n"
                      "s='idle'\nfor _ in range(4):\n    s=tick(s)\nprint(s)\n"
                      "What exactly is printed?"),
    "G9_config": ("Config:\nmax_connections: 100\nactive_connections: 97\nreserve_required: 5\n"
                  "rule: admission requires active_connections <= max_connections - reserve_required\n"
                  "A new connection request arrives. Is it admitted per the rule? Compute the limit."),
}

MODELS = {
    "laguna": "poolside/laguna-s-2.1",
    "north": "upstage/solar-pro4",
    "ling": "inclusionai/ling-3.0-flash",
    "llama": "meta-llama/llama-3.3-70b-instruct",
    "qwen": "qwen/qwen-2.5-72b-instruct",
}


class LiveSeat:
    def __init__(self, model_id, max_tokens=2200):
        self.inner = OpenRouterSeat(model_id, max_tokens=max_tokens)
        self.capsules = []

    def respond(self, capsule):
        self.capsules.append(capsule)
        hints = {
            "R1_BLIND": ("Produce your candidate answer plus ATOMIC claims, one per line, "
                         "each line starting 'CLAIM:'. Be exact with numbers."),
            "BALLOT": "Rank candidates best-first by correctness. LAST line: BALLOT: cand-N,...",
            "CHALLENGE": "One line per brief: CHALLENGE: <id> <supports_a|supports_b|neither> <short reason>",
            "ADVISORY": ("Epistemic check: for each disputed claim pair output lines "
                         "'d<id>: SUPPORTED' / 'UNSUPPORTED' / 'UNDERDETERMINED' / "
                         "'MISSING_PRECONDITION' based ONLY on the supplied evidence, "
                         "then one line of reasoning."),
            "ADJUDICATE": ("Resolve each dispute: DISPUTE <id>: UPHELD_A|UPHELD_B|INSUFFICIENT_EVIDENCE evidence=<ref>"),
            "ADJUDICATE_FINAL": (
                "You are the FINAL JUDGE of this council. Using the dispute records, advisor "
                "findings, challenge records and candidates above, write the complete final "
                "answer to the task yourself.\n"
                "End your reply with a section starting with the exact line '=== FINAL ===' "
                "followed by the complete final answer text. Everything after that line is, "
                "byte-for-byte, what will be committed under your name."),
        }
        hint = hints.get(capsule.phase)
        if hint:
            capsule = ContextCapsule(
                capsule.council_name, capsule.seat_id, capsule.role, capsule.phase,
                capsule.task_text, capsule.kernel_facts,
                (*capsule.materials, CapsuleMaterial("instructions", "fmt", hint)),
            )
        return self.inner.respond(capsule)


def council_arm(name: str, seats_spec, policy):
    def run(task_id: str, prompt: str, state: dict):
        key = f"{name}:{task_id}"
        if key in state:
            return
        spec = CouncilSpec(name=f"t4-{name}", seats=seats_spec, final_judge_seat_id="v4", policy=policy)
        models = {}
        for seat in seats_spec:
            if seat.seat_id != "v4":
                models[seat.seat_id] = LiveSeat(MODELS[seat.seat_id])
        models["v4"] = LiveSeat(V4, max_tokens=1600)
        rt = CouncilRuntime(spec, TaskInput(task_text=prompt), models)
        t0 = time.monotonic()
        rec = {}
        try:
            r = rt.run()
            rec.update({
                "status": r.committed_status, "answer": r.final_answer, "sha": r.final_sha256,
                "judge_raw": (rt._judge_raw_bytes or "")[:200],
                "paid_calls": r.paid_calls,
                "disputes": len(r.disputes), "gaps": len(r.gaps),
                "unresolved": list(r.unresolved),
                "verdicts": [(v.dispute_id, v.resolution) for v in r.verdicts],
                "advisor_tags": sorted({t for a in r.advisories for t in
                                        ("SUPPORTED", "UNSUPPORTED", "UNDERDETERMINED", "MISSING_PRECONDITION")
                                        if t in a.body}),
                "seat_failures": [list(f)[:2] + [f[2][:80]] for f in r.seat_failures],
                "tokens": sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0)
                              for c in r.call_records),
                "cost": sum(c.get("total_cost") or 0 for c in r.call_records
                            if isinstance(c.get("total_cost"), (int, float))),
                "calls": len([c for c in r.call_records if not c.get("error")]),
                "wall_s": round(time.monotonic() - t0, 1),
            })
        except Exception as exc:
            rec = {"error": f"{type(exc).__name__}: {str(exc)[:180]}", "wall_s": round(time.monotonic() - t0, 1)}
        state[key] = rec
        print(f"  {key}: {rec.get('status') or rec.get('error','?')[:50]}", flush=True)
    return run


def build_arms():
    def S(sid, role, tier=Tier.FREE_CLOUD, display=None):
        return SeatSpec(sid, display or sid.upper(), role, tier)

    judge = S("v4", Role.JUDGE, Tier.PAID)
    c3 = CouncilSpec(name="c3", seats=(
        S("laguna", Role.VOTER), S("north", Role.VOTER), S("llama", Role.CHALLENGER), judge),
        final_judge_seat_id="v4",
        policy=EscalationPolicy(paid_budget=2, judge_batch_size=3, sealed_judge=True))
    c5 = CouncilSpec(name="c5", seats=c3.seats + (S("qwen", Role.ADVISOR),),
                     final_judge_seat_id="v4",
                     policy=EscalationPolicy(paid_budget=2, judge_batch_size=3, sealed_judge=True))
    c7 = CouncilSpec(name="c7", seats=(
        S("laguna", Role.VOTER), S("north", Role.VOTER), S("ling", Role.VOTER),
        S("qwen", Role.ADVISOR), S("llamad", Role.ADVISOR), S("llama", Role.CHALLENGER), judge),
        final_judge_seat_id="v4",
        policy=EscalationPolicy(paid_budget=3, judge_batch_size=3, sealed_judge=True))
    MODELS["llamad"] = MODELS["llama"]
    return {"C3": council_arm("C3", c3.seats, c3.policy),
            "C5": council_arm("C5", c5.seats, c5.policy),
            "C7": council_arm("C7", c7.seats, c7.policy)}


def main():
    parts = sys.argv[1:] or ["all"]
    state = json.loads(OUT.read_text()) if OUT.exists() else {}

    if "all" in parts or "solo" in parts:
        print("== SOLO ==", flush=True)
        for tid, prompt in TASKS.items():
            key = f"SOLO:{tid}"
            if key in state:
                continue
            seat = OpenRouterSeat(V4, max_tokens=2000)
            cap = ContextCapsule("t4", "v4-solo", Role.JUDGE, "ADJUDICATE_FINAL",
                                 task_text=prompt + "\n\nYou alone answer this task.")
            try:
                o = seat.respond(cap)
                state[key] = {"answer": o.text, "tokens": [o.prompt_tokens, o.completion_tokens],
                              "cost": o.total_cost, "latency_ms": o.latency_ms}
            except Exception as exc:
                state[key] = {"error": str(exc)[:180]}
            print(f"  {key} ok={('answer' in state[key])}", flush=True)
            OUT.write_text(json.dumps(state, indent=2))

    arms = build_arms()
    for arm_name, runner in arms.items():
        if "all" in parts or arm_name in parts:
            print(f"== {arm_name} ==", flush=True)
            for tid, prompt in TASKS.items():
                runner(tid, prompt, state)
                OUT.write_text(json.dumps(state, indent=2))
                time.sleep(5)

    if "all" in parts or "traps" in parts:
        print("== SCRIPTED MINORITY TRAPS ==", flush=True)
        scripted_traps(state)
        OUT.write_text(json.dumps(state, indent=2))
    print("done")


def scripted_traps(state):
    class Fixed:
        def __init__(self, text): self.text = text; self.capsules = []
        def respond(self, capsule):
            if capsule.phase == "R1_BLIND":
                return self.text
            if capsule.phase == "BALLOT":
                return "BALLOT: cand-1,cand-2,cand-3"
            return ""
    import uuid as _u
    cases = {
        "M1_three_wrong_one_right": dict(
            wrong="The average of 20 km/h and 60 km/h is 40 km/h.",
            right="Average speed is total distance over total time: 2d/(d/20+d/60)=30 km/h.",
            prompt=("A cyclist rides uphill at 20 km/h and returns along the same road downhill "
                    "at 60 km/h. What is her average speed for the whole trip?")),
        "M2_three_right_one_loud": dict(
            wrong="Actually Python prints [0, 1, 4] because lambdas capture by value.",
            right="Python prints [2, 2, 2] because late binding: closures capture the variable i, which equals 2 after the loop.",
            prompt=("funcs=[]\nfor i in range(3):\n    funcs.append(lambda: i)\n"
                    "print([f() for f in funcs])\nWhat is printed?")),
    }
    for case, cfg in cases.items():
        if case in state["traps"] if isinstance(state.get("traps"), dict) else False:
            continue
        spec = CouncilSpec(name=case, seats=(
            SeatSpec("w1", "W1", Role.VOTER, Tier.LOCAL),
            SeatSpec("w2", "W2", Role.VOTER, Tier.LOCAL),
            SeatSpec("w3", "W3", Role.VOTER, Tier.LOCAL),
            SeatSpec("qwen", "EPISTEMIC ADVISOR", Role.ADVISOR, Tier.FREE_CLOUD),
            SeatSpec("llama", "CHALLENGER", Role.CHALLENGER, Tier.FREE_CLOUD),
            SeatSpec("v4", "V4 JUDGE", Role.JUDGE, Tier.PAID)),
            final_judge_seat_id="v4",
            policy=EscalationPolicy(paid_budget=3, judge_batch_size=3, sealed_judge=True))
        models = {}
        voter_text = cfg["wrong"] if case.startswith("M1") else cfg["right"]
        noise_text = cfg["right"] if case.startswith("M1") else cfg["wrong"]
        for sid in ("w1", "w2", "w3"):
            models[sid] = Fixed(voter_text)
        models["noise"] = Fixed(noise_text)
        # M1: minority correct = qwen advisor answers R1 with right text? Advisors DO answer R1.
        models["qwen"] = LiveSeat(MODELS["qwen"])
        models["qwen"].inner.__dict__["_forced_r1"] = None
        models["llama"] = LiveSeat(MODELS["llama"])
        # For M1 make one answering voice correct: override qwen respond for R1
        base_qwen_respond = models["qwen"].respond
        def qwen_with_fixed_r1(capsule, _b=base_qwen_respond, _t=noise_text if case.startswith("M1") else cfg["wrong"]):
            if capsule.phase == "R1_BLIND":
                captured.append(capsule)
                return _t
            return _b(capsule)
        captured = []
        models["qwen"].respond = qwen_with_fixed_r1
        models["v4"] = LiveSeat(V4, max_tokens=1400)
        rt = CouncilRuntime(spec, TaskInput(task_text=cfg["prompt"]), models)
        try:
            r = rt.run()
            state.setdefault("traps", {})[case] = {
                "status": r.committed_status, "answer": r.final_answer,
                "r1_map": {rt._anon_by_seat.get(s, s): t[:70] for s, t in r.r1_answers},
            }
        except Exception as exc:
            state.setdefault("traps", {})[case] = {"error": str(exc)[:180]}
        print(f"  trap {case}: done", flush=True)


if __name__ == "__main__":
    main()
