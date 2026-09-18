"""Repair #2A follow-up #3: the trusted encoder must not live in module-visible state.

Record construction, fingerprinting, envelope generation, canonical bytes, and validation
all resolve one encoder that lives inside the closed serialization boundary.  Replacing or
mutating any module-visible serialization attribute must not become self-consistent
authority for a brand-new record.

These tests assert the *object-graph* half of the Phase-0 trust boundary documented in
``core/workspace_authority_v3/base.py``.  They deliberately do not assert immunity to
arbitrary same-process code substitution; the characterization tests at the end of this
module pin where that boundary actually lies so the invariants above are not over-read.
"""

from __future__ import annotations

import contextlib
import re

import pytest

from core.workspace_authority_v3 import base as base_module
from core.workspace_authority_v3.canonical import canonical_bytes
from core.workspace_authority_v3.contracts import (
    KeyFamily,
    KeyGenerationMetadata,
    KeyReference,
    KeyRingMetadata,
    KeyState,
)

# Genuine references captured at import, before any test replaces a module attribute.
TRUE_RECORD_BYTES = base_module.sealed_authority_record_bytes
TRUE_RECORD_DIGEST = base_module.sealed_authority_record_digest
TRUE_SEAL_RECORD = base_module.sealed_authority_record
TRUE_REQUIRE_CLOSED = base_module.require_closed_authority_record

SERIALIZATION_ATTRIBUTES = (
    "encode_value",
    "dormant_envelope_record",
    "sealed_authority_record",
    "sealed_authority_record_bytes",
    "sealed_authority_record_digest",
    "validate_dormant_envelope",
    "canonical_enum_value",
)


def _forging_encoder(_value: object) -> object:
    return "FORGED"


@contextlib.contextmanager
def module_attribute(name: str, value: object):
    missing = object()
    original = getattr(base_module, name, missing)
    setattr(base_module, name, value)
    try:
        yield
    finally:
        if original is missing:
            delattr(base_module, name)
        else:
            setattr(base_module, name, original)


def _new_record() -> KeyGenerationMetadata:
    """Build a brand-new valid record; identical inputs must give identical bytes."""

    return KeyGenerationMetadata(
        KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1, KeyState.ACTIVE, "HMAC-SHA256", 1
    )


def _new_nested_record() -> KeyRingMetadata:
    return KeyRingMetadata((_new_record(),))


@pytest.fixture()
def baseline() -> bytes:
    return TRUE_RECORD_BYTES(_new_record())


# ---------------------------------------------------------------------------
# 1-3. Replacing module-visible serialization state cannot alter a NEW record.
# ---------------------------------------------------------------------------


def test_followup3_replacing_public_encode_value_cannot_alter_a_new_record(baseline: bytes) -> None:
    with module_attribute("encode_value", _forging_encoder):
        fresh = _new_record()
        assert TRUE_RECORD_BYTES(fresh) == baseline
        assert TRUE_RECORD_DIGEST(fresh) == TRUE_RECORD_DIGEST(_new_record())
    assert TRUE_RECORD_BYTES(fresh) == baseline


@pytest.mark.parametrize("attribute", SERIALIZATION_ATTRIBUTES)
def test_followup3_replacing_any_serialization_attribute_cannot_alter_a_new_record(
    attribute: str,
    baseline: bytes,
) -> None:
    with module_attribute(attribute, _forging_encoder):
        fresh = _new_record()
        assert TRUE_RECORD_BYTES(fresh) == baseline
    assert TRUE_RECORD_BYTES(fresh) == baseline


def test_followup3_replacing_every_serialization_attribute_at_once_cannot_alter_a_new_record(
    baseline: bytes,
) -> None:
    with contextlib.ExitStack() as stack:
        for attribute in SERIALIZATION_ATTRIBUTES:
            stack.enter_context(module_attribute(attribute, _forging_encoder))
        fresh = _new_record()
        nested = _new_nested_record()
        assert TRUE_RECORD_BYTES(fresh) == baseline
        assert TRUE_RECORD_BYTES(nested) == TRUE_RECORD_BYTES(_new_nested_record())
    assert TRUE_RECORD_BYTES(fresh) == baseline


def test_followup3_no_module_visible_mutable_serialization_cell_exists() -> None:
    """The trusted encoder must not be reachable through a module-visible container."""

    for name, value in vars(base_module).items():
        if name.startswith("__"):
            continue
        assert not isinstance(value, (dict, list, set)) or name == "__all__", (
            f"base.{name} is a module-visible mutable container"
        )


def test_followup3_injecting_or_mutating_a_module_container_cannot_change_the_encoder(
    baseline: bytes,
) -> None:
    with module_attribute("_SERIALIZATION_CELL", {"encode": _forging_encoder, "seal": _forging_encoder}):
        fresh = _new_record()
        assert TRUE_RECORD_BYTES(fresh) == baseline
    injected: dict[str, object] = {}
    with module_attribute("_ENCODER_CELL", injected):
        injected["encode"] = _forging_encoder
        fresh = _new_record()
        assert TRUE_RECORD_BYTES(fresh) == baseline


@pytest.mark.parametrize(
    ("name", "forged"),
    (
        ("_RESERVED_AUTHORITY_FIELDS", frozenset()),
        ("_ENVELOPE_KEYS", frozenset()),
        ("_SEALED_SERIALIZER_NAMES", frozenset()),
        ("_SUPPORTED_AUTHORITY_RECORDS", frozenset()),
        ("SAFE_IDENTIFIER_RE", re.compile(r".*")),
        ("DIGEST_RE", re.compile(r".*")),
        ("require_identifier", lambda value, field: value),
        ("require_positive", lambda value, field: value),
    ),
)
def test_followup3_module_constants_cannot_alter_a_new_record(
    name: str,
    forged: object,
    baseline: bytes,
) -> None:
    with module_attribute(name, forged):
        fresh = _new_record()
        assert TRUE_RECORD_BYTES(fresh) == baseline
    assert TRUE_RECORD_BYTES(fresh) == baseline


# ---------------------------------------------------------------------------
# 4-5. Existing and new records stay stable across replacement.
# ---------------------------------------------------------------------------


def test_followup3_existing_and_new_records_are_both_stable(baseline: bytes) -> None:
    existing = _new_record()
    assert TRUE_RECORD_BYTES(existing) == baseline
    with module_attribute("encode_value", _forging_encoder):
        assert TRUE_RECORD_BYTES(existing) == baseline
        created_during = _new_record()
        assert TRUE_RECORD_BYTES(created_during) == baseline
    assert TRUE_RECORD_BYTES(existing) == baseline
    assert TRUE_RECORD_BYTES(created_during) == baseline
    assert TRUE_RECORD_BYTES(_new_record()) == baseline


def test_followup3_a_record_built_under_replacement_still_validates_afterwards(
    baseline: bytes,
) -> None:
    with module_attribute("encode_value", _forging_encoder):
        fresh = _new_record()
    assert TRUE_REQUIRE_CLOSED(fresh) is fresh
    assert TRUE_RECORD_BYTES(fresh) == baseline
    assert b"FORGED" not in TRUE_RECORD_BYTES(fresh)


# ---------------------------------------------------------------------------
# 6. Bytes, fingerprint, and envelope all agree with the closed encoder.
# ---------------------------------------------------------------------------


def test_followup3_bytes_fingerprint_and_envelope_agree_under_replacement(baseline: bytes) -> None:
    reference_envelope = canonical_bytes(_new_record().envelope().to_record())
    reference_digest = TRUE_RECORD_DIGEST(_new_record())
    assert reference_envelope == baseline

    with contextlib.ExitStack() as stack:
        for attribute in SERIALIZATION_ATTRIBUTES:
            stack.enter_context(module_attribute(attribute, _forging_encoder))
        fresh = _new_record()
        assert TRUE_RECORD_BYTES(fresh) == baseline
        assert TRUE_RECORD_DIGEST(fresh) == reference_digest
        assert canonical_bytes(fresh.envelope().to_record()) == reference_envelope
        assert canonical_bytes(TRUE_SEAL_RECORD(fresh)) == baseline


def test_followup3_nested_records_use_the_closed_encoder(baseline: bytes) -> None:
    reference = TRUE_RECORD_BYTES(_new_nested_record())
    with module_attribute("encode_value", _forging_encoder):
        nested = _new_nested_record()
        assert TRUE_RECORD_BYTES(nested) == reference
        assert b"FORGED" not in TRUE_RECORD_BYTES(nested)
    assert TRUE_RECORD_BYTES(nested) == reference


# ---------------------------------------------------------------------------
# 7. Normal serialization is unchanged.
# ---------------------------------------------------------------------------


def test_followup3_normal_serialization_is_unchanged(baseline: bytes) -> None:
    record = _new_record()
    assert record.canonical_bytes() == baseline
    assert record.to_record() == TRUE_SEAL_RECORD(record)
    assert record.digest() == TRUE_RECORD_DIGEST(record)
    assert canonical_bytes(record.envelope().to_record()) == baseline
    assert record.to_record()["authority_metadata"] == {
        "execution_authority": False,
        "phase": "DORMANT_PHASE0",
    }
    assert record.to_record()["payload"]["key_id"] == "claim-1"
    assert base_module.encode_value(record) == TRUE_SEAL_RECORD(record)

    nested = _new_nested_record().to_record()["payload"]["generations"][0]
    assert nested["record_type"] == "KeyGenerationMetadata"
    assert nested["payload"]["key_id"] == "claim-1"
    assert nested["payload"]["family"] == "CLAIM_ENROLLMENT"
    assert nested["authority_metadata"] == {
        "execution_authority": False,
        "phase": "DORMANT_PHASE0",
    }
    authority_reference = KeyReference(KeyFamily.CLAIM_ENROLLMENT, "claim-1", 1)
    assert authority_reference.key_id == "claim-1"


def test_followup3_envelope_and_payload_values_remain_final() -> None:
    with pytest.raises(TypeError, match="final authority value"):

        class HostileMetadata(base_module.DormantAuthorityMetadata):
            pass

    with pytest.raises(TypeError, match="final authority value"):

        class HostilePayload(base_module.AuthorityRecordPayload):
            pass

    with pytest.raises(TypeError, match="final authority value"):

        class HostileEnvelope(base_module.AuthorityRecordEnvelope):
            pass

    with pytest.raises(ValueError, match="reserved envelope"):
        base_module.AuthorityRecordPayload((("execution_authority", True),))


# ---------------------------------------------------------------------------
# Follow-up #4 characterization: where the Phase-0 trust boundary actually lies.
#
# These tests document the boundary rather than widen it.  Arbitrary same-process
# code substitution is NOT a pass requirement here: no pure-Python arrangement can
# defend against an actor who can already rewrite trusted execution, and chasing it
# would only move the seam from module -> closure -> stdlib -> interpreter forever.
# ---------------------------------------------------------------------------


def _encoder_closure_cell():
    """Locate the closure cell holding the trusted encoder, if it is still shaped that way."""

    for cell in base_module.dormant_envelope_record.__closure__ or ():
        try:
            content = cell.cell_contents
        except ValueError:  # pragma: no cover - empty cell
            continue
        if callable(content) and getattr(content, "__name__", "") == "encode":
            return cell
    return None


def test_followup4_defended_half_of_the_boundary_is_ordinary_object_graph_manipulation(
    baseline: bytes,
) -> None:
    """Everything Phase-0 actually claims: ordinary manipulation of the object graph."""

    # Public wrapper replacement.
    with module_attribute("encode_value", _forging_encoder):
        assert TRUE_RECORD_BYTES(_new_record()) == baseline

    # Subclassing a sealed record type.
    with pytest.raises(TypeError):

        class HostileSubtype(KeyGenerationMetadata):  # type: ignore[misc]
            pass

    # Frozen-field mutation after construction.
    mutated = _new_record()
    object.__setattr__(mutated, "key_id", "claim-forged")
    with pytest.raises(ValueError, match="changed after sealed construction"):
        TRUE_RECORD_BYTES(mutated)

    # Construction bypass.
    bypassed = object.__new__(KeyGenerationMetadata)
    for name, value in (
        ("family", KeyFamily.CLAIM_ENROLLMENT),
        ("key_id", "claim-1"),
        ("generation", 1),
        ("state", KeyState.ACTIVE),
        ("algorithm", "HMAC-SHA256"),
        ("algorithm_version", 1),
    ):
        object.__setattr__(bypassed, name, value)
    with pytest.raises(TypeError, match="not issued"):
        TRUE_RECORD_BYTES(bypassed)


def test_followup4_same_process_code_substitution_is_outside_the_asserted_invariant(
    baseline: bytes,
) -> None:
    """Characterization: rewriting trusted code in-process defeats the encoder, by design.

    Phase-0 does not claim integrity against this.  The test pins the boundary so the
    invariants above are not mistaken for a stronger guarantee; if a future change ever
    moves this seam, this test fails and the trust-boundary docstring must be revisited.
    """

    cell = _encoder_closure_cell()
    if cell is None:  # pragma: no cover - only if the boundary is reshaped
        pytest.skip("trusted encoder is no longer held in a discoverable closure cell")

    original = cell.cell_contents
    cell.cell_contents = _forging_encoder
    try:
        # Reachable, and it does change the encoding. That is the documented out-of-scope case.
        assert TRUE_RECORD_BYTES(_new_record()) != baseline
    finally:
        cell.cell_contents = original

    # The boundary is restored the moment the substituted code is withdrawn.
    assert TRUE_RECORD_BYTES(_new_record()) == baseline


def test_followup4_phase0_stays_dormant_regardless_of_substitution_pressure() -> None:
    """Whatever an in-process actor rewrites, Phase-0 grants no execution authority."""

    from core.workspace_authority_v3 import EXECUTION_AUTHORITY, PHASE

    assert PHASE is base_module.AuthorityPhase.DORMANT_PHASE0
    assert EXECUTION_AUTHORITY is False
    record = _new_record()
    assert record.to_record()["authority_metadata"]["execution_authority"] is False
    with pytest.raises(TypeError, match="sealed by the dormant authority hard gate"):
        KeyGenerationMetadata.execution_authority = True
