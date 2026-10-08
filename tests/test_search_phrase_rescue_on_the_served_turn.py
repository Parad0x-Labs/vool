"""The search-phrase rescue on the real served turn: a phrase-found record arrives, an asked question never does.

Served path through `run_agent` with normal ingestion; only the model transport is scripted, including the memory
search-phrase call (answered as a model would). One chat holds a project record the question's words miss; another
holds only a question the user once asked ("Should I pay 290 euros for a folding kayak?").
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


def _served(tmp_path, chat, seed, turn):
    work = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / f"rescue-{chat}"
    home = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"rescue-{chat}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "seed.json").write_text(json.dumps(seed))
    (work / "turns.json").write_text(json.dumps({"turns": [turn]}))
    out = work / "served.json"
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--repo-root", str(repo), "--home", str(home), "--out", str(out),
         "--turns-json", str(work / "turns.json"), "--seed-json", str(work / "seed.json"), "--chat-id", chat],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    return json.loads(out.read_text())["turns"][0]


def test_a_phrase_found_record_reaches_the_reader(tmp_path):
    seed = [{"chat": "chat-project", "stated": "2025-02-01", "assistant": "Nice.",
             "user": "I'm building a recipe-sharing app called PantryPal with a Flask backend."}]
    turn = _served(tmp_path, "chat-project", seed, {
        "id": "project", "question": "Summarize my project",
        "scripted": "PantryPal, a recipe-sharing app with a Flask backend.", "reader_require": ["PantryPal"],
        "system_replies": [["You help search a stored record", '["building an app", "recipe app I am making"]']],
    })
    assert "PantryPal" in turn["admitted_capsule"], turn["routes"]


def test_a_question_the_user_once_asked_never_reaches_the_reader(tmp_path):
    seed = [{"chat": "chat-kayak", "stated": "2025-02-01", "assistant": "",
             "user": "Should I pay 290 euros for a folding kayak?"}]
    turn = _served(tmp_path, "chat-kayak", seed, {
        "id": "kayak", "question": "How much did I pay for the folding kayak?",
        "scripted": "I don't know.",
        "system_replies": [["You help search a stored record", '["folding kayak price", "paid for kayak", "kayak cost"]']],
    })
    assert "290" not in turn["admitted_capsule"], turn["admitted_capsule"]
