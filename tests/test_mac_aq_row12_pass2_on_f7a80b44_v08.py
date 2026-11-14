"""Mac AQ rerun on f7a80b44 (qwen3:8b, fresh home): row 12 and 4a-2 still fell short.

* Row 12, Web on, "suggest a good tripod for travel and tell me what it costs today": the shopping search read the
  wrong pages (Wikipedia "Tripod (web hosting)" gave the priced line "US$4 million venture capital"), the reply named
  the "Manfrotto MT055XPRO3 Travel Light Carbon Tripod", which no fetched page names, and left "2. This price is based
  on the latest reviews" with no price before it.
* 4a-2: the earlier turn was the certification stop ("I can't publish this answer: ..."), and "keep that routine in
  my notes" saved that stop as the note and said it was saved.
"""
from __future__ import annotations

import json
from typing import Any

ASK = "suggest a good tripod for travel and tell me what it costs today"
WIKI = {"title": "Tripod (web hosting) - Wikipedia", "url": "https://en.wikipedia.test/wiki/Tripod_(web_hosting)",
        "snippet": "Tripod.com is a web hosting service founded in 1992."}
NOVELS = {"title": "The Tripods - Wikipedia", "url": "https://en.wikipedia.test/wiki/The_Tripods",
          "snippet": "The Tripods is a series of young adult novels."}
GUIDE = {"title": "12 Best Travel Tripods (November 2026)", "url": "https://guides.example.test/best-travel-tripods",
         "snippet": "We tested travel tripods from $20 to $600."}
PAGES = {
    WIKI["url"]: "Tripod (web hosting)\nIn 1995 Tripod raised US$4 million venture capital.\nPlans cost $5 a month.\n",
    NOVELS["url"]: "The Tripods\nThe first novel sold for $3.95 in 1967.\n",
    GUIDE["url"]: "12 Best Travel Tripods\n1. Peak Design Travel Tripod\n$379.95\n2. Manfrotto Befree Advanced\nPrice: $219\n",
}


def _search(query: str, rows: list[dict[str, str]], fetched: list[str]) -> Any:
    from core.execution.models import _tool_observation
    from core.execution.web_tools import execute_web_tool

    def call_tool(name: str, **kwargs: Any) -> Any:
        assert name == "web.fetch", name
        fetched.append(kwargs["url"])
        return {"status": "ok", "url": kwargs["url"], "text": PAGES[kwargs["url"]]}

    return execute_web_tool(
        "web.search", {"query": query, "limit": 4}, task_id="t-row12b", source_context={"surface": "desktop"},
        allow_web_fallback_fn=lambda: True, load_builtin_tools_fn=lambda: None,
        planned_search_query_fn=lambda *a, **k: [dict(row) for row in rows], call_tool_fn=call_tool,
        adaptive_research_fn=None, unsupported_execution_for_intent_fn=None, tool_observation_fn=_tool_observation,
        audit_log_fn=lambda *a, **k: None,
    )


def test_a_shopping_search_reads_the_guide_not_the_off_topic_hits() -> None:
    fetched: list[str] = []
    execution = _search("best travel tripod 2026 price", [WIKI, NOVELS, GUIDE], fetched)

    assert fetched == [GUIDE["url"]], fetched
    lines = [line for page in execution.details["observation"]["page_excerpts"] for line in page["priced_lines"]]
    assert "1. Peak Design Travel Tripod -- $379.95" in lines, lines
    assert not any("million" in line or "$5 a month" in line or "$3.95" in line for line in lines), lines


def test_a_line_about_millions_is_not_a_price() -> None:
    from core.execution.web_tools import priced_page_lines

    assert priced_page_lines("Tripod raised US$4 million venture capital.\nThe Zero Y costs $149.99.") == [
        "The Zero Y costs $149.99."
    ]


def _edge(reply: str, request: str = ASK) -> str:
    from core.agent_runtime.reply_edge import unfetched_prices_reply

    fetched: list[str] = []
    observation = _search("best travel tripod 2026 price", [WIKI, NOVELS, GUIDE], fetched).details["observation"]
    return unfetched_prices_reply(reply, {"_tool_loop_web_text": json.dumps(observation, ensure_ascii=False)}, request)


def test_a_model_no_page_names_goes_and_so_does_the_line_leaning_on_its_price() -> None:
    from core.agent_runtime.reply_edge import UNFETCHED_PRICES_NAMES_LINE

    reply = ("Based on the latest information, one of the recommended travel tripods for 2026 is the **Manfrotto "
             "MT055XPRO3 Travel Light Carbon Tripod**.\n1. It sells for about $329.\n2. This price is based on the "
             "latest reviews and may vary by retailer.\nFor a lighter pick, the Peak Design Travel Tripod costs $379.95.")
    text = _edge(reply)

    assert "MT055XPRO3" not in text and "Manfrotto MT055" not in text, text
    assert "This price is based" not in text and "$329" not in text, text
    assert "\n2." not in text and not text.startswith("2."), text
    assert "Peak Design Travel Tripod costs $379.95" in text, text
    assert text.endswith(UNFETCHED_PRICES_NAMES_LINE), text


def test_a_reply_the_pages_back_is_left_alone() -> None:
    reply = "The Peak Design Travel Tripod costs $379.95, and the Manfrotto Befree Advanced is $219."
    assert _edge(reply) == reply


def test_a_model_code_outside_a_shopping_ask_is_left_alone() -> None:
    reply = "The A320neo first flew in 2014."
    assert _edge(reply, request="tell me about the airbus family") == reply


def test_a_stop_message_is_not_what_keep_that_routine_saves(monkeypatch) -> None:
    import core.chat_housekeeping as housekeeping
    from core.operator.models import OperatorActionIntent
    from core.operator.notes import with_the_answer_it_follows

    stop = ("I can't publish this answer: the model chosen to write answers here (qwen3:8b) hasn't passed VOOL's "
            "answer check yet.")
    monkeypatch.setattr(housekeeping, "newest_assistant_message",
                        lambda session_id, source_context=None: {"role": "assistant", "text": stop, "ts": ""})
    intent = OperatorActionIntent(kind="save_note", raw_text="keep that routine in my notes")
    assert with_the_answer_it_follows(intent, session_id="s-stop").note_body is None

    routine = "Here's a short desk stretching routine:\n1. Neck rolls, 30 seconds."
    monkeypatch.setattr(housekeeping, "newest_assistant_message",
                        lambda session_id, source_context=None: {"role": "assistant", "text": routine, "ts": ""})
    saved = with_the_answer_it_follows(intent, session_id="s-stop")
    assert saved.note_body and "Neck rolls" in saved.note_body
    assert saved.target_label == "Desk stretching routine", saved.target_label


def test_a_question_back_is_not_what_keep_that_routine_saves(monkeypatch) -> None:
    import core.chat_housekeeping as housekeeping
    from core.entity_ambiguity import render_unresolved_clarification
    from core.operator.models import OperatorActionIntent
    from core.operator.notes import with_the_answer_it_follows

    intent = OperatorActionIntent(kind="save_note", raw_text="keep that routine in my notes")
    for asked in (render_unresolved_clarification(ROUTINE_ASK, attempts=2), "Which desk do you mean?"):
        monkeypatch.setattr(housekeeping, "newest_assistant_message",
                            lambda session_id, source_context=None, text=asked: {"role": "assistant", "text": text})
        assert with_the_answer_it_follows(intent, session_id="s-ask").note_body is None, asked


ROUTINE_ASK = "could you put together a short stretching routine I can do at my desk?"


def test_a_check_that_cannot_finish_does_not_turn_a_routine_ask_into_a_question(tmp_path) -> None:
    import pytest

    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
    from tests._blackbox_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, run_in_home
    from tests.test_mac_aq_rows_on_cbc915ec_served_v08 import _SEED, MODEL, _mac_chat

    routine = "Here's a short desk stretching routine:\n1. Neck rolls, 30 seconds.\n2. Shoulder shrugs, 10 times."
    probed: list[int] = []

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            probed.append(1)
            return "the check could not finish"  # no verdict, as a probe that ran out of time leaves it
        return routine

    home = tmp_path / "home"
    provider = ScriptedProvider({MODEL: routine}, reply_fn=reply_fn)
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
        text = _mac_chat(daemon, ROUTINE_ASK, session_id="row4a-probe")
        springfield = _mac_chat(daemon, "what is the population of Springfield?", session_id="row4a-probe-control")
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)

    assert "Neck rolls" in text, text
    assert "Which place, person or thing" not in text, text
    # The probe still runs, and still asks when it can't finish, for a question that names a place.
    assert probed and "Which place, person or thing" in springfield, springfield
