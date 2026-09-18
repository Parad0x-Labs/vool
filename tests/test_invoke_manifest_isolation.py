"""Each provider candidate gets its own request metadata, not the caller's dict.

`dataclasses.replace` is shallow. `_invoke_manifest` rebuilt the per-candidate request with it and
did not copy `metadata`, so `call_request.metadata IS request.metadata` — one dict shared by the
caller, by every failover candidate, and by every thread.

That last part is the sharp edge. `_maybe_mux_manifests` fans a turn out across several local models
concurrently and `_local_remote_race_pair` runs a local and a remote lane against each other; all of
those threads reach this seam, and the adapter writes `request.metadata["prompt_budget"]` from
inside each one (`adapters/openai_compatible_adapter.py`). Concurrent writes to one dict, with the
last writer deciding what the turn's telemetry says.

The existing `call_request.metadata.pop("prompt_budget", None)` is the same bug seen from the other
side: it exists to scrub the *previous* candidate's telemetry out of the dict they share. Copying
removes the aliasing rather than cleaning up after it.

This is also a prerequisite: a per-lane output budget has to record what it resolved for THIS
candidate, and there is nowhere safe to put that while the dict is shared.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from adapters.base_adapter import ModelRequest

ROUTER = Path(__file__).resolve().parents[1] / "core" / "memory_first_router.py"


def _request() -> ModelRequest:
    return ModelRequest(
        task_kind="chat",
        prompt="hello",
        output_mode="plain_text",
        metadata={"generation_profile": {"max_output_tokens": 240}},
    )


# --------------------------------------------------------------------------------------
# The hazard itself, so the reason for the copy cannot be refactored away by accident
# --------------------------------------------------------------------------------------


def test_dataclasses_replace_shares_the_metadata_dict() -> None:
    """The language behaviour this fix exists for. If this ever fails, the fix is redundant."""

    original = _request()
    naive = replace(original, model_call_id="call-1", response_id="")
    assert naive.metadata is original.metadata

    naive.metadata["prompt_budget"] = {"status": "fit"}
    assert "prompt_budget" in original.metadata, "the caller's dict was mutated"


def test_an_explicit_copy_isolates_each_candidate() -> None:
    original = _request()
    first = replace(
        original, model_call_id="call-1", response_id="", metadata={**(original.metadata or {})}
    )
    second = replace(
        original, model_call_id="call-2", response_id="", metadata={**(original.metadata or {})}
    )

    assert first.metadata is not original.metadata
    assert second.metadata is not first.metadata

    first.metadata["prompt_budget"] = {"status": "rejected"}
    assert "prompt_budget" not in original.metadata, "caller must not see a candidate's telemetry"
    assert "prompt_budget" not in second.metadata, "one candidate must not see another's"

    # The inherited entries still arrive.
    assert second.metadata["generation_profile"]["max_output_tokens"] == 240


def test_the_copy_survives_a_none_metadata() -> None:
    """`metadata or {}` matters: a request built without metadata must not raise here."""

    bare = ModelRequest(task_kind="chat", prompt="hi", output_mode="plain_text")
    copied = replace(
        bare, model_call_id="c", response_id="", metadata={**(getattr(bare, "metadata", None) or {})}
    )
    assert copied.metadata == {} or copied.metadata is not getattr(bare, "metadata", None)


# --------------------------------------------------------------------------------------
# The seam actually ships the copy
# --------------------------------------------------------------------------------------


def test_the_router_seam_copies_metadata_per_candidate() -> None:
    """Asserted on the source: reaching `_invoke_manifest` needs a provisioned registry, a manifest
    and a live adapter, none of which is what this pins. The property is that the ONE seam every
    candidate funnels through does not alias the caller's dict."""

    # Whitespace-normalised rather than bracket-matched: the seam spans several lines and a
    # non-greedy paren match stops inside `{**(request.metadata or {})}`.
    flat = re.sub(r"\s+", " ", ROUTER.read_text(encoding="utf-8"))
    assert "call_request = replace( request, model_call_id=model_call_id," in flat, (
        "the per-candidate request rebuild has moved or changed shape"
    )
    assert "metadata={**(request.metadata or {})}" in flat, (
        "the per-candidate request must not alias the caller's metadata dict"
    )


def test_every_invoke_manifest_call_site_goes_through_that_seam() -> None:
    """Four entrypoints — failover, verifier, local/remote race, mux — must all inherit the copy.

    If a new call site ever builds its own request instead, it silently reintroduces the sharing.
    """

    source = ROUTER.read_text(encoding="utf-8")
    call_sites = re.findall(r"self\._invoke_manifest\(", source)
    assert len(call_sites) >= 4, f"expected the four known entrypoints, found {len(call_sites)}"
    # Exactly one place rebuilds the request for a candidate.
    assert source.count("call_request = replace(") == 1
