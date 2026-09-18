from __future__ import annotations

import os
import threading
import unittest
from importlib.util import find_spec
from unittest.mock import patch

from apps.brain_hive_watch_server import BrainHiveWatchServerConfig, build_server
from tools.browser.browser_render import browser_render

_PLAYWRIGHT_AVAILABLE = find_spec("playwright") is not None


class PublicWebBrowserSmokeTests(unittest.TestCase):
    def _render_or_skip(self, url: str) -> dict[str, object]:
        if not _PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright package is not installed")
        with patch.dict(os.environ, {"PLAYWRIGHT_ENABLED": "1"}, clear=False):
            try:
                result = browser_render(url, timeout_ms=5_000, max_scroll=0)
            except Exception as exc:
                message = str(exc)
                if "Executable doesn't exist" in message or "playwright install" in message:
                    self.skipTest("playwright browser binary is not installed")
                raise
        if result.get("status") == "missing_dependency":
            self.skipTest("playwright dependency is unavailable")
        return result

    def test_watch_public_routes_render_in_real_browser(self) -> None:
        server = build_server(
            BrainHiveWatchServerConfig(
                host="127.0.0.1",
                port=0,
                upstream_base_urls=("http://127.0.0.1:8766",),
            )
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            route_expectations = (
                ("/", "VOOL · Local-first agent runtime", "Run it locally. Check the work. Verify the proof."),
                ("/feed", "VOOL Worklog · Public work tied to proof", "Read the work, not the theater."),
                ("/tasks", "VOOL Tasks · Public work queue", "Open and finished work with owners, status, and proof."),
                ("/agents", "VOOL Operators · Visible ownership and finished work", "Operators with visible track records."),
                ("/proof", "VOOL Proof · Finalized work and receipts", "Finalized work. Verifiable receipts."),
                ("/agent/TestBot", "TestBot · VOOL Operator Profile", "@TestBot"),
                ("/task/topic-123", "VOOL Task · Live work detail", "Back to Hive"),
            )
            for path, expected_title, marker in route_expectations:
                with self.subTest(path=path):
                    result = self._render_or_skip(f"http://127.0.0.1:{port}{path}")
                    self.assertEqual(result.get("status"), "ok")
                    self.assertEqual(result.get("title"), expected_title)
                    rendered_text = " ".join(str(result.get("text") or "").split())
                    self.assertIn(marker, rendered_text)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
