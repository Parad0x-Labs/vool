from __future__ import annotations

import unittest

from core.dashboard.workstation import render_workstation_dashboard_html
from core.dashboard.workstation_render import render_workstation_document
from core.dashboard.workstation_render_voolbook_content_styles import (
    WORKSTATION_RENDER_VOOLBOOK_CONTENT_STYLES,
)
from core.dashboard.workstation_render_voolbook_directory_agent_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_AGENT_STYLES,
)
from core.dashboard.workstation_render_voolbook_directory_community_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_COMMUNITY_STYLES,
)
from core.dashboard.workstation_render_voolbook_directory_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_STYLES,
)
from core.dashboard.workstation_render_voolbook_directory_surface_styles import (
    WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_SURFACE_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_cards_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_CARDS_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_onboarding_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_ONBOARDING_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_telemetry_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TELEMETRY_STYLES,
)
from core.dashboard.workstation_render_voolbook_fabric_timeline_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_STYLES,
)
from core.dashboard.workstation_render_voolbook_feed_styles import (
    WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES,
)
from core.dashboard.workstation_render_voolbook_mode_styles import (
    WORKSTATION_RENDER_VOOLBOOK_MODE_STYLES,
)
from core.dashboard.workstation_render_shell_card_styles import (
    WORKSTATION_RENDER_SHELL_CARD_STYLES,
)
from core.dashboard.workstation_render_shell_components import (
    WORKSTATION_RENDER_SHELL_COMPONENTS,
)
from core.dashboard.workstation_render_shell_footer_styles import (
    WORKSTATION_RENDER_SHELL_FOOTER_STYLES,
)
from core.dashboard.workstation_render_shell_inspector_styles import (
    WORKSTATION_RENDER_SHELL_INSPECTOR_STYLES,
)
from core.dashboard.workstation_render_shell_layout import (
    WORKSTATION_RENDER_SHELL_LAYOUT,
)
from core.dashboard.workstation_render_shell_learning_styles import (
    WORKSTATION_RENDER_SHELL_LEARNING_STYLES,
)
from core.dashboard.workstation_render_shell_primitives import (
    WORKSTATION_RENDER_SHELL_PRIMITIVES,
)
from core.dashboard.workstation_render_shell_responsive_styles import (
    WORKSTATION_RENDER_SHELL_RESPONSIVE_STYLES,
)
from core.dashboard.workstation_render_shell_stat_styles import (
    WORKSTATION_RENDER_SHELL_STAT_STYLES,
)
from core.dashboard.workstation_render_shell_workbench_styles import (
    WORKSTATION_RENDER_SHELL_WORKBENCH_STYLES,
)
from core.dashboard.workstation_render_styles import WORKSTATION_RENDER_STYLES
from core.dashboard.workstation_render_tab_markup import WORKSTATION_RENDER_TAB_MARKUP
from core.dashboard.workstation_state import build_workstation_initial_state_payload


class _Hooks:
    def _branding_payload(self) -> dict[str, str]:
        return {"title": "VOOL"}


class DashboardWorkstationTests(unittest.TestCase):
    def test_workstation_state_payload_keeps_expected_top_level_sections(self) -> None:
        payload = build_workstation_initial_state_payload(hooks=_Hooks())

        self.assertEqual(payload["branding"], {"title": "VOOL"})
        self.assertIn("recent_activity", payload)
        self.assertIn("trading_learning", payload)
        self.assertIn("learning_lab", payload)
        self.assertEqual(payload["recent_activity"]["tasks"], [])

    def test_workstation_document_replaces_template_placeholders(self) -> None:
        html = render_workstation_document(
            initial_state='{"branding":{"title":"VOOL"}}',
            api_endpoint="/api/dashboard",
            topic_base_path="/task",
            initial_mode="fabric",
            canonical_url="https://vool.test/hive?mode=fabric",
        )

        self.assertIn("/api/dashboard", html)
        self.assertIn('"branding":{"title":"VOOL"}', html)
        self.assertIn("VOOL Brain Hive · Live dashboard", html)
        self.assertNotIn("__INITIAL_STATE__", html)
        self.assertNotIn("__WORKSTATION_CLIENT__", html)
        self.assertNotIn("__WORKSTATION_HEADER__", html)

    def test_workstation_document_keeps_extracted_style_contracts(self) -> None:
        html = render_workstation_document(
            initial_state='{"branding":{"title":"VOOL"}}',
            api_endpoint="/api/dashboard",
            topic_base_path="/task",
            initial_mode="overview",
            canonical_url="https://vool.test/hive?mode=overview",
        )

        self.assertIn(".dashboard-stage {", WORKSTATION_RENDER_STYLES)
        self.assertIn(".nb-hero {", WORKSTATION_RENDER_STYLES)
        self.assertIn("body.voolbook-mode .nb-topbar { display: flex; }", WORKSTATION_RENDER_STYLES)
        self.assertIn(".dashboard-stage {", html)
        self.assertIn(".nb-hero {", html)
        self.assertIn("body.voolbook-mode .nb-topbar { display: flex; }", html)

    def test_workstation_document_keeps_split_style_fragments(self) -> None:
        self.assertIn(":root {", WORKSTATION_RENDER_SHELL_PRIMITIVES)
        self.assertIn(".card {", WORKSTATION_RENDER_SHELL_COMPONENTS)
        self.assertIn(".dashboard-workbench {", WORKSTATION_RENDER_SHELL_LAYOUT)
        self.assertIn(".nb-feed {", WORKSTATION_RENDER_VOOLBOOK_CONTENT_STYLES)
        self.assertIn("body.voolbook-mode .nb-topbar { display: flex; }", WORKSTATION_RENDER_VOOLBOOK_MODE_STYLES)
        self.assertIn(".stats {", WORKSTATION_RENDER_SHELL_STAT_STYLES)
        self.assertIn(".card {", WORKSTATION_RENDER_SHELL_CARD_STYLES)
        self.assertIn(".learning-program {", WORKSTATION_RENDER_SHELL_LEARNING_STYLES)
        self.assertIn(".hero-follow-link {", WORKSTATION_RENDER_SHELL_FOOTER_STYLES)
        self.assertIn(".dashboard-workbench {", WORKSTATION_RENDER_SHELL_WORKBENCH_STYLES)
        self.assertIn(".dashboard-inspector-title {", WORKSTATION_RENDER_SHELL_INSPECTOR_STYLES)
        self.assertIn("#initialLoadingOverlay {", WORKSTATION_RENDER_SHELL_RESPONSIVE_STYLES)
        self.assertIn(".nb-feed {", WORKSTATION_RENDER_VOOLBOOK_FEED_STYLES)
        self.assertIn(".nb-communities {", WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_STYLES)
        self.assertIn(".nb-communities {", WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_COMMUNITY_STYLES)
        self.assertIn(".nb-agent-grid {", WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_AGENT_STYLES)
        self.assertIn(".nb-section-head {", WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_SURFACE_STYLES)
        self.assertIn(".nb-vitals {", WORKSTATION_RENDER_VOOLBOOK_FABRIC_STYLES)
        self.assertIn(".nb-vitals {", WORKSTATION_RENDER_VOOLBOOK_FABRIC_TELEMETRY_STYLES)
        self.assertIn(".nb-timeline {", WORKSTATION_RENDER_VOOLBOOK_FABRIC_TIMELINE_STYLES)
        self.assertIn(".nb-proof-card {", WORKSTATION_RENDER_VOOLBOOK_FABRIC_CARDS_STYLES)
        self.assertIn(".nb-onboard-step {", WORKSTATION_RENDER_VOOLBOOK_FABRIC_ONBOARDING_STYLES)

    def test_workstation_document_keeps_extracted_tab_markup_contracts(self) -> None:
        html = render_workstation_document(
            initial_state='{"branding":{"title":"VOOL"}}',
            api_endpoint="/api/dashboard",
            topic_base_path="/task",
            initial_mode="overview",
            canonical_url="https://vool.test/hive?mode=overview",
        )

        self.assertIn('data-tab="overview"', WORKSTATION_RENDER_TAB_MARKUP)
        self.assertIn('id="tab-commons"', WORKSTATION_RENDER_TAB_MARKUP)
        self.assertIn('id="tradingCallTable"', WORKSTATION_RENDER_TAB_MARKUP)
        self.assertIn('data-tab="overview"', html)
        self.assertIn('id="tab-commons"', html)
        self.assertIn('id="tradingCallTable"', html)

    def test_workstation_dashboard_html_keeps_existing_surface_contract(self) -> None:
        html = render_workstation_dashboard_html(
            api_endpoint="/api/dashboard",
            topic_base_path="/task",
            initial_mode="fabric",
            canonical_url="https://vool.test/hive?mode=fabric",
            hooks=_Hooks(),
        )

        self.assertIn("/api/dashboard", html)
        self.assertIn("/task", html)
        self.assertIn("VOOL Operator Workstation", html)
        self.assertIn("https://vool.test/hive?mode=fabric", html)
        self.assertNotIn("__PUBLIC_META__", html)


if __name__ == "__main__":
    unittest.main()
