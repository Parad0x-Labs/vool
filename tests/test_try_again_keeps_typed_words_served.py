"""A "try again" that reruns a failed request keeps its own words in the chat.

Measured on the phone (2026-11-23): "Check unread emails how many i have" failed, the owner sent
"Try again", and the chat then showed "Check unread emails how many i have" where "Try again" was.
The retry restores the failed turn's stored context, and the transcript row of the new turn was
written with the words that context carried. The rerun request routes the turn; the row keeps the
words typed on it.
"""
from __future__ import annotations

import json
import unittest
from urllib.request import Request, urlopen

from tests.test_a9_turn_routing_served import FREE_A, ORIGINAL_QUESTION, _RoutingServed


def _post(served, message: str, session_id: str, model: str = "") -> dict:
    payload = {"messages": [{"role": "user", "content": message}], "stream": False, "session_id": session_id}
    if model:
        payload["model"] = model
    request = Request(f"{served.daemon.base_url}/api/chat", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=240) as response:
        return json.loads(response.read().decode("utf-8"))


def _history(served, session: str) -> list[dict]:
    with urlopen(f"{served.daemon.base_url}/api/chat/history?session={session}&limit=50", timeout=60) as response:
        return json.loads(response.read().decode("utf-8")).get("messages") or []


class TestTryAgainKeepsTypedWords(unittest.TestCase):
    def test_failed_then_try_again_shows_try_again(self) -> None:
        with _RoutingServed(timeout=3) as served:
            served.local.hang_models = {FREE_A}
            first = _post(served, ORIGINAL_QUESTION, "sess-typed", model=FREE_A)
            session = str(first.get("vool_session_id") or "")
            self.assertTrue(session, first)
            served.local.hang_models = set()
            _post(served, "Try again", "sess-typed")
            users = [row["content"] for row in _history(served, session) if row.get("role") == "user"]
            self.assertEqual(users, [ORIGINAL_QUESTION, "Try again"], users)
            # The rerun really ran the failed request, not the two words.
            prompts = served.local.prompts_for(FREE_A)
            self.assertTrue(prompts and "Bloom filter" in prompts[-1], prompts[-1:] and prompts[-1][-600:])


if __name__ == "__main__":
    unittest.main()
