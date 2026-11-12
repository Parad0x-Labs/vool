"""LIVE COUNCIL TRIAL #2 — attack claim-alignment authority with real models.

Parts:
  A. Five alignment scenarios designed to trigger: paraphrase equivalence,
     causal inversion, same-value/different-fact, conditional disagreement,
     numerical disagreement. Dumps RAW R1 claims + the full dispute graph.
  B. Judge truncation reproduction + bounded batching under one authorization
     and a fixed paid budget.
  C. V4-alone arm on the three Trial-#1 tasks (no council output shown),
     for quality/latency/token/cost comparison against Trial #1 receipts.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

OUT = Path(__file__).with_name("council_live_trial2_results.json")
RESULTS: dict = {"alignment": {}, "batching": {}, "solo": {}}


class LiveSeat:
    def __init__(self, model_id: str, max_tokens: int = 3000) -> None:
        self.inner = OpenRouterSeat(model_id, max_tokens=max_tokens)
        self.capsules: list[ContextCapsule] = []

    def respond(self, capsule: ContextCapsule):
        self.capsules.append(capsule)
        hint = {
            "R1_BLIND": "Answer independently in 3-6 short declarative sentences. State every number explicitly.",
            "BALLOT": "Rank candidates best-first. LAST line exactly: BALLOT: cand-N,cand-M,...",
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
    def model(self) -> str:
        return self.inner.model


def small_council(task: TaskInput, *, seats=("laguna", "ling"), policy: EscalationPolicy | None = None):
    role_map = {"laguna": Role.CHALLENGER, "ling": Role.VOTER,
                "north": Role.ADVISOR}
    model_ids = {"laguna": "poolside/laguna-s-2.1",
                 "ling": "inclusionai/ling-3.0-flash",
                 "north": "cohere/north-mini-code:free"}
    chosen = list(seats)
    spec_seats = [SeatSpec(s, s.upper(), role_map[s], Tier.FREE_CLOUD) for s in chosen]
    spec_seats.append(SeatSpec("v4", "V4 JUDGE", Role.JUDGE, Tier.PAID))
    spec = CouncilSpec(
        name="trial2", seats=tuple(spec_seats), final_judge_seat_id="v4",
        policy=policy or EscalationPolicy(paid_budget=1),
    )
    models = {sid: LiveSeat(model_ids[sid]) for sid in chosen}
    models["v4"] = LiveSeat("deepseek/deepseek-v4-flash-0731", max_tokens=1200)
    return spec, CouncilRuntime(spec, task, models), models


def dump_run(res: dict, rt: CouncilRuntime, receipt) -> None:
    res["status"] = receipt.committed_status if receipt else None
    res["error"] = None
    res["claims"] = [
        {"candidate": c.candidate, "text": c.text,
         "quantities": [list(q) for q in (c.sem.quantities if c.sem else ())],
         "causal": list(c.sem.causal_pair) if c.sem and c.sem.causal_pair else None,
         "negated": bool(c.sem.polarity_negated) if c.sem else None,
         "condition": c.sem.condition if c.sem else ""}
        for c in rt.transcript.delta.claims_for_dump() if True
    ] if hasattr(rt.transcript.delta, "claims_for_dump") else None
    # simpler: rebuild claims from r1 answers
    from core.council.claims import extract_claims
    res["raw_claims"] = {
        label: [c.text for c in extract_claims(text, label)]
        for label, text in sorted(
            {rt._anon_by_seat[sid]: t for sid, t in rt.transcript.r1_answers.items()}.items()
        )
    }
    res["dispute_graph"] = [
        {"id": d.dispute_id, "kind": d.kind,
         "a": d.claim_a.text, "b": d.claim_b.text if d.claim_b else None}
        for d in (rt.transcript.delta.disputes + rt.transcript.delta.gaps)
    ]
    if receipt:
        res["verdicts"] = [{"id": v.dispute_id, "resolution": v.resolution} for v in receipt.verdicts]
        res["unresolved"] = list(receipt.unresolved)
        res["paid_calls"] = receipt.paid_calls
        res["sha"] = receipt.final_sha256
        res["call_records"] = receipt.call_records
        res["seat_failures"] = [list(f) for f in receipt.seat_failures]
        res["answer"] = receipt.final_answer


SCENARIOS = {
    "paraphrase_equivalence": TaskInput(
        task_text="A Kafka cluster of 3 brokers costs $730 per month total. State the monthly cost of the Kafka cluster."),
    "causal_inversion": TaskInput(
        task_text=("Records show towns with lamp-oil refineries gained glassworks ten years later. "
                   "What is the causal direction between refineries and glassworks, and why?")),
    "same_value_different_fact": TaskInput(
        task_text=("Sensor report: the CPU temperature is 45 celsius and the free memory is 45 gigabytes. "
                   "State the CPU temperature and the free memory size."),
    ),
    "conditional_disagreement": TaskInput(
        task_text=("The payment service is considered safe when it runs on at least 3 replicas. "
                   "It currently runs on 2 replicas. Is the payment service safe right now? Why?"),
    ),
    "numerical_disagreement": TaskInput(
        task_text=("Postgres direct writes cost about $650/month; Kafka (3 brokers + workers) costs about "
                   "$950/month. The budget is $700/month. Which fits, and by what margin?"),
    ),
}


def part_alignment() -> None:
    print("\n" + "=" * 70 + "\nPART A — LIVE ALIGNMENT SCENARIOS\n" + "=" * 70, flush=True)
    for name, task in SCENARIOS.items():
        print(f"\n--- {name} ---", flush=True)
        _, rt, _models = small_council(task)
        res: dict = {}
        try:
            t0 = time.monotonic()
            receipt = rt.run()
            dump_run(res, rt, receipt)
            res["wall_s"] = round(time.monotonic() - t0, 1)
            print(f"status={receipt.committed_status} disputes="
                  f"{[(d.dispute_id, d.kind) for d in receipt.disputes]} "
                  f"gaps={len(receipt.gaps)} paid={receipt.paid_calls} "
                  f"unresolved={list(receipt.unresolved)}")
            for c in receipt.call_records:
                if c.get("error"):
                    print("  SEAT FAIL:", c["seat_id"], c["phase"], str(c["error"])[:100])
        except Exception as exc:
            res = {"error": f"{type(exc).__name__}: {exc}"}
            print("RUN ERROR:", res["error"])
        RESULTS["alignment"][name] = res


def part_batching() -> None:
    print("\n" + "=" * 70 + "\nPART B — JUDGE BATCHING UNDER FIXED BUDGET\n" + "=" * 70, flush=True)
    task = TaskInput(
        task_text=("Design the ingest pipeline. Constraints: peak 12,000 events/sec, 4 KB payloads, "
                   "p99 < 250 ms, budget $1,100/month, team of 2, single region. Choose between "
                   "(1) HTTPS batched Postgres and (2) Kafka 3 brokers + columnar workers. "
                   "State throughput headroom and monthly cost."),
    )
    # four answering voices -> many disputes; batch=2, budget=2 -> at most 2 judge
    # calls resolve at most 4 disputes; the rest MUST surface as unresolved.
    policy = EscalationPolicy(paid_budget=2, judge_batch_size=2)
    _, rt, _models = small_council(task, seats=("laguna", "ling", "north"), policy=policy)
    res: dict = {}
    try:
        receipt = rt.run()
        dump_run(res, rt, receipt)
        contested_kinds = [d.kind for d in receipt.disputes]
        n_contested = len(receipt.disputes)
        expected_batches = min(2, -(-n_contested // 2))
        actual_judge_calls = len([c for c in receipt.call_records
                                  if c.get("seat_id") == "v4" and not c.get("error")])
        res["contested"] = n_contested
        res["expected_batches"] = expected_batches
        res["actual_judge_calls"] = actual_judge_calls
        print(f"contested={n_contested} judge_calls={actual_judge_calls} "
              f"(budget allows 2 x batch of 2 = 4 resolutions)")
        print(f"resolved={[v['id'] for v in res['verdicts']]} "
              f"unresolved={res['unresolved']}")
        assert actual_judge_calls <= 2, "budget violated"
    except Exception as exc:
        res = {"error": f"{type(exc).__name__}: {exc}"}
        print("RUN ERROR:", res["error"])
    RESULTS["batching"] = res


TRIAL1_TASKS = {
    "task_A_causal": (
        "Historical records from nine 19th-century mill towns show that every town "
        "with a lamp-oil refinery also had a glassworks within ten years, and in 8 of "
        "the 9 towns the refinery predates the glassworks. Question: what is the causal "
        "direction between lamp-oil refineries and glassworks, and why? Answer in 4-8 "
        "declarative sentences."),
    "task_B_numeric": (
        "Design the ingest pipeline for a telemetry service. Hard constraints: peak "
        "12,000 events/sec, average payload 4 KB, p99 end-to-end latency under 250 ms, "
        "monthly infrastructure budget $1,100, team of 2 engineers, single cloud region. "
        "Compare two architectures: (1) direct HTTPS batched writes into Postgres "
        "partitioned tables; (2) Kafka with 3 brokers plus consumer workers writing "
        "columnar storage. Recommend exactly one architecture and state its expected peak "
        "throughput headroom and estimated monthly cost. Answer in 5-8 sentences."),
    "task_C_open": (
        "A 6-engineer developer-tools startup has $18k MRR and 3.2% monthly churn. It "
        "must pick exactly ONE focus for next quarter: (a) build a self-hosted deployment "
        "option, (b) deepen the hosted product with SSO and audit logs for mid-market "
        "buyers, or (c) attack churn with onboarding rework. Everything is measurable; "
        "only one track fits in the quarter. Which track should it pick, why, and what is "
        "the strongest risk of that choice? Answer in 5-8 sentences."),
}


def part_solo() -> None:
    print("\n" + "=" * 70 + "\nPART C — V4 ALONE (same problems, no council)\n" + "=" * 70, flush=True)
    for name, prompt in TRIAL1_TASKS.items():
        seat = OpenRouterSeat("deepseek/deepseek-v4-flash-0731", max_tokens=2000)
        cap = ContextCapsule(
            "solo", "v4-solo", Role.JUDGE, "SOLO",
            task_text=prompt + "\n\nAnswer directly. You are the only model consulted.",
        )
        t0 = time.monotonic()
        try:
            out = seat.respond(cap)
            RESULTS["solo"][name] = {
                "answer": out.text,
                "attested_model": out.model_attested,
                "prompt_tokens": out.prompt_tokens,
                "completion_tokens": out.completion_tokens,
                "cost": out.total_cost,
                "latency_ms": out.latency_ms,
                "wall_s": round(time.monotonic() - t0, 1),
            }
            print(f"\n{name}: {out.prompt_tokens}/{out.completion_tokens} tok, "
                  f"${out.total_cost}, {out.latency_ms} ms")
            print(out.text[:400], "...")
        except Exception as exc:
            RESULTS["solo"][name] = {"error": str(exc)[:200]}
            print(f"{name} FAILED: {exc}")


def _save() -> None:
    OUT.write_text(json.dumps(RESULTS, indent=2))


def main() -> int:
    parts = sys.argv[1:] or ["alignment", "batching", "solo"]
    if "alignment" in parts:
        part_alignment()
        _save()
    if "batching" in parts:
        part_batching()
        _save()
    if "solo" in parts:
        part_solo()
        _save()
    print(f"\nresults written: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
