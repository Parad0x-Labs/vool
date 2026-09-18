"""C14 — measured spend is captured, carried, aggregated and SHOWN, never inferred.

The seat turn is a real ``/api/chat`` turn and its stream already carries a typed
``cloud.cost_updated`` event with the numbers the provider reported (prompt/output
tokens, ``usd_actual``). Before this law those numbers died on the wire while the card
said "spending on N cloud seats" — an inference from model strings, not a measurement.

Four seams, one direction of travel:

1. dispatch captures the measured cost events into a ``usage`` dict on the seat result;
2. the orchestrator carries ``usage`` on the seat report, into the ledger and snapshot;
3. the scorecard aggregates per-seat and run totals and states the bound honestly
   (missing usage is a LOWER BOUND, never zeros sold as measurement);
4. the chat card shows the measured totals instead of the inferred "spending on …".

A missing cost event is a fact (``complete: false``), never fabricated zeros.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from core.council import run_store
from core.council.orchestrator import CouncilOrchestrator, Seat
from core.council.scorecard import scorecard_from_events


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        return out

    monkeypatch.setattr(run_store, "data_path", _patched)
    return tmp_path


def _orchestrator(seat_turn, seats=None, max_rounds=4) -> CouncilOrchestrator:
    return CouncilOrchestrator(
        problem="measure what the bench actually spent",
        seats=seats
        or [
            Seat("s1", "builder", "vendor/alpha", votes=True),
            Seat("s2", "falsifier", "vendor/beta", votes=True),
            Seat("s3", "verifier", "vendor/gamma", votes=True),
        ],
        seat_turn=seat_turn,
        max_rounds=max_rounds,
    )


# ------------------------------------------------------------- 1. dispatch capture


class _FakeResponse:
    """A byte stream shaped like the seat turn's NDJSON reply (or a JSON pin reply)."""

    def __init__(self, lines: list[bytes] | None = None, body: bytes = b""):
        self._lines = lines or []
        self._body = body
        self.status = 200
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def close(self):
        self.closed = True


def _cost_event(*, prompt=100, output=50, usd=0.0, cost_class="free_cloud") -> bytes:
    return (
        json.dumps(
            {
                "vool_event": {
                    "type": "cloud.cost_updated",
                    "raw_type": "model_usage",
                    "summary": "vendor/alpha used 150 tokens.",
                    "cost": {
                        "cost_class": cost_class,
                        "paid": "paid" in cost_class,
                        "usd_actual": usd,
                        "prompt_tokens": prompt,
                        "output_tokens": output,
                    },
                }
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _seat_stream(lines: list[bytes], monkeypatch) -> dict[str, object]:
    """Drive ``live_seat_turn`` against a fake stream and return its result dict."""

    captured: dict[str, object] = {}

    def _fake_urlopen(request, timeout=None):
        path = getattr(request, "full_url", "")
        if path.endswith("/api/cloud/model"):
            # The model-pin write succeeds: these tests are about the turn, not the fence.
            return _FakeResponse(body=b'{"ok": true}')
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(lines)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    from core.council.dispatch import live_seat_turn_factory

    turn = live_seat_turn_factory("http://127.0.0.1:1", "cap-token")
    seat = Seat("s1", "builder", "vendor/alpha", votes=True)
    return turn(seat, "investigate the defect", 1, "council-spendtest")


# ------------------------------------------ 5. the wire's real typed-event shape


def test_receipt_and_cost_events_are_read_off_the_real_wire_shape(monkeypatch):
    """The served stream emits ``{"vool_event": {"type": ..., "cost": ...}}`` —
    ``build_task_event`` renames ``event_type`` to ``type``. A reader keyed on
    ``event_type`` counts zero receipts on every real turn, which silently disables
    receipt-backed counterexample precedence (the verdict-precedence law itself)."""
    lines = [
        json.dumps({"vool_event": {"type": "tool.started", "raw_type": "tool_started"}}).encode() + b"\n",
        json.dumps({"vool_event": {"type": "tool.completed", "raw_type": "tool_completed"}}).encode() + b"\n",
        json.dumps({"vool_event": {"type": "tool.completed", "raw_type": "tool_completed"}}).encode() + b"\n",
        _cost_event(prompt=100, output=50, usd=0.0),
        b'{"model":"m","message":{"content":"COUNTEREXAMPLE: it does not reproduce"}}\n',
        b'{"model":"m","message":{"content":"diagnosis prose"},"done":true}\n',
    ]
    result = _seat_stream(lines, monkeypatch)
    assert result["receipt_count"] == 2, (
        "receipts must be counted off the REAL wire shape, or a receipt-backed "
        f"counterexample can never fire live; got {result.get('receipt_count')}"
    )
    assert result["usage"]["prompt_tokens"] == 100


def test_dispatch_captures_measured_usage_from_the_seat_stream(monkeypatch):
    lines = [
        _cost_event(prompt=100, output=50, usd=0.0),
        b'{"model":"vendor/alpha","message":{"role":"assistant","content":"DIAGNOSIS: x"},"done":false}\n',
        b'{"model":"vendor/alpha","message":{"role":"assistant","content":""},"done":true}\n',
    ]
    result = _seat_stream(lines, monkeypatch)
    usage = result.get("usage")
    assert isinstance(usage, dict), "the seat result carries no measured usage at all"
    assert usage["prompt_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["usd_actual"] == 0.0, "a measured zero (free) is a measurement, keep it"
    assert usage["cost_class"] == "free_cloud"
    assert usage["complete"] is True


def test_dispatch_sums_multi_call_turn_usage(monkeypatch):
    """A seat turn with a tool loop makes several provider calls; the usage is the SUM."""
    lines = [
        _cost_event(prompt=100, output=50, usd=0.0),
        _cost_event(prompt=200, output=80, usd=0.25),
        b'{"model":"m","message":{"content":"DIAGNOSIS: y"},"done":true}\n',
    ]
    usage = _seat_stream(lines, monkeypatch)["usage"]
    assert usage["prompt_tokens"] == 300
    assert usage["output_tokens"] == 130
    assert usage["usd_actual"] == pytest.approx(0.25)


def test_a_turn_with_no_cost_event_reports_incomplete_never_fabricated_zeros(monkeypatch):
    lines = [b'{"model":"m","message":{"content":"DIAGNOSIS: z"},"done":true}\n']
    result = _seat_stream(lines, monkeypatch)
    usage = result.get("usage")
    assert usage is not None, "absence of measurement must be stated, not silent"
    assert usage["complete"] is False
    assert usage.get("prompt_tokens") in (None, 0) and usage.get("output_tokens") in (None, 0)
    assert usage.get("usd_actual") is None, "no provider cost arrived; None is the truth"


def test_partial_usage_from_a_call_missing_tokens_is_incomplete(monkeypatch):
    lines = [
        _cost_event(prompt=100, output=50, usd=0.0),
        _cost_event(prompt=None, output=None, usd=None),
        b'{"model":"m","message":{"content":"DIAGNOSIS: w"},"done":true}\n',
    ]
    usage = _seat_stream(lines, monkeypatch)["usage"]
    assert usage["complete"] is False, "one call without usage makes the total a lower bound"


# --------------------------------------------------- 2. report carries it onward


def _scripted_turn(usage=None, text="DIAGNOSIS: scripted root cause."):
    def _turn(seat, prompt, round_no, run_id):
        return {"text": text, "receipt_count": 1, "session_id": f"stub-{seat.seat_id}",
                "usage": usage}

    return _turn


def test_seat_report_ledger_row_carries_measured_usage(isolated_store):
    usage = {"cost_class": "free_cloud", "prompt_tokens": 120, "output_tokens": 60,
             "usd_actual": 0.0, "complete": True}
    orch = _orchestrator(_scripted_turn(usage=usage))
    orch.seats = [Seat("s1", "builder", "vendor/alpha", votes=True)]
    orch.run()
    events = orch.store.read_events()
    landed = [e for e in events if e.get("type") == "seat_report" and e.get("status") == "landed"]
    assert landed and landed[0].get("usage") == usage, (
        "the ledger row must carry the measurement; a spend nobody can audit later is not "
        "visible spend"
    )


def test_snapshot_rows_carry_usage_and_failed_reports_carry_none(isolated_store):
    usage = {"prompt_tokens": 10, "output_tokens": 5, "usd_actual": None, "complete": False}

    def _turn(seat, prompt, round_no, run_id):
        if seat.seat_id == "s2":
            raise RuntimeError("transport dead")
        return {"text": "DIAGNOSIS: ok" if seat.role_id == "builder" else "looked",
                "receipt_count": 0, "usage": usage}

    orch = _orchestrator(_turn, max_rounds=2)
    orch.run()  # ends needs_attention: s2 failed, a missing vote is never voted away
    reports = orch.rounds[0]
    landed = next(r for r in reports if r.seat_id == "s1")
    failed = next(r for r in reports if r.seat_id == "s2")
    assert landed.usage == usage
    assert not failed.usage, "a seat that never answered has no usage to show"


# --------------------------------------------------------- 3. scorecard aggregates


def _ledger_with_usage(isolated_store, per_seat_usage, statuses=None):
    """Build a real run ledger through the orchestrator with per-seat scripted usage."""
    statuses = statuses or {}

    def _turn(seat, prompt, round_no, run_id):
        status = statuses.get(seat.seat_id, "landed")
        if status == "failed":
            raise RuntimeError("seat dead")
        usage = per_seat_usage.get(seat.seat_id)
        text = "DIAGNOSIS: root cause found." if round_no == 1 else "VERDICT: AGREE"
        return {"text": text, "receipt_count": 1, "usage": usage}

    orch = _orchestrator(_turn)
    return orch


def test_scorecard_aggregates_measured_spend_per_seat_and_run(isolated_store):
    usage = {
        "s1": {"cost_class": "free_cloud", "prompt_tokens": 100, "output_tokens": 50,
               "usd_actual": 0.0, "complete": True},
        "s2": {"cost_class": "free_cloud", "prompt_tokens": 200, "output_tokens": 100,
               "usd_actual": 0.5, "complete": True},
        "s3": {"cost_class": "paid_cloud", "prompt_tokens": 40, "output_tokens": 20,
               "usd_actual": 1.25, "complete": True},
    }
    orch = _ledger_with_usage(isolated_store, usage)
    orch.run()
    card = scorecard_from_events(orch.store.read_events())
    by_model_token = {}
    for seat in card["seats"]:
        assert "usage" in seat, f"seat {seat['seat_id']} carries no spend block"
        by_model_token[seat["seat_id"]] = seat["usage"]
    # The bench converges in round 2, so each seat landed twice and carried its usage
    # both times: the seat total is twice one round's measurement.
    assert by_model_token["s1"]["prompt_tokens"] == 200
    assert by_model_token["s2"]["usd_actual"] == pytest.approx(1.0)
    run = card["run"]
    assert run["usage"]["prompt_tokens"] == 680
    assert run["usage"]["output_tokens"] == 340
    assert run["usage"]["usd_actual"] == pytest.approx(3.5)
    assert run["usage"]["complete"] is True


def test_scorecard_states_a_lower_bound_when_any_landed_seat_lacks_usage(isolated_store):
    usage = {
        "s1": {"prompt_tokens": 100, "output_tokens": 50, "usd_actual": 0.0, "complete": True},
        "s2": None,
        "s3": {"prompt_tokens": 40, "output_tokens": 20, "usd_actual": None, "complete": False},
    }
    orch = _ledger_with_usage(isolated_store, usage)
    orch.run()
    card = scorecard_from_events(orch.store.read_events())
    run_usage = card["run"]["usage"]
    assert run_usage["complete"] is False
    assert "lower bound" in str(run_usage.get("note", "")).lower(), (
        "a partial sum presented as the total is the exact dishonesty this law forbids"
    )
    seat_rows = {s["seat_id"]: s["usage"] for s in card["seats"]}
    assert seat_rows["s2"]["complete"] is False
    assert seat_rows["s2"].get("prompt_tokens") in (None, 0)
    assert seat_rows["s3"]["usd_actual"] is None, "no provider cost arrived; never estimate one here"


def test_a_failed_seat_makes_the_run_total_a_lower_bound(isolated_store):
    """A seat that dies mid-turn may have spent tokens nobody recorded. A run total
    that ignores that possibility overclaims `complete` — the exact spend-honesty
    defect this law exists to prevent."""
    usage = {
        "s1": {"cost_class": "free_cloud", "prompt_tokens": 100, "output_tokens": 50,
               "usd_actual": 0.0, "complete": True},
        "s2": None,
        "s3": {"cost_class": "paid_cloud", "prompt_tokens": 40, "output_tokens": 20,
               "usd_actual": 1.25, "complete": True},
    }
    orch = _ledger_with_usage(isolated_store, usage, statuses={"s2": "failed"})
    orch.run()
    card = scorecard_from_events(orch.store.read_events())
    run_usage = card["run"]["usage"]
    assert run_usage["complete"] is False, (
        "a run whose bench includes a failed seat cannot claim a complete spend total"
    )
    assert "lower bound" in str(run_usage.get("note", "")).lower()


# --------------------------------------------------------------- 4. the card shows it


def test_card_shows_measured_spend_not_the_inferred_cloud_count():
    from tests.test_council_chat_card import RUN, _state, drive

    state = _state()
    for report in state["rounds"][0]["reports"]:
        if report["status"] == "landed":
            report["usage"] = {"prompt_tokens": 1500, "output_tokens": 700,
                               "usd_actual": 0.0, "complete": True}
    body = """
      setDisplayedChat('openclaw:c3c3c3c3c3c3c3c3');
      renderChat('openclaw:c3c3c3c3c3c3c3c3');
      window.VoolCouncilCard.adopt('openclaw:c3c3c3c3c3c3c3c3', 'COUNCILRUN');
      await window.VoolCouncilCard.refresh('COUNCILRUN');
      var card = cardFor('COUNCILRUN');
      var head = textOf(one(card, cls('vcx-head')));
      var folds = all(card, cls('vcx-seat-row')).map(textOf);
      out({ head: head, seatRows: folds });
    """
    result = drive(body.replace("COUNCILRUN", RUN), status=state)
    head = result["head"]
    # Two landed reports each measured 1500+700 tokens → 4400 total.
    assert "4.4k tok" in head, (
        "the card must show the measured token total; got: " + head
    )
    assert "spending on" not in head, (
        "an inferred cloud-seat count must not stand in for the measured spend when a "
        "measurement exists: " + head
    )


def test_card_without_measurements_keeps_honest_waiting_wording():
    from tests.test_council_chat_card import RUN, _state, drive

    state = _state()  # no usage anywhere
    body = """
      setDisplayedChat('openclaw:c3c3c3c3c3c3c3c3');
      renderChat('openclaw:c3c3c3c3c3c3c3c3');
      window.VoolCouncilCard.adopt('openclaw:c3c3c3c3c3c3c3c3', 'COUNCILRUN');
      await window.VoolCouncilCard.refresh('COUNCILRUN');
      var card = cardFor('COUNCILRUN');
      out({ head: textOf(one(card, cls('vcx-head'))) });
    """
    result = drive(body.replace("COUNCILRUN", RUN), status=state)
    assert "measured" not in result["head"].lower(), (
        "with no measurement there is no measured spend to show: " + result["head"]
    )
