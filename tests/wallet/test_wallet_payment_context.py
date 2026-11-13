"""Why a payment exists, kept beside its chain facts (core.wallet.payment_context): who asked, the owner's words, and
how the service was found, recorded once per proposal from what the turn actually did, carried onto every receipt, and
answered in chat ("what did my agent spend on, and why"). SIMULATION: the swap rig (tests/wallet/_swap_rig.py)."""
from __future__ import annotations

import pytest

from tests.wallet._simulated_solana import USDC_MAINNET_MINT
from tests.wallet.test_wallet_swap import _approve, _quote, _wallet, rig  # noqa: F401  (rig is a fixture)

pytestmark = [pytest.mark.safety]


def test_the_trail_keeps_only_tool_names_and_their_query_or_url() -> None:
    from core.wallet import payment_context

    steps = [
        {"tool_name": "web.search", "ok": True, "arguments": {"query": "cheap weather api x402", "api_key": "sk-secret-123"}},
        {"tool_name": "web.fetch", "ok": True, "arguments": {"url": "https://api.example.com/docs", "headers": {"Authorization": "Bearer x"}}},
        {"tool_name": "wallet.status", "ok": False, "correction": True, "arguments": {"question": "ignored"}},
    ]
    trail = payment_context.trail_from_steps(steps)
    assert trail == [{"tool": "web.search", "ok": "1", "query": "cheap weather api x402"},
                     {"tool": "web.fetch", "ok": "1", "url": "https://api.example.com/docs"}]
    assert "sk-secret" not in str(trail) and "Bearer" not in str(trail)


@pytest.mark.parametrize(("trail", "kwargs", "kind", "text"), [
    ([{"tool": "web.search", "query": "usdc price api"}], {"resource_url": "https://pay.example.com/q"}, "web_search", "a web search for “usdc price api”"),
    ([{"tool": "web.fetch", "url": "https://docs.example.org/pricing"}], {}, "page", "a page the assistant read (docs.example.org)"),
    ([], {"plugin": "weather-pro"}, "plugin", "the plugin weather-pro"),
    ([], {"resource_url": "https://pay.example.com/q"}, "x402_endpoint", "a paid endpoint (pay.example.com) that asked for payment (x402)"),
    ([], {}, "your_request", "your own request"),
])
def test_how_the_service_was_found_is_read_from_what_the_turn_did(trail, kwargs, kind, text) -> None:
    from core.wallet import payment_context

    found = payment_context._found_via(trail, **kwargs)
    assert (found["kind"], found["text"]) == (kind, text)


def test_the_first_record_wins_and_a_follow_up_send_carries_the_same_story(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import payment_context

    store_module.reset_default_store()
    try:
        context = {payment_context.ASK_KEY: "send 10 usdc to Maya", payment_context.TRAIL_KEY: [], "session_id": "s1"}
        first = payment_context.capture("pay-1", source_context=context, by=payment_context.BY_OWNER_CHAT)
        again = payment_context.capture("pay-1", source_context={payment_context.ASK_KEY: "something else"}, by=payment_context.BY_PLUGIN, plugin="x")
        assert first == again and again["ask"] == "send 10 usdc to Maya" and again["by"] == payment_context.BY_OWNER_CHAT
        payment_context.copy("pay-1", "pay-2", note="the send the conversion pay-1 was for")
        assert payment_context.why_text("pay-2") == ("asked by you, in chat; your words: “send 10 usdc to Maya”; found through your own request; "
                                                     "the send the conversion pay-1 was for")
        assert payment_context.why_text("pay-unknown") == ""
    finally:
        store_module.reset_default_store()


def test_send_to_kas_after_a_conversion_keeps_who_asked_and_why_on_both_receipts_and_answers_it_in_chat(rig) -> None:  # noqa: F811
    from solders.keypair import Keypair

    from core.contacts import store
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import money_answers, payment_context, quotes
    from core.wallet.store import connection, loads

    node, _jup = rig
    _wallet(node)
    maya = str(Keypair().pubkey())
    node.fund_sol(maya, 5_000_000)
    store.create_contact(display_name="Maya", actor=store.ACTOR_OWNER, endpoints=[{"kind": "wallet", "value": maya}])
    ask = "send 10 usdc to Maya, convert some SOL if you need to"
    # what the tool loop hands a wallet spend door: the owner's words and this turn's earlier steps
    context = {"session_id": "chat-why", payment_context.ASK_KEY: ask, payment_context.TRAIL_KEY: []}

    yes = _dispatch_runtime_tool("wallet.swap", {"from_asset": "SOL", "to_asset": "USDC", "amount": "10", "side": "out", "then_send_to": "Maya", "then_send_amount": "10"},
                                 source_context=context)
    swap_id = yes.details["proposal"]["proposal_id"]
    first = _approve(swap_id, _quote(swap_id))
    send_id = first["next_proposal"]["proposal_id"]
    second = _approve(send_id, quotes.mint_quote(send_id))
    assert second["transfer"]["state"] == "confirmed"
    assert node.token_balance(maya, USDC_MAINNET_MINT) == 10_000_000

    with connection() as conn:
        rows = conn.execute("SELECT proposal_id, state, payload_json FROM wallet_receipts WHERE proposal_id IN (?, ?) AND state = 'confirmed'", (swap_id, send_id)).fetchall()
    receipts = {row[0]: loads(row[2], {}) for row in rows}
    assert set(receipts) == {swap_id, send_id}, rows
    for proposal_id, receipt in receipts.items():
        why = receipt["why"]
        assert why["asked_by"] == payment_context.BY_OWNER_CHAT and why["ask"] == ask and why["found_via"] == "your_request", (proposal_id, why)
        assert receipt["tx_signature"] and receipt["asset"] and receipt["network"], receipt
    assert receipts[send_id]["why"]["note"] == "sent after converting SOL to USDC for it"
    assert receipts[swap_id]["why"]["note"] == "converted to have the USDC for the send to Maya"

    reply = money_answers.answer("what did I spend today, and on what?", source_context={"session_id": "chat-why"}).text
    assert reply and "to Maya" in reply and "asked by you, in chat" in reply and "sent after converting SOL to USDC for it" in reply, reply
    assert "converted into 10 USDC in your own wallet (price from Jupiter)" in reply and "for the send to Maya" in reply, reply
    assert swap_id not in reply and send_id not in reply, "no ids in a chat answer"
