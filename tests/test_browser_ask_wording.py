"""How a person's words become the browser's authority for one turn.

Mac run 3 (2026-11-11, qwen3:8b) found asks that got no card at all: a typo ("teh broswer"),
a site written without https:// ("news.ycombinator.com"), an address inside brackets that
kept the ")" and "search <site> for ...". Each is read here exactly as the turn boundary reads
it, and the words that must grant nothing still grant nothing.
"""
from __future__ import annotations

import pytest

from core.vool_browser.live_requests import addresses, requested, tool_allowed, turn_scope


def boundary(text: str):
    from core.vool_browser import live_requests

    with turn_scope(text, 'wording-test'):
        b = live_requests._CONFINED.get()
        return {'active': b.active, 'urls': set(b.urls), 'open': b.open, 'follow': b.follow,
                'fill': b.fill, 'submit': b.submit,
                'navigate': tool_allowed('vool-browser.navigate', {'url': next(iter(b.urls), 'https://other.example/')})}


@pytest.mark.parametrize('text,url', [
    ('pls opne exmaple.com in teh broswer', 'https://exmaple.com/'),
    ('open news.ycombinator.com in the browser and list the top 3 stories', 'https://news.ycombinator.com/'),
    ('Use the browser (https://duckduckgo.com/html/?q=riga) to search for Riga', 'https://duckduckgo.com/html/?q=riga'),
    ('use the brower to visit en.wikipedia.org/wiki/Riga.', 'https://en.wikipedia.org/wiki/Riga'),
    ('can you browse over to lsm.lv and tell me the headline', 'https://lsm.lv/'),
])
def test_varied_asks_name_the_address_and_may_open_it(text, url):
    seen = boundary(text)
    assert seen['active'] and seen['open'] and seen['navigate'], seen
    assert seen['urls'] == {url}


def test_search_grants_filling_and_sending_the_search_box():
    seen = boundary('use the browser to search duckduckgo.com for riga weather')
    assert seen['fill'] and seen['submit'] and seen['urls'] == {'https://duckduckgo.com/'}


def test_open_the_link_is_a_follow():
    seen = boundary('use the browser and open the link called Later trains')
    assert seen['active'] and seen['follow']


@pytest.mark.parametrize('text', [
    'check run.py and notes.md for the bug',
    'mail jane@example.com about the train',
    'what does the setup.sh script do?',
])
def test_file_names_and_email_addresses_are_not_sites(text):
    assert addresses(text) == []


@pytest.mark.parametrize('text', [
    "don't open anything, just tell me what example.com is",
    'how do I open a page in the browser?',
])
def test_words_that_ask_nothing_grant_nothing(text):
    # Not a browser turn at all: no browser tool is offered, so nothing can be proposed.
    seen = boundary(text)
    assert not requested(text) and not seen['active'] and not seen['open']
