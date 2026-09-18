"""A11 minimum product truth — PASS 001.

Focused behavioral fence for the A11 slice at SHA 2dbcfd03+:

- receipts carry additive provenance (requested model, selection mode, lane,
  fallback-from) WITHOUT breaking verification of legacy signed receipts;
- POST /api/cloud/model refuses an unconfirmed catalog-known PAID pin
  (request-scoped ``confirm_paid``), never silently and never permanently;
- GET /api/cloud/model exposes the server-authoritative selection state;
- pending approvals survive a daemon restart honestly (expired prompts say
  expired) instead of vanishing into "no such approval";
- the served chat page consumes typed ``vool_terminal`` frames instead of
  demoting every empty ending to "(no response)" + Completed, hydrates its
  composer pin FROM the server on boot, and gates send-time paid pins behind a
  session-scoped (never global-sticky) acknowledgment.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths

# --------------------------------------------------------------------------- receipts

def _issue(**overrides):
    from core.cloud_route_receipt import issue_cloud_route_receipt

    payload = dict(
        attempt_id="at",
        phase="completed",
        session_id="s",
        task_id="t",
        turn_id="turn",
        subtask_id="",
        model_call_id="mc",
        provider_id="openrouter-byok",
        model_id="vendor/paid-model",
        pricing_state="PAID",
        estimated_max_usd=0.01,
        privacy_class="cloud_egress",
        policy_decision="permitted",
        route_reason="explicit_pin",
        success=True,
    )
    payload.update(overrides)
    return issue_cloud_route_receipt(**payload)


def test_new_receipt_carries_additive_provenance():
    receipt = _issue(
        requested_model="auto-pick",
        selection_mode="auto",
        lane="cloud",
        fallback_from="ollama-local",
    )
    data = receipt.to_dict()
    assert data["requested_model"] == "auto-pick"
    assert data["selection_mode"] == "auto"
    assert data["lane"] == "cloud"
    assert data["fallback_from"] == "ollama-local"
    ok, reason = __import__("core.cloud_route_receipt", fromlist=["verify"]).verify_cloud_route_receipt(data)
    assert ok, reason


def test_new_receipt_defaults_stay_empty_when_unknown():
    data = _issue().to_dict()
    # Unknown must remain unknown — never fabricated values.
    assert data["requested_model"] == ""
    assert data["selection_mode"] == ""
    ok, reason = __import__("core.cloud_route_receipt", fromlist=["verify"]).verify_cloud_route_receipt(data)
    assert ok, reason


def test_legacy_receipt_without_provenance_keys_still_verifies():
    """A pre-A11 stored receipt must hash over exactly its original bytes."""
    import hashlib

    import core.cloud_route_receipt as crr

    legacy_payload = {
        "schema": crr.SCHEMA,
        "receipt_id": "crr-legacy",
        "attempt_id": "at",
        "phase": "completed",
        "session_id": "s",
        "task_id": "t",
        "turn_id": "turn",
        "subtask_id": "",
        "model_call_id": "mc",
        "provider_id": "p",
        "model_id": "m",
        "pricing_state": "FREE",
        "estimated_max_usd": 0.0,
        "actual_usd": None,
        "usage": {},
        "privacy_class": "local_only",
        "data_categories": [],
        "policy_decision": "permitted",
        "route_reason": "r",
        "success": True,
        "retry_index": 0,
        "fallback_chain": [],
        "error_kind": "",
        "safe_error": "",
        "issued_at": 1234567890.0,
        "prev_hash": "",
    }
    content = {key: legacy_payload.get(key) for key in crr._LEGACY_CONTENT_KEYS}
    content_hash = hashlib.sha256(crr._canonical_bytes(content)).hexdigest()
    # Legacy rows on disk are plain dicts WITHOUT the provenance keys (the dataclass
    # always materializes them, so verify()'s legacy path is keyed off key ABSENCE).
    _ok, reason = crr.verify_cloud_route_receipt({
        **legacy_payload,
        "content_hash": content_hash,
        "signer_peer_id": "",   # unsigned fixture: verification stops at signature gate
        "signature": "",
    })
    assert reason == "unsigned", "legacy payload must pass the CONTENT-HASH gate"
    recomputed = hashlib.sha256(crr._canonical_bytes(crr._content(dict(legacy_payload)))).hexdigest()
    assert recomputed == content_hash


# -------------------------------------------------- server selection authority + paid gate

PAGES_PAYLOAD = None


@pytest.fixture()
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _model_get(client_host: str = "127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/api/cloud/model",
        query={},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        client_host=client_host,
    )
    return res.status, json.loads(res.body.decode("utf-8"))


def _model_post(body):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path="/api/cloud/model",
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="N"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    try:
        return res.status, json.loads(res.body.decode("utf-8"))
    except Exception:
        return res.status, {}


def test_get_returns_server_authoritative_selection(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    assert _model_get()[1]["source"] == "server"
    status, _payload = _model_post({"model": "deepseek/deepseek-chat-v3:free", "confirm_paid": False})
    assert status == 200
    got = _model_get()[1]
    assert got["ok"] is True
    assert got["model"] == "deepseek/deepseek-chat-v3:free"


def test_get_is_owner_local_symmetric_with_post(monkeypatch):
    """A read of the pinned selection is as owner-local as the write that sets it."""
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    status, payload = _model_get(client_host="10.1.2.3")
    assert status == 403 and payload.get("error") == "owner_local_required"


CATALOG_PAYLOAD = {
    "data": [
        {"id": "zzz/alpha-big", "name": "Alpha Big", "context_length": 500000,
         "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0"}},
        {"id": "deepseek/deepseek-chat-v3:free", "name": "DeepSeek Free", "context_length": 163840,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
    ]
}


@pytest.fixture()
def _cached_catalog(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    runtime_paths.configure_runtime_home(tmp_path)
    import core.openrouter_catalog as cat

    cache = tmp_path / "catalog_cache.json"
    cache.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": CATALOG_PAYLOAD}))
    monkeypatch.setattr(cat, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        cat, "refresh_openrouter_catalog",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("no network in tests")),
    )
    yield
    runtime_paths.configure_runtime_home(None)


def test_unconfirmed_paid_pin_refused_before_persist(_cached_catalog, monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    status, payload = _model_post({"model": "zzz/alpha-big"})
    assert status == 409 and payload.get("code") == "paid_model_confirm_required"
    assert payload["model"] == "zzz/alpha-big"
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model != "zzz/alpha-big", "a paid pin must never persist unconfirmed"


def test_confirmed_paid_pin_is_allowed_and_reported(_cached_catalog, monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    status, payload = _model_post({"model": "zzz/alpha-big", "confirm_paid": True})
    assert status == 200 and payload["ok"] is True
    assert payload["model"] == "zzz/alpha-big"
    got = _model_get()[1]
    assert got["model"] == "zzz/alpha-big"
    # The served state carries the server-derived classification, not a client guess.
    assert got.get("cost_state") == "paid"


def test_unknown_cost_pin_fails_closed_with_machine_readable_reason(_cached_catalog, monkeypatch):
    """A11 pass002 law: cold/missing catalog cost knowledge NEVER becomes 'free'.

    An id the catalog cannot classify is refused BEFORE anything persists, with a stable
    machine-readable reason, whether the cache is cold or the id simply is not listed.
    Nothing invents a price. The client may later re-send with an explicit request-scoped
    confirmation — the server still never claims to know what the cost is.
    """
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    for request_id in ("does/not-exist-id",):  # listed neither as free nor paid
        _status, payload = _model_post({"model": request_id})
        assert _status == 409, payload
        assert payload["code"] == "MODEL_COST_UNKNOWN"
        assert payload["cost_state"] == "unknown"
        from core.cloud_escalation_policy import load_policy

        assert load_policy().model != request_id, "an unknown-cost pin must never persist unconfirmed"
        # Confirmed path: allowed but STILL unknown-cost in the served truth (no invented price).
        _ok_status, _confirmed = _model_post({"model": request_id, "confirm_paid": True})
        assert _ok_status == 200
        from core.cloud_escalation_policy import load_policy as load2

        assert load2().model == request_id
        got = _model_get()[1]
        assert got["cost_state"] == "unknown", "confirmed-but-unknown stays honestly unknown"


def test_unknown_pricing_row_fails_closed_as_paid_status_unknown(tmp_path, monkeypatch):
    """A row the catalog HAS but whose published pricing is indeterminate is not 'paid' either.

    model_is_free would read pricing-unknown as paid conservatively at EXECUTION time, but a
    pin-time decision must say precisely that it does not know: PAID_STATUS_UNKNOWN, refused
    unconfirmed like every other spend-needing pin."""
    import json as _json
    from datetime import datetime, timezone

    import core.openrouter_catalog as cat
    from core import runtime_paths

    runtime_paths.configure_runtime_home(tmp_path)
    extra_payload = {
        "data": [
            {"id": "mystery/unpublished-price", "name": "Mystery", "context_length": 8192,
             "pricing": {"prompt": None, "completion": None, "request": None}},
            {"id": "clearly/free-row", "name": "Free Row", "context_length": 4096,
             "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        ]
    }
    cache = tmp_path / "extra_catalog_cache.json"
    cache.write_text(
        _json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": extra_payload})
    )
    monkeypatch.setattr(cat, "_cache_path", lambda: cache)
    monkeypatch.setattr(
        cat, "refresh_openrouter_catalog",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("no network in tests")),
    )
    try:
        monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
        status, payload = _model_post({"model": "mystery/unpublished-price"})
        assert status == 409 and payload["code"] == "PAID_STATUS_UNKNOWN", payload
        free_status, _fp = _model_post({"model": "clearly/free-row"})
        assert free_status == 200, "a published all-zero row stays free without confirmation"
    finally:
        runtime_paths.configure_runtime_home(None)


# ------------------------------------------------------------- durable approval truth

def _mint_pending():
    from core.mode_permission_policy import OperatingMode, PermissionAction, _new_approval_request

    return _new_approval_request(
        session_id="cx-session",
        task_id="cx-task",
        intent="workspace.write_file",
        arguments={"path": "cx.txt"},
        actions=(PermissionAction.OVERWRITE_EXISTING_FILES,),
        mode=OperatingMode.MANUAL,
        mode_revision=0,
        source_context={},
        fingerprint="fp-cx",
    )


def test_pending_approval_survives_module_state_loss_and_resolves(_isolated_home):
    import core.mode_permission_policy as mpp

    request = _mint_pending()
    token = request["approval_id"]
    # Simulated daemon restart: in-memory approvals are gone; disk still holds the prompt.
    mpp._APPROVALS.clear()
    mpp._TASK_APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False
    resolved = mpp.resolve_approval(token, decision="allow")
    assert resolved is not None and resolved["status"] == "approved"
    # Resolved approvals leave the durable mirror — no sticky grant can be resurrected.
    mirror = json.loads((runtime_paths.active_data_dir() / "pending_approvals.json").read_text())
    assert token not in mirror


def test_expired_restored_pending_says_expired_not_resolvable(_isolated_home):
    import core.mode_permission_policy as mpp

    request = _mint_pending()
    token = request["approval_id"]
    mirror_path = runtime_paths.active_data_dir() / "pending_approvals.json"
    mirror = json.loads(mirror_path.read_text())
    mirror[token]["expires_at"] = 0.0
    mirror_path.write_text(json.dumps(mirror))
    mpp._APPROVALS.clear()
    mpp._PERSISTED_APPROVALS_RESTORED = False
    assert mpp.resolve_approval(token, decision="allow") is None
    state = mpp._APPROVALS[token]
    assert state["status"] == "expired"


# ------------------------------------------------------------------ served page truth

_PAGE = None


def _page_source() -> str:
    global _PAGE
    if _PAGE is None:
        _PAGE = open("core/vool_chat_page.py", encoding="utf-8").read()
    return _PAGE


def test_stream_reader_consumes_typed_nolla_terminal_frames():
    src = _page_source()
    idx = src.find("obj && obj.vool_event")
    reader_tail = src[idx: idx + 2400]
    assert "obj.vool_terminal" in reader_tail, "reader loop must consume typed no-answer terminals"
    assert "reason_code" in reader_tail


def test_no_response_paint_never_ends_a_typed_terminal_run_as_completed():
    src = _page_source()
    block_start = src.find("const t = run.terminal || null;")
    assert block_start > 0, "typed terminal branch missing from run-end paint"
    nr = src[block_start:block_start + 700]
    assert "'(no response)'" in nr, "plain empty ending still gets the honest no-response text"
    final_line = next(l for l in src.splitlines() if l.strip().startswith("if (!run.ended) finishRun(run,"))
    assert "run.terminal ? 'failed' : 'completed'" in final_line


def test_paid_send_gate_is_session_scoped_not_global_sticky():
    src = _page_source()
    ack_at = src.find("acknowledgePaidPin(chatId, String(selectedModel), ok)")
    assert ack_at > 0
    guard = src[src.find("sendRequiresPaidAck = false") : ack_at + 120]
    assert "window.confirm" in guard
    assert "acknowledgePaidPin(chatId, String(selectedModel), ok)" in guard, "ack is per conversation and exact model id"
    # The verdict is SERVER truth (the GET's cost_state), not a client-side guess; the
    # degraded-GET fallback still consults the catalog map instead of silently widening.
    assert "serverCostState === 'paid' || serverCostState === 'unknown'" in guard
    assert "degradedRow" in guard
    # A localStorage write here would be a sticky grant across sessions.
    assert "localStorage.setItem" not in guard


def test_boot_hydration_clears_stale_pin_when_server_pin_empty():
    src = _page_source()
    fn = src[src.find("async function hydrateModelFromServer()") :]
    fn = fn[: fn.find("\n}", 200) + 400]
    assert "setModelValue('vool', chatId)" in fn, (
        "an empty/unset server pin must CLEAR a stale browser pin — reload must never "
        "resurrect a previously paid client-side pin from localStorage"
    )


def test_boot_hydrates_selection_from_server_authority():
    src = _page_source()
    fn = src[src.find("async function hydrateModelFromServer()") :]
    fn = fn[: fn.find("\n}", 200)]
    assert "fetch('/api/cloud/model?session_id=' + encodeURIComponent(chatId))" in fn
    assert "setModelValue(serverModel, chatId)" in fn, "hydrated truth re-cached for the owning chat"
    assert "hydrateModelFromServer();" in src.split("// ---- Boot hydration")[1]


def test_switch_posts_confirm_paid_for_the_server_paid_gate():
    src = _page_source()
    seg = src[src.find("async function switchCloudModel") : src.find("async function switchAutoFreeModel")]
    # SERVER-OWNED truth: the FIRST POST carries NO client-asserted confirmation — the server
    # classifies and derives the requirement. confirm_paid rides only the post-409 retry, for
    # that request alone.
    #
    # ATTRIBUTION (2026-09-18): 9e079175 ("Make model selection and price review responsive
    # under slow reads") moved the wire into the shared `modelSelectionPost` helper (timeout +
    # per-selection dedup), so the literal `body: JSON.stringify(pinBody)` left this function
    # while the LAW held. The pin now names the helper's own fetch line and asserts the law
    # directly: the initial pinBody carries no confirm_paid, wherever its transport lives.
    assert "const pinBody = { model: id, session_id: chatId, selection_revision: revision };" in seg, (
        "the initial switch POST body must stay exactly model/session/revision(/provider)"
    )
    assert "await modelSelectionPost(pinBody)" in seg, "the initial POST rides the shared selection transport"
    helper_start = src.find("async function modelSelectionPost")
    helper = src[helper_start : helper_start + 900]
    helper = helper[: helper.find("\n}", 200)]
    assert "fetch('/api/cloud/model'" in helper and "body:JSON.stringify(body)" in helper, (
        "the selection transport still POSTs the pin body as JSON to the model-selection door"
    )
    pin_line = seg[seg.find("const pinBody") : seg.find("\n", seg.find("const pinBody"))]
    assert "confirm_paid" not in pin_line, (
        "the initial switch POST must not pre-assert confirm_paid (client never decides paid)"
    )
    assert "confirm_paid: true" in seg
    assert "paid_model_confirm_required" in seg
    assert "MODEL_COST_UNKNOWN" in seg and "PAID_STATUS_UNKNOWN" in seg, (
        "the page must react to every machine-readable refusal code the server can emit"
    )
