"""The x402 lanes bind the paid retry to the caller's own request.

The v1/v2 lane replays a request by method and URL only, so a request body is refused before anything is sent (it
pays through the pay-kit lane instead), and a v2 offer is bound to the method the caller used: an offer naming another
method is refused, and one naming none never falls back to GET.
"""
from __future__ import annotations

import pytest

from core.wallet.errors import WalletFault
from tests.wallet.test_wallet_x402_v2 import (
    BASE_SEPOLIA,
    PAY_TO,
    USDC_BASE,
    _wire_chain,
    wallet_env,
)

pytestmark = [pytest.mark.safety]


def _method_resource(evm_rig, *, offered_method: str | None):
    from tests.wallet._rig_evm import X402V2Resource

    class MethodResource(X402V2Resource):
        def payment_required(self):
            required = super().payment_required()
            if offered_method is not None:
                required["accepts"][0]["method"] = offered_method
            return required

    return MethodResource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000)


def test_a_request_body_is_refused_by_the_v1_v2_lane_before_anything_is_sent(wallet_env, evm_rig, monkeypatch):
    from core.wallet import proposals
    from core.wallet import x402 as wallet_x402

    with _method_resource(evm_rig, offered_method=None) as resource:
        with pytest.raises(WalletFault) as refused:
            wallet_x402.fetch_paid_resource(resource.url, wallet_id="any", method="POST", body=b'{"prompt": "hello"}')
        assert refused.value.code == "x402_scheme_unavailable"
        assert refused.value.context.get("reason") == "request_body_needs_paykit_lane"
        assert resource.challenges == 0
    assert proposals.list_proposals() == []


def test_a_v2_offer_naming_another_method_is_refused_and_nothing_is_parked(wallet_env, evm_rig, monkeypatch):
    from core.wallet import custody, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, _method_resource(evm_rig, offered_method="PUT") as resource:
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        with pytest.raises(WalletFault) as refused:
            wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id, method="POST")
        assert refused.value.code == "x402_scheme_unavailable"
        assert refused.value.context.get("reason") == "offer_method_differs_from_request"
        assert resource.challenges == 1
    assert proposals.list_proposals() == []


@pytest.mark.parametrize("offered_method", [None, "POST", "post"])
def test_a_v2_offer_is_bound_to_the_callers_method_never_a_silent_get(wallet_env, evm_rig, monkeypatch, offered_method):
    from core.wallet import custody
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, _method_resource(evm_rig, offered_method=offered_method) as resource:
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        outcome = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id, method="POST")
        assert outcome.status == "payment_required" and outcome.proposal_id
        binding = wallet_x402.binding_for_proposal(outcome.proposal_id)
        assert binding is not None
        assert binding["resource_method"] == "POST"
        # the owner approves the request the retry will actually make
        from core.wallet import proposals

        engine = wallet_x402.lifecycle_default_engine()
        challenge = engine._challenge_for(proposals.get_proposal(outcome.proposal_id), custody.get_wallet(profile.wallet_id))
        assert challenge.resource_method == "POST"
