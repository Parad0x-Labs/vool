"""Table arithmetic is code-derived evidence, never a raw-file final answer.

Prompt cache bench B2 (2026-10-15): the raw CSV was pasted as the answer and the next turn's
hand-made sum was wrong. First landed 2026-10-18, taken back with its routing half 2026-10-20, and
re-landed on the v0.8 line 2026-11-12 with the evidence half only. The measured wordings and new
ones follow the original cases. Contributor: sls_0x.
"""
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


@pytest.mark.parametrize("question,filename,rows,heading,total", [
    ("What were the total expenses in expenses_2026_q3.csv?", "expenses_2026_q3.csv",
     "item,amount\nrent,1450\ntravel,312.40\nfood,280.10\n", "amount", "2042.5"),
    ("what's the total weight in harvest.tsv?", "harvest.tsv",
     "crate\tweight\nA1\t18.31\nA2\t100.03\nB1\t74.09\n", "weight", "192.43"),
    ("how much did each of us pay in trip_costs.csv, and who owes whom if we split it evenly?", "trip_costs.csv",
     "person,paid,memo\nMira,120.50,fuel\nJonas,64.20,food\nMira,40.00,parking\nAda,200.00,cabin\n", "paid", "424.7"),
])
def test_new_wordings_get_computed_totals_not_the_raw_file(tmp_path, question, filename, rows, heading, total):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / filename).write_text(rows)
    result, context = enter_turn(question, workspace)
    assert rows.splitlines()[1] not in str(result.get("response", "")), result
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    tables = [o["computed_table"] for o in context.get("runtime_tool_observations", []) if o.get("computed_table")]
    assert tables and Decimal(tables[0]["totals"][heading]) == Decimal(total), tables


def test_the_split_settles_everyone_to_the_cent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "trip_costs.csv").write_text(
        "person,paid,memo\nMira,120.50,fuel\nJonas,64.20,food\nMira,40.00,parking\nAda,200.00,cabin\n"
    )
    _, context = enter_turn("Compute the total in trip_costs.csv per person, and who owes whom if we split it "
                            "equally.", workspace)
    table = next(o["computed_table"] for o in context.get("runtime_tool_observations", []) if o.get("computed_table"))
    assert table["group_totals"]["person"] == {"Ada": {"paid": "200"}, "Jonas": {"paid": "64.2"},
                                              "Mira": {"paid": "160.5"}}
    ledger = table["hypothetical_equal_shares"]["person"]["paid"]
    paid = {"Ada": Decimal("200.00"), "Jonas": Decimal("64.20"), "Mira": Decimal("160.50")}
    settled = dict(paid)
    for row in ledger["transfers"]:
        settled[row["from"]] += Decimal(row["amount"])
        settled[row["to"]] -= Decimal(row["amount"])
    # After the transfers everyone has paid a third, to the cent, and no money appeared or vanished.
    assert sum(settled.values()) == sum(paid.values()), settled
    assert all(abs(amount - sum(paid.values()) / 3) < Decimal("0.01") for amount in settled.values()), settled
    # Only Jonas paid less than a third. 424.70 does not split into cents evenly, and the leftover cent
    # goes by the stated rule, so Mira's share is 141.56 and the others' 141.57.
    assert {(row["from"], row["to"], row["amount"]) for row in ledger["transfers"]} == {
        ("Jonas", "Ada", "58.43"), ("Jonas", "Mira", "18.94")}, ledger


def test_secret_shaped_tables_get_no_computed_facts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "accounts.csv").write_text("site,user,password,balance\nbank,amy,Zz-81kk,10\nmail,amy,Hx81kkzq,20\n")
    _, context = enter_turn("What is the total balance in accounts.csv?", workspace)
    observations = context.get("runtime_tool_observations", [])
    assert not [o for o in observations if o.get("computed_table")], observations
    assert "Zz-81kk" not in json.dumps(observations, default=str)


@pytest.mark.parametrize("text,whole", [
    ("Compute the total in tally.txt, per group, and who owes whom if shared equally.", True),
    ("how much did each of us pay in tally.csv, and who owes whom if we split it evenly?", True),
    ("Compute the total in tally.csv. Then tell me who paid the most.", True),
    ("Work out the average value in tally.csv and then which team is above it.", True),
    ("Compute the total in missing.csv, per group, and who owes whom if shared equally.", False),
    ("Calculate 37 x 19 on the ledger from tally.txt. Tell me a two-word joke.", False),
    ("Compute the total in tally.csv and what time is it in Tokyo?", False),
    ("Read tally.csv and tell me a joke.", False),
    ("Compute the total in tally.csv and compare it with budget.csv.", False),
    ("Sum the paid column in tally.csv and also remind me to pay Ada tomorrow.", False),
    ("Compute the total in ../tally.csv, and who owes whom if shared equally.", False),
])
def test_one_computation_over_one_file_is_one_request(tmp_path, text, whole):
    from core.agent_runtime.demand_ownership import one_file_computation_owns_turn

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("tally.csv", "tally.txt", "budget.csv"):
        (workspace / name).write_text("team,value\ngroup-a,18.31\ngroup-b,7.10\n")
    (tmp_path / "tally.csv").write_text("team,value\ngroup-z,1\n")
    assert one_file_computation_owns_turn(text, {"workspace": str(workspace)}) is whole
