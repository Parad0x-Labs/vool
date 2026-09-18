"""QA-050-025: an ordinary correction must never write persistent profile state.

Measured on the v0.5.0 smoke run. Asked for a one-sentence description of an app, told it was
wrong, and the follow-up "Change it to a photo editor." was claimed by the VoolBook fast path,
which wrote::

    voolbook_profiles.display_name = 'a photo editor'

Two arms produced that between them, and both are gone:

* `classify_voolbook_intent` returned "rename" for a bare ``chang\\w+ (it|this|that|<any word>) to
  ...``. Nothing in the sentence mentions VoolBook, a profile, a name or a handle -- "it" means
  "the thing we were just talking about", which in a chat is almost never the user's profile.
* `extract_display_name` then took everything after "to" as the new name.

The tests below are behavioural where it matters: they drive the real fast path with the real
identity module patched at the WRITE seam, so a claim that reaches any mutation fails, rather than
asserting on an intent string that a later refactor could route around.

This is the third time this fast path has over-claimed on a substring (`x` inside "tax" wrote a
Twitter handle; `name:` inside pasted YAML triggered a rename), so the negative corpus below is
deliberately wider than the two sentences QA measured.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.voolbook import classify_voolbook_intent, extract_display_name

# Ordinary chat that must never reach a profile write. The first two are QA-050-025's own repro.
_CORRECTIONS_AND_FOLLOW_UPS = (
    "No, that is wrong. Lumen is a photo editor, not a note-taking app. Fix the description.",
    "Change it to a photo editor.",
    "can we change it to a photo editor",
    "change that to celsius",
    "no, that's wrong",
    "that is not what i asked for",
    "change this to something shorter",
    "create calendar note for me on vool and mac calendar, tomorrow 1pm event name: Call mom",
    "Review this YAML configuration: name: worker bio: queues twitter: sample",
    "Create a contact with name: Alex and twitter: alex_example",

    "changed it to dark mode and it looks better",
)


def _agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        peer_id="peer-qa-050-025",
        handle="vool",
        bio="",
        display_name="VOOL",
        twitter_handle="",
        post_count=0,
        claim_count=0,
    )


@pytest.mark.parametrize("turn", _CORRECTIONS_AND_FOLLOW_UPS)
def test_no_correction_or_follow_up_classifies_as_a_profile_edit(turn: str) -> None:
    assert classify_voolbook_intent(" ".join(turn.lower().split())) is None


@pytest.mark.parametrize("turn", _CORRECTIONS_AND_FOLLOW_UPS)
def test_no_correction_or_follow_up_yields_a_display_name(turn: str) -> None:
    """The second half of the defect: even reached, there must be nothing to write."""
    assert extract_display_name(turn) == ""


@pytest.mark.parametrize("turn", _CORRECTIONS_AND_FOLLOW_UPS)
def test_no_correction_or_follow_up_reaches_a_profile_write(turn: str) -> None:
    """The claim that matters: the fast path declines, and nothing mutates.

    Patched at the write seam rather than the intent seam. A future arm that re-reaches the profile
    by some other route fails this test without needing to be predicted here.
    """
    agent = _agent()

    with (
        mock.patch("core.agent_runtime.agent.signer_mod.get_local_peer_id", return_value="peer-qa-050-025"),
        mock.patch("core.voolbook_identity.get_profile", return_value=_profile()),
        mock.patch("core.voolbook_identity.update_profile") as update_profile,
        mock.patch("core.voolbook_identity.rename_handle") as rename_handle,
        mock.patch("core.voolbook_identity.register_voolbook_account") as register,
    ):
        result = agent._maybe_handle_voolbook_fast_path(
            turn,
            session_id="session-qa-050-025",
            source_context={"surface": "openclaw"},
        )

    assert result is None, f"fast path claimed an ordinary turn: {turn!r}"
    update_profile.assert_not_called()
    rename_handle.assert_not_called()
    register.assert_not_called()


def test_a_correction_typed_into_an_open_rename_prompt_is_not_written_as_the_name() -> None:
    """The pending flow consumes the WHOLE next message as the new name.

    That is correct when the reply is a name and catastrophic when it is a correction, and the
    runtime having just asked the question does not make the next sentence an answer to it. The
    correction is declined before the pending branch, so the profile is untouched.
    """
    agent = _agent()
    agent._voolbook_pending["session-qa-050-025-pending"] = {"step": "awaiting_rename"}

    with (
        mock.patch("core.agent_runtime.agent.signer_mod.get_local_peer_id", return_value="peer-qa-050-025"),
        mock.patch("core.voolbook_identity.get_profile", return_value=_profile()),
        mock.patch("core.voolbook_identity.update_profile") as update_profile,
        mock.patch("core.voolbook_identity.rename_handle") as rename_handle,
    ):
        result = agent._maybe_handle_voolbook_fast_path(
            "No, that is wrong. Lumen is a photo editor, not a note-taking app. Fix the description.",
            session_id="session-qa-050-025-pending",
            source_context={"surface": "openclaw"},
        )

    assert result is None
    update_profile.assert_not_called()
    rename_handle.assert_not_called()


# ---------------------------------------------------------------------------------------------
# The positive half: an intentional profile edit still works.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("turn", "expected"),
    [
        ("change my name to lumen", "lumen"),
        ("update my display name to Lumen Studio", "Lumen Studio"),
        ("rename my handle to lumen", "lumen"),
        ("change my voolbook to lumen", "lumen"),
        ("set my name: lumen", "lumen"),
    ],
)
def test_an_explicit_profile_edit_still_parses(turn: str, expected: str) -> None:
    assert classify_voolbook_intent(" ".join(turn.lower().split())) == "rename"
    assert extract_display_name(turn).lower() == expected.lower()


def test_an_explicit_display_name_change_still_writes_the_profile() -> None:
    """The capability is intact: an explicit edit reaches `update_profile` with the right value."""
    agent = _agent()
    profile = _profile()

    with (
        mock.patch("core.agent_runtime.agent.signer_mod.get_local_peer_id", return_value="peer-qa-050-025"),
        mock.patch("core.voolbook_identity.get_profile", return_value=profile),
        mock.patch("core.voolbook_identity.update_profile") as update_profile,
        mock.patch.object(agent, "_sync_profile_to_hive"),
    ):
        result = agent._maybe_handle_voolbook_fast_path(
            "update my display name to Lumen Studio",
            session_id="session-qa-050-025-positive",
            source_context={"surface": "openclaw"},
        )

    assert result is not None
    update_profile.assert_called_once_with(profile.peer_id, display_name="Lumen Studio")
    assert "Lumen Studio" in str(result.get("response") or "")


def test_a_name_answered_into_an_open_rename_prompt_still_writes_the_profile() -> None:
    """The pending flow keeps working for what it is for: a reply that is actually a name."""
    agent = _agent()
    agent._voolbook_pending["session-qa-050-025-answer"] = {"step": "awaiting_rename"}
    profile = _profile()

    with (
        mock.patch("core.agent_runtime.agent.signer_mod.get_local_peer_id", return_value="peer-qa-050-025"),
        mock.patch("core.voolbook_identity.get_profile", return_value=profile),
        mock.patch("core.voolbook_identity.update_profile") as update_profile,
        mock.patch.object(agent, "_sync_profile_to_hive"),
    ):
        result = agent._maybe_handle_voolbook_fast_path(
            "Lumen Studio ✨",
            session_id="session-qa-050-025-answer",
            source_context={"surface": "openclaw"},
        )

    assert result is not None
    update_profile.assert_called_once_with(profile.peer_id, display_name="Lumen Studio ✨")
