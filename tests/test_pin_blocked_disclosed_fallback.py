"""A blocked pin discloses a substitution instead of dead-ending the chat.

Measured 2026-11-03 (build 8af687ca, two served chats): the operator pinned models whose
provider lane was excluded by policy (``selected_provider_excluded_before_invocation``). Every
ask returned the same dead end — "pick another model from the selector, or switch it to Auto" —
across a dozen consecutive turns, with zero answers served. The pin contract bans a SILENT
substitution; it does not require the runtime to answer nothing forever. These tests pin the
disclosure contract: the substituted answer names the pinned model, the cause and the answering
model, in that order, before the answer text; the blocked terminal stays the truth when no
fallback can serve.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.memory_first_router import _pin_disclosed_fallback_notice


def test_notice_names_model_cause_and_answerer_in_order() -> None:
    notice = _pin_disclosed_fallback_notice(
        "z-ai/glm-5.3-flash",
        "selected_provider_excluded_before_invocation",
        SimpleNamespace(model_name="nvidia/nemotron-3.5-lightning:free"),
    )
    assert "`z-ai/glm-5.3-flash` could not run this turn:" in notice
    assert "its provider lane is not available right now" in notice
    assert "`nvidia/nemotron-3.5-lightning:free`" in notice
    assert "did not substitute silently" in notice
    assert notice.index("could not run") < notice.index("provider lane") < notice.index("nemotron")


def test_notice_spend_cap_cause_is_plain_language() -> None:
    notice = _pin_disclosed_fallback_notice(
        "openai/gpt-4.1-mini",
        "daily_call_cap_exceeded",
        SimpleNamespace(model_name="ollama-local:qwen3:8b"),
    )
    assert "daily paid-call cap" in notice
    # Never a raw internal code on the wire.
    assert "daily_call_cap_exceeded" not in notice


def test_notice_without_answerer_still_discloses() -> None:
    notice = _pin_disclosed_fallback_notice(
        "z-ai/glm-5.3-flash",
        "not_owner_local",
        SimpleNamespace(model_name=""),
    )
    assert "`z-ai/glm-5.3-flash` could not run this turn:" in notice
    assert "paid models can only run from your own local session" in notice
    assert "did not substitute silently" in notice


def test_notice_degrades_to_plain_phrase_for_unknown_code() -> None:
    notice = _pin_disclosed_fallback_notice(
        "some/model", "totally_unknown_code_xyz", SimpleNamespace(model_name="m")
    )
    assert "could not be started this turn" in notice
    assert "totally_unknown_code_xyz" not in notice


def test_notice_blank_model_falls_back_to_generic_name() -> None:
    notice = _pin_disclosed_fallback_notice("", "not_owner_local", SimpleNamespace(model_name="m"))
    assert "The model you selected could not run this turn:" in notice
