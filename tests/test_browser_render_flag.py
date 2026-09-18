from __future__ import annotations

import os
import unittest
from unittest import mock

from tools.browser.browser_render import _classify_rendered_content, browser_render


class BrowserRenderFlagTests(unittest.TestCase):
    def test_browser_render_disabled_by_default(self) -> None:
        os.environ.pop("PLAYWRIGHT_ENABLED", None)
        with mock.patch("tools.browser.browser_render.policy_engine.playwright_enabled", return_value=False):
            result = browser_render("https://example.com")
        self.assertEqual(result["status"], "disabled_by_policy")

    def test_duckduckgo_anomaly_page_is_treated_as_captcha(self) -> None:
        status = _classify_rendered_content(
            '<div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div>',
            "Select all squares containing a duck.",
        )
        self.assertEqual(status, "captcha")


if __name__ == "__main__":
    unittest.main()
