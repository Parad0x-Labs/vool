"""Real agent programs for the agent-team tests (run as separate processes, never imported).

Each one talks to the coordinator only the way any agent can: the status file, the result file
and its exit code, all named by the environment the coordinator gives it.

    python agent_team_agent.py own_writer <dir> <steps> <interval>
    python agent_team_agent.py grandchild_writer <target> <seconds> [scrub]
    python agent_team_agent.py sleeper <seconds>
    python agent_team_agent.py liar <file>
    python agent_team_agent.py ask <question>
    python agent_team_agent.py hook_writer <hook path> <seconds>
    python agent_team_agent.py fail <code>
    python agent_team_agent.py big_result <words>
    python agent_team_agent.py silent_writer <file>
    python agent_team_agent.py env_dump <file> [repo]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def status(done: int, planned: int) -> None:
    path = os.environ.get("VOOL_AGENT_STATUS")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"done": done, "planned": planned}) + "\n")


def result(**payload) -> None:
    path = os.environ.get("VOOL_AGENT_RESULT")
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)


def own_writer(directory: str, steps: str, interval: str) -> None:
    os.makedirs(directory, exist_ok=True)
    changed = []
    for i in range(int(steps)):
        name = os.path.join(directory, f"step{i}.txt")
        with open(name, "w", encoding="utf-8") as fh:
            fh.write(f"step {i}\n")
        changed.append(os.path.relpath(name, os.environ.get("AGENT_TEAM_WORKSPACE", ".")))
        status(i + 1, int(steps))
        time.sleep(float(interval))
    result(status="done", summary=f"wrote {steps} files", changed=changed)


_DAEMON = r'''
import os, sys, time
target, seconds = sys.argv[1], float(sys.argv[2])
if os.fork():
    os._exit(0)
os.setsid()
if os.fork():
    os._exit(0)
os.chdir("/")
end = time.time() + seconds
with open(target, "a", encoding="utf-8") as fh:
    while time.time() < end:
        fh.write("daemon line\n")
        fh.flush()
        time.sleep(0.1)
'''


def grandchild_writer(target: str, seconds: str, scrub: str = "") -> None:
    """agent -> sh -> python -> double-forked setsid daemon that appends to ``target``."""
    py = sys.executable
    inner = f"{py} -c '{_DAEMON}' {target} {seconds}"
    if scrub:
        inner = f"env -i {inner}"
    subprocess.run(["/bin/sh", "-c", inner], check=True)
    status(1, 2)
    # Counted in steps, not one long sleep: a frozen process makes no progress, so a resume
    # continues the remaining work instead of finding its deadline already passed.
    for _ in range(int((float(seconds) + 2) / 0.25)):
        time.sleep(0.25)
    status(2, 2)
    result(status="done", summary="ran my test helper", changed=[])


def sleeper(seconds: str) -> None:
    status(0, 1)
    time.sleep(float(seconds))
    status(1, 1)
    result(status="done", summary="slept", changed=[])


def liar(path: str) -> None:
    result(status="done", summary=f"I fixed the bug and saved {path}.", changed=[path])


def ask(question: str) -> None:
    decision = os.environ.get("VOOL_AGENT_DECISION", "")
    if decision:
        result(status="done", summary=f"went ahead with: {decision}", changed=[])
        return
    result(status="needs_decision", question=question, options=["yes", "no"], summary="waiting for a decision")


def hook_writer(hook: str, seconds: str) -> None:
    os.makedirs(os.path.dirname(hook), exist_ok=True)
    with open(hook, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\necho hooked\n")
    time.sleep(float(seconds))
    result(status="done", summary="set up the hook", changed=[])


def fail(code: str) -> None:
    result(status="done", summary="All done, everything passed.", changed=[])
    sys.exit(int(code))


def silent_writer(path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("changed\n")
    result(status="done", summary="looked around, changed nothing", changed=[])


def env_dump(path: str, repo: str = "") -> None:
    lines = [f"{k}={v}" for k, v in sorted(os.environ.items())]
    if repo:
        push = subprocess.run(["git", "-C", repo, "remote", "get-url", "--push", "origin"],
                              capture_output=True, text=True).stdout.strip()
        lines.append(f"PUSH_URL={push}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    result(status="done", summary="dumped my environment", changed=[os.path.basename(path)])


def big_result(words: str) -> None:
    result(status="done", summary=" ".join(f"word{i}" for i in range(int(words))), changed=[])


if __name__ == "__main__":
    globals()[sys.argv[1]](*sys.argv[2:])
