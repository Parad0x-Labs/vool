"""Honest model naming guard.

The local-LLM community's defining 2026 grievance against Ollama was deceptive model
naming (`deepseek-r1` pulling an 8B distill presented as the 671B flagship). VOOL ships
the same Ollama distill tags, so it must never surface them to a user as the flagship.
This test locks that: every deepseek-r1 tag is marked as a distill and carries an honest
display name, and the honest label never reads as the bare flagship.
"""
from __future__ import annotations

from core.local_model_bundles import (
    MODEL_METADATA,
    honest_model_label,
    model_is_distill,
)

_DISTILL_TAGS = ("deepseek-r1:8b", "deepseek-r1:14b", "deepseek-r1:32b")


def test_deepseek_r1_tags_are_marked_as_distills() -> None:
    for tag in _DISTILL_TAGS:
        assert model_is_distill(tag), f"{tag} must be flagged distilled=True"
        meta = MODEL_METADATA[tag]
        assert meta.get("base_model") == "DeepSeek-R1"
        assert "distill" in str(meta.get("honest_note", "")).lower()
        assert "671" in str(meta.get("honest_note", "")), "note must disclaim the 671B flagship"


def test_honest_label_reads_as_distill_not_flagship() -> None:
    for tag in _DISTILL_TAGS:
        label = honest_model_label(tag)
        assert "Distill" in label, f"{tag} honest label {label!r} must say 'Distill'"
        # Must never present as the bare flagship name.
        assert label.lower() not in {"deepseek-r1", "deepseek r1"}
    assert honest_model_label("deepseek-r1:14b") == "DeepSeek-R1-Distill-Qwen-14B"
    assert honest_model_label("deepseek-r1:8b") == "DeepSeek-R1-Distill-Llama-8B"


def test_non_distill_models_are_not_flagged_and_fall_back_to_tag() -> None:
    assert model_is_distill("qwen2.5:7b") is False
    assert model_is_distill("qwen3:8b") is False
    # No honest display name defined -> label is the raw tag (still accurate).
    assert honest_model_label("qwen2.5:7b") == "qwen2.5:7b"
    assert honest_model_label("unknown:1b") == "unknown:1b"


def test_every_deepseek_r1_family_entry_is_honestly_labeled() -> None:
    # Guard against a future deepseek-r1 tag being added without honest labeling.
    for tag, meta in MODEL_METADATA.items():
        if str(meta.get("family")) == "deepseek-r1":
            assert meta.get("distilled") is True, f"{tag} (deepseek-r1 family) must be distilled=True"
            assert "distill" in str(meta.get("display_name", "")).lower(), (
                f"{tag} needs a display_name that says Distill"
            )
