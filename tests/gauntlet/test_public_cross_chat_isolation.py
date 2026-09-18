"""Two chats through the real `/api/chat`, in ONE child runtime, and neither may see the other.

Same behaviour as the in-process version; only the execution boundary moved. ALPHA and BETA stay in
the SAME child deliberately: two processes share no history, so "B did not see A" would be true of
any implementation, including a broken one. `runtime_bootstraps == 1` is what makes the claim about
the product rather than about `fork`.

Markers are asserted against the PROMPT THE MODEL ACTUALLY RECEIVED, not the reply. A reply can omit
a leaked marker by luck; a prompt that carries it has already leaked.

NOT YET LOAD-BEARING FOR THE TWO HISTORY WRAPPERS -- unchanged by this migration, and not claimed as
solved. Both wrapper mutations were run and both SURVIVED (`core/web/api/service.py` and
`apps/vool_agent.py` `_augment_history_from_session_log`), while blanking the id at the
`dispatch_post` CALL SITE did redden them. The recall control still passes with both wrappers
neutered, so the marker reaches the later prompt by some other path. Closing that is a later task;
this file only moves the existing behaviour onto the hermetic boundary.
"""
from __future__ import annotations

from tests.gauntlet.hermetic.runner import group_evidence

GROUP = "cross_chat"


def _turns() -> dict[str, dict]:
    return {turn["label"]: turn for turn in group_evidence(GROUP)["turns"]}


def _seen(turn: dict) -> str:
    return str(turn["prompt_seen_by_model"]) + str(turn["reply"])


def test_neither_chat_ever_receives_the_other_chats_history() -> None:
    evidence = group_evidence(GROUP)
    alpha, beta = evidence["alpha_marker"], evidence["beta_marker"]
    by_label = _turns()
    for label in ("B1", "B2"):
        assert alpha not in _seen(by_label[label]), f"{label} received chat A's marker\n{by_label[label]}"
    for label in ("A1", "A2", "A3"):
        assert beta not in _seen(by_label[label]), f"{label} received chat B's marker\n{by_label[label]}"


def test_each_chat_still_keeps_its_own_session() -> None:
    """Isolation that also loses a chat's OWN session is not isolation."""
    evidence = group_evidence(GROUP)
    assert evidence["alpha_session"] and evidence["beta_session"]
    assert evidence["alpha_session"] != evidence["beta_session"]


def test_a_recall_turn_really_does_consult_stored_history() -> None:
    """Guards the guard: without this, deleting history entirely would pass every test above."""
    evidence = group_evidence(GROUP)
    later = _turns()["A2"]
    assert evidence["alpha_marker"] in str(later["prompt_seen_by_model"]), (
        "a chat's own history never reached its own prompt, so the isolation tests above prove "
        f"nothing\n{later}"
    )


def test_both_chats_shared_one_child_runtime() -> None:
    """The condition under which the isolation claim is about the product at all."""
    evidence = group_evidence(GROUP)
    assert evidence["runtime_bootstraps"] == 1, evidence["runtime_bootstraps"]
