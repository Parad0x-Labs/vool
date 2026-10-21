"""A forget and a finalization erase, run at the same time, never deadlock (forget-locks2 REVIEW-1, E2).

A forget holds the forget fence (`_command_erasure_fence`) and erases the chat's finalizations
inside it: `run_history._erase_finalizations` -> `erase_finalization_payload` -> `set_availability`,
which takes `finalization.A8_TRAVERSAL_LOCK`. A chat delete (`delete_conversation_session` ->
`erase_finalizations_for_session`) or any other finalization erase runs `_run_derivative_sweep`,
which held `A8_TRAVERSAL_LOCK` and then entered the fence in its memory step
(`sweep_governed_memory_rows` -> `_ensure_memory_files` -> `initialize_memory_home`).

Both locks are RLocks, which no other thread can release, so a deadlock here cannot be broken from
the test and would wedge every later test in the session. Each case therefore runs in its own
child process: two threads joined against a deadline, and the child's own timeout as a backstop.
The erase is held for a second in its memory step (A8 held) while the forget side starts; the
forget side is exactly what the command fence wraps around `run_history._erase_finalizations`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEADLINE_S = 20.0

_CHILD = r'''
import json, os, sys, threading, time
from pathlib import Path
case, home = sys.argv[1], sys.argv[2]
os.environ.update(VOOL_HOME=home, VOOL_WORKSPACE_ROOT=os.path.join(home, "workspace"))
from core import embedding_service
embedding_service._best_embed_model = lambda: None
from core import finalization as fz
from core.memory import entries, revocation, run_history
from tests.foundation.test_a8_privacy_pass004 import _finalize
from tests.test_question_date_time_leg_20261002 import _ingest, _ts

a, b = "openclaw:" + "a" * 20, "openclaw:" + "b" * 20
_ingest(Path(home), a, [(_ts("2026-03-04T09:00:00"), "hello")])
f1 = _finalize("Answer one about the north gate.", request_id="req-1", turn_id="t-1")
f2 = _finalize("Answer two about the locker.", request_id="req-2", turn_id="t-2")
fid1, fid2 = f1["finalization_id"], f2["finalization_id"]
if case == "chat_delete":
    entries._ensure_memory_files()
    with open(entries.conversation_log_path(), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"session_id": b, "request_id": "req-1", "user": "When does the gate open?",
                                 "assistant": "Answer one about the north gate."}) + "\n")
held = threading.Event()
real = fz._sweep_step_memory_jsonl
def memory_step(request_id):
    if threading.current_thread().name == "eraser" and not held.is_set():
        held.set(); time.sleep(1.0)   # A8_TRAVERSAL_LOCK is held here
    return real(request_id)
fz._sweep_step_memory_jsonl = memory_step
out = {}
def erase():
    try:
        if case == "chat_delete":
            out["erase"] = bool(entries.delete_conversation_session(b))
        else:
            out["erase"] = bool(fz.erase_finalization_payload(fid1).get("sweep_complete"))
    except BaseException as exc:
        out["erase_error"] = repr(exc)
def forget():
    held.wait(10)
    try:
        with revocation.serialized():   # what _command_erasure_fence holds around the forget
            run_history._erase_finalizations([{"finalization_id": fid2}])
        out["forget"] = True
    except BaseException as exc:
        out["forget_error"] = repr(exc)
threads = [threading.Thread(target=erase, name="eraser", daemon=True),
           threading.Thread(target=forget, name="forget", daemon=True)]
for t in threads: t.start()
end = time.monotonic() + float(sys.argv[3])
for t in threads: t.join(max(0.0, end - time.monotonic()))
out["held"] = held.is_set()
out["hung"] = [t.name for t in threads if t.is_alive()]
print("RESULT " + json.dumps(out), flush=True)
os._exit(0)
'''


@pytest.mark.parametrize("case", ["chat_delete", "finalization_erase"])
def test_a_forget_and_a_finalization_erase_never_deadlock(tmp_path, case):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        child = subprocess.run([sys.executable, "-c", _CHILD, case, str(tmp_path / "home"), str(DEADLINE_S)],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=DEADLINE_S + 90)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{case}: the child process never finished", pytrace=False)
    line = next((l for l in child.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, (child.returncode, child.stdout[-2000:], child.stderr[-4000:])
    out = json.loads(line[len("RESULT "):])
    if out["hung"]:
        pytest.fail(f"{case}: {', '.join(out['hung'])} still running after {DEADLINE_S:.0f}s: a lock-order deadlock "
                    "between the forget fence and A8_TRAVERSAL_LOCK", pytrace=False)
    assert out["held"], "the erase never reached its memory step under A8_TRAVERSAL_LOCK"
    assert out.get("erase") is True and out.get("forget") is True, out
