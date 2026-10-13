import copy

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from core.registry.codec import RegistryError, canonical
from core.registry.trust import TrustedCatalog
from registry_service.auth import Provider, oidc_subject, url64
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, signed, trust_fixture


@pytest.mark.parametrize("name,issuer", [("google", "https://accounts.google.com"), ("apple", "https://appleid.apple.com")])
def test_real_oidc_signature_and_issuer_audience_nonce_expiry_binding(name, issuer):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwks = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "fixture",
        "n": url64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")), "e": url64(numbers.e.to_bytes(3, "big"))}]}
    provider = Provider(name, "registered-client", "fixture-secret")
    payload = {"iss": issuer, "aud": provider.client_id, "sub": "stable-subject", "nonce": "expected-nonce",
               "iat": NOW, "exp": NOW + 60, "email": "not-an-account-link-key@example.test"}

    def token(value):
        message = url64(canonical({"alg": "RS256", "kid": "fixture"})) + "." + url64(canonical(value))
        signature = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return message + "." + url64(signature)

    assert oidc_subject(token(payload), jwks, provider, "expected-nonce", now=NOW) == "stable-subject"
    for field, wrong in (("iss", "https://evil.test"), ("aud", "another-client"), ("nonce", "replayed"), ("exp", NOW)):
        with pytest.raises(RegistryError):
            oidc_subject(token({**payload, field: wrong}), jwks, provider, "expected-nonce", now=NOW)
    forged = token(payload).split(".")
    forged[1] = url64(canonical({**payload, "sub": "attacker"}))
    with pytest.raises(RegistryError, match="signature"):
        oidc_subject(".".join(forged), jwks, provider, "expected-nonce", now=NOW)


def test_dual_signed_root_rotation_rejects_untrusted_rotation_and_preserves_revocation(tmp_path):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, old_keys, old_root = trust_fixture()
    _, new_keys, new_root = trust_fixture()
    new = {**new_root["signed"], "version": 2}
    client = TrustedCatalog(old_root, tmp_path / "state", now=NOW)
    first = catalog_fixture(raw, policy, old_keys)
    sha = first["signed"]["entries"][0]["signed"]["artifact_sha256"]
    client.accept(catalog_fixture(raw, policy, old_keys, revoked=[sha]), now=NOW)
    with pytest.raises(RegistryError):
        client.rotate(signed(new, new_keys["root"]), now=NOW)
    combined = dict(old_keys["root"]) | dict(new_keys["root"])
    client.rotate(signed(new, combined), now=NOW)
    snapshot = catalog_fixture(raw, policy, new_keys, root_version=2)
    client.accept(signed(snapshot["signed"], new_keys["catalog"]), now=NOW)
    with pytest.raises(RegistryError, match="revoked"):
        client.resolve("alice/echo", "1.0.0", target=TARGET, now=NOW)
    client.close()
