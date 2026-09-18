from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=False, reason="The runtime can scaffold bounded builds, but full research -> code -> run -> verify autonomy is not implemented yet.")
def test_future_builder_mode_can_research_generate_write_and_verify_without_manual_stitching(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "build a next-gen Telegram bot from official docs and good GitHub repos, write it, run it, and summarize what happened",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "verification passed" in result["response"].lower()
    assert "sources used" in result["response"].lower()


@pytest.mark.xfail(strict=False, reason="Multi-agent Hive collaboration is not yet a normal chat-level contract.")
def test_future_hive_mind_mode_can_delegate_to_multiple_helpers_and_merge_results(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "ask the hive mind to split this research into three helper lanes and merge the findings",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "3 helper lanes active" in result["response"].lower()
    assert "merged finding" in result["response"].lower()


@pytest.mark.xfail(strict=False, reason="The repo has contribution/economics primitives, but end-user chat does not yet expose a full trustless commons contract.")
def test_future_world_computer_mode_can_show_real_earned_and_spent_contribution_budget(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "show my earned hive contribution credits, what I spent, and what tasks paid out",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "earned credits" in result["response"].lower()
    assert "spent credits" in result["response"].lower()
    assert "payout history" in result["response"].lower()


@pytest.mark.xfail(strict=False, reason="Long-horizon user modeling is still shallow; the runtime stores heuristics but does not yet behave like a frontier companion model.")
def test_future_companion_mode_can_infer_user_needs_from_sparse_context(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "you know the project, just continue from where we left off",
        session_id_override="openclaw:future-companion",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "continuing the telegram bot build" in result["response"].lower()
