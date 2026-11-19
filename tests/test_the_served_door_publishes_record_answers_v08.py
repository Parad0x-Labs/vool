"""A record question is published at the HTTP door, whose own context never saw the turn's records.

The door (`core.web.api.runtime.ollama_chat_response`) commits the result with the context it built before the run.
The runtime works on a copy of that context: the request seal with the admitted records, the turn ids the seal is bound
to and the grounding lifecycle stamp all land on the copy. `core.finalization` publishes the turn's memory records to the
gate from the context it is handed, so at the door it published none, the gate held no memory rows, and a question the
records answer was refused as a lookup nothing answered.

Measured on the real app (cloud route, web lookups off, 2026-11-19): "My friend Ines told me she is leaving for Ireland
next month." then "When will Ines leave for Ireland?" got "Web lookups are off, so I can't look this up online. ...", as
did three questions about a stored conversation whose model replies were right ("Aragorn.").

`test_memory_records_ground_served_turn` runs the same turns and commits with the context the runtime RETURNS, where the
records are; it passed while the door failed. This file commits with the door's own pre-run context.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("_memory_evidence_contract_driver.py")
_spec = importlib.util.spec_from_file_location("_served_turn_cases", Path(__file__).with_name("test_memory_records_ground_served_turn.py"))
_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cases)
SEED, TURNS = _cases.SEED, _cases.TURNS


@pytest.fixture(scope="module")
def door(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("door")
    work = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / "memory-record-door"
    home = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"memory-record-door-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "seed.json").write_text(json.dumps(SEED))
    (work / "turns.json").write_text(json.dumps({"turns": TURNS}))
    out = work / "served.json"
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--repo-root", str(repo), "--home", str(home), "--out", str(out),
         "--turns-json", str(work / "turns.json"), "--seed-json", str(work / "seed.json"),
         "--reference-date", "2026-09-28", "--commit", "--door-context"],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    doc = json.loads(out.read_text())
    assert doc["network_blocked"] is True
    return {turn["id"]: turn for turn in doc["turns"]}


@pytest.mark.parametrize("turn_id", ["aragorn", "ireland", "ireland-abbrev"])
def test_an_answer_the_records_state_is_published_at_the_door(door, turn_id):
    turn = door[turn_id]
    committed = turn["committed"]
    assert turn["diagnostic_decisions"]["current_information_required"] is True, turn["diagnostic_decisions"]
    assert committed["grounding_publication"]["state"] == "published", committed
    assert committed["grounding_publication"]["support_origin"] == "memory_record", committed
    assert committed["canonical_content"].strip() == turn["scripted_raw"], committed
    assert "Web lookups are off" not in committed["canonical_content"], committed


def test_a_value_the_records_do_not_state_is_still_withheld_at_the_door(door):
    dance = door["dance"]["committed"]
    assert dance["canonical_content"].startswith("That is not mentioned in the records I have from our conversations"), dance


def test_a_live_lookup_is_still_refused_at_the_door_and_states_nothing(door):
    train = door["train"]["committed"]
    assert train["canonical_content"].startswith("Web lookups are off"), train
    assert "14:05" not in train["canonical_content"], train
    assert "records I have from our conversations" not in train["canonical_content"], train
