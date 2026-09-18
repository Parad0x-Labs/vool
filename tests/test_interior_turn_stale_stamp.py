"""Interior-lane turn identity: a stale runtime stamp is not a redelivery claim.

`VoolAgent.run_once` stamps `_canonical_user_turn_id` into the CALLER-OWNED
`source_context` dict (the runtime keeps the dict's identity on purpose so receipts
written deep in the router reach the API's terminal trace). An in-process caller --
the CLI, a test, a discord in-process agent -- that reuses one context dict for two
DIFFERENT user inputs then hands the runtime turn one's stamp back as turn two's
external identity. The accept-once invocation door is right to refuse that as a
byte-diff redelivery; what is wrong is what happened next: the interior fallback
swallowed the `InvocationConflict`, left the request unbound, and the turn died at
the execution-identity fence with an opaque "no A0 request bound at ingress"
refusal. The second user input IS a new turn -- the fallback must mint a fresh
identity for it, record the anomaly, and keep the door's two real contracts intact:

  * a caller-authored id redelivered with the SAME bytes is still ONE request
    (idempotent retry), and
  * the door itself still conflicts on byte-diff under one external id
    (pinned separately in tests/foundation/test_f0b_invocation_ledger_fence.py).
"""

from __future__ import annotations

import unittest

from apps.vool_agent import VoolAgent
from core.invocation.ledger import get_invocation
from storage.migrations import run_migrations


def _ask_agent() -> VoolAgent:
    agent = VoolAgent(backend_name="test-backend", device="interior-test", persona_id="default")
    agent.start()
    return agent


class InteriorStaleStampTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_second_distinct_input_gets_a_fresh_turn_identity(self) -> None:
        # The caller reuses ONE context dict across two different user inputs -- the
        # pattern the runtime's own stamp leak turns into a false redelivery claim.
        agent = _ask_agent()
        shared_ctx: dict = {"surface": "openclaw", "platform": "openclaw", "operating_mode": "ask"}
        first = agent.run_once("what does the disk report say", source_context=shared_ctx)
        turn_one = str(shared_ctx.get("_canonical_user_turn_id") or "")
        second = agent.run_once("now summarize the same report in one line", source_context=shared_ctx)
        turn_two = str(shared_ctx.get("_canonical_user_turn_id") or "")

        # Both turns answer (neither dies at the execution-identity fence) ...
        self.assertIsInstance(first, dict)
        self.assertIsInstance(second, dict)
        self.assertNotIn("no A0 request bound", str(second.get("response") or ""))
        # ... and the second input is a NEW turn, not a redelivery of the first.
        self.assertTrue(turn_one, "runtime must stamp the canonical turn id into the context")
        self.assertTrue(turn_two)
        self.assertNotEqual(turn_one, turn_two)

    def test_stale_stamp_recovery_is_observable(self) -> None:
        # Recovering from the stale stamp must not be silent: the anomaly (a turn id
        # redelivered with different bytes by an in-process lane) is logged.
        agent = _ask_agent()
        shared_ctx: dict = {"surface": "openclaw", "platform": "openclaw"}
        agent.run_once("first distinct question about zebras", source_context=shared_ctx)
        with self.assertLogs("core.agent_runtime.agent", level="WARNING") as captured:
            agent.run_once("second distinct question about magpies", source_context=shared_ctx)
        self.assertTrue(
            any("stale" in (message or "").lower() for message in captured.output),
            captured.output,
        )

    def test_caller_authored_turn_id_reaches_the_door_verbatim(self) -> None:
        # Negative control: a caller-AUTHORED id (no runtime stamp involved) is still
        # honored as the turn's external identity -- the repair must only ignore
        # stale RUNTIME stamps, never the authored-redelivery contract. (A full
        # same-bytes replay through run_once trips a separate pre-existing defect:
        # the dialogue recorder's turn_id UNIQUE constraint. Not this seam.)
        agent = _ask_agent()
        ctx = {
            "surface": "openclaw",
            "platform": "openclaw",
            "_canonical_user_turn_id": "dlg-authored-first-1",
        }
        result = agent.run_once("one authored-identity question", source_context=ctx)
        self.assertIsInstance(result, dict)
        row = get_invocation("req:turn:dlg-authored-first-1")
        self.assertIsNotNone(row, "the authored turn id must reach the invocation door verbatim")


if __name__ == "__main__":
    unittest.main()
