from __future__ import annotations

import unittest

from core.source_credibility import evaluate_source_domain, is_domain_allowed
from core.source_reputation import allowed_domains_for_topic, get_source_profile, render_query


class SourceCredibilityTests(unittest.TestCase):
    def test_primary_technical_domain_scores_high(self) -> None:
        verdict = evaluate_source_domain("docs.python.org")
        self.assertFalse(verdict.blocked)
        self.assertGreaterEqual(verdict.score, 0.9)
        self.assertEqual(verdict.category, "primary_technical")

    def test_state_propaganda_domain_is_blocked(self) -> None:
        verdict = evaluate_source_domain("rt.com")
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.score, 0.0)
        self.assertEqual(verdict.category, "state_propaganda")

    def test_consent_interstitial_domain_is_blocked(self) -> None:
        verdict = evaluate_source_domain("consent.google.com")
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.category, "interstitial")

    def test_hyperpartisan_domain_is_blocked(self) -> None:
        verdict = evaluate_source_domain("breitbart.com")
        self.assertTrue(verdict.blocked)
        self.assertEqual(verdict.category, "hyperpartisan")

    def test_unknown_domain_requires_caution(self) -> None:
        verdict = evaluate_source_domain("example.org")
        self.assertFalse(verdict.blocked)
        self.assertLess(verdict.score, 0.5)
        self.assertEqual(verdict.category, "unknown_web")

    def test_allowlist_and_blocklist_are_enforced(self) -> None:
        self.assertTrue(is_domain_allowed("core.telegram.org", allow_domains=("core.telegram.org",)))
        self.assertFalse(is_domain_allowed("rt.com", deny_domains=("rt.com",)))
        self.assertFalse(is_domain_allowed("random.example", allow_domains=("core.telegram.org",)))

    def test_messaging_platform_profile_is_narrowed_to_telegram_when_query_is_telegram_specific(self) -> None:
        profile = get_source_profile("messaging_platform_docs")
        assert profile is not None

        query = render_query(profile, "latest telegram bot api updates")
        allow_domains = allowed_domains_for_topic(profile, "latest telegram bot api updates")

        self.assertIn("site:core.telegram.org", query)
        self.assertNotIn("discord.com", query)
        self.assertEqual(allow_domains, ("core.telegram.org",))


if __name__ == "__main__":
    unittest.main()
