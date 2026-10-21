"""The Activity panel speaks plain words; the engine's exact text stays one click away (J-06).

A fresh install asking "what is the weather like in rome" used to open Activity on rows such as
"Conservation: slots conserved, publication NOT conserved (evidence: text_ladder_over_final_bytes)",
"Runtime attempt attempt-… -> FAILED_TOOL." and "Live-data plan livedata-…: 0/1 succeeded,
max_concurrent=1" -- the ledger's own messages, printed as row titles by the browser projection
(`ledgerRow` title = label + ": " + message, then filed verbatim by `buildActivityTree`).

The records are evidence other code relies on, so they are NOT changed. What changed is the
presentation: `activityPlainRow` gives each event a plain title by its TYPE, files bookkeeping under
a collapsed "Behind the scenes", and an unknown type fails closed to a calm generic line. These tests
EXECUTE the real page script under node over the event shapes the server actually emitted for that
turn (copied from a live ledger, ids shortened), and pin both halves: the default view carries no
engine text, and the exact machine text is still in the row details, Copy all and the Event log.
"""

from __future__ import annotations

import json
import re

from tests.chat_page_js_harness import DOM, run_node, script

TURN = "turn-rome"

# The live ledger of one "what is the weather like in rome" turn on a fresh install with no model.
WEATHER_TURN = [
    {
        "seq": 1,
        "event_type": "task_received",
        "message": "Received request: what is the weather like in rome",
        "request_preview": "what is the weather like in rome",
    },
    {
        "seq": 2,
        "event_type": "runtime_attempt_created",
        "message": "Runtime attempt attempt-e20eb4f1a9e78c9b created (LIVE_DATA).",
        "attempt_id": "attempt-e20eb4f1a9e78c9b",
        "answer_mode": "LIVE_DATA",
    },
    {
        "seq": 3,
        "event_type": "runtime_attempt_updated",
        "message": "Runtime attempt attempt-e20eb4f1a9e78c9b -> PLANNED.",
        "lifecycle_state": "PLANNED",
        "retryable": False,
    },
    {
        "seq": 4,
        "event_type": "live_data_plan_created",
        "message": "Live-data plan livedata-f0b8c0b0: 1 subtasks",
        "plan_id": "livedata-f0b8c0b0",
        "subtasks": [{"operation": "weather_lookup", "entity": "Rome", "subtask_id": "livedata-f0b8c0b0:weather:rome"}],
    },
    {
        "seq": 5,
        "event_type": "agent_node_started",
        "message": "livedata-f0b8c0b0:weather:rome started (weather_lookup)",
        "lane": "live_data",
        "operation": "weather_lookup",
    },
    {
        "seq": 6,
        "event_type": "agent_node_completed",
        "message": "livedata-f0b8c0b0:weather:rome failed in 0.6s",
        "lane": "live_data",
        "ok": False,
        "failure_reason": "URLError: <urlopen error Tunnel connection failed: 403 Forbidden>",
    },
    {
        "seq": 7,
        "event_type": "live_data_operation_outcome",
        "message": "Live-data operation weather_lookup: failed",
        "operation": "weather_lookup",
        "lifecycle": "failed",
        "reason": "URLError: <urlopen error Tunnel connection failed: 403 Forbidden>",
    },
    {
        "seq": 8,
        "event_type": "web_retrieval_completed",
        "message": "Completed 1 live-data retrieval(s).",
        "plan_id": "livedata-f0b8c0b0",
        "receipts": [{"status": "failed", "failure_class": "URLError"}],
    },
    {
        "seq": 9,
        "event_type": "tool_selected",
        "message": "Running live_data.weather_lookup",
        "tool_name": "live_data.weather_lookup",
    },
    {
        "seq": 10,
        "event_type": "tool_failed",
        "message": "Finished live_data.weather_lookup: failed",
        "tool_name": "live_data.weather_lookup",
    },
    {
        "seq": 11,
        "event_type": "live_data_plan_completed",
        "message": "Live-data plan livedata-f0b8c0b0: 0/1 succeeded, max_concurrent=1",
    },
    {
        "seq": 12,
        "event_type": "runtime_attempt_completed",
        "message": "Runtime attempt attempt-e20eb4f1a9e78c9b -> FAILED_TOOL.",
        "lifecycle_state": "FAILED_TOOL",
        "retryable": True,
    },
    {"seq": 13, "event_type": "task_classified", "message": "Task classified as research.", "task_class": "research"},
    {
        "seq": 14,
        "event_type": "model_routing_started",
        "message": "Autopilot routed summarization through the daily lane.",
        "lane": "daily",
    },
    {
        "seq": 15,
        "event_type": "model_lane_selected",
        "message": "Selected ollama-local:qwen3:4b for the daily lane.",
        "actual_adapter_provider_id": "ollama-local:qwen3:4b",
        "actual_adapter_model_id": "qwen3:4b",
        "lane": "daily",
    },
    {
        "seq": 16,
        "event_type": "model_lane_failed",
        "message": "ollama-local:qwen3:4b failed; trying fallback if available.",
        "model_id": "qwen3:4b",
        "provider_id": "ollama-local:qwen3:4b",
        "error": "author_not_certified_for_final_answer",
        "lane": "daily",
    },
    {
        "seq": 17,
        "event_type": "cloud_route_declined",
        "message": "Cloud route not consulted: free_cloud_disabled.",
        "fallback_reason": "free_cloud_disabled",
    },
    {
        "seq": 18,
        "event_type": "model_routing_failed",
        "message": "All ranked provider lanes failed.",
        "rejection_reason": "all_ranked_providers_failed",
    },
    {
        "seq": 19,
        "event_type": "task_failed",
        "message": "Task did not meet its completion requirements.",
        "task_class": "research",
    },
    {
        "seq": 20,
        "event_type": "grounding_required",
        "message": "This turn needs current information; its grounding lifecycle is open.",
    },
    {
        "seq": 21,
        "event_type": "semantic_resolution_receipt",
        "message": "Resolution: 10 gates consulted, preempted by nothing",
        "reason": "model_lane",
    },
    {"seq": 22, "event_type": "turn.trace_completed", "message": "Turn trace completed."},
    {
        "seq": 23,
        "event_type": "grounding_publication",
        "message": "Grounding lifecycle published: every stage reached.",
        "state": "published",
    },
    {
        "seq": 24,
        "event_type": "semantic_conservation_verdict",
        "message": "Conservation: slots conserved, publication NOT conserved (evidence: text_ladder_over_final_bytes)",
        "reason": "semantic_conservation",
    },
]
for _event in WEATHER_TURN:
    _event["client_turn_id"] = TURN

# Every engine phrase the review saw in the default view, plus the id / enum shapes behind them.
ENGINE_TEXT = re.compile(
    r"Conservation:|gates consulted|Classified|Runtime attempt|attempt-[0-9a-f]|livedata-|Live-data|"
    r"max_concurrent|FAILED_TOOL|PLANNED|LIVE_DATA|free_cloud_disabled|Cloud route|Grounding lifecycle|"
    r"Turn trace|->|live_data\.|ollama-local|research\b|\b[a-z]+_[a-z_]+\b"
)


def _tree(events: list[dict]) -> dict:
    return run_node(
        DOM
        + script()
        + "\nconst EVENTS = "
        + json.dumps(events)
        + """;
const tree = buildActivityTree(EVENTS);
const cats = tree.categories.map((c) => ({ key: c.key, label: c.label, items: c.items.map((i) => ({
  title: i.display ? i.display.title : i.tool, sub: i.display ? i.display.sub : '',
  details: activityItemDetailLines(i, i.display ? i.display.title : i.tool).map((l) => l.join(': ')),
})) }));
const html = tree.categories.map((c) => activityCategoryHtml(c, {})).join('');
const htmlOpened = tree.categories.map((c) => activityCategoryHtml(c, { background: true })).join('');
out({ cats, treeText: activityTreeText(tree), html, htmlOpened, totalActions: tree.totalActions,
      machine: EVENTS.map((e) => { const r = ledgerRow(e); return r ? r.title : null; }) });
"""
    )


def _titles(result: dict, key: str) -> list[str]:
    cat = next((c for c in result["cats"] if c["key"] == key), None)
    return [i["title"] for i in cat["items"]] if cat else []


def test_the_weather_turn_reads_as_plain_words() -> None:
    result = _tree(WEATHER_TURN)
    main = _titles(result, "runtime")
    searches = _titles(result, "searches")
    # The lookup says what it was for and that it did not work -- not "Search failed" + a tool id.
    assert searches == ["Looked up the weather in Rome: it didn’t work"]
    for expected in (
        "Got your message",
        "Worked out what you’re asking for",
        "Chose qwen3:4b to answer",
        "qwen3:4b couldn’t answer",
        "Didn’t use an online model",
        "No model could answer",
        "Couldn’t finish this one",
        "This needs up-to-date information",
        "Checked the answer against what was found",
    ):
        assert expected in main, (expected, main)
    # No title a person reads by default carries engine text, ids or enum names.
    shown = main + searches
    leaked = [t for t in shown if ENGINE_TEXT.search(t)]
    assert not leaked, leaked


def test_bookkeeping_files_behind_the_scenes_collapsed_and_last() -> None:
    result = _tree(WEATHER_TURN)
    keys = [c["key"] for c in result["cats"]]
    assert keys[-1] == "background", keys
    background = _titles(result, "background")
    assert "Kept track of this attempt" in background
    assert "Checked nothing was lost from the answer" in background
    assert "Matched your question to the right kind of answer" in background
    # The live-data plan's own retrieval receipt repeats the lookup row, and it FAILED: it must not
    # read "Searched the web" anywhere.
    assert "Looked it up, but nothing came back" in background
    assert "Searched the web" not in background + _titles(result, "runtime")
    # Bookkeeping is not counted as an action the user's request took.
    assert result["totalActions"] == 1
    # Collapsed by default, open when the person (or Expand all) opens it.
    bg_default = re.search(r'<details class="xp-cat" data-node-id="background"( open)?>', result["html"])
    main_default = re.search(r'<details class="xp-cat" data-node-id="runtime"( open)?>', result["html"])
    assert bg_default and bg_default.group(1) is None
    assert main_default and main_default.group(1) == " open"
    assert re.search(r'<details class="xp-cat" data-node-id="background" open>', result["htmlOpened"])
    assert "Behind the scenes" in result["html"] and "What VOOL did" in result["html"]


def test_the_exact_machine_text_is_still_reachable() -> None:
    """Nothing is deleted: details, Copy all and the machine row keep every engine word."""
    result = _tree(WEATHER_TURN)
    everything = json.dumps(result["cats"], ensure_ascii=False)
    for raw in (
        "Conservation: slots conserved, publication NOT conserved (evidence: text_ladder_over_final_bytes)",
        "Resolution: 10 gates consulted, preempted by nothing",
        "Runtime attempt attempt-e20eb4f1a9e78c9b -> FAILED_TOOL.",
        "Live-data plan livedata-f0b8c0b0: 0/1 succeeded, max_concurrent=1",
        "Cloud route not consulted: free_cloud_disabled.",
        "live_data.weather_lookup",
    ):
        assert raw in everything, raw
        assert raw in result["treeText"], raw
    # ledgerRow -- the Event log, the copied failure report and the pinned machine contract -- is
    # unchanged: still the engine's own words.
    assert (
        "Conservation: slots conserved, publication NOT conserved (evidence: text_ladder_over_final_bytes)"
        in result["machine"]
    )
    assert "Classified: research" in result["machine"]


def test_an_unknown_event_type_fails_closed_to_a_calm_line() -> None:
    raw = "Quantum flux capacitor engaged: shard=7 epoch=0x2f (QFC_ENGAGED)"
    result = _tree([{"seq": 1, "event_type": "qfc_engaged_v9", "message": raw, "client_turn_id": TURN}])
    assert _titles(result, "background") == ["Worked on your request"]
    assert _titles(result, "runtime") == []
    shown = json.dumps([i["title"] for c in result["cats"] for i in c["items"]])
    assert "Quantum" not in shown and "QFC" not in shown
    # ...and the raw line is still there for whoever opens the row.
    item = result["cats"][0]["items"][0]
    assert raw in item["sub"] or any(raw in d for d in item["details"])
    assert raw in result["treeText"]


def test_two_places_for_one_lookup_name_neither() -> None:
    """A plan that asks the weather in two cities cannot pin one city to an unlabelled tool row."""
    events = [
        {
            "seq": 1,
            "event_type": "live_data_plan_created",
            "message": "Live-data plan livedata-aa: 2 subtasks",
            "subtasks": [
                {"operation": "weather_lookup", "entity": "Rome"},
                {"operation": "weather_lookup", "entity": "Paris"},
            ],
        },
        {
            "seq": 2,
            "event_type": "tool_selected",
            "tool_name": "live_data.weather_lookup",
            "message": "Running live_data.weather_lookup",
        },
        {
            "seq": 3,
            "event_type": "tool_executed",
            "tool_name": "live_data.weather_lookup",
            "message": "Finished live_data.weather_lookup: ok",
        },
    ]
    result = _tree(events)
    assert _titles(result, "searches") == ["Looked up the weather"]


def test_tool_rows_outside_live_data_keep_their_plain_titles() -> None:
    """Pinned behaviour elsewhere (Editing / Running tests) is the plain title already."""
    events = [
        {
            "seq": 1,
            "event_type": "tool_selected",
            "tool_name": "workspace.write_file",
            "message": "Running workspace.write_file.",
        },
        {"seq": 2, "event_type": "tool_selected", "tool_name": "pytest_runner", "message": "Running pytest_runner."},
    ]
    result = _tree(events)
    assert _titles(result, "edits") == ["Editing"]
    assert _titles(result, "tests") == ["Running tests"]


# Failure types the runtime emits from core/ (stepped_audit, agent, orchestration.executor,
# research_tool_loop_facade), with the messages those emit sites write.
NAMED_FAILURES = [
    ("audit_budget_refused", "audit stopped: budget_exhausted"),
    ("conductor_integrity_failure", "Conductor plan cp-77: requirement accounting is broken"),
    ("task_envelope_dependency_failed", "child env-12 failed: dependency dep-3 raised"),
    ("task_envelope_restore_failed", "Restore of envelope env-12 failed: snapshot missing"),
    ("task_envelope_capacity_blocked", "worker envelope `env-12` is blocked by capacity policy."),
    ("attempt_claim_refused", "Execution claim refused for attempt attempt-9f: CAS lost. This executor aborts."),
    ("code_task_refusal_returned", "workspace.write_file: refused. Returning the task's refusal to the model."),
]


def test_named_failures_are_visible_failure_lines_not_behind_the_scenes() -> None:
    events = [
        {"seq": i + 1, "event_type": t, "message": m, "client_turn_id": TURN} for i, (t, m) in enumerate(NAMED_FAILURES)
    ]
    result = _tree(events)
    # audit_budget_refused closes a tool call; every other one is a step row in "What VOOL did".
    assert _titles(result, "background") == []
    shown = [i for c in result["cats"] for i in c["items"]]
    assert len(shown) == len(NAMED_FAILURES)
    titles = [i["title"] for i in shown]
    assert "Worked on your request" not in titles
    assert not [t for t in titles if ENGINE_TEXT.search(t)], titles
    assert "Couldn’t start this attempt" in titles and "That step was refused" in titles
    # Drawn as failures: the fail class on every one of them.
    assert result["html"].count('class="xp-item fail"') == len(NAMED_FAILURES), result["html"]
    for _, message in NAMED_FAILURES:
        assert message in result["treeText"]


def test_an_unknown_failure_fails_closed_to_a_visible_failure_line() -> None:
    """Unknown type, failure by name or by its own status: visible, failed, calm, raw kept."""
    events = [
        {
            "seq": 1,
            "event_type": "widget_sync_rejected",
            "message": "Widget sync rejected: WS_E42",
            "client_turn_id": TURN,
        },
        {
            "seq": 2,
            "event_type": "widget_sync",
            "message": "Widget sync: error E7",
            "status": "error",
            "client_turn_id": TURN,
        },
        {"seq": 3, "event_type": "widget_probe", "message": "probe p-1 ok=false", "ok": False, "client_turn_id": TURN},
        {"seq": 4, "event_type": "widget_noted", "message": "noted n-1", "status": "recorded", "client_turn_id": TURN},
    ]
    result = _tree(events)
    assert _titles(result, "runtime") == ["This step didn’t work"] * 3
    assert _titles(result, "background") == ["Worked on your request"]
    failed = next(c for c in result["cats"] if c["key"] == "runtime")["items"]
    for item in failed:
        assert item["sub"] == "What happened is in the details below."
    assert result["html"].count('class="xp-item fail"') == 3
    for raw in ("Widget sync rejected: WS_E42", "Widget sync: error E7", "probe p-1 ok=false"):
        assert raw in result["treeText"]
        assert raw not in json.dumps([i["title"] + i["sub"] for i in failed])
