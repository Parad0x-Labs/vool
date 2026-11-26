"""A page VOOL has read is not "page failed" because its picture took longer than three seconds.

CI on f7800a1ad (Linux runner under full load): the members page was read (address, title and text were all
there), then the picture of it took over three seconds and the whole step ended "page_failed". A busy computer
paints late: the picture gets one longer try. A picture that never comes still fails the look, and an error
that is not a slow picture is never retried.
"""
from __future__ import annotations

import asyncio

import pytest

from core.vool_browser import live


class TimeoutError(Exception):
    """The engine's own timeout carries this class name."""


class _Body:
    async def inner_text(self) -> str:
        return "Rooms open from nine"


class _Page:
    url = "http://127.0.0.1:9/rooms"

    def __init__(self, failures: list[Exception]) -> None:
        self.failures = list(failures)
        self.timeouts: list[int] = []

    async def evaluate(self, _source: str) -> dict:
        return {"nodes": [], "forms": [], "view": [800, 600]}

    def locator(self, _selector: str) -> _Body:
        return _Body()

    async def title(self) -> str:
        return "Rooms"

    async def screenshot(self, **kwargs) -> bytes:
        self.timeouts.append(kwargs["timeout"])
        if self.failures:
            raise self.failures.pop(0)
        return b"jpeg"


def _session(page: _Page) -> live.LiveSession:
    item = object.__new__(live.LiveSession)
    item._page, item.closed, item.result = page, False, {}
    item.nodes, item._forms, item.view, item.revision, item.url, item.title, item.frame = [], [], [0, 0], 0, "", "", ""
    return item


def test_a_slow_picture_gets_one_longer_try_and_the_look_stands() -> None:
    page = _Page([TimeoutError("screenshot: Timeout 3000ms exceeded")])
    item = _session(page)
    asyncio.run(item._observe_once())
    assert item.frame and item.revision == 1
    assert page.timeouts == [3000, live.TIMEOUT * 1000]
    assert "Rooms open from nine" in str(item.result["page"])


def test_a_picture_that_never_comes_still_fails_the_look() -> None:
    page = _Page([TimeoutError("first"), TimeoutError("second")])
    item = _session(page)
    with pytest.raises(TimeoutError):
        asyncio.run(item._observe_once())
    assert item.revision == 0 and len(page.timeouts) == 2


def test_another_picture_error_is_not_retried() -> None:
    page = _Page([RuntimeError("Target page, context or browser has been closed")])
    item = _session(page)
    with pytest.raises(RuntimeError):
        asyncio.run(item._observe_once())
    assert page.timeouts == [3000]
