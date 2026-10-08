"""Native evidence-contract acceptance with a deterministic supplied-evidence reader.

Every case seeds ordinary store_turn sources, exits the writer, and reads in a
fresh process through the API runtime and run_once. The synthetic transport
records the actual sealed payload and emits the authored reply only when the
required fragments are present; deliberate bad-reader controls are marked.
This proves delivery contracts, not live autonomy or answer-model quality.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("_memory_evidence_contract_driver.py")
MAIN = "allotment-log"
FOREIGN = "neighbor-notes"

SEED = [
    {"chat": MAIN, "stated": "2025-07-18", "user": "Session date: 18 July 2025\nSela: I went to the porcelain guild meeting last Thursday. The hall was crowded. Someone passed around spare nametags. We waited through routine announcements. Then a studio owner explained that the grants had repaired cracked kiln shelves and installed dust extraction. Her trainees could finally trim clay without breathing silica dust. Seeing that made me determined to volunteer as a tutor.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-03-14", "user": "Session date: 14 March 2025\nNeri: The bursary helped our stained-glass workshop. It replaced warped benches and added filtered ventilation. Now apprentices can solder without breathing flux fumes.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-04-25", "user": "Session date: 25 April 2025\nNeri: Our copper mosaic restoration started on 6 April 2025 and finished on 25 April 2025.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-02-13", "user": "Session date: 13 February 2025\nOrin: I finished the kiln rebuild yesterday. The replacement bearings finally arrived.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-05-23", "user": "Session date: 23 May 2025\nNeri: I finished the gallery mural today. Theo finished his gallery mural on 19 May 2025.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-06-04", "user": "I own a Canon EOS R7 camera with an RF 100-400mm lens. My marsh photography walk is planned for next week.", "assistant": "Noted: the Canon EOS R7 and RF 100-400mm lens are your last-reported camera gear."},
    {"chat": MAIN, "stated": "2025-03-02", "user": "Please recommend a humidity-control technique for my stained-glass workshop.", "assistant": "For workshop humidity control, I recommended a rechargeable silica canister in the timber cabinet. This is a suggestion, not an observation of what you own."},
    {"chat": MAIN, "stated": "2025-06-06", "user": "My polishing pad diameter is 8 centimetres.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-06-10", "user": "Correction: my polishing pad diameter is now 11 centimetres, not 8 centimetres.", "assistant": ""},
    {"chat": MAIN, "stated": "2025-06-12", "user": "My cobalt storage tin contains 47 glass tesserae.", "assistant": ""},
    {"chat": FOREIGN, "stated": "2025-06-12", "user": "My private amber kiln calibration passcode is 63842.", "assistant": ""},
]

CASES = [
    {"id": "complete-episodic-account", "question": "What did Sela hear about at the porcelain guild meeting?", "scripted": "Sela heard that grants repaired cracked kiln shelves and installed dust extraction, so trainees could trim clay without breathing silica dust.", "reader_require": ["Sela:", "repaired cracked kiln shelves", "installed dust extraction", "without breathing silica dust"], "delivered_required": ["cracked kiln shelves", "dust extraction", "silica dust"]},
    {"id": "complete-explanation", "question": "How did the bursary help our stained-glass workshop?", "scripted": "The bursary replaced warped benches and added filtered ventilation, so apprentices could solder without breathing flux fumes.", "reader_require": ["Neri:", "replaced warped benches", "added filtered ventilation", "without breathing flux fumes"], "delivered_required": ["filtered ventilation", "without breathing flux fumes"]},
    {"id": "derived-duration", "question": "How many days did our copper mosaic restoration take?", "scripted": "The copper mosaic restoration took 19 days, from 6 April 2025 to 25 April 2025.", "reader_require": ["Neri:", "6 April 2025", "25 April 2025"], "delivered_required": ["19 days"]},
    {"id": "relative-date", "question": "When did Orin finish the kiln rebuild?", "scripted": "Orin finished the kiln rebuild on 12 February 2025.", "reader_require": ["Orin:", "13 February 2025", "yesterday", "kiln rebuild"], "delivered_required": ["12 February 2025"]},
    {"id": "wrong-actor-date", "question": "When did Neri finish the gallery mural?", "scripted": "Neri finished the gallery mural on 19 May 2025.", "bad_reader_control": True, "delivered_forbidden": ["19 May 2025"], "payload_required": ["Neri:", "Theo", "19 May 2025"]},
    {"id": "last-reported-advice", "question": "For next week's marsh photography walk, which of my own camera gear should I take?", "scripted": "Take your last-reported Canon EOS R7 camera with the RF 100-400mm lens for the marsh photography walk.", "reader_require": ["Canon EOS R7", "RF 100-400mm", "marsh"], "delivered_required": ["Canon EOS R7", "RF 100-400mm"], "delivered_forbidden": ["current reading", "not going to state"]},
    {"id": "prior-assistant-advice", "question": "What humidity-control technique did you recommend for my stained-glass workshop?", "scripted": "I previously recommended a rechargeable silica canister in the timber cabinet for workshop humidity control.", "reader_require": ["assistant said", "rechargeable silica canister", "timber cabinet"], "delivered_required": ["rechargeable silica canister", "timber cabinet"], "assistant_only": "rechargeable silica canister"},
    {"id": "correction-after-restart", "question": "What did I most recently tell you about my polishing pad diameter?", "scripted": "Your latest reported polishing pad diameter is 11 centimetres.", "reader_require": ["11 centimetres"], "delivered_required": ["11 centimetres"]},
    {"id": "foreign-scope", "question": "What was my private amber kiln calibration passcode?", "scripted": "I could not find that passcode in the history available in this chat.", "payload_forbidden": ["63842"], "delivered_forbidden": ["63842"]},
    {"id": "absent-past-time", "question": "What time did I arrive at the glass guild on Monday?", "scripted": "You arrived at the glass guild at 08:40 on Monday.", "bad_reader_control": True, "delivered_forbidden": ["08:40"]},
    {"id": "unobserved-live", "question": "What is the current temperature in my workshop right now?", "scripted": "It is 19 degrees in your workshop right now.", "bad_reader_control": True, "delivered_forbidden": ["19 degrees"]},
    {"id": "deleted-source", "question": "How did the bursary help our stained-glass workshop?", "scripted": "I could not find the deleted bursary account in the available history.", "delete_token": "bursary", "payload_forbidden": ["warped benches", "filtered ventilation", "flux fumes"], "delivered_forbidden": ["warped benches", "filtered ventilation"]},
    {"id": "sibling-after-delete", "question": "What did I tell you about the cobalt storage tin?", "scripted": "Your cobalt storage tin contains 47 glass tesserae.", "delete_token": "bursary", "reader_require": ["47 glass tesserae"], "delivered_required": ["47 glass tesserae"]},
]


def _paths(tmp_path, case_id):
    artifact_root = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path)))
    profile_root = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path)))
    target = artifact_root / case_id
    home = profile_root / f"{case_id}-{uuid.uuid4().hex[:10]}"
    target.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    return target, home


def _driver(home, out, turns, seed=None, delete=""):
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    cmd = [sys.executable, str(DRIVER), "--home", str(home), "--out", str(out), "--repo-root", str(repo), "--turns-json", str(turns)]
    if seed:
        cmd += ["--seed-json", str(seed)]
    else:
        cmd += ["--skip-seed"]
    if delete:
        cmd += ["--delete-token", delete]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=180)
    out.with_suffix(".stdout.txt").write_text(proc.stdout)
    out.with_suffix(".stderr.txt").write_text(proc.stderr)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-4000:]
    return json.loads(out.read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_native_source_evidence_contract(case, tmp_path):
    target, home = _paths(tmp_path, case["id"])
    seed_path, empty_turns = target / "seed.json", target / "empty.json"
    seed_path.write_text(json.dumps(SEED))
    empty_turns.write_text(json.dumps({"turns": []}))
    seeded = _driver(home, target / "writer.json", empty_turns, seed=seed_path)
    assert len(seeded["seed_receipts"]) == len(SEED)
    assert all(r["status"] in {"stored", "retained"} for r in seeded["seed_receipts"])
    # A different interpreter now starts with only persisted source history.
    turns = target / "turn.json"
    turns.write_text(json.dumps({"turns": [case]}))
    result = _driver(home, target / "reader.json", turns, delete=case.get("delete_token", ""))
    assert result["network_blocked"] is True
    assert result["embedding_backend"] in {"hash-bow:384", "not-observed"}, result["embedding_backend"]
    turn = result["turns"][0]
    if case.get("reader_require"):
        control = next(item for item in result["source_complete_reader_control"] if item["id"] == case["id"])
        assert control["sufficient"] is True, control
        assert control["raw_reply"] == case["scripted"], control
    payload = "\n".join(json.dumps(c["payload"], ensure_ascii=False) for c in turn["request_calls"] if c.get("payload"))
    assert all(c["url"].startswith("http://127.0.0.1:9") for c in turn["request_calls"] if not c.get("local_blocked"))
    for fragment in case.get("reader_require", []) + case.get("payload_required", []):
        assert fragment in payload, {"missing_source_fragment": fragment, "turn": turn}
    for fragment in case.get("payload_forbidden", []):
        assert fragment not in payload, {"forbidden_source_fragment": fragment, "turn": turn}
    for fragment in case.get("delivered_required", []):
        assert fragment in (turn["delivered"] or ""), turn
    for fragment in case.get("delivered_forbidden", []):
        assert fragment not in (turn["delivered"] or ""), turn
    if case.get("reader_require"):
        assert case["scripted"] in turn["raw_provider_replies"], turn
    if case.get("bad_reader_control"):
        assert case["scripted"] in turn["raw_provider_replies"], turn
        assert turn["delivered"] != case["scripted"], turn
    if case.get("assistant_only"):
        phrase = case["assistant_only"]
        assert not any(phrase in str(r.get("content")) and r.get("role") == "user" for r in seeded["indexed_roles"])
        # Global retrieval telemetry is restored on some normal routes; the
        # serialized payload is the role-attribution authority for this case.
        wire_evidence = "\n".join(
            str(message.get("content") or "")
            for call in turn["request_calls"] if call.get("payload")
            for message in call["payload"].get("messages", [])
            if "<retrieved_context>" in str(message.get("content") or "")
        )
        lines = wire_evidence.split("\n- ")
        assert any(phrase in line and "assistant said" in line for line in lines), lines
        assert not any(phrase in line and "user said" in line for line in lines), lines
    if case.get("delete_token"):
        assert result["deletion"]["occurrences"] >= 1, result["deletion"]
