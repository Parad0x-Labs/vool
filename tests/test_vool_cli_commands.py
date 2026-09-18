"""CLI command surfaces. Money commands are typed refusals now (core.wallet is the one money
authority): `x402-pay`, `wallet-init`, `wallet-topup-hot`, `wallet-move-to-cold`,
`wallet-buy-credits` all return 2 with a fault receipt and never sign, broadcast or mint a key.
Read-only commands (resolve, receipts, manifest, sell-quote, web) keep their contracts."""
from __future__ import annotations

import json
import re
from unittest import mock

import pytest

from apps.vool_cli import (
    WEB_DISABLED_MESSAGE,
    _explorer_tx_link,
    _quote_target_to_uri,
    _strip_null_suffix,
    cmd_manifest,
    cmd_register,
    cmd_resolve,
    cmd_sell_quote,
    cmd_wallet_buy_credits,
    cmd_wallet_init,
    cmd_wallet_move_to_cold,
    cmd_wallet_topup_hot,
    cmd_web,
    cmd_x402_pay,
    format_receipt_row,
    render_manifest_lines,
    render_quote_lines,
    render_receipt_lines,
    render_resolve_lines,
    resolve_record_payload,
)
from core.execution.models import ToolIntentExecution
from core.null_resolver import NullDomainRecord
from core.vool_wallet import WALLET_FILENAME

LEGACY = "wallet_legacy_surface_retired"
_B58_RUN = re.compile(r"[1-9A-HJ-NP-Za-km-z]{40,}")


@pytest.fixture(autouse=True)
def _iso_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    yield tmp_path
    assert not list(tmp_path.rglob(WALLET_FILENAME)), "a CLI command minted a legacy key file"


def _legacy_receipts() -> list:
    from core.faults.recorder import list_faults

    return [f for f in list_faults(limit=200) if f.code == LEGACY]

# --- resolve --------------------------------------------------------------

def _record(
    *,
    name: str = "web0",
    owner: str = "Owner111",
    arweave: str | None = "txid-abc",
    endpoint: str = "https://parad0xlabs.com/x402",
    passport: str | None = None,
) -> NullDomainRecord:
    return NullDomainRecord(
        name=name,
        owner=owner,
        arweave_txid=arweave,
        x402_endpoint=endpoint,
        passport_hash=passport,
    )


def test_cmd_register_refuses_premium_and_bad_names(capsys) -> None:
    # 1-3 char premium names are auction-only; bad charset is refused — both before any wallet/RPC.
    assert cmd_register("ab.null") == 2
    assert "auction" in capsys.readouterr().out.lower()
    assert cmd_register("bad_name!.null") == 2
    assert "a-z" in capsys.readouterr().out


def test_cmd_register_dry_run_never_executes(capsys) -> None:
    fake_plan = mock.Mock(total_sol=0.0112)
    preview = mock.Mock(status="preview", message="Register mysite on Solana MAINNET for ~0.0112 SOL", plan=fake_plan)
    registered = mock.Mock(pubkey="28hxXaSfXrY2UTEEuHseP1VfRdq3nUyyPaYBMHsWW2VX")
    with mock.patch("core.null_register_execute.preview_registration", return_value=preview) as preview_mock, \
         mock.patch("core.null_register_execute.execute_registration") as exec_mock, \
         mock.patch("core.wallet.custody.default_wallet", return_value=registered), \
         mock.patch("core.vool_wallet.VoolWallet.load", side_effect=AssertionError("legacy wallet loaded")):
        rc = cmd_register("mysite.null")  # 6 chars (valid), no --allow-spend / --mainnet
    assert rc == 0
    exec_mock.assert_not_called()  # dry run must never sign/broadcast
    preview_mock.assert_called_once_with("mysite", registered.pubkey)  # the canonical owner, read-only
    assert "Dry run" in capsys.readouterr().out


def test_cmd_register_with_a_stale_legacy_key_file_returns_a_code_not_a_traceback(tmp_path, capsys) -> None:
    stale = tmp_path / "data" / "keys" / WALLET_FILENAME
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    with mock.patch("core.null_register_execute.execute_registration") as exec_mock, \
         mock.patch("core.null_register_execute.sign_and_broadcast_registration") as sign_mock, \
         mock.patch("core.vool_wallet.VoolWallet.load", side_effect=AssertionError("legacy wallet loaded")):
        rc = cmd_register("mysite.null")
    out = capsys.readouterr().out
    assert rc == 1
    exec_mock.assert_not_called()
    sign_mock.assert_not_called()
    assert "Traceback" not in out and "installer" not in out.lower()
    assert "no wallet is registered" in out.lower()
    assert stale.read_text(encoding="utf-8") == "{}"  # untouched
    stale.unlink()  # the fixture's teardown proves the command minted none of its own


# --- money commands: typed refusals ----------------------------------------------------------

def _assert_refusal_text(out: str) -> None:
    assert LEGACY in out
    assert "wallet.propose" in out and "fault-" in out
    assert _B58_RUN.search(out) is None
    assert "tx:" not in out.lower() and "explorer.solana.com" not in out


def _assert_refusal_json(raw: str) -> dict:
    payload = json.loads(raw)
    assert payload["ok"] is False and payload["error"] == LEGACY
    assert payload["fault_id"].startswith("fault-") and payload["message"].strip()
    assert "payment_tx" not in payload and "explorer" not in payload
    return payload


def test_cmd_x402_pay_refuses_even_with_every_spend_flag_and_a_keypair(tmp_path, capsys) -> None:
    keypair = tmp_path / "kp.json"
    keypair.write_text("[" + ",".join(["1"] * 64) + "]", encoding="utf-8")
    with mock.patch("core.x402.client.X402Client") as client_cls:
        rc = cmd_x402_pay(0.5, "11111111111111111111111111111111", keypair_path=str(keypair), mainnet=True, allow_spend=True)
    assert rc == 2
    client_cls.assert_not_called()
    _assert_refusal_text(capsys.readouterr().out)
    receipts = _legacy_receipts()
    assert receipts and receipts[0].context.get("surface") == "cli.x402-pay"


def test_cmd_x402_pay_dry_run_is_the_same_refusal_not_a_would_pay_preview(capsys) -> None:
    assert cmd_x402_pay(0.5, "11111111111111111111111111111111", json_mode=True) == 2
    payload = _assert_refusal_json(capsys.readouterr().out)
    assert "would_pay" not in payload


def test_cmd_x402_pay_rejects_nothing_silently_on_bad_input(capsys) -> None:
    # even malformed arguments get the typed refusal, never a usage line that implies a working payer
    assert cmd_x402_pay(0.0, "") == 2
    assert LEGACY in capsys.readouterr().out


@pytest.mark.parametrize(
    ("command", "surface"),
    [
        (lambda **kw: cmd_wallet_init(hot_address="Hot111", cold_address="Cold111", cold_secret="s3cret", hot_usdc=5.0, cold_usdc=50.0, **kw), "cli.wallet-init"),
        (lambda **kw: cmd_wallet_topup_hot(usdc=5.0, cold_secret="s3cret", **kw), "cli.wallet-topup-hot"),
        (lambda **kw: cmd_wallet_move_to_cold(usdc=5.0, cold_secret="s3cret", **kw), "cli.wallet-move-cold"),
        (lambda **kw: cmd_wallet_buy_credits(usdc=5.0, **kw), "cli.wallet-buy-credits"),
    ],
)
def test_simulated_custody_commands_refuse_typed(command, surface: str, capsys) -> None:
    with mock.patch("apps.vool_cli.DNAWalletManager", side_effect=AssertionError("simulated custody reached"), create=True), \
         mock.patch("apps.vool_cli._resolve_secret", side_effect=AssertionError("a secret prompt was shown"), create=True):
        assert command() == 2
        _assert_refusal_text(capsys.readouterr().out)
        assert command(json_mode=True) == 2
        _assert_refusal_json(capsys.readouterr().out)
    surfaces = [f.context.get("surface") for f in _legacy_receipts()]
    assert surfaces.count(surface) == 2
    out_all = capsys.readouterr().out
    assert "s3cret" not in out_all


def test_strip_null_suffix_trims_trailing_null() -> None:
    assert _strip_null_suffix("web0.null") == "web0"
    assert _strip_null_suffix("  web0.NULL ") == "web0"
    assert _strip_null_suffix("web0") == "web0"
    assert _strip_null_suffix("") == ""


def test_resolve_record_payload_for_hit() -> None:
    payload = resolve_record_payload("web0", _record(passport="ab" * 32))
    assert payload["resolved"] is True
    assert payload["owner"] == "Owner111"
    assert payload["arweave_txid"] == "txid-abc"
    assert payload["x402_endpoint"] == "https://parad0xlabs.com/x402"
    assert payload["passport_present"] is True


def test_resolve_record_payload_for_miss() -> None:
    payload = resolve_record_payload("nope", None)
    assert payload == {"name": "nope", "resolved": False}


def test_render_resolve_lines_hit_and_miss() -> None:
    hit = render_resolve_lines(resolve_record_payload("web0", _record()))
    joined = "\n".join(hit)
    assert "Name:           web0.null" in joined
    assert "Owner:          Owner111" in joined
    assert "x402 endpoint:  https://parad0xlabs.com/x402" in joined
    assert "Passport:       none" in joined

    miss = render_resolve_lines(resolve_record_payload("ghost", None))
    assert any("unresolved" in line for line in miss)


def test_cmd_resolve_hit_returns_zero(capsys) -> None:
    with mock.patch("apps.vool_cli.resolve_null_domain", return_value=_record()) as resolver:
        assert cmd_resolve("web0.null") == 0
    resolver.assert_called_once_with("web0")
    out = capsys.readouterr().out
    assert "VOOL .null resolution" in out
    assert "web0.null" in out


def test_cmd_resolve_miss_returns_one(capsys) -> None:
    with mock.patch("apps.vool_cli.resolve_null_domain", return_value=None):
        assert cmd_resolve("ghost.null") == 1
    assert "unresolved" in capsys.readouterr().out


def test_cmd_resolve_json(capsys) -> None:
    with mock.patch("apps.vool_cli.resolve_null_domain", return_value=_record()):
        assert cmd_resolve("web0.null", json_mode=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"] is True
    assert payload["name"] == "web0"


def test_cmd_resolve_empty_name() -> None:
    assert cmd_resolve(".null") == 2


# --- receipts -------------------------------------------------------------

def test_explorer_tx_link_is_mainnet_explorer() -> None:
    assert _explorer_tx_link("sig123") == "https://explorer.solana.com/tx/sig123"


def test_format_receipt_row_with_signature() -> None:
    row = {
        "event_type": "solana_proof_anchored",
        "target_id": "task-1",
        "details_json": json.dumps({"signature": "SIGabc", "confidence": 0.91}),
        "created_at": "2026-06-22T00:00:00+00:00",
    }
    entry = format_receipt_row(row)
    assert entry["task_id"] == "task-1"
    assert entry["confidence"] == 0.91
    assert entry["signature"] == "SIGabc"
    assert entry["explorer_link"] == "https://explorer.solana.com/tx/SIGabc"


def test_format_receipt_row_finalized_without_signature() -> None:
    row = {
        "event_type": "parent_output_finalized",
        "target_id": "task-2",
        "details_json": json.dumps({"status": "finalized", "confidence": 0.5}),
        "created_at": "2026-06-22T00:00:01+00:00",
    }
    entry = format_receipt_row(row)
    assert entry["signature"] == ""
    assert entry["explorer_link"] == ""
    assert entry["confidence"] == 0.5


def test_format_receipt_row_handles_bad_json() -> None:
    row = {"event_type": "x", "target_id": "t", "details_json": "{not-json", "created_at": "now"}
    entry = format_receipt_row(row)
    assert entry["confidence"] == 0.0
    assert entry["signature"] == ""


def test_render_receipt_lines_empty_and_populated() -> None:
    assert any("No anchored" in line for line in render_receipt_lines([]))
    entry = format_receipt_row(
        {
            "event_type": "solana_proof_anchored",
            "target_id": "task-1",
            "details_json": json.dumps({"signature": "SIGabc", "confidence": 0.91}),
            "created_at": "now",
        }
    )
    lines = render_receipt_lines([entry])
    joined = "\n".join(lines)
    assert "Task:      task-1" in joined
    assert "Explorer:  https://explorer.solana.com/tx/SIGabc" in joined


# --- manifest -------------------------------------------------------------

def test_render_manifest_lines() -> None:
    manifest_dict = {
        "worker_id": "vool",
        "top_tier": "drone",
        "top_tps": 12.5,
        "context_window": 32768,
        "provider_ids": ["ollama:qwen"],
        "tools": [],
        "price_per_token_usdc": 0.000001,
        "privacy_mode": "plain",
    }
    joined = "\n".join(render_manifest_lines(manifest_dict))
    assert "Worker ID:      vool" in joined
    assert "Providers:      ollama:qwen" in joined
    assert "Tools:          none" in joined
    assert "Price/token:    0.00000100 USDC" in joined
    assert "Privacy mode:   plain" in joined


def test_cmd_manifest_json(capsys) -> None:
    assert cmd_manifest(json_mode=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "worker_id" in payload
    assert "price_per_token_usdc" in payload


# --- sell-quote -----------------------------------------------------------

def test_quote_target_to_uri() -> None:
    assert _quote_target_to_uri("null://task/code-review") == "null://task/code-review"
    assert _quote_target_to_uri("web0.null") == "null://task/web0"
    assert _quote_target_to_uri("web0") == "null://task/web0"
    assert _quote_target_to_uri("") == ""


def test_render_quote_lines() -> None:
    payload = {
        "uri": "null://task/code-review",
        "service": "task",
        "path": "code-review",
        "session_id": "null-abc",
        "quote": {
            "amount_usdc": 0.0005,
            "recipient_wallet": "Wallet111",
            "usdc_mint": "Mint111",
            "quote_hash": "hash111",
        },
    }
    joined = "\n".join(render_quote_lines(payload))
    assert "URI:            null://task/code-review" in joined
    assert "Service:        task" in joined
    assert "Amount:         0.00050000 USDC" in joined
    assert "Recipient:      Wallet111" in joined


def test_cmd_sell_quote_runs_read_only(capsys) -> None:
    assert cmd_sell_quote("null://task/code-review", json_mode=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"] == "task"
    assert payload["quote"]["amount_usdc"] > 0


def test_cmd_sell_quote_empty_target() -> None:
    assert cmd_sell_quote("") == 2


# --- web (opt-in) ---------------------------------------------------------

def test_cmd_web_disabled_prints_opt_in_message_and_makes_no_call(capsys) -> None:
    # Web is off by default. The command must refuse without dispatching anything.
    with mock.patch("apps.vool_cli.policy_engine.allow_web_fallback", return_value=False), mock.patch(
        "apps.vool_cli._run_web_intent",
        side_effect=AssertionError("web command must not dispatch while web is off"),
    ):
        assert cmd_web(query="latest qwen release notes") == 2
    out = capsys.readouterr().out
    assert WEB_DISABLED_MESSAGE in out
    assert "VOOL_ENABLE_WEB=1" in out


def test_cmd_web_disabled_json_reports_enabled_false(capsys) -> None:
    with mock.patch("apps.vool_cli.policy_engine.allow_web_fallback", return_value=False):
        assert cmd_web(query="anything", json_mode=True) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False
    assert payload["intent"] == "web.search"


def test_cmd_web_empty_query_returns_usage() -> None:
    assert cmd_web(query="") == 2


def test_cmd_web_enabled_routes_search_through_executor(capsys) -> None:
    execution = ToolIntentExecution(
        handled=True,
        ok=True,
        status="executed",
        response_text='Search results for "qwen":\n- Qwen notes - https://example.test/qwen',
        mode="tool_executed",
        tool_name="web.search",
    )
    with mock.patch("apps.vool_cli.policy_engine.allow_web_fallback", return_value=True), mock.patch(
        "apps.vool_cli._bootstrap_cli_storage", return_value=None
    ), mock.patch("apps.vool_cli._run_web_intent", return_value=execution) as runner:
        assert cmd_web(query="qwen") == 0
    runner.assert_called_once()
    intent, arguments = runner.call_args.args
    assert intent == "web.search"
    assert arguments["query"] == "qwen"
    assert "Search results for" in capsys.readouterr().out


def test_cmd_web_enabled_fetch_routes_web_fetch_intent(capsys) -> None:
    execution = ToolIntentExecution(
        handled=True,
        ok=True,
        status="executed",
        response_text="Fetched https://example.test/\n- Status: ok",
        mode="tool_executed",
        tool_name="web.fetch",
    )
    with mock.patch("apps.vool_cli.policy_engine.allow_web_fallback", return_value=True), mock.patch(
        "apps.vool_cli._bootstrap_cli_storage", return_value=None
    ), mock.patch("apps.vool_cli._run_web_intent", return_value=execution) as runner:
        assert cmd_web(fetch_url="https://example.test/") == 0
    intent, arguments = runner.call_args.args
    assert intent == "web.fetch"
    assert arguments["url"] == "https://example.test/"


# --- dial --allow-spend: a typed refusal, and the dial itself still runs without spending -------

def test_cmd_dial_allow_spend_refuses_typed_and_still_dials_without_spending(tmp_path, capsys, monkeypatch) -> None:
    from apps.vool_cli import cmd_dial
    from core.faults.recorder import list_faults

    monkeypatch.setattr("apps.vool_cli.policy_engine.null_dial_enabled", lambda: True)  # remote dial itself is opt-in
    # a stale legacy key file must be neither probed nor loaded
    stale = tmp_path / "data" / "keys" / WALLET_FILENAME
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    record = NullDomainRecord(name="web0", owner="OwNeR111", arweave_txid=None, x402_endpoint="https://pay.example/x402", passport_hash=None)
    seen: list[dict] = []

    def _dial(uri, task_text, *, record, wallet, allow_spend, **kw):
        seen.append({"wallet": wallet, "allow_spend": allow_spend})
        return {"status": "user_action_required", "amount_usdc": 0.02}

    with mock.patch("apps.vool_cli.resolve_null_domain", return_value=record), \
         mock.patch("core.null_dial.try_dial", _dial), \
         mock.patch("core.vool_wallet.VoolWallet.load", side_effect=AssertionError("legacy wallet loaded")):
        rc = cmd_dial("web0.null", "task", allow_spend=True, max_spend_usdc=0.05, json_mode=True)
    out = capsys.readouterr().out
    assert rc == 0, out
    payload = json.loads(out)  # json mode: the refusal rides inside the one payload, nothing else is printed
    assert payload["dialed"] is True
    assert payload["spend"]["error"] == LEGACY and payload["spend"]["fault_id"].startswith("fault-")
    assert seen == [{"wallet": None, "allow_spend": False}]  # dialed once, without any spend authority
    assert any(f.context.get("surface") == "cli.dial-allow-spend" for f in list_faults(code=LEGACY, limit=20))
    assert stale.read_text(encoding="utf-8") == "{}"  # untouched
    stale.unlink()  # the fixture's teardown proves the command minted none of its own
