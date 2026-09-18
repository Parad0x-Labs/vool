"""A turn that rules out taking an action does not get to write a skill to disk.

`maybe_handle_skill_request` is called at `turn_frontdoor.py:1284` -- twenty-one lines ABOVE the
`if action_forbidden:` mute that every other mutation-capable front-door lane sits behind
(`:1359` web0_builder, `:1504` machine_download, `:1516` machine_write, `:1539` image_generation).
Four of the five were guarded. This one was the holdout, and it consulted no policy at all:
`grep -c action_policy core/agent_runtime/fast_paths_skill.py` was 0, as were `operating_mode`,
`action_forbidden` and `decide_tool_call`.

Measured on the shipped runtime 2026-08-17, `action_policy=FORBIDDEN` stamped by the runtime itself
at `apps/vool_agent.py:409` and confirmed live at the `turn_dispatch.py:349` call site:

    "Do not create any files. Install the skill qa-echo."
        -> skill.install ran, `action_policy` never read

Both write surfaces were reachable. `skill.create` stages under
`~/Desktop/Vool-skills-plugins/staged-skills/<slug>/SKILL.md`; `skill.install`
(`core/skill_tools.py:285-286`) copies into `plugins/<plugin>/skills/<slug>/SKILL.md` -- the
ACTIVATED tree. That second one is the sharper half: a skill written there is LOADABLE, so the lane
could change what the runtime does next, past an explicit refusal, outside the workspace and outside
`VOOL_HOME`. `core/skill_tools.py:28-30` states the separation it broke: staging is "deliberately
not under the plugins tree ... `create` is not an authorisation to change behaviour."

SCOPE, stated so this is not mistaken for more than it is. The guard binds to the OPERATION, not to
the turn: `list` and `validate` are reads and stay available, because withdrawing them too would be a
denial applied wider than the sentence that justified it -- the defect class this repository has been
removing all week. And it closes exactly one hole:

    CLOSES        action_policy=FORBIDDEN  ->  skill.create / skill.install do not write
    DOES NOT      denials `is_opted_out` cannot see ("never write", "avoid writing", "do not save")

So "Never write anything to disk. Create a skill called X" still writes after this change (in a
mode whose matrix permits the write -- since the fast-path gate-parity repair the lane's dispatch
also crosses the ONE authorized execution boundary, so a modeless or Manual-mode turn is now
prompt-gated there as well; the sentence-level denial below is still invisible to every gate).
That is defect B, tracked separately, and this test deliberately pins that boundary rather than
hiding it.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_skill import maybe_handle_skill_request, skill_request
from core.agent_runtime.intent_claims import ActionPolicy, turn_action_constraints


class _Agent:
    """The only surface the lane uses. Records what it was asked to answer with."""

    def __init__(self) -> None:
        self.results: list[dict[str, object]] = []

    def _fast_path_result(self, **kwargs: object) -> dict[str, object]:
        self.results.append(kwargs)
        return dict(kwargs)


def _forbidden_context() -> dict[str, object]:
    """The context shape `apps/vool_agent.py:409` stamps for a no-action turn."""
    return {"surface": "api", "action_policy": ActionPolicy.FORBIDDEN.value}


def _auto_context(**extra: object) -> dict[str, object]:
    """An Auto-mode context: the matrix allows the create; the boundary then dispatches."""
    return {"surface": "api", "operating_mode": "auto", **extra}


# Verbatim from the measured defect. Each names a WRITE action and forbids action in the same turn.
FORBIDDEN_WRITES = {
    "install": "Do not create any files. Install the skill qa-echo.",
    "create": "Do not create any files. Create a skill called qa-echo that echoes input.",
}

# Reads. A turn may forbid action and still ask what exists -- withdrawing these would be the
# scope defect, not a fix for it.
FORBIDDEN_READS = {
    "list": "Do not create any files. List my skills.",
    "validate": "Do not create any files. Validate the skill qa-echo.",
}


@pytest.mark.parametrize("action", sorted(FORBIDDEN_WRITES))
def test_a_forbidden_turn_does_not_reach_a_skill_write(action: str) -> None:
    text = FORBIDDEN_WRITES[action]
    assert turn_action_constraints(text).policy is ActionPolicy.FORBIDDEN, (
        "fixture drift: this turn must be FORBIDDEN for the test to mean anything"
    )
    assert (skill_request(text) or {}).get("action") == action

    agent = _Agent()
    reply = maybe_handle_skill_request(
        agent, text, session_id="s", source_context=_forbidden_context()
    )
    assert reply is not None, "the lane must answer, not fall through to another writer"
    assert reply["reason"] == f"skill_{action}_action_forbidden"
    assert "did not run" in str(reply["response"])


@pytest.mark.parametrize("action", sorted(FORBIDDEN_READS))
def test_a_forbidden_turn_may_still_read_skills(action: str) -> None:
    """The guard binds to the operation. A read is not a mutation and keeps working."""
    text = FORBIDDEN_READS[action]
    agent = _Agent()
    reply = maybe_handle_skill_request(
        agent, text, session_id="s", source_context=_forbidden_context()
    )
    if reply is None:
        return  # the recogniser did not classify it as a skill turn at all; not this guard's doing
    assert reply["reason"] != f"skill_{action}_action_forbidden", (
        f"{action} is a READ and must not be refused by the write guard"
    )


def test_an_ordinary_turn_still_authors_a_skill() -> None:
    """Positive control: with no restriction the lane must still reach its tool."""
    text = "Create a skill called qa-echo that echoes input."
    assert turn_action_constraints(text).policy is not ActionPolicy.FORBIDDEN

    calls: list[str] = []

    def _spy(tool_name: str, arguments: object, **kwargs: object) -> None:
        calls.append(tool_name)
        return None  # "this build has no such tool" -- enough to prove we got past the guard

    import core.runtime_execution_tools as ret

    original = ret.execute_runtime_tool
    ret.execute_runtime_tool = _spy  # type: ignore[assignment]
    try:
        maybe_handle_skill_request(
            _Agent(), text, session_id="s", source_context=_auto_context()
        )
    finally:
        ret.execute_runtime_tool = original  # type: ignore[assignment]

    assert calls == ["skill.create"], f"the unrestricted path must still dispatch, got {calls}"


def test_removing_the_guard_lets_a_forbidden_turn_write_again() -> None:
    """Anti-vacuity: with the policy check neutralised, the write dispatches again.

    This is the arm that names the cause. If `action_policy_from_context` stops reporting FORBIDDEN,
    the lane reaches the skill.install dispatch exactly as it did before the repair. The narrow
    internal-authority scope below only settles the MODE question (install classifies
    create_files + change_settings, which prompts in every ordinary mode) so the arm still tests
    the LANE guard, not the boundary.
    """
    text = FORBIDDEN_WRITES["install"]
    calls: list[str] = []

    def _spy(tool_name: str, arguments: object, **kwargs: object) -> None:
        calls.append(tool_name)
        return None

    from core.mode_permission_policy import (
        PermissionAction,
        grant_internal_authority,
    )

    token = grant_internal_authority(
        label="test-install-scope",
        actions=[PermissionAction.CREATE_FILES, PermissionAction.CHANGE_SETTINGS],
        intents=["skill.install"],
        session_id="s",
        duration_seconds=300,
    )

    import core.agent_runtime.intent_claims as ic
    import core.runtime_execution_tools as ret

    real_policy = ic.action_policy_from_context
    real_tool = ret.execute_runtime_tool
    ic.action_policy_from_context = lambda *_a, **_k: ActionPolicy.ALLOWED  # type: ignore[assignment]
    ret.execute_runtime_tool = _spy  # type: ignore[assignment]
    try:
        maybe_handle_skill_request(
            _Agent(), text, session_id="s",
            source_context=_auto_context(session_id="s", internal_authority_token=token),
        )
    finally:
        ic.action_policy_from_context = real_policy  # type: ignore[assignment]
        ret.execute_runtime_tool = real_tool  # type: ignore[assignment]

    assert calls == ["skill.install"], (
        "SABOTAGE DID NOT BITE: with the policy check neutralised the forbidden turn must reach "
        f"skill.install again, so the policy check is what stops it. Got {calls}"
    )

    # And the guard is genuinely back on afterwards.
    reply = maybe_handle_skill_request(
        _Agent(), text, session_id="s", source_context=_forbidden_context()
    )
    assert reply is not None and reply["reason"] == "skill_install_action_forbidden"


def test_the_boundary_this_repair_does_not_close() -> None:
    """Pin the scope honestly: a denial `is_opted_out` cannot see still writes.

    Recorded so nobody reads this repair as "the skill lane is fixed". "Never write anything to
    disk" is invisible to BOTH `is_opted_out` and `turn_action_constraints`, so the turn is still
    ALLOWED and still reaches the tool. That is defect B, not this one.
    """
    text = "Never write anything to disk. Create a skill called qa-echo."
    assert turn_action_constraints(text).policy is not ActionPolicy.FORBIDDEN, (
        "if this now reads as FORBIDDEN, defect B was fixed and this boundary test is stale"
    )

    calls: list[str] = []

    def _spy(tool_name: str, arguments: object, **kwargs: object) -> None:
        calls.append(tool_name)
        return None

    import core.runtime_execution_tools as ret

    original = ret.execute_runtime_tool
    ret.execute_runtime_tool = _spy  # type: ignore[assignment]
    try:
        maybe_handle_skill_request(
            _Agent(), text, session_id="s", source_context=_auto_context()
        )
    finally:
        ret.execute_runtime_tool = original  # type: ignore[assignment]

    assert calls == ["skill.create"], (
        "boundary drift: this turn is expected to STILL reach the write at this SHA, because its "
        f"denial phrasing is invisible to both gates. Got {calls}"
    )
