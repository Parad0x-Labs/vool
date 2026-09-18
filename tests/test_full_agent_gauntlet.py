"""Full-agent gauntlet regression suite (T-transcript). Each test reproduces a live failure at the
deterministic routing/tool layer so it can never silently regress. Live integration is proven
separately against the dev server; these lock the logic."""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_news_rendering import render_news_response
from core.agent_runtime.fast_live_info_news_topic import extract_news_topic
from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request
from core.execution.constants import (
    elliptical_drive_followup,
    largest_kind,
    machine_diagnostics_intent,
    machine_disk_scope,
    machine_folder_search_intent,
    machine_largest_intent,
    resolve_drive_scope,
)
from core.task_router import evaluate_direct_math_request


@pytest.mark.parametrize(
    "prompt",
    [
        "what's my total free space",
        "how much free space do I have",
        "how much storage is left",
    ],
)
def test_issue5_natural_disk_space_phrasings_route_to_disk_tool(prompt: str) -> None:
    assert machine_diagnostics_intent(prompt) == "machine.disk_usage"


def test_issue5_taking_up_most_space_routes_to_largest_files() -> None:
    assert machine_largest_intent("what's taking up the most space on C") == "machine.find_largest"


@pytest.mark.parametrize(
    "prompt",
    [
        "biggest movie this year",
        "write a poem about the ocean",
        "delete the last sentence",
        "what is the largest planet",
        "build a case for remote work",
        "free up space in my schedule",
        "is there any free space in my calendar today",
        "how much space is left in my cloud plan",
        "how much storage does my iCloud plan give me",
        "how much storage should a photo app offer",
        "compare cloud storage providers",
        "this plan is 50 times better",
        "which plan is 100 / 4 better for",
        "read the file config.json",
        "create a file notes.txt with exactly this content: hi",
        "show me pyproject.toml",
    ],
)
def test_issue5_negative_controls_do_not_fire_deterministic_tool_detectors(prompt: str) -> None:
    assert machine_diagnostics_intent(prompt) is None
    assert machine_largest_intent(prompt) is None
    assert machine_folder_search_intent(prompt) is None
    assert evaluate_direct_math_request(prompt) is None
    assert looks_like_agentic_build_request(prompt) is False


# --- T011: a market/direction news question is about the ENTITY, not the direction clause ---
def test_t011_direction_question_extracts_the_subject() -> None:
    assert extract_news_topic("From the latest news, is oil going up or down and why?") == "oil"
    assert extract_news_topic("is Solana going up or down?") == "Solana"
    assert extract_news_topic("are interest rates rising?") == "interest rates"


def test_news_topic_simple_queries_unaffected() -> None:
    assert extract_news_topic("latest on Iran news") == "Iran"
    assert extract_news_topic("whats the latest on Tesla") == "Tesla"


# --- Installed-app feedback: "total space" disk queries route to the disk tool (D:, not operator C:) ---
def test_total_space_on_named_drive_routes_to_disk_tool() -> None:
    assert machine_diagnostics_intent("what is my D drive total space?") == "machine.disk_usage"
    assert machine_disk_scope("what is my D drive total space?") == "D:\\"
    assert machine_diagnostics_intent("total space used on C:") == "machine.disk_usage"


# --- Installed-app feedback: "100x50" arithmetic is deterministic (x/× = multiply), not a slow model call ---
def test_arithmetic_handles_x_and_multiplication_sign() -> None:
    assert evaluate_direct_math_request("100x50") == "100*50 = 5000."
    assert evaluate_direct_math_request("100 x 50") == "100*50 = 5000."
    assert evaluate_direct_math_request("100×50") == "100*50 = 5000."
    # A stray 'x' that is not between digits stays out of the math path.
    assert evaluate_direct_math_request("free space on C: x") is None


# --- T003 / T005: "free space on <drive>" is a disk question even without a disk noun ---
def test_t003_free_space_on_named_drive_routes_to_disk_tool() -> None:
    assert machine_diagnostics_intent("Tell me free space on C:") == "machine.disk_usage"
    assert machine_disk_scope("Tell me free space on C:") == "C:\\"


def test_t005_free_space_on_missing_drive_still_routes_to_the_real_tool() -> None:
    # Must run the real check (which then honestly reports absent), never fall to a fabricated model reply.
    assert machine_diagnostics_intent("Tell me free space on E:") == "machine.disk_usage"
    assert machine_disk_scope("Tell me free space on E:") == "E:\\"


def test_free_space_metaphor_without_a_drive_does_not_route() -> None:
    # The fix must NOT over-trigger: no drive scope + no disk noun -> stays a model/chat answer.
    assert machine_diagnostics_intent("is there any free space in my calendar today?") is None
    assert machine_diagnostics_intent("how much space is left in my cloud plan?") is None


def test_free_up_space_is_a_cleanup_write_not_a_read() -> None:
    # "free up space on C:" is a mutating/cleanup intent -> operator lane, not the read tool.
    assert machine_diagnostics_intent("free up space on C:") is None


# --- T004: D: disk read then "biggest single file on that drive" resolves to D:, files only ---
def test_t004_followup_anaphora_and_file_kind() -> None:
    # After a disk read scoped to D:, the follow-up resolves "that drive" -> D: and asks for FILES.
    assert resolve_drive_scope("what is the biggest single file on that drive?", last_drive="D:\\") == "D:\\"
    assert largest_kind("what is the biggest single file on that drive?") == "files"
    assert machine_largest_intent("what is the biggest single file on that drive?") == "machine.find_largest"


# --- T006: local folder search, not drive inventory / web ---
def test_t006_dropbox_routes_to_folder_search() -> None:
    hit = machine_folder_search_intent("Find my Dropbox folder on this PC.")
    assert hit is not None and hit[0] == "machine.find_folder"
    assert machine_diagnostics_intent("Find my Dropbox folder on this PC.") is None  # not a disk query


# --- T007: largest folders on C: ---
def test_t007_largest_folders_routes_and_is_folder_kind() -> None:
    assert machine_largest_intent("What are the five largest folders on C:?") == "machine.find_largest"
    assert largest_kind("What are the five largest folders on C:?") == "folders"


# --- T004 elliptical bare-drive follow-up remains tight ---
def test_elliptical_followup_only_matches_bare_drive() -> None:
    assert elliptical_drive_followup("ok what about D?") == "D:\\"
    assert elliptical_drive_followup("what about the weather?") is None


# --- Round-2: natural-language arithmetic must hit the deterministic calculator (never the slow
# model), so a greeting/article/spaced "x" phrasing is answered instantly like the bare form. This is
# the "cheating/prescripted" complaint: the inconsistency (some instant, some slow) is what looked fake.
def test_natural_language_arithmetic_is_deterministic() -> None:
    assert evaluate_direct_math_request("Hi, what is the 100 x 50?") == "100*50 = 5000."
    assert evaluate_direct_math_request("what is the 100 x 50?") == "100*50 = 5000."
    assert evaluate_direct_math_request("ok what is 100 x 50") == "100*50 = 5000."
    assert evaluate_direct_math_request("50x100") == "50*100 = 5000."
    assert evaluate_direct_math_request("what is 50 times 50") == "50 * 50 = 2500."
    assert evaluate_direct_math_request("whats the value of 7*8") == "7*8 = 56."
    # A non-math sentence with an "x" is never mangled into multiplication.
    assert evaluate_direct_math_request("what is the box office for the new film") is None


# --- Round-2: "latest news and trends on <X>" reduces to the entity, not "and trends on X". ---
def test_news_topic_strips_and_trends_on() -> None:
    assert extract_news_topic("what is the latest news and trends on BTC?") == "BTC"
    assert extract_news_topic("latest news and trends on BTC") == "BTC"
    assert extract_news_topic("news and analysis on solana") == "solana"
    # Real subjects that merely contain a framing word are preserved.
    assert extract_news_topic("news about the war in ukraine") == "the war in ukraine"
    assert extract_news_topic("News Corp latest") == "News Corp"


# --- Round-2: each news source is a short clickable markdown link, not a raw redirect URL. ---
def test_news_render_embeds_link_in_short_source_name() -> None:
    notes = [
        {
            "summary": "CaptainAltcoin | 2026-07-15 | Bitcoin surges past a new high",
            "result_url": "https://news.google.com/rss/articles/CBMiXlongredirecttoken",
            "origin_domain": "captainaltcoin.com",
        }
    ]
    out = render_news_response(query="what is the latest news and trends on BTC?", notes=notes)
    assert out.splitlines()[0] == "Latest coverage on BTC:"
    # The link is embedded in the short source name; the raw URL is not appended in brackets.
    assert "[CaptainAltcoin](https://news.google.com/rss/articles/CBMiXlongredirecttoken)" in out
    assert " [https://" not in out
