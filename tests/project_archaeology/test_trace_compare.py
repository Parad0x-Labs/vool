"""C21 project archaeology: trace/compare contract tests (written first, RED).

The module under test, ``core/project_archaeology.py``, is a READ-ONLY reader
that answers the C21 questions as data over the seeded one-night corpus:

- cross-store lineage: one turn joins blackbox + runtime_events + tool_receipts
- why did this fail: the push error surfaces wrapped as untrusted history
- which task changed this file: path -> turn/effect mapping
- what happened last night: the night window, not the next morning
- poison turns: injection payloads quarantined inside untrusted wrappers only
- honesty joins: the turn's receipt is part of the same story
- git/turn/file compare: bounded diffs with quarantined content
- read-only law: journal and workspace bytes are untouched by every call

``core.project_archaeology`` is imported lazily INSIDE each test so the file
collects cleanly while the module does not exist yet (RED = per-test errors).
Deterministic only: no network, no daemon, no live model.
"""
from __future__ import annotations

import json

import pytest

from tests.project_archaeology.conftest import (
    INJECTION_PAYLOAD,
    NIGHT_WINDOW,
    journal_count,
    receipt_key_for,
)

ENV_KEYS = {
    "schema", "operation", "query", "scope", "results", "returned", "truncated",
    "limits", "stores_read", "redaction", "untrusted_content", "execution",
}
SCOPE_KEYS = {
    "workspace_root", "store_roots", "grants", "refused", "content_access",
    "default_scope",
}
ITEM_KEYS = {
    "store", "object_id", "hash", "timestamp", "authority", "verified",
    "turn_id", "session_id", "effect_id", "attempt_id", "path", "kind",
    "outcome", "confidence", "availability", "excerpt", "truncated",
}
STORES_READ_KEYS = {"store", "root", "entries", "verified"}
UNTRUSTED_BEGIN = "[untrusted-history:begin]"


def _pa():
    """Lazy import of the module under test (may not exist yet: RED)."""
    from core import project_archaeology as pa

    return pa


def _item_json(item) -> str:
    return json.dumps(item, default=str, sort_keys=True)


# ---------------------------------------------------------------------------
# envelope + item shape
# ---------------------------------------------------------------------------


def test_trace_envelope_carries_the_full_contract_shape(corpus):
    """Every envelope key, scope key, item key and store-read key is present,
    with the pinned schema string and read-only execution marker."""
    pa = _pa()
    env = pa.trace(
        turn_id="turn-night-2", workspace_root=str(corpus["workspace"])
    )

    assert set(env) >= ENV_KEYS
    assert env["schema"] == "vool.archaeology.v1"
    assert env["operation"] == "trace"
    assert env["untrusted_content"] is True
    assert env["execution"] == "none"

    assert set(env["scope"]) >= SCOPE_KEYS
    assert isinstance(env["scope"]["store_roots"], dict)
    assert isinstance(env["scope"]["grants"], list)
    assert isinstance(env["scope"]["refused"], list)
    assert isinstance(env["scope"]["content_access"], bool)
    assert isinstance(env["scope"]["default_scope"], bool)

    assert "turn-night-2" in _item_json(env["query"])
    assert env["returned"] == len(env["results"])
    assert isinstance(env["truncated"], bool)
    assert isinstance(env["limits"], dict)

    assert env["results"], "the seeded push turn must match something"
    for item in env["results"]:
        assert set(item) >= ITEM_KEYS
        assert isinstance(item["store"], str)

    assert env["stores_read"], "at least one store must be read"
    for read in env["stores_read"]:
        assert set(read) >= STORES_READ_KEYS
        assert isinstance(read["entries"], int)
        assert isinstance(read["verified"], bool)
    assert "blackbox" in {read["store"] for read in env["stores_read"]}

    assert isinstance(env["redaction"]["secrets_detected"], int)
    assert isinstance(env["redaction"]["excerpts_wrapped"], int)
    assert env["redaction"]["secrets_detected"] >= 0
    assert env["redaction"]["excerpts_wrapped"] >= 0

    # trace ADDS timeline + lineage on top of the shared envelope
    assert isinstance(env["timeline"], list)
    assert isinstance(env["lineage"], dict)
    assert "stores_joined" in env["lineage"]


# ---------------------------------------------------------------------------
# cross-store lineage
# ---------------------------------------------------------------------------


def test_trace_of_failed_push_turn_joins_blackbox_runtime_events_and_tool_receipts(corpus):
    """One turn id stitches three authorities: the journal effect, the runtime
    failure event and the tool receipt all answer for turn-night-2."""
    pa = _pa()
    env = pa.trace(turn_id="turn-night-2", workspace_root=str(corpus["workspace"]))

    results = env["results"]
    assert env["returned"] == len(results) > 0

    stores = {item["store"] for item in results}
    assert {"blackbox", "runtime_events", "tool_receipts"} <= stores

    assert any(
        item["store"] == "blackbox" and item["effect_id"] == "eff-push-77"
        for item in results
    ), "the journal effect eff-push-77 must be in the join"
    assert any(
        item["store"] == "runtime_events"
        and "effect_failed" in _item_json(item)
        for item in results
    ), "the runtime effect_failed event must be in the join"
    assert any(
        item["store"] == "tool_receipts"
        and receipt_key_for(2) in _item_json(item)
        for item in results
    ), f"receipt {receipt_key_for(2)} must be in the join"

    joined = set(env["lineage"]["stores_joined"])
    assert {"blackbox", "runtime_events", "tool_receipts"} <= joined

    for item in results:
        assert (
            item["turn_id"] == "turn-night-2" or item["session_id"] == "sess-night"
        ), "every joined item must tie back to the queried turn's session"


def test_timeline_sorts_night_history_ascending_with_empty_timestamps_last(corpus):
    """The trace timeline is the result set re-sorted by timestamp ascending;
    items without a timestamp sink to the end."""
    pa = _pa()
    env = pa.trace(
        session_id="sess-night",
        since=NIGHT_WINDOW[0],
        until=NIGHT_WINDOW[1],
        workspace_root=str(corpus["workspace"]),
    )

    timeline = env["timeline"]
    assert timeline, "the night window must produce a timeline"
    stamps = [item["timestamp"] for item in timeline]
    assert stamps == sorted(stamps, key=lambda s: (s == "", s))
    assert {item["store"] for item in timeline} <= {
        item["store"] for item in env["results"]
    }


# ---------------------------------------------------------------------------
# why did this fail
# ---------------------------------------------------------------------------


def test_trace_explains_why_the_push_failed_with_wrapped_error_excerpt(corpus):
    """The push failure's real error text surfaces in some item's outcome and
    excerpt, wrapped as untrusted history — never as bare trusted prose."""
    pa = _pa()
    env = pa.trace(turn_id="turn-night-2", workspace_root=str(corpus["workspace"]))

    failed = [item for item in env["results"] if item["outcome"] == "failed"]
    assert failed, "eff-push-77 failed; a failed-outcome item must exist"

    excerpts = [item["excerpt"] or "" for item in failed]
    assert any(
        UNTRUSTED_BEGIN in text and "pre-receive hook declined" in text
        for text in excerpts
    ), "the error text must appear inside a quarantined excerpt"


# ---------------------------------------------------------------------------
# which task changed this file
# ---------------------------------------------------------------------------


def test_which_task_changed_this_file_maps_config_and_report_to_their_turns(corpus):
    """src/config.py traces back to eff-config-08/turn-night-3, while the night
    report traces back to BOTH its creation (eff-report-01) and its push
    attempt (eff-push-77)."""
    pa = _pa()

    config = pa.trace(path="src/config.py", workspace_root=str(corpus["workspace"]))
    assert config["returned"] > 0
    config_items = [
        item for item in config["results"] if item["effect_id"] == "eff-config-08"
    ]
    assert config_items, "eff-config-08 must answer for src/config.py"
    assert all(
        item["turn_id"] == "turn-night-3" and item["session_id"] == "sess-night"
        for item in config_items
    ), "the items must record the turn and session that changed the file"

    report = pa.trace(
        path="reports/night-report.md", workspace_root=str(corpus["workspace"])
    )
    effect_ids = {item["effect_id"] for item in report["results"]}
    assert {"eff-report-01", "eff-push-77"} <= effect_ids, (
        "the report path must show both the creation and the failed push"
    )


# ---------------------------------------------------------------------------
# what happened last night
# ---------------------------------------------------------------------------


def test_what_happened_last_night_returns_only_the_night_window(corpus):
    """The night query spans 22:05 -> 23:41 and stops before the next
    morning: turn-day-1 / eff-day-01 stay out of the results."""
    pa = _pa()
    env = pa.trace(
        session_id="sess-night",
        since=NIGHT_WINDOW[0],
        until=NIGHT_WINDOW[1],
        workspace_root=str(corpus["workspace"]),
    )

    dumped = _item_json(env["results"])
    assert "eff-day-01" not in dumped
    assert "turn-day-1" not in dumped

    effect_ids = {item["effect_id"] for item in env["results"]}
    assert {"eff-report-01", "eff-push-77", "eff-config-08", "eff-poison-09"} <= effect_ids

    stamps = [item["timestamp"] for item in env["timeline"] if item["timestamp"]]
    assert stamps, "the night timeline must carry timestamps"
    assert stamps[0] == "2026-09-03T22:05:00+00:00"
    assert stamps[-1] == "2026-09-03T23:41:00+00:00"


def test_trace_honors_limit_and_reports_truncation(corpus):
    """A bounded night query returns at most ``limit`` items and flags that
    more history exists behind the cap."""
    pa = _pa()
    env = pa.trace(
        session_id="sess-night",
        since=NIGHT_WINDOW[0],
        until=NIGHT_WINDOW[1],
        limit=2,
        workspace_root=str(corpus["workspace"]),
    )

    assert 0 < env["returned"] == len(env["results"]) <= 2
    assert env["truncated"] is True


def test_trace_of_unknown_turn_is_a_typed_empty_search_not_an_error(corpus):
    """A miss is a result: returned == 0, nothing truncated — a search, not a
    lookup, so an unknown id must not raise."""
    pa = _pa()
    env = pa.trace(turn_id="turn-missing-999", workspace_root=str(corpus["workspace"]))

    assert env["returned"] == 0
    assert env["results"] == []
    assert env["truncated"] is False


def test_trace_without_selectors_raises_input_error():
    """With no turn/session/effect/attempt/path/window at all there is nothing
    to trace: the reader refuses via ArchaeologyInputError."""
    pa = _pa()
    with pytest.raises(pa.ArchaeologyInputError):
        pa.trace()


# ---------------------------------------------------------------------------
# adversarial history: secret + injection payload
# ---------------------------------------------------------------------------


def test_trace_quarantines_injection_payload_only_inside_untrusted_excerpt_wrappers(corpus):
    """turn-night-4's crashed handler carries a prompt-injection payload: it may
    surface only inside quarantined excerpts, never in query/scope/lineage."""
    pa = _pa()
    env = pa.trace(turn_id="turn-night-4", workspace_root=str(corpus["workspace"]))

    assert env["returned"] > 0
    assert env["untrusted_content"] is True

    poisoned_excerpts = [
        item["excerpt"] or ""
        for item in env["results"]
        if INJECTION_PAYLOAD in (item["excerpt"] or "")
    ]
    assert poisoned_excerpts, "the payload must surface in some excerpt"
    for text in poisoned_excerpts:
        assert UNTRUSTED_BEGIN in text, "payload-bearing excerpts must be wrapped"

    for section in ("query", "scope", "lineage"):
        assert INJECTION_PAYLOAD not in _item_json(env.get(section)), (
            f"the raw payload must never leak into the {section} section"
        )


# ---------------------------------------------------------------------------
# honesty join
# ---------------------------------------------------------------------------


def test_trace_joins_the_honesty_receipt_for_turn_night_1(corpus):
    """The honesty verdict issued for the report turn joins the same story via
    session/turn, appearing as a honesty_receipts store item."""
    pa = _pa()
    env = pa.trace(turn_id="turn-night-1", workspace_root=str(corpus["workspace"]))

    assert env["returned"] > 0
    assert "honesty_receipts" in {item["store"] for item in env["results"]}


# ---------------------------------------------------------------------------
# compare: git commits, turns, files
# ---------------------------------------------------------------------------


def test_compare_git_commits_reports_changed_config_and_untouched_report(corpus):
    """Diffing sha_a..sha_b sees src/config.py change while the unchanged night
    report stays out of changed_paths; both sides resolve to git items."""
    pa = _pa()
    env = pa.compare(
        kind="git_commits",
        a=corpus["git"]["sha_a"],
        b=corpus["git"]["sha_b"],
        workspace_root=corpus["git_repo"],
    )

    comparison = env["comparison"]
    assert comparison["kind"] == "git_commits"
    assert comparison["a"] and comparison["b"]
    assert comparison["a"]["store"] == "git"
    assert comparison["a"]["hash"] == corpus["git"]["sha_a"]
    assert comparison["b"]["store"] == "git"
    assert comparison["b"]["hash"] == corpus["git"]["sha_b"]

    assert "src/config.py" in comparison["changed_paths"]
    assert "reports/night-report.md" not in comparison["changed_paths"]

    summary = comparison["summary"]
    assert isinstance(summary, str) and 0 < len(summary) <= 2000


def test_compare_turns_contrasts_creation_with_the_failed_push(corpus):
    """Comparing turn-night-1 with turn-night-2 contrasts eff-report-01 with
    eff-push-77 and reports the file both turns touched."""
    pa = _pa()
    env = pa.compare(kind="turns", a="turn-night-1", b="turn-night-2")

    comparison = env["comparison"]
    assert comparison["kind"] == "turns"
    assert comparison["a"] and comparison["b"]
    assert "eff-report-01" in _item_json(comparison["a"])
    assert "eff-push-77" in _item_json(comparison["b"])
    assert "reports/night-report.md" in comparison["changed_paths"]


def test_compare_files_diffs_content_and_identity(corpus):
    """File compare reads bounded, quarantined CONTENT: two different files
    register as changed, a file against itself registers no change."""
    pa = _pa()
    workspace_root = str(corpus["workspace"])

    diff = pa.compare(
        kind="files",
        a="reports/night-report.md",
        b="src/config.py",
        workspace_root=workspace_root,
    )["comparison"]
    assert diff["kind"] == "files"
    assert diff["a"] and diff["b"]
    assert diff["changed_paths"], "differing contents must register as changed"
    excerpts = [(diff["a"] or {}).get("excerpt") or "", (diff["b"] or {}).get("excerpt") or ""]
    assert any(excerpts), "file compare must expose bounded content excerpts"
    for text in excerpts:
        if text:
            assert UNTRUSTED_BEGIN in text, "file content excerpts must be wrapped"

    same = pa.compare(
        kind="files",
        a="reports/night-report.md",
        b="reports/night-report.md",
        workspace_root=workspace_root,
    )["comparison"]
    assert same["changed_paths"] == [], "a file against itself must show no change"


def test_compare_rejects_unknown_kind_and_missing_operands():
    """Validation is typed: an unknown comparison kind, or git_commits without
    both operands, raise ArchaeologyInputError."""
    pa = _pa()
    with pytest.raises(pa.ArchaeologyInputError):
        pa.compare(kind="bogus", a="x", b="y")
    with pytest.raises(pa.ArchaeologyInputError):
        pa.compare(kind="git_commits", a="0" * 40)
    with pytest.raises(pa.ArchaeologyInputError):
        pa.compare(kind="git_commits", b="0" * 40)


# ---------------------------------------------------------------------------
# read-only law
# ---------------------------------------------------------------------------


def test_read_only_law_journal_and_workspace_untouched_by_trace_and_compare(corpus):
    """After tracing and comparing, the blackbox journal still holds exactly 9
    entries and the workspace report is byte- and mtime-identical."""
    pa = _pa()
    report = corpus["workspace"] / "reports" / "night-report.md"
    before_bytes = report.read_bytes()
    before_mtime_ns = report.stat().st_mtime_ns
    assert journal_count(corpus["blackbox_root"]) == 9

    pa.trace(turn_id="turn-night-2", workspace_root=str(corpus["workspace"]))
    pa.trace(
        session_id="sess-night",
        since=NIGHT_WINDOW[0],
        until=NIGHT_WINDOW[1],
        workspace_root=str(corpus["workspace"]),
    )
    pa.compare(kind="turns", a="turn-night-1", b="turn-night-2")
    pa.compare(
        kind="files",
        a="reports/night-report.md",
        b="src/config.py",
        workspace_root=str(corpus["workspace"]),
    )

    assert journal_count(corpus["blackbox_root"]) == 9
    assert report.read_bytes() == before_bytes
    assert report.stat().st_mtime_ns == before_mtime_ns
