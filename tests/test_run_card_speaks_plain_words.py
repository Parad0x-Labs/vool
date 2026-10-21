"""The chat run card and the Activity panel chrome speak the person's language, in plain words.

The run card under a reply printed the server's task-event summary verbatim. For a tool step that
summary is the runtime's own log line: a fresh install asking about the weather watched the card say
"Running live_data.weather_lookup", then "Finished live_data.weather_lookup: failed" -- English in
every locale, naming an internal tool id. The Activity panel around it (title, tabs, scope picker,
buttons, empty states) was English-only too, although catalog keys for the tabs already existed.

These tests EXECUTE the real page script under node. The tool events are produced by the real
server path (`publish_live_data_retrieval_receipts` -> `build_task_event`), not hand-written, so a
change to either end shows up here. They pin: the card never shows a tool id, for any tool (an
unknown id gets a plain generic line); its words come from the catalogs in every locale; and the
server's exact summary is still kept on the run's events for the Event log.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from core.i18n.catalog import CATALOGS_DIR, catalog_for
from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

LOCALES = sorted(p.stem for p in CATALOGS_DIR.glob("*.json"))
_SCRIPTS: dict[str, str] = {}


def _script(locale: str) -> str:
    if locale not in _SCRIPTS:
        html = render_vool_chat_html(ui_locale=locale)
        scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL), key=len, reverse=True)
        _SCRIPTS[locale] = scripts[0]
    return _SCRIPTS[locale]


def _msg(locale: str, key: str, **params: str) -> str:
    return catalog_for(locale).format(key, **params) if params else catalog_for(locale).format(key)


def _outcome(operation: str, *, ok: bool, state: str, failure: str = "", **arguments):
    subtask = SimpleNamespace(operation=operation, arguments=dict(arguments))
    return SimpleNamespace(subtask=subtask, ok=ok, failure_reason=failure, state=SimpleNamespace(value=state))


def _served_task_events(monkeypatch: pytest.MonkeyPatch, outcomes: list) -> list[dict]:
    """The typed task events the chat stream sends for one live-data lookup, from the real code."""
    import core.live_data_retrieval_receipts as receipts
    from core.task_event_model import build_task_event

    raw: list[dict] = []

    def capture(_ctx, *, event_type, message, details=None):
        raw.append({"event_type": event_type, "message": message, **dict(details or {})})

    monkeypatch.setattr(receipts, "emit_runtime_event", capture)
    receipts.publish_live_data_retrieval_receipts({}, outcomes, plan_id="livedata-1")
    typed = [build_task_event(e) for e in raw]
    return [t for t in typed if t is not None and t["type"].startswith("tool.")]


def _card(events: list[dict], locale: str = "en") -> dict:
    program = (
        DOM
        + _script(locale)
        + "\nconst EVENTS = "
        + json.dumps(events)
        + """;
const run = adoptRun(displayedChat, newRun(displayedChat));
buildCard(run, null);
const seen = [];
for (const ev of EVENTS) {
  applyTaskEvent(run, ev);
  const r = run.refs;
  seen.push({ stage: r.stage.textContent, tip: r.stage.title, recent: r.recent.textContent,
              last: r.last.textContent, sr: r.sr.textContent });
}
out({ seen, steps: run.steps.map((s) => s.summary), log: run.events.map((e) => evTitle(e)) });
__report(); process.exit(0);
"""
    )
    data = run_node(program)
    assert data["errors"] == [], data["errors"]
    return data


def _all_card_text(data: dict) -> str:
    return json.dumps([data["seen"], data["steps"]], ensure_ascii=False)


TOOL_ID = re.compile(r"live_data|weather_lookup|zorblax|run_tests|\b[a-z]+[._][a-z_.]+\b")


# ---- 1. the weather turn: the real server events, the real card ----------------------------------


def test_a_failed_weather_lookup_reads_as_plain_words_on_the_card(monkeypatch) -> None:
    events = _served_task_events(
        monkeypatch, [_outcome("weather_lookup", ok=False, state="failed", failure="URLError: 403", location="rome")]
    )
    # The server really sends the log line (this is the shape the card used to print).
    assert [e["summary"] for e in events] == [
        "Running live_data.weather_lookup",
        "Finished live_data.weather_lookup: failed",
    ]
    data = _card(events)
    looking = "Looking up the weather"
    failed = "Looked up the weather: it didn’t work"
    assert data["seen"][0]["stage"] == looking
    assert data["seen"][1]["stage"] == failed
    assert data["seen"][1]["tip"] == failed and data["seen"][1]["last"] == failed
    assert data["seen"][1]["sr"] == failed
    assert data["seen"][1]["recent"] == "× " + failed
    assert data["steps"] == [failed]
    assert not TOOL_ID.search(_all_card_text(data)), _all_card_text(data)
    # Nothing dropped: the exact server line is still what the Event log reads.
    assert data["log"] == ["Running live_data.weather_lookup", "Finished live_data.weather_lookup: failed"]


def test_a_successful_lookup_says_it_was_looked_up(monkeypatch) -> None:
    events = _served_task_events(monkeypatch, [_outcome("market_quote", ok=True, state="succeeded", asset_key="bitcoin")])
    data = _card(events)
    assert data["seen"][-1]["stage"] == "Looked up the price"
    assert data["seen"][-1]["recent"] == "✓ Looked up the price"
    assert not TOOL_ID.search(_all_card_text(data))


# ---- 2. every tool, not just weather: known kinds by kind, unknown ids fail closed ----------------


def test_an_unknown_tool_id_never_reaches_the_card() -> None:
    events = [
        {"type": "tool.started", "tool": "acme.zorblax_v2", "summary": "Running acme.zorblax_v2", "status": "running"},
        {"type": "tool.failed", "tool": "acme.zorblax_v2", "summary": "Finished acme.zorblax_v2: E42", "status": "failed"},
        {"type": "tool.started", "tool": "", "summary": "", "status": "running"},
        {"type": "tool.completed", "tool": "", "summary": "Tool step finished.", "status": "completed"},
    ]
    data = _card(events)
    assert [s["stage"] for s in data["seen"]] == [
        "Working on a step", "This step didn’t work", "Working on a step", "Finished a step",
    ]
    assert "acme" not in _all_card_text(data) and not TOOL_ID.search(_all_card_text(data))
    assert "Finished acme.zorblax_v2: E42" in data["log"]


def test_an_unknown_live_data_operation_still_reads_as_a_lookup() -> None:
    data = _card([{"type": "tool.failed", "tool": "live_data.tide_tables", "summary": "Finished live_data.tide_tables: failed"}])
    assert data["seen"][0]["stage"] == "Looked up current information: it didn’t work"


def test_tools_of_a_known_kind_say_what_kind_of_step_it_was() -> None:
    events = [
        {"type": "tool.started", "tool": "run_tests", "summary": "Running run_tests", "stage": "Testing"},
        {"type": "tool.failed", "tool": "run_tests", "summary": "Finished run_tests: exit 1", "stage": "Testing"},
        {"type": "tool.started", "tool": "workspace.read_file", "summary": "Running workspace.read_file", "stage": "Reading"},
        {"type": "tool.completed", "tool": "workspace.read_file", "summary": "Finished workspace.read_file", "stage": "Reading"},
    ]
    data = _card(events)
    assert [s["stage"] for s in data["seen"]] == ["Running tests", "Test command failed", "Reading", "Read finished"]
    assert not TOOL_ID.search(_all_card_text(data)), _all_card_text(data)


def test_a_lane_node_is_worded_by_its_own_outcome() -> None:
    events = [
        {"type": "agent_node_started", "tool": "weather_lookup", "summary": "livedata-1:weather:rome started (weather_lookup)", "status": "running"},
        {"type": "agent_node_completed", "tool": "weather_lookup", "summary": "livedata-1:weather:rome failed in 0.6s", "status": "failed"},
        {"type": "agent_node_completed", "tool": "arithmetic", "summary": "livedata-1:math skipped", "status": "cancelled"},
        {"type": "agent_node_completed", "tool": "arithmetic", "summary": "livedata-1:math done", "status": None},
    ]
    data = _card(events)
    assert [s["stage"] for s in data["seen"]] == [
        "Looking up the weather", "Looked up the weather: it didn’t work", "Skipped a step", "A step ended",
    ]
    assert "livedata" not in _all_card_text(data)


def test_engine_lines_for_the_model_and_the_plan_read_as_plain_words() -> None:
    events = [
        {"type": "task.started", "raw_type": "task_received", "summary": "Received request: what is the weather like in rome"},
        {"type": "model.changed", "raw_type": "model_lane_selected", "summary": "Selected ollama-local:qwen3:4b for the daily lane.",
         "model": {"provider_id": "ollama-local:qwen3:4b", "model_id": "qwen3:4b"}},
        {"type": "task.stage_changed", "raw_type": "tool_repeat_blocked", "summary": "Repeated tool request detected. Switching to grounded synthesis instead of looping."},
        # Words the server already wrote for the person are left alone: a note picked up mid-task
        # and an approval request.
        {"type": "task.stage_changed", "raw_type": "followup_note_applied", "summary": "Picked up your note: use metric"},
        {"type": "permission.required", "raw_type": "task_pending_approval", "summary": "Write notes.md"},
    ]
    data = _card(events)
    assert [s["stage"] for s in data["seen"]] == [
        "Got your message", "Chose qwen3:4b to answer", "Skipped repeating the same step",
        "Picked up your note: use metric", "Write notes.md",
    ]


# ---- 3. in every locale ---------------------------------------------------------------------------


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_the_card_speaks_each_shipped_language(monkeypatch, locale: str) -> None:
    events = _served_task_events(
        monkeypatch, [_outcome("weather_lookup", ok=False, state="failed", failure="URLError", location="rome")]
    )
    events.append({"type": "tool.failed", "tool": "acme.zorblax_v2", "summary": "Finished acme.zorblax_v2: E42"})
    data = _card(events, locale)
    what = _msg(locale, "activity.plain.what.weather")
    assert data["seen"][0]["stage"] == _msg(locale, "activity.plain.lookup_active", what=what)
    assert data["seen"][1]["stage"] == _msg(locale, "activity.plain.lookup_failed", what=what)
    assert data["seen"][2]["stage"] == _msg(locale, "activity.plain.step_failed")
    shown = _all_card_text(data)
    for english in ("Looking up", "Looked up", "didn’t work", "live_data", "acme"):
        assert english not in shown, (locale, english, shown)


# ---- 4. the Activity panel chrome -----------------------------------------------------------------

PANEL = """
document.body.classList.add('panel-open');
const run = adoptRun(displayedChat, newRun(displayedChat));
applyTaskEvent(run, { type: 'tool.started', tool: 'run_tests', summary: 'x', stage: 'Testing' });
applyTaskEvent(run, { type: 'tool.completed', tool: 'run_tests', summary: 'x', stage: 'Testing', measurable: { current: 1, total: 2 } });
applyTaskEvent(run, { type: 'tool.completed', tool: 'edit_file', summary: 'x', stage: 'Editing', path: '/w/a.txt' });
buildTabs();
const tabs = xpTabsEl.children.map((b) => b.innerHTML.replace(/ <span.*$/, ''));
const scopes = document.getElementById('xpScope').children.map((o) => o.textContent);
scopeNoteHtml();
const note = document.getElementById('xpScopeNote').textContent;
panelTab = 'Activity'; renderPanelBody();
const copyTitle = document.getElementById('xpCopy').title;
const actions = activityTreeActionsHtml().replace(/<[^>]*>/g, '|').split('|').filter(Boolean);
const emptyTurn = activityTreeBodyHtml(null).replace(/<[^>]*>/g, '');
panelTab = 'Steps'; run.steps = []; renderPanelBody();
const stepsEmpty = xpBodyEl.innerHTML.replace(/<[^>]*>/g, '');
panelTab = 'Preview'; renderPanelBody();
const previewEmpty = xpBodyEl.innerHTML.replace(/<[^>]*>/g, '');
view.recoveredTask = { execution_history: {}, request_preview: '' };
const recovery = renderRecoveryHtml().replace(/<[^>]*>/g, '|').split('|').filter(Boolean);
out({ tabs, scopes, note, copyTitle, actions, emptyTurn, stepsEmpty, previewEmpty, recovery });
__report(); process.exit(0);
"""


def _panel(locale: str) -> dict:
    data = run_node(DOM + _script(locale) + PANEL)
    assert data["errors"] == [], data["errors"]
    return data


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "en"])
def test_the_panel_chrome_speaks_each_shipped_language(locale: str) -> None:
    en, data = _panel("en"), _panel(locale)
    # English reads exactly as it always has.
    assert en["tabs"] == ["Steps", "Activity", "Changes", "Files", "Event log", "Tests", "Council"]
    assert en["scopes"] == ["Current chat", "Current project", "All activity"]
    assert en["note"] == "Showing only this chat’s evidence."
    assert en["copyTitle"] == "Copy Activity (this chat)"
    assert en["actions"] == ["Expand all", "Collapse all", "Copy all"]
    assert en["emptyTurn"] == "No recorded activity for this turn."
    assert en["stepsEmpty"] == "Planning…"
    assert en["previewEmpty"] == "No preview for this task."
    assert en["recovery"] == [
        "Task interrupted by application restart", "Saved work can be continued from its checkpoint.",
        "Resume", "Review activity", "Cancel task",
    ]
    tab_keys = ["steps", "activity", "changes", "files", "event_log", "tests", "council"]
    assert data["tabs"] == [_msg(locale, "panel.tab_" + k) for k in tab_keys]
    for field in ("scopes", "note", "actions", "emptyTurn", "stepsEmpty", "previewEmpty", "recovery"):
        assert data[field] != en[field], (locale, field, data[field])
    # Each piece of chrome is translated on its own, not just the list as a whole.
    for field in ("scopes", "actions", "recovery"):
        same = [a for a, b in zip(data[field], en[field], strict=True) if a == b]
        assert not same, (locale, field, same)
    assert _msg(locale, "panel.tab_activity") in data["copyTitle"]


# The static header of the panel: every visible word carries a catalog key whose English is exactly
# the text the page ships, so the bundle's DOM pass replaces it in every locale.
def _panel_markup() -> str:
    html = render_vool_chat_html()
    lo = html.index('<div id="xpResize"')
    hi = html.index('<div class="xp-body" id="xpBody"')
    return html[lo:hi]


def test_the_static_panel_header_is_keyed_for_every_language() -> None:
    markup = _panel_markup()
    en = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    elements = re.findall(r"<(\w+)([^>]*)>([^<]*)", markup)
    unkeyed: list[str] = []
    for tag, attrs, text in elements:
        pairs = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        for attr, key_attr in (("title", "data-i18n-title"), ("aria-label", "data-i18n-aria-label"),
                               ("placeholder", "data-i18n-placeholder")):
            if attr in pairs:
                key = pairs.get(key_attr)
                if not key:
                    unkeyed.append(f"{tag} {attr}={pairs[attr]!r}")
                else:
                    assert en.get(key) == pairs[attr], (key, en.get(key), pairs[attr])
        word = text.strip()
        if word and re.search(r"[A-Za-z]", re.sub(r"&#?\w+;", "", word)):   # an entity (&times;) is no word
            key = pairs.get("data-i18n")
            if not key:
                unkeyed.append(f"{tag} text={word!r}")
            else:
                assert en.get(key) == word, (key, en.get(key), word)
    assert not unkeyed, unkeyed
    for locale in LOCALES:
        if locale == "en":
            continue
        messages = json.loads((CATALOGS_DIR / f"{locale}.json").read_text(encoding="utf-8"))["messages"]
        for key in set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', markup)):
            assert messages.get(key) and messages[key] != en[key], (locale, key)
