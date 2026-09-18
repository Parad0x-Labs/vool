"""The four model-facing wallet tools: status, propose, simulate, payment_status. The model can
propose and read; it can never approve, sign, raise limits or broadcast — those names do not
exist as tools, the handler has no path to them, and a served turn proves the whole flow.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.wallet._rig import DESTINATION, ScriptedRpc, phrase_leaked

pytestmark = [pytest.mark.safety]
PIN = "246810"
WALLET_TOOLS = ("wallet.status", "wallet.propose", "wallet.simulate", "wallet.payment_status")


def test_the_four_tools_are_contracted_and_nothing_operator_only_is(wallet_env):
    from core.runtime_tool_contracts import runtime_tool_contracts

    by_intent = {c.intent: c for c in runtime_tool_contracts()}
    for intent in WALLET_TOOLS:
        contract = by_intent[intent]
        assert contract.supported is True and contract.handler == "runtime" and contract.tool_surface == "wallet"
        assert contract.approval_requirement == "none"
        assert contract.side_effect_class in {"read_only", "creative_state"}
        assert contract.permission_actions == ("read_files",)
    for forbidden in ("wallet.approve", "wallet.sign", "wallet.broadcast", "wallet.set_limits", "wallet.limits", "wallet.submit_signature", "wallet.create_pocket", "wallet.reveal"):
        assert forbidden not in by_intent, forbidden
    assert by_intent["wallet.propose"].side_effect_class == "creative_state"
    assert set(by_intent["wallet.propose"].input_schema) == {"destination", "amount", "amount_minor", "asset", "chain", "network", "wallet_id", "memo", "idempotency_key"}


def test_tools_vanish_from_the_model_surface_when_the_wallet_is_off(monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    from core.runtime_tool_contracts import runtime_tool_contracts

    by_intent = {c.intent: c for c in runtime_tool_contracts()}
    assert all(by_intent[i].supported is False for i in WALLET_TOOLS)


def test_native_names_and_direct_render_membership(wallet_env):
    from core.agent_runtime.research_tool_loop_facade import _DIRECT_RENDER_TOOL_INTENTS
    from core.cloud_tool_call_contract import build_cloud_tool_definitions
    from core.runtime_execution_tools import runtime_execution_tool_specs

    specs = [spec for spec in runtime_execution_tool_specs() if spec.get("intent") in WALLET_TOOLS]
    names = {d.intent: d.name for d in build_cloud_tool_definitions(specs)}
    assert names == {"wallet.status": "wallet__status", "wallet.propose": "wallet__propose", "wallet.simulate": "wallet__simulate", "wallet.payment_status": "wallet__payment_status"}
    assert set(WALLET_TOOLS) <= _DIRECT_RENDER_TOOL_INTENTS


def test_handler_source_has_no_path_to_approval_signing_limits_or_broadcast():
    from core import runtime_execution_tools

    source = inspect.getsource(runtime_execution_tools._wallet_model_tool)
    for symbol in ("approve_and_execute", "signer_for", "set_limits", "submit_external_signature", "request_external_signature", "PinApprover", "_broadcast", "create_pocket_wallet", "restore_pocket_wallet", "reserve_spend"):
        assert symbol not in source, symbol


def test_handlers_propose_prepare_simulate_and_report_without_touching_a_key(wallet_env):
    from core.runtime_execution_tools import _wallet_model_tool
    from core.wallet import custody, proposals

    profile = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile
    status = _wallet_model_tool("wallet.status", {}, {"session_id": "s1"})
    assert status.ok and profile.public_key in status.response_text and "pocket_sealed" in status.response_text
    proposed = _wallet_model_tool("wallet.propose", {"destination": DESTINATION, "amount_minor": 1200, "asset": "SOL", "memo": "coffee"}, {"session_id": "s1"})
    assert proposed.ok and proposed.status == "pending_approval"
    proposal_id = proposed.details["observation"]["proposal_id"]
    assert proposal_id.startswith("pay-") and proposals.get_proposal(proposal_id).origin == proposals.ORIGIN_MODEL
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_PENDING_APPROVAL
    first_line = proposed.response_text.splitlines()[0]
    assert proposal_id in first_line and "pending_approval" in proposed.response_text
    simulated = _wallet_model_tool("wallet.simulate", {"proposal_id": proposal_id}, {"session_id": "s1"})
    assert simulated.ok and "fee" in simulated.response_text.lower() and "nothing was signed" in simulated.response_text.lower()
    pending = _wallet_model_tool("wallet.payment_status", {"proposal_id": proposal_id}, {"session_id": "s1"})
    assert pending.ok and "pending_approval" in pending.response_text
    # arguments that try to smuggle an approval are not in the schema -> refused by the dispatcher whitelist, and the handler ignores them anyway
    smuggled = _wallet_model_tool("wallet.propose", {"destination": DESTINATION, "amount_minor": 1, "asset": "SOL", "approve": True, "pin": PIN}, {"session_id": "s1"})
    assert smuggled.ok and proposals.get_proposal(smuggled.details["observation"]["proposal_id"]).state == proposals.STATE_PENDING_APPROVAL
    assert wallet_env["rpc"].send_count() == 0
    for text in (status.response_text, proposed.response_text, simulated.response_text, pending.response_text):
        assert PIN not in text and "seed" not in text.lower() and "phrase" not in text.lower()


def test_tool_wording_survives_the_wallet_honesty_gate(wallet_env):
    from core.agent_runtime.action_honesty_validator import _claims_false_payment
    from core.runtime_execution_tools import _wallet_model_tool
    from core.wallet import approval, custody, lifecycle

    custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN)
    proposed = _wallet_model_tool("wallet.propose", {"destination": DESTINATION, "amount_minor": 5, "asset": "SOL", "memo": ""}, None)
    proposal_id = proposed.details["observation"]["proposal_id"]
    lifecycle.default_lifecycle().approve_and_execute(proposal_id, approver=approval.PinApprover(PIN))
    done = _wallet_model_tool("wallet.payment_status", {"proposal_id": proposal_id}, None)
    assert "confirmed" in done.response_text and wallet_env["rpc"].send_count() == 1
    for text in (proposed.response_text, done.response_text, _wallet_model_tool("wallet.status", {}, None).response_text):
        assert not _claims_false_payment(text), text


def test_golden_tier_table_and_surface_snapshot_carry_the_wallet_tools(wallet_env):
    from tests.gauntlet.test_contract_map_golden import GOLDEN_TIERS
    from tests.gauntlet.tool_surface_snapshot import build_snapshot

    for intent in WALLET_TOOLS:
        assert intent in GOLDEN_TIERS
    snapshot = json.loads(json.dumps(build_snapshot()))
    text = json.dumps(snapshot)
    for native in ("wallet__status", "wallet__propose", "wallet__simulate", "wallet__payment_status"):
        assert native in text, native


def test_a_payment_request_requires_tools_only_while_the_wallet_is_on(wallet_env, monkeypatch):
    """The lane law behind the chat flow: a wallet demand is an action the runtime performs with its
    own tools, so the tool-less chat lane may not keep it; with the wallet off nothing changes."""
    import apps.vool_agent  # noqa: F401 - settles the import order the router needs
    from core.execution.planner import should_attempt_tool_intent
    from core.execution_requirements import requirements_for

    pay = f"Please pay 1500 lamports to {DESTINATION} for the report"
    status = "What is the status of payment pay-0123456789abcdef0123?"
    for text in (pay, status):
        reqs = requirements_for(text, task_class="unknown", source_context={"surface": "web"})
        assert reqs.tools_required and reqs.allowed_toolsets == ("wallet",) and reqs.forbids_toolless_lane(), text
        assert should_attempt_tool_intent(text, task_class="unknown", source_context={"surface": "web"}), text
    forbidden = requirements_for(pay + " and do not use any tools, just tell me how", task_class="unknown", source_context={})
    assert not forbidden.tools_required, "the user's own prohibition still wins"
    chat = requirements_for("what do you think about card payments in general", task_class="unknown", source_context={})
    assert not chat.tools_required
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    off = requirements_for(pay, task_class="unknown", source_context={})
    assert not off.tools_required and "wallet_action_request" not in off.reason_codes


def test_the_workflow_planner_never_sends_a_wallet_question_to_the_web(wallet_env):
    """A wallet status question classifies as research; the planner must not pre-plan a web search
    with the proposal id (the turn is confined to the wallet toolset)."""
    import apps.vool_agent  # noqa: F401 - settles the import order
    from core.execution.planner import plan_tool_workflow

    text = "What is the status of payment pay-0123456789abcdef0123?"
    decision = plan_tool_workflow(user_text=text, task_class="research", executed_steps=[], source_context={"surface": "web"})
    payload = dict(decision.next_payload or {})
    assert str(payload.get("intent") or "") not in {"web.search", "web.fetch", "web.research"}, payload
    # an ordinary research question still plans the web exactly as before
    ordinary = plan_tool_workflow(user_text="What is the latest documentation for the Solana JSON-RPC API?", task_class="research", executed_steps=[], source_context={"surface": "web"})
    assert str((ordinary.next_payload or {}).get("intent") or "") in {"web.search", "web.fetch", "web.research"}, ordinary


# --- served: the real daemon runs a model tool call end to end ------------------------------------

def _http(url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Origin": url.rsplit("/api", 1)[0]}, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@pytest.mark.served
def test_served_chat_turn_model_proposes_operator_approves_model_reports_confirmed(tmp_path: Path) -> None:
    from tests._blackbox_served_rig import ServedDaemon
    from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

    home = tmp_path / "home"
    provider = PromptRoutedProvider()
    rpc = ScriptedRpc()
    daemon = ServedDaemon(home, env_extra={"VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "VOOL_WALLET_TESTNET_RPC_URL": rpc.url, "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url, "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat"})
    provider.__enter__()
    rpc.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(home, provider.base_url)
        provider.reset()
        base = f"http://127.0.0.1:{daemon.port}"
        from core.wallet import custody

        _s, created = _http(f"{base}/api/wallet/pocket/create", {"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE})
        phrase = created["recovery_phrase"]
        session = "wallet-chat-1"
        turn1 = daemon.chat(f"Please pay 1500 lamports to {DESTINATION} for the report", session_id=session, model=MODEL, mode="auto")
        text1 = str((turn1.get("message") or {}).get("content") or "")
        assert "pending_approval" in text1 and "pay-" in text1, text1
        proposal_id = next(w.strip(".,:") for w in text1.split() if w.startswith("pay-"))
        assert rpc.send_count() == 0
        _s, st = _http(f"{base}/api/wallet/status")
        assert st["status"]["pending_approvals"] == 1 and st["status"]["pending"][0]["origin"] == "model"
        _s, done = _http(f"{base}/api/wallet/approve", {"proposal_id": proposal_id, "pin": PIN})
        assert done["ok"] and done["receipt"]["state"] == "confirmed" and rpc.send_count() == 1
        turn2 = daemon.chat(f"What is the status of payment {proposal_id}?", session_id=session, model=MODEL, mode="auto")
        text2 = str((turn2.get("message") or {}).get("content") or "")
        assert "confirmed" in text2 and done["receipt"]["tx_signature"] in text2, text2
        assert rpc.send_count() == 1
        # model context: no prompt the stand-in model ever saw carried the phrase or the PIN
        prompts = " ".join(str(c.get("prompt") or "") for c in provider.calls)
        assert PIN not in prompts
        assert phrase_leaked(phrase, prompts) is None, phrase_leaked(phrase, prompts)
        db = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
        try:
            rows = db.execute("SELECT event_type FROM runtime_session_events WHERE event_type LIKE 'tool_%'").fetchall()
        finally:
            db.close()
        assert rows, "the served ledger recorded the tool executions"
    finally:
        daemon.stop()
        rpc.__exit__(None, None, None)
        provider.__exit__(None, None, None)
