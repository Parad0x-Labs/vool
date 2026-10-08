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

import pytest

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


# The second counted run of the same shape (ledger, t2_auth-vool-single-c1): the auth review prompt, its three
# fixture files and its recorded decomposition reply, one grouped workspace investigation over all four clauses.
# t3_pipeline is not a case here: its recorded reply (t3_pipeline-vool-single-c1) is TWO requests (a workspace
# investigation over clauses 1, 3, 4 and a conclusion over clauses 2, 4), not one grouped request.
PROMPT_T2 = (
    "Review the three Python files in this folder: tokens.py, password.py and session.py. For each file, find the "
    "single most important bug and report it as `file:line — what is wrong — the fix` (one line per file). Read the "
    "code; do not change any file. Split the work across sub-agents if you can (one file each)."
)
DECOMPOSITION_T2 = ('{"requests":[{"request":"","source_clause_ids":["clause-1","clause-2","clause-3","clause-4"],'
                    '"operation":"workspace_investigation","depends_on":[]}]}')
FILES_T2 = {
    "tokens.py": "\"\"\"Signed access tokens.\"\"\"\n\nimport base64\nimport hashlib\nimport hmac\nimport json\nimport time\n\nSECRET = b\"dev-only-secret\"\nTTL_MS = 15 * 60 * 1000\n\n\ndef issue(user_id: str, now_ms: int | None = None) -> str:\n    now_ms = int(time.time() * 1000) if now_ms is None else now_ms\n    body = json.dumps({\"sub\": user_id, \"exp\": now_ms + TTL_MS}).encode()\n    sig = hmac.new(SECRET, body, hashlib.sha256).hexdigest()\n    return base64.urlsafe_b64encode(body).decode() + \".\" + sig\n\n\ndef verify(token: str) -> str:\n    raw, _, sig = token.partition(\".\")\n    body = base64.urlsafe_b64decode(raw.encode())\n    expected = hmac.new(SECRET, body, hashlib.sha256).hexdigest()\n    if not hmac.compare_digest(sig, expected):\n        raise PermissionError(\"bad signature\")\n    claims = json.loads(body)\n    if claims[\"exp\"] < time.time():\n        raise PermissionError(\"expired\")\n    return claims[\"sub\"]\n",
    "password.py": "\"\"\"Password hashing and checking.\"\"\"\n\nimport hashlib\nimport os\n\nITERATIONS = 200_000\n\n\ndef hash_password(password: str, salt: bytes | None = None) -> str:\n    salt = os.urandom(16) if salt is None else salt\n    digest = hashlib.pbkdf2_hmac(\"sha256\", password.encode(), salt, ITERATIONS)\n    return salt.hex() + \"$\" + digest.hex()\n\n\ndef check_password(password: str, stored: str) -> bool:\n    salt_hex, _, digest_hex = stored.partition(\"$\")\n    salt = bytes.fromhex(salt_hex)\n    candidate = hashlib.pbkdf2_hmac(\"sha256\", password.encode(), salt, ITERATIONS)\n    return candidate.hex() == digest_hex\n\n\ndef needs_rehash(stored: str) -> bool:\n    return \"$\" not in stored\n",
    "session.py": "\"\"\"Server-side sessions.\"\"\"\n\nimport random\nimport string\nimport time\n\nSESSION_TTL = 3600\n_sessions: dict[str, dict] = {}\n\n\ndef new_session_id(length: int = 32) -> str:\n    alphabet = string.ascii_letters + string.digits\n    return \"\".join(random.choice(alphabet) for _ in range(length))\n\n\ndef open_session(user_id: str) -> str:\n    sid = new_session_id()\n    _sessions[sid] = {\"user\": user_id, \"created\": time.time()}\n    return sid\n\n\ndef lookup(sid: str) -> str | None:\n    row = _sessions.get(sid)\n    if row is None or time.time() - row[\"created\"] > SESSION_TTL:\n        _sessions.pop(sid, None)\n        return None\n    return row[\"user\"]\n\n\ndef close(sid: str) -> None:\n    _sessions.pop(sid, None)\n"
}

CASES = {
    "t1_shop": (PROMPT, DECOMPOSITION, FILES,
                "orders.py:2 — cancelling never releases stock — release the cancelled quantity."),
    "t2_auth": (PROMPT_T2, DECOMPOSITION_T2, FILES_T2,
                "tokens.py:27 — exp is milliseconds but compared to time.time() seconds — compare to time.time() * 1000."),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_grouped_request_reaches_no_split_sub_turn(tmp_path, case):
    prompt, decomposition, files, scripted = CASES[case]
    work = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / f"one-grouped-request-{case}"
    home = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"grouped-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (home / name).write_text(body)
    (work / "seed.json").write_text("[]")
    (work / "turns.json").write_text(json.dumps({"turns": [{
        "id": case, "question": prompt, "decomposition": decomposition, "scripted": scripted,
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
    fragments = [text for text in asked if text.strip() and prompt not in text and text.strip() in prompt]
    assert not fragments, {"split_sub_turn_requests": fragments}
    assert "which code" not in str(turn["delivered"]).lower(), turn["delivered"]
