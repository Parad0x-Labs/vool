"""The Skills & plugins wallet: separate from the owner's and the assistant's wallets, one ticked allowance per plugin,
each with its own per-payment, per-period amount and per-period count, on the real wallet store and custody."""
from __future__ import annotations

import pytest

from core.wallet import addon_wallet
from core.wallet import allowances as alw
from core.wallet.security import WalletFault
from tests.wallet._rig import DESTINATION, DEVNET, wallet_env

PIN = "730461"
SERVICE = "https://api.weather.example"
UNDECLARED = "https://other.example"
NOW = 1_800_000_000.0
PLUGIN = addon_wallet.addon_agent_id("plugin", "weather-pro")
OTHER_PLUGIN = addon_wallet.addon_agent_id("plugin", "maps")


@pytest.fixture
def owner(wallet_env):
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


@pytest.fixture
def declared(monkeypatch):
    listed = [{"kind": "plugin", "id": "weather-pro", "name": "Weather Pro", "services": [SERVICE + "/v1"], "skills": ["forecast"]},
              {"kind": "plugin", "id": "maps", "name": "Maps", "services": ["https://maps.example"]}]
    monkeypatch.setattr(addon_wallet, "_SOURCES", [lambda: listed])
    return listed


def _wallet(agent: str):
    from core.wallet import agent_wallets, custody

    created = agent_wallets.create_agent_wallet(agent, network=DEVNET, credential=PIN)
    revealed = agent_wallets.reveal_backup(created["wallet_id"], credential=PIN)
    agent_wallets.acknowledge_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    return custody.require_wallet(created["wallet_id"])


def _spec(wallet, *, agent=PLUGIN, **over):
    base = dict(agent_id=agent, wallet_id=wallet.wallet_id, network=DEVNET, asset="SOL", per_payment_minor=1_000, window_seconds=alw.DAY, window_minor=5_000,
                total_minor=20_000, max_payments=50, min_interval_seconds=0, expires_at=NOW + 7 * alw.DAY, services=(alw.ServiceRule(SERVICE, 20_000, DESTINATION),),
                window_payments=2)
    base.update(over)
    return alw.AllowanceSpec(**base)


def _ask(wallet, *, agent=PLUGIN, proposal="pay-1", amount=500, digest="", now=NOW):
    from core.wallet import limits
    from core.wallet.store import connection

    with connection() as conn:
        limits._begin_immediate(conn)
        return alw.authorize(conn, agent_id=agent, proposal_id=proposal, wallet_id=wallet.wallet_id, network=DEVNET, asset="SOL", amount_minor=amount, origin=SERVICE,
                             pay_to=DESTINATION, request_digest=digest or f"req-{proposal}", now=now)


def _paid(wallet, proposal, **kw):
    decision = _ask(wallet, proposal=proposal, **kw)
    assert decision.pay, decision
    alw.settle(proposal, amount_moved=True)
    alw.record_delivery(proposal, delivered=True)


def test_the_addons_wallet_carries_only_plugin_allowances_and_no_other_wallet_carries_them(owner):
    addons = _wallet(addon_wallet.ADDONS)
    assistant = _wallet("assistant")
    # the assistant's own allowance never rides the Skills & plugins wallet
    with pytest.raises(WalletFault) as refused:
        alw.create_allowance(_spec(addons, agent="assistant"), credential=PIN, now=NOW)
    assert refused.value.context["reason"] == "allowance_needs_the_agents_own_wallet"
    # a plugin's allowance never rides the assistant's wallet, nor the owner's own wallet
    for wallet in (assistant, owner):
        with pytest.raises(WalletFault):
            alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    assert alw.list_allowances() == []
    created = alw.create_allowance(_spec(addons), credential=PIN, now=NOW)
    assert [v["agent_id"] for v in alw.list_allowances()] == [PLUGIN] and created


def test_each_plugin_spends_only_through_its_own_counters(owner):
    addons = _wallet(addon_wallet.ADDONS)
    alw.create_allowance(_spec(addons), credential=PIN, now=NOW)
    alw.create_allowance(_spec(addons, agent=OTHER_PLUGIN), credential=PIN, now=NOW)
    _paid(addons, "a-1")
    _paid(addons, "a-2", now=NOW + 10)
    # the per-period count is used up for this plugin only
    third = _ask(addons, proposal="a-3", now=NOW + 20)
    assert not third.pay and third.reason == alw.REASON_WINDOW_COUNT_USED
    assert _ask(addons, agent=OTHER_PLUGIN, proposal="b-1", now=NOW + 20).pay
    # a new period gives the count back
    assert _ask(addons, proposal="a-4", now=NOW + alw.DAY + 30).pay
    # a plugin nobody ticked pays nothing
    assert _ask(addons, agent=addon_wallet.addon_agent_id("plugin", "stranger"), proposal="c-1").reason == alw.REASON_NO_ALLOWANCE


def test_per_payment_and_per_period_amounts_hold_for_a_plugin(owner):
    addons = _wallet(addon_wallet.ADDONS)
    alw.create_allowance(_spec(addons, window_payments=0, window_minor=1_500), credential=PIN, now=NOW)
    assert _ask(addons, proposal="big", amount=1_001).reason == alw.REASON_OVER_PER_PAYMENT
    _paid(addons, "p-1", amount=1_000)
    assert _ask(addons, proposal="p-2", amount=600, now=NOW + 5).reason == alw.REASON_WINDOW_USED


def test_raising_the_count_per_period_needs_the_credential_and_lowering_does_not(owner):
    addons = _wallet(addon_wallet.ADDONS)
    allowance_id = alw.create_allowance(_spec(addons), credential=PIN, now=NOW)
    with pytest.raises(WalletFault):
        alw.change_allowance(allowance_id, _spec(addons, window_payments=5), credential="", now=NOW)
    with pytest.raises(WalletFault):  # 0 means no count per period: that loosens too
        alw.change_allowance(allowance_id, _spec(addons, window_payments=0), credential="", now=NOW)
    alw.change_allowance(allowance_id, _spec(addons, window_payments=1), credential="", now=NOW)
    assert alw.allowance_view(allowance_id, now=NOW)["window_payments"] == 1
    with pytest.raises(WalletFault):  # more per period than in all is not a bounded allowance
        alw.create_allowance(_spec(addons, agent=OTHER_PLUGIN, window_payments=60), credential=PIN, now=NOW)


def test_settings_allow_only_a_declared_plugin_and_only_its_declared_services(owner, declared):
    from core.wallet import agent_settings

    _wallet(addon_wallet.ADDONS)
    with pytest.raises(WalletFault) as refused:
        agent_settings.save_addon({"addon_id": addon_wallet.addon_agent_id("plugin", "not-installed"), "asset": "SOL", "per_payment": "0.000001",
                                   "period_amount": "0.00001", "period_payments": "3", "credential": PIN}, now=NOW)
    assert refused.value.context["reason"] == "unknown_addon"
    saved = agent_settings.save_addon({"addon_id": PLUGIN, "asset": "SOL", "per_payment": "0.000001", "period_amount": "0.00001", "period_payments": "3",
                                       "credential": PIN}, now=NOW)["allowance"]
    assert [s["address"] for s in saved["services"]] == [SERVICE]
    view = agent_settings.overview(now=NOW)["addons"]
    by_id = {p["addon_id"]: p for p in view["plugins"]}
    assert by_id[PLUGIN]["allowed"] is True and by_id[PLUGIN]["allowance"]["period_payments"] == 3
    assert by_id[OTHER_PLUGIN]["allowed"] is False and by_id[PLUGIN]["skills"] == ["forecast"]
    # unticking ends it with no credential
    agent_settings.set_state({"allowance_id": saved["allowance_id"], "action": "end"})
    assert agent_settings.overview(now=NOW)["addons"]["plugins"][1]["allowed"] is False


def test_ticking_a_plugin_is_the_owners_act(owner, declared):
    from core.wallet import agent_settings

    _wallet(addon_wallet.ADDONS)
    for wrong in ("", "000000"):
        with pytest.raises(WalletFault):
            agent_settings.save_addon({"addon_id": PLUGIN, "asset": "SOL", "per_payment": "0.000001", "period_amount": "0.00001", "credential": wrong}, now=NOW)
    assert alw.list_allowances() == []


def test_a_plugin_fetch_pays_nothing_outside_the_owners_turn_or_its_declared_services(owner, declared):
    from core.request_trust import OWNER_LOCAL_KEY

    addons = _wallet(addon_wallet.ADDONS)
    alw.create_allowance(_spec(addons), credential=PIN)
    remote = {"surface": "phone"}
    assert addon_wallet.fetch_for_addon(PLUGIN, SERVICE + "/v1/today", source_context=remote)["reason"] == "not_the_owners_turn"
    owner_turn = {OWNER_LOCAL_KEY: True}
    assert addon_wallet.fetch_for_addon(PLUGIN, UNDECLARED + "/x", source_context=owner_turn)["reason"] in ("not_a_declared_service", "not_the_owners_turn")
    assert addon_wallet.fetch_for_addon("assistant", SERVICE, source_context=owner_turn)["reason"] == "not_a_plugin"
