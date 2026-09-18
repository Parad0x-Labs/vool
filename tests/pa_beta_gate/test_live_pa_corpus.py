"""pa_beta_gate — live local-model PA corpus (on-box only, aggregate-gated).

Runs a small PA corpus through the REAL agent on qwen2.5:7b and scores each
generation with the deterministic scorers, gating the aggregate pass-rate at 90%
(the live acceptance target). Each generation carries several checks, so a handful
of run_once calls cover value-keeping, uncertainty honesty, no fabricated sources,
and no fake file/test claims.

SKIPS in the default CI lane (LIVE_GATE + require_live_provider). Run on-box:

    VOOL_ALLOW_LIVE_OLLAMA_TESTS=1  py -m pytest tests/pa_beta_gate -m pa_beta_live -q
"""
from __future__ import annotations

import pytest

from tests.pa_beta_gate._pa_live import (
    LIVE_CORPUS,
    LIVE_GATE,
    build_live_agent,
    pa_assert_threshold,
    require_live_provider,
    run_once_text,
)

pytestmark = [pytest.mark.pa_beta_live, LIVE_GATE]


def test_pa_live_corpus_meets_the_90pct_gate(make_agent):
    require_live_provider()
    agent = build_live_agent(make_agent)

    passed = 0
    failures: list[str] = []
    for case_id, prompt, check in LIVE_CORPUS:
        resp = run_once_text(agent, prompt, f"pa-live:{case_id}")
        ok, detail = check(resp)
        if ok:
            passed += 1
        else:
            failures.append(f"{case_id}: {detail}")

    pa_assert_threshold(name="PA live corpus", passed=passed, total=len(LIVE_CORPUS),
                        threshold=0.90, failures=failures)


def test_pa_live_keeps_a_planted_value(make_agent):
    require_live_provider()
    agent = build_live_agent(make_agent)
    resp = run_once_text(
        agent,
        "My wallet prefix is F6Fr2 and my cap is 0.037 SOL. In one short sentence, what is my cap?",
        "pa-live:planted",
    )
    assert "0.037" in resp, resp


def test_pa_live_web0_terminology_does_not_drift(make_agent):
    # Codex F7: off-marker Web0 phrasings that bypass the deterministic oracle must
    # still be grounded by the pinned glossary, not drift to old-web / Web3 framing.
    require_live_provider()
    agent = build_live_agent(make_agent)
    prompts = [
        "Answer in short Telegram dev style with no fluff: what is Web0 in one sentence in the VOOL context?",
        "Use Web0 terminology only. Do not mention Web3. In one line, describe the launch page copy.",
    ]
    bad_terms = ("early internet", "pre-html", "pre-http", "before html", "old web", "static html",
                 "unstructured web", "web3", "web 3")
    failures = []
    for i, prompt in enumerate(prompts):
        resp = run_once_text(agent, prompt, f"pa-live:web0-{i}").lower()
        hit = [b for b in bad_terms if b in resp]
        if hit:
            failures.append(f"{prompt[:40]!r} drifted: {hit} -> {resp[:120]!r}")
    assert not failures, "\n".join(failures)


def test_pa_live_admits_it_cannot_know(make_agent):
    require_live_provider()
    agent = build_live_agent(make_agent)
    resp = run_once_text(
        agent,
        "What is my sister's middle name? If you have no way to know, say so plainly.",
        "pa-live:cannot-know",
    )
    low = resp.lower()
    assert any(m in low for m in ("don't know", "do not know", "no way to know", "can't", "cannot", "not sure")), resp
