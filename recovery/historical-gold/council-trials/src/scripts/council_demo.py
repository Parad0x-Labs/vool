"""Executable demo: VOOL Agentic Council (experimental lane).

Council under test — exactly the shape from the brief:

    Ox Alpha      challenger   local
    DeepSeek V4   investigator local
    Ling          voter        free_cloud
    GPT-5.6 Sol   high judge   paid

Policy: "use free lanes first; invoke the paid judge only if a factual/causal
dispute survives." Run A proves the cheap path never calls Sol; Run B proves a
surviving factual dispute escalates exactly once and commits one answer.

Run:  python scripts/council_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.council import (  # noqa: E402
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

AGREE_TEXT = (
    "The scanned subtotal is 10.75 EUR because the three items sum to it. "
    "With the 8% checkout fee of 0.86 EUR, the total is 11.61 EUR."
)
DISPUTE_TEXT = (
    "The scanned subtotal is 10.75 EUR. "
    "Applying the 10% checkout fee of 1.08 EUR, the total is 11.83 EUR."
)

captured: list[tuple[str, str, str]] = []  # (seat, phase, prompt)


def scripted_model(seat_id: str, r1_text: str, ballot: str = "", challenges: str = "", verdicts: str = ""):
    def respond(capsule: ContextCapsule) -> str:
        captured.append((seat_id, capsule.phase, capsule.render_prompt()))
        if capsule.phase == "R1_BLIND":
            return r1_text
        if capsule.phase == "BALLOT":
            return ballot
        if capsule.phase == "CHALLENGE":
            return challenges
        if verdicts:
            return verdicts
        # Derive the judge's verdicts from the brief it actually received:
        # every factual/causal dispute presented is upheld on side A with the
        # kernel receipt as evidence.
        lines = []
        for m in capsule.materials:
            if m.kind == "dispute_brief" and "KIND: factual" in m.body or (
                m.kind == "dispute_brief" and "KIND: causal" in m.body
            ):
                lines.append(f"DISPUTE {m.ref}: UPHELD_A evidence=receipt:basket.scan#r-1")
        return "\n".join(lines)
    return respond


def build_council():
    spec = CouncilSpec(
        name="basket-council",
        seats=(
            SeatSpec("ox-alpha", "Ox Alpha", Role.CHALLENGER, Tier.LOCAL),
            SeatSpec("deepseek-v4", "DeepSeek V4", Role.ADVISOR, Tier.LOCAL),
            SeatSpec("ling", "Ling", Role.VOTER, Tier.FREE_CLOUD),
            SeatSpec("sol", "GPT-5.6 Sol", Role.JUDGE, Tier.PAID),
        ),
        final_judge_seat_id="sol",
        policy=EscalationPolicy(escalate_kinds=frozenset({"factual", "causal", "value_mismatch"}), paid_budget=1),
    )
    task = TaskInput(
        task_text="Total cost of the scanned basket with the checkout fee?",
        kernel_facts=(KernelFact("kf-1", "The scanned subtotal is 10.75 EUR", "tool:basket.scan#r-1"),),
        evidence_refs=("receipt:basket.scan#r-1",),
    )
    models = {
        "ox-alpha": scripted_model(
            "ox-alpha", AGREE_TEXT,
            challenges="CHALLENGE: {d} supports_a receipt:basket.scan#r-1",
        ),
        "deepseek-v4": scripted_model("deepseek-v4", AGREE_TEXT),
        "ling": scripted_model("ling", AGREE_TEXT, ballot="BALLOT: cand-1"),
        "sol": scripted_model(
            "sol", "",
            verdicts="",  # filled per run below
        ),
    }
    return spec, task, models


def show(title: str) -> None:
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")


def main() -> int:
    # -- Run A: consensus on free lanes --------------------------------------
    spec, task, models = build_council()
    captured.clear()
    rt = CouncilRuntime(spec, task, models)
    receipt_a = rt.run()
    show("RUN A — full agreement on free lanes")
    print(f"status       : {receipt_a.committed_status}")
    print(f"paid calls   : {receipt_a.paid_calls}")
    print(f"disputes     : {len(receipt_a.disputes) + len(receipt_a.gaps)}")
    print(f"final sha256 : {receipt_a.final_sha256}")
    print(f"answer       : {receipt_a.final_answer}")
    assert receipt_a.paid_calls == 0, "paid judge must NOT be invoked without surviving disputes"

    # -- Run B: factual dispute survives -> one paid adjudication ------------
    spec_b, task_b, models_b = build_council()
    # Ox Alpha now disagrees (10% fee); everyone else holds 8%.
    models_b["ox-alpha"] = scripted_model(
        "ox-alpha", DISPUTE_TEXT,
        challenges="CHALLENGE: d0 supports_b receipt:fee.schedule#r-9",
    )
    captured.clear()
    rt_b = CouncilRuntime(spec_b, task_b, models_b)
    receipt_b = rt_b.run()
    show("RUN B — factual dispute survived; paid judge escalated ONCE")
    print(f"status       : {receipt_b.committed_status}")
    print(f"paid calls   : {receipt_b.paid_calls}")
    print(f"disputes     : {[(d.dispute_id, d.kind) for d in receipt_b.disputes + receipt_b.gaps]}")
    print(f"verdicts     : {[(v.dispute_id, v.resolution) for v in receipt_b.verdicts]}")
    print(f"unresolved   : {receipt_b.unresolved}")
    print(f"final sha256 : {receipt_b.final_sha256}")
    print(f"answer       : {receipt_b.final_answer}")
    assert receipt_b.paid_calls == 1, f"expected exactly one paid call, got {receipt_b.paid_calls}"
    assert "10.75 EUR" in receipt_b.final_answer, "kernel fact must survive byte-exact"
    assert receipt_b.disputes or receipt_b.gaps, "disagreement must be preserved in the receipt"

    show("ISOLATION PROOF — what each seat actually saw")
    for seat, phase, prompt in captured:
        head = prompt.splitlines()[0]
        peer_leak = any(other in prompt for other in ("Ox Alpha", "DeepSeek V4", "cand-")) if phase == "R1_BLIND" else False
        print(f"{seat:<12} {phase:<12} leak={peer_leak}  ({head})")

    print("\nDEMO PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
