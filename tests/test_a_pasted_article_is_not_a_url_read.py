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
