"""Cost ladder, operator escalation and hard spend ceilings.

The ladder is the law the spec states flatly: free, free, cheap, pause. PREMIUM is not
a rung — it exists only behind an EXPLICIT operator escalation, and the escalation
object cannot be forged by the code that wants to spend. Ceilings are hard on four
axes (calls, tokens, cost, wall clock) and cannot be constructed unbounded, because
"unlimited budget" is not a budget.
"""

from __future__ import annotations

import threading

import pytest

from core.council.cost_ladder import (
    BudgetExhausted,
    CostLadderError,
    CostPolicy,
    CostTier,
    DEFAULT_LADDER,
    ExhaustionReason,
    OperatorEscalation,
    SpendCeilings,
    SpendMeter,
    mint_operator_escalation,
    next_tier,
)


def make_ceilings(**overrides):
    fields = {
        "max_calls": 4,
        "max_tokens": 100_000,
        "max_cost_usd": 0.50,
        "wall_clock_seconds": 900.0,
    }
    fields.update(overrides)
    return SpendCeilings(**fields)


class TestLadder:
    def test_default_ladder_is_free_free_cheap_then_pause(self):
        assert DEFAULT_LADDER == (CostTier.FREE, CostTier.FREE, CostTier.CHEAP)
        policy = CostPolicy(ceilings=make_ceilings())
        assert [next_tier(policy, i) for i in range(4)] == [
            "free", "free", "cheap", "pause",
        ]

    def test_pause_is_terminal_beyond_the_ladder(self):
        policy = CostPolicy(ceilings=make_ceilings())
        assert next_tier(policy, 99) == "pause"

    def test_premium_is_not_on_the_default_ladder(self):
        policy = CostPolicy(ceilings=make_ceilings())
        for attempt in range(10):
            assert next_tier(policy, attempt) != "premium"

    def test_ladder_may_not_contain_premium(self):
        with pytest.raises(CostLadderError):
            CostPolicy(
                ceilings=make_ceilings(),
                ladder=(CostTier.FREE, CostTier.PREMIUM),
            )

    def test_ladder_must_start_free(self):
        with pytest.raises(CostLadderError):
            CostPolicy(ceilings=make_ceilings(), ladder=(CostTier.CHEAP,))

    def test_ladder_must_be_non_empty(self):
        with pytest.raises(CostLadderError):
            CostPolicy(ceilings=make_ceilings(), ladder=())

    def test_premium_arrives_only_through_an_escalation(self):
        escalation = mint_operator_escalation(task_id="T1")
        policy = CostPolicy(ceilings=make_ceilings(), escalation=escalation)
        assert next_tier(policy, 3) == "premium"
        assert next_tier(policy, 4) == "premium"


class TestOperatorEscalation:
    def test_escalation_cannot_be_forged_by_direct_construction(self):
        with pytest.raises(Exception):
            OperatorEscalation(task_id="T1", tier=CostTier.PREMIUM, granted_by="seat-1")

    def test_escalation_is_minted_for_one_task_and_premium_only(self):
        escalation = mint_operator_escalation(task_id="T9", granted_by="operator")
        assert escalation.task_id == "T9"
        assert escalation.tier is CostTier.PREMIUM
        assert escalation.granted_by == "operator"
        with pytest.raises(Exception):
            mint_operator_escalation(task_id="T9", tier=CostTier.CHEAP)

    def test_escalation_names_its_task(self):
        escalation = mint_operator_escalation(task_id="T1")
        assert escalation.task_id == "T1"


class TestSpendCeilings:
    def test_all_four_axes_are_required_and_bounded_above_zero(self):
        ceilings = make_ceilings()
        assert ceilings.max_calls == 4
        assert ceilings.max_tokens == 100_000
        assert ceilings.max_cost_usd == 0.50
        assert ceilings.wall_clock_seconds == 900.0

    @pytest.mark.parametrize(
        "overrides",
        [
            {"max_calls": 0},
            {"max_calls": -1},
            {"max_tokens": 0},
            {"max_cost_usd": 0.0},
            {"wall_clock_seconds": 0.0},
        ],
    )
    def test_unbounded_or_negative_ceilings_are_refused(self, overrides):
        with pytest.raises(CostLadderError):
            make_ceilings(**overrides)

    def test_no_infinite_ceiling_construction_path_exists(self):
        # There is no factory that hands out an unbounded meter: the type itself
        # refuses. (Guards the "hard ceilings" law against a friendly shortcut.)
        assert not hasattr(SpendCeilings, "unbounded")


class TestSpendMeter:
    def test_reservation_within_the_call_ceiling_succeeds(self):
        meter = SpendMeter(make_ceilings(max_calls=2))
        assert meter.try_reserve_call() == 1
        assert meter.try_reserve_call() == 2

    def test_reservation_past_the_call_ceiling_is_a_typed_refusal(self):
        meter = SpendMeter(make_ceilings(max_calls=1))
        meter.try_reserve_call()
        with pytest.raises(BudgetExhausted) as caught:
            meter.try_reserve_call()
        assert caught.value.reason is ExhaustionReason.CALLS

    def test_exhaustion_reports_calls_tokens_cost_and_wall_clock_by_name(self):
        assert (
            SpendMeter(make_ceilings(max_tokens=10)).record_usage(tokens=11).exhaustion()
            is ExhaustionReason.TOKENS
        )
        assert (
            SpendMeter(make_ceilings(max_cost_usd=0.01))
            .record_usage(cost_usd=0.02)
            .exhaustion()
            is ExhaustionReason.COST
        )

    def test_wall_clock_exhaustion_uses_the_injected_clock(self):
        now = {"t": 0.0}
        meter = SpendMeter(
            make_ceilings(max_calls=10, wall_clock_seconds=60.0),
            clock=lambda: now["t"],
        )
        assert meter.exhaustion() is None
        now["t"] = 61.0
        assert meter.exhaustion() is ExhaustionReason.WALL_CLOCK

    def test_wall_clock_exhaustion_blocks_new_reservations(self):
        now = {"t": 0.0}
        meter = SpendMeter(
            make_ceilings(max_calls=10, wall_clock_seconds=60.0),
            clock=lambda: now["t"],
        )
        now["t"] = 100.0
        with pytest.raises(BudgetExhausted) as caught:
            meter.try_reserve_call()
        assert caught.value.reason is ExhaustionReason.WALL_CLOCK

    def test_snapshot_states_the_books(self):
        meter = SpendMeter(make_ceilings())
        meter.try_reserve_call()
        meter.record_usage(tokens=1_500, cost_usd=0.01)
        snap = meter.snapshot()
        assert snap["calls_used"] == 1
        assert snap["tokens_used"] == 1_500
        assert snap["cost_used"] == pytest.approx(0.01)
        assert snap["exhaustion"] is None

    def test_a_budget_race_admits_exactly_one_winner(self):
        meter = SpendMeter(make_ceilings(max_calls=1))
        barrier = threading.Barrier(8)
        outcomes: list[object] = []
        lock = threading.Lock()

        def racer():
            barrier.wait()
            try:
                meter.try_reserve_call()
                result = "won"
            except BudgetExhausted as exc:
                result = exc.reason
            with lock:
                outcomes.append(result)

        threads = [threading.Thread(target=racer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert outcomes.count("won") == 1
        assert outcomes.count(ExhaustionReason.CALLS) == 7
        assert meter.snapshot()["calls_used"] == 1

    def test_tokens_crossing_the_ceiling_block_all_later_reservations(self):
        meter = SpendMeter(make_ceilings(max_calls=8, max_tokens=1_000))
        booked = 0
        while True:
            try:
                meter.try_reserve_call()
            except BudgetExhausted as exc:
                assert exc.reason is ExhaustionReason.TOKENS
                break
            meter.record_usage(tokens=300)
            booked += 1
            assert booked <= 8, "the call ceiling must also bound this loop"
        # Three 300-token calls fit under 1,000; a fourth may reserve while the
        # books still say 900 — future tokens are unknowable at the door — but its
        # 300 tokens cross the ceiling and NO reservation after it succeeds.
        assert booked == 4
        assert meter.snapshot()["tokens_used"] == 1_200
        assert meter.snapshot()["exhaustion"] is ExhaustionReason.TOKENS
