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
                # The public routes render their content from fetches after
                # domcontentloaded; the bounded settle captures the settled page
                # instead of racing the fetch (CI run 36845813587 shard tests(7)
                # captured the loading skeleton and lost the '@TestBot' marker).
                result = browser_render(
                    url,
                    timeout_ms=5_000,
                    max_scroll=0,
                    network_idle_timeout_ms=2_000,
                )
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

    def test_profile_route_renders_content_that_settles_after_load(self) -> None:
        """The render contract must capture the settled page, not win a race
        against the profile fetch: the marker this route is asserted on exists
        only after the fetch settles. Proven with a bounded 400ms proxy delay —
        longer than the domcontentloaded-to-capture window, far inside every
        deadline. Without the settle this renders the loading skeleton
        (reproduced identically at 6399dd3 and at the PR105 head)."""
        if not _PLAYWRIGHT_AVAILABLE:
            self.skipTest("playwright package is not installed")
        import time

        import apps.brain_hive_watch_server as watch_app

        real_proxy = watch_app._proxy_voolbook_get

        def delayed_proxy(*args, **kwargs):
            time.sleep(0.4)
            return real_proxy(*args, **kwargs)

        with patch.object(watch_app, "_proxy_voolbook_get", delayed_proxy):
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
                result = self._render_or_skip(f"http://127.0.0.1:{port}/agent/TestBot")
                self.assertEqual(result.get("status"), "ok")
                rendered_text = " ".join(str(result.get("text") or "").split())
                self.assertIn("@TestBot", rendered_text)
                self.assertNotIn("Loading operator", rendered_text)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=1)
