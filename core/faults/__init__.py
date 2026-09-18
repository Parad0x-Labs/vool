"""The ONE versioned fault plane: catalog, records, mapping, redaction, recorder.

Public surface (consumers import from here, modules may import siblings directly):

* ``core.faults.catalog``   -- the closed, versioned vocabulary and its export;
* ``core.faults.records``   -- the typed, identity-carrying, redacted record;
* ``core.faults.mapping``   -- the one-mapping law (``map_exception``, ``FaultError``);
* ``core.faults.recorder``  -- the durable, fail-open ledger and lifecycle;
* ``core.faults.redaction`` -- the allowlist/mask law for everything a record keeps;
* ``core.faults.bug_export``-- the privacy-safe fault block bug reports attach.
"""
from core.faults.catalog import (
    CATALOG_VERSION,
    FAULT_SCHEMA,
    DuplicateFaultCodeError,
    FaultSpec,
    UnknownFaultCodeError,
    all_codes,
    all_specs,
    catalog_digest,
    export_catalog,
    get_spec,
)
from core.faults.mapping import (
    FaultError,
    fault_code_for_provider_error_class,
    map_exception,
    retry_classification_from_faults,
)
from core.faults.recorder import (
    advance_lifecycle,
    fault_by_id,
    faults_for_turn,
    identity_from_context,
    list_faults,
    record_fault,
    record_for_exception,
)
from core.faults.records import (
    FaultCause,
    FaultRecord,
    MissingFaultIdentityError,
    fault_record_for_code,
)

__all__ = [
    "CATALOG_VERSION",
    "FAULT_SCHEMA",
    "DuplicateFaultCodeError",
    "FaultCause",
    "FaultError",
    "FaultRecord",
    "FaultSpec",
    "MissingFaultIdentityError",
    "UnknownFaultCodeError",
    "advance_lifecycle",
    "all_codes",
    "all_specs",
    "catalog_digest",
    "export_catalog",
    "fault_by_id",
    "fault_code_for_provider_error_class",
    "fault_record_for_code",
    "faults_for_turn",
    "get_spec",
    "identity_from_context",
    "list_faults",
    "map_exception",
    "record_fault",
    "record_for_exception",
    "retry_classification_from_faults",
]
