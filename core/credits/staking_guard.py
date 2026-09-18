"""
VOOL NULL Staking Anti-Cheat Layer
=====================================
Workers stake NULL tokens before submitting work.
Wrong result → stake slashed.  Honest work → keep stake + earn credits.

Economic invariant:
  cost_of_cheating = stake_lost  >  reward_for_honest_work = credits_earned
  At 1000 NULL stake: cheat = lose 1000 NULL, honest = earn 200 NULL + keep 1000.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Slash conditions — percentage of stake forfeited per violation
# ---------------------------------------------------------------------------

SLASH_CONDITIONS: dict[str, int] = {
    "wrong_result":    100,  # 100% slashed for provably wrong result
    "timeout":          50,  # 50% slashed for missing deadline
    "challenge_fail":  100,  # 100% slashed for failing challenge verification
    "spam":             25,  # 25% slashed for clearly trivial non-answer
}

# ---------------------------------------------------------------------------
# Minimum stake required by task complexity (atomic NULL units)
# ---------------------------------------------------------------------------

MIN_STAKE_BY_COMPLEXITY: dict[str, int] = {
    "simple":   10,    # 10 NULL minimum for simple tasks
    "medium":   50,    # 50 NULL for medium
    "complex":  200,   # 200 NULL for complex
    "expert":  1000,   # 1000 NULL for expert-level
}

# Credits earned on honest completion (basis for economic incentive check)
CREDITS_EARNED_BY_COMPLEXITY: dict[str, int] = {
    "simple":   2,
    "medium":   10,
    "complex":  50,
    "expert":  200,
}

# Reputation multiplier: workers above this threshold earn a stake discount
REPUTATION_DISCOUNT_THRESHOLD = 0.85  # 85%+ rep → 20% stake discount
REPUTATION_DISCOUNT_FACTOR    = 0.80  # pay 80% of base stake


# ---------------------------------------------------------------------------
# StakeRecord
# ---------------------------------------------------------------------------

@dataclass
class StakeRecord:
    worker_node_id: str
    task_id: str
    staked_null: int           # NULL tokens locked (atomic units)
    staked_at: float
    status: str                # 'staked' | 'released' | 'slashed'
    slash_reason: Optional[str] = None
    solana_tx: Optional[str]   = None  # on-chain stake tx if anchored

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# SlashEvidence — anchored on Solana when a slash occurs
# ---------------------------------------------------------------------------

@dataclass
class SlashEvidence:
    worker_id: str
    task_id: str
    slash_amount: int
    slash_reason: str
    evidence_hash: str   # sha256 of the wrong result bytes / challenge payload
    timestamp: float

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sha256_hex(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part if isinstance(part, bytes) else part.encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# StakingGuard
# ---------------------------------------------------------------------------

class StakingGuard:
    """
    In-process NULL staking guard.

    Tracks worker stakes, enforces minimums, releases or slashes on
    result submission, and maintains per-worker reputation scores.

    All balances are in atomic NULL units.
    """

    def __init__(self) -> None:
        # worker_id → current locked balance
        self._locked: dict[str, int] = {}
        # worker_id → available (unlocked) balance
        self._available: dict[str, int] = {}
        # worker_id → list of all StakeRecords (history)
        self._history: dict[str, list[StakeRecord]] = {}
        # task_id → active StakeRecord (only 'staked' status)
        self._active: dict[str, StakeRecord] = {}

    # ------------------------------------------------------------------
    # Balance helpers (used by tests / integration layer)
    # ------------------------------------------------------------------

    def deposit(self, worker_id: str, amount: int) -> None:
        """Credit a worker's available NULL balance (e.g. from wallet)."""
        if amount <= 0:
            raise ValueError("Deposit amount must be positive.")
        self._available[worker_id] = self._available.get(worker_id, 0) + amount

    def available_balance(self, worker_id: str) -> int:
        return self._available.get(worker_id, 0)

    def locked_balance(self, worker_id: str) -> int:
        return self._locked.get(worker_id, 0)

    # ------------------------------------------------------------------
    # Core staking API
    # ------------------------------------------------------------------

    def calculate_required_stake(
        self,
        task_complexity: str,
        worker_id: Optional[str] = None,
    ) -> int:
        """
        Return the minimum NULL stake required for a given complexity tier.

        High-reputation workers (score >= REPUTATION_DISCOUNT_THRESHOLD)
        receive a 20% stake discount as an earned trust bonus.

        Parameters
        ----------
        task_complexity : one of 'simple' | 'medium' | 'complex' | 'expert'
        worker_id       : if provided, applies reputation discount when eligible

        Raises
        ------
        ValueError  if task_complexity is unknown
        """
        if task_complexity not in MIN_STAKE_BY_COMPLEXITY:
            raise ValueError(
                f"Unknown complexity '{task_complexity}'. "
                f"Valid values: {list(MIN_STAKE_BY_COMPLEXITY)}"
            )
        base = MIN_STAKE_BY_COMPLEXITY[task_complexity]
        if worker_id is not None:
            rep = self.reputation_score(worker_id)
            if rep >= REPUTATION_DISCOUNT_THRESHOLD:
                return max(1, int(base * REPUTATION_DISCOUNT_FACTOR))
        return base

    def require_stake(
        self,
        task_id: str,
        worker_id: str,
        complexity: str,
    ) -> StakeRecord:
        """
        Lock the required NULL stake for a worker before they submit work.

        Raises
        ------
        ValueError  if the worker lacks sufficient available balance
        ValueError  if a stake is already active for this task_id
        ValueError  if complexity is unrecognised
        """
        if task_id in self._active:
            raise ValueError(
                f"A stake is already active for task '{task_id}'."
            )

        required = self.calculate_required_stake(complexity, worker_id)
        available = self._available.get(worker_id, 0)

        if available < required:
            raise ValueError(
                f"Worker '{worker_id}' has {available} NULL but needs "
                f"{required} NULL to stake for '{complexity}' task."
            )

        # Move funds from available → locked
        self._available[worker_id] = available - required
        self._locked[worker_id] = self._locked.get(worker_id, 0) + required

        record = StakeRecord(
            worker_node_id=worker_id,
            task_id=task_id,
            staked_null=required,
            staked_at=time.time(),
            status="staked",
        )

        self._active[task_id] = record
        self._history.setdefault(worker_id, []).append(record)
        return record

    def release_stake(
        self,
        stake: StakeRecord,
        work_proof_valid: bool,
        slash_reason: Optional[str] = None,
        evidence_bytes: Optional[bytes] = None,
    ) -> dict:
        """
        Finalise a stake after work is submitted.

        Parameters
        ----------
        stake            : The StakeRecord returned by require_stake().
        work_proof_valid : True if the work result passed verification.
        slash_reason     : Key from SLASH_CONDITIONS (required if invalid).
        evidence_bytes   : Raw wrong-result bytes for evidence hashing.

        Returns
        -------
        dict with keys:
          released_null : int   — NULL returned to worker's available balance
          slash_amount  : int   — NULL forfeited (0 if honest)
          credits_earned: int   — VOOL credits awarded (0 if slashed)
          slash_evidence: SlashEvidence | None
        """
        if stake.status != "staked":
            raise ValueError(
                f"Stake for task '{stake.task_id}' is already '{stake.status}'."
            )
        if stake.task_id not in self._active:
            raise KeyError(
                f"No active stake found for task '{stake.task_id}'."
            )

        worker_id = stake.worker_node_id
        amount    = stake.staked_null
        slash_evidence: Optional[SlashEvidence] = None

        if work_proof_valid:
            # Happy path: return full stake, award credits
            self._locked[worker_id]    = max(0, self._locked.get(worker_id, 0) - amount)
            self._available[worker_id] = self._available.get(worker_id, 0) + amount

            # Infer complexity from staked amount for credit lookup
            credits = self._credits_for_stake(amount)

            stake.status = "released"
            result = {
                "released_null":  amount,
                "slash_amount":   0,
                "credits_earned": credits,
                "slash_evidence": None,
            }
        else:
            # Slash path
            if slash_reason is None:
                slash_reason = "wrong_result"
            if slash_reason not in SLASH_CONDITIONS:
                raise ValueError(
                    f"Unknown slash_reason '{slash_reason}'. "
                    f"Valid values: {list(SLASH_CONDITIONS)}"
                )

            pct          = SLASH_CONDITIONS[slash_reason]
            slash_amount = (amount * pct) // 100
            returned     = amount - slash_amount

            self._locked[worker_id]    = max(0, self._locked.get(worker_id, 0) - amount)
            self._available[worker_id] = self._available.get(worker_id, 0) + returned

            stake.status       = "slashed"
            stake.slash_reason = slash_reason

            slash_evidence = SlashEvidence(
                worker_id=worker_id,
                task_id=stake.task_id,
                slash_amount=slash_amount,
                slash_reason=slash_reason,
                evidence_hash=_sha256_hex(evidence_bytes or b""),
                timestamp=time.time(),
            )

            result = {
                "released_null":  returned,
                "slash_amount":   slash_amount,
                "credits_earned": 0,
                "slash_evidence": slash_evidence,
            }

        del self._active[stake.task_id]
        return result

    # ------------------------------------------------------------------
    # History & reputation
    # ------------------------------------------------------------------

    def get_worker_stake_history(self, worker_id: str) -> list[StakeRecord]:
        """Return all StakeRecords for a worker (oldest first)."""
        return list(self._history.get(worker_id, []))

    def reputation_score(self, worker_id: str) -> float:
        """
        Reputation in [0.0, 1.0] based on slash history.

        score = (non-slashed completions) / (total completed)

        New workers with no history get 0.5 (neutral, benefit of the doubt).
        Workers with all honest completions reach 1.0.
        """
        history = self._history.get(worker_id, [])
        completed = [r for r in history if r.status in ("released", "slashed")]
        if not completed:
            return 0.5  # neutral prior for new workers
        honest = sum(1 for r in completed if r.status == "released")
        return honest / len(completed)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _credits_for_stake(staked_null: int) -> int:
        """
        Infer earned credits from the staked amount by reverse-mapping
        MIN_STAKE_BY_COMPLEXITY → CREDITS_EARNED_BY_COMPLEXITY.

        Uses the highest matching complexity tier.
        """
        tiers = sorted(
            MIN_STAKE_BY_COMPLEXITY.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        for complexity, min_stake in tiers:
            if staked_null >= min_stake:
                return CREDITS_EARNED_BY_COMPLEXITY[complexity]
        return CREDITS_EARNED_BY_COMPLEXITY["simple"]


# ---------------------------------------------------------------------------
# Tests (run: python staking_guard.py)
# ---------------------------------------------------------------------------

def _run_tests() -> None:
    import traceback

    passed = 0
    failed = 0

    def ok(label: str) -> None:
        nonlocal passed
        passed += 1
        print(f"  PASS  {label}")

    def fail(label: str, exc: Exception) -> None:
        nonlocal failed
        failed += 1
        print(f"  FAIL  {label}")
        traceback.print_exc()

    # ------------------------------------------------------------------
    # Test 1: stake required before submission
    # ------------------------------------------------------------------
    try:
        guard = StakingGuard()
        guard.deposit("worker-1", 200)
        stake = guard.require_stake("task-001", "worker-1", "complex")
        assert stake.status == "staked"
        assert stake.staked_null == 200
        assert guard.available_balance("worker-1") == 0
        assert guard.locked_balance("worker-1") == 200
        ok("stake required before submission")
    except Exception as exc:
        fail("stake required before submission", exc)

    # ------------------------------------------------------------------
    # Test 2: insufficient balance raises ValueError
    # ------------------------------------------------------------------
    try:
        guard2 = StakingGuard()
        guard2.deposit("worker-2", 5)  # only 5, need 10 for simple
        raised = False
        try:
            guard2.require_stake("task-002", "worker-2", "simple")
        except ValueError:
            raised = True
        assert raised, "Should have raised ValueError for insufficient balance"
        ok("insufficient balance raises ValueError")
    except Exception as exc:
        fail("insufficient balance raises ValueError", exc)

    # ------------------------------------------------------------------
    # Test 3: slash on wrong result
    # ------------------------------------------------------------------
    try:
        guard3 = StakingGuard()
        guard3.deposit("worker-3", 1000)
        stake3 = guard3.require_stake("task-003", "worker-3", "expert")
        result = guard3.release_stake(
            stake3,
            work_proof_valid=False,
            slash_reason="wrong_result",
            evidence_bytes=b"bad_inference_output",
        )
        assert result["slash_amount"] == 1000, f"Expected 1000, got {result['slash_amount']}"
        assert result["released_null"] == 0
        assert result["credits_earned"] == 0
        assert result["slash_evidence"] is not None
        assert result["slash_evidence"].slash_reason == "wrong_result"
        assert guard3.available_balance("worker-3") == 0
        assert guard3.locked_balance("worker-3") == 0
        ok("slash on wrong result (100% of 1000 NULL)")
    except Exception as exc:
        fail("slash on wrong result", exc)

    # ------------------------------------------------------------------
    # Test 4: partial slash on timeout (50%)
    # ------------------------------------------------------------------
    try:
        guard4 = StakingGuard()
        guard4.deposit("worker-4", 200)
        stake4 = guard4.require_stake("task-004", "worker-4", "complex")
        result = guard4.release_stake(
            stake4,
            work_proof_valid=False,
            slash_reason="timeout",
        )
        assert result["slash_amount"] == 100
        assert result["released_null"] == 100
        assert guard4.available_balance("worker-4") == 100
        ok("partial slash on timeout (50% of 200 NULL)")
    except Exception as exc:
        fail("partial slash on timeout", exc)

    # ------------------------------------------------------------------
    # Test 5: full release on correct result
    # ------------------------------------------------------------------
    try:
        guard5 = StakingGuard()
        guard5.deposit("worker-5", 1000)
        stake5 = guard5.require_stake("task-005", "worker-5", "expert")
        result = guard5.release_stake(stake5, work_proof_valid=True)
        assert result["released_null"] == 1000
        assert result["slash_amount"] == 0
        assert result["credits_earned"] == 200  # expert credits
        assert result["slash_evidence"] is None
        assert guard5.available_balance("worker-5") == 1000
        assert guard5.locked_balance("worker-5") == 0
        ok("full release on correct result + credits earned")
    except Exception as exc:
        fail("full release on correct result", exc)

    # ------------------------------------------------------------------
    # Test 6: reputation score drops after slash
    # ------------------------------------------------------------------
    try:
        guard6 = StakingGuard()
        guard6.deposit("worker-6", 10_000)

        # 4 honest completions
        for i in range(4):
            s = guard6.require_stake(f"task-hon-{i}", "worker-6", "simple")
            guard6.release_stake(s, work_proof_valid=True)

        rep_before = guard6.reputation_score("worker-6")
        assert rep_before == 1.0, f"Expected 1.0, got {rep_before}"

        # 1 slash
        s_bad = guard6.require_stake("task-bad", "worker-6", "simple")
        guard6.release_stake(s_bad, work_proof_valid=False, slash_reason="wrong_result")

        rep_after = guard6.reputation_score("worker-6")
        assert rep_after == 0.8, f"Expected 0.8, got {rep_after}"  # 4/5
        assert rep_after < rep_before
        ok("reputation score drops after slash (1.0 -> 0.8)")
    except Exception as exc:
        fail("reputation score drops after slash", exc)

    # ------------------------------------------------------------------
    # Test 7: high-reputation workers get lower stake requirements (bonus)
    # ------------------------------------------------------------------
    try:
        guard7 = StakingGuard()
        guard7.deposit("worker-7", 100_000)

        # Build 90% reputation (9 honest, 1 slash)
        for i in range(9):
            s = guard7.require_stake(f"task-rep-{i}", "worker-7", "simple")
            guard7.release_stake(s, work_proof_valid=True)
        s_bad = guard7.require_stake("task-rep-bad", "worker-7", "simple")
        guard7.release_stake(s_bad, work_proof_valid=False, slash_reason="spam")

        rep = guard7.reputation_score("worker-7")
        assert rep == 0.9, f"Expected 0.9, got {rep}"

        # 90% > 85% threshold → discount applies
        base_stake    = MIN_STAKE_BY_COMPLEXITY["complex"]   # 200
        reduced_stake = guard7.calculate_required_stake("complex", "worker-7")
        expected      = int(base_stake * REPUTATION_DISCOUNT_FACTOR)          # 160
        assert reduced_stake == expected, (
            f"Expected discounted stake {expected}, got {reduced_stake}"
        )
        assert reduced_stake < base_stake

        # New worker gets no discount
        new_stake = guard7.calculate_required_stake("complex", "worker-new")
        assert new_stake == base_stake
        ok(
            f"high-rep worker gets discounted stake "
            f"({base_stake} -> {reduced_stake} NULL for complex)"
        )
    except Exception as exc:
        fail("high-reputation stake discount", exc)

    # ------------------------------------------------------------------
    # Test 8: duplicate stake on same task raises ValueError
    # ------------------------------------------------------------------
    try:
        guard8 = StakingGuard()
        guard8.deposit("worker-8", 200)
        guard8.require_stake("task-dup", "worker-8", "simple")
        raised = False
        try:
            guard8.require_stake("task-dup", "worker-8", "simple")
        except ValueError:
            raised = True
        assert raised, "Should raise ValueError for duplicate task stake"
        ok("duplicate stake on same task raises ValueError")
    except Exception as exc:
        fail("duplicate stake on same task raises ValueError", exc)

    # ------------------------------------------------------------------
    # Test 9: evidence_hash is deterministic sha256 of evidence bytes
    # ------------------------------------------------------------------
    try:
        guard9 = StakingGuard()
        guard9.deposit("worker-9", 50)
        stake9 = guard9.require_stake("task-ev", "worker-9", "medium")
        evidence = b"wrong answer payload"
        result = guard9.release_stake(
            stake9,
            work_proof_valid=False,
            slash_reason="challenge_fail",
            evidence_bytes=evidence,
        )
        expected_hash = hashlib.sha256(evidence).hexdigest()
        assert result["slash_evidence"].evidence_hash == expected_hash
        ok("SlashEvidence.evidence_hash matches sha256 of evidence bytes")
    except Exception as exc:
        fail("SlashEvidence evidence_hash", exc)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    print("Running NULL staking anti-cheat tests...\n")
    _run_tests()
