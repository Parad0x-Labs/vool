"""vool://routine/<id> in the desktop window: exactly that shape opens the routine's list, nothing else."""
from __future__ import annotations

import pytest

from installer.bundle import vool_window


@pytest.mark.parametrize("url", [
    "vool://routine/rt-0123456789",
    "vool://routine/rt-0123456789/",
    "VOOL://ROUTINE/rt-0123456789",
    "  vool://routine/rt-abcdef0123  ",
])
def test_a_routine_link_opens_its_list(url):
    routine_id = url.strip().rstrip("/").rsplit("/", 1)[1]
    assert vool_window.routine_link_path(url) == "/routines?run=" + routine_id


@pytest.mark.parametrize("url", [
    "", "vool://routine/", "vool://routine/rt-012345678", "vool://routine/rt-01234567890", "vool://routine/rt-ABCDEF0123",
    "vool://routine/rt-0123456789?run=1", "vool://routine/rt-0123456789#x", "vool://routine/rt-0123456789;x",
    "vool://routine/../settings", "vool://routine/rt-0123456789/../x", "vool://routine//rt-0123456789",
    "vool://auth/openrouter/callback?code=a&state=b", "https://routine/rt-0123456789", "vool://routines/rt-0123456789",
    "vool://user@routine/rt-0123456789", "vool://routine:80/rt-0123456789",
])
def test_anything_else_is_not_a_routine_link(url):
    assert vool_window.routine_link_path(url) == ""


class _Window:
    def __init__(self):
        self.loaded = []
        self.shown = 0

    def load_url(self, url):
        self.loaded.append(url)

    def show(self):
        self.shown += 1

    def restore(self):
        pass


def test_the_window_opens_my_routines_for_a_routine_link():
    window = _Window()
    vool_window._deliver_vool_url("vool://routine/rt-0123456789", window)
    assert window.loaded == [vool_window._API_ORIGIN + "/routines?run=rt-0123456789"] and window.shown == 1


def test_other_links_never_load_a_page(person):
    window = _Window()
    for url in ("vool://routine/rt-0123456789?x=1", "vool://routine/../settings", "vool://something/else"):
        vool_window._deliver_vool_url(url, window)
    assert window.loaded == [] and window.shown == 0
    vool_window._deliver_vool_url("vool://routine/rt-0123456789", None)  # no window yet: ignored, no error
