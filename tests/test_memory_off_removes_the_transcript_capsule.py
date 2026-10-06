"""The per-turn memory switch governs the transcript capsule lane.

Measured on the served path on head 9174b42c (q90-routing rig, F15-05
fresh-profile read, 2026-09-29): with ``source_context.memory_prompt_enabled=False``
the semantic capsule block still rode the serialized provider request, so a turn
that had explicitly turned memory off still received stored facts. The assembler
now drops the capsule category when the memory prompt was suppressed by the
caller, by group context, or by an unallowed surface; structured/operational
modes keep it by their own pinned contract. The metadata reports
``suppressed_reason`` so "mode disabled the prompt" and "caller disabled memory"
cannot be conflated again. (Adopted from the old role's e92664b6 after
reproducing its target on this head.)
"""

from __future__ import annotations

from core.prompt_normalizer import _memory_prompt_metadata


def _meta(source_context: dict, *, output_mode: str = "plain_text",
          prompt_profile: str = "default") -> dict:
    return _memory_prompt_metadata(
        source_context=source_context,
        source_platform="api",
        source_surface="api",
        prompt_profile=prompt_profile,
        output_mode=output_mode,
        runtime_session_id="s",
    )


def test_explicit_memory_off_reports_disabled_by_caller() -> None:
    meta = _meta({"memory_prompt_enabled": False})
    assert meta["enabled"] is False
    assert meta["suppressed_reason"] == "disabled_by_caller"


def test_memory_on_by_default_on_direct_surfaces() -> None:
    meta = _meta({})
    assert meta["enabled"] is True
    assert meta["suppressed_reason"] == ""


def test_group_chat_denies_with_reason() -> None:
    meta = _meta({"is_group": True})
    assert meta["enabled"] is False
    assert meta["suppressed_reason"] == "group_chat"


def test_denied_surface_reports_surface_not_allowed() -> None:
    # A platform that is neither group-like nor in the direct allowlist: not a group
    # chat, but not a surface memory may write to either.
    meta = _memory_prompt_metadata(
        source_context={},
        source_platform="patreon",
        source_surface="patreon",
        prompt_profile="default",
        output_mode="plain_text",
        runtime_session_id="s",
    )
    assert meta["enabled"] is False
    assert meta["suppressed_reason"] == "surface_not_allowed"


def test_assembler_drops_capsule_messages_when_memory_is_off() -> None:
    """Source-level pin of the assembler's gate; the served-path proof is the
    q90-routing rig run on the repaired head (fresh profile, --memory-prompt off,
    zero capsule blocks in the serialized request)."""
    import inspect

    import core.prompt_normalizer as pn

    source = inspect.getsource(pn)
    assert 'suppressed_reason") in {"disabled_by_caller", "group_chat", "surface_not_allowed"}' in source
    assert "capsule_messages = []" in source


def test_structured_mode_keeps_the_capsule_by_its_own_contract() -> None:
    # Pinned by test_prompt_assembly_profiles: operational/structured turns still
    # receive the transcript capsule; the mode disables the conversational memory
    # prompt, not the evidence lane, so the assembler's gate does not fire.
    meta = _meta({}, output_mode="tool_intent")
    assert meta["enabled"] is False
    assert meta["suppressed_reason"] == ""
