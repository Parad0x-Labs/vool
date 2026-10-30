"""Served regression probes for transport and PC-wallet boundaries; external services are local fakes."""
from __future__ import annotations

import json
import secrets
import threading
import time

import pytest

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call as raw
from tests.mobile.test_sealed_transport import pair
from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc

PIN = "482913"


@pytest.fixture
def rig(desktop, monkeypatch):
    from core.wallet import caller_binding, chains

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
    caller_binding.reset_for_tests()
    chains.invalidate_chain_identity()
    base = desktop[0]
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as rpc:
        rpc.real_signature = True
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({chains.SOLANA_MAINNET: rpc.url}))
        s, created = http(base, "/api/wallet/setup/create", {"network": chains.SOLANA_MAINNET, "method": "pin",
            "credential": PIN, "credential_confirmation": PIN, "creation_key": secrets.token_hex(16)})
        assert s == 200, created
        wid = created["setup"]["wallet_id"]
        s, rev = http(base, "/api/wallet/setup/reveal", {"wallet_id": wid, "credential": PIN})
        assert s == 200
        assert http(base, "/api/wallet/setup/acknowledge", {"wallet_id": wid, "ack_token": rev["backup"]["ack_token"]})[0] == 200
        phone = pair(desktop, activate_chat=False)
        cap = {"wallet_id": wid, "network": chains.SOLANA_MAINNET, "asset": "SOL",
               "per_payment_minor": 10_000_000, "daily_minor": 15_000_000}
        s, g = http(base, "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"],
            "rules": {"chat": True, "wallet_view": True, "payment_propose": True}, "credential": PIN, "payment_cap": cap})
        assert s == 200, g
        yield base, phone, wid, cap, rpc, desktop
    chains.invalidate_chain_identity()


def propose(phone, dest_byte, amount_minor, key=None):
    from core.vool_wallet import b58encode

    params = {"destination": b58encode(bytes([dest_byte]) * 32), "amount_minor": amount_minor,
              "idempotency_key": key or secrets.token_hex(16)}
    s, v = raw(*phone, "wallet.preview", params)
    if s != 200:
        return s, v, params
    params["review_token"] = v["result"]["review_token"]
    params["warnings_acknowledged"] = v["result"]["warnings"]
    s, v = raw(*phone, "wallet.propose", params)
    return s, v, params


def quote_and_approve(base, pid, pin=PIN):
    s, q = http(base, "/api/wallet/quote", {"proposal_id": pid})
    if s != 200:
        return s, q
    return http(base, "/api/wallet/approve", {"proposal_id": pid, "quote_id": q["quote"]["quote_id"],
                                              "quote_digest": q["quote"]["digest"], "pin": pin})


def test_probe_A_full_pc_approval_of_phone_proposal_settles_once_and_counts(rig):
    from core.wallet import receipts
    base, phone, wid, cap, rpc, _ = rig
    s, v, params = propose(phone, 91, 8_000_000)
    assert s == 200, v
    pid = v["result"]["proposal"]["proposal_id"]
    s, ans = quote_and_approve(base, pid)
    assert s == 200 and ans.get("ok"), ans
    print("APPROVE1", s, json.dumps(ans)[:400])
    print("SENDS_AFTER_1", rpc.send_count())
    s2, ans2 = quote_and_approve(base, pid)
    print("APPROVE2", s2, json.dumps(ans2)[:300])
    print("SENDS_AFTER_2", rpc.send_count())
    rows = [r for r in receipts.list_receipts(limit=50) if r.get("proposal_id") == pid]
    print("WALLET_RECEIPTS_FOR_PID", len(rows), [r.get("kind") or r.get("state") for r in rows])
    s, rec = raw(*phone, "wallet.receipts")
    mine = [r for r in rec["result"]["receipts"] if r.get("proposal_id") == pid]
    print("PHONE_RECEIPT_VIA", [(r.get("via"), r.get("device_id") == phone[3]["device_id"]) for r in mine])
    # same token replay after settlement must not mint a new payment
    s, again = raw(*phone, "wallet.propose", params)
    print("REPLAY_SAME_TOKEN", s, (again.get("result") or {}).get("proposal", {}).get("proposal_id") == pid, again.get("code"))
    # daily cap must count the confirmed payment incl. fee: 8M+fee + 8M > 15M
    s3, v3, _ = propose(phone, 92, 8_000_000)
    print("SECOND_PAYMENT_SAME_DAY", s3, v3.get("code"))
    assert rpc.send_count() == 1


def test_probe_B_concurrent_pc_approvals_send_once(rig):
    base, phone, wid, cap, rpc, _ = rig
    s, v, _ = propose(phone, 93, 7_000_000)
    assert s == 200, v
    pid = v["result"]["proposal"]["proposal_id"]
    s, q = http(base, "/api/wallet/quote", {"proposal_id": pid})
    assert s == 200, q
    body = {"proposal_id": pid, "quote_id": q["quote"]["quote_id"], "quote_digest": q["quote"]["digest"], "pin": PIN}
    out = []
    def go():
        out.append(http(base, "/api/wallet/approve", body))
    ts = [threading.Thread(target=go) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print("CONCURRENT", sorted((o[0], o[1].get("error") or o[1].get("ok")) for o in out))
    print("SENDS", rpc.send_count())
    assert rpc.send_count() <= 1


def test_probe_C_fee_pushes_total_over_phone_per_payment_cap(rig):
    base, phone, wid, cap, rpc, _ = rig
    s, v, _ = propose(phone, 94, 9_999_000)  # + 5000 fee = 10_004_000 > 10_000_000
    print("PROPOSE", s, v.get("code"))
    if s != 200:
        return
    pid = v["result"]["proposal"]["proposal_id"]
    s, ans = quote_and_approve(base, pid)
    print("APPROVE", s, ans.get("error"), (ans.get("context") or ans.get("detail") or ""))
    print("SENDS", rpc.send_count())
    assert rpc.send_count() == 0


def test_probe_D_unknown_outcome_no_double_pay(rig, monkeypatch):
    from core.wallet import lifecycle, proposals
    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 0.5)
    base, phone, wid, cap, rpc, _ = rig
    rpc.send_mode = "accept_then_500"
    rpc.status_mode = "none"
    s, v, params = propose(phone, 95, 6_000_000)
    assert s == 200, v
    pid = v["result"]["proposal"]["proposal_id"]
    s, ans = quote_and_approve(base, pid)
    print("APPROVE_UNKNOWN", s, json.dumps(ans)[:300])
    print("STATE", proposals.get_proposal(pid).state, "SENDS", rpc.send_count())
    rpc.send_mode = "ok"
    rpc.status_mode = "none"
    s, ans = quote_and_approve(base, pid)
    print("PC_RETRY_SAME", s, (ans.get("error") or ans.get("ok")), "SENDS", rpc.send_count())
    s, again = raw(*phone, "wallet.propose", params)
    print("PHONE_REPLAY_SAME_TOKEN", s, again.get("code"), (again.get("result") or {}).get("proposal", {}).get("proposal_id") == pid)
    s, v2, _ = propose(phone, 95, 6_000_000)  # new token, same payee+amount: 6M(unknown)+6M <= 15M -> a NEW payment
    print("PHONE_NEW_TOKEN_SAME_INTENT", s, v2.get("code"))
    assert s == 403 and v2.get("code") == "wallet_approval_rejected", v2
    if s == 200:
        pid2 = v2["result"]["proposal"]["proposal_id"]
        s, ans = quote_and_approve(base, pid2)
        print("PC_APPROVE_SECOND_WHILE_FIRST_UNKNOWN", s, ans.get("error") or ans.get("ok"), "SENDS", rpc.send_count())


def test_probe_E_wallet_limit_lowered_after_grant_is_reclamped(rig):
    import dataclasses

    from core.wallet import limits
    base, phone, wid, cap, rpc, _ = rig
    before = limits.load_limits(wid, "SOL")
    limits.set_limits(wid, "SOL", dataclasses.replace(before, per_tx_minor=5_000_000))
    s, v, _ = propose(phone, 89, 6_000_000)
    print("PROPOSE_ABOVE_LOWERED_WALLET_CAP", s, v.get("code"))
    s, v, _ = propose(phone, 89, 4_000_000)
    print("PROPOSE_BELOW", s)
    if s == 200:
        pid = v["result"]["proposal"]["proposal_id"]
        limits.set_limits(wid, "SOL", dataclasses.replace(before, per_tx_minor=3_000_000))
        s, ans = quote_and_approve(base, pid)
        print("PC_APPROVE_AFTER_WALLET_CAP_LOWERED", s, ans.get("error"), "SENDS", rpc.send_count())


def test_probe_D2_unknown_outcome(rig, monkeypatch):
    from core.wallet import lifecycle, proposals
    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 0.5)
    base, phone, wid, cap, rpc, _ = rig
    rpc.send_mode = "accept_then_500"
    rpc.status_mode = "none"
    s, v, params = propose(phone, 88, 6_000_000)
    pid = v["result"]["proposal"]["proposal_id"]
    s, ans = quote_and_approve(base, pid)
    print("APPROVE_UNKNOWN", s, ans.get("error") or (ans.get("transfer") or {}).get("state"))
    print("STATE", proposals.get_proposal(pid).state, "SENDS", rpc.send_count())
    s, ans = quote_and_approve(base, pid)
    print("PC_RETRY_SAME", s, ans.get("error") or (ans.get("transfer") or {}).get("state"), "SENDS", rpc.send_count())
    s, again = raw(*phone, "wallet.propose", params)
    print("PHONE_REPLAY_SAME_TOKEN", s, again.get("code"), ((again.get("result") or {}).get("proposal") or {}).get("proposal_id") == pid)
    s, v2, _ = propose(phone, 88, 6_000_000)
    print("PHONE_NEW_TOKEN_SAME_INTENT", s, v2.get("code"))
    assert s == 403 and v2.get("code") == "wallet_approval_rejected", v2
    if s == 200:
        s_, a_ = quote_and_approve(base, v2["result"]["proposal"]["proposal_id"])
        print("PC_APPROVE_DUPLICATE_INTENT_WHILE_FIRST_UNKNOWN", s_, a_.get("error") or (a_.get("transfer") or {}).get("state"), "SENDS", rpc.send_count())
    s, v3, _ = propose(phone, 87, 9_500_000)
    print("PHONE_DAILY_COUNTS_UNKNOWN(6M+6M+9.5M>15M)", s, v3.get("code"))
    assert s == 403, v3
    s, rel = http(base, "/api/wallet/transfers")
    print("TRANSFERS", [(t.get("proposal_id") == pid, t.get("state")) for t in (rel.get("transfers") or [])])


def test_probe_F_phone_chat_owner_local(rig):
    base, phone, wid, cap, rpc, desk = rig
    for text in ("/startx402", "/stopx402"):
        s, v = raw(*phone, "chats.send", {"message": text})
        print("CHAT", text, s, str((v.get("result") or {}).get("reply") or v)[:300])
    from core.wallet import limits
    print("FROZEN_AFTER_PHONE_CHAT", limits.is_frozen())


def test_probe_G_unfreeze_with_other_wallet_pin_and_mirror(rig):
    from core.wallet import limits
    base, phone, wid, cap, rpc, _ = rig
    assert raw(*phone, "wallet.freeze")[0] == 200
    from core.wallet_spend_policy_store import load_policy_and_ledger
    try:
        policy, _ = load_policy_and_ledger(None, allow_missing_policy=True)
        print("LEGACY_MIRROR_FROZEN_AFTER_PHONE_FREEZE", policy.frozen, "CANONICAL", limits.is_frozen())
        assert policy.frozen and limits.is_frozen()
    except Exception as exc:
        print("LEGACY_MIRROR_ERR", type(exc).__name__, exc)
    s, v = http(base, "/api/mobile/settings", {"operation": "unfreeze", "wallet_id": wid, "credential": "000000"})
    print("UNFREEZE_WRONG_PIN", s, v.get("error"), limits.is_frozen())
    s, v = http(base, "/api/mobile/settings", {"operation": "unfreeze", "wallet_id": "", "credential": ""})
    print("UNFREEZE_NO_WALLET", s, v.get("error"), limits.is_frozen())
    s, v = raw(*phone, "wallet.freeze", {"frozen": False})
    print("PHONE_UNFREEZE_ATTEMPT", s, limits.is_frozen())
    s, v = http(base, "/api/mobile/settings", {"operation": "unfreeze", "wallet_id": wid, "credential": PIN},
                headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"})
    print("UNFREEZE_CROSS_SITE", s, v.get("error"), limits.is_frozen())


def test_probe_H_bot_like_direct_propose_skips_preview_and_warnings(rig):
    """The PC stores and enforces acknowledgement, and shows the same warnings."""
    base, phone, wid, cap, rpc, _ = rig
    from core.vool_wallet import b58encode
    params = {"destination": b58encode(bytes([96]) * 32), "amount_minor": 9_000_000, "idempotency_key": secrets.token_hex(16)}
    s, v = raw(*phone, "wallet.preview", params)
    print("WARNINGS", v["result"]["warnings"])
    s, refused = raw(*phone, "wallet.propose", {**params, "review_token": v["result"]["review_token"]})
    assert s == 403 and refused.get("code") == "wallet_approval_rejected", refused
    s, proposal = raw(*phone, "wallet.propose", {**params, "review_token": v["result"]["review_token"], "warnings_acknowledged": v["result"]["warnings"]})
    assert s == 200, proposal
    pid = proposal["result"]["proposal"]["proposal_id"]
    s, q = http(base, "/api/wallet/quote", {"proposal_id": pid})
    assert s == 200 and q["quote"]["fields"]["mobile_review"]["warnings"] == v["result"]["warnings"], q


def test_probe_I_confusable_contact_name(rig):
    from core.contacts import store
    from core.vool_wallet import b58encode
    from core.wallet import chains
    base, phone, wid, cap, rpc, _ = rig
    store.create_contact(display_name="Alice", actor=store.ACTOR_OWNER,
        endpoints=[{"kind": "wallet", "value": b58encode(bytes([97]) * 32), "chain_network": chains.SOLANA_MAINNET}])
    for name in ("Alice", "Аlice", "Alice "):
        params = {"destination": name, "amount_minor": 1_000_000, "idempotency_key": secrets.token_hex(16)}
        s, v = raw(*phone, "wallet.preview", params)
        print("PREVIEW", repr(name), s, v.get("code"), (v.get("result") or {}).get("warnings"), str(v.get("error") or "")[:120])


def test_probe_J_device_revoked_between_preview_and_propose_and_v1_rules(rig):
    from core.web.api.mobile_companion_api import mobile_companion_service, revoke_device
    base, phone, wid, cap, rpc, _ = rig
    from core.vool_wallet import b58encode
    params = {"destination": b58encode(bytes([98]) * 32), "amount_minor": 1_000_000, "idempotency_key": secrets.token_hex(16)}
    s, v = raw(*phone, "wallet.preview", params)
    tok = v["result"]["review_token"]
    revoke_device(mobile_companion_service(), phone[3]["device_id"])
    s, v = raw(*phone, "wallet.propose", {**params, "review_token": tok})
    print("PROPOSE_AFTER_REVOKE", s, v.get("code"))
    from core.wallet import proposals
    print("PROPOSALS", len(proposals.list_proposals()))


def test_probe_C2_fee_detail(rig):
    from core.wallet.store import connection
    base, phone, wid, cap, rpc, _ = rig
    s, v, _ = propose(phone, 99, 9_999_000)
    pid = v["result"]["proposal"]["proposal_id"]
    s, q = http(base, "/api/wallet/quote", {"proposal_id": pid})
    f = q["quote"]["fields"]
    print("QUOTE_FEE_FIELDS", {k: f[k] for k in f if "fee" in k or "total" in k or "amount" in k})
    s, ans = http(base, "/api/wallet/approve", {"proposal_id": pid, "quote_id": q["quote"]["quote_id"],
                                                "quote_digest": q["quote"]["digest"], "pin": PIN})
    t = ans.get("transfer") or {}
    print("APPROVED", s, {k: t[k] for k in t if "fee" in k or "amount" in k or k == "state"})
    with connection() as conn:
        print("CLAIM_ROW", tuple(conn.execute("SELECT fee_minor FROM mobile_payment_claims WHERE proposal_id=?", (pid,)).fetchone()))
    print("SENDS", rpc.send_count(), "PHONE_CAP_PER", cap["per_payment_minor"])


def test_probe_K_command_projection_reaches_money_settings_door_without_caller_evidence(rig):
    from core.command_registry.execute import ExecutionContext
    from core.command_registry.groups import delegated_routes
    from core.wallet import limits
    base, phone, wid, cap, rpc, desk = rig
    ctx = ExecutionContext(projection="api", extra={"client_host": "127.0.0.1", "runtime": desk[1]})
    wallet = delegated_routes._wallet_post("/api/wallet/limits")(delegated_routes.RouteInput(body={"wallet_id": wid}), ctx)
    print("WALLET_PROJECTION", json.dumps(getattr(wallet, "detail", None) or getattr(wallet, "data", None), default=str)[:200])
    limits.set_frozen(True)
    out = delegated_routes._mobile_post("/api/mobile/settings")(delegated_routes.RouteInput(
        body={"operation": "unfreeze", "wallet_id": wid, "credential": "000000"}), ctx)
    print("MOBILE_PROJECTION_WRONG_PIN", json.dumps(getattr(out, "detail", None) or getattr(out, "data", None), default=str)[:200])
    out = delegated_routes._mobile_post("/api/mobile/settings")(delegated_routes.RouteInput(
        body={"operation": "unfreeze", "wallet_id": wid, "credential": PIN}), ctx)
    print("MOBILE_PROJECTION_UNFREEZE", json.dumps(getattr(out, "detail", None) or getattr(out, "data", None), default=str)[:200], "FROZEN", limits.is_frozen())
    out = delegated_routes._mobile_post("/api/mobile/settings")(delegated_routes.RouteInput(
        body={"operation": "device_rules", "device_id": phone[3]["device_id"], "rules": {"location": True, "camera": True}}), ctx)
    print("MOBILE_PROJECTION_RIGHTS_NO_PIN", json.dumps(getattr(out, "detail", None) or getattr(out, "data", None), default=str)[:200])

    for output in (out,):
        result = getattr(output, "detail", None) or getattr(output, "data", None)
        assert "wallet_caller_refused" in json.dumps(result)
    assert limits.is_frozen()
    from core.web.api.mobile_companion_api import mobile_companion_service
    device = mobile_companion_service().store.device(phone[3]["device_id"])
    assert not device["rules"].get("location") and not device["rules"].get("camera")
