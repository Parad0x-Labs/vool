"""Canonical inbox rendering: source snapshots, section-scoped reads and deep links.

The Node DOM seam uses labelled server items. Persistence, migration, duplicate
observations and completion recording are tested against real stores separately.
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
    """Render real inbox code with labelled canonical notification fixtures."""
    items = [{"notification_id": f"ntf-{ev['seq']}", "source_kind": "model_market",
              "title": ev.get("display_name", ""), "body": "", "read_at": "fixture-read",
              "payload": {"section": "offers", "market_event": ev}} for ev in reversed(events)]
    program = (DOM + "\nglobalThis.__items = " + json.dumps(items) + ";\n"
        + "globalThis.__inspects = 0; globalThis.__reads = []; globalThis.__dismissed = [];\n"
        + r"""
globalThis.fetch = async (url, opts) => {
  let j = {ok:true};
  if (url.indexOf('/api/notifications?') === 0) j = {ok:true, items:__items.filter(x=>!x.dismissed_at), unread:__items.filter(x=>!x.read_at&&!x.dismissed_at).length};
  if (url === '/api/notifications/read') { const ids=JSON.parse(opts.body).notification_ids; __reads.push(...ids); __items.forEach(x=>{if(ids.includes(x.notification_id))x.read_at='saved';}); }
  if (url === '/api/notifications/action') { const b=JSON.parse(opts.body); __items.forEach(x=>{if(x.notification_id===b.notification_id){x.read_at='saved';if(b.action==='dismiss')x.dismissed_at='saved';}}); }
  return {ok:true,status:200,json:async()=>j};
};
window.VoolPageActions={openModelMenu:()=>{__inspects++;}};
document.getElementById('panelBtn').parentNode=document.body;
""" + DRIVER_HELPERS + prelude + "\n;(async function(){\n"
        + FRAGMENT.split("<script>")[1].split("</script>")[0].replace("(function(){", "(async function(){", 1).rsplit("})();", 1)[0]
        + "\nsection='offers';\n" + driver + "\n})();\n})();\n")
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


def test_saved_read_state_is_used_in_the_browser() -> None:
    out = run_fragment(TICK + r"""
for(let i=0;i<10;i++) await tick();
out({pending:window.VoolNotify.pending(),unread:window.VoolNotify.unread()});
""", [_free_event(1), _free_event(2)])
    assert not out["errors"]
    assert out["pending"] == 2 and out["unread"] == 0


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
    assert out["unread"] == 0, "the canonical fixture is already read"
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
    rows = out["snapshotFirst"].split('data-vmarket="')
    assert len(rows) >= 3, f"expected two item rows: {len(rows) - 1}"
    original_alert = rows[2]
    assert "Observed free: Example One (" in original_alert, original_alert[:400]
    assert "$0.0000/1M" in original_alert, "the first alert keeps its own observed prices"
    assert "Example One RENAMED" not in original_alert


def test_read_visible_section_only_not_other_unread_sections() -> None:
    prelude = "__items.forEach(x=>x.read_at=null); __items.push({notification_id:'reminder',source_kind:'reminder',title:'Meet',payload:{},read_at:null});"
    out = run_fragment(TICK + r"""
for(let i=0;i<10;i++) await tick();
vfBell().__on.click({});
for(let i=0;i<10;i++) await tick();
out({reads:__reads,unread:window.VoolNotify.unread(),html:vfPop().innerHTML});
""", [_free_event(1)], prelude=prelude)
    assert out["reads"] == ["ntf-1"] and out["unread"] == 1
    assert "Needs you" in out["html"] and "Updates" in out["html"] and "Model offers" in out["html"]


def test_background_run_never_fabricates_an_item_in_the_browser() -> None:
    out = run_fragment(TICK + r"""
for(let i=0;i<10;i++) await tick();
await window.VoolNotify.runFinished({chatId:'chat',turnId:'turn',status:'completed',displayed:false});
out({pending:window.VoolNotify.pending()});
""", [])
    assert out["pending"] == 0  # real runtime recording is proven in test_notification_hub


def test_discovery_and_inbox_dismissal_have_distinct_scope() -> None:
    assert "Discover models" in FRAGMENT
    assert "your alerts and model choice stay unchanged" in FRAGMENT
    assert "/api/model-radar/dismiss" not in FRAGMENT
    assert "vool_notify_state_v1" in FRAGMENT  # migration only, no second read owner
    assert "localStorage.setItem" not in FRAGMENT


def test_inspect_action_opens_the_model_menu() -> None:
    out = run_fragment(TICK + r"""
for(let i=0;i<10;i++) await tick();
openCard(centre[0]);
out({inspects:__inspects});
""", [_free_event(1)])
    assert out["inspects"] == 1


def test_inbox_boots_when_browser_storage_is_unavailable() -> None:
    out = run_fragment(TICK + r"""
for(let i=0;i<10;i++) await tick();
out({pending:window.VoolNotify.pending()});
""", [_free_event(1)], prelude="localStorage.getItem=()=>{throw Error('storage disabled');};")
    assert not out["errors"] and out["pending"] == 1
