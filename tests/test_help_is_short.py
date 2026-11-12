"""Bare ``help`` must be a short, plain, grouped reply (help-short-20261029).

The long capability dump stays owned by "what can you do" / "what can we do".
Bare ``help`` is split to its own short answer built by the module-level
``help_short_reply(agent)`` in ``core.agent_runtime.fast_paths_utility``.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime import fast_paths_utility

CLOSING_SENTENCE = "Ask me in your own words; you don't need exact commands."
MAX_CHARS = 1200
MAX_LINES = 12
BANNED_SUBSTRINGS = ("VOOL_", "Arweave", "Solana", "token", "lane")
# Every Settings place the help reply is allowed to name. Each one must exist
# literally on the Settings page source before it may be mentioned at all.
SETTINGS_PLACES = (
    "Privacy & Permissions",
    "Web lookups",
    "API Keys",
    "General",
    "Models & Providers",
    "Notifications",
    "Crypto",
)
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_ENV_VAR_WORD_RE = re.compile(r"[A-Z][A-Z0-9_]{3,}")
_EMOJI_CODE_RANGES = (
    (0x2190, 0x21FF),  # arrows
    (0x2600, 0x27BF),  # misc symbols and dingbats
    (0x2B00, 0x2BFF),  # stars and other picture symbols
    (0xFE0F, 0xFE0F),  # emoji variation selector
    (0x1F000, 0x1FBFF),  # pictographs, transport, supplemental slots, flags
)


@pytest.fixture(scope="module")
def agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _help_reply(agent: VoolAgent) -> str:
    reply = agent._smalltalk_fast_path("help", source_surface="openclaw", session_id="t")
    assert isinstance(reply, str), "bare 'help' must be answered by the fast path"
    return reply


def _assert_short_plain_reply(reply: str) -> None:
    assert reply.strip(), "the help reply must not be empty"
    assert len(reply) <= MAX_CHARS, f"help reply is {len(reply)} chars (max {MAX_CHARS})"
    lines = reply.splitlines()
    assert 0 < len(lines) <= MAX_LINES, f"help reply is {len(lines)} lines (max {MAX_LINES})"
    assert reply.rstrip(" \n\t").endswith(CLOSING_SENTENCE)
    lowered = reply.lower()
    for banned in BANNED_SUBSTRINGS:
        assert banned.lower() not in lowered, f"banned word in help reply: {banned}"
    assert "not connected" not in lowered, "help reply names a connection state it should not"
    for word in _WORD_RE.findall(reply):
        assert not _ENV_VAR_WORD_RE.fullmatch(word), f"env-var-shaped word in help reply: {word}"
    emoji = [ch for ch in reply if any(lo <= ord(ch) <= hi for lo, hi in _EMOJI_CODE_RANGES)]
    assert not emoji, f"emoji in help reply: {emoji!r}"


def test_bare_help_reply_is_short_plain_and_ends_with_the_closing_sentence(agent: VoolAgent) -> None:
    _assert_short_plain_reply(_help_reply(agent))


def test_help_reply_comes_from_help_short_reply_module_function(agent: VoolAgent) -> None:
    builder = getattr(fast_paths_utility, "help_short_reply", None)
    assert callable(builder), (
        "core.agent_runtime.fast_paths_utility must define a module-level help_short_reply(agent)"
    )
    assert _help_reply(agent) == builder(agent)


def test_capability_questions_keep_the_full_dump_and_help_differs(agent: VoolAgent) -> None:
    dump = agent._help_capabilities_text()
    assert (
        agent._smalltalk_fast_path("what can you do", source_surface="openclaw", session_id="t")
        == dump
    )
    assert (
        agent._smalltalk_fast_path("what can we do", source_surface="openclaw", session_id="t")
        == dump
    )
    assert _help_reply(agent) != dump


def test_help_reply_names_only_real_settings_places(agent: VoolAgent) -> None:
    settings_source = (
        Path(__file__).resolve().parents[1] / "core" / "vool_settings_page.py"
    ).read_text(encoding="utf-8")
    for place in SETTINGS_PLACES:
        assert place in settings_source, f"Settings place does not exist on the page: {place}"
    reply = _help_reply(agent)
    mentioned = [place for place in SETTINGS_PLACES if place in reply]
    assert len(mentioned) >= 2, f"help reply must point at real Settings places, got: {mentioned}"


def test_help_reply_has_no_crypto_line_when_the_wallet_switch_is_off(agent: VoolAgent) -> None:
    with mock.patch("core.wallet.config.wallet_enabled", return_value=False):
        reply = agent._smalltalk_fast_path("help", source_surface="openclaw", session_id="t")
    assert isinstance(reply, str)
    assert "crypto" not in reply.lower()


def test_help_reply_names_crypto_when_the_wallet_switch_is_on(agent: VoolAgent) -> None:
    with mock.patch("core.wallet.config.wallet_enabled", return_value=True):
        reply = agent._smalltalk_fast_path("help", source_surface="openclaw", session_id="t")
    assert isinstance(reply, str)
    assert "Crypto" in reply
    # The full form, Crypto line included, still fits the short-plain budget.
    _assert_short_plain_reply(reply)


def test_bare_help_is_answered_without_a_model(agent: VoolAgent) -> None:
    reply = agent._smalltalk_fast_path("help", source_surface="openclaw", session_id="t")
    assert isinstance(reply, str)
    assert reply.strip()
