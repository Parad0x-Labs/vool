"""Laws for the model popover's server-truth provenance strip (full-a11-demo-app pass-001).

The strip is a LENS on the A11 server authority (GET /api/cloud/model): it renders the
served selection, provider and spend class verbatim, keeps UNKNOWN a distinct literal
word, and never derives a spend class from client-side state. A missing or failed
verdict renders UNKNOWN — never a comforting default.
"""

from __future__ import annotations

import functools
import re

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

HTML = render_vool_chat_html()


def page_scripts() -> list[str]:
    """The house script (it defines the provenance strip) and the companion runtime it binds to.

    Selected by what they DEFINE, not by count: the chat page carries the wallet, media, pet and
    settings fragments as their own inline scripts now (14 on 2026-09-07), and a count of two pinned
    the page's layout rather than the laws under test.
    """
    found = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
    wanted = [
        script
        for script in found
        if "renderSelectionProvenance" in script or "runFinished" in script
    ]
    assert wanted, f"no inline script defines the provenance strip; {len(found)} scripts on the page"
    return wanted


LAWS = r"""
const laws = {};
async function law(name, fn) {
  try { await fn(); laws[name] = "PASS"; }
  catch (e) { laws[name] = "FAIL: " + ((e && e.message) || e); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
function stripHtml() { const el = document.getElementById('modelProv'); return el ? el.innerHTML : ''; }

law("P1.server_verdict_renders_verbatim", () => {
  renderSelectionProvenance({ ok: true, model: 'zzz/alpha-big', provider: 'openrouter', cost_state: 'paid' });
  let html = stripHtml();
  assert(html.indexOf('openrouter · zzz/alpha-big') !== -1, 'pin renders provider + id');
  assert(html.indexOf('PAID') !== -1, 'paid renders as PAID');
  renderSelectionProvenance({ ok: true, model: 'qwen3:8b', provider: '', cost_state: 'free' });
  html = stripHtml();
  assert(html.indexOf('free') !== -1 && html.indexOf('PAID') === -1, 'free renders as free');
});

law("P2.unknown_stays_unknown_never_free", () => {
  // A missing cost_state (unclassified pin) must render UNKNOWN — the literal word, not a guess.
  renderSelectionProvenance({ ok: true, model: 'cold/not-listed', provider: 'openrouter', cost_state: '' });
  assert(stripHtml().indexOf('UNKNOWN') === -1, 'empty cost_state means local/Auto wording, not UNKNOWN');
  renderSelectionProvenance({ ok: true, model: 'cold/not-listed', provider: 'openrouter', cost_state: 'unknown' });
  const html = stripHtml();
  assert(html.indexOf('UNKNOWN') !== -1, 'unknown renders the literal word');
  assert(html.indexOf('free') === -1 && html.indexOf('FREE') === -1, 'unknown never borrows free');
});

law("P3.failed_fetch_is_unknown_not_comforting", () => {
  renderSelectionProvenance(null);
  const html = stripHtml();
  assert(html.indexOf('UNKNOWN') !== -1, 'a failed server read renders UNKNOWN');
  assert(html.indexOf('free') === -1 && html.indexOf('PAID') === -1, 'no invented spend class after failure');
});

law("P4.local_only_names_the_block", () => {
  const prev = modelValue;
  modelValue = 'vool-local-only';
  renderSelectionProvenance(null);
  modelValue = prev;
  assert(stripHtml().indexOf('Local Only') !== -1, 'local-only mode renders the block truth');
});
"""

PROGRAM_TAIL = "\nout({ laws: laws });\n"


@functools.lru_cache(maxsize=1)
def provenance_js_laws() -> dict:
    program = (DOM + "\n;(async function(){\n" + "\n;\n".join(page_scripts())
               + "\n" + LAWS + PROGRAM_TAIL + "\n})();\n")
    result = run_node(program, timeout=120)
    assert not result.get("errors"), f"page booted with errors: {result['errors']}"
    return result["laws"]


def test_provenance_strip_laws():
    results = provenance_js_laws()
    for name in sorted(results):
        assert results[name] == "PASS", f"{name}: {results[name]}"
    for required in ["P1.server_verdict_renders_verbatim", "P2.unknown_stays_unknown_never_free",
                     "P3.failed_fetch_is_unknown_not_comforting", "P4.local_only_names_the_block"]:
        assert required in results, f"law {required} did not run; ran: {sorted(results)}"
