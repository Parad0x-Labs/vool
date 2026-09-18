"""Shared data corpus for the pa_beta_gate suite (personal-assistant beta gate).

Not a test module (leading underscore -> pytest does not collect it). The
data-driven PA tests iterate these corpora so the suite reaches broad coverage
through genuinely distinct assertions, not repeated boilerplate.

Design fact carried from the audit: exact values (decimals, .null names, wallet
prefixes, dates, paths, caps) survive VERBATIM only through the char-exact L3
memory layer (core.vool_memory.node_store / node_search_hybrid). The dialogue
`current_user_goal` snapshot is normalised and corrupts decimals ("0.037" ->
"0. 037") and dotted names ("alice.null" -> "alice. null"), so exact-value recall
tests target L3, and the goal path is asserted only for topic-level continuity.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlantedFact:
    kind: str          # decimal | null_name | wallet_prefix | date | path | cap | number | name | preference
    statement: str     # what the user says (a natural PA utterance)
    needle: str        # the exact substring that must survive char-for-char
    query: str         # a recall query that should surface this fact


# A spread across every exact-value type the beta gate names, plus preference/name.
PLANTED_FACTS: tuple[PlantedFact, ...] = (
    PlantedFact("decimal", "My launch spend cap is exactly 0.037 SOL and it must not be exceeded.", "0.037", "launch spend cap"),
    PlantedFact("decimal", "Set the slippage tolerance to 0.5 percent on every swap from now on.", "0.5", "slippage tolerance percent"),
    PlantedFact("decimal", "The gas budget per action is 0.0021 SOL, keep it tight.", "0.0021", "gas budget per action"),
    PlantedFact("null_name", "Register the project domain as alice.null for the launch.", "alice.null", "project domain name"),
    PlantedFact("null_name", "My personal web0 handle is sls-0x.null, remember it.", "sls-0x.null", "personal web0 handle"),
    PlantedFact("null_name", "The docs site should live at goldenloop.null once it's ready.", "goldenloop.null", "docs site domain"),
    PlantedFact("wallet_prefix", "My launch wallet prefix is F6Fr2 and only that one is mine.", "F6Fr2", "launch wallet prefix"),
    PlantedFact("wallet_prefix", "Use the treasury address starting 28hxX for all payouts.", "28hxX", "treasury wallet prefix"),
    PlantedFact("date", "The mainnet launch deadline is 2026-07-15, plan the rollout around it.", "2026-07-15", "mainnet launch deadline"),
    PlantedFact("date", "The security audit is due 2026-08-01 at the very latest.", "2026-08-01", "security audit due date"),
    PlantedFact("path", "The spend policy lives at data/keys/spend_policy.json, note that path.", "data/keys/spend_policy.json", "spend policy file path"),
    PlantedFact("path", "Runtime logs are written to logs/vool/runtime.log on this box.", "logs/vool/runtime.log", "runtime log file path"),
    PlantedFact("cap", "The daily transfer cap is 50 USDC and the weekly cap is 200 USDC.", "50 USDC", "daily transfer cap"),
    PlantedFact("cap", "Never send more than 3 payments per hour under any condition.", "3 payments", "payment rate limit per hour"),
    PlantedFact("number", "My operator node id is 8829145, keep it for later reference.", "8829145", "operator node id"),
    PlantedFact("number", "The staging API port is 8096 and production is 8097.", "8096", "staging api port"),
    PlantedFact("name", "My name is Loop and the project codename is GOLDEN_LOOP for now.", "GOLDEN_LOOP", "project codename"),
    PlantedFact("preference", "Always answer me in short Telegram dev style with no fluff.", "Telegram", "preferred answer style"),
)

# Every distinct exact-value type covered above (for coverage assertions).
VALUE_KINDS = ("decimal", "null_name", "wallet_prefix", "date", "path", "cap", "number")


# Superseding pairs — "latest instruction overrides older" for the same slot.
# (kind, first statement, first needle, second statement, second needle)
SUPERSEDING_FACTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("decimal", "My spend cap is 0.037 SOL.", "0.037", "Actually, raise my spend cap to 0.088 SOL.", "0.088"),
    ("null_name", "Register alice.null for the project.", "alice.null", "Change of plan — register beta7.null instead.", "beta7.null"),
    ("date", "The deadline is 2026-07-15.", "2026-07-15", "The deadline moved to 2026-09-30.", "2026-09-30"),
    ("cap", "Daily cap is 50 USDC.", "50 USDC", "Bump the daily cap to 120 USDC.", "120 USDC"),
    ("number", "The staging port is 8096.", "8096", "We migrated staging to port 9090.", "9090"),
)


# Distractor turns for long-session torture — none share the mission vocabulary.
DISTRACTORS: tuple[str, ...] = (
    "By the way, what's a good sourdough hydration ratio?",
    "Remind me how photosynthesis works in one line.",
    "What's the tallest mountain in Africa again?",
    "Tell me a quick fact about the Roman aqueducts.",
    "How many time zones does Russia span?",
    "What's the boiling point of water at sea level?",
    "Recommend a stretch for tight hamstrings.",
    "What's the difference between weather and climate?",
    "How do noise-cancelling headphones work?",
    "What year did the first email get sent?",
    "Give me a two-word summary of jazz.",
    "What's a fun fact about octopuses?",
)


def distractors(n: int) -> list[str]:
    """A deterministic list of n distractor turns (cycled, index-tagged so each is unique)."""
    return [f"{DISTRACTORS[i % len(DISTRACTORS)]} (aside {i})" for i in range(n)]
