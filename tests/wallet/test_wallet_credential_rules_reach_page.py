"""The PIN and password rules reach the create form.

The wallet status passes the record redaction on its way out, and the redaction drops every key named
pin or password outright (a PIN under any spelling never leaves custody). The credential policy rows used
those names, so the page never received them and every create form offered "PIN (digits)" and
"Wallet password ()". The rows now carry _rule names; this proves they survive the door the page reads,
with the numbers the custody code enforces.
"""

from __future__ import annotations

import json

import pytest

from tests.wallet.test_crypto_pilot_request import home as request_home

pytestmark = [pytest.mark.safety]

home = request_home


def test_the_served_status_carries_the_pin_and_password_rules(home):
    from core.wallet import custody, pilot_custody
    from core.web.api.wallet_api import handle_wallet_get

    response = handle_wallet_get("/api/wallet/status", {}, client_host="127.0.0.1")
    policy = json.loads(response.body)["status"]["crypto_pilot"]["credential_policy"]
    assert policy["pin_rule"]["min"] == pilot_custody.PIN_MIN and policy["pin_rule"]["max"] == pilot_custody.PIN_MAX
    assert policy["password_rule"]["min"] == custody._PASSWORD_MIN and policy["password_rule"]["max"] == custody._PASSWORD_MAX
    assert "device" in policy


def test_the_create_form_reads_the_rule_rows():
    from core.wallet_fragment import render_wallet_fragment

    page = render_wallet_fragment()
    assert "policy.pin_rule || policy.pin" in page and "policy.password_rule || policy.password" in page
