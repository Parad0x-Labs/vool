"""A forget the request's reader counts as two operands, and the forget command as one target, closes its admission.

Mac run of d585608b6 (real app from source, local qwen3:8b): "forget everything about my scooter, erase that memory
completely" erased the scooter and answered "Forget applied", but the turn's forget admission stayed pending in the
ledger. The request's reader expected two operands ("everything about my scooter", "that memory completely"); the forget
command ran one target holding both and recorded its result under it. Every later read of the forgotten list in the
profile was then refused ("owner erasure is incomplete"), so a recall after a Lithuanian forget could no longer tell it
had nothing on record.
"""
from __future__ import annotations

import json
import random

import pytest

from tests.test_forget_turn_admission_r9 import _isolated_admission_database
from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

__all__ = ["_hash_backend", "_isolated_admission_database"]


@pytest.mark.usefixtures("_hash_backend")
@pytest.mark.parametrize("seed", range(3))
def test_a_two_operand_forget_closes_its_admission(tmp_path, seed):
    from core.agent_runtime.agent import VoolAgent
    from core.memory import revocation

    rng = random.Random(f"two-operand-{seed}")
    code = rng.choice(["MINT84", "KESTREL19", "OAK552"])
    tail = rng.choice(["erase that memory completely", "wipe it", "delete that from memory pls"])
    text = f"forget my locker code {code}, {tail}"
    home = _profile(tmp_path)
    chat = f"two-operand-{seed}"
    _ingest(home, chat, [(1760000000, f"My locker code is {code}.")])
    agent = VoolAgent(backend_name="test-backend", device="admission-fixture", persona_id="default")
    context = {"chat_id": chat, "runtime_home": str(home), "client_turn_id": f"two-operand-{seed}"}
    agent.run_once(text, session_id_override=chat, source_context=context)
    rows = json.loads((home / ".forget-authority" / "ledger.json").read_text())["operations"]
    admissions = [row for row in rows.values() if row.get("kind") == "admission"]
    assert admissions and all(row["status"] == "complete" for row in admissions), admissions
    # The forgotten list reads again, across every chat.
    assert code.lower() in " ".join(revocation.revoked_tokens(None, home))
