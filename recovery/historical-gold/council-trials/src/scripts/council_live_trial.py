"""LIVE COUNCIL TRIAL #1 — real OpenRouter models through the council runtime.

Seats (user-configured):
    LING 3 FLASH      inclusionai/ling-3.0-flash        VOTER       (:free slug retired upstream; OpenRouter directs to this slug)
    LAGUNA S 2.1      poolside/laguna-s-2.1             CHALLENGER
    NORTH MINI CODE   cohere/north-mini-code:free       ADVISOR
    DEEPSEEK V4 FLASH deepseek/deepseek-v4-flash-0731   JUDGE (PAID tier)

Every claim printed at the end is derived from recorded receipts and captured
capsules — nothing is asserted without a record behind it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.council import (  # noqa: E402
    CapsuleMaterial,
    ContextCapsule,
    CouncilRuntime,
    CouncilSpec,
    EscalationPolicy,
    KernelFact,
    Role,
    SeatSpec,
    TaskInput,
    Tier,
)
from core.council.openrouter_adapter import OpenRouterSeat  # noqa: E402

MODELS = {
    "ling": ("ling", "inclusionai/ling-3.0-flash"),
    "laguna": ("laguna", "poolside/laguna-s-2.1"),
    "north": ("north", "cohere/north-mini-code:free"),
    "v4": ("v4", "deepseek/deepseek-v4-flash-0731"),
}

FORMAT_HINTS = {
    "R1_BLIND": (
        "Answer independently in 4-8 short declarative sentences. "
        "State every number explicitly. Do not hedge every sentence."
    ),
    "BALLOT": (
        "Below are anonymized candidate answers. Rank them best-first by factual "
        "soundness. Your LAST line must be exactly:\nBALLOT: cand-N,cand-M,..."
    ),
    "CHALLENGE": (
        "For each dispute brief above, output exactly one line:\n"
        "CHALLENGE: <dispute_id> <supports_a|supports_b|neither> <short evidence ref>\n"
        "Base the position only on the verified facts and the two claims."
    ),
    "ADJUDICATE": (
        "You are the final judge. Resolve ONLY from the dispute records, typed "
        "challenge records, and verified facts. For EACH dispute output exactly one line:\n"
        "DISPUTE <dispute_id>: UPHELD_A evidence=<ref>\n"
        "DISPUTE <dispute_id>: UPHELD_B evidence=<ref>\n"
        "DISPUTE <dispute_id>: INSUFFICIENT_EVIDENCE\n"
        "No other verdict format counts. An unresolved dispute stays unresolved."
    ),
}


class LiveSeat:
    """Wraps the raw HTTP seat: injects the phase format contract, captures capsules."""

    def __init__(self, model_id: str, seat_label: str) -> None:
        self.inner = OpenRouterSeat(model_id, max_tokens=900)
        self.seat_label = seat_label
        self.capsules: list[ContextCapsule] = []

    def respond(self, capsule: ContextCapsule):
        self.capsules.append(capsule)
        hint = FORMAT_HINTS.get(capsule.phase)
        if hint:
            capsule = ContextCapsule(
                council_name=capsule.council_name,
                seat_id=capsule.seat_id,
                role=capsule.role,
                phase=capsule.phase,
                task_text=capsule.task_text,
                kernel_facts=capsule.kernel_facts,
                materials=(*capsule.materials, CapsuleMaterial("instructions", "format-contract", hint)),
            )
        return self.inner.respond(capsule)

    @property
    def model(self) -> str:
        return self.inner.model


def make_council(task: TaskInput, policy: EscalationPolicy | None = None):
    spec = CouncilSpec(
        name="live-trial-1",
        seats=(
            SeatSpec("laguna", "LAGUNA S 2.1", Role.CHALLENGER, Tier.FREE_CLOUD),
            SeatSpec("north", "NORTH MINI CODE", Role.ADVISOR, Tier.FREE_CLOUD),
            SeatSpec("ling", "LING 3 FLASH", Role.VOTER, Tier.FREE_CLOUD),
            SeatSpec("v4", "DEEPSEEK V4 FLASH", Role.JUDGE, Tier.PAID),
        ),
        final_judge_seat_id="v4",
        policy=policy or EscalationPolicy(escalate_kinds=frozenset({"factual", "causal"}), paid_budget=1),
    )
    models = {
        sid: LiveSeat(model, sid) for sid, (_label, model) in MODELS.items()
    }
    return spec, CouncilRuntime(spec, task, models), models


# --------------------------------------------------------------------------- tasks

TASK_A = TaskInput(
    task_text=(
        "Historical records from nine 19th-century mill towns show that every town "
        "with a lamp-oil refinery also had a glassworks within ten years, and in 8 of "
        "the 9 towns the refinery predates the glassworks. Question: what is the causal "
        "direction between lamp-oil refineries and glassworks, and why?"
    ),
    kernel_facts=(
        KernelFact("kf-a1", "In 8 of the 9 towns the lamp-oil refinery was founded before the glassworks",
                   "tool:archive.scan#r-1"),
    ),
)

TASK_B = TaskInput(
    task_text=(
        "Design the ingest pipeline for a telemetry service. Hard constraints: peak "
        "12,000 events/sec, average payload 4 KB, p99 end-to-end latency under 250 ms, "
        "monthly infrastructure budget $1,100, team of 2 engineers, single cloud region. "
        "Compare two architectures: (1) direct HTTPS batched writes into Postgres "
        "partitioned tables; (2) Kafka with 3 brokers plus consumer workers writing "
        "columnar storage. Recommend exactly one architecture and state its expected peak "
        "throughput headroom and estimated monthly cost."
    ),
)

TASK_C = TaskInput(
    task_text=(
        "A 6-engineer developer-tools startup has $18k MRR and 3.2% monthly churn. It "
        "must pick exactly ONE focus for next quarter: (a) build a self-hosted deployment "
        "option, (b) deepen the hosted product with SSO and audit logs for mid-market "
        "buyers, or (c) attack churn with onboarding rework. Everything is measurable; "
        "only one track fits in the quarter. Which track should it pick, why, and what is "
        "the strongest risk of that choice?"
    ),
)


def advisor_pre_run_note(models: dict, task: TaskInput) -> str | None:
    """Consult the ADVISOR seat on the bare task (never on peers' answers)."""
    north = models["north"]
    cap = ContextCapsule(
        council_name="live-trial-1", seat_id="north", role=Role.ADVISOR,
        phase="ADVISORY_PREP", task_text=task.task_text,
        kernel_facts=task.kernel_facts,
    )
    try:
        out = north.respond(cap)
        return out.text
    except Exception as exc:  # honest degradation: advisory is optional
        print(f"[advisor prep failed: {exc}]")
        return None


def run_task(name: str, task: TaskInput) -> dict:
    spec, rt, models = make_council(task)
    note = advisor_pre_run_note(models, task)
    if note:
        rt.collect_advisory("north", note)

    receipt = None
    error = None
    try:
        receipt = rt.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    # ---------------- proof extraction -------------------------------------
    r1_prompts = {sid: [c for c in m.capsules if c.phase == "R1_BLIND"] for sid, m in models.items()}
    blind_ok = all(
        caps and caps[0].materials == () for sid, caps in r1_prompts.items() if caps
    )
    peer_tokens = ["LAGUNA S 2.1", "NORTH MINI CODE", "LING 3 FLASH", "DEEPSEEK V4 FLASH",
                   "poolside/", "inclusionai/", "cohere/", "deepseek/"]
    bleed_hits = []
    for sid, m in models.items():
        for cap in m.capsules:
            prompt = cap.render_prompt()
            others = [t for t in peer_tokens if t not in MODELS[sid] and t.split()[0].lower() != sid[:4]]
            for tok in ("LAGUNA S 2.1", "NORTH MINI CODE", "LING 3 FLASH", "DEEPSEEK V4 FLASH"):
                if tok.startswith(MODELS[sid][0].upper()[:4]):
                    continue
                if tok in prompt and tok.lower() not in prompt.replace(tok, "").lower():
                    bleed_hits.append((sid, cap.phase, tok))
    # simpler authoritative check: no seat name appears in ANY prompt
    bleed_authoritative = []
    for sid, m in models.items():
        own = {"laguna": "LAGUNA", "north": "NORTH", "ling": "LING", "v4": "DEEPSEEK"}[sid]
        for cap in m.capsules:
            prompt = cap.render_prompt()
            for label in ("LAGUNA S 2.1", "NORTH MINI CODE", "LING 3 FLASH", "DEEPSEEK V4 FLASH"):
                if label in prompt and not label.startswith(own):
                    bleed_authoritative.append((sid, cap.phase, label))
                    break

    invoked = sorted({r.get("seat_id") for r in rt.transcript.call_records if not r.get("error")})
    judge_r1 = [s for s, p in rt.transcript.model_calls if s == "v4" and p == "R1_BLIND"]
    ballot_seats = [b.voter_seat_id for b in (receipt.ballots if receipt else ())]
    anon_ok = True
    for sid, m in models.items():
        for cap in m.capsules:
            if cap.phase in ("BALLOT", "CHALLENGE", "ADJUDICATE") and cap.materials:
                body = " ".join(x.body for x in cap.materials)
                if any(lbl in body for lbl in ("LAGUNA S 2.1", "NORTH MINI CODE", "LING 3 FLASH", "DEEPSEEK V4 FLASH")):
                    anon_ok = False

    result = {
        "name": name,
        "receipt": receipt,
        "error": error,
        "blind_ok": blind_ok,
        "invoked_seats": invoked,
        "judge_in_r1": judge_r1,
        "ballot_seats": ballot_seats,
        "anonymized": anon_ok,
        "bleed": bleed_authoritative,
        "failures": list(rt.transcript.seat_failures),
        "call_records": list(rt.transcript.call_records),
        "paid_calls": rt.transcript.paid_calls,
        "seat_models": {sid: m.model for sid, m in models.items()},
        "attested": {
            sid: [r.get("attested_model") for r in rt.transcript.call_records
                  if r.get("seat_id") == sid]
            for sid in models
        },
    }
    return result


def main() -> int:
    outcomes = {}
    for name, task in (("TASK A — causal trap", TASK_A), ("TASK B — numerical/architectural", TASK_B),
                       ("TASK C — hard open-ended", TASK_C)):
        print(f"\n{'=' * 70}\n{name}\n{'=' * 70}", flush=True)
        res = run_task(name.split("—")[0].strip(), task)
        outcomes[name] = res
        r = res["receipt"]
        if r is None:
            print(f"RUN FAILED: {res['error']}")
            print(f"seat failures: {res['failures']}")
            continue
        print(f"status        : {r.committed_status}")
        print(f"paid calls    : {res['paid_calls']}")
        print(f"invoked seats : {res['invoked_seats']}")
        print(f"disputes      : {[(d.dispute_id, d.kind) for d in (r.disputes + r.gaps)]}")
        print(f"verdicts      : {[(v.dispute_id, v.resolution) for v in r.verdicts]}")
        print(f"unresolved    : {list(r.unresolved)}")
        print(f"seat failures : {[f[:2] for f in r.seat_failures]}")
        print(f"final sha256  : {r.final_sha256}")
        print(f"ANSWER        :\n{r.final_answer[:1200]}")

    # ---------------- aggregate report ------------------------------------
    print(f"\n{'=' * 70}\nAGGREGATE PROOF DATA\n{'=' * 70}")
    total_prompt = total_completion = 0
    total_cost = 0.0
    calls_by_seat: dict[str, int] = {}
    for name, res in outcomes.items():
        for rec in res["call_records"]:
            if rec.get("error"):
                print(f"{name[:7]} ERROR {rec['seat_id']}: {rec['error'][:110]}")
                continue
            sid = rec["seat_id"]
            calls_by_seat[sid] = calls_by_seat.get(sid, 0) + 1
            total_prompt += rec.get("prompt_tokens") or 0
            total_completion += rec.get("completion_tokens") or 0
            c = rec.get("total_cost")
            if isinstance(c, (int, float)):
                total_cost += c
            print(f"{name[:7]} {sid:<7} {rec['phase']:<12} {rec.get('requested_model',''):<36}"
                  f"-> {str(rec.get('attested_model'))[:36]:<36} "
                  f"tok={rec.get('prompt_tokens')}/{rec.get('completion_tokens')} "
                  f"ms={rec.get('latency_ms')}")
    print("\ncalls by seat:", calls_by_seat)
    print(f"total tokens: prompt={total_prompt} completion={total_completion} "
          f"total={total_prompt + total_completion}")
    print(f"total reported cost: ${total_cost:.6f}")
    json_out = Path(__file__).with_name("council_live_trial_results.json")
    import json

    payload = {}
    for name, res in outcomes.items():
        r = res["receipt"]
        payload[name] = {
            "error": res["error"],
            "status": r.committed_status if r else None,
            "final_sha256": r.final_sha256 if r else None,
            "answer": r.final_answer if r else None,
            "paid_calls": res["paid_calls"],
            "blind_ok": res["blind_ok"],
            "invoked_seats": res["invoked_seats"],
            "judge_in_r1": res["judge_in_r1"],
            "ballot_seats": res["ballot_seats"],
            "anonymized": res["anonymized"],
            "bleed": res["bleed"],
            "seat_failures": [list(f) for f in res["failures"]],
            "disputes": [
                {"id": d.dispute_id, "kind": d.kind, "a": d.claim_a.text,
                 "b": d.claim_b.text if d.claim_b else None}
                for d in ((r.disputes + r.gaps) if r else [])
            ],
            "verdicts": [{"id": v.dispute_id, "resolution": v.resolution, "evidence": v.evidence_ref}
                         for v in (r.verdicts if r else [])],
            "unresolved": list(r.unresolved) if r else [],
            "call_records": res["call_records"],
        }
    json_out.write_text(json.dumps(payload, indent=2))
    print(f"\nresults written: {json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
