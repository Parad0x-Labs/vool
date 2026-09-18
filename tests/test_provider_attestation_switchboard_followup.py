"""SWITCHBOARD follow-up on provider-response model attestation (tests/test_provider_model_
attestation.py): two confirmed findings against the adapter-level work.

F1 -- STREAM ASSEMBLY DROPS ATTESTATION. adapters/openai_compatible_adapter.py's streaming paths
already capture `provider_attested_model` correctly per-chunk (sourced only from the provider's own
response frames). But `core.memory_first_router.MemoryFirstRouter._stream_response` -- the seam
that joins those chunks into one final `ModelResponse` -- read `chunk.usage` into `stream_usage`
and forwarded it, yet never read `chunk.provider_attested_model` at all, so a genuinely
provider-attested identity was silently dropped back to `None` at exactly the point the whole
contract was supposed to survive to. Fixed by mirroring the existing `stream_usage` capture
pattern: take the last truthy `provider_attested_model` seen across the chunk stream, forward it to
the assembled `ModelResponse`, never derive it from `requested_model`/`resolved_model`/
`manifest.model_name`/`actual_model`.

F2 -- CLOUD NORMALIZER ANTI-FABRICATION TEST. `core.normalized_provider_result.
normalize_cloud_model_response` already sets `provider_attested_model=None` unconditionally (System
B / CloudProviderAdapter carries no response-side model identity of its own), but SWITCHBOARD's own
mutation test -- swapping that `None` for `resolved_model`, a specific and plausible fabrication --
survived every existing test with no failure. Closed with a direct, named regression proving the
correct behavior even when every OTHER identity field is populated.

F3 (recorded, NOT implemented here): the adapter's streaming capture is "first value wins, never
overwritten" -- if a real provider ever changed model identity mid-stream, the assembled result
would keep the FIRST value, not the true final one. Out of scope for this narrow round per the
operator's explicit instruction not to redesign this without being required by the smallest safe
repair; this file adds no reconciliation logic for it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.cloud_provider_contract import CloudModelResponse
from core.memory_first_router import MemoryFirstRouter
from core.normalized_provider_result import normalize_cloud_model_response

# --- F1: stream assembly propagates provider_attested_model -------------------------------------


def _manifest(model_name: str = "vendor/selected-model") -> SimpleNamespace:
    return SimpleNamespace(
        provider_id=f"fixture:{model_name}", model_name=model_name, metadata={"confidence_baseline": 0.7}
    )


def _request() -> SimpleNamespace:
    return SimpleNamespace(output_mode="plain_text")


class _ChunkStreamAdapter:
    def __init__(self, chunks, *, raise_after: int | None = None):
        self._chunks = chunks
        self._raise_after = raise_after

    def stream_text_task(self, request):
        for index, chunk in enumerate(self._chunks):
            if self._raise_after is not None and index == self._raise_after:
                raise RuntimeError("stream_aborted")
            yield chunk


def _chunk(delta_text="", *, provider_attested_model=None, usage=None):
    return SimpleNamespace(delta_text=delta_text, raw_event=None, usage=usage, done=False, provider_attested_model=provider_attested_model)


def _run_stream(chunks, *, raise_after=None, manifest=None):
    return MemoryFirstRouter._stream_response(
        SimpleNamespace(),
        adapter=_ChunkStreamAdapter(chunks, raise_after=raise_after),
        manifest=manifest or _manifest(),
        request=_request(),
        source_context=None,
    )


def test_case_a_all_chunks_omit_identity_assembles_none() -> None:
    chunks = [_chunk("Hello"), _chunk(" there", usage={"prompt_tokens": 1, "completion_tokens": 2})]
    response = _run_stream(chunks)
    assert response.provider_attested_model is None


def test_case_b_initial_chunk_provides_identity_assembles_same_identity() -> None:
    chunks = [
        _chunk("Hello", provider_attested_model="vendor/actually-served-model"),
        _chunk(" there", provider_attested_model="vendor/actually-served-model", usage={"prompt_tokens": 1, "completion_tokens": 2}),
    ]
    response = _run_stream(chunks)
    assert response.provider_attested_model == "vendor/actually-served-model"


def test_case_c_later_chunk_first_provides_identity_assembles_same_identity() -> None:
    """A chunk sequence where identity only appears partway through -- the router's own assembly
    loop must not stop reading after the first chunk; it must still pick up an identity that
    arrives later."""
    chunks = [
        _chunk("Hello", provider_attested_model=None),
        _chunk(" there", provider_attested_model="vendor/actually-served-model"),
        _chunk("!", provider_attested_model="vendor/actually-served-model", usage={"prompt_tokens": 1, "completion_tokens": 2}),
    ]
    response = _run_stream(chunks)
    assert response.provider_attested_model == "vendor/actually-served-model"


def test_case_d_contradiction_between_selected_and_attested_is_preserved_verbatim() -> None:
    chunks = [_chunk("Hi", provider_attested_model="vendor/a-completely-different-model")]
    response = _run_stream(chunks, manifest=_manifest("vendor/selected-model"))
    assert response.model_name == "vendor/selected-model"
    assert response.provider_attested_model == "vendor/a-completely-different-model"
    assert response.model_name != response.provider_attested_model


def test_case_e_stream_aborts_before_identity_appears_no_fabricated_value() -> None:
    """The stream fails partway through, before any chunk carried identity. The call must raise --
    not silently return a ModelResponse with a fabricated (or even a correctly-None) attestation,
    since no response was ever actually assembled."""
    chunks = [_chunk("partial"), _chunk("more")]
    with pytest.raises(RuntimeError, match="stream_aborted"):
        _run_stream(chunks, raise_after=1)


# --- F2: normalize_cloud_model_response never fabricates attestation from resolved_model --------


def test_cloud_normalizer_never_fabricates_attestation_even_with_every_other_field_populated() -> None:
    """System B (CloudProviderAdapter/CloudModelResponse) carries no response-side model identity
    of its own. provider_attested_model must stay None regardless of how fully populated
    requested_model/resolved_model/actual_model are -- SWITCHBOARD's own mutation (swap None for
    resolved_model) survived every existing test before this one was added."""
    response = CloudModelResponse(output_text="ok", usage={"prompt_tokens": 5, "completion_tokens": 3}, tool_calls=())
    normalized = normalize_cloud_model_response(
        response,
        requested_provider="openrouter",
        requested_model="vendor/requested-model",
        resolved_provider="openrouter",
        resolved_model="vendor/resolved-model",
        actual_provider="openrouter",
        actual_model="vendor/resolved-model",
    )
    assert normalized.requested_model == "vendor/requested-model"
    assert normalized.resolved_model == "vendor/resolved-model"
    assert normalized.actual_model == "vendor/resolved-model"
    assert normalized.provider_attested_model is None
