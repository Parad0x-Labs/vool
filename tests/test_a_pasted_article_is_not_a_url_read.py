"""Links inside a text pasted below the ask are shown, not asked for.

Measured on 0.7.3 (2026-10-24, the X Article trials) and again on the 0.8 candidate 28fc78dd
(2026-11-12): "Turn this into an X article:" followed by a pasted article with links went to the
URL-read lane. The article's own words ("easy to read", "check the shelf") met the lane's read
markers, so the lane fetched the article's first link and the model never saw the article. The
address-shown rule (tests/test_an_address_shown_is_not_an_address_requested.py) already covers
quotes, backticks and fences; a long text pasted under the ask's first line is the same thing.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_web import url_read_request

ARTICLE = """# How our tool library works

Maple Court started lending tools in spring. Every loan is written in the register, so anyone can
read who has the ladder and when it comes back. Read the full autumn report at
https://library.example.org/report?period=autumn#loans before you sign up for a shift.

Volunteers check each item when it is returned: blades, cords and batteries. The checklist we use
is at https://library.example.org/checklist and it is short enough to keep on the shelf.

[PICTURE 1: shelf.png | The labelled shelf | Tools sorted by size on a wooden shelf]

We need two more people for Saturday mornings. If you can help, look at the rota on
https://example.com/rota or ask at the counter. Nobody needs to own a single tool to take part.
"""

PASTED_ASKS = (
    "Turn this into an X article:",
    "Prepare an X Article from this draft, keep every link:",
    "read this article and make it shorter:",
    "check the grammar in this post please",
    "Summarise the article below in three bullets",
)


@pytest.mark.parametrize("ask", PASTED_ASKS)
def test_links_in_a_pasted_article_are_not_read_by_the_url_lane(ask):
    assert len(ARTICLE) >= 600
    assert url_read_request(ask + "\n\n" + ARTICLE) is None
    assert url_read_request(ask + "\n" + ARTICLE) is None


@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("read this:\nhttps://example.com/post", "https://example.com/post"),
        ("summarise these two pages:\nhttps://example.com/a\nhttps://example.com/b", "https://example.com/a"),
        ("what does https://example.com/news say?", "https://example.com/news"),
    ],
)
def test_an_address_under_a_short_ask_is_still_read(text, url):
    assert url_read_request(text) == {"url": url}


def test_an_address_on_the_ask_line_is_still_read():
    """The pasted text below does not take the address the ask itself names."""
    text = "read https://example.com/notes and then tidy this list:\n" + "\n".join(
        f"- item {n}: something to keep or throw away" for n in range(30)
    )
    assert len(text) >= 600
    assert url_read_request(text) == {"url": "https://example.com/notes"}


@pytest.mark.parametrize("ask", PASTED_ASKS)
def test_the_lane_reads_the_paste_as_written_not_as_the_front_door_flattens_it(ask, monkeypatch):
    """The front door hands the lane the NORMALIZED turn, one line with the paste run into the ask.

    Measured served on the v0.8 round 6 candidate (11eef8dc), once no planner split the turn first:
    the lane fetched the article's first link from that line and the model never saw the article.
    """
    from core.agent_runtime import fast_paths_web
    from core.human_input_adapter import adapt_user_input

    turn = ask + "\n\n" + ARTICLE
    interpreted = adapt_user_input(turn, session_id="pasted-article-url", persist=False)
    flattened = str(interpreted.normalized_text)
    assert "\n" not in flattened.strip()
    fetched: list[object] = []
    monkeypatch.setattr("core.vool_browser.live_chat.refuse_if_off", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "core.runtime_execution_tools.execute_runtime_tool", lambda *args, **_k: fetched.append(args)
    )
    result = fast_paths_web.maybe_handle_url_read(
        object(), flattened, session_id="pasted-article-url", source_context={}, interpretation=interpreted
    )
    assert result is None
    assert fetched == []


def test_an_address_the_ask_names_is_still_fetched_from_the_flattened_turn(monkeypatch):
    from core.agent_runtime import fast_paths_web
    from core.human_input_adapter import adapt_user_input

    turn = "read https://example.com/notes and then tidy this list:\n" + "\n".join(
        f"- item {n}: something to keep or throw away" for n in range(30)
    )
    interpreted = adapt_user_input(turn, session_id="pasted-article-url", persist=False)
    fetched: list[object] = []
    monkeypatch.setattr("core.vool_browser.live_chat.refuse_if_off", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "core.runtime_execution_tools.execute_runtime_tool", lambda *args, **_k: fetched.append(args)
    )
    fast_paths_web.maybe_handle_url_read(
        object(), str(interpreted.normalized_text), session_id="pasted-article-url", source_context={},
        interpretation=interpreted,
    )
    assert fetched == [("web.fetch", {"url": "https://example.com/notes"})]


@pytest.mark.parametrize("ask", PASTED_ASKS)
def test_an_answer_over_a_pasted_article_is_not_a_claim_about_an_unfetched_page(ask):
    """The grounding backstop reads the paste the way the lane does.

    Measured served on 11eef8dc: the x-article-builder answer over the pasted article was replaced
    by "You linked a URL and I did not open it on this turn", the paste's own "check" meeting the
    backstop's review words.
    """
    from core.agent_runtime.action_honesty_validator import (
        enforce_url_grounding,
        response_describes_unfetched_url,
    )

    turn = ask + "\n\n" + ARTICLE
    answer = "Maple Court's tool library: every loan in the register, two more volunteers wanted."
    assert response_describes_unfetched_url(user_input=turn, response=answer, fetch_attempts=0) is False
    assert enforce_url_grounding({"response": answer}, user_input=turn, fetch_attempts=0)["response"] == answer


@pytest.mark.parametrize(
    "turn",
    [
        "check https://example.com/notes and tidy this list:\n"
        + "\n".join(f"- item {n}: something to keep or throw away" for n in range(30)),
        "summarise these two pages:\nhttps://example.com/a\nhttps://example.com/b",
        "is https://example.com/news any good?",
    ],
)
def test_an_answer_about_an_address_the_ask_names_still_needs_the_fetch(turn):
    from core.agent_runtime.action_honesty_validator import response_describes_unfetched_url

    assert response_describes_unfetched_url(user_input=turn, response="The page says hello.", fetch_attempts=0) is True
