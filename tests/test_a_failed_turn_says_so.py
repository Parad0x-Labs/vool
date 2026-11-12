"""A turn that ends with no reply must say so on the page, never keep spinning.

Measured on the 0.7.8 release sweep (2026-11-12, real chat page): the second turn of a chat failed on
the server, the bubble kept its "…" and the person waited 15 minutes. Contributor: sls_0x.
"""
from __future__ import annotations

from tests.chat_page_js_harness import DOM, run_node, script

DRIVE = """
const drove = globalThis.__drove;
function check(name, fn) { try { const r = fn(); drove.push(name + '=' + JSON.stringify(r)); } catch (e) { errors.push(name + ': ' + ((e && e.message) || e)); } }
check('failed-empty', () => {
  const run = adoptRun(displayedChat, newRun(displayedChat)); buildCard(run, null);
  finishRun(run, 'cancelled', 'Cancelled', new Date().toISOString());
  return run.text;
});
check('user-stopped', () => {
  const other = 'openclaw:' + 'd'.repeat(20);
  const run = adoptRun(other, newRun(other)); buildCard(run, null);
  run.stopRequested = true;
  finishRun(run, 'cancelled', 'Cancelled', new Date().toISOString());
  return run.text;
});
check('failed-with-text', () => {
  const other = 'openclaw:' + 'e'.repeat(20);
  const run = adoptRun(other, newRun(other)); buildCard(run, null);
  run.text = 'Venus is the hottest.';
  finishRun(run, 'failed', 'Stopped safely', new Date().toISOString());
  return run.text;
});
_st(() => { __report(); process.exit(0); }, 300);
"""


def _results() -> dict[str, str]:
    data = run_node(DOM + script() + DRIVE)
    assert data["errors"] == [], data["errors"]
    import json

    return {item.split("=", 1)[0]: json.loads(item.split("=", 1)[1]) for item in data["drove"]}


def test_an_empty_failed_turn_tells_the_person_and_points_at_try_again() -> None:
    results = _results()
    assert "couldn't finish that reply" in str(results["failed-empty"]) and "Try again" in str(results["failed-empty"])


def test_a_turn_the_person_stopped_or_that_has_text_is_left_alone() -> None:
    results = _results()
    assert "couldn't finish" not in str(results["user-stopped"] or "")
    assert results["failed-with-text"] == "Venus is the hottest."
