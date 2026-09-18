"""What the council decided, and whose contributions carried it — from the record only.

"Council completed" tells an operator nothing: not what was agreed, not when, not who
proposed the thing that won, not what is still disputed. The obvious way to fill that gap
is also the wrong one — ask the models who did best, or rank them by how much prose each
produced. Both invent a number and dress it as a finding.

Everything here is DERIVED, from the structured events the council already writes:

* ``candidate_set`` records which SEAT's diagnosis became the candidate, and when;
* ``candidate_rejected`` records which seat's receipt-backed counterexample killed one;
* ``seat_report`` records each seat's verdict, its counterexample, and — for attribution —
  the model that actually ran that turn;
* ``tally`` records when agreement was first reached and how strong it was;
* the terminal event records what was committed.

No prose is measured, no similarity is computed, no model is asked to rank itself, and no
ranking is emitted without the counts it came from.

**Durable by construction.** Every function here reads the append-only ledger and nothing
else — not the run state, not process memory. A reload rebuilds the identical card because
the card was never anywhere but in the evidence.
"""

from __future__ import annotations

from typing import Any

from core.council.run_store import CouncilRunStore

#: Ledger events that end a run, newest wins. `run_failed` is legacy (pre-C4).
_TERMINAL_EVENTS = (
    "converged", "no_convergence", "stopped", "crashed", "needs_attention", "run_failed",
)

_INSUFFICIENT = "insufficient evidence to name a most-adopted contributor"


def _sum_usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Measured spend summed over the given seat_report rows, honestly bounded.

    Totals cover ONLY the reports whose ledger rows carry a usage block. Any landed
    report without one — or a bench that produced none at all — flips ``complete`` to
    false and the note says the totals are a lower bound. ``usd_actual`` is summed only
    over providers that reported a number; None means no provider named one, and this
    module never estimates a price the evidence does not carry.
    """
    total = {
        "prompt_tokens": 0,
        "output_tokens": 0,
        "usd_actual": None,
        "complete": bool(rows),
        "measured_reports": 0,
    }
    landed = 0
    failed = 0
    for row in rows:
        if str(row.get("status") or "") != "landed" or row.get("superseded"):
            if str(row.get("status") or "") == "failed" and not row.get("superseded"):
                failed += 1
            continue
        landed += 1
        usage = row.get("usage")
        if not isinstance(usage, dict):
            total["complete"] = False
            continue
        total["measured_reports"] += 1
        total["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        total["output_tokens"] += int(usage.get("output_tokens") or 0)
        usd = usage.get("usd_actual")
        if isinstance(usd, (int, float)) and usd >= 0:
            total["usd_actual"] = (total["usd_actual"] or 0.0) + float(usd)
        if usage.get("complete") is False:
            total["complete"] = False
    if landed == 0:
        total["complete"] = False
    if failed:
        # A seat that died mid-turn may have spent tokens nobody recorded (dispatch
        # raises before any usage survives). Pretending the books are complete would
        # turn every crash into a clean bill — so a failed seat bounds the total too.
        total["complete"] = False
    total["note"] = (
        "measured from the run's provider-reported usage"
        if total["complete"]
        else "lower bound — some seat turns reported no provider usage, so the real total is at least this"
    )
    return total


def _seats_from(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The bench, with each seat's CURRENT model — the convened model, then every
    replacement applied in order. Read from the ledger so a deleted state file changes
    nothing."""
    seats: list[dict[str, Any]] = []
    for row in events:
        if row.get("type") == "convened":
            for seat in row.get("seats") or []:
                seats.append({
                    "seat_id": str(seat.get("seat_id") or ""),
                    "role_id": str(seat.get("role_id") or ""),
                    "model": str(seat.get("model") or ""),
                    "votes": seat.get("votes") is not False,
                    "active": True,
                })
            break
    index = {s["seat_id"]: s for s in seats}
    for row in events:
        kind = row.get("type")
        seat = index.get(str(row.get("seat_id") or ""))
        if seat is None:
            continue
        if kind == "seat_model_replaced":
            seat["model"] = str(row.get("model_to") or seat["model"])
        elif kind == "seat_disabled":
            seat["active"] = False
    return seats


def _claims_from(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every candidate a seat put forward: one per ``candidate_set``, each with a DURABLE id.

    This is the ONLY thing counted as a proposal. A seat that wrote at length without ever
    producing a diagnosis the orchestrator could cut a candidate from proposed nothing, and
    saying otherwise would be measuring prose.

    Identity is the ledger row's own sequence (``clm:<seq>``), so the same ledger rebuilds
    the same ids forever and two claims never share one — even when two seats propose the
    IDENTICAL wording. Text is a claim's content, never its identity: verdicts bind to the
    claim that was under adjudication, not to every string that happens to match it.
    """
    claims = []
    for row in events:
        if row.get("type") != "candidate_set":
            continue
        text = str(row.get("candidate") or "")
        if not text:
            continue
        seq = int(row.get("seq") or 0)
        claims.append({
            "claim_id": (f"clm:{seq}" if seq else f"clm:r{row.get('round_no')}:{len(claims)}"),
            "seat_id": str(row.get("from_seat") or ""),
            "round_no": int(row.get("round_no") or 0),
            "text": text,
            "seq": seq,
        })
    return claims


def _verdict_bindings(
    events: list[dict[str, Any]], claims: list[dict[str, Any]]
) -> tuple[str, set[str], dict[str, int]]:
    """Which claim was ADOPTED and which were REJECTED, bound by chain position.

    The orchestrator runs one ACTIVE candidate at a time: ``candidate_set`` puts a claim
    under adjudication, ``candidate_rejected`` kills the active one, ``converged`` commits
    it. Binding verdicts by TEXT instead let an identical wording from another seat — or
    from another round — inherit a verdict that was never about it. Returns
    (adopted_claim_id, rejected_claim_ids, refuting_seats_counterexample_counts).
    """
    by_seq = {c["seq"]: c for c in claims}
    active: dict[str, Any] | None = None
    adopted_ids: set[str] = set()
    rejected_ids: set[str] = set()
    refutations: dict[str, int] = {}
    for row in events:
        kind = row.get("type")
        if kind == "candidate_set":
            active = by_seq.get(int(row.get("seq") or 0)) or active
        elif kind == "candidate_rejected":
            if active is not None:
                rejected_ids.add(active["claim_id"])
            for entry in row.get("counterexamples") or []:
                seat_id = str((entry or {}).get("seat_id") or "")
                if seat_id:
                    refutations[seat_id] = refutations.get(seat_id, 0) + 1
            active = None
        elif kind == "converged" and active is not None:
            adopted_ids.add(active["claim_id"])
    return adopted_ids, rejected_ids, refutations


def _provenance_at(events: list[dict[str, Any]], seat_id: str, round_no: int) -> dict[str, Any]:
    """The requested AND the proven-actual model for this seat's turn in this round.

    Two separate facts from the seat's own report: ``model_requested`` is what the operator
    asked the seat to run; ``model_actual`` is what streamed evidence says answered, and is
    empty unless dispatch established it. Attribution never promotes the request to a
    provenance claim: the caller decides which fact its question needs.
    """
    requested = ""
    actual: str | None = None
    evidence = "unknown"
    for row in events:
        if row.get("type") != "seat_report":
            continue
        if str(row.get("seat_id") or "") != seat_id or int(row.get("round_no") or 0) != round_no:
            continue
        if str(row.get("model_requested") or ""):
            requested = str(row.get("model_requested"))
        actual = row.get("model_actual") or None
        if actual:
            evidence = str(row.get("model_evidence") or "unknown")
        if not row.get("superseded"):
            return {
                "model_requested": str(row.get("model_requested") or requested),
                "model_actual": (str(row.get("model_actual")) if row.get("model_actual") else None),
                "model_evidence": str(row.get("model_evidence") or "unknown"),
            }
    return {"model_requested": requested, "model_actual": actual, "model_evidence": evidence}


def scorecard_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole card, derived. Pure: same ledger in, same card out, forever."""
    seats = _seats_from(events)
    claims = _claims_from(events)
    reports = [r for r in events if r.get("type") == "seat_report"]
    attempts = [r for r in events if r.get("type") == "seat_attempt"]

    terminal_state = ""
    for row in events:
        if row.get("type") in _TERMINAL_EVENTS:
            terminal_state = str(row.get("type"))
    if terminal_state == "run_failed":
        terminal_state = "failed"

    adopted_ids, rejected_ids, refutations = _verdict_bindings(events, claims)
    adopted_claims_all = [c for c in claims if c["claim_id"] in adopted_ids]
    adopted = [c["text"] for c in adopted_claims_all]

    tallies = [r for r in events if r.get("type") == "tally"]
    converged_tally = next((t for t in tallies if t.get("converged") is True), None)
    agreement_round = int(converged_tally["round_no"]) if converged_tally else None
    agreement_strength = {
        "agree": int(converged_tally.get("agree") or 0),
        "disagree": int(converged_tally.get("disagree") or 0),
        "voting_seats": int(converged_tally.get("voting_seats") or 0),
    } if converged_tally else None

    # A claim is under adjudication from the round after it was set until another candidate
    # replaces it or it is rejected. Support is an AGREE from a DIFFERENT seat in that span.
    ordered = sorted(claims, key=lambda c: c["seq"])
    spans: dict[int, tuple[int, int]] = {}
    for index, claim in enumerate(ordered):
        end = 10**6
        for later in ordered[index + 1:]:
            end = later["round_no"]
            break
        for row in events:
            if row.get("type") == "candidate_rejected" and \
                    str(row.get("candidate") or "") == claim["text"]:
                end = min(end, int(row.get("round_no") or 0))
        spans[claim["seq"]] = (claim["round_no"], end)

    rows: list[dict[str, Any]] = []
    for seat in seats:
        seat_id = seat["seat_id"]
        mine = [c for c in claims if c["seat_id"] == seat_id]
        my_reports = [r for r in reports if str(r.get("seat_id") or "") == seat_id]
        my_attempts = [a for a in attempts if str(a.get("seat_id") or "") == seat_id]
        failed = [r for r in my_reports if str(r.get("status") or "") == "failed"]
        adopted_claims = [c for c in mine if c["claim_id"] in adopted_ids]
        refuted_claims = [c for c in mine if c["claim_id"] in rejected_ids]
        supported = 0
        for claim in mine:
            start, end = spans.get(claim["seq"], (claim["round_no"], 10**6))
            for report in reports:
                if str(report.get("seat_id") or "") == seat_id:
                    continue
                round_no = int(report.get("round_no") or 0)
                if start < round_no <= end and str(report.get("verdict") or "") == "AGREE":
                    supported += 1
        note = ""
        if failed:
            outcomes = sorted({str(r.get("outcome") or "FAILED") for r in failed})
            note = (
                f"{len(failed)} failed attempt(s) ({', '.join(outcomes)}). Reliability, not "
                "contribution: a seat that never returned proposed nothing, which is not the "
                "same as having been wrong."
            )
        rows.append({
            "seat_id": seat_id,
            "role_id": seat["role_id"],
            "model": seat["model"],
            "votes": seat["votes"],
            "active": seat["active"],
            "valid_attempts": sum(1 for a in my_attempts if str(a.get("outcome")) == "VALID"),
            "attempts": len(my_attempts),
            "retries": sum(int(r.get("retries_used") or 0) for r in my_reports),
            "usage": _sum_usage(my_reports),
            "claims_proposed": len(mine),
            "claims_adopted": len(adopted_claims),
            "claims_supported": supported,
            "claims_refuted": len(refuted_claims),
            "claims_unresolved": len(mine) - len(adopted_claims) - len(refuted_claims),
            "refutations_landed": refutations.get(seat_id, 0),
            "failures": len(failed),
            "timeouts": sum(1 for r in failed if str(r.get("outcome") or "") == "TIMED_OUT"),
            "reliability_note": note,
            "claim_attribution": [
                {"claim_id": c["claim_id"],
                 "round_no": c["round_no"],
                 "model_requested": _provenance_at(events, seat_id, c["round_no"])["model_requested"],
                 "model_actual": _provenance_at(events, seat_id, c["round_no"])["model_actual"],
                 "model_evidence": _provenance_at(events, seat_id, c["round_no"])["model_evidence"],
                 "adopted": c["claim_id"] in adopted_ids}
                for c in sorted(mine, key=lambda c: c["round_no"])
            ],
        })

    # The VISIBLE winner attribution names the model that PRODUCED each adopted claim —
    # the proven-actual model when streamed evidence established one, else the request,
    # labelled by which fact it is. Never the seat's CURRENT model: an operator may
    # replace a seat mid-run, and crediting the replacement with its predecessor's claim
    # would be a fabricated provenance.
    adopted_attribution = []
    for claim in adopted_claims_all:
        prov = _provenance_at(events, claim["seat_id"], claim["round_no"])
        adopted_attribution.append({
            "claim_id": claim["claim_id"],
            "seat_id": claim["seat_id"],
            "round_no": claim["round_no"],
            "model_requested": prov["model_requested"],
            "model_actual": prov["model_actual"],
            "model_evidence": prov["model_evidence"],
            "produced_by": prov["model_actual"] or prov["model_requested"],
            "produced_by_is_proven": bool(prov["model_actual"]),
        })

    top = max((r["claims_adopted"] for r in rows), default=0)
    winners = sorted(r["seat_id"] for r in rows if r["claims_adopted"] == top and top > 0)
    if not winners:
        most = {
            "seat_ids": [], "claims_adopted": 0, "tied": False,
            "insufficient_evidence": True, "label": _INSUFFICIENT,
            "adopted_claims": [],
            "basis": (
                "no claim proposed by any seat was adopted into a committed answer, so "
                "there is nothing to rank. 0 adopted claims across the bench."
            ),
        }
    else:
        tied = len(winners) > 1
        most = {
            "seat_ids": winners,
            "claims_adopted": top,
            "tied": tied,
            "insufficient_evidence": False,
            "label": "most adopted contributions" + (" (tied)" if tied else ""),
            "adopted_claims": adopted_attribution,
            "basis": (
                f"{top} claim(s) adopted into the committed answer, counted from the run's "
                f"candidate_set and terminal events; {len(winners)} seat(s) hold that count."
            ),
        }

    # Named only where the evidence names it: a seat whose proposals were killed by a
    # receipt-backed counterexample AND none of which was adopted. A seat that merely
    # failed is not here — it proposed nothing, which is a reliability fact, not a verdict
    # on its judgement.
    refuted_seats = sorted(r["seat_id"] for r in rows
                          if r["claims_refuted"] > 0 and r["claims_adopted"] == 0)
    substantially_refuted = {
        "seat_ids": refuted_seats,
        "claims_refuted": sum(r["claims_refuted"] for r in rows
                              if r["seat_id"] in refuted_seats),
        "label": (
            "claims refuted by receipt-backed counterexamples" if refuted_seats
            else "no seat had a claim refuted"
        ),
        "basis": (
            "counted from candidate_rejected events; reliability failures are reported "
            "separately per seat and are never counted here."
        ),
    }

    verdict_rounds = [int(r.get("round_no") or 0) for r in reports if r.get("verdict")]
    final_round = max(verdict_rounds) if verdict_rounds else 0
    by_seat = {s["seat_id"]: s for s in seats}
    disagreements = []
    for report in reports:
        if int(report.get("round_no") or 0) != final_round:
            continue
        if str(report.get("verdict") or "") != "DISAGREE":
            continue
        seat = by_seat.get(str(report.get("seat_id") or ""), {})
        disagreements.append({
            "seat_id": str(report.get("seat_id") or ""),
            "role_id": str(seat.get("role_id") or ""),
            "model": str(seat.get("model") or ""),
            "round_no": final_round,
            "verdict": "DISAGREE",
            "counterexample": str(report.get("counterexample") or ""),
            "receipt_backed": report.get("counterexample_backed") is True,
        })

    provisional = terminal_state == "needs_attention"
    return {
        "run": {
            "terminal_state": terminal_state,
            "seats_used": [r["seat_id"] for r in rows],
            "models_used": list(dict.fromkeys(r["model"] for r in rows if r["model"])),
            "agreement_first_reached_round": agreement_round,
            "agreement_strength": agreement_strength,
            "usage": _sum_usage(reports),
            "adopted_conclusions": adopted,
            "remaining_disagreements": disagreements,
            "most_adopted": most,
            "substantially_refuted": substantially_refuted,
            "provisional": provisional,
            "provisional_note": (
                "this council is paused and has not committed an answer, so the ranking "
                "below is not final" if provisional else ""
            ),
        },
        "seats": rows,
    }


def build_scorecard(run_id: str) -> dict[str, Any]:
    """The card for one run, rebuilt from its ledger. No state file is read."""
    return scorecard_from_events(CouncilRunStore(run_id).read_events_ordered())


def persist_scorecard(run_id: str) -> bool:
    """Write the derived card into the run's own ledger, once. True when it wrote.

    The record is durable evidence of what the run concluded; the READ path recomputes
    from the events regardless, so this row can never become the only copy of the truth —
    or drift from it.
    """
    try:
        store = CouncilRunStore(run_id)
    except ValueError:
        return False
    events = store.read_events_ordered()
    if not events or any(row.get("type") == "scorecard" for row in events):
        return False
    card = scorecard_from_events(events)
    if not card["run"]["terminal_state"] or card["run"]["provisional"]:
        return False
    store.append_event("scorecard", **card)
    return True
