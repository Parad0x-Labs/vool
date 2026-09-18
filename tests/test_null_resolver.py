from __future__ import annotations

import base64
import unittest
from unittest import mock

from core import null_resolver
from core.null_resolver import (
    NULL_DOMAIN_SIZE,
    decode_null_domain,
    derive_domain_pda,
    domain_filters,
    find_program_address,
    is_valid_x402_endpoint,
    pad_name64,
    resolve_null_domain,
    resolve_x402_endpoint,
)
from core.vool_wallet import b58decode, b58encode


def _blob(name="web0", owner=b"\x01" * 32, arweave=b"\x02" * 32,
          endpoint="https://parad0xlabs.com/x402", passport=b"\x00" * 32) -> bytes:
    raw = bytearray(NULL_DOMAIN_SIZE)
    raw[0] = 0x4E  # 'N'
    raw[1:1 + len(name.encode())] = name.encode()
    raw[65:97] = owner
    raw[97:129] = arweave
    ep = endpoint.encode()
    raw[129:129 + len(ep)] = ep
    raw[257:289] = passport
    return bytes(raw)


class DecodeTests(unittest.TestCase):
    def test_decodes_all_fields(self) -> None:
        rec = decode_null_domain(_blob())
        self.assertIsNotNone(rec)
        self.assertEqual(rec.name, "web0")
        self.assertEqual(rec.owner, b58encode(b"\x01" * 32))
        self.assertEqual(rec.arweave_txid, base64.urlsafe_b64encode(b"\x02" * 32).rstrip(b"=").decode())
        self.assertEqual(rec.x402_endpoint, "https://parad0xlabs.com/x402")
        self.assertIsNone(rec.passport_hash)  # all-zero -> None

    def test_passport_hash_when_set(self) -> None:
        rec = decode_null_domain(_blob(passport=b"\xab" * 32))
        self.assertEqual(rec.passport_hash, ("ab" * 32))

    def test_empty_endpoint_is_blank(self) -> None:
        rec = decode_null_domain(_blob(endpoint=""))
        self.assertEqual(rec.x402_endpoint, "")

    def test_rejects_short_blob(self) -> None:
        self.assertIsNone(decode_null_domain(b"\x4e" + b"\x00" * 10))

    def test_rejects_wrong_discriminator(self) -> None:
        bad = bytearray(_blob())
        bad[0] = 0x00
        self.assertIsNone(decode_null_domain(bytes(bad)))


class FilterTests(unittest.TestCase):
    def test_pad_name64(self) -> None:
        self.assertEqual(len(pad_name64("web0")), 64)
        self.assertIsNone(pad_name64("x" * 65))  # overflows the 64-byte field

    def test_domain_filters_shape(self) -> None:
        f = domain_filters("web0")
        # No dataSize filter: it would drop v2 (378-byte) NullDomain accounts. The
        # disc (@0) + full 64-byte name (@1) memcmp pair is already unique.
        self.assertTrue(all("dataSize" not in flt for flt in f))
        self.assertEqual(f[0]["memcmp"]["offset"], 0)
        self.assertEqual(f[1]["memcmp"]["offset"], 1)
        # disc byte 'N' base58-encodes to a non-empty ascii string
        self.assertTrue(isinstance(f[0]["memcmp"]["bytes"], str) and f[0]["memcmp"]["bytes"])

    def test_overflow_name_yields_no_filters(self) -> None:
        self.assertIsNone(domain_filters("x" * 65))


class ResolveGuardTests(unittest.TestCase):
    def test_resolve_x402_endpoint_none_on_overflow(self) -> None:
        # name too long -> filters None -> resolve returns None, no network call
        self.assertIsNone(resolve_x402_endpoint("x" * 65))


class EndpointValidatorTests(unittest.TestCase):
    def test_accepts_https(self) -> None:
        self.assertTrue(is_valid_x402_endpoint("https://parad0xlabs.com/x402"))
        self.assertTrue(is_valid_x402_endpoint("https://pay.web0.null/x402?asset=usdc"))

    def test_accepts_http_localhost_only(self) -> None:
        self.assertTrue(is_valid_x402_endpoint("http://localhost:11435/x402"))
        self.assertTrue(is_valid_x402_endpoint("http://127.0.0.1:11435/x402"))
        # bare http to a remote host is rejected
        self.assertFalse(is_valid_x402_endpoint("http://parad0xlabs.com/x402"))

    def test_rejects_javascript_and_other_schemes(self) -> None:
        self.assertFalse(is_valid_x402_endpoint("javascript:alert(1)"))
        self.assertFalse(is_valid_x402_endpoint("data:text/html,<script>x</script>"))
        self.assertFalse(is_valid_x402_endpoint("file:///etc/passwd"))
        self.assertFalse(is_valid_x402_endpoint("ftp://parad0xlabs.com/x402"))

    def test_rejects_empty_overlong_and_bad_charset(self) -> None:
        self.assertFalse(is_valid_x402_endpoint(""))
        # over the 128-byte on-chain field width
        self.assertFalse(is_valid_x402_endpoint("https://parad0xlabs.com/" + "a" * 130))
        # spaces / control bytes have no place in a stored URL
        self.assertFalse(is_valid_x402_endpoint("https://parad0xlabs.com/ x402"))
        self.assertFalse(is_valid_x402_endpoint("https://parad0xlabs.com/\nx402"))

    def test_decode_blanks_unsafe_endpoint(self) -> None:
        rec = decode_null_domain(_blob(endpoint="javascript:alert(1)"))
        self.assertEqual(rec.x402_endpoint, "")
        ok = decode_null_domain(_blob(endpoint="https://parad0xlabs.com/x402"))
        self.assertEqual(ok.x402_endpoint, "https://parad0xlabs.com/x402")


class PdaDerivationTests(unittest.TestCase):
    # Golden vectors confirmed LIVE against the deployed registrar (NXgQhepF):
    # getAccountInfo on each PDA returns the matching on-chain NullDomain. These
    # pin the client derivation to the on-chain seed scheme forever.
    # NOTE: "web0" was previously CT2QddD… - the WRONG value from a hand-rolled PDA
    # walk that pointed at an empty account (why web0.null never resolved). The
    # reference derivation (solders/web3.js) gives FJ5kcbF…, which is the real
    # account and the one that actually holds web0's Arweave content.
    GOLDEN = {
        "vool": "5LnTqT68dERqRL7jYvPZBWsbTRrC8sR6hYaXh2q7aJbN",
        "null":  "6LGKrgqdUAo1ErsHpMgZmuhRLYGzjkA7dRvsJtg8fGku",
        "web0":  "FJ5kcbFxU6pEVdUHcpvu6hX8CYfTd4LAvhHdiPcK1FG3",
    }

    def test_derive_matches_onchain_golden_pdas(self) -> None:
        for name, expected in self.GOLDEN.items():
            self.assertEqual(derive_domain_pda(name), expected, name)

    def test_derive_is_deterministic(self) -> None:
        self.assertEqual(derive_domain_pda("vool"), derive_domain_pda("vool"))

    def test_derive_overflow_name_is_none(self) -> None:
        self.assertIsNone(derive_domain_pda("x" * 65))

    def test_find_program_address_is_off_curve(self) -> None:
        import hashlib

        from core.null_resolver import _is_on_curve
        seed_hash = hashlib.sha256(pad_name64("vool")).digest()
        pda, bump = find_program_address(
            [b"null-domain", seed_hash], null_resolver.NULL_REGISTRAR_MAINNET
        )
        self.assertEqual(bump, 255)
        # A real PDA must NOT be a valid ed25519 point.
        self.assertFalse(_is_on_curve(b58decode(pda)))


class ResolveViaAccountInfoTests(unittest.TestCase):
    def _account_info(self, raw: bytes) -> dict:
        return {"context": {"slot": 1}, "value": {"data": [base64.b64encode(raw).decode(), "base64"]}}

    def test_resolves_via_get_account_info_on_pda(self) -> None:
        calls: list = []

        def _fake_rpc(method, params, *, timeout=5.0):
            calls.append((method, params))
            return self._account_info(_blob(name="vool", endpoint=""))

        with mock.patch.object(null_resolver, "_rpc_call", _fake_rpc):
            rec = resolve_null_domain("vool")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.name, "vool")
        # The cheap getAccountInfo path is used — NOT the getProgramAccounts scan.
        self.assertEqual(calls[0][0], "getAccountInfo")
        self.assertEqual(calls[0][1][0], derive_domain_pda("vool"))

    def test_empty_account_value_is_unresolved(self) -> None:
        with mock.patch.object(null_resolver, "_rpc_call", lambda *a, **k: {"value": None}):
            self.assertIsNone(resolve_null_domain("vool"))

    def test_rpc_error_is_unresolved(self) -> None:
        with mock.patch.object(null_resolver, "_rpc_call", lambda *a, **k: None):
            self.assertIsNone(resolve_null_domain("vool"))

    def test_name_mismatch_is_rejected(self) -> None:
        # A PDA whose stored record decodes to a different name is not returned.
        with mock.patch.object(
            null_resolver, "_rpc_call",
            lambda *a, **k: {"value": {"data": [base64.b64encode(_blob(name="other")).decode(), "base64"]}},
        ):
            self.assertIsNone(resolve_null_domain("vool"))

    def test_falls_back_to_scan_when_pda_read_misses(self) -> None:
        # Derivation now goes through solders (independent of the nacl curve check),
        # so the cheap getAccountInfo-on-PDA is always attempted first. When that PDA
        # read misses (here the mock returns None for it), resolution falls back to the
        # getProgramAccounts name-scan instead of giving up - the fix that stopped
        # silently dropping names whose live record isn't at the derived PDA.
        calls: list = []

        def _fake_rpc(method, params, *, timeout=5.0):
            calls.append(method)
            if method == "getProgramAccounts":
                return [{"account": {"data": [base64.b64encode(_blob(name="vool")).decode(), "base64"]}}]
            return None

        with mock.patch.object(null_resolver, "_rpc_call", _fake_rpc):
            rec = resolve_null_domain("vool")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.name, "vool")
        self.assertEqual(calls, ["getAccountInfo", "getProgramAccounts"])


if __name__ == "__main__":
    unittest.main()
