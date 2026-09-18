"""null:// remote dial after the money-authority retirement.

``try_dial`` (reach, SSRF guard, 402 parsing, the no-spend preview, the cap clamp, the
allow_spend / wallet gates) is behaviour that still exists and is pinned here unchanged. The one
retired surface is the default payer, ``_dial_pay_x402``: it no longer signs and settles a 402 --
it returns a typed, receipt-backed refusal (``core.wallet.authority.LEGACY_RETIRED``) and never
constructs the x402 client, wraps a signer, or re-requests the resource with a settlement proof.
Through ``try_dial`` that refusal reads as ``None`` (fall back to LOCAL), exactly like any other
payment error, with the fault on file.
"""
from __future__ import annotations

import json
from typing import Any
from unittest import mock

from core.null_dial import try_dial
from core.null_resolver import NullDomainRecord
from core.wallet.authority import LEGACY_RETIRED
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post

# A public, SSRF-safe endpoint. The IP-level guard is stubbed per-test so these
# never touch the network or DNS.
_SAFE_ENDPOINT = "https://pay.parad0xlabs.com/x402"


def _record(endpoint: str = _SAFE_ENDPOINT, owner: str = "OwNeRwAlLeT1111111111111111111111111111111") -> NullDomainRecord:
    return NullDomainRecord(
        name="web0",
        owner=owner,
        arweave_txid=None,
        x402_endpoint=endpoint,
        passport_hash=None,
    )


# ---------------------------------------------------------------------------
# try_dial — direct unit coverage
# ---------------------------------------------------------------------------

def test_try_dial_posts_task_and_returns_remote_result() -> None:
    calls: list[dict[str, Any]] = []

    def fake_http(method: str, url: str, *, body=None, headers=None, timeout=15.0):
        calls.append({"method": method, "url": url, "body": body})
        return {"result": "remote answer", "confidence": 1.0}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "do the thing",
            record=_record(),
            wallet=None,
            allow_spend=False,
            http=fake_http,
        )

    assert out == {"result": "remote answer", "confidence": 1.0}
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == _SAFE_ENDPOINT
    assert calls[0]["body"]["prompt"] == "do the thing"
    assert calls[0]["body"]["uri"] == "null://web0/task"


def test_try_dial_returns_none_when_no_endpoint() -> None:
    http_calls: list[Any] = []
    out = try_dial(
        "null://web0/task",
        "task",
        record=_record(endpoint=""),
        http=lambda *a, **k: http_calls.append(1) or {},
    )
    assert out is None
    assert http_calls == []  # no endpoint -> no network call


def test_try_dial_returns_none_when_endpoint_ssrf_unsafe() -> None:
    http_calls: list[Any] = []
    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=False):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            http=lambda *a, **k: http_calls.append(1) or {},
        )
    assert out is None
    assert http_calls == []  # unsafe -> never dialed


def test_try_dial_returns_none_on_remote_error() -> None:
    def erroring_http(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "message": "boom"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial("null://web0/task", "task", record=_record(), http=erroring_http)
    assert out is None  # non-payment error -> caller falls back to local


def test_try_dial_returns_none_on_http_raise() -> None:
    def raising_http(method, url, *, body=None, headers=None, timeout=15.0):
        raise OSError("connection refused")

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial("null://web0/task", "task", record=_record(), http=raising_http)
    assert out is None


def test_try_dial_402_without_allow_spend_returns_preview_no_spend() -> None:
    pay_calls: list[Any] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.02}

    def fake_pay(*a, **k):
        pay_calls.append((a, k))
        return {"status": "paid"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=None,
            allow_spend=False,
            http=http_402,
            pay=fake_pay,
        )

    assert out is not None
    assert out["status"] == "user_action_required"
    assert out["amount_usdc"] == 0.02
    assert pay_calls == []  # allow_spend off -> never paid


def test_try_dial_402_with_allow_spend_pays_within_cap() -> None:
    pay_calls: list[dict[str, Any]] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.02}

    def fake_pay(resource_url, wallet, *, max_spend_usdc=1.0, allow_spend=False, **k):
        pay_calls.append({"resource": resource_url, "max_spend_usdc": max_spend_usdc, "allow_spend": allow_spend})
        return {"status": "paid", "resource_response": "unlocked", "amount_paid_usdc": 0.02}

    sentinel_wallet = object()
    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=sentinel_wallet,
            allow_spend=True,
            max_spend_usdc=0.05,
            http=http_402,
            pay=fake_pay,
        )

    assert out == {"status": "paid", "resource_response": "unlocked", "amount_paid_usdc": 0.02}
    assert len(pay_calls) == 1
    assert pay_calls[0]["resource"] == _SAFE_ENDPOINT
    assert pay_calls[0]["allow_spend"] is True
    assert pay_calls[0]["max_spend_usdc"] == 0.05  # the caller cap, within the 1.0 ceiling


def test_try_dial_cap_is_clamped_to_one_usdc_ceiling() -> None:
    pay_calls: list[dict[str, Any]] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.5}

    def fake_pay(resource_url, wallet, *, max_spend_usdc=1.0, allow_spend=False, **k):
        pay_calls.append({"max_spend_usdc": max_spend_usdc})
        return {"status": "paid"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=object(),
            allow_spend=True,
            max_spend_usdc=999.0,  # absurd cap must be clamped
            http=http_402,
            pay=fake_pay,
        )

    assert pay_calls[0]["max_spend_usdc"] == 1.0  # clamped to the 1.0 USDC ceiling


def test_try_dial_402_amount_over_cap_returns_preview_not_pay() -> None:
    pay_calls: list[Any] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        return {"error": True, "status": 402, "amountUsdc": 0.50}

    def fake_pay(*a, **k):
        pay_calls.append(1)
        return {"status": "paid"}

    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task",
            "task",
            record=_record(),
            wallet=object(),
            allow_spend=True,
            max_spend_usdc=0.05,  # quote 0.50 > cap 0.05
            http=http_402,
            pay=fake_pay,
        )

    assert out["status"] == "user_action_required"
    assert pay_calls == []  # over the cap -> never paid


# ---------------------------------------------------------------------------
# /api/null service route — dial gated by the policy flag
# ---------------------------------------------------------------------------

def _runtime() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _agent(runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
    return {"response": f"local: {text}", "confidence": 1.0}


def test_service_flag_off_runs_local_and_never_dials(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: False)
    http_calls: list[Any] = []

    def boom_dial(*a, **k):
        http_calls.append(1)
        raise AssertionError("try_dial must not be called when the flag is off")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: _record(),
        try_dial_provider=boom_dial,
    )

    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["result"] == "local: review"  # local path ran
    assert data.get("dialed") is None
    assert http_calls == []  # dial provider never invoked


def test_service_flag_on_returns_remote_result(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)
    seen: list[dict[str, Any]] = []

    def fake_dial(uri, task_text, *, record, wallet, allow_spend, **k):
        seen.append({"uri": uri, "task": task_text, "endpoint": record.x402_endpoint, "allow_spend": allow_spend})
        return {"result": "remote answer"}

    def agent_must_not_run(*a, **k):
        raise AssertionError("local agent must not run when dial returns a result")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=agent_must_not_run,
        resolve_null_domain_provider=lambda name: _record(),
        try_dial_provider=fake_dial,
    )

    assert resp.status == 200
    data = json.loads(resp.body)
    assert data["dialed"] is True
    assert data["result"] == {"result": "remote answer"}
    assert seen[0]["task"] == "review"
    assert seen[0]["endpoint"] == _SAFE_ENDPOINT
    assert seen[0]["allow_spend"] is False  # the service never spends


def test_service_flag_on_but_resolution_miss_falls_back_local(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)

    def boom_dial(*a, **k):
        raise AssertionError("try_dial must not be called when the name does not resolve")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: None,  # miss
        try_dial_provider=boom_dial,
    )

    data = json.loads(resp.body)
    assert data["result"] == "local: review"


def test_service_flag_on_dial_returns_none_falls_back_local(monkeypatch) -> None:
    from core import policy_engine

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: _record(),
        try_dial_provider=lambda *a, **k: None,  # remote miss / error
    )

    data = json.loads(resp.body)
    assert data["result"] == "local: review"  # graceful local fallback


def test_service_threads_resolved_owner_into_quote_when_dial_on(monkeypatch) -> None:
    from core import policy_engine

    # Owner resolution is a live on-chain read, so it only runs when remote dial
    # is opted in. With the flag ON the quote carries the REAL on-chain owner.
    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)
    owner = "RealOwnerWallet22222222222222222222222222222"

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task"},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=lambda name: _record(owner=owner),
        try_dial_provider=lambda *a, **k: None,  # no remote result -> local run, quote still built
    )

    data = json.loads(resp.body)
    assert data["quote"]["recipient_wallet"] == owner


def test_service_keeps_stub_wallet_when_dial_off(monkeypatch) -> None:
    from core import policy_engine

    # With the flag OFF the route is fully local: NO resolution, so the injected
    # provider is never called and the quote keeps the default wallet (zero network).
    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: False)
    calls: list[str] = []

    def _resolver(name: str):
        calls.append(name)
        return _record(owner="RealOwnerWallet22222222222222222222222222222")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task"},
        headers={},
        runtime=_runtime(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=_agent,
        resolve_null_domain_provider=_resolver,
    )

    data = json.loads(resp.body)
    assert data["quote"]["recipient_wallet"] == "stub-wallet"
    assert calls == []  # resolver never invoked when dial is off


# ── canonical 402 parsing stays ─────────────────────────────────────────────

def test_amount_and_requirements_from_canonical_402() -> None:
    from core.null_dial import _amount_from_402, _requirements_from_402
    resp = {"error": True, "status": 402, "accepts": [
        {"network": "solana-devnet", "maxAmountRequired": "1500", "payTo": "R", "asset": "M"}]}
    assert _amount_from_402(resp) == 0.0015          # atomic 1500 -> 0.0015 USDC
    assert _requirements_from_402(resp)["payTo"] == "R"
    assert _requirements_from_402({"status": 402}) is None


# ── the default dial payer is retired: typed refusal, nothing settled ────────

class _NeverSigns:
    def __init__(self) -> None:
        self.touched = 0

    def pubkey(self):
        self.touched += 1
        raise AssertionError("pubkey read")

    def sign(self, *_args):
        self.touched += 1
        raise AssertionError("sign called")

    sign_message = sign


class _NeverConstructed:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("X402Client was constructed by the retired dial payer")


def _assert_retired_receipt(fault_id: str, surface: str) -> None:
    from core.faults.recorder import fault_by_id
    from core.security_events.catalog import SEC_WALLET_LEGACY_SURFACE_RETIRED
    from core.security_events.store import list_security_events

    assert fault_id.startswith("fault-")
    record = fault_by_id(fault_id)
    assert record is not None and record.code == LEGACY_RETIRED
    assert record.context.get("surface") == surface
    observed = [e for e in list_security_events(limit=50) if e.fault_id == fault_id]
    assert observed and observed[0].sec_code == SEC_WALLET_LEGACY_SURFACE_RETIRED


def _canonical_requirements() -> dict[str, Any]:
    return {"network": "solana-devnet", "asset": "MINT", "maxAmountRequired": "1000",
            "payTo": "R", "extra": {"feePayer": "F"}}


def test_dial_pay_x402_refuses_typed_and_never_settles_or_unlocks(monkeypatch) -> None:
    from core import null_dial

    monkeypatch.setattr("core.x402.client.X402Client", _NeverConstructed)
    monkeypatch.setattr("core.x402.client.wallet_signer", lambda w: (_ for _ in ()).throw(AssertionError("wallet_signer reached")))
    # Even a GRANTED spend brake opens nothing: the refusal is not the brake saying no.
    monkeypatch.setattr("core.spend_authorization.require_spend_authorized", lambda **kw: (True, "ok"))
    http = mock.Mock(side_effect=AssertionError("no unlock re-request may be sent"))
    wallet = _NeverSigns()
    req = _canonical_requirements()

    out = null_dial._dial_pay_x402(
        "https://agent.example/x402", wallet, max_spend_usdc=1.0, allow_spend=True,
        owner_local=True, requirements=req, task_text="do-it", http=http,
    )

    assert out["error"] == LEGACY_RETIRED
    assert out["detail"]
    assert out["requirements"] == req and out["requirements"] is not req  # the 402 is surfaced, not paid
    assert "status" not in out and "payment_tx" not in out
    _assert_retired_receipt(out["fault_id"], "null_dial._dial_pay_x402")
    http.assert_not_called()
    assert wallet.touched == 0


def test_dial_pay_x402_refuses_the_same_way_when_not_owner_local(monkeypatch) -> None:
    # The owner-local fence used to answer `spend_not_authorized` before signing; the retired
    # surface answers the typed refusal for every caller, owner or not -- nothing is signed.
    from core.null_dial import _dial_pay_x402

    monkeypatch.setattr("core.x402.client.X402Client", _NeverConstructed)
    out = _dial_pay_x402(
        "https://agent.example/x402", _NeverSigns(), max_spend_usdc=1.0, allow_spend=True,
        owner_local=False, requirements={"network": "solana", "asset": "M", "payTo": "R"},
    )
    assert out["error"] == LEGACY_RETIRED
    _assert_retired_receipt(out["fault_id"], "null_dial._dial_pay_x402")


def test_dial_pay_x402_not_attempted_guards() -> None:
    from core.faults.recorder import list_faults
    from core.null_dial import _dial_pay_x402

    # Without an opt-in or without a 402 to pay, nothing is attempted -- and no refusal is filed,
    # because no money surface was reached.
    assert _dial_pay_x402("u", _NeverSigns(), allow_spend=False, requirements={"a": 1})["error"] == "payment_not_attempted"
    assert _dial_pay_x402("u", _NeverSigns(), allow_spend=True, requirements=None)["error"] == "payment_not_attempted"
    assert list_faults(code=LEGACY_RETIRED, limit=10) == []
    # A wallet-less opt-in lands on the retired surface's refusal, not on a payment.
    assert _dial_pay_x402("u", None, allow_spend=True, requirements={"a": 1})["error"] == LEGACY_RETIRED


def test_try_dial_with_the_default_payer_refuses_and_falls_back_local(monkeypatch) -> None:
    """End to end through try_dial: a 402 + allow_spend + a wallet used to settle; now the default
    payer refuses, try_dial answers None (LOCAL fallback) and the only request is the first POST."""
    from core.faults.recorder import list_faults

    monkeypatch.setattr("core.x402.client.X402Client", _NeverConstructed)
    calls: list[dict[str, Any]] = []

    def http_402(method, url, *, body=None, headers=None, timeout=15.0):
        calls.append({"url": url, "headers": dict(headers or {})})
        return {"error": True, "status": 402, "accepts": [
            {"network": "solana-devnet", "maxAmountRequired": "20000", "payTo": "R", "asset": "M"}]}

    wallet = _NeverSigns()
    with mock.patch("core.null_dial.is_ssrf_safe_url", return_value=True):
        out = try_dial(
            "null://web0/task", "task", record=_record(), wallet=wallet,
            allow_spend=True, max_spend_usdc=0.05, http=http_402,
        )

    assert out is None
    assert len(calls) == 1 and "X-PAYMENT-RECEIPT" not in calls[0]["headers"]
    assert wallet.touched == 0
    filed = [f for f in list_faults(code=LEGACY_RETIRED, limit=10) if f.context.get("surface") == "null_dial._dial_pay_x402"]
    assert len(filed) == 1
