"""RED-H — two adjudications against the live repair build (2b2f9e51).

ANOMALY 1 — same-chat recall of a value the runtime itself served.
  Observed by the lead in chat `fab-runA`: turn 1 served `Rome, Italy: Partly cloudy, 27.7C`,
  and a later turn in the SAME chat asking `what was the rome temperature again?` came back
  `The current temperature in Rome is not available without a lookup.`

  The discriminator this probe exists for: does the refusal depend on the REFUSED-SLOT REGISTER
  being armed (i.e. on this branch's BLUE-3 work), or does it happen in a chat where the register
  is provably empty? A chat whose turn 1 asks ONE thing and gets it answered records no
  `unanswered` slot, so `core.refused_slot_register.refused_slots_for_session` returns () and the
  C12 contract cannot fire. If recall is refused THERE TOO, the branch did not cause it.

  Arms A/B/C are therefore a controlled pair plus a phrasing control, not a demonstration.

ANOMALY 2 — literal-output contract as a silent-drop hole.
  `core/finalization.py:292` withholds the RSS disclosure rows whenever the turn is under a
  literal-output contract (`core.raw_output_contract.parse_raw_output_contract`) or was served by
  a stipulated-contract lane. That fix was for `Only the list.` / `PNG - ISO/IEC 15948:2004.`,
  where appended rows violate the contract in the served bytes.

  The question: does a turn that is under such a contract AND genuinely drops a requested slot now
  drop it SILENTLY? Arms D/E are the matched pair -- the SAME two-slot request, with and without
  the contract clause. If D (no contract) discloses the dropped slot and E (contract) serves a body
  that neither answers it nor mentions it, the fix re-opened the original defect inside the
  contract's scope.

NO EXPECTED ANSWERS ANYWHERE. Every verdict is read off the runtime's own bytes: whether its own
disclosure header is present, and whether the slot's own anchor tokens appear anywhere in the body.
The instrument does not know what the Baltic temperature is and never compares to one.

USAGE
-----
    cd /Users/example-user/vool/worktrees/rss-repair-20260829
    PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -u \
        tests/red_h_recall_and_literal_holes.py --base-url http://127.0.0.1:11437 \
        --out /tmp/red_h.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

AUDIT_PREFIX = "audit-final-anomalies-"

FOUR_PART = (
    "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in Rome, "
    "and what is the water temperature in the Baltic Sea?"
)
ROME_ONLY = "What is the weather in Rome?"
# The decisive shape for anomaly 1: a turn whose Rome leg IS served while another slot is
# refused, so the chat history carries BOTH a real Rome reading and the branch's refusal prose.
ROME_PLUS_BALTIC = (
    "What is the weather in Rome, and what is the water temperature in the Baltic Sea?"
)
RECALL = "what was the rome temperature again?"
RECALL_ALT = "what temperature did you just tell me for Rome?"

# Anomaly 2: the SAME two-slot demand, with and without a literal-output clause.
TWO_SLOT_PLAIN = (
    "What is 1000 EUR to RUB, and what is the water temperature in the Baltic Sea?"
)
TWO_SLOT_LITERAL = (
    "What is 1000 EUR to RUB, and what is the water temperature in the Baltic Sea? "
    "Answer with JSON only."
)
# NOTE: the bare "Only the list." phrasing BLUE-1's report quotes does NOT arm
# `core.finalization._under_literal_output_contract` on this composite request -- measured, see
# the report. These two DO, so the arms actually exercise the gate they are aimed at.
TWO_SLOT_ONLYLIST = (
    "What is 1000 EUR to RUB, and what is the water temperature in the Baltic Sea? "
    "Output only the list, nothing else."
)
TWO_SLOT_NUMBERS = (
    "What is 1000 EUR to RUB, and what is the water temperature in the Baltic Sea? "
    "Answer with just the numbers, nothing else."
)

#: The runtime's own disclosure header vocabulary. Read from the module that owns it when the
#: import is available, so this instrument cannot drift from what the runtime actually renders.
def _disclosure_headers() -> tuple[str, ...]:
    found: list[str] = []
    try:
        from core.conductor.obligation_ledger import RSS_UNAVAILABLE_HEADER

        found.append(str(RSS_UNAVAILABLE_HEADER).strip().rstrip(":").lower())
    except Exception:
        pass
    try:
        from core.finalization import RSS_UNAVAILABLE_HEADER as _H2

        found.append(str(_H2).strip().rstrip(":").lower())
    except Exception:
        pass
    for fallback in ("could not be answered", "not answered", "unavailable"):
        if fallback not in found:
            found.append(fallback)
    return tuple(found)


_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def send(base_url: str, prompt: str, chat_id: str, timeout: float) -> dict[str, Any]:
    assert chat_id.startswith(AUDIT_PREFIX), f"probe chat_id must carry {AUDIT_PREFIX}"
    payload = json.dumps(
        {"chat_id": chat_id, "messages": [{"role": "user", "content": prompt}], "stream": False}
    ).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"prompt": prompt, "body": f"<TRANSPORT ERROR: {exc}>", "verdict": {}, "secs": time.time() - t0}
    body = str(((data.get("message") or {}).get("content")) or "")
    commit = data.get("vool_response_commit") or {}
    return {
        "prompt": prompt,
        "chat_id": chat_id,
        "body": body,
        "verdict": commit.get("closure_verdict") or {},
        "commit_keys": sorted(commit.keys()) if isinstance(commit, dict) else [],
        "top_keys": sorted(data.keys()),
        "secs": round(time.time() - t0, 1),
    }


def has_disclosure(body: str) -> bool:
    low = body.lower()
    return any(h in low for h in _disclosure_headers())


def mentions(body: str, *tokens: str) -> bool:
    """Whether the body names ALL of these tokens (case-folded whole words)."""
    have = {m.group(0).lower() for m in _TOKEN_RE.finditer(body)}
    return all(t.lower() in have for t in tokens)


def carries_number(body: str, *near: str) -> bool:
    """A quantity on a line that names every one of `near`. Line-scoped, not whole-body."""
    for line in body.splitlines():
        if not mentions(line, *near):
            continue
        if _NUM_RE.search(line):
            return True
    return False


def drive(base_url: str, tag: str, prompts: list[str], timeout: float) -> dict[str, Any]:
    cid = AUDIT_PREFIX + tag + "-" + time.strftime("%H%M%S")
    turns = []
    print(f"\n================ chat {tag}  ({cid}) ================")
    for prompt in prompts:
        rec = send(base_url, prompt, cid, timeout)
        turns.append(rec)
        print(f"\n  >>> {prompt}")
        for line in rec["body"].splitlines():
            print(f"      | {line[:160]}")
        print(f"      cert: {json.dumps(rec['verdict'])}   ({rec['secs']}s)")
    return {"tag": tag, "chat_id": cid, "turns": turns}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:11437")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="", help="comma-separated arm letters, e.g. A,D,E")
    args = ap.parse_args()

    arms: dict[str, tuple[str, list[str]]] = {
        # ---- Anomaly 1
        "A": ("A1-repro-register-armed", [FOUR_PART, RECALL]),
        "B": ("A1-control-register-empty", [ROME_ONLY, RECALL]),
        "C": ("A1-control-phrasing", [ROME_ONLY, RECALL_ALT]),
        # ---- Anomaly 2
        "D": ("A2-control-no-contract", [TWO_SLOT_PLAIN]),
        "E": ("A2-literal-json-only", [TWO_SLOT_LITERAL]),
        "F": ("A2-literal-only-the-list", [TWO_SLOT_ONLYLIST]),
        "G": ("A2-literal-just-the-numbers", [TWO_SLOT_NUMBERS]),
        # H replays the lead's FULL fab-runA sequence, follow-ups included. Arm A skipped the two
        # follow-ups; H is the test of whether the branch's own refusal PROSE, carried in the
        # chat history, is what steers the recall turn into declining a value the runtime served.
        # J/K isolate WHICH text primes the refusal: J has the follow-up notice in history,
        # K has only turn 1's disclosure rows. Both require turn 1 to have SERVED Rome.
        "J": ("A1-rome-served-plus-followup-notice",
              [ROME_PLUS_BALTIC, "why did that fail?", RECALL]),
        "K": ("A1-rome-served-disclosure-only", [ROME_PLUS_BALTIC, RECALL]),
        # L is the closest attainable replay of fab-runA: turn 1 must SERVE Rome while a second
        # slot is refused, then both follow-ups run, then the recall. This is the only shape in
        # which the lead's observation is a defect rather than a correct refusal.
        "L": ("A1-rome-served-full-followup-chain",
              ["What is the weather in Rome, and what is the half-life of caesium-137?",
               "why did that fail?", "retry that exact failed request", RECALL]),
        # M/N attribute arm L's refusal. Same turn 1 (Rome SERVED, sourced) in all three.
        # M: recall immediately -- no follow-up prose in history at all.
        # N: two neutral turns between, so turn DISTANCE is held constant against M without
        #    putting any refusal notice into the history.
        "M": ("A1-rome-served-immediate-recall", ["What is the weather in Rome, and what is the half-life of caesium-137?", RECALL]),
        "N": ("A1-rome-served-neutral-gap", ["What is the weather in Rome, and what is the half-life of caesium-137?",
              "thanks", "ok cool", RECALL]),
        # P is arm L with ONE token changed: "again" -> "earlier". `is_same_chat_history_recall`
        # is False for the first and True for the second, so if P recalls correctly where L
        # refused, the cause is the recall RECOGNIZER's vocabulary, not this branch.
        "P": ("A1-recognizer-discriminator-earlier",
              ["What is the weather in Rome, and what is the half-life of caesium-137?",
               "why did that fail?", "retry that exact failed request",
               "what was the rome temperature earlier?"]),
        # R isolates whether BRANCH-NEW refusal prose is NECESSARY for the recall failure.
        # History carries only PRE-EXISTING failure prose ("The retry could not be executed."),
        # never the sweep's disclosure block or the branch's follow-up notice.
        "R": ("A1-preexisting-prose-only",
              ["What is the weather in Rome?",
               "retry that exact failed request", RECALL]),
        "H": ("A1-repro-full-fabrunA-sequence",
              [FOUR_PART, "why did that fail?", "retry that exact failed request", RECALL]),
    }
    wanted = [a.strip().upper() for a in args.only.split(",") if a.strip()] or list(arms)

    results: dict[str, Any] = {"base_url": args.base_url, "arms": {}}
    for letter in wanted:
        if letter not in arms:
            continue
        tag, prompts = arms[letter]
        results["arms"][letter] = drive(args.base_url, tag, prompts, args.timeout)

    print("\n\n================ READINGS ================")
    for letter, run in results["arms"].items():
        turns = run["turns"]
        last = turns[-1]["body"]
        row: dict[str, Any] = {
            "arm": run["tag"],
            "chat_id": run["chat_id"],
            "final_has_disclosure_header": has_disclosure(last),
            "final_names_rome": mentions(last, "rome"),
            "final_carries_number_near_rome": carries_number(last, "rome"),
        }
        if letter in ("D", "E", "F", "G"):
            row["body_answers_baltic_number"] = carries_number(last, "baltic")
            row["body_names_baltic_at_all"] = mentions(last, "baltic")
            row["body_carries_fx_number"] = carries_number(last, "rub") or carries_number(last, "eur")
        results["arms"][letter]["readings"] = row
        print(json.dumps(row, indent=2))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
