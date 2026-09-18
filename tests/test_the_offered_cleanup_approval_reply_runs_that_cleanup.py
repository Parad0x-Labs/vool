"""The confirmation the cleanup preview offers is a usable approval of that cleanup.

MEASURED (the recorded residual, VOOL-DELIVERY/CURRENT_STATE.json, approval-words lane): a cleanup
request without its own approval words is answered "Temp cleanup is ready but still needs explicit
approval. Reply with: approve cleanup <id> or just say clean all temp files." (handlers.
handle_cleanup_temp_files), but `parse_operator_action_intent` selected no kind for 'approve cleanup
<id>' -- the cleanup branch had no id-shaped arm, unlike the move and calendar branches -- so the
confirmation the runtime itself offered parsed as no operator action.

THE LAW UNDER TEST: the offered reply selects cleanup_temp_files with its action id and an approval
that stands; the pending cleanup then runs over the scoped temp root. A negation of the offered
reply ("don't approve cleanup <id>") selects nothing and runs nothing.

In-process through the real served agent (`VoolAgent.run_once`) with the real pending-action store;
temp roots are disposable fixture directories. No network, no osascript.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from apps.vool_agent import VoolAgent

_CTX = {"surface": "openclaw", "platform": "openclaw"}
_OFFERED_ID = re.compile(r"approve cleanup ([0-9a-f]{8}-[0-9a-f-]{27,})\b")


class OfferedCleanupApprovalTests(unittest.TestCase):
    def _offered_id_after_asking(self, agent: VoolAgent, temp_root: Path) -> str:
        with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
            agent.run_once(f'find disk bloat in "{temp_root}"', source_context=dict(_CTX))
            asking = agent.run_once("cleanup temp files", source_context=dict(_CTX))
        reply = str(asking.get("response") or "")
        offered_id = next(iter(_OFFERED_ID.findall(reply)), "")
        self.assertTrue(offered_id, f"the cleanup reply offers no approval id: {reply[:300]!r}")
        return offered_id

    def test_the_offered_confirmation_runs_the_offered_cleanup(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            (temp_root / "one.tmp").write_bytes(b"a" * 1024)
            (temp_root / "two.tmp").write_bytes(b"b" * 1024)
            offered_id = self._offered_id_after_asking(agent, temp_root)
            with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
                approved = agent.run_once(f"approve cleanup {offered_id}", source_context=dict(_CTX))
                remaining = sorted(path.name for path in temp_root.iterdir())
        self.assertIn("Temp cleanup finished.", str(approved.get("response") or ""))
        self.assertEqual(approved.get("mode"), "tool_executed")
        self.assertEqual(remaining, [])

    def test_a_negation_of_the_offered_confirmation_runs_nothing(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            (temp_root / "keep.tmp").write_bytes(b"k" * 512)
            offered_id = self._offered_id_after_asking(agent, temp_root)
            with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
                refused = agent.run_once(f"don't approve cleanup {offered_id}", source_context=dict(_CTX))
                remaining = sorted(path.name for path in temp_root.iterdir())
        self.assertNotIn("Temp cleanup finished.", str(refused.get("response") or ""))
        self.assertEqual(remaining, ["keep.tmp"], "the negated approval must not delete anything")


if __name__ == "__main__":
    unittest.main()
