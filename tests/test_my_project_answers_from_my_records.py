""""Summarize my project" answers from the user's own records, never from VOOL's description of itself.

Found on a spent benchmark question (2026-10-07): the request matched the one-shot text-task route (a "summary"),
which strips every context message, so the model received no record of the user's project and answered by
describing VOOL's own product. A text-task request whose object is the speaker's own thing and that supplies no
material to work on is a memory question; it now takes the ordinary path with the chat's memory. Served path,
authored history; only the model transport is scripted, and it answers only when the project record reached it.
Retrieval here uses the hash-embedding fallback, so the records name "my project" in the user's own words; the
semantic (paraphrase) leg is not exercised.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from core.plain_task_routing import plain_task_kind

REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("_memory_evidence_contract_driver.py")
CHAT = "allotment-log"
SEED = [
    {"chat": CHAT, "stated": "2025-02-01", "assistant": "Nice, PantryPal sounds fun.",
     "user": "My project is PantryPal, a recipe-sharing app I'm building with a Flask backend and a React frontend."},
    {"chat": CHAT, "stated": "2025-02-10", "assistant": "Grouping by aisle is handy.",
     "user": "For my project I added a shopping-list feature that groups ingredients by aisle."},
    {"chat": CHAT, "stated": "2025-02-20", "assistant": "A June beta is a clear target.",
     "user": "My goal for PantryPal is a beta with 50 home cooks by June."},
]
SEARCH_PHRASES = '["building an app", "my project", "working on", "app I am making"]'
ANSWER = "PantryPal: a recipe-sharing app (Flask and React) with an aisle-grouped shopping list, aiming for a 50-cook beta by June."


@pytest.mark.parametrize("text,kind", [
    ("Summarize my project", ""),
    ("Explain what I'm building", ""),
    ("Summarize my notes: the meeting covered budget cuts and the new hire plan for the spring quarter.", "summary"),
    ("Summarize the French revolution in three sentences.", "summary"),
    ("Explain how a hash table works", "explanation"),
], ids=["own-project", "own-work", "own-notes-pasted", "public-topic", "public-explain"])
def test_a_text_task_about_the_speakers_own_things_without_material_is_not_one_shot(text, kind):
    assert plain_task_kind(text) == kind


def test_summarize_my_project_reaches_the_users_records(tmp_path):
    work = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / "my-project"
    home = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"my-project-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    (work / "seed.json").write_text(json.dumps(SEED))
    (work / "turns.json").write_text(json.dumps({"turns": [
        {"id": "summary", "question": "Summarize my project", "scripted": ANSWER, "reader_require": ["PantryPal"],
         # The memory search-phrase call, answered as a model would for this question.
         "system_replies": [["You help search a stored record", SEARCH_PHRASES]]},
    ]}))
    out = work / "served.json"
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--repo-root", str(repo), "--home", str(home), "--out", str(out),
         "--turns-json", str(work / "turns.json"), "--seed-json", str(work / "seed.json")],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    turn = json.loads(out.read_text())["turns"][0]
    assert "PantryPal" in turn["admitted_capsule"], turn["routes"]
    assert str(turn["delivered"]).startswith(ANSWER), turn["delivered"]
