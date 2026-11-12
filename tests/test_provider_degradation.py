"""Provider failure cleanup proofs: every adapter failure path lands in the
typed LiveSeatError degradation channel — nothing escapes, nothing is faked."""
from __future__ import annotations

import pytest

from core.council import LiveSeatError
from core.council.capsule import ContextCapsule
from core.council.openrouter_adapter import OpenRouterSeat, _TruncatedBeforeContent
from core.council.seats import Role


def _capsule() -> ContextCapsule:
    return ContextCapsule("t", "s", Role.VOTER, "R1_BLIND", task_text="x")


def test_double_truncation_degrades_to_liveseaterror(monkeypatch):
    seat = OpenRouterSeat("fake/model")

    def always_truncated(capsule, max_tokens, prior_error=""):
        raise _TruncatedBeforeContent(f"truncated at {max_tokens}")

    monkeypatch.setattr(seat, "_respond_once", always_truncated)
    with pytest.raises(LiveSeatError) as excinfo:
        seat.respond(_capsule())
    assert "6000" in str(excinfo.value)  # retried at double budget first
    assert not isinstance(excinfo.value, _TruncatedBeforeContent)


def test_http_error_is_liveseaterror(monkeypatch):
    import urllib.error

    seat = OpenRouterSeat("fake/model")
    monkeypatch.setattr(
        "core.council.openrouter_adapter.urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(
            urllib.error.HTTPError("url", 429, "rate limited", {}, None)
        ),
    )
    with pytest.raises(LiveSeatError):
        seat.respond(_capsule())


def test_runtime_records_failure_and_continues_without_stub():
    """A seat that dies mid-R1 is recorded, skipped, never substituted."""
    from core.council import (
        CouncilRuntime,
        CouncilSpec,
        EscalationPolicy,
        Role,
        SeatSpec,
        TaskInput,
        Tier,
    )
    from core.council.runtime import LiveSeatError as RuntimeLiveSeatError

    class DyingModel:
        def __init__(self) -> None:
            self.capsules: list = []

        def respond(self, capsule):
            self.capsules.append(capsule)
            if capsule.phase == "R1_BLIND":
                raise RuntimeLiveSeatError("HTTP 429: rate limited")
            return "BALLOT: cand-1"

    class OkModel:
        def __init__(self, text: str) -> None:
            self.text = text
            self.capsules: list = []

        def respond(self, capsule):
            self.capsules.append(capsule)
            return self.text

    spec = CouncilSpec(
        name="degrade",
        seats=(
            SeatSpec("dead", "Dead Seat", Role.CHALLENGER, Tier.LOCAL),
            SeatSpec("alive1", "A1", Role.VOTER, Tier.LOCAL),
            SeatSpec("alive2", "A2", Role.ADVISOR, Tier.LOCAL),
            SeatSpec("judge", "J", Role.JUDGE, Tier.LOCAL),
        ),
        final_judge_seat_id="judge",
        policy=EscalationPolicy(paid_budget=1),
    )
    models = {
        "dead": DyingModel(),
        "alive1": OkModel("The service handles 5000 requests per second."),
        "alive2": OkModel("The service handles 9000 requests per second."),
        "judge": OkModel("DISPUTE d0: UPHELD_A evidence=receipt:r-1"),
    }
    rt = CouncilRuntime(spec, TaskInput(task_text="throughput?"), models)
    receipt = rt.run()
    # the dead seat's R1 failure was recorded and produced NO candidate answer
    assert any(f[0] == "dead" and "429" in f[2] for f in receipt.seat_failures)
    assert len(receipt.r1_answers) == 2
    # the surviving two candidates disputed; judge resolved; single commit held
    assert receipt.disputes and receipt.verdicts


def test_all_r1_dead_refuses_whole_run():
    from core.council import (
        CouncilRuntime,
        CouncilSpec,
        EscalationPolicy,
        Role,
        SeatSpec,
        TaskInput,
        Tier,
    )

    class Dead:
        capsules: list = []

        def respond(self, capsule):
            raise LiveSeatError("provider down")

    spec = CouncilSpec(
        name="alldead",
        seats=(
            SeatSpec("a", "A", Role.CHALLENGER, Tier.LOCAL),
            SeatSpec("b", "B", Role.VOTER, Tier.LOCAL),
            SeatSpec("j", "J", Role.JUDGE, Tier.LOCAL),
        ),
        final_judge_seat_id="j",
        policy=EscalationPolicy(paid_budget=1),
    )
    rt = CouncilRuntime(spec, TaskInput(task_text="x"), {"a": Dead(), "b": Dead(), "j": Dead()})
    with pytest.raises(LiveSeatError, match="fewer than two seats"):
        rt.run()
