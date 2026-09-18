"""Regression tests for the queued-jobs chat UI density/collapse fix.

The composer's `#queue` strip used to grow one `.qchip` per pending item with no cap, inside a
`flex-wrap:wrap` ROW -- which could pack two short descriptions ("hi", "hello") onto the same
visual row instead of giving each job its own row, and let a long queue push the composer down
without bound. This drives the REAL `renderQueue()` under node against a DOM shim so density
behavior (collapse threshold, expand/collapse, ordering, which job a click targets) is proven by
actually executing it, not by grepping the source for the right words. The one property node's
DOM shim cannot observe -- real box-model layout -- is instead pinned at the CSS source level
(`test_queue_container_lays_out_one_chip_per_row_by_css`).

Scope: presentation only. Nothing here touches `core/runtime_continuity.py`, the `/api/chat/queue`
endpoint, or queue order/dispatch/persistence -- see tests/test_message_queue.py for that layer.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.chat_page_js_harness import DOM, HTML, run_node, script


def _item(qid: str, text: str, status: str = "pending") -> dict:
    return {"status": status, "queue_item_id": qid, "payload": {"text": text}}


def _run(items: list[dict], extra_js: str = "", *, source: str | None = None) -> dict:
    """Execute renderQueue(items) under node, optionally clicking controls via extra_js first,
    and return a JSON-serializable snapshot of the #queue DOM plus every fetch call observed.

    The page itself fires one unawaited `refreshQueue()` at boot (its own initial load), which
    resolves asynchronously via the DOM harness's stub fetch and would call renderQueue([]) AFTER
    our own renderQueue(items) if we called it synchronously -- silently wiping the queue we just
    populated. Everything that touches queueEl is deferred inside the same _st(...) callback,
    scheduled well after that boot microtask chain has settled, so nothing else can race it.
    """
    src = source if source is not None else script()
    program = (
        DOM
        + src
        + """
const __calls = [];
const __origFetch = globalThis.fetch;
globalThis.fetch = async (url, opts) => {
  __calls.push({ url: String(url), body: (opts && opts.body) ? JSON.parse(opts.body) : null });
  return __origFetch(url, opts);
};
function __snap() {
  return {
    display: queueEl.style.display,
    children: queueEl.children.map(function (c) {
      return {
        cls: c.className,
        text: c.textContent,
        title: (c.children[0] && c.children[0].title) || '',
        hasCancel: c.children.some(function (cc) { return cc.className === 'qchip-x'; }),
        ariaExpanded: c.getAttribute('aria-expanded'),
      };
    }),
  };
}
_st(function () {
  const items = """
        + json.dumps(items)
        + """;
  renderQueue(items);
"""
        + extra_js
        + """
  out({ snap: __snap(), calls: __calls });
  process.exit(0);
}, 60);
"""
    )
    return run_node(program)


CLICK_LAST = "queueEl.children[queueEl.children.length - 1].__click();\n"


def _assert_collapsed_two_plus_one(snap: dict) -> None:
    assert len(snap["children"]) == 3
    assert "qchip-more" in snap["children"][2]["cls"]
    assert snap["children"][2]["text"] == "+1 queued"


# ---- required cases: 0 / 1 / 2 / 3 / 10+ queued ----------------------------------------------


def test_zero_queued_hides_the_strip() -> None:
    data = _run([])
    assert data["errors"] == []
    assert data["snap"]["display"] == "none"
    assert data["snap"]["children"] == []


def test_one_queued_renders_a_single_row_no_toggle() -> None:
    data = _run([_item("q1", "buy milk")])
    assert data["errors"] == []
    snap = data["snap"]
    assert snap["display"] == "flex"
    assert len(snap["children"]) == 1
    assert "Queued 1: buy milk" in snap["children"][0]["text"]
    assert snap["children"][0]["hasCancel"] is True
    assert not any("qchip-more" in c["cls"] for c in snap["children"])


def test_two_queued_is_the_boundary_both_show_no_toggle() -> None:
    data = _run([_item("q1", "one"), _item("q2", "two")])
    snap = data["snap"]
    assert len(snap["children"]) == 2
    assert "Queued 1: one" in snap["children"][0]["text"]
    assert "Queued 2: two" in snap["children"][1]["text"]
    assert not any("qchip-more" in c["cls"] for c in snap["children"])


def test_three_queued_collapses_to_two_plus_one_summary() -> None:
    data = _run([_item("q1", "one"), _item("q2", "two"), _item("q3", "three")])
    _assert_collapsed_two_plus_one(data["snap"])
    assert "Queued 1: one" in data["snap"]["children"][0]["text"]
    assert "Queued 2: two" in data["snap"]["children"][1]["text"]
    # the third job's own row is never painted while collapsed
    assert not any("three" in c["text"] for c in data["snap"]["children"])


def test_ten_plus_queued_collapses_behind_a_single_summary_chip() -> None:
    items = [_item(f"q{i}", f"job {i}") for i in range(1, 13)]  # 12 items
    data = _run(items)
    snap = data["snap"]
    assert len(snap["children"]) == 3  # 2 visible + 1 toggle, never one row per item
    assert snap["children"][2]["text"] == "+10 queued"


# ---- one-job-per-row, regardless of description length ---------------------------------------


def test_short_descriptions_each_get_their_own_row_not_packed_together() -> None:
    data = _run([_item("q1", "hi"), _item("q2", "hello")])
    snap = data["snap"]
    assert len(snap["children"]) == 2
    texts = [c["text"] for c in snap["children"]]
    assert any(t.startswith("Queued 1: hi") for t in texts)
    assert any(t.startswith("Queued 2: hello") for t in texts)
    assert not any(("hi" in t and "hello" in t) for t in texts)  # never merged into one chip


def test_very_long_description_stays_a_single_row_with_full_text_preserved() -> None:
    long_text = "investigate the intermittent timeout in the nightly export job " * 5
    data = _run([_item("q1", long_text), _item("q2", "short")])
    snap = data["snap"]
    assert len(snap["children"]) == 2  # long text never splits its job across rows
    assert snap["children"][0]["title"] == long_text  # untruncated source of truth


def test_queue_container_lays_out_one_chip_per_row_by_css() -> None:
    m = re.search(r"#queue\s*\{([^}]*)\}", HTML)
    assert m, "no #queue rule found in the rendered page"
    rule = m.group(1).replace(" ", "")
    assert "flex-direction:column" in rule, (
        "#queue must stack chips in a column -- a wrapping ROW is what let a short chip leave "
        "room for a second one beside it"
    )
    assert "flex-wrap:wrap" not in rule


# ---- expand / collapse ------------------------------------------------------------------------


def test_expand_reveals_every_job_in_original_order_with_correct_numbering() -> None:
    items = [_item(f"q{i}", f"job-{i}") for i in range(1, 6)]  # 5 items, 3 initially hidden
    data = _run(items, CLICK_LAST)
    snap = data["snap"]
    assert len(snap["children"]) == 6  # 5 jobs + "Show less"
    for i in range(5):
        assert f"Queued {i + 1}: job-{i + 1}" in snap["children"][i]["text"]
    less = snap["children"][5]
    assert "qchip-more" in less["cls"]
    assert less["text"] == "Show less"
    assert less["ariaExpanded"] == "true"


def test_collapse_again_after_expand_returns_to_the_two_plus_summary() -> None:
    items = [_item(f"q{i}", f"job-{i}") for i in range(1, 6)]
    data = _run(items, CLICK_LAST + CLICK_LAST)  # expand, then collapse
    snap = data["snap"]
    assert len(snap["children"]) == 3  # back to 2 + "+3 queued"
    assert snap["children"][2]["text"] == "+3 queued"
    assert snap["children"][2]["ariaExpanded"] == "false"


def test_shrinking_back_under_the_limit_drops_the_toggle_entirely() -> None:
    # A queue that grows past 2 and later shrinks back to 2 (e.g. jobs completed/cancelled
    # server-side) must not leave a stale "+0 queued" chip sitting around.
    grown = [_item(f"q{i}", f"job-{i}") for i in range(1, 4)]  # 3 -> collapses
    data_grown = _run(grown)
    assert len(data_grown["snap"]["children"]) == 3

    shrunk = grown[:2]
    data_shrunk = _run(shrunk)  # fresh render call, as refreshQueue would issue after a poll
    snap = data_shrunk["snap"]
    assert len(snap["children"]) == 2
    assert not any("qchip-more" in c["cls"] for c in snap["children"])


# ---- ordering + correct-target actions ---------------------------------------------------------


def test_order_is_whatever_the_array_order_is_never_resorted() -> None:
    # 3 items so the third would be hidden behind the collapse toggle by default -- expand to
    # confirm the collapse never reshuffles anything, it only defers painting.
    items = [_item("zzz", "zebra task"), _item("aaa", "aardvark task"), _item("mmm", "middle task")]
    data = _run(items, CLICK_LAST)
    snap = data["snap"]
    assert len(snap["children"]) == 4  # 3 jobs + "Show less"
    assert "zebra" in snap["children"][0]["text"]
    assert "aardvark" in snap["children"][1]["text"]
    assert "middle" in snap["children"][2]["text"]


def test_cancel_on_an_expanded_job_targets_its_own_queue_item_id() -> None:
    items = [_item(f"q{i}", f"job-{i}") for i in range(1, 6)]  # 5 items
    extra = (
        CLICK_LAST  # expand -> 5 chips + "Show less"
        + "queueEl.children[3].children.find(function (cc) { return cc.className === 'qchip-x'; }).__click();\n"
    )
    data = _run(items, extra)
    assert data["errors"] == []
    cancel_calls = [c for c in data["calls"] if c["body"] and c["body"].get("op") == "cancel"]
    assert len(cancel_calls) == 1
    assert cancel_calls[0]["body"]["queue_item_id"] == "q4"  # the 4th job, not a shifted index


def test_cancel_on_the_second_visible_job_while_collapsed_targets_correctly() -> None:
    # Row 2 is still visible while collapsed (limit is 2) -- confirm its cancel isn't confused
    # with the hidden row(s) behind the "+N queued" toggle.
    items = [_item(f"q{i}", f"job-{i}") for i in range(1, 5)]  # 4 items, collapsed to 2 + toggle
    extra = "queueEl.children[1].children.find(function (cc) { return cc.className === 'qchip-x'; }).__click();\n"
    data = _run(items, extra)
    cancel_calls = [c for c in data["calls"] if c["body"] and c["body"].get("op") == "cancel"]
    assert len(cancel_calls) == 1
    assert cancel_calls[0]["body"]["queue_item_id"] == "q2"


# ---- running job + negative controls -----------------------------------------------------------


def test_running_plus_one_pending_is_two_total_no_toggle() -> None:
    data = _run([_item("q1", "in progress", "in_flight"), _item("q2", "next up", "pending")])
    snap = data["snap"]
    assert len(snap["children"]) == 2
    assert "Running: in progress" in snap["children"][0]["text"]
    assert snap["children"][0]["hasCancel"] is False  # the active job has no cancel control
    assert "Queued 1: next up" in snap["children"][1]["text"]  # numbering skips the running slot


def test_running_job_stays_visible_even_behind_a_long_queue() -> None:
    items = [_item("r1", "in progress", "in_flight")] + [
        _item(f"q{i}", f"job-{i}", "pending") for i in range(1, 6)
    ]
    data = _run(items)
    snap = data["snap"]
    assert len(snap["children"]) == 3  # running + 1 pending + toggle
    assert "Running: in progress" in snap["children"][0]["text"]
    assert "Queued 1: job-1" in snap["children"][1]["text"]
    assert snap["children"][2]["text"] == "+4 queued"


def test_non_active_statuses_are_excluded_from_both_count_and_render() -> None:
    items = [
        _item("q1", "keep me pending", "pending"),
        _item("q2", "already done", "completed"),
        _item("q3", "was cancelled", "cancelled"),
        _item("q4", "failed earlier", "failed"),
        _item("q5", "keep me too", "pending"),
    ]
    data = _run(items)
    snap = data["snap"]
    assert len(snap["children"]) == 2  # only the two pending ones, no toggle needed
    assert "keep me pending" in snap["children"][0]["text"]
    assert "keep me too" in snap["children"][1]["text"]


# ---- adversarial near-miss + sloppy text ---------------------------------------------------


def test_adversarial_description_that_mimics_the_toggle_label_is_rendered_inert() -> None:
    items = [_item("q1", "+7 queued"), _item("q2", "Show less"), _item("q3", "job-3")]
    data = _run(items)
    snap = data["snap"]
    assert len(snap["children"]) == 3  # 2 visible jobs + our real "+1 queued" toggle
    assert "Queued 1: +7 queued" in snap["children"][0]["text"]
    assert "qchip-more" not in snap["children"][0]["cls"]  # still a real job chip
    assert "qchip-more" not in snap["children"][1]["cls"]
    assert snap["children"][2]["text"] == "+1 queued"  # our real toggle, not confused by the text


def test_messy_description_text_survives_intact_one_row_each() -> None:
    items = [
        _item("q1", ""),
        _item("q2", "   "),
        _item("q3", "<script>alert(1)</script>"),
        _item("q4", "emoji \U0001f525\U0001f680 party"),
        _item("q5", "line1\nline2\ttabbed"),
    ]
    data = _run(items, CLICK_LAST)  # expand to see all 5
    assert data["errors"] == []
    snap = data["snap"]
    assert len(snap["children"]) == 6  # 5 jobs + "Show less"
    assert "\U0001f525\U0001f680" in snap["children"][3]["text"]
    assert "<script>" in snap["children"][2]["title"]  # literal text, never executed


# ---- sabotage: prove the tests above actually bite --------------------------------------------


def test_sabotage_reverting_queue_to_a_wrapping_row_is_caught_by_the_css_layout_test() -> None:
    """Reintroduce the exact bug ("hi" + "hello" sharing a row) and require the CSS test to fail."""
    marker = "#queue { display:none; flex-direction:column; align-items:flex-start; gap:6px; }"
    assert marker in HTML, "the #queue rule text has moved -- update this sabotage target"
    sabotaged = HTML.replace(marker, "#queue { display:none; flex-wrap:wrap; gap:6px; }", 1)
    m = re.search(r"#queue\s*\{([^}]*)\}", sabotaged)
    rule = m.group(1).replace(" ", "")
    still_one_per_row = "flex-direction:column" in rule and "flex-wrap:wrap" not in rule
    assert not still_one_per_row, "SABOTAGE DID NOT BITE: the wrapping-row regression must be caught"


def test_sabotage_removing_the_collapse_threshold_breaks_the_density_test() -> None:
    """Widen QUEUE_VISIBLE_LIMIT so nothing ever collapses; the 3-item density test must fail."""
    source = script()
    marker = "const QUEUE_VISIBLE_LIMIT = 2;"
    assert marker in source, "QUEUE_VISIBLE_LIMIT no longer has the shape this sabotage targets"
    sabotaged = source.replace(marker, "const QUEUE_VISIBLE_LIMIT = 999;", 1)
    items = [_item("q1", "one"), _item("q2", "two"), _item("q3", "three")]
    data = _run(items, source=sabotaged)
    with pytest.raises(AssertionError):
        _assert_collapsed_two_plus_one(data["snap"])
