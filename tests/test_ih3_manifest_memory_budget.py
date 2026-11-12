from core import context_retrieval as retrieval
from core.context_capsule_v2 import resolve_budget


def _manifest_budget(context_window: int, transcript_tokens: int = 0, output_tokens: int = 2048):
    return resolve_budget(
        bucket="B",
        role="general",
        context_window_tokens=context_window,
        transcript_tokens=transcript_tokens,
        output_reserve_tokens=output_tokens,
        pin_ceiling_frac=0.0,
        recent_floor_frac=0.0,
        evidence_target_tokens=31000,
        retrieval_ceiling_tokens=31000,
    )


def test_small_local_context_gets_a_smaller_budget_than_32k():
    small = _manifest_budget(4096, transcript_tokens=200)
    local_32k = _manifest_budget(32768, transcript_tokens=4000)

    assert small.num_ctx == 4096
    assert small.evidence_target_tokens == 1294
    assert local_32k.num_ctx == 32768
    assert local_32k.evidence_target_tokens == 21098
    assert small.evidence_target_tokens < local_32k.evidence_target_tokens <= 31000
    assert local_32k.pack_target_tokens <= local_32k.usable_tokens
    assert local_32k.usable_tokens + local_32k.output_reserve_tokens < local_32k.num_ctx


def test_large_cloud_context_can_use_the_product_ceiling():
    cloud = _manifest_budget(131072, transcript_tokens=8000, output_tokens=4096)

    assert cloud.num_ctx == 131072
    assert cloud.evidence_target_tokens == cloud.free_tokens == 31000


def test_unknown_invalid_tiny_and_transcript_exhausted_windows_fail_closed():
    unknown = _manifest_budget(0)
    invalid = resolve_budget(bucket="B", role="general", context_window_tokens=True)
    tiny = _manifest_budget(1024, output_tokens=2048)
    oversized_transcript = _manifest_budget(32768, transcript_tokens=40000)
    invalid_text = resolve_budget(bucket="B", role="general", context_window_tokens="131072")

    assert unknown.num_ctx == 0 and unknown.free_tokens == unknown.evidence_target_tokens == 0
    assert invalid.num_ctx == 0 and invalid.free_tokens == invalid.evidence_target_tokens == 0
    assert invalid_text.num_ctx == 0 and invalid_text.free_tokens == invalid_text.evidence_target_tokens == 0
    assert tiny.free_tokens == tiny.evidence_target_tokens == 0
    assert oversized_transcript.free_tokens == oversized_transcript.evidence_target_tokens == 0


def test_candidate_and_whole_turn_caps_match_the_product_setting():
    assert retrieval._V2_MAX_INJECT_ITEMS_CEILING == 96
    assert retrieval._TURN_LANE_MAX_UNITS == 36
    assert retrieval._TURN_LANE_MAX_TOKENS == 13500
    assert retrieval._allowance_item_limit(31000) == 96
