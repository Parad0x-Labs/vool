"""The verifier pick must prefer a model that is ALREADY in memory.

Live defect (2026-08-14, RAM-starved box, sessions ed890df3/cceda886/026e147c): the verifier
ranking scored by parameter size/role/speed with no residency term, so after primary qwen3:14b
answered (resident, ~5.2 GB free) it picked qwen3:8b -- whose ~6.7 GB load the resource governor's
model-load gate refused every time ("model_load_gated_low_memory ... refusing to swap-freeze the
machine", retryable:false) -- and the review reported independent_failed. Meanwhile qwen3:4b was
resident from the SAME turn's classifier call and would have verified with zero new RAM. Which
model "failed" per chat simply tracked which happened to be resident.

Residency is measured by the CALLER (best-effort, empty on any error) and threaded in as data, so
this ranking stays fully deterministic: no probe here, and with no residency signal the ranking is
byte-identical to what it was before -- the fail-soft negative control below.
"""

from __future__ import annotations

from core.local_inference_autopilot import _select_verifier_capability
from core.provider_routing import ProviderCapabilityTruth


def _cap(model_id: str, *, tokens_per_second: float = 20.0, role_fit: str = "general") -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}",
        model_id=model_id,
        role_fit=role_fit,
        context_window=8192,
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=tokens_per_second,
        ram_budget_gb=4.0,
        vram_budget_gb=4.0,
        quantization="q4",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
    )


PRIMARY = _cap("qwen3:14b")
MID = _cap("qwen3:8b", tokens_per_second=35.0)   # size-ranked favourite without residency
TINY = _cap("qwen3:4b", tokens_per_second=40.0)


def test_the_recorded_defect_scenario_picks_the_resident_tiny_model() -> None:
    """Worst case from the ledger: RAM-starved, only the primary and the tiny model resident.
    The unloadable size-favourite must lose to the model already in memory."""
    pick = _select_verifier_capability(
        (PRIMARY, MID, TINY),
        selected=PRIMARY,
        required=True,
        resident_model_tags=("qwen3:14b", "qwen3:4b"),
    )
    assert pick is not None and pick.model_id == "qwen3:4b", pick


def test_no_residency_signal_preserves_the_existing_ranking() -> None:
    """Fail-soft negative control: the caller's probe failing (empty/None) must leave the ranking
    exactly as it was before residency awareness existed -- the mid model wins on size."""
    for resident in (None, (), []):
        pick = _select_verifier_capability(
            (PRIMARY, MID, TINY), selected=PRIMARY, required=True, resident_model_tags=resident,
        )
        assert pick is not None and pick.model_id == "qwen3:8b", (resident, pick)


def test_everything_resident_also_preserves_the_relative_ranking() -> None:
    """The bonus is relative: when every candidate is resident nothing distinguishes them by
    residency, so the pre-existing size ranking decides again."""
    pick = _select_verifier_capability(
        (PRIMARY, MID, TINY),
        selected=PRIMARY,
        required=True,
        resident_model_tags=("qwen3:14b", "qwen3:8b", "qwen3:4b"),
    )
    assert pick is not None and pick.model_id == "qwen3:8b", pick


def test_the_primary_never_verifies_itself_even_when_it_is_the_only_resident() -> None:
    """Residency must never override independence: with ONLY the primary resident, the pick is a
    distinct model (the normal ranking), never the primary."""
    pick = _select_verifier_capability(
        (PRIMARY, MID, TINY), selected=PRIMARY, required=True, resident_model_tags=("qwen3:14b",),
    )
    assert pick is not None and pick.model_id != "qwen3:14b", pick
    assert pick.model_id == "qwen3:8b", pick


def test_resident_matching_is_case_and_whitespace_insensitive() -> None:
    # Malformed/sloppy tags from the probe (Ollama reports tags verbatim) must still match.
    pick = _select_verifier_capability(
        (PRIMARY, MID, TINY), selected=PRIMARY, required=True, resident_model_tags=("  QWEN3:4B  ", ""),
    )
    assert pick is not None and pick.model_id == "qwen3:4b", pick


def test_not_required_still_returns_none() -> None:
    assert (
        _select_verifier_capability(
            (PRIMARY, MID, TINY), selected=PRIMARY, required=False, resident_model_tags=("qwen3:4b",),
        )
        is None
    )
