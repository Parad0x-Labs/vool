"""The running card reads like three calm lines: "Vooling for 12s  >", what VOOL is doing now, and
one row per kind of step ("Ran 3 searches"). The playful title word is decoration only: every
state the person acts on (approval, a paused price, the answer review, a finished turn) and every
non-English UI keeps its plain words, and the plain state stays on the title's tooltip.

Driven through the real page script under the node DOM, so these are what the card paints.
"""

from __future__ import annotations

from core.working_words import MAX_WORD, WORKING_WORDS
from tests.chat_page_js_harness import DOM, run_node, script

DRIVE = """
const seen = {};
function cardFor() {
  const run = adoptRun(displayedChat, newRun(displayedChat));
  buildCard(run, null);
  return run;
}
function title(run) { return { text: run.refs.title.textContent, tip: run.refs.title.title, clock: run.refs.clock.textContent }; }
try {
  const fresh = cardFor();
  applyTaskEvent(fresh, { type: 'task.started', summary: 'Understanding the request' });
  seen.fresh = title(fresh);

  const later = cardFor();
  later.start = Date.now() - 7000;
  renderCard(later);
  seen.later = title(later);

  const perm = cardFor();
  applyTaskEvent(perm, { type: 'permission.required', summary: 'Approve', approval: { approval_id: 'a1', scope_options: ['once'] } });
  seen.permission = title(perm);

  const review = cardFor();
  review.verifying = true; renderCard(review);
  seen.verifying = title(review);

  const paused = cardFor();
  paused.pricePaused = true; renderCard(paused);
  seen.paused = title(paused);

  const done = cardFor();
  finishRun(done, 'completed', 'Complete', new Date().toISOString());
  seen.done = title(done);

  const steps = cardFor();
  for (let i = 0; i < 3; i++) applyTaskEvent(steps, { type: 'tool.started', tool: 'web_search', summary: 'Searching', stage: 'Searching', step_id: 's' + i });
  applyTaskEvent(steps, { type: 'tool.started', tool: 'read_file', summary: 'Reading', stage: 'Reading', step_id: 'r1' });
  applyTaskEvent(steps, { type: 'tool.completed', tool: 'read_file', summary: 'Read', stage: 'Reading', step_id: 'r1' });
  seen.rows = steps.refs.groups.innerHTML;
  seen.detailsStart = { hidden: steps.refs.details.hidden, expanded: steps.refs.toggle.attrs['aria-expanded'] };
  steps.refs.toggle.click();
  seen.detailsOpen = { hidden: steps.refs.details.hidden, expanded: steps.refs.toggle.attrs['aria-expanded'] };
  steps.refs = null; buildCard(steps, null);
  seen.detailsRebuilt = { hidden: steps.refs.details.hidden };

  window.VOOL_I18N = { tag: 'de' };
  const german = cardFor();
  seen.german = title(german);
} catch (e) { errors.push(String((e && e.stack) || e)); }
globalThis.__drove.push(seen);
_st(() => { __report(); process.exit(0); }, 200);
"""


def _seen() -> dict:
    data = run_node(DOM + script() + DRIVE)
    assert data["errors"] == [], "\n".join(data["errors"])
    return data["drove"][-1]


def test_the_word_list_is_short_plain_and_unique() -> None:
    assert WORKING_WORDS[0] == "Vooling", "a turn opens on VOOL's own word"
    assert len(set(WORKING_WORDS)) == len(WORKING_WORDS)
    for word in WORKING_WORDS:
        assert 0 < len(word) <= MAX_WORD, word
        assert word == word.strip() and "<" not in word and "\n" not in word, word
        assert all(ord(ch) < 0x2000 for ch in word), f"no emoji or symbols: {word!r}"


def test_a_working_turn_shows_a_playful_word_with_its_clock_and_keeps_the_plain_state_on_hover() -> None:
    seen = _seen()
    assert seen["fresh"]["text"] == "Vooling"
    assert seen["fresh"]["clock"].startswith("for ")
    assert seen["fresh"]["tip"], "the plain state must stay on the tooltip"
    assert seen["later"]["text"] in WORKING_WORDS, "the word rotates through the shipped list"
    assert seen["later"]["clock"] == "for 7s"


def test_states_the_person_acts_on_keep_their_plain_words() -> None:
    seen = _seen()
    assert seen["permission"]["text"] == "Waiting for approval"
    assert seen["verifying"]["text"] == "Answer ready · model review running…"
    for key in ("permission", "verifying", "paused"):
        assert seen[key]["text"] not in WORKING_WORDS, key
        assert seen[key]["clock"].startswith("· "), key
    assert seen["done"] == {"text": "Completed", "tip": "", "clock": ""}


def test_a_non_english_ui_never_shows_the_english_words() -> None:
    seen = _seen()
    assert seen["german"]["text"] not in WORKING_WORDS
    assert seen["german"]["clock"].startswith("· ")


def test_steps_roll_up_into_one_row_per_kind_and_only_unfinished_kinds_pulse() -> None:
    rows = _seen()["rows"]
    assert '<div class="tc-group vi live" data-ico="search">Ran 3 searches</div>' in rows
    assert '<div class="tc-group vi" data-ico="read">Read 1 file</div>' in rows
    assert rows.index("searches") < rows.index("file"), "rows keep the order the kinds appeared"


def test_the_title_toggles_the_details_and_a_rebuilt_card_keeps_the_choice() -> None:
    seen = _seen()
    assert seen["detailsStart"] == {"hidden": True, "expanded": "false"}
    assert seen["detailsOpen"] == {"hidden": False, "expanded": "true"}
    assert seen["detailsRebuilt"] == {"hidden": False}
