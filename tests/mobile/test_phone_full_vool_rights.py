"""A paired phone the owner gave "computer tools" / "paid services" on the computer uses VOOL in full.

The rights live on the computer and are read live at every tool check; money never runs from chat;
chat apps and unpaired or expired phones keep the old fence.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from types import SimpleNamespace

import pytest

from core import request_trust as rt
from core.mobile_companion import policy
from tests.mobile.test_remote_spend_fence import search_chain_transport as search_chain_transport

# --- the fence itself -----------------------------------------------------------------------------

_REMOTE = {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False}


class _Store:
    def __init__(self, device):
        self.record = device

    def device(self, device_id):
        return dict(self.record) if self.record and device_id == self.record["device_id"] else None


@pytest.fixture
def phone(monkeypatch):
    device = {"device_id": "phone:abc", "status": "active", "kind": "phone", "sealed_protocol": True,
              "rules": {"computer_tools": True, "paid_services": False},
              "right_expiry": {"computer_tools": time.time() + 3600}}
    store = _Store(device)
    import core.mobile_companion.listener as listener
    import core.web.api.mobile_companion_api as api

    monkeypatch.setattr(api, "mobile_companion_service", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(listener, "load_settings", lambda: {"enabled": True})
    return device


def _turn(device_id="phone:abc"):
    return {**_REMOTE, rt.REMOTE_DEVICE_KEY: device_id}


def test_a_granted_phone_runs_computer_tools_but_never_money(phone):
    assert rt.remote_turn_tool_refusal("workspace.list_files", _turn()) == ""
    assert rt.remote_turn_tool_refusal("operator.list_processes", _turn()) == ""
    for money in ("pay.x402", "wallet.propose", "wallet.spend", "x402.propose", "sell.quote",
                  "marketplace.purchase_knowledge", "null.register", "ai.paid_ask", "web0.publish",
                  "vool-browser.checkout.begin", "vool-browser.checkout.handoff", "vool-browser.order.reconcile"):
        assert rt.remote_turn_tool_refusal(money, _turn()) == "owner_local_required", money
        assert "Wallet tab" in rt.remote_turn_refusal_text(money, _turn())
    # computer_tools does not open the owner's paid keys.
    assert rt.remote_turn_tool_refusal("web.search", _turn()) == "owner_local_required"
    assert not rt.turn_may_spend_owner_keys(_turn())
    assert "Look things up online" in rt.remote_turn_refusal_text("web.search", _turn())


def test_changing_vool_itself_and_unknown_tools_stay_the_owners(phone):
    phone["rules"] = {"computer_tools": True, "paid_services": True}
    phone["right_expiry"] = {"computer_tools": time.time() + 3600, "paid_services": time.time() + 3600}
    for owner_only in ("skill.install", "skill.remove", "skill.rollback", "contacts.delete",
                       "repo.push.authorize", "vool-browser.permission.grant", "plugin.unknown_tool",
                       "browser.render", "vool-browser.session.open", "vool-browser.navigate",
                       "operator.command.settings.prefs.list"):
        assert rt.remote_turn_tool_refusal(owner_only, _turn()) == "owner_local_required", owner_only
        assert "only works in VOOL on the computer" in rt.remote_turn_refusal_text(owner_only, _turn())


def test_plugin_mcp_and_unknown_effects_stay_the_owners(phone, monkeypatch):
    from core import tool_registry

    tools = {
        "mcp.pay.send": SimpleNamespace(tool_surface="mcp", side_effect_class="network_send", permission_actions=(), source="mcp:pay"),
        "plugin.x.run": SimpleNamespace(tool_surface="plugin", side_effect_class="read_only", permission_actions=(), source="plugin:x"),
        "odd.tool": SimpleNamespace(tool_surface="odd", side_effect_class="external_unpinned", permission_actions=(), source="builtin"),
    }
    monkeypatch.setattr(tool_registry, "tool_for_intent", lambda name: tools.get(name))
    for name in tools:
        assert rt.remote_turn_tool_refusal(name, _turn()) == "owner_local_required", name


def test_a_phone_run_command_never_gets_the_relaxed_network(monkeypatch, tmp_path):
    from core import runtime_execution_tools as tools

    seen = {}

    class Runner:
        def __init__(self, *_a, network_isolation_mode=None, **_k):
            seen["mode"] = network_isolation_mode
            raise RuntimeError("stop here")

    monkeypatch.setattr(tools, "SandboxRunner", Runner)
    from core.effect_gateway import named_background_effect_scope

    for context, expected in ((_REMOTE, None), ({rt.OWNER_LOCAL_KEY: True}, "heuristic_only")):
        with named_background_effect_scope("test", source_context={}):
            try:
                tools._run_command({"command": "pytest -q", "_trusted_local_only": True}, workspace_root=tmp_path,
                                   source_context=context)
            except RuntimeError:
                pass
        assert seen.pop("mode") == expected, context


def test_a_remote_resume_keeps_no_stored_grant():
    assert {"repo_push_authorization", "authorized_paid_call", "context_grants"} <= set(rt.RESUME_GRANT_KEYS)


def test_keyed_data_and_x_tools_need_paid_services(phone):
    for paid in ("market.collect", "watch.add", "watch.resume", "x.search", "token.flow", "image.generate"):
        assert rt.remote_turn_tool_refusal(paid, _turn()) == "owner_local_required", paid


def test_paid_services_opens_web_lookups_and_never_the_owners_keys(phone):
    # MONEY RULE (security review 2026-10-29, decided by coordinator on sls's delegation): a phone never
    # spends the owner's keys. Web lookups run on free sources for a phone; keyed data tools stay on the computer.
    phone["rules"] = {"computer_tools": False, "paid_services": True}
    phone["right_expiry"] = {"paid_services": time.time() + 3600}
    assert rt.remote_turn_tool_refusal("web.search", _turn()) == ""
    assert rt.remote_turn_tool_refusal("token.research", _turn()) == "owner_local_required"
    assert "paid keys" in rt.remote_turn_refusal_text("token.research", _turn())
    assert not rt.turn_may_spend_owner_keys(_turn())
    assert rt.remote_turn_tool_refusal("workspace.list_files", _turn()) == "owner_local_required"
    assert "Use tools on my computer" in rt.remote_turn_refusal_text("workspace.list_files", _turn())
    assert rt.remote_turn_tool_refusal("pay.x402", _turn()) == "owner_local_required"


@pytest.mark.parametrize("change", [
    {"status": "revoked"}, {"kind": "bot"}, {"sealed_protocol": False},
    {"right_expiry": {"computer_tools": time.time() - 1}}, {"right_expiry": {}},
    {"right_expiry": {"computer_tools": time.time() + 40 * 86400}},
])
def test_the_right_is_read_live_and_fails_closed(phone, change):
    phone.update(change)
    assert rt.remote_turn_tool_refusal("workspace.list_files", _turn()) == "owner_local_required"


def test_phone_access_switched_off_closes_every_right(phone, monkeypatch):
    import core.mobile_companion.listener as listener

    monkeypatch.setattr(listener, "load_settings", lambda: {"enabled": False})
    assert rt.remote_turn_tool_refusal("workspace.list_files", _turn()) == "owner_local_required"


def test_another_device_or_a_chat_app_keeps_the_old_answer(phone):
    assert rt.remote_turn_tool_refusal("workspace.list_files", _turn("phone:other")) == "owner_local_required"
    assert rt.remote_turn_refusal_text("workspace.list_files", _REMOTE) == rt.REMOTE_TURN_REFUSAL_TEXT


def test_the_device_stamp_cannot_come_from_a_request_body():
    assert rt.REMOTE_DEVICE_KEY in rt.RESERVED_TRUST_KEYS
    assert rt.REMOTE_DEVICE_KEY not in rt.strip_reserved_trust_keys({rt.REMOTE_DEVICE_KEY: "phone:abc"})
    context = {rt.OWNER_LOCAL_KEY: False}
    rt.stamp_remote_device(context)
    assert rt.REMOTE_DEVICE_KEY not in context
    with rt.trusted_remote_device("phone:abc"):
        rt.stamp_remote_device(context)
    assert context[rt.REMOTE_DEVICE_KEY] == "phone:abc"
    owner = {rt.OWNER_LOCAL_KEY: True}
    with rt.trusted_remote_device("phone:abc"):
        rt.stamp_remote_device(owner)
    assert rt.REMOTE_DEVICE_KEY not in owner


def test_the_owner_session_is_unchanged():
    assert rt.remote_turn_tool_refusal("pay.x402", {rt.OWNER_LOCAL_KEY: True}) == ""


# --- the rights on the computer ---------------------------------------------------------------------

def test_new_rights_are_off_by_default_strong_and_never_for_chat_apps():
    assert policy.defaults()["computer_tools"] is False and policy.defaults()["paid_services"] is False
    assert {"computer_tools", "paid_services"} <= policy.STRONG
    from core.mobile_companion.bot_runtime import BOT_RIGHTS

    assert not {"computer_tools", "paid_services"} & BOT_RIGHTS


def test_saving_settings_keeps_a_chosen_expiry_and_renews_only_on_switch_on(monkeypatch):
    from core.wallet import caller_binding

    monkeypatch.setattr(caller_binding, "refusal_reason", lambda _headers: "")
    chosen = time.time() + 5 * 86400
    record = {"device_id": "phone:abc", "status": "active", "kind": "phone", "permission_revision": 0,
              "rules": {"computer_tools": True}, "right_expiry": {"computer_tools": chosen}, "notification_state": {"x": 1}}
    saved = {}

    class Store:
        def device(self, _id):
            return dict(record)

        def mark_device(self, _id, **fields):
            saved.update(fields)
            record.update(fields)

        def append_receipt(self, *_a, **_k):
            pass

    service = SimpleNamespace(store=Store())
    policy.update(service, {"device_id": "phone:abc", "rules": {"computer_tools": True, "alerts": True}})
    assert saved["right_expiry"]["computer_tools"] == chosen
    policy.update(service, {"device_id": "phone:abc", "rules": {"computer_tools": False}})
    policy.update(service, {"device_id": "phone:abc", "rules": {"computer_tools": True}})
    assert saved["right_expiry"]["computer_tools"] > time.time() + 29 * 86400


def test_a_usepod_lane_stays_the_owners_even_with_paid_services(phone, monkeypatch):
    from core import model_selection_policy, paid_call_reservation

    monkeypatch.setattr(model_selection_policy, "provider_cost_class", lambda _m: "paid_cloud")
    monkeypatch.setattr(model_selection_policy, "is_verified_free_cloud_manifest", lambda _m: False)

    phone["rules"] = {"paid_services": True}
    phone["right_expiry"] = {"paid_services": time.time() + 3600}
    manifest = SimpleNamespace(provider_id="usepod:x402", model_name="m")
    denial = {}
    assert paid_call_reservation.reserve_owner_pick_paid_call(manifest=manifest, task=None, source_context=_turn(), denial=denial) is None
    assert denial["reason"] == "not_owner_local"


# --- served: a real paired phone, a real daemon ------------------------------------------------------

CALLS = {'workspace.list_files': {'path': '.'}, 'web.search': {'query': 'silver price'},
         'pay.x402': {'resource': 'public service'}}
FAMILY = {'workspace.list_files': 'workspace', 'web.search': 'web', 'pay.x402': 'wallet'}


def _provider_class():
    from tests.mobile.test_phone_model_injection import InjectionProvider

    class ToolProvider(InjectionProvider):
        def __init__(self):
            super().__init__()
            self.emitted = []

        def reply(self, model, body):
            prompt = json.dumps(body)
            matches = re.findall(r'FULL-VOOL-scenario([0-9]+)', prompt)
            tools = {row['function']['name']: row['function'] for row in body.get('tools', [])}
            answered = any(row.get('role') == 'tool' for row in body.get('messages', [])
                           if 'FULL-VOOL' not in json.dumps(row))
            if matches and tools and not answered:
                intent = list(CALLS)[int(matches[-1])]
                name = intent.replace('.', '__')
                if name in tools:
                    properties = tools[name]['parameters'].get('properties', {})
                    args = {k: v for k, v in CALLS[intent].items() if k in properties}
                    self.emitted.append({'intent': intent})
                    return {'content': '', 'tool_calls': [{'id': 'full-' + str(len(self.emitted)), 'type': 'function',
                            'function': {'name': name, 'arguments': args}}]}
                return {'content': '', 'tool_calls': [{'id': 'expand', 'type': 'function',
                        'function': {'name': 'capability__expand_family', 'arguments': {'family': FAMILY[intent]}}}]}
            return super().reply(model, body)

    return ToolProvider


def test_served_phone_rights_open_tools_and_paid_search_but_not_money(tmp_path, search_chain_transport):
    from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, run_in_home
    from tests._served_sufficiency_provider import MODEL_A, SEED
    from tests.mobile.test_phone_chat_delivery import ledger_row
    from tests.mobile.test_phone_model_injection import call, http
    from tests.mobile.test_sealed_transport import pair
    from tests.test_pb01_served_sufficiency_served import _certify

    transport, hits = search_chain_transport
    provider = _provider_class()()
    daemon = ServedDaemon(tmp_path / 'home', env_extra={
        'VOOL_TEST_MODE': '1', 'VOOL_UNATTENDED': '1', 'VOOL_ALWAYS_ON_CATALOG': '1',
        'VOOL_MODEL_LOAD_FLOOR_GB': '0', 'VOOL_WALLET_ENABLED': '1',
        'VOOL_FORCE_OLLAMA_MODEL': MODEL_A, 'VOOL_OLLAMA_MODEL': MODEL_A,
        'OLLAMA_HOST': provider.base_url, 'VOOL_OLLAMA_URL': provider.base_url,
        'VOOL_OLLAMA_CHAT_URL': provider.base_url + '/api/chat',
        'SEARXNG_URL': transport, 'VOOL_WALLET_RPC_URLS': json.dumps({'solana:mainnet': transport}),
    })
    provider.__enter__()
    try:
        daemon.start(timeout=60)
        seed = SEED.replace('"structured_json"]', '"structured_json", "tool_intent", "code_basic", "code_complex"]')
        run_in_home(daemon.home, seed.format(root=REPO_ROOT, base_url=provider.base_url, models=[(MODEL_A, {})]))
        assert _certify(daemon, MODEL_A).get('state') == 'verified'
        base = daemon.base_url
        status, config = http(base, '/api/mobile/settings', {'enabled': True, 'address': '127.0.0.1'})
        assert status == 200, config
        desktop = (base, SimpleNamespace(phone_connection=config), daemon.home)
        plain = pair(desktop, rights={'history': True})
        full = pair(desktop, rights={'history': True, 'computer_tools': True})

        def run(phone, number, words=None):
            start = len(provider.emitted)
            status, prepared = call(*phone, 'chats.prepare')
            assert status == 200, prepared
            ask = ['list the files in my workspace', 'search the web for the silver price', 'pay the public service']
            text = 'Execute the workflow using available tools. FULL-VOOL-scenario' + str(number) + ': ' + (words or ask[number]) + '.'
            status, result = call(*phone, 'chats.send', {'chat_id': prepared['result']['chat_id'], 'message': text,
                                                         'idempotency_key': secrets.token_hex(16)})
            assert status == 200, result
            turn_id = result['result']['turn_id']
            assert [row['intent'] for row in provider.emitted[start:]][:1] == [list(CALLS)[number]], provider.emitted[start:]
            refused = ledger_row(desktop, "SELECT details_json FROM runtime_session_events WHERE event_type IN ('tool_failed','refused') AND details_json LIKE ? ORDER BY rowid DESC LIMIT 1", ('%' + turn_id + '%',))
            ran = ledger_row(desktop, "SELECT event_type FROM runtime_session_events WHERE event_type = 'tool_executed' AND details_json LIKE ? AND details_json LIKE '%\"failed\": false%' LIMIT 1", ('%' + turn_id + '%',))
            return bool(ran), (json.loads(refused['details_json']) if refused else None)

        # No right: the computer tool is refused, and the phone is told which switch opens it. (Asked about
        # the owner's files, such a phone is told before any model runs: test_phone_without_computer_rights.py.
        # Words that name no file still reach the model, whose tool call the fence must refuse.)
        _result, refused = run(plain, 0, 'do step one')
        assert refused and (refused.get('observation') or refused).get('reason', refused.get('status')) == 'owner_local_required'
        # Computer tools on: the same tool runs.
        ran, refused = run(full, 0)
        assert ran and (refused is None or 'owner_local_required' not in json.dumps(refused)), refused
        # ...but paid search is still closed without "paid services", and money is closed for good.
        hits.clear()
        _result, refused = run(full, 1)
        assert refused and 'owner_local_required' in json.dumps(refused) and hits == []
        _result, refused = run(full, 2)
        assert refused and 'owner_local_required' in json.dumps(refused)
        # Paid services on: the keyed search reaches its provider.
        device_id = full[3]['device_id']
        status, changed = http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': device_id,
                               'rules': {'paid_services': True}})
        assert status == 200, changed
        # (Web search itself is switched off in this test daemon, so the search answers "disabled"; what
        # matters is that the phone fence no longer refuses it.)
        _ran, refused = run(full, 1)
        assert refused is None or 'owner_local_required' not in json.dumps(refused), refused
        # Switching computer tools off on the computer applies at once.
        status, changed = http(base, '/api/mobile/settings', {'operation': 'device_rules', 'device_id': device_id,
                               'rules': {'computer_tools': False}})
        assert status == 200, changed
        _result, refused = run(full, 0, 'do step one')
        assert refused and 'owner_local_required' in json.dumps(refused)
    finally:
        (tmp_path / 'provider-calls.json').write_text(json.dumps(provider.calls, indent=2))
        daemon.stop()
        provider.__exit__(None, None, None)
