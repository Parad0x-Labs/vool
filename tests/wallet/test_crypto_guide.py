"""VOOL's built-in crypto guide (core.wallet.crypto_guide): plain answers with fixed, official links, offline, and the
crypto.guide tool that hands them to the assistant without opening a browser."""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

#: each link is the project's own site or a public authority, never a search result or a blog
OFFICIAL_HOSTS = {
    "solana.com", "explorer.solana.com", "solscan.io", "ethereum.org", "docs.base.org", "docs.bnbchain.org", "docs.arbitrum.io",
    "www.circle.com", "tether.to", "dev.jup.ag", "etherscan.io", "basescan.org", "bscscan.com", "arbiscan.io", "www.x402.org", "www.ic3.gov",
}


def test_every_link_is_https_on_an_official_host_and_every_topic_has_one() -> None:
    from core.wallet import crypto_guide

    for name, url in crypto_guide.SOURCES.values():
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.hostname in OFFICIAL_HOSTS and name, url
    ids = [topic.topic_id for topic in crypto_guide.TOPICS]
    assert len(ids) == len(set(ids))
    for topic in crypto_guide.TOPICS:
        assert topic.sources and all(key in crypto_guide.SOURCES for key in topic.sources), topic.topic_id
        assert "honest" not in topic.text.lower()


@pytest.mark.parametrize(("question", "expected"), [
    ("what is solana?", "solana"),
    ("how do gas fees work on base", "gas"),
    ("what is slippage when I convert sol to usdc", "swaps"),
    ("someone on telegram wants my seed phrase to fix my wallet", "recovery_phrase"),
    ("is this free airdrop a scam", "scams"),
    ("what's the difference between USDC and USDT", "stablecoins"),
    ("I sent usdc on the wrong network, what now", "wrong_network"),
    ("how do I check a tx hash on solscan", "explorers"),
    ("what is a token approval and how do drainers work", "approvals"),
    ("what is x402", "x402"),
])
def test_the_question_finds_the_topic_that_answers_it(question, expected) -> None:
    from core.wallet import crypto_guide

    found = crypto_guide.find(question)
    assert found and found[0].topic_id == expected, (question, [t.topic_id for t in found])


def test_the_answer_carries_its_links_and_says_so_when_it_has_no_entry() -> None:
    from core.wallet import crypto_guide

    found = crypto_guide.answer("what is solana")
    assert found["topics"][0] == "solana" and "Read more:" in found["text"] and "https://solana.com/learn" in found["text"]
    assert all(link["url"] in found["text"] for link in found["links"])
    nothing = crypto_guide.answer("what's a good pizza recipe")
    assert nothing["topics"] == [] and nothing["links"] == [] and "It covers:" in nothing["text"]


def test_the_crypto_guide_tool_answers_offline_and_is_offered_for_a_crypto_question(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.tool_demand_signals import resolve_demand_signals
    from core.wallet import crypto_guide

    result = _dispatch_runtime_tool("crypto.guide", {"question": "how much is a solana network fee?"}, source_context={"session_id": "guide"})
    assert result.ok and result.status == "answered", result.response_text
    assert result.details["guide"]["topics"][0] == "solana_fees" and "5,000 lamports" in result.response_text
    official = {url for _name, url in crypto_guide.SOURCES.values()}
    assert set(result.details["observation"]["links"]) <= official
    assert "crypto.guide" in resolve_demand_signals("what is slippage?").explicit_intents
    assert "crypto.guide" in resolve_demand_signals("how does a seed phrase work").explicit_intents
    assert "crypto.guide" not in resolve_demand_signals("send 10 usdc to Maya").explicit_intents
