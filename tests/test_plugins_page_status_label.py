"""The Plugins page's status line says what the catalog states — never more.

Every card used to read "Installed · Enabled": a missing `enabled` field counted as enabled,
and bundled first-party packs were labelled like installed ones. The real renderPlugins,
extracted from the served page, now renders under node with a minimal DOM and a German
bundle published as VOOLT, so the assertions pin both WHAT each card claims and that the
words resolve through the catalog (a raw literal would render English).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html

_DOM_STUB = """
function el(tag) {
  return { tag: tag, children: [], className: '', _text: '', attrs: {}, style: {},
    appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, setAttribute(k, v) { this.attrs[k] = v; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; } };
}
const body = el('div');
globalThis.document = { createElement: el, getElementById: (id) => (id === 'pluginsBody' ? body : null) };
globalThis.VOOLT = function (key) {
  var m = {
    'plugins.status.built_in': 'Integriert', 'plugins.status.installed': 'Installiert',
    'plugins.status.off': 'Aus', 'plugins.status.on': 'Ein', 'plugins.status.unknown': 'Status unbekannt'
  };
  return (key in m) ? m[key] : key;
};
globalThis.pageT = function (key, fallback) { var t = VOOLT(key); return (t && t !== key) ? t : fallback; };
globalThis._firstPartyBadge = function () { return el('span'); };
globalThis.togglePlugin = function () {};
"""

_GERMAN = {"built_in": "Integriert", "installed": "Installiert", "on": "Ein", "off": "Aus", "unknown": "Status unbekannt"}


def _render(plugins: list[dict]) -> dict[str, dict]:
    """Run the real renderPlugins over `plugins`; returns id -> {status, muted} per card."""
    html = render_vool_chat_html()
    start = html.index("function renderPlugins(d, query) {")
    end = html.index("function renderSkills(d, query) {")
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat page script")
    program = (
        _DOM_STUB
        + html[start:end]
        + "\n"
        + "renderPlugins({ installed: true, plugin_count: " + str(len(plugins)) + ", skill_count: 0, plugins: "
        + json.dumps(plugins)
        + " }, '');\n"
        + """
const cards = {};
for (const card of body.children) {
  if (card.className.indexOf('plugin-card') === -1) continue;
  let name = '', status = '', muted = false;
  for (const node of card.children) {
    if (node.className === 'p-head') name = node.children[0].textContent;
    if (node.className === 'p-actions') { status = node.children[0].textContent; muted = node.children[0].className.indexOf(' off') !== -1; }
  }
  cards[name] = { status: status, muted: muted };
}
console.log(JSON.stringify(cards));
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"renderPlugins failed under node:\n{result.stderr[:800]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_card_says_exactly_what_the_catalog_states() -> None:
    cards = _render([
        {"id": "a", "name": "Ordinary", "version": "1", "enabled": True, "origin": "installed"},
        {"id": "b", "name": "Silent", "version": "1", "origin": "installed"},
        {"id": "c", "name": "Turned off", "version": "1", "enabled": False, "origin": "installed"},
        {"id": "d", "name": "Bundled", "version": "1", "enabled": True, "origin": "bundled", "first_party": True},
        {"id": "e", "name": "Native library", "version": "1", "enabled": True, "first_party": True},
        {"id": "f", "name": "No origin", "version": "1", "enabled": True},
    ])
    assert cards["Ordinary"]["status"] == f"{_GERMAN['installed']} · {_GERMAN['on']}"
    # A catalog that does not state `enabled` is unknown — never claimed on, and muted.
    assert cards["Silent"]["status"] == f"{_GERMAN['installed']} · {_GERMAN['unknown']}"
    assert cards["Silent"]["muted"] is True
    assert cards["Turned off"]["status"] == f"{_GERMAN['installed']} · {_GERMAN['off']}"
    assert cards["Turned off"]["muted"] is True
    assert cards["Bundled"]["status"] == f"{_GERMAN['built_in']} · {_GERMAN['on']}"
    assert cards["Native library"]["status"] == f"{_GERMAN['built_in']} · {_GERMAN['on']}"
    # With no origin stated, the line claims no origin — just the state.
    assert cards["No origin"]["status"] == _GERMAN["on"]
    assert cards["Ordinary"]["muted"] is False


def test_the_status_words_resolve_through_the_catalog() -> None:
    """A raw literal would render English; the German bundle must win when present."""
    cards = _render([{"id": "a", "name": "Ordinary", "version": "1", "enabled": True, "origin": "installed"}])
    assert "Enabled" not in cards["Ordinary"]["status"]
    assert _GERMAN["on"] in cards["Ordinary"]["status"]
