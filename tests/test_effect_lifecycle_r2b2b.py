"""R2b2b — ONE DURABLE LIFECYCLE PER NETWORK TRANSPORT ATTEMPT.

R2b1 made the door deny without a ledger and own receipt identity, but a fetch
the door AUTHORIZED still ends its account at `authorized`: the receipt is
written before the socket, and nothing after it. Whether transport ran, and how
the attempt ended, is unprovable from the ledger -- an authorized fetch that
timed out reads identically to one that returned a page. The receipt mint also
gives every append a NEW effect id, so even if a later state were recorded it
could not be attributed to the same logical effect.

WHAT THIS FILE PINS (the objective's ten named cases, then the laws)

* authorized transport succeeds  -> authorized → started → succeeded, one id
* authorized transport raises    -> terminal failed, same id, safe reason
* authorized transport times out -> failed naming timeout, same id
* authorized transport cancelled -> terminal cancelled, same id
* denied before socket           -> one denied entry, no started, no success
* retry after failure            -> NEW attempt identity, SAME logical effect
* duplicate terminal write       -> idempotent: exactly one terminal per attempt
* concurrent effects             -> interleaved chains never cross ids
* nested scopes                  -> the chain survives the production scope shape
* missing context                -> no ledger means typed refusal, never a mint

plus: per-turn ceiling preserved (bounded registry + receipts), receipts carry
no secrets, TurnResult publishes the terminal lifecycle, and the served web
lane's page transport (`http_fetch_text`, a raw urlopen since the first day)
runs through the same door and therefore the same lifecycle.

New API is imported INSIDE each test: at the base commit every case below must
fail on its own line, not on one collection error.
"""
from __future__ import annotations

import ast
import concurrent.futures
import pathlib
import threading
import urllib.error
import urllib.request
from contextvars import copy_context

import pytest

from core.effect_gateway import (
    EFFECT_NETWORK_FETCH,
    MAX_RECEIPTS_PER_TURN,
    effect_receipts,
)
from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    remote_fetch_policy_scope,
    remote_fetch_turn_scope,
)

TURN = {"surface": "openclaw", "platform": "openclaw", "session_id": "r2b2b"}
VETO = {"allow_remote_fetch": False, "surface": "openclaw", "platform": "openclaw"}


# --------------------------------------------------------------------------- harness


class _Resp:
    """The door's return shape: anything with a status attribute."""

    def __init__(self, status: int = 200) -> None:
        self.status = status


class _PageResp:
    """http_fetch_text's return shape: a readable, closeable response."""

    def __init__(self, body: bytes = b"<html><body>words</body></html>") -> None:
        self._body = body
        self.status = 200
        self.headers = {"Content-Type": "text/html"}

    def geturl(self) -> str:
        return "https://example.invalid/"

    def read(self, *_a) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _lifecycles(effect_id: str = "") -> list[str]:
    entries = effect_receipts()
    if effect_id:
        entries = tuple(e for e in entries if e.get("effect_id") == effect_id)
    return [str(e.get("lifecycle") or "") for e in entries]


def _one_effect_id(expected_states: int) -> str:
    """The entries for one logical effect must carry ONE id across every state."""
    ids = {str(e.get("effect_id") or "") for e in effect_receipts()}
    assert len(ids) == 1, f"one logical effect must keep one effect_id, saw {ids}"
    (effect_id,) = ids
    assert effect_id, "the ledger must mint a non-empty effect_id"
    return effect_id


def _require_chain(effect_id: str, expected: list[str]) -> None:
    """The named invariant helper: ordered lifecycle chain under one id.

    Sabotage tests reuse this under `pytest.raises` — if a defect can pass it,
    the named tests have no teeth."""
    seen = _lifecycles(effect_id)
    assert seen == expected, f"effect {effect_id}: expected {expected}, account shows {seen}"


def _outcomes() -> tuple[dict, ...]:
    from core.effect_gateway import effect_outcomes

    return effect_outcomes()


# ------------------------------------------------------- 1. authorized transport succeeds


def test_authorized_transport_succeeds_ends_succeeded_not_authorized(monkeypatch):
    """Req 1/2/3. RED at base: the account stops at `authorized` — there is no
    started and no succeeded entry, so an authorized fetch and a completed one
    read identically."""
    seen_at_socket: list[list[str]] = []

    def _ok(request, timeout=None, **kw):
        seen_at_socket.append(_lifecycles())
        return _Resp(200)

    monkeypatch.setattr(urllib.request, "urlopen", _ok)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        response = open_remote(
            urllib.request.Request("https://example.invalid/page"), timeout=5
        )
        effect_id = _one_effect_id(3)
        _require_chain(effect_id, ["authorized", "started", "succeeded"])
        outcomes = _outcomes()

    assert seen_at_socket == [["authorized", "started"]], (
        "`started` must be on the record immediately BEFORE the transport call: "
        f"the socket saw {seen_at_socket}"
    )
    assert response.status == 200
    mine = [o for o in outcomes if o["effect_id"] == effect_id]
    assert len(mine) == 1
    assert mine[0]["lifecycle"] == "succeeded"
    assert mine[0]["transport_ran"] is True
    assert mine[0]["attempts"] == 1


# --------------------------------------------------------- 2. authorized transport raises


def test_authorized_transport_that_raises_ends_failed(monkeypatch):
    """Req 3. RED at base: a ConnectionRefusedError leaves the account at
    `authorized` — the failure is unprovable from the ledger."""

    def _refused(request, timeout=None, **kw):
        raise ConnectionRefusedError("connection refused by example.invalid")

    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        with pytest.raises(ConnectionRefusedError):
            open_remote(urllib.request.Request("https://example.invalid/x"), timeout=5)
        effect_id = _one_effect_id(3)
        _require_chain(effect_id, ["authorized", "started", "failed"])
        entries = effect_receipts()
        outcome = next(o for o in _outcomes() if o["effect_id"] == effect_id)

    failed = [e for e in entries if e["lifecycle"] == "failed"]
    assert len(failed) == 1
    assert "ConnectionRefusedError" in failed[0]["reason"], (
        "the failure entry must name the failure class it ended with"
    )
    assert "refused by example.invalid" not in failed[0]["reason"], (
        "the reason must be the safe type-derived token, not the raw exception text"
    )
    assert outcome["lifecycle"] == "failed"


# ---------------------------------------------------------- 3. authorized transport times out


def test_authorized_transport_that_times_out_ends_failed_naming_timeout(monkeypatch):
    """Req 3. RED at base: a timeout is indistinguishable from success."""

    def _timeout(request, timeout=None, **kw):
        raise urllib.error.URLError(TimeoutError("timed out fetching secret query"))

    monkeypatch.setattr(urllib.request, "urlopen", _timeout)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        with pytest.raises(urllib.error.URLError):
            open_remote(urllib.request.Request("https://example.invalid/slow"), timeout=5)
        effect_id = _one_effect_id(3)
        _require_chain(effect_id, ["authorized", "started", "failed"])
        failed = next(e for e in effect_receipts() if e["lifecycle"] == "failed")

    assert "timeout" in failed["reason"], f"the timeout must be named, saw {failed['reason']!r}"
    assert "secret query" not in failed["reason"]


# ------------------------------------------------------------ 4. authorized transport cancelled


def test_cancelled_transport_ends_cancelled_and_reraises(monkeypatch):
    """Req 3. Cancellation is a BaseException in the CancelledError family; it
    must terminalize as `cancelled` — not `failed`, and never be swallowed."""

    def _cancelled(request, timeout=None, **kw):
        raise concurrent.futures.CancelledError()

    monkeypatch.setattr(urllib.request, "urlopen", _cancelled)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        with pytest.raises(concurrent.futures.CancelledError):
            open_remote(urllib.request.Request("https://example.invalid/c"), timeout=5)
        effect_id = _one_effect_id(3)
        _require_chain(effect_id, ["authorized", "started", "cancelled"])


# -------------------------------------------------------------- 5. denied before the socket


def test_denied_before_socket_never_gains_a_started_or_success_entry(monkeypatch):
    """Req 2. A veto denial ends at `denied` with no socket. (The denial receipt
    itself is R2b1's; what is new is that the effect's OUTCOME account must show
    transport_ran=False and no success — the no-false-success half of the law.)"""

    def _explode(request, timeout=None, **kw):
        raise AssertionError("a denied effect must not reach a socket")

    monkeypatch.setattr(urllib.request, "urlopen", _explode)
    with remote_fetch_policy_scope(dict(VETO)):
        from core.remote_fetch_policy import open_remote

        with pytest.raises(RemoteFetchRefusedError):
            open_remote(urllib.request.Request("https://example.invalid/d"), timeout=5)
        entries = effect_receipts()
        outcomes = _outcomes()

    assert [e["lifecycle"] for e in entries] == ["denied"], (
        f"a denied effect writes exactly one entry, saw {[e['lifecycle'] for e in entries]}"
    )
    outcome = outcomes[0]
    assert outcome["lifecycle"] == "denied"
    assert outcome["transport_ran"] is False
    assert outcome["attempts"] == 0


# ------------------------------------------------------------------ 6. retry after failure


def test_retry_after_failure_gets_a_new_attempt_on_the_same_effect(monkeypatch):
    """Req 4. RED at base: `retry_of` does not exist and each call mints a new
    effect id, so a retry is unattributable to the effect it retried."""
    calls: list[int] = []

    def _flaky(request, timeout=None, **kw):
        calls.append(len(calls))
        if len(calls) == 1:
            raise ConnectionRefusedError("first attempt refused")
        return _Resp(200)

    monkeypatch.setattr(urllib.request, "urlopen", _flaky)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        with pytest.raises(ConnectionRefusedError):
            open_remote(urllib.request.Request("https://example.invalid/r"), timeout=5)
        first = _one_effect_id(3)
        open_remote(
            urllib.request.Request("https://example.invalid/r"),
            timeout=5,
            retry_of=first,
        )
        _require_chain(
            first,
            ["authorized", "started", "failed", "started", "succeeded"],
        )
        outcomes = _outcomes()
        receipt_ids = {e["effect_id"] for e in effect_receipts()}

    assert receipt_ids == {first}, (
        "the retry must not mint a second logical effect"
    )
    mine = next(o for o in outcomes if o["effect_id"] == first)
    assert mine["attempts"] == 2, "a retry is a new attempt identity on the same effect"
    assert mine["lifecycle"] == "succeeded"


# ------------------------------------------------------------ 7. duplicate terminal writes


def test_duplicate_terminal_write_is_idempotent(monkeypatch):
    """Req 4. Terminal transitions are append-only and idempotent: the second
    terminal for an attempt records nothing and returns the first outcome."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(200)
    )
    with remote_fetch_policy_scope(dict(TURN)):
        from core.effect_gateway import (
            DECISION_ALLOWED,
            EffectReceipt,
            current_effect_ledger,
        )

        ledger = current_effect_ledger()
        effect = ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_ALLOWED,
                lifecycle="authorized",
                reason="test",
                host="example.invalid",
                recorded_at="t0",
            )
        )
        attempt = effect.begin_attempt()
        assert attempt == 1
        first = effect.succeed(status=200)
        second = effect.fail(exc=ConnectionRefusedError("late failure"))
        third = effect.cancel(reason="double cancel")

        assert (first, second, third) == ("succeeded",) * 3, (
            "a duplicate terminal must report the FIRST terminal, never overwrite it"
        )
        entries = [e for e in effect_receipts() if e["effect_id"] == effect.effect_id]
        assert [e["lifecycle"] for e in entries] == ["authorized", "started", "succeeded"], (
            "exactly one terminal entry may exist per attempt"
        )
        assert all(e["attempt"] in (0, 1) for e in entries)


# --------------------------------------------------------------- 8. concurrent effects


def test_concurrent_effects_never_cross_ids_or_terminals(monkeypatch):
    """Req 1/5. Two effects run interleaved behind a barrier; each keeps its own
    id, its own chain, and exactly one terminal."""
    barrier = threading.Barrier(2, timeout=10)

    def _slow(request, timeout=None, **kw):
        barrier.wait()  # both effects are in flight at once before either returns
        return _Resp(200)

    monkeypatch.setattr(urllib.request, "urlopen", _slow)
    errors: list[Exception] = []

    def _fetch(url: str) -> None:
        try:
            from core.remote_fetch_policy import open_remote

            open_remote(urllib.request.Request(url), timeout=5)
        except Exception as exc:  # pragma: no cover - surfaced via errors list
            errors.append(exc)

    with remote_fetch_policy_scope(dict(TURN)):
        ctx_a, ctx_b = copy_context(), copy_context()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(ctx_a.run, _fetch, "https://example.invalid/a")
            pool.submit(ctx_b.run, _fetch, "https://example.invalid/b")
        entries = effect_receipts()
        outcomes = _outcomes()
        ids = {e["effect_id"] for e in entries}
        assert len(ids) == 2, f"two concurrent effects own two ids, saw {ids}"
        for effect_id in ids:
            _require_chain(effect_id, ["authorized", "started", "succeeded"])
        assert {e["host"] for e in entries} == {"example.invalid"}, (
            "hosts recorded honestly for both effects"
        )

    assert not errors, errors
    assert all(o["lifecycle"] == "succeeded" for o in outcomes)
    assert all(o["attempts"] == 1 for o in outcomes)


# ------------------------------------------------------------------- 9. nested scopes


def test_lifecycle_chain_survives_the_production_nested_scope_shape(monkeypatch):
    """Req 1/7. The served lane opens nested fetch scopes; the inner one defers
    to the turn's ledger and the full chain must publish at the OUTER close."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(200)
    )
    source_context = dict(TURN)
    with remote_fetch_turn_scope(source_context):
        with remote_fetch_turn_scope(source_context):
            from core.remote_fetch_policy import open_remote

            open_remote(urllib.request.Request("https://example.invalid/n"), timeout=5)
            inner_view = list(effect_receipts())
        outer_view = list(effect_receipts())

    assert [e["lifecycle"] for e in inner_view] == ["authorized", "started", "succeeded"]
    assert [e["lifecycle"] for e in outer_view] == ["authorized", "started", "succeeded"], (
        "the inner scope's exit must not close or drain the owning turn's account"
    )
    published = source_context["effect_receipts"]
    assert [e["lifecycle"] for e in published] == ["authorized", "started", "succeeded"], (
        "the turn's published account must expose the terminal lifecycle"
    )


# ------------------------------------------------------------------- 10. missing context


def test_missing_context_denies_and_invents_no_effect(monkeypatch):
    """Req 1/8. No ledger: the door refuses typed before any socket (R2b1's
    law, restated as the boundary of this slice), the lifecycle authority mints
    nothing, and an outcome read outside any scope is empty — never invented."""
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no socket without a ledger")),
    )
    from core.remote_fetch_policy import open_remote

    with pytest.raises(RemoteFetchRefusedError) as refusal:
        open_remote(urllib.request.Request("https://example.invalid/none"), timeout=5)
    assert "no active turn or background effect ledger" in str(refusal.value)
    assert effect_receipts() == ()
    assert _outcomes() == ()


# ------------------------------------------------- identity propagation across threads (req 5)


def test_handle_terminated_from_a_foreign_context_keeps_canonical_identity(monkeypatch):
    """Req 5. A worker thread that inherits the turn's context runs the fetch;
    a thread holding ONLY the handle (a fresh context, no ledger binding) can
    still record the terminal — identity comes from the owning ledger, not from
    the ambient context."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(200)
    )
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    request = TurnRequest.from_ingress(
        user_text="fetch it",
        source_context={"surface": "openclaw"},
        request_id="req-r2b2b",
        turn_id="turn-r2b2b",
        session_id="sess-r2b2b",
    )
    context = {**TURN, TURN_REQUEST_KEY: request}
    terminal_error: list[BaseException] = []

    def _terminate_from_bare_thread(handle) -> None:
        try:
            handle.fail(exc=TimeoutError("worker gave up"))
        except BaseException as exc:  # pragma: no cover - surfaced via list
            terminal_error.append(exc)

    with remote_fetch_policy_scope(context):
        from core.effect_gateway import DECISION_ALLOWED, EffectReceipt, current_effect_ledger

        ledger = current_effect_ledger()
        handle = ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_ALLOWED,
                lifecycle="authorized",
                reason="test",
                host="example.invalid",
                recorded_at="t0",
            )
        )
        handle.begin_attempt()
        thread = threading.Thread(target=_terminate_from_bare_thread, args=(handle,))
        thread.start()
        thread.join(timeout=10)
        entries = effect_receipts()
        outcomes = _outcomes()

    assert not terminal_error, terminal_error
    failed = [e for e in entries if e["lifecycle"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["effect_id"] == handle.effect_id
    assert failed[0]["request_id"] == "req-r2b2b"
    assert failed[0]["turn_id"] == "turn-r2b2b"
    outcome = next(o for o in outcomes if o["effect_id"] == handle.effect_id)
    assert outcome["lifecycle"] == "failed"


# ------------------------------------------------------------------ ceiling preserved (req 7)


def test_per_turn_ceiling_bounds_receipts_and_the_outcome_registry(monkeypatch):
    """Req 7. Lifecycle entries multiply the account (up to 3 per effect) — the
    ceiling, the truncation flag and the dropped count must still hold, and the
    per-effect registry stays bounded with its own truncation flag."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(200)
    )
    from core.effect_gateway import effect_receipts_dropped, effect_receipts_truncated

    total = MAX_RECEIPTS_PER_TURN // 2 + 40  # ~168 effects × 3 entries > 256
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        for index in range(total):
            open_remote(
                urllib.request.Request(f"https://example.invalid/{index}"), timeout=5
            )
        entries = effect_receipts()
        truncated = effect_receipts_truncated()
        dropped = effect_receipts_dropped()
        outcomes = _outcomes()

    assert len(entries) == MAX_RECEIPTS_PER_TURN, (
        f"the receipt account stays capped at {MAX_RECEIPTS_PER_TURN}, saw {len(entries)}"
    )
    assert truncated is True
    assert dropped == total * 3 - MAX_RECEIPTS_PER_TURN
    assert len(outcomes) <= MAX_RECEIPTS_PER_TURN, "the registry is bounded too"
    kept = [o for o in outcomes if o["lifecycle"] == "succeeded"]
    assert kept, "the effects that did complete still answer how they ended"


# ------------------------------------------------------------------ no secrets (req 6)


def test_receipts_carry_no_secrets_bodies_or_unsafe_headers(monkeypatch):
    """Req 6. A URL with query secrets, a bearer header and a body must never
    appear in any lifecycle entry — reasons are type-derived tokens only."""
    secret_url = "https://api.example.invalid/v1/data?token=SECRETTOKEN&sig=ABCD"

    def _leaky(request, timeout=None, **kw):
        raise urllib.error.URLError(
            ValueError(f"GET {secret_url} failed with Authorization: Bearer SECRETTOKEN")
        )

    monkeypatch.setattr(urllib.request, "urlopen", _leaky)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote_url

        with pytest.raises(urllib.error.URLError):
            open_remote_url(
                secret_url,
                headers={"Authorization": "Bearer SECRETTOKEN"},
                timeout=5,
            )
        entries = effect_receipts()
        outcomes = _outcomes()

    assert entries, "the failed attempt must be on the record"
    blob = repr(entries) + repr(outcomes) + repr(
        [e.get("reason", "") for e in entries]
    )
    for forbidden in ("SECRETTOKEN", "ABCD", "Bearer", "token=", "TOPSECRETBODY"):
        assert forbidden not in blob, f"secret material {forbidden!r} leaked into the account"


# -------------------------------------------------- TurnResult publication (req 10)


def test_turn_result_publishes_the_terminal_lifecycle_honestly(monkeypatch):
    """Req 10. TurnResult gains the per-effect outcome account and finalization
    wires it from the turn's ledger, exactly as it wires the receipts."""
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(503)
    )
    from core.turn_contract import TurnResult

    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        open_remote(urllib.request.Request("https://example.invalid/pub"), timeout=5)
        from core.effect_gateway import effect_outcomes

        outcomes = effect_outcomes()

    assert outcomes and outcomes[0]["lifecycle"] == "succeeded"
    result = TurnResult(turn_id="t", request_id="r", effect_outcomes=outcomes)
    projected = result.to_dict()
    assert projected["effect_outcomes"] == [dict(o) for o in outcomes], (
        "the egress projection must carry the outcome account losslessly"
    )
    rebuilt = TurnResult(**projected)
    assert list(rebuilt.effect_outcomes) == list(outcomes)

    # The finalizer reads the SAME account it already reads receipts from.
    finalization_source = pathlib.Path("core/finalization.py").read_text()
    tree = ast.parse(finalization_source)
    wired = any(
        isinstance(node.func, ast.Name)
        and node.func.id == "TurnResult"
        and any(kw.arg == "effect_outcomes" for kw in node.keywords)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )
    assert wired, "finalization must construct TurnResult with effect_outcomes"
    assert "effect_outcomes" in finalization_source


# ------------------------------------------- the served web lane's page transport (req 8)


def test_served_web_lane_page_fetch_runs_the_full_lifecycle(monkeypatch):
    """Req 8. `http_fetch_text` is the transport the served web lane reads
    pages through, and it opens a RAW urlopen — its fetches appear in no
    account at all. Through the door it inherits the whole lifecycle."""
    from tools.web import http_fetch

    monkeypatch.setattr(http_fetch.urllib.request, "urlopen", lambda r, timeout=None: _PageResp())
    with remote_fetch_policy_scope(dict(TURN)):
        result = http_fetch.http_fetch_text("https://example.invalid/article")
        entries = effect_receipts()

    assert result["status"] == "ok", result
    assert [e["lifecycle"] for e in entries] == ["authorized", "started", "succeeded"], (
        "the lane's page fetches must live in the turn's effect account"
    )
    assert {e["host"] for e in entries} == {"example.invalid"}


def test_served_web_lane_page_fetch_denial_maps_to_status_not_silence(monkeypatch):
    """Req 2/8. Under the veto the lane's transport denies before the socket,
    records the denial, and answers its callers with the typed status they
    already understand."""
    from tools.web import http_fetch

    def _explode(*a, **kw):
        raise AssertionError("a vetoed page fetch must not open a socket")

    monkeypatch.setattr(http_fetch.urllib.request, "urlopen", _explode)
    with remote_fetch_policy_scope(dict(VETO)):
        result = http_fetch.http_fetch_text("https://example.invalid/article")
        entries = effect_receipts()

    assert result["status"] == "remote_fetch_disabled", result
    assert [e["lifecycle"] for e in entries] == ["denied"]


def test_page_transport_module_opens_no_raw_socket_of_its_own():
    """Req 8, structural. The bypass the R2b2 audit named (`http_fetch.py`,
    the biggest raw-urlopen bypass) stays dead: no urlopen call site may exist
    in the module outside the one door."""
    source = pathlib.Path("tools/web/http_fetch.py").read_text()
    tree = ast.parse(source)
    raw = [
        f"{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "urlopen"
    ]
    assert not raw, (
        f"http_fetch.py calls urlopen directly at lines {raw} — every socket "
        "belongs to the one door, or the attempt lives in no account"
    )


# --------------------------------------------------------------------------- sabotage
#
# Each sabotage injects the named defect AT RUNTIME and asserts the invariant
# helper above CATCHES it (raises). If a defect can slip past the helper, the
# named test it belongs to has no teeth — and this file fails.


def _authorized_handle():
    from core.effect_gateway import (
        DECISION_ALLOWED,
        EffectReceipt,
        current_effect_ledger,
    )

    ledger = current_effect_ledger()
    return ledger.open_effect(
        EffectReceipt(
            effect_class=EFFECT_NETWORK_FETCH,
            decision=DECISION_ALLOWED,
            lifecycle="authorized",
            reason="sabotage",
            host="example.invalid",
            recorded_at="t0",
        )
    )


def test_sabotage_false_success_is_caught(monkeypatch):
    """Inject: every terminal records as succeeded. The raising-transport
    invariant (failed, not succeeded) must fire."""
    from core import effect_gateway as eg

    monkeypatch.setattr(urllib.request, "urlopen", _raise_refused)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        original = eg.EffectLifecycle.record_terminal

        def lie(self, lifecycle, **kw):
            return original(self, eg.LIFECYCLE_SUCCEEDED, **kw)

        monkeypatch.setattr(eg.EffectLifecycle, "record_terminal", lie)
        with pytest.raises(ConnectionRefusedError):
            open_remote(urllib.request.Request("https://example.invalid/s"), timeout=5)
        effect_id = {e["effect_id"] for e in effect_receipts()}.pop()
        with pytest.raises(AssertionError):
            _require_chain(effect_id, ["authorized", "started", "failed"])


def test_sabotage_missing_terminal_is_caught(monkeypatch):
    """Inject: the failure path records no terminal. The exactly-one-terminal
    invariant must fire."""
    from core import effect_gateway as eg

    monkeypatch.setattr(urllib.request, "urlopen", _raise_refused)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        monkeypatch.setattr(eg.EffectLifecycle, "fail", lambda self, **kw: "")
        with pytest.raises(ConnectionRefusedError):
            open_remote(urllib.request.Request("https://example.invalid/s"), timeout=5)
        effect_id = {e["effect_id"] for e in effect_receipts()}.pop()
        with pytest.raises(AssertionError):
            _require_chain(effect_id, ["authorized", "started", "failed"])


def test_sabotage_double_terminal_is_caught(monkeypatch):
    """Inject: the idempotence guard removed — a second terminal appends. The
    one-terminal-per-attempt invariant must fire."""
    from core import effect_gateway as eg

    with remote_fetch_policy_scope(dict(TURN)):
        handle = _authorized_handle()
        handle.begin_attempt()

        def unguarded(self, effect_id, attempt, lifecycle, entry):
            record = self._effects.get(effect_id)
            if record is not None:
                record["terminal_attempt"] = attempt
                record["terminal"] = lifecycle
            self._append_entry(entry, keep_effect_id=effect_id)
            return lifecycle

        monkeypatch.setattr(eg.EffectLedger, "_terminal_transition", unguarded)
        handle.succeed(status=200)
        handle.cancel(reason="now twice")
        entries = [e for e in effect_receipts() if e["effect_id"] == handle.effect_id]
        with pytest.raises(AssertionError):
            assert [e["lifecycle"] for e in entries] == [
                "authorized",
                "started",
                "succeeded",
            ], "a second terminal entry must never exist"


def test_sabotage_changed_effect_id_is_caught(monkeypatch):
    """Inject: continuations mint a fresh id per append (the base defect). The
    one-id-per-effect invariant must fire."""
    from core import effect_gateway as eg

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None, **kw: _Resp(200)
    )
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        original = eg.EffectLedger._append_entry
        monkeypatch.setattr(
            eg.EffectLedger,
            "_append_entry",
            lambda self, entry, keep_effect_id="": original(self, entry, keep_effect_id=""),
        )
        open_remote(urllib.request.Request("https://example.invalid/s"), timeout=5)
        with pytest.raises(AssertionError):
            _one_effect_id(3)


def test_sabotage_secret_bearing_receipt_is_caught(monkeypatch):
    """Inject: the failure reason builder returns the raw exception text (which
    embeds the secret-bearing URL). The no-secrets invariant must fire."""
    from core import effect_gateway as eg

    secret_url = "https://api.example.invalid/v1?token=SECRETTOKEN"

    def _leak(request, timeout=None, **kw):
        raise urllib.error.URLError(f"GET {secret_url} exploded")

    monkeypatch.setattr(urllib.request, "urlopen", _leak)
    with remote_fetch_policy_scope(dict(TURN)):
        from core.remote_fetch_policy import open_remote

        monkeypatch.setattr(
            eg, "safe_transport_failure_reason", lambda exc: str(exc)
        )
        with pytest.raises(urllib.error.URLError):
            open_remote(urllib.request.Request(secret_url), timeout=5)
        blob = repr(effect_receipts())
        with pytest.raises(AssertionError):
            assert "SECRETTOKEN" not in blob, "the leaked token must be caught"


def _raise_refused(request, timeout=None, **kw):
    raise ConnectionRefusedError("refused")
