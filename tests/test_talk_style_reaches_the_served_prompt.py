"""The Talk style chosen in Settings reaches the model on a real served chat turn.

Settings -> Talk style (Casual / Business / Cheeky) writes ``communication_style`` through
``POST /api/settings/prefs``, and onboarding promises "I'll keep a {style} tone". This drives the
real daemon (``apps.vool_api_server``) in its own ``VOOL_HOME``, sets the style through the same
HTTP door the Settings page uses, sends an ordinary question to ``/api/chat`` and reads the
request the provider actually received. The provider is the scripted C18 stub: no model runs.

Changing the style between turns must change the wire, so a stale or hard-coded directive fails.
"""
from __future__ import annotations

import json
import time
import unittest
from urllib.request import Request, urlopen

from core.user_preferences import communication_style_directive
from tests._authorship_served_rig import REPO_ROOT, ServedDaemon, seed_in_home
from tests.test_c18_language_parity_served import _SEED, CountingProvider, _reply_text

LOCAL = "talkstyle-local-1.5b"
PAID = "talkstyle-paid-1.5b"
QUESTION = "Please explain how a database index improves search speed."


class TalkStyleReachesTheServedPrompt(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self.home = Path(tempfile.mkdtemp(prefix="talk-style-served-"))
        self.local = CountingProvider({LOCAL: "An index is a sorted lookup structure."}, answer_marker="You are Atlas")
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
        self.local.reset()

    def tearDown(self) -> None:
        self.daemon.stop()
        self.local.__exit__(None, None, None)
        self.paid.__exit__(None, None, None)

    def _set_style(self, style: str) -> None:
        request = Request(
            f"{self.daemon.base_url}/api/settings/prefs",
            data=json.dumps({"communication_style": style}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            self.assertLess(response.status, 300)
        with urlopen(f"{self.daemon.base_url}/api/settings/prefs", timeout=30) as response:
            stored = json.loads(response.read().decode("utf-8"))
        prefs = stored.get("preferences", stored)
        self.assertEqual(prefs.get("communication_style"), style, stored)

    def _answer_wire_after(self, session_id: str) -> str:
        self.local.reset()
        payload = self.daemon.chat(QUESTION, session_id=session_id, model=LOCAL, timeout=600.0)
        self.assertTrue(_reply_text(payload).strip(), f"empty served reply: {payload}")
        return self.local.wire_text(LOCAL, index=0)

    def test_the_saved_talk_style_is_in_the_request_the_model_receives(self) -> None:
        business = communication_style_directive("business")
        cheeky = communication_style_directive("cheeky")
        self.assertTrue(business and cheeky and business != cheeky)

        self._set_style("business")
        wire = self._answer_wire_after("talk-style-business")
        self.assertIn(business, wire, "the Business style never reached the model")
        self.assertNotIn(cheeky, wire)

        self._set_style("cheeky")
        wire = self._answer_wire_after("talk-style-cheeky")
        self.assertIn(cheeky, wire, "changing the style in Settings did not change the model's instructions")
        self.assertNotIn(business, wire)


if __name__ == "__main__":
    unittest.main()
