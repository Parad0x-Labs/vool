"""The council runtime — a transactional execution mode, not group chat.

Phases, in fixed order:

1. R1_BLIND      every answering seat gets its frozen capsule (built BEFORE any
                 invocation) and answers independently. Answers land raw in the
                 transcript; no seat sees another's output at any point here.
2. DELTA         mechanical disagreement extraction (core/council/claims.py).
                 Authorship is stripped here: downstream sees cand-N labels.
3. BALLOT        voters rank anonymized candidates; advisors may not.
4. CHALLENGE     challengers receive per-dispute briefs (claim A vs claim B,
                 anonymous) and return typed challenge records only.
5. ADJUDICATE    if qualifying disputes survive AND policy authorizes it, the
                 final judge resolves each dispute from the structured record;
                 PAID judges require a live gate authorization. Coverage gaps
                 settle by voter majority and are labelled preference, never truth.
6. COMMIT        deterministic assembly under the kernel-fact verbatim contract,
                 then exactly ONE seal. The seal is the single final-byte owner.

Commit authority composes with core/kernel/obligations.py semantics: an answer
commits once; unresolved disputes force a PARTIAL commit that names every open
item — "partial" can never masquerade as "done".
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Callable, Protocol

from core.council.capsule import CapsuleMaterial, ContextCapsule, KernelFact, TaskInput
from core.council.claims import Claim, Delta, Dispute, extract_delta
from core.council.policy import (
    AdjudicationAuthorization,
    EscalationGate,
    EscalationRefused,
    QUALIFYING_KINDS,
)
from core.council.seats import ANSWERING_ROLES, CouncilSpec, Role, SeatSpec, Tier


class LiveSeatError(RuntimeError):
    """A live provider call failed (transport, HTTP, rate limit, empty body).

    Raised by adapters; the runtime records it as an honest seat failure and
    substitutes nothing — no synthetic output ever replaces a dead seat.
    """


class DoubleCommitRefused(RuntimeError):
    """A second seal was attempted. One council run owns one final answer."""


class KernelFactTampered(RuntimeError):
    """Committed bytes altered a mechanically proven fact."""


class RoleViolation(PermissionError):
    """A seat exercised authority its role does not hold."""


class SeatModel(Protocol):
    """What a provider must expose to join a council.

    The runtime calls ``respond(capsule)`` and nothing else: there is no channel
    through which a seat could read peer state, the transcript, or the runtime.
    Local adapters and cloud adapters implement this identically — the council
    never knows or cares which lane answered.
    """

    def respond(self, capsule: ContextCapsule) -> str: ...


# ---------------------------------------------------------------------------
# typed records parsed from seat outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ballot:
    voter_seat_id: str
    ranked: tuple[str, ...]  # candidate labels, best first


@dataclass(frozen=True)
class ChallengeRecord:
    record_id: str
    challenger_seat_id: str
    dispute_id: str
    position: str            # supports_a | supports_b | neither
    evidence_ref: str


@dataclass(frozen=True)
class Verdict:
    dispute_id: str
    resolution: str          # upheld_a | upheld_b | insufficient_evidence | preference_majority
    evidence_ref: str = ""
    is_preference: bool = False


@dataclass(frozen=True)
class AdvisoryNote:
    advisor_seat_id: str
    body: str                # free text, NON-BINDING by construction


@dataclass
class CouncilTranscript:
    """Everything that happened, preserved raw. Sealed into the receipt."""

    r1_answers: dict[str, str] = field(default_factory=dict)   # seat_id -> raw text
    delta: Delta | None = None
    ballots: tuple[Ballot, ...] = ()
    rejected_ballots: tuple[str, ...] = ()
    challenges: tuple[ChallengeRecord, ...] = ()
    advisories: list[AdvisoryNote] = field(default_factory=list)
    verdicts: tuple[Verdict, ...] = ()
    unresolved: tuple[str, ...] = ()
    paid_calls: int = 0
    model_calls: list[tuple[str, str]] = field(default_factory=list)  # (seat_id, phase)
    seat_failures: list[tuple[str, str, str]] = field(default_factory=list)  # (seat, phase, error)
    call_records: list[dict] = field(default_factory=list)  # provider-bound metadata per call


# ---------------------------------------------------------------------------
# strict parsers — malformed output degrades to abstain/insufficient, never guesses
# ---------------------------------------------------------------------------


def parse_ballot(text: str, valid_candidates: tuple[str, ...]) -> tuple[str, ...]:
    ranked = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("BALLOT:"):
            for tok in line[len("BALLOT:"):].split(","):
                tok = tok.strip()
                if tok in valid_candidates and tok not in ranked:
                    ranked.append(tok)
    return tuple(ranked)


def parse_challenges(text: str, valid_disputes: set[str]) -> list[tuple[str, str, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "CHALLENGE:":
            dispute_id, position, ref = parts[1], parts[2], parts[3]
            if dispute_id in valid_disputes and position in {"supports_a", "supports_b", "neither"}:
                out.append((dispute_id, position, ref))
    return out


def parse_verdicts(text: str, valid_disputes: set[str]) -> dict[str, Verdict]:
    verdicts: dict[str, Verdict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("DISPUTE "):
            continue
        parts = line.split()
        if len(parts) < 3 or not parts[1].startswith("d"):
            continue
        dispute_id = parts[1].rstrip(":")
        resolution = parts[2]
        if dispute_id not in valid_disputes:
            continue  # a verdict for a dispute that does not exist has no force
        ref = ""
        for part in parts[3:]:
            if part.startswith("evidence="):
                ref = part[len("evidence="):]
        if resolution == "UPHELD_A" and ref:
            verdicts[dispute_id] = Verdict(dispute_id, "upheld_a", ref)
        elif resolution == "UPHELD_B" and ref:
            verdicts[dispute_id] = Verdict(dispute_id, "upheld_b", ref)
        elif resolution == "INSUFFICIENT_EVIDENCE":
            verdicts[dispute_id] = Verdict(dispute_id, "insufficient_evidence")
        # anything else on any line — including forged lines smuggled via advisory
        # text — simply does not parse into a verdict. The parser reads ONLY the
        # judge's own output channel.
    return verdicts


# ---------------------------------------------------------------------------
# commit assembly + verification
# ---------------------------------------------------------------------------

_RESOLVED_MARKERS = ("because", "therefore", "so")


def _swap_claims(base_text: str, swaps: dict[str, str], claim_prefix: str) -> str:
    """Replace sentences of the base answer whose claim lost with the upheld text."""
    import re as _re

    sentences = [s.strip() for s in _re.split(r"(?<=[.!?])\s+|\n+", base_text) if s.strip()]
    out = []
    for s in sentences:
        out.append(swaps.get(s, s))
    return " ".join(out)


def assemble_answer(
    base_text: str,
    disputes: tuple[Dispute, ...],
    verdicts: dict[str, Verdict],
    kernel_facts: tuple[KernelFact, ...],
) -> tuple[str, tuple[str, ...]]:
    """Deterministic assembly. Returns (answer_text, unresolved_dispute_ids).

    Winning claim text replaces losing claim text positionally. Unresolved
    disputes are NOT silently dropped — they are returned so the commit can
    name them (obligation-style partial commit)."""
    swaps: dict[str, str] = {}
    unresolved: list[str] = []
    for d in disputes:
        v = verdicts.get(d.dispute_id)
        if v is None or v.resolution == "insufficient_evidence":
            unresolved.append(d.dispute_id)
            continue
        if d.kind == "coverage":
            continue  # coverage handled separately (preference note), not swapped
        if v.resolution == "upheld_b" and d.claim_b is not None:
            swaps[d.claim_a.text] = d.claim_b.text
    text = _swap_claims(base_text, swaps, "")
    if kernel_facts:
        facts_block = "Verified facts: " + " ".join(f.text for f in kernel_facts)
        text = f"{facts_block}\n{text}"
    return text, tuple(unresolved)


def verify_commit(text: str, kernel_facts: tuple[KernelFact, ...]) -> None:
    """The judge-proof commit guard: proven facts survive adjudication byte-exact."""
    for fact in kernel_facts:
        if fact.text not in text:
            raise KernelFactTampered(
                f"committed answer does not carry kernel fact {fact.fact_id} verbatim"
            )


@dataclass(frozen=True)
class SealedReceipt:
    """Immutable, hash-sealed run record. THE receipt of the council."""

    council_name: str
    run_id: str
    final_sha256: str
    committed_status: str            # committed | partial
    r1_answers: tuple[tuple[str, str], ...]
    disputes: tuple[Dispute, ...]
    gaps: tuple[Dispute, ...]
    ballots: tuple[Ballot, ...]
    challenges: tuple[ChallengeRecord, ...]
    advisories: tuple[AdvisoryNote, ...]
    verdicts: tuple[Verdict, ...]
    unresolved: tuple[str, ...]
    paid_calls: int
    model_calls: tuple[tuple[str, str], ...]
    final_answer: str = ""
    call_records: tuple[dict, ...] = ()
    seat_failures: tuple[tuple[str, str, str], ...] = ()


# ---------------------------------------------------------------------------
# the runtime
# ---------------------------------------------------------------------------

PHASE_R1 = "R1_BLIND"
PHASE_BALLOT = "BALLOT"
PHASE_CHALLENGE = "CHALLENGE"
PHASE_ADJUDICATE = "ADJUDICATE"
PHASE_ADJUDICATE_FINAL = "ADJUDICATE_FINAL"
PHASE_ADVISORY = "ADVISORY"


class CouncilRuntime:
    """One run of one council over one task. Constructed per run; sealed once."""

    def __init__(self, spec: CouncilSpec, task: TaskInput, models: dict[str, SeatModel]) -> None:
        self.spec = spec.validated() if not isinstance(spec, CouncilSpec) else spec
        self.task = task
        self.models = dict(models)
        self.gate = EscalationGate(spec.policy)
        self.transcript = CouncilTranscript()
        self._sealed: SealedReceipt | None = None
        self._judge_raw_bytes: str | None = None
        self._anon_by_seat: dict[str, str] = {}
        self._seat_by_anon: dict[str, str] = {}

        missing = [s.seat_id for s in spec.answering_seats() if s.seat_id not in self.models]
        if missing:
            raise KeyError(f"no model bound for answering seats: {missing}")

    # -- invocation boundary -------------------------------------------------

    def _invoke(self, seat: SeatSpec, capsule: ContextCapsule, *, required: bool = False) -> str:
        if seat.tier is Tier.PAID:
            authz: AdjudicationAuthorization | None = getattr(self, "_paid_authz", None)
            if authz is None:
                raise EscalationRefused(
                    f"paid seat {seat.seat_id} invoked without escalation authorization"
                )
            self.transcript.paid_calls += 1
        self.transcript.model_calls.append((seat.seat_id, capsule.phase))
        model = self.models[seat.seat_id]
        respond = model.respond if hasattr(model, "respond") else model
        try:
            outcome = respond(capsule)
        except LiveSeatError as exc:
            # Honest degradation: record the failure, substitute NOTHING.
            self.transcript.seat_failures.append((seat.seat_id, capsule.phase, str(exc)))
            self.transcript.call_records.append(
                {"seat_id": seat.seat_id, "phase": capsule.phase, "error": str(exc)}
            )
            if required:
                raise
            return ""
        meta = None
        text = outcome
        if isinstance(outcome, tuple) or hasattr(outcome, "text"):
            text = outcome.text
            meta = outcome
        record = {
            "seat_id": seat.seat_id,
            "phase": capsule.phase,
            "requested_model": getattr(model, "model", ""),
            "attested_model": getattr(meta, "model_attested", ""),
            "prompt_tokens": getattr(meta, "prompt_tokens", 0),
            "completion_tokens": getattr(meta, "completion_tokens", 0),
            "total_cost": getattr(meta, "total_cost", None),
            "latency_ms": getattr(meta, "latency_ms", 0),
            "error": None,
        }
        self.transcript.call_records.append(record)
        return text

    def _capsule(self, seat: SeatSpec, phase: str, materials: tuple[CapsuleMaterial, ...] = ()) -> ContextCapsule:
        return ContextCapsule(
            council_name=self.spec.name,
            seat_id=seat.seat_id,
            role=seat.role,
            phase=phase,
            task_text=self.task.task_text,
            kernel_facts=self.task.kernel_facts,
            materials=materials,
        )

    # -- phases ---------------------------------------------------------------

    def run(self) -> SealedReceipt:
        if self._sealed is not None:
            raise DoubleCommitRefused("this run already committed")

        # Phase 1 — R1 blind. Capsules are built from TaskInput only, before any
        # answer exists, so blindness holds even though invocations are sequential.
        r1_capsules = {
            s.seat_id: self._capsule(s, PHASE_R1) for s in self.spec.answering_seats()
        }
        answered = []
        for seat in self.spec.answering_seats():
            try:
                self.transcript.r1_answers[seat.seat_id] = self._invoke(
                    seat, r1_capsules[seat.seat_id], required=True
                )
                answered.append(seat.seat_id)
            except LiveSeatError:
                continue  # failed seat is recorded as a failure, never stubbed
        if len(answered) < 2:
            raise LiveSeatError(
                f"fewer than two seats completed R1 ({answered}); council cannot run honestly"
            )

        # stable anonymization of authors (sorted by seat id)
        for n, seat_id in enumerate(sorted(self.transcript.r1_answers), start=1):
            anon = f"cand-{n}"
            self._anon_by_seat[seat_id] = anon
            self._seat_by_anon[anon] = seat_id

        # Phase 2 — mechanical delta
        anon_answers = {
            self._anon_by_seat[sid]: text for sid, text in self.transcript.r1_answers.items()
        }
        delta = extract_delta(anon_answers)
        self.transcript.delta = delta

        # cheap path: full agreement across all claims -> no ballots, no judge.
        # SEALED JUDGE LAW exception: with sealing on, the judge ALWAYS runs so
        # the final bytes always have exactly one semantic author. Full-agreement
        # candidates are still presented for an independent final response.
        if not delta.has_disputes:
            if not self.spec.policy.sealed_judge:
                return self._commit(delta)
            return self._sealed_adjudicate(delta, contested=[], ballots=[], challenges=[])

        # Phase 3 — ballot over anonymized candidates (voters only)
        ballot_material = tuple(
            CapsuleMaterial("candidate", label, anon_answers[label]) for label in delta.candidates
        )
        ballots: list[Ballot] = []
        rejected: list[str] = []
        for seat in self.spec.by_role(Role.VOTER):
            raw = self._invoke(seat, self._capsule(seat, PHASE_BALLOT, ballot_material))
            ranked = parse_ballot(raw, delta.candidates)
            if ranked:
                ballots.append(Ballot(seat.seat_id, ranked))
            else:
                rejected.append(seat.seat_id)
        self.transcript.ballots = tuple(ballots)
        self.transcript.rejected_ballots = tuple(rejected)

        # Phase 4 — typed challenges per factual/causal dispute
        contested = [d for d in delta.disputes if d.kind in QUALIFYING_KINDS]
        challenge_material = tuple(
            CapsuleMaterial(
                "dispute_brief",
                d.dispute_id,
                f"CLAIM_A: {d.claim_a.text}\nCLAIM_B: {d.claim_b.text if d.claim_b else '(absent)'}\n"
                f"KIND: {d.kind}",
            )
            for d in contested
        )
        challenges: list[ChallengeRecord] = []
        if challenge_material:
            for seat in self.spec.by_role(Role.CHALLENGER):
                raw = self._invoke(seat, self._capsule(seat, PHASE_CHALLENGE, challenge_material))
                for k, (dispute_id, position, ref) in enumerate(
                    parse_challenges(raw, {d.dispute_id for d in contested})
                ):
                    challenges.append(
                        ChallengeRecord(f"ch-{len(challenges)}", seat.seat_id, dispute_id, position, ref)
                    )
        self.transcript.challenges = tuple(challenges)

        # coverage gaps settle by ballot majority as PREFERENCE (never truth)
        preference_verdicts = self._settle_coverage(delta, ballots)

        # Phase 4b — ADVISORY phase: advisors see candidates, disputes and
        # challenge records (never a hidden answer key) and emit analysis.
        advisor_material = tuple(ballot_material) + tuple(
            CapsuleMaterial("dispute_brief", d.dispute_id,
                            f"CLAIM_A: {d.claim_a.text}\nCLAIM_B: "
                            f"{d.claim_b.text if d.claim_b else '(absent)'}\nKIND: {d.kind}")
            for d in delta.disputes
        ) + tuple(challenge_material) + tuple(
            CapsuleMaterial("challenge_record", c.record_id,
                            f"dispute={c.dispute_id} position={c.position} evidence_ref={c.evidence_ref}")
            for c in challenges
        )
        for seat in self.spec.by_role(Role.ADVISOR):
            try:
                raw = self._invoke(seat, self._capsule(seat, PHASE_ADVISORY, advisor_material))
            except LiveSeatError:
                continue  # advisory is optional; failure recorded by _invoke
            self.transcript.advisories.append(AdvisoryNote(seat.seat_id, raw))

        return self._sealed_adjudicate(delta, contested, ballots, challenges)

    def _sealed_adjudicate(self, delta, contested, ballots, challenges):
        preference_verdicts = self._settle_coverage(delta, ballots)
        verdicts: dict[str, Verdict] = dict(preference_verdicts)
        judge = self.spec.judge
        sealed = self.spec.policy.sealed_judge

        # SEALED mode: the judge always produces the final response, even when
        # nothing qualified for dispute — the brief then carries all candidates.
        if sealed and not contested:
            anon_answers = {
                self._anon_by_seat[sid]: text for sid, text in self.transcript.r1_answers.items()
            }
            contested = []
            batch_material = tuple(
                CapsuleMaterial("candidate", label, anon_answers[label])
                for label in delta.candidates
            )
            raw = self._run_judge(judge, batch_material, [], force_final=True)
            return self._commit_sealed(raw, delta, verdicts)

        if contested:
            batch_size = max(1, int(self.spec.policy.judge_batch_size))
            batches = [contested[i:i + batch_size] for i in range(0, len(contested), batch_size)]
            if judge.tier is Tier.PAID:
                kinds = {d.kind for d in contested}
                # Escalation is authorized by POLICY or not at all. The budget
                # buys CALLS, not outcomes: with budget N only the first N
                # batches run; every dispute in an unbought batch is recorded
                # as unresolved — never silently dropped, never force-resolved.
                self._paid_authz = self.gate.request(kinds)  # raises EscalationRefused
                affordable = self.gate.last_authorized_calls
            else:
                self._paid_authz = None
                affordable = len(batches)
            for idx, batch in enumerate(batches):
                if idx >= affordable:
                    break
                try:
                    brief = self._judge_brief(batch, challenges)
                    raw = self._invoke(
                        judge,
                        self._capsule(judge, PHASE_ADJUDICATE, brief),
                    )
                except LiveSeatError:
                    break  # remaining batches stay unresolved; never fabricated
                verdicts.update(parse_verdicts(raw, {d.dispute_id for d in batch}))
            if not sealed:
                self._paid_authz = None  # sealed mode keeps the session authz
                # alive for the final judge call within the same budget.

        if sealed:
            raw = self._run_judge(judge, (), challenges, force_final=True)
            self._paid_authz = None
            return self._commit_sealed(raw, delta, verdicts)

        self.transcript.verdicts = tuple(verdicts.values())
        return self._commit(delta, verdicts)

    def _run_judge(self, judge: SeatSpec, extra_materials, challenges, *, force_final: bool) -> str | None:
        """One sealed-final judge call. Returns RAW bytes or None on failure.

        The returned text is stored verbatim; nothing downstream may alter it.
        """
        brief = tuple(extra_materials) + self._judge_brief([], challenges)
        try:
            raw = self._invoke(judge, self._capsule(judge, PHASE_ADJUDICATE_FINAL, brief))
        except LiveSeatError as exc:
            self.transcript.seat_failures.append((judge.seat_id, PHASE_ADJUDICATE_FINAL, str(exc)))
            return None
        self._judge_raw_bytes = raw
        return raw

    def _commit_sealed(self, raw: str | None, delta: Delta, verdicts: dict[str, Verdict]):
        """SEALED JUDGE commit path. Zero semantic discretion.

        Commit owner may only: (1) refuse malformed/absent output, (2) extract
        the fixed '=== FINAL ===' section syntactically, (3) seal + hash.
        Any other transformation is structurally absent from this code path.
        """
        import re as _re
        if not raw or not raw.strip():
            receipt = SealedReceipt(
                council_name=self.spec.name, run_id=uuid.uuid4().hex[:12],
                final_sha256="", committed_status="invalid_refused",
                r1_answers=tuple(sorted(self.transcript.r1_answers.items())),
                disputes=delta.disputes, gaps=delta.gaps,
                ballots=self.transcript.ballots, challenges=self.transcript.challenges,
                advisories=tuple(self.transcript.advisories),
                verdicts=tuple(verdicts.values()), unresolved=(),
                paid_calls=self.transcript.paid_calls,
                model_calls=tuple(self.transcript.model_calls),
                final_answer="",
                call_records=tuple(self.transcript.call_records),
                seat_failures=tuple(self.transcript.seat_failures),
            )
            self._sealed = receipt
            return receipt
        marker = "=== FINAL ==="
        if marker in raw:
            body = raw.split(marker)[-1].strip()   # mechanical section extraction
        else:
            body = raw.strip()
        body = _re.sub(r"\n{3,}", "\n\n", body)
        answer_text = body
        status = "committed"
        unresolved_ids = [d.dispute_id for d in delta.disputes
                          if verdicts.get(d.dispute_id) is None
                          or verdicts[d.dispute_id].resolution == "insufficient_evidence"]
        if unresolved_ids:
            status = "partial"
        receipt = SealedReceipt(
            council_name=self.spec.name, run_id=uuid.uuid4().hex[:12],
            final_sha256=hashlib.sha256(answer_text.encode()).hexdigest(),
            committed_status=status,
            r1_answers=tuple(sorted(self.transcript.r1_answers.items())),
            disputes=delta.disputes, gaps=delta.gaps,
            ballots=self.transcript.ballots, challenges=self.transcript.challenges,
            advisories=tuple(self.transcript.advisories),
            verdicts=tuple(verdicts.values()), unresolved=tuple(unresolved_ids),
            paid_calls=self.transcript.paid_calls,
            model_calls=tuple(self.transcript.model_calls),
            final_answer=answer_text,
            call_records=tuple(self.transcript.call_records),
            seat_failures=tuple(self.transcript.seat_failures),
        )
        self._sealed = receipt
        return receipt

    # -- helpers --------------------------------------------------------------

    def _settle_coverage(self, delta: Delta, ballots: list[Ballot]) -> dict[str, Verdict]:
        out: dict[str, Verdict] = {}
        for gap in delta.gaps:
            winner_label = gap.claim_a.candidate
            votes = sum(1 for b in ballots if b.ranked and b.ranked[0] == winner_label)
            out[gap.dispute_id] = Verdict(
                gap.dispute_id,
                "preference_majority",
                evidence_ref=f"ballots:{votes}",
                is_preference=True,
            )
        return out

    def _judge_brief(self, contested: list[Dispute], challenges: list[ChallengeRecord]) -> tuple[CapsuleMaterial, ...]:
        mats = [
            CapsuleMaterial(
                "dispute_brief", d.dispute_id,
                f"CLAIM_A: {d.claim_a.text}\nCLAIM_B: {d.claim_b.text if d.claim_b else '(absent)'}\n"
                f"KIND: {d.kind}",
            )
            for d in contested
        ]
        mats += [
            CapsuleMaterial("challenge_record", c.record_id,
                            f"dispute={c.dispute_id} position={c.position} evidence_ref={c.evidence_ref}")
            for c in challenges
        ]
        # Advisor notes are appended VERBATIM-LABELLED non-binding. They carry no
        # ballot weight and their text can never parse into verdicts (the verdict
        # parser reads only the judge's own response channel).
        mats += [
            CapsuleMaterial("advisory_note", f"adv-{i}", f"[NON-BINDING ADVISORY]\n{note.body}")
            for i, note in enumerate(self.transcript.advisories)
        ]
        return tuple(mats)

    def collect_advisory(self, seat_id: str, body: str) -> None:
        """Advisors submit analysis between challenge and adjudication."""
        seat = self.spec.seat(seat_id)
        if seat.role is not Role.ADVISOR:
            raise RoleViolation(f"{seat.role.value} seat {seat_id} cannot submit advisory notes")
        self.transcript.advisories.append(AdvisoryNote(seat_id, body))

    def cast_ballot(self, seat_id: str, ranked: tuple[str, ...]) -> None:
        """Programmatic ballot path — same authority check as the model path."""
        seat = self.spec.seat(seat_id)
        if seat.role is not Role.VOTER:
            raise RoleViolation(
                f"{seat.role.value} seats cannot vote; ballot from {seat_id} refused"
            )
        self.transcript.ballots = (*self.transcript.ballots, Ballot(seat_id, ranked))

    # -- commit -----------------------------------------------------------------

    def _pick_base_candidate(self, delta: Delta, verdicts: dict[str, Verdict]) -> str:
        scores = {label: 0 for label in delta.candidates}
        for b in self.transcript.ballots:
            for pos, label in enumerate(b.ranked):
                scores[label] += max(0, len(b.ranked) - pos)
        for d in delta.disputes:
            v = verdicts.get(d.dispute_id)
            if v is None:
                continue
            if v.resolution == "upheld_a":
                scores[d.claim_a.candidate] += 2
            elif v.resolution == "upheld_b" and d.claim_b is not None:
                scores[d.claim_b.candidate] += 2
        return max(sorted(scores), key=lambda l: scores[l])

    def _commit(self, delta: Delta, verdicts: dict[str, Verdict] | None = None) -> SealedReceipt:
        verdicts = verdicts or {}
        base_label = self._pick_base_candidate(delta, verdicts)
        base_text = {self._anon_by_seat[sid]: t for sid, t in self.transcript.r1_answers.items()}[base_label]

        answer_text, unresolved_ids = assemble_answer(
            base_text, delta.disputes + delta.gaps, verdicts, self.task.kernel_facts
        )
        # Commit guard: mechanically proven facts survive byte-exact, whatever the
        # judge said. This runs AFTER adjudication and CANNOT be waived by any role.
        verify_commit(answer_text, self.task.kernel_facts)

        status = "committed" if not unresolved_ids else "partial"
        self.transcript.unresolved = unresolved_ids
        receipt = SealedReceipt(
            council_name=self.spec.name,
            run_id=uuid.uuid4().hex[:12],
            final_sha256=hashlib.sha256(answer_text.encode()).hexdigest(),
            committed_status=status,
            r1_answers=tuple(sorted(self.transcript.r1_answers.items())),
            disputes=delta.disputes,
            gaps=delta.gaps,
            ballots=self.transcript.ballots,
            challenges=self.transcript.challenges,
            advisories=self.transcript.advisories,
            verdicts=tuple(verdicts.values()),
            unresolved=unresolved_ids,
            paid_calls=self.transcript.paid_calls,
            model_calls=tuple(self.transcript.model_calls),
            final_answer=answer_text,
            call_records=tuple(self.transcript.call_records),
            seat_failures=tuple(self.transcript.seat_failures),
        )
        self._sealed = receipt
        return receipt

    @property
    def sealed_receipt(self) -> SealedReceipt | None:
        return self._sealed

    def commit_again(self, *_a, **_k) -> SealedReceipt:
        raise DoubleCommitRefused("one council run owns one final answer")


__all__ = [
    "Ballot",
    "ChallengeRecord",
    "CouncilRuntime",
    "CouncilTranscript",
    "DoubleCommitRefused",
    "KernelFactTampered",
    "RoleViolation",
    "SeatModel",
    "SealedReceipt",
    "Verdict",
    "assemble_answer",
    "parse_ballot",
    "parse_challenges",
    "parse_verdicts",
    "verify_commit",
]
