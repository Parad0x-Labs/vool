"""The typed status-chip vocabulary — one visual language for truth states, as a page fragment.

Ported from the ux-pass1 design authority's `.st` chip system (docs/design/
ux-pass1-authority-20260825.html) with its law intact: a chip states a TYPED truth and cosmetics
may never redefine it. UNKNOWN and REFUSED carry a dashed border so colour is never the only
signal; UNKNOWN is never success-green; WAITING pulses only when motion is allowed.

Class prefix is `vst-` (vool status), NOT the prototype's `.st` — the served page already uses a
`.st` class as a muted sub-text line (core/vool_chat_page.py), and reusing the name would have
restyled real UI during the port (caught by grep before it shipped; pinned by the collision
check in tests/test_ui_fragments.py).

Fragment contract (the companion layer's proven shape): one appended <style>+<script> IIFE,
exposing exactly one namespace — `window.VoolChips`:

    VoolChips.html(state, label?) -> the chip's HTML string (safe: label is escaped)
    VoolChips.states               -> the canonical state list

Renderers call it where they already build HTML strings. Nothing here reads runtime state; a chip
is a PRESENTATION of a typed value the caller already holds — this module cannot invent truth.
"""

from __future__ import annotations

#: state -> (display label, css modifier). The vocabulary is closed on purpose: a new truth state
#: is a runtime event-model change first, a chip second.
_CHIP_STATES: tuple[tuple[str, str], ...] = (
    ("running", "RUNNING"),
    ("done", "DONE"),
    ("pass", "PASS"),
    ("partial", "PARTIAL"),
    ("failed", "FAILED"),
    ("refused", "REFUSED"),
    ("unknown", "UNKNOWN"),
    ("waiting", "WAITING"),
    ("approval", "NEEDS APPROVAL"),
    ("cancelled", "CANCELLED"),
)

_CHIP_CSS = """
.vst{display:inline-flex;align-items:center;gap:6px;font-family:'SF Mono',ui-monospace,Menlo,monospace;
  font-size:10px;letter-spacing:.06em;padding:2px 8px;border-radius:5px;font-weight:600;
  vertical-align:middle;white-space:nowrap}
.vst-running{color:#60a5fa;background:#14243f}
.vst-done,.vst-pass{color:#4ade80;background:#0e2e1e}
.vst-partial{color:#fbbf24;background:#332708}
.vst-failed{color:#f87171;background:#3a1414}
.vst-refused{color:#fb923c;background:#38200e;border:1px dashed #7a4520}
.vst-unknown{color:#8b93a3;background:#20242c;border:1px dashed #4a5262}
.vst-waiting{color:#fbbf24;background:#332708;animation:vstPulse 1.5s infinite}
.vst-approval{color:#fde68a;background:#3d2f07;border:1px solid #6b5514}
.vst-cancelled{color:#8b93a3;background:#20242c}
@keyframes vstPulse{50%{opacity:.4}}
@media (prefers-reduced-motion: reduce){.vst-waiting{animation:none}}
"""

_CHIP_JS = """
(function(){
'use strict';
var STATES = __VST_STATES__;
var LABELS = {};
STATES.forEach(function(pair){ LABELS[pair[0]] = pair[1]; });
function esc(text){
  return String(text == null ? '' : text)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function chipHtml(state, label){
  var key = String(state || '').toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(LABELS, key)) key = 'unknown';
  return '<span class="vst vst-' + key + '">' + esc(label || LABELS[key]) + '</span>';
}
window.VoolChips = Object.freeze({
  html: chipHtml,
  states: STATES.map(function(pair){ return pair[0]; }),
});
})();
"""


def render_chip_fragment() -> str:
    """The chip vocabulary as an appended <style>+<script> fragment."""
    import json

    states_json = json.dumps([list(pair) for pair in _CHIP_STATES])
    return (
        "<style>" + _CHIP_CSS + "</style>"
        "<script>" + _CHIP_JS.replace("__VST_STATES__", states_json) + "</script>"
    )


__all__ = ["render_chip_fragment"]
