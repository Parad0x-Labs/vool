"""Receipt erasure follows authored payloads without revoking generated coordinates."""
from __future__ import annotations

import json

import pytest

from core.memory_receipts import receipts_for_scope, write_receipt
from core.vool_memory import VoolMemory
from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


@pytest.mark.parametrize("carrier", ["new_column", "json_column", "said_column", "facts_alias", "nested_value", "nested_key"])
def test_receipt_sweep_covers_future_writer_fields(tmp_path, carrier):
    home = _profile(tmp_path)
    memory = VoolMemory(runtime_home=home)
    chat = "future-receipt-fields"
    try:
        occurrence = memory.occurrence_store(
            chat_scope=chat, role="user", authority="observed-user-statement",
            body="The greenhouse aisle has six trays.", statement_at=1770000000,
        )
        assert write_receipt(memory, occurrence, chat_scope=chat).status == "written"
        if carrier == "new_column":
            memory._conn.execute("ALTER TABLE memory_receipts ADD COLUMN extra_caption TEXT DEFAULT ''")
            memory._conn.execute("UPDATE memory_receipts SET extra_caption = ?", ("MINT-84",))
        elif carrier == "json_column":
            memory._conn.execute("ALTER TABLE memory_receipts ADD COLUMN extra_payload JSON DEFAULT '{}' ")
            memory._conn.execute("UPDATE memory_receipts SET extra_payload = ?", ('{"caption":"M\\u0049NT-84"}',))
        elif carrier == "said_column":
            memory._conn.execute("ALTER TABLE memory_receipts ADD COLUMN said TEXT DEFAULT ''")
            memory._conn.execute("UPDATE memory_receipts SET said = ?", ("MINT-84",))
        elif carrier == "facts_alias":
            memory._conn.execute("ALTER TABLE memory_receipts ADD COLUMN facts JSON DEFAULT '[]'")
            memory._conn.execute("UPDATE memory_receipts SET facts = ?", ('[{"sentence":"M\\u0049NT-84"}]',))
        else:
            row = memory._conn.execute("SELECT facts_json FROM memory_receipts").fetchone()
            facts = json.loads(row[0])
            assert facts
            facts[0]["additional_annotation"] = {"caption": "MINT-84"} if carrier == "nested_value" else {"MINT-84": "copy"}
            memory._conn.execute("UPDATE memory_receipts SET facts_json = ?", (json.dumps(facts),))
        memory._conn.commit()
        assert memory._forget_derived_receipts("MINT-84", chat_scope=chat) == 1
        row = dict(memory._conn.execute("SELECT * FROM memory_receipts").fetchone())
        assert "MINT-84" not in json.dumps(row)
        assert "greenhouse" in row["head_text"]
    finally:
        memory.close()


def test_receipt_reader_preserves_sibling_with_revoked_digit_in_generated_date(tmp_path):
    home = _profile(tmp_path)
    memory = VoolMemory(runtime_home=home)
    chat = "receipt-date-not-authored"
    try:
        occurrence = memory.occurrence_store(
            chat_scope=chat, role="user", authority="observed-user-statement",
            body="My lead wall grade is 6.", statement_at=1775293200,
        )
        assert write_receipt(memory, occurrence, chat_scope=chat).status == "written"
        memory.record_revocation("4", chat_id=chat)
        rows = receipts_for_scope(memory, chat)
        assert len(rows) == 1
        assert "lead wall grade is 6" in rows[0]["head_text"]
    finally:
        memory.close()
