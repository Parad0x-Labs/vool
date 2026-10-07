"""The memory kernel on the real served turn: a day count the receipts derive ships, a wrong one does not.

Normal ingestion, then `run_agent` and the served door's response commit, with only the model transport scripted
(`_memory_evidence_contract_driver`). The scripted reader answers "How many days ago did I go to the pottery
workshop?" with the right count (from the stored "on 3 March", stated 2025-03-05) or a wrong one. With the kernel
switches off, the past-time guard withdraws both, as before. With them on, the receipt packet reaches the reader,
the right count ships and the wrong one stays withdrawn by the temporal claim binder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("_memory_evidence_contract_driver.py")
CHAT = "allotment-log"
SEED = [
    {"chat": CHAT, "stated": "2025-03-05", "assistant": "", "user": "I went to the pottery workshop on 3 March, it was messy but fun."},
    {"chat": CHAT, "stated": "2025-03-18", "assistant": "", "user": "I started the welding class on 17 March at the community college."},
    {"chat": CHAT, "stated": "2025-03-20", "assistant": "", "user": "I prefer evening classes because of work."},
]
QUESTION = "How many days ago did I go to the pottery workshop?"
SWITCHES = ("VOOL_MEMORY_RECEIPTS", "VOOL_EVIDENCE_COMPILER", "VOOL_EVIDENCE_HOP", "VOOL_EVIDENCE_VERIFY", "VOOL_EVIDENCE_KERNEL")
WITHDRAWN = "I don't have that time"


def _served(tmp_path, *, kernel: bool) -> dict:
    days = (date.today() - date(2025, 3, 3)).days
    turns = [
        {"id": "right", "question": QUESTION, "scripted": f"{days} days ago (3 March 2025).", "reader_require": ["pottery"]},
        {"id": "wrong", "question": QUESTION, "scripted": f"{days + 17} days ago (14 February 2025).", "reader_require": ["pottery"]},
    ]
    label = "on" if kernel else "off"
    work = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / f"memory-kernel-served-{label}"
    home = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"memory-kernel-{label}-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "seed.json").write_text(json.dumps(SEED))
    (work / "turns.json").write_text(json.dumps({"turns": turns}))
    out = work / "served.json"
    env = {k: v for k, v in os.environ.items() if k not in SWITCHES}
    if kernel:
        env.update({name: "1" for name in SWITCHES})
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--repo-root", str(repo), "--home", str(home), "--out", str(out),
         "--turns-json", str(work / "turns.json"), "--seed-json", str(work / "seed.json"), "--commit"],
        cwd=repo, capture_output=True, text=True, timeout=300, env=env,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    doc = json.loads(out.read_text())
    assert doc["network_blocked"] is True
    assert all(a["url"].startswith("http://127.0.0.1:") for a in doc["blocked_network_attempts"]), doc["blocked_network_attempts"]
    return {turn["id"]: turn for turn in doc["turns"]}


def test_with_the_kernel_off_the_guard_withdraws_both_counts(tmp_path):
    turns = _served(tmp_path, kernel=False)
    for turn in turns.values():
        assert turn["routes"]["memory_route"]["route"] == "capsule_v2", turn["routes"]
        assert "Evidence receipts" not in turn["admitted_capsule"]
        assert turn["committed"]["canonical_content"].startswith(WITHDRAWN), turn["committed"]


def test_with_the_kernel_on_the_derived_count_ships_and_a_wrong_one_stays_withdrawn(tmp_path):
    turns = _served(tmp_path, kernel=True)
    right, wrong = turns["right"], turns["wrong"]
    assert right["routes"]["memory_route"] == {"route": "capsule_v2+kernel", "switches": list(SWITCHES)}, right["routes"]
    assert "Evidence receipts" in right["admitted_capsule"]
    assert right["committed"]["canonical_content"].strip() == right["scripted_raw"], right["committed"]
    assert wrong["committed"]["canonical_content"].startswith(WITHDRAWN), wrong["committed"]
    decision = wrong["diagnostic_decisions"]["evidence_verification"]
    assert decision["attempted"] is True and decision["restored"] is False, decision
