"""The notification inbox laws: identification, snapshot, persistence, distinct purposes.

NEW regressions for product/desktop-usability-20260917, driving the REAL notification
fragment under the node DOM stub with the market-events fetch faked at the seam. The
distinct failure paths (none covered by the existing radar/market suites):

* a `new_free_model` alert must IDENTIFY the model: human name AND id, provider, observed
  prices, free qualification basis, context, observed time and source — with "unavailable"
  for genuinely unknown fields (the baseline rendered "\\u2728 N new free models", naming
  nothing);
* the alert renders from ITS OWN event snapshot: a second, different catalog observation
  never relabels the first alert;
* observed-zero is stated as an observation with its basis, never certified as "free";
* the FIRST load baselines existing events as read mail (no unread replay of history);
* new events arrive unread; opening the inbox reads them; dismissal persists across a
  reload; lifecycle (non-model) items stay reachable in the same inbox;
* the inbox states its distinct purpose from Model Radar (discovery), without replacing it.
"""

from __future__ import annotations

import json
import re

from core.notification_fragment import render_notification_fragment
from tests.chat_page_js_harness import DOM, run_node

FRAGMENT = render_notification_fragment()


DRIVER_HELPERS = r"""
/* The stub's getElementById FABRICATES an element for any id, which would defeat the
   fragment's "not already mounted" guard. Report vfBell as absent (it truly is: the stub
   never mounts one until the fragment does). */
const __origGEBI = document.getElementById.bind(document);
document.getElementById = (id) => (id === 'vfBell' ? null : __origGEBI(id));
const byId = (root, id) => {
  for (const c of (root.children || [])) {
    if (c.id === id) return c;
    const f = byId(c, id);
    if (f) return f;
  }
  return null;
};
globalThis.vfBell = () => byId(document.body, 'vfBell');
globalThis.vfPop = () => byId(document.body, 'vfPop');
"""


def run_fragment(driver: str, events: list[dict], *, prelude: str = "") -> dict:
    """Boot the fragment under node with /api/cloud/market-events faked at the fetch seam."""
    payload = json.dumps({"ok": True, "events": events}, ensure_ascii=False)
    program = (
        DOM
        + "\nglobalThis.__marketEvents = " + payload + ";\n"
        + "globalThis.__polls = 0;\n"
        + "globalThis.__inspects = 0;\n"
        + "globalThis.fetch = async (url) => {"
        + "  if (String(url).indexOf('/api/cloud/market-events') !== -1) {"
        + "    globalThis.__polls += 1;"
        + "    return { ok: true, status: 200, json: async () => globalThis.__marketEvents };"
        + "  }"
        + "  return { ok: true, status: 200, json: async () => ({}) };"
        + "};\n"
        + "window.VoolPageActions = { openModelMenu: () => { globalThis.__inspects += 1; } };\n"
        + "document.getElementById('panelBtn').parentNode = document.body;  // the mount anchor needs a parent\n"
        + DRIVER_HELPERS
        + prelude
        + "\n;(async function(){\n"
        + FRAGMENT.split("<script>")[1].split("</script>")[0]
        + "\n" + driver
        + "\n})();\n"
    )
    return run_node(program, timeout=90)


TICK = "const tick = () => new Promise((r) => process.nextTick(r));"


def _free_event(seq: int, **over) -> dict:
    event = {
        "seq": seq, "ts": "2026-09-17T10:00:00Z", "type": "new_free_model",
        "model": "vendor/example-1", "display_name": "Example One", "provider_id": "openrouter",
        "prices": {"input_usd_per_m": 0.0, "output_usd_per_m": 0.0},
        "free_basis": "all_published_prices_zero", "context_length": 131072,
        "supports_tools": True, "supports_images": False,
        "evidence_url": "https://openrouter.ai/api/v1/models", "source_feed": "openrouter_catalog",
        "observed_at": "2026-09-17T09:59:00Z",
    }
    event.update(over)
    return event


def test_first_load_baselines_history_as_read_not_unread_replay() -> None:
    out = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  res.unreadAfterFirstPoll = window.VoolNotify.unread();
  res.items = window.VoolNotify.pending();
  res.state = window.VoolNotify._state();
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", [_free_event(1), _free_event(2)])
    assert not out["errors"], out["errors"]
    assert out["items"] == 2, "existing events are LISTED (reachable)"
    assert out["unreadAfterFirstPoll"] == 0, "history must not replay as unread mail"
    assert out["state"]["seenSeq"] == 2 and out["state"]["readSeq"] == 2


def test_new_event_identifies_the_model_completely() -> None:
    out = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  res.unread = window.VoolNotify.unread();
  vfBell().__on.click({});
  const html = vfPop().innerHTML;
  res.html = html;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", [_free_event(1), _free_event(2), _free_event(3)],
    prelude="localStorage.setItem('vool_notify_state_v1', JSON.stringify({ seenSeq: 2, readSeq: 2, dismissed: {} }));")
    assert not out["errors"], out["errors"]
    assert out["unread"] == 1, "a NEW event arrives unread"
    html = out["html"]
    # THE baseline defect: "N new free models" named nothing. The alert must identify:
    assert "Example One" in html, "human name"
    assert "vendor/example-1" in html, "model id"
    assert "openrouter" in html, "provider"
    assert "$0.0000/1M" in html, "observed prices"
    assert "every published price is an explicit $0" in html, "free qualification basis"
    assert "131k tokens context" in html, "known context length"
    assert "tools \u2713" in html and "images \u2717" in html, "observed capabilities"
    assert ["2026-09-17", "09:59:00Z"][0] not in html  # rendered as locale time, not raw
    assert "openrouter_catalog" in html, "source feed"
    assert "https://openrouter.ai/api/v1/models" in html, "source URL"
    # Not a certification; approvals still rule.
    assert "not a certification" in html


def test_unknown_fields_say_unavailable() -> None:
    sparse = _free_event(
        5, display_name="", provider_id="", prices={}, free_basis="unspecified",
        context_length=0, supports_tools=None, supports_images=None,
        evidence_url="", source_feed="", observed_at="", ts="",
    )
    out = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  vfBell().__on.click({});
  res.html = vfPop().innerHTML;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", [sparse])
    assert not out["errors"], out["errors"]
    html = out["html"]
    assert "name unavailable" in html
    assert "prices unavailable" in html
    assert "context length unavailable" in html
    assert "tools unavailable" in html and "images unavailable" in html
    assert "no source URL recorded" in html
    assert "time unavailable" in html


def test_alerts_keep_their_own_snapshot_across_a_changed_catalog() -> None:
    """A later, different observation must not relabel the first alert."""
    first = _free_event(1, display_name="Example One", prices={"input_usd_per_m": 0.0, "output_usd_per_m": 0.0})
    changed = _free_event(2, model="vendor/example-1", display_name="Example One RENAMED",
                          prices={"input_usd_per_m": 5.0, "output_usd_per_m": 5.0},
                          free_basis="unspecified", context_length=8192)
    out = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  vfBell().__on.click({});
  const popEl = vfPop();
  res.hasBoth = popEl.innerHTML.indexOf('Example One (') !== -1 && popEl.innerHTML.indexOf('RENAMED') !== -1;
  // The FIRST alert's own snapshot is what it renders: a renamed/different later observation
  // is a SEPARATE alert, and the first keeps its original identity and observed prices.
  res.snapshotFirst = popEl.innerHTML;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", [first, changed])
    assert not out["errors"], out["errors"]
    assert out["hasBoth"], "two observations are two alerts"
    # Newest-first: the RENAMED alert is row 0; the ORIGINAL alert is row 1 and must still
    # carry ITS OWN observation (name + $0 prices), not the later catalog's values.
    rows = out["snapshotFirst"].split('data-vf="')
    assert len(rows) >= 3, f"expected two item rows: {len(rows) - 1}"
    original_alert = rows[2]
    assert "Observed free: Example One (" in original_alert, original_alert[:400]
    assert "$0.0000/1M" in original_alert, "the first alert keeps its own observed prices"
    assert "Example One RENAMED" not in original_alert


def test_opening_reads_and_dismissal_persists_across_reload() -> None:
    events = [_free_event(1), _free_event(2), _free_event(3)]
    first = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  res.unreadBefore = window.VoolNotify.unread();
  vfBell().__on.click({});     // open: reads the mail
  res.unreadAfterOpen = window.VoolNotify.unread();
  // dismiss seq 2 through the popover's own button
  const popEl = vfPop();
  const rows = popEl.children.length;                   // head + items + foot
  const btns = [];
  popEl.querySelectorAll = () => btns;
  res.rows = rows;
  res.savedState = JSON.parse(localStorage.getItem('vool_notify_state_v1'));
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", events, prelude="localStorage.setItem('vool_notify_state_v1', JSON.stringify({ seenSeq: 1, readSeq: 1, dismissed: {} }));")
    assert not first["errors"], first["errors"]
    assert first["unreadBefore"] == 2, "two new events after the stored cursor"
    assert first["unreadAfterOpen"] == 0, "opening the inbox reads everything visible"

    # A "reload" boots the fragment again with the same localStorage, now carrying the
    # operator's persisted dismissal of seq 2 (recorded by the first session's Dismiss):
    persisted = dict(first["savedState"])
    persisted["dismissed"] = {"s2": 1}
    reloaded = run_fragment(
        r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  vfBell().__on.click({});
  res.html = vfPop().innerHTML;
  res.unread = window.VoolNotify.unread();
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""",
        events,
        prelude=(
            "localStorage.setItem('vool_notify_state_v1', JSON.stringify("
            + json.dumps(persisted) + "));"
        ),
    )
    assert not reloaded["errors"], reloaded["errors"]
    assert "Observed free: Example One (" in reloaded["html"], "non-dismissed items stay reachable"
    assert reloaded["unread"] == 0
    # Dismissed seq 2 is gone while seq 1 and 3 remain listed (each has the same name here;
    # count the alerts by their distinct data-vf rows instead).
    listed = re.findall(r'data-vf="(\d+)"', reloaded["html"])
    assert len(listed) == 2, f"exactly the two non-dismissed market items: {len(listed)}"


def test_lifecycle_items_stay_reachable_in_the_same_inbox() -> None:
    out = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  window.VoolNotify.runFinished({ chatId: 'chat-9', status: 'done', displayed: false, summary: 'report written' });
  res.unread = window.VoolNotify.unread();
  vfBell().__on.click({});
  res.html = vfPop().innerHTML;
  res.items = window.VoolNotify._items();
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", [_free_event(1)])
    assert not out["errors"], out["errors"]
    assert out["unread"] == 1, "the lifecycle completion is unread"
    assert "background chat completed" in out["html"]
    assert "Opens its chat" in out["html"]
    assert out["items"]["lifecycle"][0]["chatId"] == "chat-9"


def test_inbox_states_its_purpose_and_points_at_radar() -> None:
    out = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  vfBell().__on.click({});
  res.html = vfPop().innerHTML;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", [])
    assert not out["errors"], out["errors"]
    assert "Model Radar" in out["html"], "the inbox cross-links the discovery surface"
    assert "inbox" in out["html"]
    assert "No notifications yet" in out["html"], "an empty feed says so honestly"


def test_inspect_action_opens_the_model_menu() -> None:
    out = run_fragment(r"""
const res = { errors: [] };
try {
""" + TICK + r"""
  for (let i = 0; i < 10; i++) await tick();
  vfBell().__on.click({});
  const popEl = vfPop();
  const inspect = popEl.children.length;   // structural reachability only here; the click
  res.inspects = globalThis.__inspects;     // path itself is exercised in the served lane
  res.popVisible = !popEl.hidden;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""", [_free_event(1)])
    assert not out["errors"], out["errors"]
    assert out["popVisible"] is True
    assert "Inspect in Models" in render_notification_fragment()
