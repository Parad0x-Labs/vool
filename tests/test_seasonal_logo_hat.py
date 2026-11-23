"""The Christmas hat sits on the VOOL logo in the header, never on a pet (owner 2026-11-23).

It shows from 1 December to 6 January by this computer's own date, a person who turns it off keeps
it off, and turning it back on brings it back in every open window.
"""

from __future__ import annotations

import json

from core.companion_presentation_fragment import render_companion_fragment
from core.seasonal_fragment import SEASONAL_JS, render_seasonal_fragment
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html
from tests.chat_page_js_harness import run_node

_STUB = r"""
class Node {
  constructor(cls) { this.className = cls || ''; this.children = []; this.hidden = false; this.attrs = {}; this.innerHTML = ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  insertBefore(n) { this.children.unshift(n); return n; }
  get firstChild() { return this.children[0] || null; }
  querySelector(sel) { return sel === '.vs-logo-hat' ? (this.children.find((c) => c.className === 'vs-logo-hat') || null) : null; }
}
const brand = new Node('brand');
const store = {};
const handlers = {};
globalThis.window = globalThis;
globalThis.addEventListener = (t, f) => { (handlers[t] = handlers[t] || []).push(f); };
globalThis.dispatchEvent = (e) => { (handlers[e.type] || []).forEach((f) => f(e)); };
globalThis.Event = class { constructor(t) { this.type = t; } };
globalThis.localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
globalThis.setInterval = () => 0;
globalThis.document = { readyState: 'complete', hidden: false, addEventListener() {},
  querySelector: (sel) => (sel === 'header .brand' && globalThis.__hasHeader ? brand : null),
  createElement: () => new Node('') };
globalThis.__hasHeader = true;
"""

_DRIVER = r"""
const S = window.VoolSeasonal;
const hat = () => brand.children.find((c) => c.className === 'vs-logo-hat');
const res = {};
const days = { nov30: [2026, 10, 30], dec1: [2026, 11, 1], dec25: [2026, 11, 25], jan6: [2027, 0, 6], jan7: [2027, 0, 7], jul4: [2027, 6, 4] };
res.season = {};
for (const k of Object.keys(days)) res.season[k] = S.inSeason(new Date(days[k][0], days[k][1], days[k][2]));
res.outOfSeasonBuildsNothing = (S.dressLogo(new Date(2026, 10, 23)), hat() === undefined);
S.dressLogo(new Date(2026, 11, 5));
res.inSeasonShown = !!hat() && hat().hidden === false && hat().attrs['aria-hidden'] === 'true' && /<svg/.test(hat().innerHTML);
S.setEnabled(false);
res.offStored = localStorage.getItem('vool_seasonal_v1');
S.dressLogo(new Date(2026, 11, 5));
res.offHidden = hat().hidden === true;
S.setEnabled(true);
S.dressLogo(new Date(2026, 11, 5));
res.backOn = hat().hidden === false && localStorage.getItem('vool_seasonal_v1') === null;
S.dressLogo(new Date(2027, 0, 7));
res.goneJan7 = hat().hidden === true;
res.oneHat = brand.children.filter((c) => c.className === 'vs-logo-hat').length;
globalThis.__hasHeader = false;
res.noHeaderSafe = S.dressLogo(new Date(2026, 11, 5)) === false;
console.log(JSON.stringify(res));
"""


def test_the_hat_follows_the_date_window_and_the_off_switch() -> None:
    program = _STUB + "\n" + SEASONAL_JS + "\n" + _DRIVER
    res = run_node(program)
    assert res["season"] == {"nov30": False, "dec1": True, "dec25": True, "jan6": True, "jan7": False, "jul4": False}, res
    assert res["outOfSeasonBuildsNothing"], res
    assert res["inSeasonShown"], res
    assert res["offStored"] == "off" and res["offHidden"], res
    assert res["backOn"], res
    assert res["goneJan7"], res
    assert res["oneHat"] == 1, res
    assert res["noHeaderSafe"], res


def test_the_hat_is_on_the_header_logo_and_never_on_a_pet() -> None:
    page = render_vool_chat_html(build_commit="test-commit")
    assert render_seasonal_fragment() in page, "the chat page carries the logo hat"
    assert "header .brand .vs-logo-hat" in page and "pointer-events:none" in render_seasonal_fragment()
    pet = render_companion_fragment()
    assert "VoolSeasonal" not in pet, "the pet never wears the hat"


def test_settings_offers_the_christmas_hat_switch() -> None:
    page = render_vool_settings_html(build_commit="test-commit")
    assert "window.VoolSeasonal" in page and "widgetSeasonal" in page
    assert json.dumps("Christmas hat")[1:-1] in page
