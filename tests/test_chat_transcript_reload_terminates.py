"""Reopening a transcript must TERMINATE, whatever the assistant put in it.

`renderRichText` walks the message line by line and dispatches each line to a block branch. The
paragraph fallback at the bottom consumed "consecutive non-blank lines that started no block" --
and consumed nothing at all when the current line DID start a block that none of the branches
above it accepted. `para` came back empty, `i` never moved, and `continue` re-read the same line
forever, pushing an empty <p> on every pass.

Two ordinary replies reached it:

* `| Car | Speed |` on its own. `isTableRow` accepts it, but the table branch also demands a
  divider row underneath, so the branch declines and the line falls through as a block start.
* ```` ```json title="x" ```` -- `isBlockStart` matches any ```` ``` ````, while the fence branch
  matches only ```` ```<word chars> ```` and declines the rest.

This was found in production, not in review: a turn that answered "output the exact string
`| Car | Speed |`" poisoned the transcript, and every later reopen of that chat spun the WebKit
content process at >100% CPU until it had allocated ~12 GB. The freeze was not in the runtime --
the server answered /healthz in 5ms throughout -- so nothing server-side noticed.

The assertions here are about TERMINATION and forward progress, not about prose. Each render runs
with `Array.prototype.push` capped, so a non-terminating branch fails as a named assertion in
about a second instead of hanging the suite until the harness timeout.
"""

from __future__ import annotations

from tests.chat_page_js_harness import DOM, HTML, run_node, script

__all__ = ["HTML"]


# Every one of these is a real assistant reply shape. The first is the exact text that froze the
# shipped app; the rest are the same defect reached by a different line.
POISON = {
    "lone_table_row": "| Car | Speed |",
    "single_cell_row": "| a |",
    "two_rows_no_divider": "| x |\n| y |",
    "degenerate_pipes": "|||",
    "fence_with_attributes": '```json title="x"\n{"a": 1}\n```',
    "row_after_prose": "Here it is:\n\n| Car | Speed |",
}

# Shapes that already worked and must keep working -- otherwise "it terminates" could be bought by
# breaking the renderer into rendering nothing.
CONTROL = {
    "real_table": "| Car | Speed |\n|---|---|\n| a | b |",
    "paragraph": "hello world",
    "bullets": "- a\n- b",
    "numbered": "1. a\n2. b",
    "mixed_blocks": "# H\n\n> quote\n\n---\n\n```js\nx\n```",
}

# Cap far above any legitimate render (the controls use single digits) and far below the ~300k
# that the live defect reached before the tab became unusable.
HARNESS = """
const PUSH_CAP = 100000;
const __origPush = Array.prototype.push;
function bounded(fn) {
  let n = 0;
  Array.prototype.push = function () {
    if (++n > PUSH_CAP) { Array.prototype.push = __origPush; throw new Error('NONTERMINATING'); }
    return __origPush.apply(this, arguments);
  };
  try { const value = fn(); return { ok: true, pushes: n, value }; }
  catch (e) { return { ok: false, pushes: n, error: String((e && e.message) || e) }; }
  finally { Array.prototype.push = __origPush; }
}

const CASES = __CASES__;
const rendered = {};
for (const [name, text] of Object.entries(CASES)) {
  rendered[name] = bounded(() => {
    const el = document.createElement('div');
    renderRichText(el, text);
    return el.textContent;
  });
}

// The real reload path, not just the leaf: renderChat -> addMsg -> renderAssistantContent ->
// renderRichText, which is the exact stack the shipped freeze produced.
const reload = bounded(() => {
  const chatId = displayedChat;
  const owner = chatState(chatId);
  owner.history.length = 0;
  owner.history.push({ role: 'user', content: 'output the exact string', ts: '' });
  owner.history.push({ role: 'assistant', content: '| Car | Speed |', ts: '' });
  renderChat(chatId);
  return owner.history.length;
});

// A user gesture on that same poisoned transcript: reopening the chat re-renders it.
const gesture = bounded(() => { addMsg('assistant', '| Car | Speed |', new Date().toISOString()); return 'addMsg'; });

globalThis.__out = { rendered, reload, gesture };
_st(() => { __report(); process.exit(0); }, 200);
"""


def _drive(source: str, cases: dict[str, str]) -> dict:
    import json

    return run_node(DOM + source + HARNESS.replace("__CASES__", json.dumps(cases)))


def test_every_hostile_block_shape_terminates_and_keeps_its_text() -> None:
    data = _drive(script(), {**POISON, **CONTROL})
    assert data["errors"] == [], "the page threw while rendering:\n" + "\n".join(data["errors"])

    spun = {name: r for name, r in data["rendered"].items() if not r["ok"]}
    assert not spun, "renderRichText did not terminate on: " + ", ".join(
        f"{name} ({r['error']} after {r['pushes']} pushes)" for name, r in spun.items()
    )

    # Terminating by rendering nothing would be a regression wearing a green badge. A line the
    # branches declined must survive as literal text.
    assert "| Car | Speed |" in data["rendered"]["lone_table_row"]["value"]
    assert "| a |" in data["rendered"]["single_cell_row"]["value"]

    # The controls must still be PARSED, not dumped as literal source.
    real_table = data["rendered"]["real_table"]["value"]
    assert "|" not in real_table, f"a real table stopped parsing into cells: {real_table!r}"
    assert "Car" in real_table and "Speed" in real_table

    # Forward progress means a bounded number of blocks, not merely "finished".
    for name, result in data["rendered"].items():
        assert result["pushes"] < 500, f"{name} took {result['pushes']} pushes -- that is not linear"


def test_reopening_a_poisoned_transcript_completes() -> None:
    """The shipped failure, end to end: a stored transcript carrying the poison line is re-rendered."""
    data = _drive(script(), POISON)
    assert data["reload"]["ok"], f"renderChat never returned: {data['reload'].get('error')}"
    assert data["reload"]["value"] == 2, "the harness did not actually seed the transcript"
    assert data["gesture"]["ok"], f"addMsg never returned: {data['gesture'].get('error')}"


def test_the_termination_guard_is_what_makes_this_pass() -> None:
    """Anti-vacuity: remove the forward-progress guard and require every assertion above to bite.

    Without this, a later refactor could delete the guard and the suite above would stay green for
    some unrelated reason. Reverting the fix must reproduce the exact production hang.
    """
    source = script()
    guard = "    if (!para.length) {\n      para.push(lines[i]);\n      i += 1;\n    }\n"
    assert guard in source, "the forward-progress guard no longer has the shape this sabotage targets"

    sabotaged = source.replace(guard, "", 1)
    data = _drive(sabotaged, {**POISON, **CONTROL})

    spun = [name for name, r in data["rendered"].items() if not r["ok"]]
    assert "lone_table_row" in spun, (
        "SABOTAGE DID NOT BITE: removing the guard must make a lone table row non-terminating. "
        f"Non-terminating cases seen: {spun}"
    )
    assert "fence_with_attributes" in spun, (
        "SABOTAGE DID NOT BITE: removing the guard must make a fence with an attribute info string "
        f"non-terminating. Non-terminating cases seen: {spun}"
    )
    # The controls must stay green under sabotage, or the test is proving something far too broad.
    assert all(data["rendered"][name]["ok"] for name in CONTROL), (
        "the sabotage broke shapes it should not touch: "
        + ", ".join(name for name in CONTROL if not data["rendered"][name]["ok"])
    )
    # And the real reload path must hang too -- the leaf and the path it ships through.
    assert not data["reload"]["ok"], "SABOTAGE DID NOT BITE: renderChat completed on a poisoned transcript"
