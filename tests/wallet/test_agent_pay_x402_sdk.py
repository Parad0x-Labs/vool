"""An agent pays a real x402 request from its own wallet, inside its allowance and not one step outside it.

The same lane as an owner-approved payment (tests/wallet/test_wallet_x402_sdk_pilot.py): a v2 402 offering USDC on
Base Sepolia -> ONE proposal bound to that request -> the Pilot quote -> the claim, the wallet's own ceilings, ONE
EIP-3009 authorization signed by the agent wallet's real sealed key -> the chain decides. What differs is who says
yes: the agent's allowance, or the owner when the allowance does not.

SIMULATED CHAIN: the loopback node answers as Base Sepolia and runs USDC as the token runs it; the resource builds and
reads the x402 headers with the SDK's own encoders and its facilitator submits on that chain.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.wallet._rig_x402_sdk import BASE_SEPOLIA, PAID_BODY, ScriptedUsdcChain, ScriptedX402SdkResource

pytest.importorskip("x402", reason="the x402 SDK lane is the optional `x402` extra")

pytestmark = [pytest.mark.safety]

PIN = "482913"
AMOUNT = 250_000  # 0.25 USDC
FUNDED = 2_000_000
PAYEE = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
THIEF = "0x00000000000000000000000000000000000BEEF1"


@pytest.fixture
def lane(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", str(AMOUNT * 4))
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    with ScriptedUsdcChain(network=BASE_SEPOLIA) as chain:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: chain.url}))
        environment.set_active_environment("testnet")
        yield chain
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


@pytest.fixture
def agent(lane):
    """The owner's wallet, and the researcher agent's own funded wallet."""
    from core.wallet import agent_wallets, pilot_custody

    created = pilot_custody.create_pilot_wallet(network=BASE_SEPOLIA, method="pin", credential=PIN, credential_confirmation=PIN, creation_key="owner-1")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    owner = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    made = agent_wallets.create_agent_wallet("researcher", network=BASE_SEPOLIA, credential=PIN)
    shown = agent_wallets.reveal_backup(made["wallet_id"], credential=PIN)
    wallet = agent_wallets.acknowledge_backup(made["wallet_id"], ack_token=shown["ack_token"])
    lane.fund_token(wallet["address"], FUNDED)
    return {"owner": owner, "wallet": wallet, "chain": lane}


def _allow(agent, resource, *, pay_to: str = "", **over) -> str:
    from core.wallet import allowances as alw

    terms = dict(agent_id="researcher", wallet_id=agent["wallet"]["wallet_id"], network=BASE_SEPOLIA, asset="USDC", per_payment_minor=AMOUNT, window_seconds=alw.DAY,
                 window_minor=AMOUNT * 3, total_minor=AMOUNT * 4, max_payments=10, min_interval_seconds=0, expires_at=time.time() + alw.DAY,
                 services=(alw.ServiceRule(resource.url, AMOUNT * 4, pay_to),))
    terms.update(over)
    return alw.create_allowance(alw.AllowanceSpec(**terms), credential=PIN)


class Site(ScriptedX402SdkResource):
    """One site selling several things: each address it is asked at is its own offer (the 402 names that address)."""

    asked_at = ""

    def challenge(self):
        from x402.schemas import PaymentRequired

        return PaymentRequired.model_validate({"x402Version": 2, "error": "payment required", "accepts": [self.requirement()],
                                               "resource": {"url": self.asked_at or self.url, "description": "Base numbers", "mimeType": "application/json"}})


def _park(agent, url: str, site: Site | None = None) -> str:
    from core.wallet import x402

    if site is not None:
        site.asked_at = url
    parked = x402.fetch_paid_resource(url, wallet_id=agent["wallet"]["wallet_id"])
    assert parked.status == x402.OUTCOME_PAYMENT_REQUIRED, parked
    return parked.proposal_id


def _pay(proposal_id: str, who: str = "researcher") -> dict:
    from core.wallet import agent_pay

    return agent_pay.pay(proposal_id, agent_id=who)


def _hold(proposal_id: str):
    from core.wallet.store import connection

    with connection() as conn:
        row = conn.execute("SELECT amount_minor, state FROM wallet_spend_ledger WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return tuple(row) if row else None


def test_an_agent_pays_a_pinned_service_inside_its_allowance_with_no_one_asked(agent):
    from core.wallet import allowances as alw
    from core.wallet import proposals, x402

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        allowance_id = _allow(agent, resource, pay_to=PAYEE)
        proposal_id = _park(agent, resource.url)
        outcome = _pay(proposal_id)
        assert (outcome["decision"], outcome["paid"], outcome["delivered"], outcome["transfer"]["state"]) == ("pay", True, True, "confirmed")
        assert len(resource.paid_requests) == 1 and len(resource.landed) == 1
        assert (chain.token_balance(agent["wallet"]["address"]), chain.token_balance(PAYEE)) == (FUNDED - AMOUNT, AMOUNT)
        assert _hold(proposal_id) == (AMOUNT, "settled"), "the wallet's own ceilings held it too"
        view = alw.allowance_view(allowance_id)
        assert (view["total_used_minor"], view["payments"]) == (AMOUNT, 1)
        assert x402.retry_paid_resource(proposal_id).body == PAID_BODY, "the agent's request gets what it paid for"
        assert proposals.get_proposal(proposal_id).state == proposals.STATE_CONFIRMED
        again = _pay(proposal_id)
        assert (again["decision"], again["reason"]) == ("refuse", "not_awaiting_approval") and len(resource.paid_requests) == 1
        assert [row["reason"] for row in alw.decisions(agent_id="researcher")] == [alw.REASON_WITHIN]


def test_the_first_payment_to_a_service_is_the_owners_and_pins_its_recipient_and_a_swapped_recipient_never_rides(agent):
    from core.wallet import agent_pay, quotes
    from core.wallet import allowances as alw
    from core.wallet.errors import WalletFault

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        allowance_id = _allow(agent, resource)
        first = _park(agent, resource.url)
        asked = _pay(first)
        assert (asked["decision"], asked["reason"], asked["paid"]) == ("ask_owner", alw.REASON_FIRST_PAYMENT, False)
        assert resource.paid_requests == [] and _hold(first) is None and alw.allowance_view(allowance_id)["total_used_minor"] == 0
        quote = quotes.mint_quote(first)
        for wrong in ("", "593027"):
            with pytest.raises(WalletFault):
                agent_pay.approve_as_owner(first, quote_id=quote["quote_id"], quote_digest=quote["digest"], credential=wrong)
        assert resource.paid_requests == []
        approved = agent_pay.approve_as_owner(first, quote_id=quote["quote_id"], quote_digest=quote["digest"], credential=PIN)
        assert approved["transfer"]["state"] == "confirmed" and approved["pinned_service"] is True
        view = alw.allowance_view(allowance_id)
        assert view["services"][0]["pay_to"] == PAYEE.lower() and view["total_used_minor"] == 0, "the owner's own approval counts against no allowance"

        second = _park(agent, resource.url + "?q=2", resource)
        assert _pay(second)["paid"] is True, "the pinned service is now paid without asking"

        resource.pay_to = THIEF
        third = _park(agent, resource.url + "?q=3", resource)
        swapped = _pay(third)
        assert (swapped["decision"], swapped["reason"]) == ("ask_owner", alw.REASON_RECIPIENT_CHANGED)
        assert chain.token_balance(THIEF) == 0 and len(resource.paid_requests) == 2


def test_over_the_allowance_another_agent_or_the_owners_own_wallet_pays_nothing(agent):
    from core.wallet import allowances as alw
    from core.wallet import x402

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _allow(agent, resource, pay_to=PAYEE, window_minor=AMOUNT, total_minor=AMOUNT, services=(alw.ServiceRule(resource.url, AMOUNT, PAYEE),))
        other = _pay(_park(agent, resource.url + "?a=1", resource), who="writer")
        assert (other["decision"], other["reason"]) == ("ask_owner", alw.REASON_NO_ALLOWANCE)
        assert _pay(_park(agent, resource.url + "?a=1", resource))["paid"] is True
        over = _pay(_park(agent, resource.url + "?a=2", resource))
        assert (over["decision"], over["reason"]) == ("ask_owner", alw.REASON_WINDOW_USED)
        resource.amount_minor = AMOUNT + 1
        dear = _pay(_park(agent, resource.url + "?a=3", resource))
        assert (dear["decision"], dear["reason"]) == ("ask_owner", alw.REASON_OVER_PER_PAYMENT)
        resource.amount_minor = AMOUNT
        chain.fund_token(agent["owner"]["address"], FUNDED)
        resource.asked_at = resource.url + "?a=4"
        parked = x402.fetch_paid_resource(resource.url + "?a=4", wallet_id=agent["owner"]["wallet_id"])
        mine = _pay(parked.proposal_id)
        assert (mine["decision"], mine["reason"]) == ("ask_owner", "not_an_agent_wallet")
        assert len(resource.paid_requests) == 1 and chain.token_balance(PAYEE) == AMOUNT


def test_a_direct_send_from_the_agent_wallet_never_rides_an_allowance(agent):
    from core.wallet import lifecycle, proposals

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _allow(agent, resource, pay_to=PAYEE)
        chain.fund(agent["wallet"]["address"], 10**18)
        proposal = proposals.propose_transaction(wallet_id=agent["wallet"]["wallet_id"], destination=PAYEE, amount_minor=10**15, asset="ETH", origin=proposals.ORIGIN_MODEL,
                                                 network=BASE_SEPOLIA)
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
        sent = _pay(proposal.proposal_id)
        assert (sent["decision"], sent["reason"]) == ("ask_owner", "not_a_service_payment")
        from core.wallet import transfers

        assert transfers.get_transfer_by_id(proposal.proposal_id) is None and chain.send_count() == 0
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL, "it waits for the owner like any send"


def test_the_freeze_stops_an_agent_payment_and_gives_nothing_away(agent):
    from core.wallet import allowances as alw
    from core.wallet import limits

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        allowance_id = _allow(agent, resource, pay_to=PAYEE)
        proposal_id = _park(agent, resource.url)
        limits.set_frozen(True)
        frozen = _pay(proposal_id)
        assert (frozen["decision"], frozen["reason"]) == ("refuse", alw.REASON_FROZEN)
        assert resource.paid_requests == [] and alw.allowance_view(allowance_id)["total_used_minor"] == 0


def test_a_wallet_ceiling_refusal_after_the_allowance_said_yes_gives_the_allowance_hold_back(agent):
    from core.wallet import allowances as alw
    from core.wallet import limits
    from core.wallet.errors import WalletFault

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        allowance_id = _allow(agent, resource, pay_to=PAYEE)
        proposal_id = _park(agent, resource.url)
        limits.set_limits(agent["wallet"]["wallet_id"], "USDC", limits.SpendLimits(AMOUNT - 1, AMOUNT * 10, AMOUNT * 10))
        with pytest.raises(WalletFault):
            _pay(proposal_id)
        assert resource.paid_requests == [] and chain.token_balance(PAYEE) == 0
        assert alw.allowance_view(allowance_id)["total_used_minor"] == 0, "an allowance never adds to the wallet's own ceilings, and holds nothing for a payment that never left"


def test_a_site_that_takes_the_payment_and_delivers_nothing_trips_the_breaker(agent):
    from core.wallet import allowances as alw

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE, mode="drop") as resource:
        allowance_id = _allow(agent, resource, pay_to=PAYEE)
        for index in range(2):
            outcome = _pay(_park(agent, resource.url + f"?n={index}", resource))
            assert outcome["decision"] == "pay", outcome
        view = alw.allowance_view(allowance_id)
        assert (view["state"], view["pause_reason"]) == (alw.STATE_PAUSED, alw.PAUSE_BREAKER_UNDELIVERED), view
        stopped = _pay(_park(agent, resource.url + "?n=9", resource))
        assert (stopped["decision"], stopped["reason"]) == ("ask_owner", alw.REASON_PAUSED) and len(resource.paid_requests) == 2


def test_a_payment_whose_outcome_nobody_knows_stays_held_against_the_allowance(agent):
    from core.wallet import allowances as alw

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE, mode="never") as resource:
        allowance_id = _allow(agent, resource, pay_to=PAYEE)
        outcome = _pay(_park(agent, resource.url))
        assert outcome["decision"] == "pay" and outcome["paid"] is False, outcome
        assert alw.allowance_view(allowance_id)["total_used_minor"] == AMOUNT, "signed and sent, never proven unused: still held"
        again = _pay(_park(agent, resource.url + "?x=1", resource))
        assert again["decision"] in {"pay", "ask_owner"}


# --- through the assistant's own tool, as a chat turn reaches it ---------------------------------------------------

def _assistant(agent) -> dict:
    from core.wallet import agent_wallets

    made = agent_wallets.create_agent_wallet("assistant", network=BASE_SEPOLIA, credential=PIN)
    shown = agent_wallets.reveal_backup(made["wallet_id"], credential=PIN)
    wallet = agent_wallets.acknowledge_backup(made["wallet_id"], ack_token=shown["ack_token"])
    agent["chain"].fund_token(wallet["address"], FUNDED)
    return wallet


def _tool(url: str, context: dict):
    from core.runtime_execution_tools import _dispatch_runtime_tool

    return _dispatch_runtime_tool("x402.propose", {"resource_url": url}, source_context=context)


def test_the_assistants_paid_fetch_pays_inside_its_allowance_on_the_owners_own_turn_only(agent):
    from core.request_trust import HTTP_INGRESS_KEY, OWNER_LOCAL_KEY, TURN_AUTHOR_KEY, in_process_owner_source_context
    from core.wallet import allowances as alw

    chain = agent["chain"]
    wallet = _assistant(agent)
    owner_turn = {"session_id": "chat-1", **in_process_owner_source_context()}
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _allow({"wallet": wallet}, resource, pay_to=PAYEE, agent_id="assistant")
        for index, not_the_owner in enumerate(({"session_id": "phone", OWNER_LOCAL_KEY: False}, {"session_id": "web", HTTP_INGRESS_KEY: True},
                                               {**owner_turn, TURN_AUTHOR_KEY: "agent"}, {"session_id": "bare"})):
            resource.asked_at = resource.url + f"?r={index}"
            _tool(resource.asked_at, not_the_owner)
            assert resource.paid_requests == [] and chain.token_balance(wallet["address"]) == FUNDED, not_the_owner

        resource.asked_at = resource.url + "?ok=1"
        paid = _tool(resource.asked_at, owner_turn)
        assert paid.status == "paid" and PAID_BODY.decode() in paid.response_text and "allowance" in paid.response_text, paid.response_text
        assert len(resource.paid_requests) == 1 and chain.token_balance(PAYEE) == AMOUNT

        resource.asked_at, resource.pay_to = resource.url + "?ok=2", THIEF
        asked = _tool(resource.asked_at, owner_turn)
        assert asked.status == "pending_approval" and "different address" in asked.response_text and "Nothing was paid" in asked.response_text
        assert chain.token_balance(THIEF) == 0 and len(resource.paid_requests) == 1
        assert [row["reason"] for row in alw.decisions(agent_id="assistant")] == [alw.REASON_RECIPIENT_CHANGED, alw.REASON_WITHIN]


def test_without_an_assistant_wallet_the_tool_does_what_it_always_did(agent):
    from core.request_trust import in_process_owner_source_context

    chain = agent["chain"]
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        result = _tool(resource.url, {"session_id": "chat-1", **in_process_owner_source_context()})
        assert result.status != "paid" and resource.paid_requests == []
        assert chain.token_balance(agent["wallet"]["address"]) == FUNDED, "another agent's wallet is never the assistant's"


def test_the_reply_says_when_a_payment_used_half_or_all_of_the_allowance_and_where_to_give_more(agent):
    from core.request_trust import in_process_owner_source_context
    from core.wallet import allowances as alw

    chain = agent["chain"]
    wallet = _assistant(agent)
    owner_turn = {"session_id": "chat-1", **in_process_owner_source_context()}
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        _allow({"wallet": wallet}, resource, pay_to=PAYEE, agent_id="assistant", window_minor=AMOUNT * 2, total_minor=AMOUNT * 8,
               services=(alw.ServiceRule(resource.url, AMOUNT * 8, PAYEE),))
        resource.asked_at = resource.url + "?n=1"
        first = _tool(resource.asked_at, owner_turn)
        assert first.status == "paid" and "This used half of the spending allowance for this period." in first.response_text, first.response_text
        assert first.response_text.index("This used half") < first.response_text.index("It returned:")
        resource.asked_at = resource.url + "?n=2"
        second = _tool(resource.asked_at, owner_turn)
        assert second.status == "paid" and "This used all of the spending allowance for this period." in second.response_text, second.response_text
        resource.asked_at = resource.url + "?n=3"
        third = _tool(resource.asked_at, owner_turn)
        assert third.status == "pending_approval" and "Nothing was paid" in third.response_text and "used up" in third.response_text
        assert "change the allowance in Settings, under Crypto, Agent spending" in third.response_text
        assert len(resource.paid_requests) == 2 and chain.token_balance(PAYEE) == AMOUNT * 2


class _Team:
    """What the team's gate binds a helper's session to: the team's own coordinator (only its id is read here)."""

    team_id = "team-abc123"

    def gate_write(self, agent_id, abs_path):
        return False, "outside the agent's claim"

    def run_token(self, agent_id):
        return ""


@pytest.fixture
def helper_session():
    from core.agent_team import gate

    gate.bind_session("helper-session", _Team(), "researcher-1")
    try:
        yield {"session_id": "helper-session", "_owner_local": True, "_http_ingress": True, "turn_author": "agent"}
    finally:
        gate.unbind_session("helper-session")


def test_a_helper_agent_pays_nothing_until_the_owner_opens_the_allowance_to_helpers_and_then_only_its_slice(agent, helper_session):
    from core.agent_team import gate
    from core.request_trust import in_process_owner_source_context
    from core.wallet import agent_pay
    from core.wallet import allowances as alw
    from core.wallet.security import WalletFault

    chain = agent["chain"]
    wallet = _assistant(agent)
    owner_turn = {"session_id": "chat-1", **in_process_owner_source_context()}
    assert agent_pay.agent_for_turn(helper_session) == "team:team-abc123:researcher-1"
    assert agent_pay.agent_for_turn({**helper_session, "session_id": "some-other-session"}) == "", "an agent-written turn no team bound is nobody's"
    assert agent_pay.agent_for_turn({**helper_session, "_owner_local": False}) == "", "a remote turn is nobody's, bound or not"
    with Site(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        parent = _allow({"wallet": wallet}, resource, pay_to=PAYEE, agent_id="assistant", window_minor=AMOUNT * 4, total_minor=AMOUNT * 4)
        ask = {"resource_url": resource.url + "?h=0"}

        # helpers are off by default: the team's fence refuses the call, and the tool itself parks nothing anywhere
        assert "agents may not run `x402.propose`" in gate.check_tool("x402.propose", ask, helper_session)
        resource.asked_at = ask["resource_url"]
        refused = _tool(resource.asked_at, helper_session)
        assert refused.status == "not_paid" and "Nothing was paid and nothing was requested" in refused.response_text, refused.response_text
        from core.wallet import proposals

        assert proposals.list_proposals() == [] and resource.requests == [] and chain.token_balance(PAYEE) == 0
        assert chain.token_balance(agent["owner"]["address"]) == 0 and alw.list_allowances(agent_id="team:team-abc123:researcher-1") == []

        # opening it is the owner's act; wider than the allowance is refused
        with pytest.raises(WalletFault):
            alw.set_helper_terms(parent, per_payment_minor=AMOUNT, total_minor=AMOUNT)
        with pytest.raises(WalletFault):
            alw.set_helper_terms(parent, per_payment_minor=AMOUNT, total_minor=AMOUNT * 40, credential=PIN)
        alw.set_helper_terms(parent, per_payment_minor=AMOUNT, total_minor=AMOUNT, credential=PIN)
        assert gate.check_tool("x402.propose", ask, helper_session) == ""
        # only the plain paid fetch is opened: a hand-written offer, or any other tool, stays behind the fence
        assert gate.check_tool("x402.propose", {**ask, "body": {"accepts": []}}, helper_session) != ""
        assert gate.check_tool("wallet.propose", {"destination": THIEF, "amount": "1"}, helper_session) != ""

        resource.asked_at = resource.url + "?h=1"
        paid = _tool(resource.asked_at, helper_session)
        assert paid.status == "paid" and "inside the spending allowance" in paid.response_text, paid.response_text
        assert len(resource.paid_requests) == 1 and chain.token_balance(PAYEE) == AMOUNT

        # the slice is one payment's worth: the helper's second request waits for the owner, the assistant's own still pays
        resource.asked_at = resource.url + "?h=2"
        over = _tool(resource.asked_at, helper_session)
        assert over.status == "pending_approval" and "Nothing was paid" in over.response_text, over.response_text
        assert len(resource.paid_requests) == 1
        resource.asked_at = resource.url + "?a=1"
        mine = _tool(resource.asked_at, owner_turn)
        assert mine.status == "paid" and len(resource.paid_requests) == 2

        # what the helper spent counts against the assistant's allowance too
        view = alw.allowance_view(parent)
        assert (view["total_used_minor"], view["payments"]) == (AMOUNT * 2, 2)
        helper = alw.list_allowances(agent_id="team:team-abc123:researcher-1")[0]
        assert (helper["parent_id"], helper["total_minor"], helper["total_used_minor"], helper["per_payment_minor"]) == (parent, AMOUNT, AMOUNT, AMOUNT)

        # turning helpers off needs no credential, ends the slice and closes the fence again
        alw.set_helper_terms(parent, per_payment_minor=0, total_minor=0)
        assert alw.list_allowances(agent_id="team:team-abc123:researcher-1") == []
        assert gate.check_tool("x402.propose", ask, helper_session) != ""
        assert chain.token_balance(agent["owner"]["address"]) == 0, "the owner's own wallet never paid"
