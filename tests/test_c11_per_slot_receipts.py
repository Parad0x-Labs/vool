"""AUD-20260829-003, C11 — receipts show which tool/source/model handled EACH slot.

C11 is the criterion that never moved at any commit. It never moved because provenance was
turn-grain by construction: `TurnProvenance` collapses a multi-tool turn to the last tool plus a
count, and the entrance-certified commit carried no `display_metadata` at all -- the
`provenance = {}` surface the signoff records.

Measured on a served two-slot turn before this change: two demands, two different lanes
(`workspace_read_fast_path`, `direct_math_fast_path`), and the receipt named ONE tool with
`extra_tools: 0`.

The per-slot facts already existed with zero production readers: `turn_demand_ledger`, one row per
minted unit. These tests pin the projection, the two attach points, and -- the part that keeps the
class honest -- that turn-grain keys can never satisfy it.
"""
from __future__ import annotations

from core.response_provenance import slot_receipts
from core.turn_contract import TURN_DEMAND_LEDGER_KEY

_LEDGER = [
    {
        "demand_id": "u1",
        "request": "Return exactly the second line of notes.txt.",
        "capability": "workspace_read",
        "lane_id": "workspace_read_fast_path",
        "attempted": True,
        "terminal_state": "executed",
        "refusal_reasons": [],
    },
    {
        "demand_id": "u2",
        "request": "What is the water temperature in the Baltic Sea?",
        "capability": "unsupported",
        "lane_id": "",
        "attempted": False,
        "terminal_state": "not_attempted",
        "refusal_reasons": [],
    },
]


def test_one_receipt_per_minted_slot_including_the_one_that_failed():
    """"Which tool handled this" is exactly the question a FAILED slot raises.

    Counting only served slots is how a turn that dropped everything passes vacuously -- the
    gauntlet's own C11 cell records that happening on the operator's evening variant.
    """
    receipts = slot_receipts({TURN_DEMAND_LEDGER_KEY: _LEDGER}, {})
    assert set(receipts) == {"u1", "u2"}
    assert receipts["u1"]["tool"] == "workspace_read"
    assert receipts["u1"]["source"] == "workspace_read_fast_path"
    assert receipts["u1"]["state"] == "executed"
    # The unserved slot still gets a row, and it says what it is rather than borrowing a lane.
    assert receipts["u2"]["state"] == "not_attempted"
    assert receipts["u2"]["source"] == "unattributed"


def test_a_deterministic_slot_is_never_attributed_to_the_turns_model():
    """The defect this class is about is turn-grain attribution wearing a per-slot label.

    A file read was not performed by the model, however prominent the model is in the turn's
    own provenance block.
    """
    receipts = slot_receipts(
        {TURN_DEMAND_LEDGER_KEY: _LEDGER},
        {"answer_provenance": {"model_label": "qwen2.5:7b"}, "usage_summary": {"model": "qwen2.5:7b"}},
    )
    assert receipts["u1"]["model"] == ""
    assert receipts["u2"]["model"] == ""


def test_a_turn_that_minted_no_demand_keeps_a_byte_identical_commit():
    """No slots, no block: the common turn's commit must not change shape at all."""
    assert slot_receipts({}, {}) == {}
    assert slot_receipts({TURN_DEMAND_LEDGER_KEY: []}, {}) == {}
    assert slot_receipts(None, None) == {}


def test_the_gauntlet_counts_these_receipts_as_per_slot_attribution():
    """THE INSTRUMENT'S OWN READER, so the class is graded by the audit's instrument and not by
    a bespoke assertion written to agree with the code.

    `red_b`'s `_per_slot_attribution` reads `slot_receipts` off `display_metadata`; its C11 cell
    requires at least one attribution per requested slot AND a non-empty provenance block.
    """
    import tests.red_b_served_gauntlet as red_b

    receipts = slot_receipts({TURN_DEMAND_LEDGER_KEY: _LEDGER}, {})
    served = red_b.Served(
        entrance="frontdoor",
        prompt="",
        chat_id="c11",
        text="",
        commit={
            "display_metadata": {
                "provenance": {"route": "demand_owned_mixed_turn"},
                "slot_receipts": receipts,
            }
        },
    )
    found = red_b._per_slot_attribution(served, ())
    assert len(found) >= 2, found


def test_turn_grain_keys_alone_do_not_satisfy_the_instrument():
    """NEGATIVE CONTROL -- the one that keeps "per-slot" from being a rename.

    A commit carrying only the turn-grain block must still score zero per-slot attributions,
    so a future change cannot pass C11 by relabelling what was already there.
    """
    import tests.red_b_served_gauntlet as red_b

    served = red_b.Served(
        entrance="frontdoor",
        prompt="",
        chat_id="c11",
        text="",
        commit={
            "display_metadata": {
                "provenance": {
                    "route": "demand_owned_mixed_turn",
                    "tool": "workspace.read_file",
                    "extra_tools": 0,
                },
                "usage": {"model": "qwen2.5:7b"},
            }
        },
    )
    assert red_b._per_slot_attribution(served, ()) == {}


def test_every_minted_slot_gets_a_receipt_even_when_no_lane_claimed_it(tmp_path):
    """THE HALF-COVERAGE DEFECT. `turn_demand_ledger` is written by ONE route, so a four-slot
    turn served by another lane produced receipts for two slots and none for the other two --
    measured on the gauntlet's evening prompt, which scored 2/4.

    A slot with no receipt is exactly the slot a reader most needs one for. Every slot the turn
    MINTED gets a row; where nothing claimed it, the row says so rather than being absent.
    """
    import storage.db as sdb
    from core.conductor import obligation_ledger as ol
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "c11.db")
    try:
        run_migrations()
        text = (
            "What is 1000 EUR to RUB? How much gold can I buy with it? "
            "What is the weather in Rome? What is the water temperature in the Baltic Sea?"
        )
        from core.agent_runtime.answer_coverage import demand_units

        units = demand_units(text)
        assert len(units) >= 3, units
        opened = ol.open_obligation_set(
            request_text=text,
            obligations=[
                {"obligation_id": "ob:c11:answer", "text": text[:240], "kind": "prose"},
                *(
                    {
                        "obligation_id": f"ob:c11:demand:{u.unit_id}",
                        "text": u.text,
                        "kind": "demand",
                        "unit_id": u.unit_id,
                        "slice_id": u.slice_id,
                    }
                    for u in units
                ),
            ],
        )
        ol.bind_active_set(opened["set_id"], opened["version"])
        # Only ONE slot carries a ledger row -- the half-coverage the defect produced.
        receipts = slot_receipts(
            {
                TURN_DEMAND_LEDGER_KEY: [
                    {
                        "demand_id": units[0].unit_id,
                        "request": units[0].text,
                        "capability": "fx_quote",
                        "lane_id": "live_data_lane",
                        "attempted": True,
                        "terminal_state": "executed",
                        "refusal_reasons": [],
                    }
                ]
            },
            {},
        )
        assert len(receipts) == len(units), receipts
        assert receipts[units[0].unit_id]["tool"] == "fx_quote"
        for unit in units[1:]:
            row = receipts[unit.unit_id]
            assert row["source"] == "unattributed" and row["state"] == "not_attempted", row
            assert row["request"] == unit.text
    finally:
        ol.clear_active_set()
        sdb.configure_default_db_path(None)


def test_the_ledger_crosses_the_transport_doors_context_copy():
    """THE SERVED GAP. `_run_agent_locked` starts from a fresh `default_agent_source_context()`
    and updates it, so `run_once` writes the demand ledger onto a COPY and the transport door
    still holds its own pre-run dict. Measured: `display_metadata` at the served door carried
    ['provenance', 'provenance_footer', 'usage'] and no `slot_receipts`, while the identical
    in-process path -- caller and runtime sharing one dict -- carried a receipt per slot.

    The ledger therefore rides the RESULT PAYLOAD, the crossing the closure verdict already
    makes. This pins that crossing: an empty context plus a payload stash must still produce
    receipts.
    """
    receipts = slot_receipts({}, {"_turn_demand_ledger": _LEDGER})
    assert set(receipts) == {"u1", "u2"}, receipts
    assert receipts["u1"]["tool"] == "workspace_read"


def test_the_context_still_wins_when_it_has_the_ledger():
    """The in-process path must not regress: a context carrying the ledger is used directly,
    and a stale payload stash cannot override it."""
    receipts = slot_receipts(
        {TURN_DEMAND_LEDGER_KEY: _LEDGER},
        {"_turn_demand_ledger": [{"demand_id": "zz", "request": "stale", "capability": "x",
                                   "lane_id": "y", "attempted": True,
                                   "terminal_state": "executed", "refusal_reasons": []}]},
    )
    assert set(receipts) == {"u1", "u2"}, receipts
