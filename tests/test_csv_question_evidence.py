"""Table arithmetic is code-derived evidence, never a raw-file final answer."""
import json
from decimal import Decimal

import pytest

from tests.test_workspace_subject_evidence import enter_turn
from tests.test_workspace_subject_evidence import isolated_home as isolated_home


@pytest.mark.parametrize("heading,dimension,filename,separator", [
    ("outlay", "participant", "tally.csv", ","),
    ("quantity", "station", "tally.txt", ","),
    ("reading", "operator", "tally.CSV", ";"),
    ("weight", "batch", "tally.tsv", "\t"),
])
def test_tabular_questions_receive_exact_totals_and_groups(tmp_path, heading, dimension, filename, separator):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    rows = [[dimension, heading, "memo"], ["group-a", "18.31", "first"],
            ["group-a", "100.03", "second"], ["group-b", "74.09", "third"],
            ["group-b", "37.95", "fourth"]]
    (workspace / filename).write_text("\n".join(separator.join(row) for row in rows) + "\n")
    result, context = enter_turn(
        f"Compute the total in {filename}, per group, and who owes whom if shared equally.", workspace
    )
    assert separator.join(rows[1]) not in str(result.get("response", "")), result
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    tables = [o["computed_table"] for o in context.get("runtime_tool_observations", []) if o.get("computed_table")]
    assert len(tables) == 1, context.get("runtime_tool_observations")
    table = tables[0]
    assert Decimal(table["totals"][heading]) == sum(map(Decimal, ("18.31", "100.03", "74.09", "37.95")))
    assert table["group_totals"][dimension] == {
        "group-a": {heading: "118.34"}, "group-b": {heading: "112.04"},
    }
    assert table["hypothetical_equal_shares"][dimension][heading]["transfers"] == [
        {"from": "group-b", "to": "group-a", "amount": "3.15"}
    ]
    from core.prompt_normalizer import _runtime_tool_observation_message

    message = _runtime_tool_observation_message(context)
    assert message is not None
    rendered = [json.loads(line[2:]) for line in message.content.splitlines() if line.startswith("- {")]
    assert [row["computed_table"] for row in rendered if row.get("computed_table")] == tables


def test_a_missing_table_still_reports_missing(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result, _ = enter_turn("Read absent.csv", workspace)
    assert "no file" in str(result.get("response", "")).lower(), result


@pytest.mark.parametrize("invalid", ["broken", "1e9999999999999999999999999", "NaN"])
def test_malformed_rows_do_not_mint_a_verified_total(tmp_path, invalid):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "tally.csv").write_text(f"team,value\ngroup-a,18.31\ngroup-b,{invalid}\n")
    result, context = enter_turn("Compute the total in tally.csv", workspace)
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    tables = [o["computed_table"] for o in context.get("runtime_tool_observations", []) if o.get("computed_table")]
    assert tables and not tables[0]["totals"], tables
    assert "value" in tables[0]["uncomputed_columns"], tables


@pytest.mark.parametrize("filename", ["tally.csv", "tally.txt"])
def test_an_explicit_byte_read_still_displays_the_file(tmp_path, filename):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / filename).write_text("label,value\nunit-a,12.75\n")
    result, _ = enter_turn(f"Read {filename}", workspace)
    assert "unit-a,12.75" in str(result.get("response", "")), result
    assert result.get("model_execution", {}).get("source") == "fast_path", result
