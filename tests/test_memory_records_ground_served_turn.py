"""The chat's memory records reach the publication gate on the real served turn and the answer publishes.

`test_memory_records_ground_record_questions` proves the gate decision with the evidence handed in. This proves
the live wiring: normal ingestion, then `run_agent` and the served door's response commit
(`core.web.api.runtime._response_commit`, where finalization and the gate run), with
`admitted_capsule_evidence_text` reading the turn's admitted capsule through its session and request bindings.
The only scripted element is the model transport (`_memory_evidence_contract_driver`): it returns the reader
draft official18 recorded for these questions, and only when the capsule it was sent carries the record.

Before the memory-record support (8ee8deae) this exact run refused both record questions with "it needed
current information" (gate state failed, detail retrieval_outcome=none), as official18 did.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("_memory_evidence_contract_driver.py")
CHAT = "allotment-log"
# The benchmark harness's LoCoMo mapping: one turn per utterance, session-date envelope and speaker kept.
SEED = [
    {"chat": CHAT, "stated": "2023-08-02", "assistant": "", "user": "Session date: 2 August, 2023\nJohn: Thanks! I've watched a bunch of them and they're inspiring. My favorite character is Aragorn, he grows so much throughout the story."},
    {"chat": CHAT, "stated": "2023-08-03", "assistant": "", "user": "Session date: 3 August, 2023\nJohn: I also started a reading club at work, we meet on Thursdays."},
    {"chat": CHAT, "stated": "2024-01-05", "assistant": "", "user": "Session date: 5 January, 2024\nTim: I've been practicing basketball every morning before class."},
    {"chat": CHAT, "stated": "2024-01-07", "assistant": "", "user": "Session date: 7 January, 2024\nTim: Great news - I'm finally in the study abroad program I applied for! Next month, I'm off to Ireland for a semester."},
]
INSTRUCTION = (
    "Answer using the imported prior conversations. You may derive only answers supported by those records. "
    "If the records do not support an answer, say you do not know. Give a concise final answer.\n"
)
TURNS = [
    {"id": "aragorn", "question": INSTRUCTION + "According to John, who is his favorite character from Lord of the Rings?",
     "scripted": "Aragorn.", "reader_require": ["Aragorn"]},
    {"id": "ireland", "question": INSTRUCTION + "When will Tim leave for Ireland?",
     "scripted": 'February 2024 — Tim said on 7 January 2024 he was off to Ireland "next month."',
     "reader_require": ["Ireland", "Next month"]},
    # The reader's draft in the a56209ae paid run: an abbreviated month and a sentence-initial "Around" beside one.
    {"id": "ireland-abbrev", "question": INSTRUCTION + "When will Tim leave for Ireland?",
     "scripted": 'Around February 2024 \u2014 on Jan 7, 2024 Tim said he was "off to Ireland next month."',
     "reader_require": ["Ireland", "Next month"]},
    # A live lookup in the same chat: the records hold nothing about trains, so the draft stays withheld.
    {"id": "train", "question": "When does the next train to Vilnius leave?",
     "scripted": "The next train to Vilnius leaves at 14:05 from platform 3."},
]


def _served(tmp_path) -> dict:
    work = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / "memory-record-served"
    home = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"memory-record-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "seed.json").write_text(json.dumps(SEED))
    (work / "turns.json").write_text(json.dumps({"turns": TURNS}))
    out = work / "served.json"
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--repo-root", str(repo), "--home", str(home), "--out", str(out),
         "--turns-json", str(work / "turns.json"), "--seed-json", str(work / "seed.json"),
         "--reference-date", "2026-09-28", "--commit"],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    doc = json.loads(out.read_text())
    assert doc["network_blocked"] is True
    # Only local runtime probes were attempted, and the sink refused them; nothing left the machine.
    assert all(a["url"].startswith("http://127.0.0.1:") for a in doc["blocked_network_attempts"]), doc["blocked_network_attempts"]
    return {turn["id"]: turn for turn in doc["turns"]}


def test_record_questions_publish_from_the_live_capsule_and_a_live_lookup_does_not(tmp_path):
    turns = _served(tmp_path)
    for turn_id, fragment in (("aragorn", "Aragorn"), ("ireland", "Ireland"), ("ireland-abbrev", "Ireland")):
        turn = turns[turn_id]
        committed = turn["committed"]
        # The turn really was escalated as current information, so the gate ran on it.
        assert turn["diagnostic_decisions"]["current_information_required"] is True, turn["diagnostic_decisions"]
        assert committed["grounding_stages"]["required"] is True, committed
        # The record reached the model, and the same admitted capsule reached the gate through its bindings.
        assert fragment in turn["admitted_capsule"]
        assert committed["grounding_publication"]["state"] == "published", committed
        assert committed["grounding_publication"]["support_origin"] == "memory_record", committed
        assert committed["canonical_content"].strip() == turn["scripted_raw"], committed

    train = turns["train"]["committed"]
    assert train["grounding_stages"]["required"] is True, train
    assert train["grounding_publication"]["state"] in {"refused", "failed"}, train
    assert "14:05" not in train["canonical_content"], train
