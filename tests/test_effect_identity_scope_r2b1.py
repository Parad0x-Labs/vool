"""R2b1 — EFFECT IDENTITY AND SCOPE CANNOT LIE.

R2 gave every turn a per-turn EffectLedger and a frozen TurnPolicy. R2b1 closes
the gaps that let a receipt or an authorization still lie about who owns it:

* the ledger trusted caller-supplied identity fields (`entry["turn_id"] or
  turn_id`), so a caller could forge the turn a receipt belonged to;
* the three effect doors (network, command, machine/filesystem) authorized
  effects with NO active turn ledger — `consume_turn_policy` handed back an
  empty "undeclared" policy and `decide_network_fetch` built one ad hoc, so an
  effect that could not be attributed to any turn was still authorized;
* the frozen decision context was a SHALLOW copy, so later caller mutation of a
  nested value rewrote the turn's policy truth mid-flight;
* a truncated receipt account said `truncated=true` but never said HOW MANY
  receipts were dropped, so even the honesty flag was uncheckable.

Every test names the requirement it pins. The unscoped-denial tests are the
RED-first set; the valid-turn and named-exemption tests are the negative
controls proving the door still opens for effects a turn legitimately owns.
"""
from __future__ import annotations

import json

import pytest

from core.effect_gateway import (
    DECISION_ALLOWED,
    DECISION_DENIED,
    EFFECT_NETWORK_FETCH,
    EFFECT_RECEIPTS_TRUNCATED_CONTEXT_KEY,
    MAX_RECEIPTS_PER_TURN,
    EffectReceipt,
    PolicyUnavailableError,
    TurnPolicy,
    close_effect_receipt_scope,
    consume_turn_policy,
    current_effect_ledger,
    current_turn_policy,
    decide_network_fetch,
    effect_receipts,
    effect_receipts_truncated,
    open_effect_receipt_scope,
    record_effect_receipt,
)
from core.execution_gate import ExecutionGate
from core.remote_fetch_policy import remote_fetch_policy_scope
from core.turn_contract import TurnResult

TURN_A = {"surface": "openclaw", "platform": "openclaw", "session_id": "r2b1-a"}
TURN_B = {"surface": "openclaw", "platform": "openclaw", "session_id": "r2b1-b"}


def _receipt(reason: str) -> EffectReceipt:
    return EffectReceipt(
        effect_class=EFFECT_NETWORK_FETCH,
        decision=DECISION_DENIED,
        reason=reason,
        recorded_at="t",
    )


# ------------------------------------------------------------------ identity (req 1 + 2)


def test_a_forged_identity_is_replaced_by_the_ledgers_canonical_identity():
    """Req 1+2. RED at base: `EffectLedger.record` honored caller-supplied
    turn_id/request_id/policy_id/effect_id (`entry[...] or canonical`), so any
    caller could stamp its effect onto a turn it does not belong to. The ledger
    owns all four; a stated conflict is replaced and the replacement recorded."""
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    request = TurnRequest.from_ingress(
        user_text="fetch the page",
        source_context={"surface": "openclaw"},
        request_id="req-canonical",
        turn_id="turn-canonical",
        session_id="sess-canonical",
    )
    context = {**TURN_A, TURN_REQUEST_KEY: request}
    with remote_fetch_policy_scope(context):
        ledger = current_effect_ledger()
        record_effect_receipt(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_DENIED,
                reason="forged",
                recorded_at="t",
                request_id="req-FORGED",
                turn_id="turn-FORGED",
                effect_id="eff-FORGED",
                policy_id="pol-FORGED",
            )
        )
        entry = effect_receipts()[0]

    assert entry["turn_id"] == "turn-canonical", entry
    assert entry["request_id"] == "req-canonical", entry
    assert entry["policy_id"] == ledger.policy.policy_id, entry
    assert entry["effect_id"].startswith("eff:"), entry
    assert "FORGED" not in entry["effect_id"], entry
    note = entry.get("identity_note", "")
    # The replacement is RECORDED, never silent: every forged value is named.
    for forged in ("turn-FORGED", "req-FORGED", "eff-FORGED", "pol-FORGED"):
        assert forged in note, (forged, note)


def test_an_honest_receipt_carries_no_identity_note():
    """Negative control for req 2: a receipt that states no identity is stamped
    canonically and no conflict is recorded — the note marks lies, not receipts."""
    with remote_fetch_policy_scope(dict(TURN_A)):
        record_effect_receipt(_receipt("honest"))
        entry = effect_receipts()[0]
    assert entry.get("identity_note", "") == "", entry
    assert entry["effect_id"].startswith("eff:"), entry


# ------------------------------------------------------------------ fail closed (req 3 + 4)


def test_unscoped_command_authorization_fails_closed():
    """Req 3. RED at base: with no turn ledger open, `consume_turn_policy`
    returned an empty "undeclared" policy and the command door authorized
    (`simulate_only`) an effect attributable to no turn at all."""
    result = ExecutionGate.evaluate_command("ls -la")
    assert result["decision"] == "blocked", result
    assert "no active turn ledger" in result["reason"], result["reason"]


def test_unscoped_machine_write_authorization_fails_closed():
    """Req 3. RED at base: the machine/filesystem door authorized a write with
    no active turn ledger (`execution.allow_machine_writes` default)."""
    result = ExecutionGate.evaluate_machine_effect(
        effect_type="machine.write_file",
        resolved_path="/tmp/r2b1-unscoped.txt",
    )
    assert result["decision"] == "refused", result
    assert "no active turn ledger" in result["reason"], result["reason"]


def test_unscoped_network_authorization_fails_closed():
    """Req 3. RED at base: `decide_network_fetch` built a policy ad hoc when no
    ledger was open and allowed the fetch ("no mode policy active")."""
    decision, reason = decide_network_fetch({})
    assert decision == DECISION_DENIED, (decision, reason)
    assert "no active turn ledger" in reason, reason


def test_consuming_the_turn_policy_without_a_ledger_raises_typed():
    """Req 3. The typed reason is an exception type, not prose: a gate that
    cannot attribute its effect to a turn gets PolicyUnavailableError."""
    with pytest.raises(PolicyUnavailableError) as excinfo:
        consume_turn_policy("test_gate")
    assert "no active turn ledger" in str(excinfo.value)


def test_the_bootstrap_exemption_is_explicit_named_and_narrow(monkeypatch):
    """Req 4. The only sanctioned no-turn authorization path is an EXPLICIT,
    NAMED scope. Inside one, doors behave as they always did outside turns
    (bootstrap/install semantics); every receipt names the scope and no turn.
    Outside it, the doors stay closed."""
    import core.policy_engine as policy_engine
    from core.effect_gateway import named_background_effect_scope

    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda path, default=None: {
            "execution.allow_sandbox_execution": False,
            "execution.allow_simulation": True,
            "execution.allow_machine_writes": True,
        }.get(path, default),
    )

    with named_background_effect_scope("install.health_probe") as ledger:
        assert ledger.scope_name == "install.health_probe"
        command = ExecutionGate.evaluate_command("ls -la")
        machine = ExecutionGate.evaluate_machine_effect(
            effect_type="machine.write_file", resolved_path="/tmp/r2b1-install.txt"
        )
        decision, reason = decide_network_fetch({})
        entries = list(effect_receipts())

    assert command["decision"] == "simulate_only", command
    assert machine["decision"] == "authorized", machine
    assert decision == DECISION_ALLOWED, (decision, reason)
    assert entries and {r["scope_name"] for r in entries} == {"install.health_probe"}
    assert all(r["turn_id"] == "" and r["request_id"] == "" for r in entries)
    # Narrow: the exemption ends with its scope.
    assert decide_network_fetch({})[0] == DECISION_DENIED
    assert ExecutionGate.evaluate_command("ls -la")["decision"] == "blocked"
    assert ExecutionGate.evaluate_machine_effect(
        effect_type="machine.write_file", resolved_path="/tmp/r2b1-install.txt"
    )["decision"] == "refused"
    assert effect_receipts() == ()


def test_the_door_denies_an_arbitrary_unscoped_fetch(monkeypatch):
    """SECURITY INVARIANT (amendment) — the door must not authorize itself.

    Inverted from the R2b1 test that expected `open_remote` to grant itself a
    generic background exemption: a door that manufactures the authority needed
    to pass is not exempted, it is fail-open. An arbitrary unscoped caller that
    merely uses the canonical door is DENIED, typed, before any socket exists.
    """
    import urllib.request

    opened: list[str] = []

    def _fake_urlopen(request, timeout=0, context=None):
        opened.append(str(getattr(request, "full_url", "")))
        return object()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote, open_remote_url

    with pytest.raises(RemoteFetchRefusedError) as url_form:
        open_remote_url("https://unscoped.invalid/feed", timeout=1.0)
    assert "no active turn" in str(url_form.value).lower(), str(url_form.value)

    req = urllib.request.Request("https://unscoped.invalid/feed")
    with pytest.raises(RemoteFetchRefusedError) as request_form:
        open_remote(req, timeout=1.0)
    assert "no active turn" in str(request_form.value).lower(), str(request_form.value)

    # The socket was never reached, in either call shape.
    assert opened == [], opened
    # And no account was invented for the refused effects.
    assert effect_receipts() == ()


def test_valid_turn_effects_still_authorize_inside_their_turn(monkeypatch):
    """Negative control for req 3: scoped, attributable effects keep working at
    all three doors — fail closed must never become fail everything."""
    import core.policy_engine as policy_engine

    monkeypatch.setattr(
        policy_engine,
        "get",
        lambda path, default=None: {
            "execution.allow_sandbox_execution": False,
            "execution.allow_simulation": True,
            "execution.allow_machine_writes": True,
        }.get(path, default),
    )
    with remote_fetch_policy_scope(dict(TURN_A)):
        command = ExecutionGate.evaluate_command("ls -la")
        machine = ExecutionGate.evaluate_machine_effect(
            effect_type="machine.write_file", resolved_path="/tmp/r2b1-scoped.txt"
        )
        decision, reason = decide_network_fetch(dict(TURN_A))
    assert command["decision"] == "simulate_only", command
    assert machine["decision"] == "authorized", machine
    assert decision == DECISION_ALLOWED, (decision, reason)


# ------------------------------------------------------------------ dropped count (req 6)


def test_truncation_reports_truncated_and_exact_dropped_count():
    """Req 6. RED at base: the ledger counted dropped receipts internally but
    neither exposed a count reader nor published one, so `truncated=true` could
    not be checked against how much account was actually lost."""
    from core.effect_gateway import EFFECT_RECEIPTS_DROPPED_CONTEXT_KEY, effect_receipts_dropped

    context = dict(TURN_A)
    with remote_fetch_policy_scope(context):
        for index in range(MAX_RECEIPTS_PER_TURN + 37):
            record_effect_receipt(_receipt(f"storm-{index}"))
        assert effect_receipts_truncated() is True
        assert effect_receipts_dropped() == 37

    assert context[EFFECT_RECEIPTS_TRUNCATED_CONTEXT_KEY] is True
    assert context[EFFECT_RECEIPTS_DROPPED_CONTEXT_KEY] == 37


def test_the_turn_result_carries_the_dropped_count():
    """Req 6. RED at base: TurnResult carried `effect_receipts_truncated` but no
    dropped count, so the terminal record asserted incompleteness it could not
    quantify."""
    result = TurnResult(
        turn_id="turn-r2b1",
        request_id="req-r2b1",
        effect_receipts_dropped=37,
    )
    payload = result.to_dict()
    assert payload["effect_receipts_dropped"] == 37, payload
    assert TurnResult().to_dict()["effect_receipts_dropped"] == 0
    # JSON-safe, like the rest of the egress projection.
    assert json.loads(json.dumps(payload))["effect_receipts_dropped"] == 37


@pytest.fixture()
def bound_ledger(tmp_path):
    """A fresh obligation ledger with one satisfied prose obligation, so
    `finalize_answer` can reach its terminal truth (same shape as the R2 file)."""
    import storage.db as sdb
    from core.conductor import obligation_ledger as ol
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "r2b1-effect-truth.db")
    run_migrations()
    opened = ol.open_obligation_set(
        request_text="fetch the page",
        obligations=[
            {"obligation_id": "ob:r2b1:answer", "text": "fetch the page", "kind": "prose"}
        ],
    )
    ol.record_disposition(
        opened["set_id"], opened["version"], "ob:r2b1:answer", "satisfied",
        evidence_source="served_bytes",
    )
    ol.bind_active_set(opened["set_id"], opened["version"])
    yield {**ol.closure_verdict(opened["set_id"], opened["version"]), "set_id": opened["set_id"]}
    ol.clear_active_set()
    sdb.configure_default_db_path(None)


def test_a_finalized_turn_reports_its_dropped_count(bound_ledger):
    """Req 6, end to end: the commit envelope's terminal record quantifies the
    dropped receipts, not merely flagging truncation."""
    from core.finalization import finalize_answer

    with remote_fetch_policy_scope(dict(TURN_A)):
        for index in range(MAX_RECEIPTS_PER_TURN + 5):
            record_effect_receipt(_receipt(f"storm-{index}"))
        commit = finalize_answer(
            turn_id="turn-r2b1-dropped",
            canonical_content="I did a great many things.",
            closure=bound_ledger,
            status="ANSWER_PRESENT",
        )

    assert commit["turn_result"]["effect_receipts_truncated"] is True
    assert commit["turn_result"]["effect_receipts_dropped"] == 5
    assert len(commit["turn_result"]["effect_receipts"]) == MAX_RECEIPTS_PER_TURN


# ------------------------------------------------------------------ frozen decision context (req 7)


def test_decision_context_is_frozen_against_later_caller_mutation():
    """Req 7. RED at base: `TurnPolicy.from_source_context` shallow-copied the
    context, so a caller mutating a NESTED value after the policy was frozen
    rewrote the turn's policy truth. The freeze is deep, and direct
    construction freezes too."""
    context = {"surface": "openclaw", "session_id": "r2b1-freeze", "nested": {"hint": "plan"}}
    with remote_fetch_policy_scope(context):
        policy = current_turn_policy()
        context["nested"]["hint"] = "TAMPERED"
        context["surface"] = "TAMPERED"
        assert policy.decision_context["nested"]["hint"] == "plan", policy.decision_context
        assert policy.decision_context["surface"] == "openclaw", policy.decision_context

    mutable = {"nested": {"k": "v"}}
    direct = TurnPolicy(principal="owner_local", decision_context=mutable)
    mutable["nested"]["k"] = "TAMPERED"
    assert direct.decision_context["nested"]["k"] == "v", direct.decision_context


def test_a_gate_cannot_rewrite_the_frozen_context_for_the_next_gate(monkeypatch):
    """Req 7, at the gate boundary. RED at base: `decide_network_fetch` handed
    the consult a shallow copy, so one consult could poison a nested value and
    rewrite what the NEXT consult of the same turn saw."""
    from core import mode_permission_policy as mpp

    seen: list[str] = []
    real = mpp.decide_tool_call

    def _spy(**kwargs):
        ctx = kwargs["source_context"]
        nested = ctx.get("nested")
        seen.append(str(nested.get("marker")) if isinstance(nested, dict) else "absent")
        if isinstance(nested, dict):
            nested["marker"] = "poisoned"
        return real(**kwargs)

    monkeypatch.setattr(mpp, "decide_tool_call", _spy)
    session = "r2b1-freeze-gate"
    mpp.reset_mode_permission_state()
    try:
        mpp.set_active_mode(session_id=session, mode="auto")
        context = {
            "surface": "openclaw",
            "session_id": session,
            "nested": {"marker": "clean"},
        }
        with remote_fetch_policy_scope(context):
            decide_network_fetch(context)
            decide_network_fetch(context)
    finally:
        mpp.reset_mode_permission_state()

    assert seen == ["clean", "clean"], seen


# ------------------------------------------------------------------ scope isolation (req 5) + no-widening (req 8)


def test_nested_scopes_keep_separate_policy_truth_and_restore_the_parent():
    """Req 5, negative control: a nested open gets its own frozen policy and
    its own identity; closing it restores the parent's binding untouched."""
    with remote_fetch_policy_scope(dict(TURN_A)):
        outer_policy = current_turn_policy()
        record_effect_receipt(_receipt("outer-1"))
        open_effect_receipt_scope(dict(TURN_B))
        inner_policy = current_turn_policy()
        record_effect_receipt(_receipt("inner-1"))
        inner_entries = list(effect_receipts())

        assert inner_policy.policy_id != outer_policy.policy_id
        assert {r["policy_id"] for r in inner_entries} == {inner_policy.policy_id}

        close_effect_receipt_scope()
        assert current_turn_policy() is outer_policy
        record_effect_receipt(_receipt("outer-2"))
        entries = list(effect_receipts())

    assert [r["reason"] for r in entries] == ["outer-1", "outer-2"]
    assert {r["policy_id"] for r in entries} == {outer_policy.policy_id}


def test_the_r2_no_widening_ceiling_survives_r2b1():
    """Req 8, preservation control: a verdict frozen DENIED stays denied for the
    rest of the turn, even when the live mode widens AND the live context the
    turn started from is rewritten underneath it."""
    from core import mode_permission_policy as mpp

    session = "r2b1-ceiling"
    mpp.reset_mode_permission_state()
    try:
        mpp.set_active_mode(session_id=session, mode="plan")
        context = {"surface": "openclaw", "session_id": session}
        with remote_fetch_policy_scope(context):
            first, first_reason = decide_network_fetch(context)
            assert first == DECISION_DENIED, (first, first_reason)
            mpp.set_active_mode(session_id=session, mode="auto")
            context["surface"] = "TAMPERED"
            second, second_reason = decide_network_fetch(context)
            assert second == DECISION_DENIED, (second, second_reason)
    finally:
        mpp.reset_mode_permission_state()


# ==================================================================================================
# AMENDMENT — THE NETWORK DOOR MUST NOT AUTHORIZE ITSELF
#
# R2b1 first placed a generic background scope INSIDE `open_remote`, which let
# the door manufacture the authority required to pass itself. The amendment:
# the door never opens a scope; each legitimate background lane's OWNING ENTRY
# POINT opens its own specifically-named scope, and an arbitrary unscoped
# caller is denied typed before any socket.
# ==================================================================================================


class _FakeResponse:
    """Minimal urlopen stand-in: context manager with a body and a status."""

    def __init__(self, payload: bytes = b"{}", status: int = 200):
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def _install_fake_urlopen(monkeypatch, captured: dict, payload: bytes = b"{}", status: int = 200) -> None:
    """Capture, AT SOCKET TIME, which ledger (if any) owned the fetch. The opener path is faked
    too: keyed credential traffic (verification, the connection probe) rides an OpenerDirector
    that never calls module-level urlopen, and a urlopen-only fake let it straight through to
    the real network (measured 2026-09-14)."""
    import urllib.request

    def _fake_urlopen(request, timeout=0, context=None):
        ledger = current_effect_ledger()
        captured["scope_name"] = getattr(ledger, "scope_name", None)
        captured["url"] = str(getattr(request, "full_url", ""))
        receipts = [dict(r) for r in effect_receipts()]
        captured["last_receipt"] = receipts[-1] if receipts else None
        return _FakeResponse(payload, status)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", staticmethod(_fake_urlopen))


def test_a_background_scope_name_must_be_specific():
    """A nameless exemption is the general fail-open under another name: blank
    or whitespace-only scope names are refused at the opening."""
    from core.effect_gateway import named_background_effect_scope

    with pytest.raises(ValueError):
        with named_background_effect_scope(""):
            pass
    with pytest.raises(ValueError):
        with named_background_effect_scope("   "):
            pass
    # Whitespace is trimmed, never recorded blurry.
    with named_background_effect_scope("  install.probe  ") as ledger:
        assert ledger.scope_name == "install.probe"


def test_a_named_scope_defers_to_an_active_turn():
    """A background entry that fires INSIDE a turn is the turn's effect: the
    named scope must not displace the turn's ledger and steal its account."""
    from core.effect_gateway import named_background_effect_scope

    with remote_fetch_policy_scope(dict(TURN_A)):
        turn_ledger = current_effect_ledger()
        with named_background_effect_scope("watch.poll") as yielded:
            assert yielded is turn_ledger
            record_effect_receipt(_receipt("inside-turn"))
        assert current_effect_ledger() is turn_ledger
        entries = list(effect_receipts())
    # Attributed to the turn, not to the background scope.
    assert [r["reason"] for r in entries] == ["inside-turn"]
    assert entries[-1]["scope_name"] == "", entries[-1]
    assert entries[-1]["policy_id"] == turn_ledger.policy.policy_id, entries[-1]


# ------------------------------------------------------------------ owning entry points


def test_self_update_check_owns_its_fetch(monkeypatch):
    """The self-update check is a legitimate background service (startup
    backbone + CLI). RED at e15b39f6: the fetch ran under the door's generic
    self-granted scope instead of the service's own named one."""
    captured: dict = {}
    _install_fake_urlopen(monkeypatch, captured, payload=b'{"tag_name": "v1.2.3"}')

    from core.self_update_check import fetch_latest_release

    release = fetch_latest_release()

    assert release == {"tag_name": "v1.2.3"}, release  # the fetch was reached
    assert captured["scope_name"] == "self_update.check", captured
    assert captured["last_receipt"]["scope_name"] == "self_update.check", captured
    assert captured["last_receipt"]["turn_id"] == "", captured
    # Retention truth: a background account is scope-local — handed to no one.
    assert effect_receipts() == ()


def test_model_catalog_refresh_owns_its_fetch(monkeypatch, tmp_path):
    """The model-catalog refresh is a legitimate background service. RED at
    e15b39f6: the fetch ran under the door's self-granted scope."""
    captured: dict = {}
    _install_fake_urlopen(monkeypatch, captured, payload=b'{"data": []}')

    from core import openrouter_catalog

    monkeypatch.setattr(openrouter_catalog, "_cache_path", lambda: tmp_path / "catalog.json")
    models = openrouter_catalog.refresh_openrouter_catalog(api_key="k", timeout_seconds=1)

    assert list(models) == [], models  # the fetch was reached and parsed
    assert captured["scope_name"] == "model_catalog.refresh", captured
    assert captured["last_receipt"]["scope_name"] == "model_catalog.refresh", captured


def test_cloud_connection_probe_owns_its_fetch(monkeypatch):
    """The connection/auth probe serves the API daemon's settings surface. RED
    at e15b39f6: the probe ran under the door's self-granted scope."""
    captured: dict = {}
    _install_fake_urlopen(monkeypatch, captured, payload=b"{}", status=200)

    from core.cloud_connection_state import _probe_once

    _probe_once("openrouter", "sk-test")

    assert captured["url"].startswith("https://"), captured  # the probe was reached
    assert captured["scope_name"] == "cloud.connection_probe", captured
    assert captured["last_receipt"]["scope_name"] == "cloud.connection_probe", captured


def test_web0_announce_owns_its_fetch(monkeypatch):
    """The startup capability broadcast is a legitimate background service.
    RED at e15b39f6: the announce ran under the door's self-granted scope."""
    captured: dict = {}
    _install_fake_urlopen(monkeypatch, captured, payload=b"{}", status=200)

    from core.web0_capability_broadcast import announce

    class _Manifest:
        def to_dict(self):
            return {"capabilities": []}

    ok = announce(_Manifest(), mesh_url="https://mesh.invalid")

    assert ok is True, ok  # the announce was reached
    assert captured["scope_name"] == "web0.announce", captured
    assert captured["last_receipt"]["scope_name"] == "web0.announce", captured


def test_kernel_repl_model_call_owns_its_fetch(monkeypatch):
    """The kernel REPL's cloud judgment is an OPERATOR path: it works only
    under its own explicit scope, named for what it is. RED at e15b39f6: the
    call ran under the door's self-granted scope."""
    captured: dict = {}
    _install_fake_urlopen(
        monkeypatch,
        captured,
        payload=b'{"choices": [{"message": {"content": "the judgment"}}]}',
    )

    from core import credential_store
    from core.kernel import repl

    monkeypatch.setattr(
        credential_store, "get_credential", lambda name: "k-test" if name == "llm.cloud.openrouter" else ""
    )
    verdict = repl._openrouter_chat("system prompt", "user text")

    assert verdict == "the judgment", verdict  # the model call was reached
    assert captured["scope_name"] == "kernel.repl.model_call", captured
    assert captured["last_receipt"]["scope_name"] == "kernel.repl.model_call", captured


def _watch_config():
    from core.web.watch.config import BrainHiveWatchServerConfig

    return BrainHiveWatchServerConfig()


def _bare_handler(handler_class, path: str):
    """A BaseHTTPRequestHandler without a socket: just enough to drive do_GET."""
    import email.message

    handler = object.__new__(handler_class)
    handler.path = path
    headers = email.message.Message()
    headers["Host"] = ""
    handler.headers = headers
    handler._written: list = []
    handler._write_json = lambda status, payload, *a, **k: handler._written.append((status, payload))
    handler._write_bytes = lambda *a, **k: handler._written.append(a)
    return handler


def _capturing_fake(captured: dict):
    def _fake(*args, **kwargs):
        ledger = current_effect_ledger()
        captured["scope_name"] = getattr(ledger, "scope_name", None)
        captured["called"] = True
        return {}

    return _fake


def _build_watch_server_with(captured: dict, dashboard_fake, proxy_fake=None):
    from core.web.watch.server import build_watch_server

    noop = lambda *a, **k: None  # noqa: E731
    server = build_watch_server(
        _watch_config(),
        workstation_version="test",
        server_factory=lambda addr, handler_class: type("S", (), {"handler": handler_class})(),
        validate_tls_config=noop,
        requires_public_tls=lambda cfg: False,
        watch_tls_enabled=lambda cfg: False,
        fetch_dashboard_from_upstreams=dashboard_fake,
        fetch_topic_from_upstreams=noop,
        fetch_topic_posts_from_upstreams=noop,
        proxy_voolbook_get=proxy_fake or noop,
        http_get_json=noop,
        normalize_base_url=lambda base: str(base).rstrip("/"),
        ssl_context_for_url=lambda *a, **k: None,
        build_tls_context=lambda cfg: None,
    )
    return server.handler


def test_watch_server_dashboard_poll_owns_its_fetch():
    """The watch daemon's dashboard poll is a legitimate background service;
    its owning entry is the server's GET handler. RED at e15b39f6: the handler
    had no scope of its own — the fetch ran under the door's self-granted one."""
    captured: dict = {}
    handler_class = _build_watch_server_with(captured, _capturing_fake(captured))

    handler = _bare_handler(handler_class, "/api/dashboard")
    handler.do_GET()

    assert captured.get("called") is True, "the dashboard poll never ran"
    assert captured["scope_name"] == "watch.poll", captured


def test_watch_server_proxy_post_owns_its_fetch(monkeypatch):
    """The watch daemon's voolbook action proxy is a legitimate background
    service; its owning entry is the server's POST handler. RED at e15b39f6:
    the POST handler had no scope of its own."""
    import io
    import json as _json
    import urllib.request

    captured: dict = {}
    opened: list[str] = []

    def _fake_urlopen(request, timeout=0, context=None):
        ledger = current_effect_ledger()
        captured["scope_name"] = getattr(ledger, "scope_name", None)
        opened.append(str(getattr(request, "full_url", "")))
        return _FakeResponse(b"{}", 200)

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    handler_class = _build_watch_server_with(captured, _capturing_fake(captured))

    handler = _bare_handler(handler_class, "/v1/voolbook/upvote")
    body = _json.dumps({"post": "p1"}).encode()
    handler.rfile = io.BytesIO(body)
    handler.headers["Content-Length"] = str(len(body))
    handler.do_POST()

    assert opened, "the proxy POST never reached the door"
    assert captured["scope_name"] == "watch.proxy", captured


def test_watch_app_dispatch_owns_its_fetch():
    """The Starlette watch composition's single dispatch entry owns the same
    background work — including through the threadpool the fetches run in."""
    import asyncio

    captured: dict = {}
    from starlette.requests import Request

    from core.web.watch.app import create_watch_app

    noop = lambda *a, **k: None  # noqa: E731
    app = create_watch_app(
        config=_watch_config(),
        workstation_version="test",
        validate_tls_config=noop,
        requires_public_tls=lambda cfg: False,
        watch_tls_enabled=lambda cfg: False,
        fetch_dashboard_from_upstreams=_capturing_fake(captured),
        fetch_topic_from_upstreams=noop,
        fetch_topic_posts_from_upstreams=noop,
        proxy_voolbook_get=noop,
        http_get_json=_capturing_fake(captured),
        normalize_base_url=lambda base: str(base).rstrip("/"),
        ssl_context_for_url=lambda *a, **k: None,
    )
    endpoint = app.routes[0].endpoint

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _drive():
        request_scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/dashboard",
            "raw_path": b"/api/dashboard",
            "query_string": b"",
            "headers": [],
            "server": ("test", 80),
            "scheme": "http",
        }
        return await endpoint(Request(request_scope, receive=_receive))

    asyncio.run(_drive())

    assert captured.get("called") is True, "the dispatch never ran its fetch"
    assert captured["scope_name"] == "watch.poll", captured


# ------------------------------------------------------------------ negative controls


def test_a_turn_scoped_fetch_still_reaches_the_socket(monkeypatch):
    """Negative control: a valid turn's fetch still opens (the mock stands in
    for the socket) — fail closed never became fail everything."""
    captured: dict = {}
    _install_fake_urlopen(monkeypatch, captured)

    from core.remote_fetch_policy import open_remote_url

    with remote_fetch_policy_scope(dict(TURN_A)):
        open_remote_url("https://turn-scoped.invalid/feed", timeout=1.0)

    assert captured["url"] == "https://turn-scoped.invalid/feed", captured
    assert captured["scope_name"] == "", captured  # a turn, not a named scope
    assert captured["last_receipt"]["decision"] == DECISION_ALLOWED, captured
    assert captured["last_receipt"]["policy_id"], captured  # the turn's frozen policy judged it
