"""The Archived chats list stays open while the sidebar refreshes.

The sidebar re-renders on every poll, archive and pin, and each render rebuilt the Archived box
closed: a person who opened it saw it fold away under the pointer (and the served row-actions
test hovered a row that had just been hidden, Nov 21 CI).
"""
from __future__ import annotations

from tests.chat_page_js_harness import DOM, run_node, script

ROWS = "[{ session_id: 'openclaw:live-aaaaaaaaaaaa', title: 'Now' }, { session_id: 'openclaw:old-bbbbbbbbbbbbb', title: 'Old', archived: true }]"


def _drive(program: str) -> dict:
    result = run_node(DOM + script() + "\nawait Promise.resolve(); await Promise.resolve();\n" + program)
    assert not result["errors"], result
    return result


def _box_state() -> str:
    return """(() => { const kids = [...sessionsEl.children];
  const at = kids.findIndex((el) => el.className === 'arch-head');
  const head = at >= 0 ? kids[at] : null; const box = at >= 0 ? kids[at + 1] : null;
  return { open: !!box && box.style.display !== 'none', expanded: head ? head.getAttribute('aria-expanded') : '' }; })()"""


def test_an_opened_archived_list_survives_a_refresh_and_closes_when_asked() -> None:
    result = _drive(f"""
renderSessions({ROWS});
const closed = {_box_state()};
[...sessionsEl.children].find((el) => el.className === 'arch-head').click();
const opened = {_box_state()};
renderSessions({ROWS});
const refreshed = {_box_state()};
[...sessionsEl.children].find((el) => el.className === 'arch-head').click();
renderSessions({ROWS});
const closedAgain = {_box_state()};
out({{closed, opened, refreshed, closedAgain}});
""")
    assert result["closed"] == {"open": False, "expanded": "false"}
    assert result["opened"] == {"open": True, "expanded": "true"}
    assert result["refreshed"] == {"open": True, "expanded": "true"}
    assert result["closedAgain"] == {"open": False, "expanded": "false"}
