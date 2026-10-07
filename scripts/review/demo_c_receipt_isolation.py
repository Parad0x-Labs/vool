#!/usr/bin/env python
"""Reviewer demo C: each turn keeps its own effect receipts.

Every outward effect a turn attempts (a network fetch, a shell command, a machine write) is
decided at an effect gate, and the decision is recorded as a receipt in that turn's ledger.
This demo runs real gates, not hand-made receipts, and shows:

  1. Two turns one after the other: each ledger holds only its own receipts, stamped with
     its own turn id.
  2. Two turns at the same time on two threads: still no cross-talk.
  3. A turn that ends while another is still running does not empty the other's ledger.
  4. Outside any turn there is no ledger to read or leak into.

The network gate runs under Plan mode, so every fetch is refused before any connection is
made: the demo needs no network.

Run from the repository root:  python scripts/review/demo_c_receipt_isolation.py
"""

from __future__ import annotations

import contextlib
import sys
import threading

from _common import Checks


def turn_context(session: str, turn: str) -> dict:
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    request = TurnRequest.from_ingress(
        user_text=f"demo turn {turn}",
        source_context={"surface": "api"},
        request_id=f"req-{turn}",
        turn_id=turn,
        session_id=session,
    )
    return {"surface": "api", "platform": "api", "session_id": session, TURN_REQUEST_KEY: request}


def attempt_effects(host: str, command: str) -> None:
    """Hit three real effect gates; each records a receipt in the current turn."""
    from core.execution_gate import ExecutionGate
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote_url

    with contextlib.suppress(RemoteFetchRefusedError, OSError):
        open_remote_url(f"https://{host}/page", timeout=0.001)
    ExecutionGate.evaluate_command(command)


def summarize(name: str, receipts: list[dict]) -> None:
    print(f"  {name}: {len(receipts)} receipt(s)")
    for r in receipts:
        print(
            f"    turn={r.get('turn_id')} effect={r.get('effect_class')} lifecycle={r.get('lifecycle')} reason={str(r.get('reason'))[:70]}"
        )


def main() -> int:
    from core import mode_permission_policy as mpp
    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY, effect_receipts
    from core.remote_fetch_policy import remote_fetch_policy_scope

    checks = Checks()
    mpp.reset_mode_permission_state()
    for session in ("session-a", "session-b"):
        mpp.set_active_mode(session_id=session, mode="plan")

    print("1. Two turns, one after the other")
    a = turn_context("session-a", "turn-a")
    b = turn_context("session-b", "turn-b")
    with remote_fetch_policy_scope(a):
        attempt_effects("a.invalid", "echo turn a")
    with remote_fetch_policy_scope(b):
        attempt_effects("b.invalid", "echo turn b")
    ra, rb = a.get(EFFECT_RECEIPTS_CONTEXT_KEY) or [], b.get(EFFECT_RECEIPTS_CONTEXT_KEY) or []
    summarize("turn-a", ra)
    summarize("turn-b", rb)
    checks.check(bool(ra) and bool(rb), "both turns recorded receipts from real gates")
    checks.check({r.get("turn_id") for r in ra} == {"turn-a"}, "turn-a's ledger holds only turn-a receipts")
    checks.check({r.get("turn_id") for r in rb} == {"turn-b"}, "turn-b's ledger holds only turn-b receipts")
    checks.check(
        any("network" in str(r.get("effect_class")) and r.get("lifecycle") == "denied" for r in ra),
        "the refused fetch is on the record as denied",
    )

    print("\n2. Two turns at the same time on two threads")
    c = turn_context("session-a", "turn-c")
    d = turn_context("session-b", "turn-d")
    both_inside = threading.Barrier(2)
    seen: dict[str, list[dict]] = {}
    errors: list[BaseException] = []

    def run(ctx: dict, name: str, host: str) -> None:
        try:
            with remote_fetch_policy_scope(ctx):
                both_inside.wait(timeout=10)
                attempt_effects(host, f"echo {name}")
                both_inside.wait(timeout=10)
                seen[name] = list(effect_receipts())
        except BaseException as exc:  # reported below, never swallowed
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(c, "turn-c", "c.invalid")),
        threading.Thread(target=run, args=(d, "turn-d", "d.invalid")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    checks.check(not errors, f"both threads finished cleanly {errors or ''}")
    summarize("turn-c (seen while running)", seen.get("turn-c", []))
    summarize("turn-d (seen while running)", seen.get("turn-d", []))
    checks.check(
        bool(seen.get("turn-c")) and {r.get("turn_id") for r in seen["turn-c"]} == {"turn-c"},
        "concurrent turn-c saw only its own receipts",
    )
    checks.check(
        bool(seen.get("turn-d")) and {r.get("turn_id") for r in seen["turn-d"]} == {"turn-d"},
        "concurrent turn-d saw only its own receipts",
    )

    print("\n3. A turn that ends early does not drain one still running")
    long_ctx = turn_context("session-a", "turn-long")
    short_ctx = turn_context("session-b", "turn-short")
    long_recorded, short_closed = threading.Barrier(2), threading.Barrier(2)
    survived: dict[str, int] = {}

    def long_turn() -> None:
        with remote_fetch_policy_scope(long_ctx):
            attempt_effects("long.invalid", "echo long")
            survived["before"] = len(effect_receipts())
            long_recorded.wait(timeout=10)
            short_closed.wait(timeout=10)
            survived["after"] = len(effect_receipts())

    def short_turn() -> None:
        long_recorded.wait(timeout=10)
        with remote_fetch_policy_scope(short_ctx):
            attempt_effects("short.invalid", "echo short")
        short_closed.wait(timeout=10)

    threads = [threading.Thread(target=long_turn), threading.Thread(target=short_turn)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    print(
        f"  turn-long receipts before the short turn closed: {survived.get('before')}, after: {survived.get('after')}"
    )
    checks.check(
        survived.get("before", 0) > 0 and survived.get("before") == survived.get("after"),
        "the long turn kept every receipt after the short turn closed",
    )

    print("\n4. Outside any turn")
    outside = list(effect_receipts())
    print(f"  receipts visible outside a turn: {len(outside)}")
    checks.check(outside == [], "nothing is readable outside a turn")

    mpp.reset_mode_permission_state()
    return checks.finish("Demo C (receipt isolation)")


if __name__ == "__main__":
    sys.exit(main())
