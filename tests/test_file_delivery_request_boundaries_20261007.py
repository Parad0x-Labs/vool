import pytest

from core.agent_runtime.intent_claims import ActionPolicy, turn_action_constraints
from core.deliverables import DeliverableRefused, refuse_unrequested_format, requested_formats
from core.tool_demand_signals import asks_for_a_file


@pytest.mark.parametrize(
    "text", ["can you explain how to make a PDF accessible?", "Explain how to export a CSV from Sheets."]
)
def test_explanation_does_not_order_file(text):
    assert not asks_for_a_file(text)
    assert requested_formats(text) == ()


def test_bundle_must_be_outer_archive():
    wanted = requested_formats("Write a tiny HTML page and a matching CSS file, then zip them together.")
    assert "zip" in wanted
    with pytest.raises(DeliverableRefused, match="request asked"):
        refuse_unrequested_format(filename="index.html", fmt="html", wanted=wanted)


def test_existing_spreadsheet_does_not_erase_output_format():
    assert requested_formats(
        "I need a spreadsheet of my monthly subscriptions with amounts and renewal dates. Excel please."
    ) == ("xlsx",)


def test_explanation_plus_separate_order_is_still_an_order():
    assert asks_for_a_file("Explain how to make a PDF accessible, then create a PDF checklist for me.")


def test_explicit_multiple_outputs_stay_multiple():
    assert requested_formats("Give me a PDF report and a CSV file of the data.") == ("pdf", "csv")


def test_parallel_how_to_clauses_remain_instructions():
    assert not asks_for_a_file("Can you explain how to export a CSV and create an Excel workbook?")


@pytest.mark.parametrize("objects", ["scripts", "apps", "files"])
def test_delivery_with_a_scoped_run_ban(objects):
    text = "Write a tiny HTML page and a matching CSS file, then zip them together. Do not run " + objects + "."
    constraints = turn_action_constraints(text)
    assert constraints.policy is ActionPolicy.ALLOWED
    assert constraints.forbid_commands

@pytest.mark.parametrize("text", [
    "Do not run scripts. Explain the HTML standard.",
    "Write an HTML page. Do not write files.",
    "Write an HTML page. Do not use tools.",
])
def test_actual_whole_turn_bans_stay_forbidden(text):
    assert turn_action_constraints(text).policy is ActionPolicy.FORBIDDEN
