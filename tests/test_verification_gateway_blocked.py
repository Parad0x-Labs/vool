"""A provider's edge refusing VOOL's request is not a verdict on the key.

Some providers sit behind an edge that answers 403 with an explicit "browser signature banned"
body (error 1010) before the provider ever looks at the key. A plain 403 is still the provider's
own "not permitted"; the explicit edge refusal is ``gateway_blocked``: inconclusive, so it never
withdraws a working key or reports the key as rejected.
"""
from __future__ import annotations

import json

from tests._credential_intelligence_support import descriptor_for

EDGE_DENIAL = json.dumps({"cloudflare_error": True, "error_code": 1010,
                          "error_name": "browser_signature_banned"}).encode()


def _classify(status: int, body: bytes):
    from core.credential_intelligence.verification import classify_verification_response

    return classify_verification_response(descriptor_for(None, "edgeprov"), status=status, body=body,
                                          host="prov.example")


def test_explicit_edge_denial_is_gateway_blocked_and_inconclusive():
    from core.credential_intelligence.verification import INCONCLUSIVE_STATUSES, STATUS_GATEWAY_BLOCKED

    outcome = _classify(403, EDGE_DENIAL)
    assert outcome.status == STATUS_GATEWAY_BLOCKED
    assert outcome.status in INCONCLUSIVE_STATUSES
    assert "not established" in outcome.detail


def test_a_plain_403_is_still_the_providers_own_refusal_without_claiming_validity():
    from core.credential_intelligence.verification import STATUS_UNAUTHORIZED

    outcome = _classify(403, b'{"error": {"code": "forbidden"}}')
    assert outcome.status == STATUS_UNAUTHORIZED
    assert "valid" not in outcome.detail.replace("validity", "")


def test_an_edge_denial_never_withdraws_a_working_lane(monkeypatch):
    from core.credential_intelligence.availability import apply_verification

    withdrawn = []
    monkeypatch.setattr("core.runtime_provider_defaults.deactivate_provider_byok",
                        lambda pid: withdrawn.append(pid) or 1)
    monkeypatch.setattr("core.runtime_provider_defaults.activate_provider_byok", lambda pid, env=None: "x")
    apply_verification(descriptor_for(None, "edgeprov"), _classify(403, EDGE_DENIAL))
    assert withdrawn == []
