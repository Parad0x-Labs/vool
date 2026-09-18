"""EAGLE3 draft-exists guard (issue #20, the "do this FIRST" item).

A draft only counts when VOOL_LLAMACPP_DRAFT_MODEL is an ABSOLUTE, existing file. A relative path,
a repo id, or the "prompt-lookup-decoding" sentinel must never flip eagle_status to
active/configured_not_proven with no real GGUF behind it. The old check treated any non-"/" string as
present, which also meant every Windows path (drive-letter, not "/") falsely counted.
"""
from __future__ import annotations

from core.backend_acceleration_truth import backend_acceleration_proof


def _eagle_status(draft_model: str) -> str:
    proof = backend_acceleration_proof(
        backend="llama.cpp",
        model_id="qwen3:8b",
        env={"VOOL_LLAMACPP_SPEC_TYPE": "draft-eagle3", "VOOL_LLAMACPP_DRAFT_MODEL": draft_model},
        probe=False,
    )
    return proof.eagle_status


def test_relative_draft_path_is_not_counted() -> None:
    # A tag mismatch of the guard: "./draft.gguf" / "draft.gguf" must NOT register as a real draft.
    assert _eagle_status("draft.gguf") == "unsupported_by_config"
    assert _eagle_status("./models/draft.gguf") == "unsupported_by_config"


def test_prompt_lookup_sentinel_is_not_counted() -> None:
    # installer's DEFAULT_LLAMACPP_DRAFT_MODEL sentinel must not masquerade as an EAGLE3 draft.
    assert _eagle_status("prompt-lookup-decoding") == "unsupported_by_config"


def test_repo_id_is_not_counted() -> None:
    assert _eagle_status("AngelSlim/Qwen3-8B_eagle3") == "unsupported_by_config"


def test_absent_absolute_path_is_not_counted() -> None:
    # Absolute but non-existent -> isfile() False -> not counted.
    missing = "C:\\vool\\models\\nope.gguf" if __import__("os").name == "nt" else "/vool/models/nope.gguf"
    assert _eagle_status(missing) == "unsupported_by_config"


def test_real_absolute_gguf_is_counted(tmp_path) -> None:
    draft = tmp_path / "Qwen3-8B-speculator.eagle3-F16.gguf"
    draft.write_bytes(b"\x00")  # a real, absolute, existing file
    # With a real draft but no live probe (probe=False -> proved False), status is configured_not_proven,
    # NOT active. That proves the draft is recognised without over-claiming the speculative path.
    assert _eagle_status(str(draft)) == "configured_not_proven"


def test_empty_draft_is_not_counted() -> None:
    # spec_type=draft-eagle3 requested but no draft path -> unsupported_by_config (not active/proven).
    assert _eagle_status("") == "unsupported_by_config"


def test_guard_never_reaches_active_without_a_live_probe(tmp_path) -> None:
    # Even a real absolute draft cannot be "active" without a proven generation (probe=False).
    draft = tmp_path / "draft.gguf"
    draft.write_bytes(b"\x00")
    assert _eagle_status(str(draft)) != "active"
