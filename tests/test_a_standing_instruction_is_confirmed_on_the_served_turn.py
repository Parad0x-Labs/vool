"""Saving or taking back a standing instruction says so on the same served turn.

"Always end your reply with a one-line summary." is stored as a standing instruction for every later chat in the
workspace (core.standing_instructions). The user must see one plain line naming what was saved,
and, when a later turn takes it back, one line naming what was removed. The line rides the
existing ``vool_profile`` frame, which the chat page renders as its confirmation line
(core/vool_chat_page.py renderProfileFrame). An ordinary turn carries no frame at all. (A rule the
Operator Profile also reads, such as bullet points, already got the profile's own line; this one
is stored only as a standing instruction, which is the silent case.)

A real daemon over ``/api/chat`` with the scripted C18 provider: no model runs.
"""
from __future__ import annotations

import time
import unittest

from tests._authorship_served_rig import REPO_ROOT, ServedDaemon, seed_in_home
from tests.test_c18_language_parity_served import _SEED, CountingProvider, _reply_text

LOCAL = "standing-local-1.5b"
PAID = "standing-paid-1.5b"
RULE = "Always end your reply with a one-line summary."
TAKE_BACK = "The summary line at the end got old, please stop."


def _confirmations(payload: dict) -> list[str]:
    frame = payload.get("vool_profile") or {}
    return [str(item.get("report") or "") for item in frame.get("saved") or [] if isinstance(item, dict)]


class StandingInstructionIsConfirmed(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self.home = Path(tempfile.mkdtemp(prefix="standing-confirm-served-"))
        self.local = CountingProvider({LOCAL: "Sure."}, answer_marker="You are Atlas")
        self.paid = CountingProvider({PAID: "PAID ANSWER"})
        self.daemon = ServedDaemon(
            self.home,
            env_extra={
                "VOOL_MODEL_LOAD_FLOOR_GB": "0",
                "VOOL_DISABLE_WEB": "1",
                "VOOL_AGENT_NAME": "Atlas",
                "OLLAMA_HOST": self.local.base_url,
                "VOOL_OLLAMA_URL": self.local.base_url,
                "VOOL_RAW_OLLAMA_API_URL": self.local.base_url,
                "VOOL_OLLAMA_CHAT_URL": f"{self.local.base_url}/api/chat",
            },
        )
        self.local.__enter__()
        self.paid.__enter__()
        self.daemon.start(timeout=300)
        seed_in_home(
            self.home,
            _SEED.format(
                root=REPO_ROOT,
                local_base=self.local.base_url,
                paid_base=self.paid.base_url,
                local=LOCAL,
                paid=PAID,
            ),
        )
        time.sleep(3.0)

    def tearDown(self) -> None:
        self.daemon.stop()
        self.local.__exit__(None, None, None)
        self.paid.__exit__(None, None, None)

    def _chat(self, text: str, session_id: str) -> dict:
        payload = self.daemon.chat(text, session_id=session_id, model=LOCAL, timeout=600.0)
        self.assertTrue(_reply_text(payload).strip(), f"empty served reply: {payload}")
        return payload

    def test_saving_and_taking_back_a_rule_each_get_one_line(self) -> None:
        saved = _confirmations(self._chat(RULE, "standing-confirm-a"))
        self.assertEqual(len(saved), 1, saved)
        self.assertIn(RULE, saved[0])
        self.assertIn("every chat", saved[0])

        ordinary = self._chat("What is a database index?", "standing-confirm-b")
        self.assertNotIn("vool_profile", ordinary, "an ordinary turn must not carry a confirmation")

        removed = _confirmations(self._chat(TAKE_BACK, "standing-confirm-c"))
        self.assertEqual(len(removed), 1, removed)
        self.assertIn(RULE, removed[0])
        self.assertNotEqual(removed[0], saved[0])


if __name__ == "__main__":
    unittest.main()
