from __future__ import annotations

import random
from decimal import Decimal, localcontext

import pytest

from core.routing_authority_v2 import (
    CanonicalizationError,
    PicoUSD,
    PicoUSDValidationError,
    canonical_bytes,
    canonical_set,
    parse_strict_json,
    typed_sha256,
)
from core.routing_authority_v2.money import MAX_PICO_USD


@pytest.mark.parametrize(
    "payload",
    [
        '{"a":1,"a":2}',
        '{"e\\u0301":1,"\\u00e9":2}',
    ],
)
def test_duplicate_json_keys_are_rejected_even_after_unicode_normalization(payload: str) -> None:
    with pytest.raises(CanonicalizationError, match="duplicate"):
        parse_strict_json(payload)


@pytest.mark.parametrize("payload", ['{"x":1.0}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'])
def test_json_float_nan_and_infinity_are_rejected(payload: str) -> None:
    with pytest.raises(CanonicalizationError):
        parse_strict_json(payload)


def test_utf8_nfc_and_deterministic_field_order_are_canonical() -> None:
    composed = {"z": "caf\u00e9", "a": "\u00e9"}
    decomposed = {"a": "e\u0301", "z": "cafe\u0301"}
    assert canonical_bytes(composed) == canonical_bytes(decomposed)
    assert canonical_bytes(composed) == b'{"a":"\xc3\xa9","z":"caf\xc3\xa9"}'


def test_mathematical_set_order_canonicalizes_but_array_order_remains_significant() -> None:
    assert canonical_set(("z", "a")) == canonical_set(("a", "z")) == ("a", "z")
    assert canonical_bytes(frozenset(("z", "a"))) == canonical_bytes(frozenset(("a", "z")))
    assert canonical_bytes(("z", "a")) != canonical_bytes(("a", "z"))


def test_typed_hashing_binds_domain_and_raw_blob_length() -> None:
    payload = canonical_bytes({"schema_version": 2, "value": "same"})
    assert typed_sha256("VOOL_TYPE_A_V2", payload) != typed_sha256("VOOL_TYPE_B_V2", payload)
    assert typed_sha256("VOOL_TYPE_A_V2", b"ab") != typed_sha256("VOOL_TYPE_A_V2", b"a\x00b")


@pytest.mark.parametrize(
    "source",
    [None, True, False, -1, -0.1, 0.0, "-1", "-0", "-0.0", "NaN", "Infinity", "1e-12", "USD 1"],
)
def test_money_rejects_missing_bool_float_negative_negative_zero_and_malformed(source: object) -> None:
    with pytest.raises(PicoUSDValidationError):
        PicoUSD.parse(source)


def test_exact_zero_stays_zero_and_positive_sub_pico_rounds_up() -> None:
    assert PicoUSD.parse("0").picos == 0
    assert PicoUSD.parse("0.0000000000001").picos == 1
    assert PicoUSD.parse(Decimal("0.000000000001")).picos == 1


def test_money_conversion_is_independent_of_ambient_decimal_precision_at_legal_maximum() -> None:
    legal_maximum = "340282366920938463463374607.431768211455"
    for precision in (6, 10, 28, 50, 100):
        with localcontext() as context:
            context.prec = precision
            assert PicoUSD.parse(legal_maximum).picos == MAX_PICO_USD
            assert PicoUSD.parse(Decimal(legal_maximum)).picos == MAX_PICO_USD
            assert PicoUSD.parse("0").picos == 0
            assert PicoUSD.parse("0.000000000000000001").picos == 1


def test_money_rejects_first_pico_above_legal_maximum() -> None:
    with pytest.raises(PicoUSDValidationError, match="128-bit"):
        PicoUSD.parse("340282366920938463463374607.431768211456")


def test_every_sampled_exact_pico_amount_survives_all_decimal_contexts() -> None:
    generator = random.Random(0xC057)
    expected_values = [0, 1, 10**12 - 1, 10**12, MAX_PICO_USD]
    expected_values.extend(generator.randrange(0, MAX_PICO_USD + 1) for _index in range(100))
    for expected in expected_values:
        whole, fractional = divmod(expected, 10**12)
        source = f"{whole}.{fractional:012d}"
        for precision in (6, 10, 28, 50, 100):
            with localcontext() as context:
                context.prec = precision
                assert PicoUSD.parse(source).picos == expected


def test_money_rejects_overflow_excess_precision_exponent_and_currency() -> None:
    with pytest.raises(PicoUSDValidationError, match="128-bit"):
        PicoUSD.from_picos(MAX_PICO_USD + 1)
    with pytest.raises(PicoUSDValidationError, match="precision"):
        PicoUSD.parse("0.0000000000000000001")
    with pytest.raises(PicoUSDValidationError, match="exponent"):
        PicoUSD.parse(Decimal("1E+2"))
    with pytest.raises(PicoUSDValidationError, match="currency"):
        PicoUSD.parse("1", currency="EUR")


def test_money_checked_arithmetic_preserves_bounds() -> None:
    assert (PicoUSD.from_picos(2) + PicoUSD.from_picos(3)).picos == 5
    assert (PicoUSD.from_picos(3) - PicoUSD.from_picos(2)).picos == 1
    assert (PicoUSD.from_picos(3) * 4).picos == 12
    with pytest.raises(PicoUSDValidationError):
        _ = PicoUSD.from_picos(0) - PicoUSD.from_picos(1)
    with pytest.raises(PicoUSDValidationError):
        _ = PicoUSD.from_picos(MAX_PICO_USD) + PicoUSD.from_picos(1)
    with pytest.raises(PicoUSDValidationError):
        _ = PicoUSD.from_picos(1) * -1
