"""Overnight run against the APP runtime, with live findings written as it goes.

Drives the sets the operator named -- travel/planning, typo tolerance, and context retention across
a 50-turn conversation -- against the daemon the shipped app is actually serving (11435), not a
scratch daemon. What the app does is the only thing that counts here.

Writes FINDINGS.md after every single turn, so a run that is interrupted still leaves everything it
learned. That is deliberate: this exists to work unattended, and a report that only appears at the
end is a report that can be lost.

Context recall is graded DETERMINISTICALLY against the fact planted earlier in the same thread --
`expect` carries the token that must appear. No prose judgement, no model grading.

usage: python overnight.py <run-name> <set.json> [set2.json ...]
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:11435"          # the app's own runtime
HERE = Path(__file__).resolve().parent


def log(msg: str) -> None:
    print(f"[night] {msg}", flush=True)


def ask(prompt: str, *, session: str, turn: str, history: list[dict]) -> dict:
    import os
    _pin = os.environ.get("HARNESS_CLOUD_MODEL", "")
    body = {
        **({"model": _pin, "model_selection": "pin"} if _pin else {"model_selection": "auto"}),
        "messages": [*history, {"role": "user", "content": prompt}],
        "stream": False, "session_id": session, "turn_id": turn,
        "mode": "auto", "autonomy": "auto",
    }
    request = urllib.request.Request(
        BASE + "/api/chat", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        answer = (payload.get("message") or {}).get("content") or payload.get("response") or ""
        error = ""
    except urllib.error.HTTPError as exc:
        answer, error, payload = "", f"HTTP {exc.code}", {}
    except Exception as exc:
        answer, error, payload = "", f"{type(exc).__name__}: {exc}", {}
    prov = (((payload.get("vool_response_commit") or {}).get("display_metadata") or {}).get("provenance")) or {}
    return {
        "answer": answer, "error": error, "seconds": round(time.time() - started, 1),
        "route": prov.get("route"), "model_ran": prov.get("model_ran"),
        "participating_models": prov.get("participating_models"),
    }


def smells(record: dict) -> list[str]:
    """Failure shapes each already measured in this project. Cheap, and they accumulate."""
    flags = []
    answer = str(record.get("answer") or "")
    low = answer.lower()
    if record.get("error"):
        flags.append(f"TRANSPORT {record['error']}")
    if not answer.strip():
        flags.append("EMPTY ANSWER")
    for leak in ("runtime error", "traceback", "were dictionaries", "nonetype",
                 "the sources provided", "i cannot browse", "as an ai"):
        if leak in low:
            flags.append(f"INTERNAL LEAK {leak!r}")
    if "could not be answered" in low:
        flags.append("PARTIAL: something in the turn was dropped")
    if "no current weather results" in low or "unable to fetch" in low:
        flags.append("RETRIEVAL EMPTY")
    if re.search(r"\bhttps?://\S+", answer) and not re.search(r"\[[^\]]+\]\(https?://", answer):
        flags.append("RAW URL, operator asked for [Name](url)")
    if float(record.get("seconds") or 0) > 90:
        flags.append(f"SLOW {record['seconds']}s")
    return flags


def graded(case: dict, answer: str) -> tuple[str, str]:
    """Deterministic only. A recall probe carries the token that must come back."""
    want = str(case.get("expect") or "")
    if want in ("", "conversational") or want.startswith(("real prices", "see ")):
        return "CHECK", "no deterministic oracle"
    tokens = [t.strip() for t in want.split("/") if t.strip()]
    low = str(answer or "").lower()
    missing = [t for t in tokens if t.lower() not in low]
    if not missing:
        return "PASS", f"recalled {tokens}"
    return "FAIL", f"missing {missing}"


def main() -> None:
    name, paths = sys.argv[1], [Path(p) for p in sys.argv[2:]]
    out_dir = HERE / f"run-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    findings = out_dir / "FINDINGS.md"
    findings.write_text(f"# Overnight run: {name}\n\nAgainst the APP runtime on {BASE}.\n", encoding="utf-8")

    totals = {"PASS": 0, "FAIL": 0, "CHECK": 0}
    for path in paths:
        cases = json.loads(path.read_text(encoding="utf-8"))
        tag = path.stem
        log(f"--- {tag}: {len(cases)} turns ---")
        results, threads = [], {}
        for index, case in enumerate(cases, 1):
            thread = case.get("thread")
            if thread:
                session = f"openclaw:{tag[:6]}{thread}".ljust(29, "0")[:29]
                history = threads.setdefault(thread, [])
            else:
                session = f"openclaw:{tag[:6]}{index:03d}".ljust(29, "0")[:29]
                history = []
            outcome = ask(case["prompt"], session=session, turn=f"{tag}-{case['id']}", history=history)
            if thread:
                history.append({"role": "user", "content": case["prompt"]})
                history.append({"role": "assistant", "content": outcome["answer"]})
            verdict, note = graded(case, outcome["answer"])
            totals[verdict] += 1
            flags = smells({**case, **outcome})
            record = {**case, **outcome, "verdict": verdict, "note": note, "flags": flags}
            results.append(record)

            (out_dir / f"{tag}.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
            with findings.open("a", encoding="utf-8") as fh:
                fh.write(f"\n### {case['id']} [{verdict}] {case.get('lane','')}\n")
                fh.write(f"- route `{outcome.get('route')}` · {outcome.get('seconds')}s · {note}\n")
                fh.write(f"- Q: {case['prompt'][:160]}\n")
                fh.write(f"- A: {' '.join(str(outcome['answer']).split())[:320]}\n")
                for f in flags:
                    fh.write(f"- **{f}**\n")

            mark = {"PASS": "ok  ", "FAIL": "FAIL", "CHECK": "    "}[verdict]
            shown = " ".join(str(outcome["answer"]).split())[:56]
            extra = ("  <<< " + "; ".join(flags)[:54]) if flags else ""
            print(f"  {mark} {case['id']:6} {outcome['seconds']:6.1f}s {shown!r}{extra}", flush=True)
        log(f"--- {tag} done ---")

    with findings.open("a", encoding="utf-8") as fh:
        fh.write(f"\n\n## Totals\nPASS={totals['PASS']} FAIL={totals['FAIL']} CHECK={totals['CHECK']}\n")
    log(f"COMPLETE  PASS={totals['PASS']} FAIL={totals['FAIL']} CHECK={totals['CHECK']} -> {findings}")


if __name__ == "__main__":
    main()
