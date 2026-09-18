"""Certify a model for the final-answer author role, the way a deployment would.

`core.final_answer_authorship` refuses an uncertified LOOPBACK model before its adapter is built
(`MemoryFirstRouter._invoke_manifest`). That is the product behaviour, and it is why a test whose
subject is something else -- call accounting, empty-response retries, lane selection -- has to say
out loud that its probe model may author, exactly as an operator would by running the probe.

Writing a completed run through the store's own public writers, at the fingerprint the runtime
reads back under, is the whole of it. Nothing here bypasses the authority; it satisfies it.
"""
from __future__ import annotations

import uuid
from typing import Any

#: The backend identity the fingerprint is taken against. Pinned so a run written here and the
#: status read by the runtime agree without depending on whether an Ollama happens to be listening.
RUNTIME_IDENTITY = {
    "backend_version": "ollama-test-0.0.0",
    "model_digest": "sha256:test-digest",
    "template_hash": "template-test",
    "quantization": "q4_K_M",
}


def certify_for_authorship(manifest: Any, *, state: str = "verified") -> str:
    """Write a completed certification run for this exact model identity. Returns the fingerprint."""

    from core.local_model_tool_certification import (
        certification_applies,
        certification_fingerprint,
        certification_fingerprint_payload,
        certification_status,
    )
    from storage.model_tool_certification_store import (
        begin_certification_run,
        complete_certification_run,
    )

    if not certification_applies(manifest):
        # Outside the probe's reach: the operator's own configuration is the attestation and
        # there is nothing to write.
        return ""
    # Ask the READER for the fingerprint it will look the run up under, rather than recomputing
    # one here. The backend version is part of that fingerprint and is resolved by a live identity
    # read against the endpoint, so a locally computed one agrees with the reader only by luck --
    # measured, it did not, and four ledger-accounting tests counted zero calls because the run
    # they had just written was read back as `stale`.
    status = certification_status(manifest)
    fingerprint = str(
        status.get("current_fingerprint") or status.get("fingerprint") or ""
    )
    payload = dict(
        status.get("current_fingerprint_components")
        or status.get("fingerprint_components")
        or {}
    )
    if not fingerprint:
        payload = certification_fingerprint_payload(
            manifest,
            backend_version=str(RUNTIME_IDENTITY["backend_version"]),
            runtime_identity=dict(RUNTIME_IDENTITY),
        )
        fingerprint = certification_fingerprint(payload)
    run_id = f"tool-cert-{uuid.uuid4().hex}"
    begin_certification_run(
        run_id=run_id,
        fingerprint=fingerprint,
        fingerprint_payload=payload,
        provider_name=manifest.provider_name,
        model_name=manifest.model_name,
        adapter_type=str(manifest.adapter_type or "openai_compatible"),
    )
    complete_certification_run(
        run_id=run_id,
        fingerprint=fingerprint,
        state=state,
        successful=state == "verified",
        stages={"transport_acceptance": {"state": "passed"}},
        evidence={"exchange_count": 4},
        latency_ms=12.0,
    )
    return fingerprint


def pinned_backend_identity():
    """Context manager pinning the backend identity the fingerprint is computed against."""

    from unittest import mock

    return mock.patch(
        "adapters.openai_compatible_adapter.OpenAICompatibleAdapter"
        ".tool_certification_runtime_identity",
        return_value=dict(RUNTIME_IDENTITY),
    )
