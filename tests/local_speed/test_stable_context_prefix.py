"""The stable-prefix split was REVERTED: pinned tests require the identity and continuity
lines to ride the assembled context message (test_prompt_assembly_profiles,
test_memory_qa_prompt_cost). This file records that outcome so the split is not re-attempted
against the same pins; the cache-prefix win it chased is instead delivered by the extraction
quiet-batch (no interleaved calls) and the volatile-block cuts.
"""
