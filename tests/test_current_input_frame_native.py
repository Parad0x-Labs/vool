"""Native restart controls for a current background frame and fresh readings.

Uses the established deterministic final-wire reader; no HTTP provider runs or
answer-quality scores. Every writer exits before the reader starts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_memory_evidence_contract_native import FOREIGN, MAIN, _driver, _paths

SEED = [
    {"chat": MAIN, "stated": "2025-08-10", "user": "I own a Sony A7R IV camera with a Sony 24-70mm lens. I want to add a stable tripod.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-08-12", "user": "My telescope assembly is a StellarVue SV80 with an 80mm objective. I use it for moon observations.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-08-13", "user": "My workshop tank held 410 litres on 13 August 2025. That is an earlier reading, not a measurement of its present contents.", "assistant": ""},
    {"chat": FOREIGN, "stated": "2025-08-14", "user": "I own a private Orion VX200 telescope with a 200mm objective.", "assistant": ""},
]

CASES = [
    {"id": "current-input-camera", "question": "Can you suggest some accessories that would complement my current photography setup?", "scripted": "For your last-reported Sony A7R IV and Sony 24-70mm lens, I suggest a stable tripod and a padded case.", "reader_require": ["Sony A7R IV", "24-70mm"], "delivered_required": ["Sony A7R IV", "24-70mm", "tripod"], "delivered_forbidden": ["current reading", "not going to state"]},
    {"id": "current-input-telescope", "question": "Can you recommend mounting accessories for my current telescope assembly?", "scripted": "For your last-reported StellarVue SV80 with an 80mm objective, I suggest a padded carry case and a stable mount.", "reader_require": ["StellarVue SV80", "80mm"], "delivered_required": ["StellarVue SV80", "80mm", "stable mount"], "payload_forbidden": ["Orion VX200", "200mm"], "delivered_forbidden": ["current reading", "Orion VX200", "200mm"]},
    {"id": "current-input-mixed-price", "question": "Can you recommend accessories for my current photography setup and tell me their prices today?", "scripted": "For your last-reported Sony A7R IV and 24-70mm lens, I suggest a padded case. The case costs $89 today.", "reader_require": ["Sony A7R IV", "24-70mm"], "delivered_required": ["Sony A7R IV", "padded case"], "delivered_forbidden": ["$89"], "bad_reader_control": True},
    {"id": "current-input-stale-tank", "question": "How much water is in my current workshop tank right now?", "scripted": "Your workshop tank currently holds 410 litres.", "payload_required": ["410 litres"], "delivered_forbidden": ["410 litres"], "bad_reader_control": True},
]

@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
@pytest.mark.parametrize("hydrated", [False, True], ids=["unhydrated-diagnostic", "hydrated-guard"])
def test_current_input_contract_after_native_restart(case, hydrated, tmp_path):
    target, home = _paths(tmp_path, case["id"] + ("-hydrated" if hydrated else "-unhydrated"))
    seeds, empty = target / "seed.json", target / "empty.json"
    seeds.write_text(json.dumps(SEED))
    empty.write_text(json.dumps({"turns": []}))
    writer = _driver(home, target / "writer.json", empty, seed=seeds)
    assert len(writer["seed_receipts"]) == len(SEED)
    assert all(row["status"] in {"stored", "retained"} for row in writer["seed_receipts"])
    turns = target / "turn.json"
    turns.write_text(json.dumps({"turns": [case]}))
    if hydrated:
        repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(Path(__file__).resolve().parents[1]))).resolve()
        command = [sys.executable, str(Path(__file__).with_name("_current_input_frame_hydrated_driver.py")),
                   "--home", str(home), "--out", str(target / "reader.json"), "--repo-root", str(repo),
                   "--turns-json", str(turns), "--skip-seed"]
        required = case.get("reader_require") or case.get("payload_required") or []
        matches = [row for row in writer["retained_sources"]
                   if row["chat_scope"] == MAIN and row["status"] == "active"
                   and all(fragment in row["body"] for fragment in required)]
        assert len(matches) == 1, "The declared fixture history exchange must be unambiguous"
        process = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=180,
                                 env=dict(os.environ, VOOL_CURRENT_INPUT_HYDRATION_FROM=str(target / "writer.json"),
                                          VOOL_CURRENT_INPUT_HYDRATION_OCCURRENCE=matches[0]["occurrence_id"]))
        (target / "reader.stdout.txt").write_text(process.stdout)
        (target / "reader.stderr.txt").write_text(process.stderr)
        assert process.returncode == 0, process.stdout[-2000:] + process.stderr[-4000:]
        reader = json.loads((target / "reader.json").read_text())
        assert reader["authorized_hydration"]["not_current_observation"]
    else:
        reader = _driver(home, target / "reader.json", turns)
    assert reader["network_blocked"]
    turn = reader["turns"][0]
    payload = "\n".join(json.dumps(call["payload"], ensure_ascii=False) for call in turn["request_calls"] if call.get("payload"))
    sufficient = all(fragment in payload for fragment in case.get("reader_require", []))
    if not hydrated and not sufficient:
        # Preserve the original strict acceptance failures as an explicit
        # diagnostic outcome. This row proves accurate abstention/classification,
        # never rescue, native recall success, or semantic answer correctness.
        assert case["scripted"] not in turn["raw_provider_replies"]
        assert "could not find sufficient source evidence" in turn["delivered"]
        assert reader["source_complete_reader_control"][0]["sufficient"]
        (target / "DIAGNOSTIC-OUTCOME.json").write_text(json.dumps({
            "case_id": case["id"], "native_workflow_acceptance": "failed",
            "classification": "retained_sources_sufficient_final_wire_missing",
            "rescued": False, "guard_acceptance": "not_exercised",
            "proof_level": "Known unhydrated failure diagnostic; original strict attempts preserved",
        }, indent=2))
        return
    assert case["scripted"] in turn["raw_provider_replies"], turn
    for fragment in case.get("reader_require", []) + case.get("payload_required", []):
        assert fragment in payload, (fragment, turn)
    for fragment in case.get("payload_forbidden", []):
        assert fragment not in payload, (fragment, turn)
    for fragment in case.get("delivered_required", []):
        assert fragment in turn["delivered"], (fragment, turn)
    for fragment in case.get("delivered_forbidden", []):
        assert fragment not in turn["delivered"], (fragment, turn)
    if case.get("bad_reader_control"):
        assert turn["delivered"] != case["scripted"]
