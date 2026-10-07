"""A message the clause decomposition reads as ONE request is answered as one, never re-split by the demand cut.

Measured on the live agent-team comparison (2026-10-07, counted run t1_shop single): the turn's clause decomposition
grouped all four clauses of the prompt below into one workspace investigation, and the demand-unit executor split the
turn anyway at "For each file". The audit received "... and orders.py. For each file"; the rest went to a separate
sub-turn, which answered "You haven't told me which code to review". Served path, real lanes, the run's three fixture
files in the workspace; the model transport replays the run's recorded decomposition reply verbatim.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("_memory_evidence_contract_driver.py")
PROMPT = (
    "Review the three Python files in this folder: pricing.py, stock.py and orders.py. For each file, find the single "
    "most important bug and report it as `file:line — what is wrong — the fix` (one line per file). Read the code; do "
    "not change any file. Split the work across sub-agents if you can (one file each)."
)
# The run's recorded clause-decomposition reply (ledger, t1_shop-vool-single-c1, call 6).
DECOMPOSITION = ('{"requests": [{"request": "", "source_clause_ids": ["clause-1", "clause-2", "clause-3", "clause-4"], '
                 '"operation": "workspace_investigation", "depends_on": []}]}')
FILES = {
    "stock.py": "STOCK = {}\n\n\ndef can_reserve(sku, qty):\n    return STOCK.get(sku, 0) > qty\n",
    "orders.py": "def cancel_line(order, sku, qty):\n    order[sku] = order.get(sku, 0) - qty\n",
    "pricing.py": "def total(prices, coupon=0):\n    return sum(prices) - coupon - coupon\n",
}


def test_the_grouped_request_reaches_no_split_sub_turn(tmp_path):
    work = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / "one-grouped-request"
    home = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"grouped-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    for name, body in FILES.items():
        (home / name).write_text(body)
    (work / "seed.json").write_text("[]")
    (work / "turns.json").write_text(json.dumps({"turns": [{
        "id": "shop", "question": PROMPT, "decomposition": DECOMPOSITION,
        "scripted": "orders.py:2 — cancelling never releases stock — release the cancelled quantity.",
    }]}))
    out = work / "served.json"
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    result = subprocess.run(
        [sys.executable, str(DRIVER), "--repo-root", str(repo), "--home", str(home), "--out", str(out),
         "--turns-json", str(work / "turns.json"), "--seed-json", str(work / "seed.json")],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    turn = json.loads(out.read_text())["turns"][0]
    asked = [
        [m["content"] for m in call["payload"]["messages"] if m["role"] == "user"][-1]
        for call in turn["request_calls"] if call.get("payload") and call.get("call_purpose") == "answer"
    ]
    fragments = [text for text in asked if text.strip() and PROMPT not in text and text.strip() in PROMPT]
    assert not fragments, {"split_sub_turn_requests": fragments}
    assert "which code" not in str(turn["delivered"]).lower(), turn["delivered"]
