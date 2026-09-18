"""Cross-turn subject contamination regressions (Checkpoint 1).

Live incident (2026-07-14, installed app, from the user's dialogue DB):
- "im askign for screen not for this!" was reconstructed as
  "...not for this! Context subject: telegram bot, telegram." and answered as Telegram,
  because a "this" pronoun in a contentful sentence resurrected the session's stale
  last_subject, and the stale subject was re-injected into the model prompt.
- A price-marker turn with its own topic ("what is oil worth?") was silently rewritten
  to a previous asset's price ("solana price now") harvested from history.

These pin all three cut points: the anaphora resolver, the model-prompt injection, and
the price-subject harvest.
"""

from __future__ import annotations

from core.agent_runtime.fast_live_info_price import recover_price_lookup_query
from core.human_input_adapter import _resolve_reference_targets


class TestAnaphoraResolverSubjectWins:
    def test_negated_reference_does_not_resurrect_stale_subject(self) -> None:
        # The exact live turn. "this" appears, but it is NEGATED ("not for this") --
        # the user is rejecting the prior subject, not referring to it.
        targets, flags = _resolve_reference_targets(
            "i am asking for screen not for this",
            current_topics=[],
            session_state={"last_subject": "telegram bot"},
            recent_turns=[{"topic_hints": ["telegram bot", "telegram"]}],
        )
        assert "telegram bot" not in targets
        assert "telegram" not in targets
        assert targets == []
        assert "ambiguous_reference" in flags

    def test_current_topic_wins_over_stale_last_subject(self) -> None:
        targets, _flags = _resolve_reference_targets(
            "what about this screen",
            current_topics=["screen"],
            session_state={"last_subject": "telegram bot"},
            recent_turns=[{"topic_hints": ["telegram bot"]}],
        )
        assert targets == ["screen"]
        assert "telegram bot" not in targets

    def test_bare_anaphor_still_resolves_the_previous_subject(self) -> None:
        # Genuine short follow-up: continuity must still work.
        targets, _flags = _resolve_reference_targets(
            "what about it",
            current_topics=[],
            session_state={"last_subject": "telegram bot"},
            recent_turns=[{"topic_hints": ["telegram bot"]}],
        )
        assert targets == ["telegram bot"]

    def test_no_pronoun_no_targets(self) -> None:
        targets, flags = _resolve_reference_targets(
            "what is my screen resolution",
            current_topics=[],
            session_state={"last_subject": "telegram bot"},
            recent_turns=[],
        )
        assert targets == []
        assert flags == []


class TestPriceHarvestDoesNotOverrideOwnTopic:
    def _solana_history(self) -> dict:
        return {
            "conversation_history": [
                {"role": "assistant", "content": "Solana is $147.20 USD. Source: CoinGecko."},
            ]
        }

    def test_own_topic_price_query_is_not_rewritten_to_prior_asset(self) -> None:
        # "what is oil worth?" -> must NOT become "solana price now".
        recovered = recover_price_lookup_query("what is oil worth?", source_context=self._solana_history())
        assert recovered == ""

    def test_news_query_is_not_rewritten(self) -> None:
        recovered = recover_price_lookup_query(
            "from the latest news is oil going up or down?", source_context=self._solana_history()
        )
        assert recovered == ""

    def test_bare_followup_still_harvests_prior_asset(self) -> None:
        # Genuine price follow-up after a Solana turn: continuity preserved (follow-up
        # prefix + price marker, no asset of its own).
        recovered = recover_price_lookup_query("what about the price now?", source_context=self._solana_history())
        assert recovered == "solana price now"

    def test_explicit_asset_still_resolves(self) -> None:
        recovered = recover_price_lookup_query("what is the bitcoin price?", source_context=self._solana_history())
        assert recovered == "bitcoin price now"
