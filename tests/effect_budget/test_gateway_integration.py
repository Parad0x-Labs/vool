"""THE ONE REAL GATE — budgets enforced where effects are authorized.

`EffectLedger.open_effect` is the convergence point every effect class flows
through; the budget reserve happens THERE (not in prompt prose, not in a
lane's private count), consumption happens at `begin_attempt`, and the
scope's close rolls back authorized-but-never-executed effects.
"""
from __future__ import annotations

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import effect_budget as eb
from core.effect_gateway import (
    DECISION_ALLOWED,
    DECISION_DENIED,
    EFFECT_NETWORK_FETCH,
    LIFECYCLE_AUTHORIZED,
    LIFECYCLE_SUCCEEDED,
    EffectReceipt,
    close_effect_receipt_scope,
    current_effect_ledger,
    named_background_effect_scope,
    open_effect_receipt_scope,
)
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures


class _ControlledPageTransport:
    """A real loopback HTTP server: the web this suite controls.

    The budget acceptance contract below used to be "exercised" with ``data:``
    URLs — a scheme the door now refuses, correctly, before any handler — so
    the old fixture stopped proving anything about a real fetch. This server is
    the honest replacement: the door's ordinary policy/ledger path runs
    unchanged, and its transport really connects — to this server, on loopback,
    serving fixed bytes. The handler's own record of what it served is the
    wire-level proof of which requests did and did not reach a socket.
    """

    def __init__(self) -> None:
        self.served: list[str] = []
        self._lock = threading.Lock()
        outer = self

        class _Pages(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # the http.server API name (N802 ignored for tests/)
                with outer._lock:
                    outer.served.append(self.path)
                body = {"/one": b"one", "/two": b"two"}.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass  # this suite's own record is the evidence; keep runner logs clean

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _ControlledPageTransport:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _must_not_open_transport(*args: object, **kwargs: object) -> None:
    """Stand-in for `urllib.request.urlopen`: reaching it for a non-web scheme
    means a handler already ran (a file read, a socket) — the exact thing the
    door's scheme law forbids."""
    raise AssertionError("the door reached a transport for a non-web scheme")


def _authorized_receipt(effect_class: str = EFFECT_NETWORK_FETCH) -> EffectReceipt:
    return EffectReceipt(
        effect_class=effect_class,
        decision=DECISION_ALLOWED,
        lifecycle=LIFECYCLE_AUTHORIZED,
        reason="test",
    )


def _turn_context(turn_id: str = "turn-1", session_id: str = "sess-1"):
    from core.turn_contract import TURN_REQUEST_KEY

    class _Request:
        pass

    request = _Request()
    request.turn_id = turn_id
    request.request_id = f"req-{turn_id}"
    request.session_id = session_id
    return {
        TURN_REQUEST_KEY: request,
        "turn_id": turn_id,
        "request_id": f"req-{turn_id}",
        "session_id": session_id,
        "workspace_root": "/tmp/project-x",
    }


def test_authorized_effect_reserves_at_the_gate(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 3))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        lifecycle = ledger.open_effect(_authorized_receipt())
        lifecycle.begin_attempt()
        lifecycle.succeed()
        rows = eb.reservation_rows()
        assert len(rows) == 1
        assert rows[0]["state"] == eb.RESERVATION_CONSUMED
        assert rows[0]["session_id"] == "sess-1"
        assert rows[0]["project_key"] == "/tmp/project-x"
        assert rows[0]["effect_id"] == lifecycle.effect_id
    finally:
        close_effect_receipt_scope()


def test_exhausted_budget_denies_at_the_gate_with_typed_code(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        first = ledger.open_effect(_authorized_receipt())
        first.begin_attempt()
        first.succeed()
        from core.effect_budget import EffectBudgetRefusedError

        with pytest.raises(EffectBudgetRefusedError) as refusal:
            ledger.open_effect(_authorized_receipt())
        assert refusal.value.code == eb.REFUSAL_BUDGET_EXCEEDED
        assert refusal.value.rule == "network_fetch/session"
        # the denial is a FIRST-CLASS FACT on the turn's own account
        denials = [
            entry
            for entry in ledger.entries()
            if entry.get("decision") == DECISION_DENIED
            and entry.get("decided_by") == "core.effect_budget"
        ]
        assert len(denials) == 1
        assert denials[0]["reason"].startswith("EFFECT_BUDGET_EXCEEDED")
        assert "refused" in eb.budget_events("refused")[0]["event_kind"]
    finally:
        close_effect_receipt_scope()


def test_denied_effect_never_touches_the_budget(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_DENIED,
                lifecycle="denied",
                reason="mode matrix refused",
            )
        )
        assert eb.reservation_rows() == [], "a denial reserves nothing"
    finally:
        close_effect_receipt_scope()


def test_scope_close_rolls_back_authorized_never_executed(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))
    ledger = open_effect_receipt_scope(_turn_context())
    executed = ledger.open_effect(_authorized_receipt())
    executed.begin_attempt()
    executed.succeed()
    never_ran = ledger.open_effect(_authorized_receipt())  # authorized, no attempt
    close_effect_receipt_scope()
    states = {row["effect_id"]: row["state"] for row in eb.reservation_rows()}
    assert states[executed.effect_id] == eb.RESERVATION_CONSUMED
    assert states[never_ran.effect_id] == eb.RESERVATION_RELEASED
    assert eb.budget_status("network_fetch", session_id="sess-1")[0].used == 1


def test_retry_does_not_reserve_a_second_unit(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        first = ledger.open_effect(_authorized_receipt())
        first.begin_attempt()
        first.fail(reason="timeout")
        # the retry continues the SAME logical effect — no second unit
        retry = ledger.open_effect(_authorized_receipt(), retry_of=first.effect_id)
        assert retry.effect_id == first.effect_id
        retry.begin_attempt()
        retry.succeed()
        assert len(eb.reservation_rows()) == 1
        assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED
        assert eb.budget_status("network_fetch", session_id="sess-1")[0].used == 1
    finally:
        close_effect_receipt_scope()


def test_begin_attempt_fails_closed_without_a_reservation(set_budget, monkeypatch):
    """Sabotage shape: the reserve is skipped (as if never wired) and the
    attempt begins anyway — the gate refuses at consumption time."""
    set_budget(("network_fetch", eb.SCOPE_SESSION, 5))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        lifecycle = ledger.open_effect(_authorized_receipt())
        eb.release_reservation(
            eb.reservation_rows()[0]["reservation_id"], reason="simulated skipped reserve"
        )
        from core.effect_budget import EffectBudgetRefusedError

        with pytest.raises(EffectBudgetRefusedError) as refusal:
            lifecycle.begin_attempt()
        assert refusal.value.code == eb.REFUSAL_STATE
        assert "re-reserve" in refusal.value.detail or "released" in refusal.value.detail
    finally:
        close_effect_receipt_scope()


def test_network_fetch_door_surfaces_the_typed_refusal(set_budget):
    """End-to-end through the real transport door over a controlled HTTP
    server: the FIRST fetch actually runs and consumes exactly one budget
    unit; the SECOND is refused with the budget's typed code before any
    second transport attempt; the ledger holds both the consumed effect's
    final state and the denial receipt."""
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

    set_budget(("network_fetch", eb.SCOPE_TURN, 1))
    ledger = open_effect_receipt_scope(_turn_context(turn_id="door-turn"))
    try:
        with _ControlledPageTransport() as web:
            # 1 — one real HTTP fetch: it runs the ordinary policy path, opens
            # a real socket to the controlled server, and returns its bytes.
            first = open_remote(urllib.request.Request(f"{web.base_url}/one"), timeout=5)
            try:
                assert first.status == 200
                assert first.read() == b"one"
            finally:
                first.close()
            assert web.served == ["/one"]
            # exactly one unit consumed, under this turn's identity, bound to
            # the effect that ran
            rows = eb.reservation_rows()
            assert len(rows) == 1
            assert rows[0]["state"] == eb.RESERVATION_CONSUMED
            assert rows[0]["turn_id"] == "door-turn"
            consumed_effect_id = rows[0]["effect_id"]

            # 2 — the second fetch is refused by the BUDGET (the scheme law is
            # not what stops it: both URLs are ordinary http), with the typed
            # code surfacing through the door's refusal.
            from core.effect_budget import EffectBudgetRefusedError

            with pytest.raises((RemoteFetchRefusedError, EffectBudgetRefusedError)) as refusal:
                open_remote(urllib.request.Request(f"{web.base_url}/two"), timeout=5)
            message = str(refusal.value)
            assert "EFFECT_BUDGET_EXCEEDED" in message, (
                f"the typed code must surface through the door: {message}"
            )
            assert "effect budget" in message, (
                f"the refusal is the budget's, not a re-classification: {message}"
            )
            # refused BEFORE another transport attempt: the controlled server
            # — the only endpoint either URL names — still saw exactly the one
            # request the first fetch made
            assert web.served == ["/one"], (
                f"a second transport attempt reached the wire: {web.served}"
            )
            # no second unit exists to be spent
            assert len(eb.reservation_rows()) == 1

        # 3 — the ledger records the right source and final state: the one
        # effect that ran is SUCCEEDED with the server as its host, and the
        # denial is a first-class fact decided by the budget
        outcome = next(
            o for o in ledger.effect_outcomes() if o["effect_id"] == consumed_effect_id
        )
        assert outcome["effect_class"] == EFFECT_NETWORK_FETCH
        assert outcome["host"] == "127.0.0.1"
        assert outcome["lifecycle"] == LIFECYCLE_SUCCEEDED
        assert outcome["transport_ran"] is True and outcome["attempts"] == 1
        denials = [
            entry
            for entry in ledger.entries()
            if entry.get("decision") == DECISION_DENIED
            and entry.get("decided_by") == "core.effect_budget"
        ]
        assert len(denials) == 1
        assert denials[0]["reason"].startswith("EFFECT_BUDGET_EXCEEDED")
        status = eb.budget_status("network_fetch", turn_id="door-turn", session_id="sess-1")
        assert status[0].used == 1 and status[0].remaining == 0
        assert "refused" in eb.budget_events("refused")[0]["event_kind"]
    finally:
        close_effect_receipt_scope()


def test_disallowed_scheme_neither_fetches_nor_spends(set_budget, tmp_path):
    """Control for the door's HTTP-only law inside the budget's own lane: a
    non-web reference is refused before any transport — no file read, no
    network call — and reserves NOTHING, so the turn's whole budget remains
    available to a real web fetch afterwards."""
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

    set_budget(("network_fetch", eb.SCOPE_TURN, 1))
    sentinel = tmp_path / "never-read.txt"
    sentinel.write_text("local bytes that must never leave through the door", encoding="utf-8")
    ledger = open_effect_receipt_scope(_turn_context(turn_id="scheme-turn"))
    try:
        # if urllib's opener is ever reached for either shape, local bytes have
        # already left the door's custody — the transport itself must not open
        with pytest.MonkeyPatch.context() as guard:
            guard.setattr(urllib.request, "urlopen", _must_not_open_transport)
            for url in (f"file://{sentinel}", "data:text/plain,no"):
                with pytest.raises(RemoteFetchRefusedError) as refusal:
                    open_remote(urllib.request.Request(url), timeout=5)
                assert "http/https" in str(refusal.value), str(refusal.value)
        # the scheme denials are first-class facts decided by the door's scheme
        # law — not budget refusals — and the budget store shows nothing
        scheme_denials = [
            entry
            for entry in ledger.entries()
            if entry.get("decided_by") == "remote_fetch_policy.http_scheme"
        ]
        assert len(scheme_denials) == 2
        assert eb.reservation_rows() == []
        assert eb.budget_events("refused") == []

        # the single unit is INTACT: one real web fetch through the same door
        # still succeeds and consumes it — disallowed schemes spent nothing
        with _ControlledPageTransport() as web:
            allowed = open_remote(urllib.request.Request(f"{web.base_url}/one"), timeout=5)
            try:
                assert allowed.read() == b"one"
            finally:
                allowed.close()
            assert web.served == ["/one"]
        rows = eb.reservation_rows()
        assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED
        status = eb.budget_status("network_fetch", turn_id="scheme-turn", session_id="sess-1")
        assert status[0].used == 1 and status[0].remaining == 0
    finally:
        close_effect_receipt_scope()


def test_named_background_scope_reserves_and_consumes(set_budget):
    """Background work budgets under the window/project dimensions (it has no
    turn/session identity — those rules honestly do not bind it)."""
    set_budget(("network_fetch", eb.SCOPE_PROJECT, 2))
    with named_background_effect_scope("watch.poll"):
        ledger = current_effect_ledger()
        lifecycle = ledger.open_effect(_authorized_receipt())
        lifecycle.begin_attempt()
        lifecycle.succeed()
        rows = eb.reservation_rows()
        assert rows[0]["project_key"] == "default"
        assert rows[0]["turn_id"] == "" and rows[0]["session_id"] == ""
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED


def test_unbudgeted_turn_works_unchanged():
    """No budgets configured: the gate adds nothing — base behavior, zero
    rows, zero events (the honest nothing of law 3)."""
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        lifecycle = ledger.open_effect(_authorized_receipt())
        lifecycle.begin_attempt()
        lifecycle.succeed()
        assert eb.reservation_rows() == []
        assert eb.budget_events() == []
        assert lifecycle.outcome()["lifecycle"] == LIFECYCLE_SUCCEEDED
    finally:
        close_effect_receipt_scope()


def test_preview_and_status_reach_the_gate_identity(set_budget):
    set_budget(("network_fetch", eb.SCOPE_TURN, 1))
    ledger = open_effect_receipt_scope(_turn_context("preview-turn"))
    try:
        identity = ledger._budget_identity()
        assert identity["turn_id"] == "preview-turn"
        assert identity["session_id"] == "sess-1"
        assert identity["project_key"] == "/tmp/project-x"
        preview = eb.preview_effect("network_fetch", **identity)
        assert preview.allowed is True and preview.per_rule[0].remaining == 1
        ledger.open_effect(_authorized_receipt())
        assert eb.preview_effect("network_fetch", **identity).allowed is False
    finally:
        close_effect_receipt_scope()
