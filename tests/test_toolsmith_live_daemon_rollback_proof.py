"""TOOLSMITH closure items 2 + 6: exact-tip isolated-daemon drive, gated like the existing live
route proofs in tests/test_vool_api_server.py (VOOL_LIVE_ROUTE_PROOF=1) since it spawns a real
subprocess and a real port -- not something CI should do on every run.

Launches the real daemon (`apps.vool_api_server`) as an isolated subprocess: its own VOOL_HOME,
its own HTTP port, its own mesh port, pointed at a temporary git repo OUTSIDE the VOOL source tree.
The installed/already-running daemon (if any) is never touched -- different ports, different
VOOL_HOME entirely.

Proves, against the live process:
- `/api/runtime/version` reports the exact running commit/branch/dirty state.
- a real HTTP-driven mutation lands on disk and emits a `workspace_mutation_completed` Activity
  event reachable over `/api/runtime/events` with the required fields (hashes, rollback ID, daemon
  SHA -- see `_emit_mutation_activity_event`).
- after killing and restarting the daemon process (new PID, same VOOL_HOME), the FILE-BACKED
  mutation ledger survives: an ordinary rollback still restores the pre-mutation content.
- a second mutation, externally edited (simulating another process) between two more restarts,
  correctly refuses with `stale_revert_conflict` and preserves the external edit -- proving the
  stale-rollback guard (see `tests/test_toolsmith_stale_rollback.py`) also holds across a restart,
  not only within one process's lifetime.

Honesty note, not glossed over: the locally available model (qwen3:8b via Ollama) did not reliably
route free-form natural language to `workspace.rollback_last_change` within reasonable time in
manual probing for this proof (100+ seconds and an unrelated, wrong response for phrasing that
should trigger a revert) -- there is no deterministic, non-model phrase-to-intent mapping for that
intent in `core/execution/planner.py` the way there is for "create a file X with Y" (used below,
confirmed fast and deterministic). Coercing this model's tool selection is a model-capability
question outside this lane's scope, not a VOOL engineering defect. The two rollback calls below
therefore go through a DIRECT dispatcher call against the same VOOL_HOME/workspace the live daemon
just wrote to, rather than a second live HTTP round-trip -- proving the actual mechanism under test
(does the persisted, file-backed ledger survive a real process restart) without depending on this
specific local model's tool-selection quality for an intent with no deterministic route to it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
PY = sys.executable


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _http_post(url: str, payload: dict, timeout: float = 90.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _run_git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


@unittest.skipUnless(os.environ.get("VOOL_LIVE_ROUTE_PROOF") == "1", "live daemon proof only")
class LiveDaemonRollbackRestartProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vool_home = tempfile.mkdtemp(prefix="toolsmith-vool-home-")
        self.workspace = Path(tempfile.mkdtemp(prefix="toolsmith-daemon-fixture-"))
        self.http_port = _free_port()
        self.mesh_port = _free_port()
        self.proc = None

    def tearDown(self) -> None:
        self._stop_daemon()
        # Undo the in-process configure_runtime_home() override from the test body (if it ran) so
        # this test doesn't leak a pointer to an already-deleted directory into whatever the pytest
        # session runs next -- restores the root conftest.py's own per-session sandbox override.
        import conftest as _root_conftest  # the repo-root conftest, not tests/conftest.py
        from core.runtime_paths import configure_runtime_home

        configure_runtime_home(getattr(_root_conftest, "_TEST_RUNTIME_HOME", None))
        shutil.rmtree(self.vool_home, ignore_errors=True)
        shutil.rmtree(str(self.workspace), ignore_errors=True)

    def _start_daemon(self) -> None:
        env = {
            **os.environ,
            "VOOL_HOME": self.vool_home,
            "VOOL_DAEMON_BIND_PORT": str(self.mesh_port),
            "VOOL_DAEMON_HEALTH_PORT": "0",
            "PYTHONPATH": REPO_ROOT,
        }
        self.proc = subprocess.Popen(
            [PY, "-m", "apps.vool_api_server", "--port", str(self.http_port), "--bind", "127.0.0.1"],
            cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        import time

        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                status, _ = _http_get(f"{self.base}/healthz", timeout=2)
                if status == 200:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        out = self.proc.stdout.read() if self.proc.stdout else ""
        self.fail(f"daemon did not become healthy in time. Output:\n{out[-4000:]}")

    def _stop_daemon(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    def test_exact_tip_daemon_rollback_survives_a_real_restart(self) -> None:
        worktree_head = _run_git(["rev-parse", "HEAD"], REPO_ROOT).strip()
        worktree_dirty = bool(_run_git(["status", "--short"], REPO_ROOT).strip())

        readme_text = "Hello\nWorld\n"
        (self.workspace / "README.md").write_text(readme_text, encoding="utf-8")
        _run_git(["init", "-q"], self.workspace)
        _run_git(["config", "user.email", "toolsmith-daemon@example.invalid"], self.workspace)
        _run_git(["config", "user.name", "Toolsmith Daemon Drive"], self.workspace)
        _run_git(["add", "-A"], self.workspace)
        _run_git(["commit", "-q", "-m", "initial fixture commit"], self.workspace)
        initial_head = _run_git(["rev-parse", "HEAD"], self.workspace).strip()

        # --- 0. isolated daemon at the exact worktree tip ---
        self._start_daemon()
        status, version = _http_get(f"{self.base}/api/runtime/version")
        self.assertEqual(status, 200)
        self.assertEqual(version.get("commit"), worktree_head[:12])
        self.assertEqual(version.get("dirty"), worktree_dirty)

        # --- list files (real HTTP, deterministic planner) ---
        status, resp = _http_post(self.base + "/api/chat", {
            "messages": [{"role": "user", "content": "List the files in this project."}],
            "workspace": str(self.workspace),
        })
        self.assertEqual(status, 200)
        session_id = resp.get("vool_session_id")
        self.assertTrue(session_id)
        self.assertIn("README.md", resp.get("message", {}).get("content", ""))

        # --- mutate via real HTTP (deterministic "create a file X with Y" phrasing) ---
        status, resp = _http_post(self.base + "/api/chat", {
            "messages": [{"role": "user", "content": "create a file scratch.txt with hello-from-the-daemon-drive"}],
            "workspace": str(self.workspace),
            "session_id": session_id,
        })
        self.assertEqual(status, 200)
        scratch_path = self.workspace / "scratch.txt"
        self.assertTrue(scratch_path.exists())
        self.assertEqual(scratch_path.read_text(encoding="utf-8"), "hello-from-the-daemon-drive")

        # --- Activity event reachable over HTTP for this session ---
        status, events_payload = _http_get(f"{self.base}/api/runtime/events?session={session_id}&limit=100")
        self.assertEqual(status, 200)
        mutation_events = [e for e in events_payload.get("events", []) if str(e.get("event_type", "")).startswith("workspace_mutation")]
        self.assertGreaterEqual(len(mutation_events), 1)
        event = mutation_events[0]
        self.assertEqual(event["event_type"], "workspace_mutation_completed")
        self.assertEqual(event["tool_intent"], "workspace.write_file")
        self.assertTrue(event["rollback_id"])
        self.assertEqual(len(event["after_hash"]), 64)
        self.assertTrue(event["daemon_sha"])

        # --- restart the daemon (same VOOL_HOME) ---
        old_pid = version.get("pid")
        self._stop_daemon()
        self._start_daemon()
        status, version_after_restart = _http_get(f"{self.base}/api/runtime/version")
        self.assertEqual(status, 200)
        self.assertEqual(version_after_restart.get("commit"), version.get("commit"))
        self.assertNotEqual(version_after_restart.get("pid"), old_pid)

        # --- ordinary rollback survives the restart (direct dispatcher call, same VOOL_HOME --
        # see module docstring for why this step isn't a second live HTTP round-trip) ---
        # `configure_runtime_home`, not just the env var: the repo's own root conftest.py already
        # calls `configure_runtime_home(<pytest sandbox home>)` at session start specifically to
        # isolate every test's VOOL_HOME, and that in-process override takes priority over
        # `os.environ["VOOL_HOME"]` in `active_vool_home()`'s default (no-arg) call path -- which
        # is exactly what every mutation-ledger read/write in this dispatcher uses. Restored in
        # tearDown so this test doesn't leak its override into whatever runs after it.
        from core.runtime_execution_tools import execute_runtime_tool
        from core.runtime_paths import configure_runtime_home

        configure_runtime_home(self.vool_home)
        source_context = {"workspace": str(self.workspace), "session_id": session_id}
        reverted = execute_runtime_tool("workspace.rollback_last_change", {}, source_context=source_context)
        self.assertIsNotNone(reverted)
        self.assertTrue(reverted.ok, reverted.response_text)
        self.assertFalse(scratch_path.exists())

        # --- second mutation, external edit, second restart, rollback -> stale_revert_conflict ---
        status, resp = _http_post(self.base + "/api/chat", {
            "messages": [{"role": "user", "content": "create a file scratch2.txt with second-mutation-content"}],
            "workspace": str(self.workspace),
            "session_id": session_id,
        })
        self.assertEqual(status, 200)
        scratch2_path = self.workspace / "scratch2.txt"
        self.assertTrue(scratch2_path.exists())

        scratch2_path.write_text("an external edit the daemon never saw", encoding="utf-8")

        self._stop_daemon()
        self._start_daemon()
        status, _ = _http_get(f"{self.base}/api/runtime/version")
        self.assertEqual(status, 200)

        configure_runtime_home(self.vool_home)
        conflict_result = execute_runtime_tool("workspace.rollback_last_change", {}, source_context=source_context)
        self.assertIsNotNone(conflict_result)
        self.assertFalse(conflict_result.ok)
        self.assertEqual(conflict_result.status, "stale_revert_conflict")
        self.assertEqual(scratch2_path.read_text(encoding="utf-8"), "an external edit the daemon never saw")

        # --- final fixture state ---
        self.assertEqual(_sha256((self.workspace / "README.md").read_text(encoding="utf-8")), _sha256(readme_text))
        self.assertEqual(_run_git(["rev-parse", "HEAD"], self.workspace).strip(), initial_head)


if __name__ == "__main__":
    unittest.main()
