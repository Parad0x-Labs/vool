"""Every model is told the date, and no model is told where the folder lives.

Measured 2026-07-29 in the owner's own session, with a cloud model selected in the composer:

    "waht is the date today?"     -> "Today is **January 15, 2025** (Wednesday)."
    "what is the time now_?"      -> "I don't have access to a real-time clock..."

Neither is a model defect. There is no clock among the 58 catalog tools, and nothing in the prompt
path ever stated the time — grep for `utcnow|datetime.now|strftime|current time|timezone` in
`core/prompt_normalizer.py` returned zero before this change. The first model answered from its
training prior; the second told the literal truth. A cloud lane made it visible, but a local model
had exactly as little to go on.

The fix states the clock as a runtime FACT in `_runtime_turn_truth`, which is built during prompt
assembly — above the `explicit_model_owns_semantic_turn` gate that returns early for a pinned model,
so it reaches every lane. It is deliberately not a front-door fast path: the operator picked a model
to answer, so the model answers. VOOL supplies the fact, the model supplies the words.

The workspace fact is the same idea with a hard limit. The system prompt is transmitted to whichever
provider serves the turn, including a cloud one, so an absolute path would carry the account name
and the directory layout off the machine. Two attempts at this were caught by
`tests/test_prompt_assembly_profiles.py`: an absolute path, then a `$HOME`-relative one that only
shortens paths under `$HOME` and would still have sent a workspace on an external volume in full.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core.prompt_normalizer import _runtime_turn_truth

# --------------------------------------------------------------------------------------
# The clock reaches every turn
# --------------------------------------------------------------------------------------


def test_a_bare_turn_is_still_told_the_date() -> None:
    """Unconditional. The two failures were on turns carrying no workspace and no pinned model."""

    truth = _runtime_turn_truth({})

    assert truth, "a turn with no other facts must still carry the clock"
    assert datetime.now().strftime("%Y") in truth
    assert datetime.now().strftime("%B") in truth


def test_the_stated_date_is_today_not_a_training_prior() -> None:
    """The exact failure: a model answered 'January 15, 2025' from memory."""

    truth = _runtime_turn_truth({})
    now = datetime.now().astimezone()

    assert now.strftime("%A") in truth, "weekday missing"
    assert now.strftime("%d %B %Y").lstrip("0") in truth or now.strftime("%d %B %Y") in truth


def test_the_timezone_is_stated_so_a_time_answer_is_unambiguous() -> None:
    truth = _runtime_turn_truth({})
    now = datetime.now().astimezone()

    assert now.strftime("%H:%M") in truth
    assert "UTC offset" in truth
    assert now.strftime("%z") in truth


def test_the_model_is_told_not_to_deny_having_a_clock() -> None:
    """The second failure was a model correctly reporting it had no clock. Now it does."""

    truth = _runtime_turn_truth({})
    assert "never say you cannot access a clock" in truth
    assert "never answer a date or time question from memory" in truth


def test_a_pinned_cloud_model_is_told_the_time_too() -> None:
    """The turns that failed had a cloud model pinned; the fact must not be lane-specific."""

    truth = _runtime_turn_truth(
        {"requested_model": "nvidia/nemotron-3-ultra-550b-a55b:free"}
    )
    assert datetime.now().strftime("%H:%M") in truth
    assert "nvidia/nemotron-3-ultra-550b-a55b:free" in truth


# --------------------------------------------------------------------------------------
# The workspace fact never carries a path
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "workspace",
    [
        "/Users/example/private-project",
        "/Users/someone-else/Desktop/client work",
        "/Volumes/ExternalSSD/secret-client-work",
        "/opt/local/checkout",
        "/private/tmp/scratch",
    ],
)
def test_no_absolute_workspace_path_reaches_the_prompt(workspace: str) -> None:
    """The prompt is transmitted to the answering provider. The layout must not go with it."""

    truth = _runtime_turn_truth(
        {"workspace": workspace, "project_id": "p", "workspace_binding": "project"}
    )

    assert workspace not in truth
    parent = workspace.rsplit("/", 1)[0]
    assert parent not in truth, "even the parent directory discloses layout"


def test_no_account_name_reaches_the_prompt() -> None:
    """An absolute home path carries the account name; that is the concrete disclosure."""

    truth = _runtime_turn_truth(
        {"workspace": "/Users/jdoe/Desktop/openclaw-skills", "project_id": "p",
         "workspace_binding": "project"}
    )
    assert "jdoe" not in truth
    assert "/Users/" not in truth


def test_the_folder_name_is_stated_so_the_question_is_still_answerable() -> None:
    """Withholding the path must not mean withholding the answer the operator asked for."""

    truth = _runtime_turn_truth(
        {"workspace": "/Users/jdoe/Desktop/openclaw-skills", "project_id": "openclaw-skills",
         "workspace_binding": "project"}
    )
    assert "openclaw-skills" in truth
    assert "workspace.identity" in truth, "the model must know how to get the full path if needed"


def test_a_turn_with_no_workspace_states_no_folder() -> None:
    truth = _runtime_turn_truth({})
    assert "active workspace folder" not in truth


def test_a_folder_name_containing_a_space_survives() -> None:
    """The owner's own project is `VOOL WEBSITE`."""

    truth = _runtime_turn_truth(
        {"workspace": "/Users/jdoe/Desktop/VOOL WEBSITE", "project_id": "p",
         "workspace_binding": "project"}
    )
    assert "`VOOL WEBSITE`" in truth
    assert "/Users/jdoe" not in truth


# --------------------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------------------


def test_the_clock_is_stated_before_the_other_facts() -> None:
    """It is the fact most often needed and least often available; it leads."""

    truth = _runtime_turn_truth(
        {"workspace": "/Users/jdoe/x", "project_id": "p", "workspace_binding": "project",
         "requested_model": "some/model"}
    )
    body = truth.split("authoritative): ", 1)[1]
    assert body.startswith("The current date and time is")


def test_the_fact_block_is_one_paragraph_not_a_wall() -> None:
    """It rides in the system prompt on every turn; it must stay cheap."""

    truth = _runtime_turn_truth(
        {"workspace": "/Users/jdoe/x", "project_id": "p", "workspace_binding": "project"}
    )
    assert "\n" not in truth
    assert len(truth) < 1200, f"{len(truth)} chars of runtime truth on every turn is too much"
