"""Served, real /api/chat: a model reply that presents a tool run the turn never made is withheld.

Distinguishable fixture (scripted loopback provider, certified through the real probe): the
"model" answers every question with a fabricated `web.search` transcript followed by one line of
prose. The door must commit the prose plus the removal notice, never the transcript -- and the
transcript row must equal the committed bytes (the commit-boundary law from Repair 3). Skips,
never passes, when the rig cannot boot here.
"""
from __future__ import annotations

import json

import pytest

FAKE_RUN_REPLY = (
    "**Tool: Web Search**\n"
    "Search query: \"Volkswagen Passat vs Golf comparison 2025\"\n"
    "**Source:** (simulated search results based on typical model knowledge)\n"
    "\n"
    "FIXTURE-REPLY: the Passat is the larger car and the Golf the cheaper one to own."
)


def test_served_fabricated_tool_transcript_is_withheld_and_the_prose_survives(tmp_path):
    from tests import _reader_served_rig as rig

    with rig.CapturingProvider(default=FAKE_RUN_REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            certification = daemon.certify(timeout=120)
            assert certification.get("state") == "verified", certification
            session = rig.canonical_session("execution-claims")
            # A timeless question: the grounding gate owns current-information turns and would
            # refuse first; the execution-claim boundary is the one under test here.
            payload = daemon.chat("what is a hatchback car body style?", session_id=session, turn_id="fake-run-turn")
            commit = payload.get("vool_response_commit") or {}
            served = str(commit.get("canonical_content") or payload.get("response") or "")
            assert served, json.dumps(payload)[:800]
            assert "Search query" not in served and "simulated search results" not in served, served
            assert "FIXTURE-REPLY: the Passat is the larger car" in served, served
            assert "Removed from this reply" in served and "web.search" in served, served
            rows = [json.loads(line) for line in (daemon.home / "data" / "conversation_log.jsonl").read_text().splitlines() if line.strip()]
            mine = [row for row in rows if row.get("session_id") == session]
            assert mine, "the served turn must be in the transcript"
            assert mine[-1]["assistant"] == served, (mine[-1]["assistant"][:200], served[:200])
            assert mine[-1].get("commit_state") == "committed"
            print("SERVED_EXECUTION_CLAIM", served[:400], flush=True)
        finally:
            daemon.stop()
