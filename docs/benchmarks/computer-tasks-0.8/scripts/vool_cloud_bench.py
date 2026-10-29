#!/usr/bin/env python3
"""Bench-only adapter (not product code, not committed): VOOL's clean loop and its tools called directly, with a brain
on OpenRouter z-ai/glm-5.3-flashx through the local or_proxy.py (same key path, ledger and $ cap as the rivals' cloud
rows). VOOL's routing and answer gates are NOT in this row.

Run with the daemon venv python from the fix14 tree:
  cd <fix14-repo> && HOME=<clone> <venv>/bin/python vool_cloud_bench.py --ask-json '<one ask>' --out <row.json>
run_h2h.py --system voolcloud drives it once per ask (fresh home clone each).
"""
import argparse, json, os, sys, time, urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--ask-json", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--proxy", default="http://127.0.0.1:18765/v1")
ap.add_argument("--cap-s", type=float, default=300.0)
a = ap.parse_args()
sys.path.insert(0, os.getcwd())
from core.clean_loop.loop import run_loop  # noqa: E402
from core.clean_loop.registry import ToolContext, default_registry  # noqa: E402

ask = json.loads(a.ask_json)
start = time.time()
calls = {"n": 0}


def brain(messages):
    if time.time() - start > a.cap_s:
        raise TimeoutError("per-ask cap")
    calls["n"] += 1
    body = {"model": "z-ai/glm-5.3-flashx", "messages": messages, "temperature": 0, "max_tokens": 700}
    req = urllib.request.Request(a.proxy + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer bench-loopback",
                                  "X-H2H-System": "voolcloud"})
    left = max(5.0, a.cap_s - (time.time() - start))
    data = json.loads(urllib.request.urlopen(req, timeout=left).read())
    return data["choices"][0]["message"].get("content") or ""


history, replies, err = [], [], ""
for turn in ask["turns"]:
    try:
        r = run_loop(turn, brain=brain, registry=default_registry(), ctx=ToolContext(home=Path(os.environ["HOME"])), history=history)
        replies.append(r.answer)
        history += [{"role": "user", "content": turn}, {"role": "assistant", "content": r.answer}]
    except Exception as e:  # a timeout or a provider error is recorded, never retried here
        replies.append(""); err = f"{type(e).__name__}: {str(e)[:200]}"
        break
json.dump({"replies": replies, "wall_s": round(time.time() - start, 1), "model_calls": calls["n"], "error": err},
          open(a.out, "w"))
