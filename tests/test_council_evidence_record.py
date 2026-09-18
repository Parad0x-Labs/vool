"""Council evidence record + seat provenance: the ledger IS the record, and a seat's
model and chat identity are what actually happened.

Three separate untruths are pinned here, each of which reads as working until you check:

* the polled state snapshot carried every seat's FULL report text and was rewritten
  after every seat, so the record and the view were the same bytes twice — and the view
  is the one that gets re-shipped on every 2.5s poll;
* nothing recorded which model actually answered a seat. The state carried the model the
  operator ASKED for, which is a request, not evidence;
* the ``session_id`` stamped on every SeatReport pointed at a chat that does not exist:
  the generated id is not canonical, so ``/api/chat`` re-hashed it and persisted the
  seat's turn somewhere else entirely.

Worst cases first: an actual model that DIFFERS from the requested one, a stream that
offers no identity at all, a stream that offers only request-strength identity, and the
exact page boundary where an off-by-one in ``complete`` lies to a paging client.
"""

from __future__ import annotations

import hashlib
import io
import json
import re

import pytest

from core.council import dispatch
from core.council.orchestrator import CouncilOrchestrator, Seat
from core.council.run_store import CouncilRunStore

CANONICAL_SESSION = re.compile(r"^openclaw:[0-9a-f]{20}$")


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        out.mkdir(parents=True, exist_ok=True)
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    return tmp_path


def _bench():
    return [
        Seat("s1", "builder", "vendor/alpha", True),
        Seat("s2", "falsifier", "vendor/beta", True),
        Seat("s3", "reviewer", "vendor/gamma", True),
    ]


DIAG = "DIAGNOSIS: the snapshot and the record are the same bytes, twice."
# No trailing whitespace: the runtime stores the STRIPPED report, and the recorded hash
# is over those exact bytes. A fixture ending in a space would be asserting against text
# the runtime never stored.
LONG_REPORT = DIAG + "\n" + ("evidence line with detail that belongs in the record. " * 40).strip()


def _report(text, receipts=0, **extra):
    return {"text": text, "receipt_count": receipts, "session_id": None, **extra}


def _converging_script(builder_text=DIAG):
    script = {
        ("s1", 1): _report(builder_text),
        ("s2", 1): _report("looked around"),
        ("s3", 1): _report("looked around"),
    }
    for seat_id in ("s1", "s2", "s3"):
        script[(seat_id, 2)] = _report("VERDICT: AGREE")
    return script


class Scripted:
    def __init__(self, script):
        self.script = script

    def __call__(self, seat, prompt, round_no, run_id):
        return self.script[(seat.seat_id, round_no)]


def _rows(state):
    return [row for rnd in state["rounds"] for row in rnd["reports"]]


def _run(store_fixture, script=None, seats=None):
    run = CouncilOrchestrator(
        problem="p", seats=seats or _bench(),
        seat_turn=Scripted(script or _converging_script()), max_rounds=3,
    )
    run.run()
    return run


# ------------------------------------------------- 1-3: the ledger is the record ----

def test_new_snapshot_does_not_inline_complete_report_text(isolated_store):
    run = _run(isolated_store, _converging_script(LONG_REPORT))
    state = run.store.read_state()
    for row in _rows(state):
        assert "text" not in row, (
            "the snapshot still inlines the full report — it is rewritten after every seat "
            "and re-shipped on every poll"
        )
        assert row["report_sha256"], row
        assert row["text_ref"]["kind"] == "ledger"
        assert isinstance(row["text_ref"]["seq"], int) and row["text_ref"]["seq"] >= 1
    # The record still holds every byte, exactly once.
    reports = [e for e in run.store.read_events() if e["type"] == "seat_report"]
    assert len(reports) == len(_rows(state))
    assert any(e["text"] == LONG_REPORT for e in reports)


def test_text_ref_resolves_to_the_exact_stored_text_and_hash(isolated_store):
    run = _run(isolated_store, _converging_script(LONG_REPORT))
    row = next(r for r in _rows(run.store.read_state()) if r["seat_id"] == "s1" and r["round_no"] == 1)

    resolved = run.store.read_event(row["text_ref"]["seq"])
    assert resolved is not None, "the text_ref points at nothing"
    assert resolved["type"] == "seat_report"
    assert resolved["seat_id"] == "s1" and resolved["round_no"] == 1
    assert resolved["text"] == LONG_REPORT
    assert hashlib.sha256(resolved["text"].encode("utf-8")).hexdigest() == row["report_sha256"], (
        "the recorded hash is not the hash of the stored bytes"
    )


def test_events_api_resolves_one_report_and_verifies_its_hash(isolated_store, monkeypatch):
    from core.council import api as council_api

    run = _run(isolated_store, _converging_script(LONG_REPORT))
    row = next(r for r in _rows(run.store.read_state()) if r["seat_id"] == "s1")
    status, payload = council_api.events(run.run_id, seq=row["text_ref"]["seq"])
    assert status == 200 and payload["ok"]
    assert len(payload["events"]) == 1, "a seq lookup must resolve exactly one event"
    event = payload["events"][0]
    assert event["seq"] == row["text_ref"]["seq"]
    assert event["text"] == LONG_REPORT
    assert payload["text_verified"] is True, "the API did not verify the hash it served"

    status, payload = council_api.events(run.run_id, seq=999_999)
    assert status == 404 and payload["ok"] is False


def test_legacy_inline_state_and_no_seq_ledger_stay_readable(isolated_store):
    """CONTROL. A run written before this change carries inline text and a ledger with no
    sequence. Both must keep serving without being rewritten or migrated."""
    run_id = "council-legacyinline1"
    store = CouncilRunStore(run_id)
    legacy_state = {
        "state": "converged", "problem": "p", "seats": [],
        "rounds": [{"round_no": 1, "reports": [{
            "seat_id": "s1", "round_no": 1, "status": "landed",
            "text": "the old inline body", "report_sha256": "deadbeef",
        }]}],
    }
    store.write_state(legacy_state)
    ledger = store._ledger_path()
    ledger.write_text(
        json.dumps({"ts": 1.0, "run_id": run_id, "type": "convened"}) + "\n", encoding="utf-8"
    )
    before_ledger = ledger.read_text(encoding="utf-8")

    read_back = store.read_state()
    assert _rows(read_back)[0]["text"] == "the old inline body", "legacy inline text was dropped"
    ordered = store.read_events_ordered()
    assert [e["seq"] for e in ordered] == [1]
    assert ordered[0]["seq_source"] == "file_order"
    assert ledger.read_text(encoding="utf-8") == before_ledger, "a legacy ledger was rewritten"


# ------------------------------------------------ 4-5: seat model provenance ----


class _Resp(io.BytesIO):
    def __init__(self, payload: bytes, status: int = 200):
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeHTTP:
    def __init__(self, routes):
        self.routes = routes
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, request, timeout=None):
        path = "/" + request.full_url.split("/", 3)[3]
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        self.requests.append((path, body))
        status, payload = self.routes[path]
        if isinstance(payload, bytes):
            return _Resp(payload, status)
        return _Resp(json.dumps(payload).encode("utf-8"), status)


def _stream_with_model(model_block):
    lines = []
    if model_block is not None:
        lines.append(json.dumps({"vool_event": {
            "type": "model.call_completed", "raw_type": "model.call_completed",
            "model": model_block,
        }}))
    lines.append(json.dumps({"message": {"content": "VERDICT: AGREE"}, "done": True}))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _turn_with(monkeypatch, model_block, seat_model="vendor/requested-one"):
    fake = _FakeHTTP({
        "/api/cloud/model": (200, {"ok": True}),
        "/api/chat": (200, _stream_with_model(model_block)),
    })
    monkeypatch.setattr("urllib.request.urlopen", fake)
    turn = dispatch.live_seat_turn_factory("http://127.0.0.1:9999")
    return turn(Seat("s1", "builder", seat_model, True), "p", 2, "council-provenance1")


def test_actual_model_comes_from_streamed_adapter_evidence_not_the_request(monkeypatch):
    """The requested model and the model that answered DIFFER. Only the second is actual."""
    result = _turn_with(monkeypatch, {
        "requested_model_id": "requested-one",
        "selected_model_id": "selected-two",
        "actual_adapter_model_id": "actual-three",
        "actual_adapter_provider_id": "vendor",
    })
    assert result["model_requested"] == "vendor/requested-one"
    assert result["model_actual"] == "vendor/actual-three", (
        "the adapter's own record of what it invoked was ignored"
    )
    assert result["model_evidence"] == "actual_adapter"


def test_selected_evidence_is_used_only_when_no_adapter_proof_exists(monkeypatch):
    result = _turn_with(monkeypatch, {
        "requested_model_id": "requested-one",
        "selected_model_id": "selected-two",
        "selected_provider_id": "vendor",
    })
    assert result["model_actual"] == "vendor/selected-two"
    assert result["model_evidence"] == "selected"


def test_absent_evidence_never_fabricates_actual_from_the_request(monkeypatch):
    """No identity on the wire at all. `actual` stays unknown — it is NOT the request."""
    result = _turn_with(monkeypatch, None)
    assert result["model_requested"] == "vendor/requested-one"
    assert result["model_actual"] is None, "a request was relabelled as evidence of execution"
    assert result["model_evidence"] == "unknown"


def test_request_strength_identity_is_labelled_and_never_promoted_to_actual(monkeypatch):
    """The stream carried identity, but only at REQUEST strength. That is not proof that
    the model answered, so `actual` stays null and the label says where we got to."""
    result = _turn_with(monkeypatch, {
        "requested_model_id": "requested-one",
        "requested_provider_id": "vendor",
    })
    assert result["model_evidence"] == "requested"
    assert result["model_actual"] is None, "request-strength identity was promoted to actual"


def test_model_provenance_reaches_the_durable_state(isolated_store):
    script = _converging_script()
    script[("s1", 1)] = _report(
        DIAG, model_requested="vendor/alpha",
        model_actual="vendor/actually-answered", model_evidence="actual_adapter",
    )
    run = _run(isolated_store, script)
    row = next(r for r in _rows(run.store.read_state()) if r["seat_id"] == "s1" and r["round_no"] == 1)
    assert row["model_requested"] == "vendor/alpha"
    assert row["model_actual"] == "vendor/actually-answered"
    assert row["model_evidence"] == "actual_adapter"
    # A seat whose dispatch reported nothing must not inherit the bench's request.
    other = next(r for r in _rows(run.store.read_state()) if r["seat_id"] == "s2" and r["round_no"] == 1)
    assert other["model_requested"] == "vendor/beta"
    assert other["model_actual"] is None
    assert other["model_evidence"] == "unknown"


# ------------------------------------------------- 6-7: seat chat identity ----

def test_seat_session_id_is_canonical_and_survives_the_server_normalizer():
    """The generated id must be `openclaw:<20 lowercase hex>`. Anything else is re-hashed
    by /api/chat, and the id recorded on the report then points at no chat at all."""
    from core.web.api.runtime import stable_openclaw_session_id

    seat_a = Seat("s1", "builder", "vendor/alpha", True)
    seat_b = Seat("s2", "falsifier", "vendor/beta", True)
    minted = dispatch.seat_session_id("council-abc123def456", seat_a)
    assert CANONICAL_SESSION.match(minted), f"not canonical: {minted!r}"

    persisted = stable_openclaw_session_id(
        body={"session_id": minted}, history=[], headers={}, allow_canonical_resume=True
    )
    assert persisted == minted, (
        "the server re-hashed the seat id — the recorded pointer is dead on arrival"
    )
    assert minted == dispatch.seat_session_id("council-abc123def456", seat_a), "not stable"
    assert minted != dispatch.seat_session_id("council-abc123def456", seat_b)
    assert minted != dispatch.seat_session_id("council-zzz999zzz999", seat_a)


def test_recorded_seat_session_resolves_through_chat_history(tmp_path, monkeypatch):
    """End of the round trip: the id the report carries must open the chat that holds
    that seat's own exchange."""
    monkeypatch.setattr("core.runtime_paths.active_data_dir", lambda: tmp_path)
    from core.persistent_memory import append_conversation_event
    from core.web.api.runtime import RuntimeServices, stable_openclaw_session_id
    from core.web.api.service import dispatch_get

    seat = Seat("s2", "falsifier", "vendor/beta", True)
    # What the report RECORDS.
    recorded = dispatch.seat_session_id("council-roundtrip01", seat)
    # Where /api/chat ACTUALLY persists it: the id is put through the same normalizer the
    # endpoint uses, so a non-canonical id is re-hashed here exactly as it is in production.
    persisted_under = stable_openclaw_session_id(
        body={"session_id": recorded}, history=[], headers={}, allow_canonical_resume=True
    )
    append_conversation_event(
        session_id=persisted_under,
        user_input="COUNCIL RUN council-roundtrip01 - ROUND 1 (INVESTIGATE)",
        assistant_output="COUNTEREXAMPLE: the ledger had no reader.",
        source_context={"surface": "council"},
    )
    assert persisted_under == recorded, (
        f"the seat turn persists under {persisted_under!r} but the report records "
        f"{recorded!r} — the recorded pointer is dead"
    )
    session_id = recorded
    res = dispatch_get(path="/api/chat/history", query={"session": [session_id]},
                       runtime=RuntimeServices(display_name="N"), model_name="vool",
                       client_host="127.0.0.1")
    payload = json.loads(res.body.decode("utf-8"))
    assert payload["session_id"] == session_id
    joined = " ".join(m["content"] for m in payload["messages"])
    assert "ROUND 1" in joined, "the seat's own prompt is not in the chat it names"
    assert "COUNTEREXAMPLE" in joined, "the seat's report is not in the chat it names"


# ---------------------------------------------------- 8: pagination boundary ----

@pytest.mark.parametrize("total", [499, 500, 501])
def test_events_pagination_is_exact_at_the_page_boundary(isolated_store, total):
    """At exactly one full page the old `len(page) < LIMIT` said "incomplete" while
    nothing remained — a paging client would loop forever on an empty tail."""
    from core.council import api as council_api

    run_id = f"council-page{total}"
    store = CouncilRunStore(run_id)
    store.write_state({"state": "converged", "problem": "p", "seats": [], "rounds": []})
    for index in range(total):
        store.append_event("round_opened", round_no=index)

    status, payload = council_api.events(run_id)
    assert status == 200
    served = len(payload["events"])
    assert served == min(total, 500)
    expected_more = total > 500
    assert payload["has_more"] is expected_more, (
        f"{total} events: has_more said {payload['has_more']}, {total - served} remain"
    )
    assert payload["complete"] is (not expected_more)
    assert payload["next_after"] == served

    if expected_more:
        status, rest = council_api.events(run_id, after=payload["next_after"])
        assert len(rest["events"]) == total - served
        assert rest["has_more"] is False and rest["complete"] is True


# --------------------------------------------------- 9: C1 laws still hold ----

def test_c1_owner_local_wall_still_covers_the_events_surface(isolated_store):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(path="/api/council/events", query={"run": ["council-x"]},
                       runtime=RuntimeServices(display_name="N"), model_name="vool",
                       client_host="10.0.0.9")
    assert res.status == 403
