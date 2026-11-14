"""Mac AQ rows on 0a84ca8f (qwen3:8b, fresh home): the two that still fell short.

* 4a-2, after "Sure! Here's a short, easy-to-do desk stretching routine to ...": "keep that routine in my notes" saved
  the routine, titled "Sure! Here's a short, easy-to-do desk stretching routine to". The title names the routine.
* 12, Web on, "suggest a good tripod for travel and tell me what it costs today": web.search gave the model only
  short snippets of guide articles, so it listed the articles, named no tripod and gave no price. A shopping search
  reads its top hits and hands the model each page's priced lines, and a price a page states survives the reply edge.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
from tests.test_mac_aq_rows_on_cbc915ec_served_v08 import _SEED, MODEL, _executed, _mac_chat

SURE_ROUTINE = ("Sure! Here's a short, easy-to-do desk stretching routine to help you loosen up:\n"
                "1. Neck rolls, 30 seconds.\n2. Shoulder shrugs, 10 times.")
GUIDE_URL = "https://www.example-guides.test/best-travel-tripods"
GUIDE_PAGE = (
    "12 Best Travel Tripods (November 2026): Tested & Reviewed\n"
    "Travel tripods range from $20 to $600.\n"
    "1. Peak Design Travel Tripod\n"
    "$379.95\n"
    "The most compact tripod we tested, folding to the width of a water bottle.\n"
    "2. Manfrotto Befree Advanced\n"
    "Price: $219\n"
    "3. Ulanzi Zero Y\n"
    "A light carbon tripod that costs $149.99 at most stores.\n"
)


def test_a_saved_answer_is_titled_by_what_the_person_pointed_at() -> None:
    from core.operator.notes import _answer_note

    title, body = _answer_note(SURE_ROUTINE, "keep that routine in my notes")
    assert title == "Desk stretching routine", title
    assert "Neck rolls" in body
    title, _ = _answer_note("Sure! Here's your weekly meal plan:\n- Monday: soup", "yes, save it as a note")
    assert title == "Weekly meal plan", title


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    tmp_path = tmp_path_factory.mktemp("aq-0a84ca8f")

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return SURE_ROUTINE

    home = tmp_path / "home"
    provider = ScriptedProvider({MODEL: SURE_ROUTINE}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the row
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
        provider.reset()
        yield home, daemon
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def test_keep_that_routine_saves_a_note_titled_by_the_routine(served) -> None:
    home, daemon = served
    ask = "could you put together a short stretching routine I can do at my desk?"
    first = _mac_chat(daemon, ask, session_id="row4a-title")
    assert "Neck rolls" in first, first
    before = list(_executed(home))
    text = _mac_chat(daemon, "keep that routine in my notes", session_id="row4a-title",
                     history=[{"role": "user", "content": ask}, {"role": "assistant", "content": first}])

    assert _executed(home)[len(before):] == ["operator.save_note"], (text, _executed(home))
    assert "Desk stretching routine" in text, text
    assert "Sure!" not in text, text


def _search(query: str, fetched: list[dict[str, Any]], *, page: str = GUIDE_PAGE, title: str = "") -> Any:
    from core.execution.models import _tool_observation
    from core.execution.web_tools import execute_web_tool

    rows = [{"title": title or "12 Best Travel Tripods (November 2026): Tested & Reviewed", "url": GUIDE_URL,
             "snippet": "We tested travel tripods from $20 to $600."}]

    def call_tool(name: str, **kwargs: Any) -> Any:
        assert name == "web.fetch", name
        fetched.append(dict(kwargs))
        return {"status": "ok", "url": kwargs["url"], "text": page}

    return execute_web_tool(
        "web.search", {"query": query}, task_id="t-row12", source_context={"surface": "desktop"},
        allow_web_fallback_fn=lambda: True, load_builtin_tools_fn=lambda: None,
        planned_search_query_fn=lambda *a, **k: rows, call_tool_fn=call_tool, adaptive_research_fn=None,
        unsupported_execution_for_intent_fn=None, tool_observation_fn=_tool_observation, audit_log_fn=lambda *a, **k: None,
    )


def test_a_shopping_search_reads_its_top_hit_and_hands_over_the_priced_lines() -> None:
    fetched: list[dict[str, Any]] = []
    execution = _search("best travel tripod 2026 price", fetched)

    assert execution.ok, execution.response_text
    assert fetched and fetched[0]["url"] == GUIDE_URL and fetched[0].get("not_local") is True, fetched
    excerpts = execution.details["observation"]["page_excerpts"]
    lines = excerpts[0]["priced_lines"]
    assert "1. Peak Design Travel Tripod -- $379.95" in lines, lines
    assert "2. Manfrotto Befree Advanced -- Price: $219" in lines, lines
    assert "Travel tripods range from $20 to $600." in lines, lines
    assert any("Ulanzi Zero Y" in line or "$149.99" in line for line in lines), lines
    assert "Peak Design Travel Tripod -- $379.95" in execution.response_text, execution.response_text


def test_a_search_that_is_not_about_buying_reads_no_pages() -> None:
    fetched: list[dict[str, Any]] = []
    execution = _search("when did the louvre open", fetched, title="History of the Louvre")

    assert execution.ok
    assert fetched == [], fetched
    assert "page_excerpts" not in execution.details["observation"]


def test_a_price_the_read_page_states_reaches_the_reply() -> None:
    from core.agent_runtime.reply_edge import unfetched_prices_reply

    fetched: list[dict[str, Any]] = []
    observation = _search("best travel tripod 2026 price", fetched).details["observation"]
    context = {"_tool_loop_web_text": json.dumps(observation, ensure_ascii=False)}
    reply = ("For travel, the Peak Design Travel Tripod is the compact pick at $379.95. "
             "The Manfrotto Befree Advanced costs $219.")
    assert unfetched_prices_reply(reply, context, "suggest a good tripod for travel and tell me what it costs today") \
        == reply
