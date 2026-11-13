"""Count the paid model calls each chat turn made, and flag a turn that paid twice.

A paid-pinned file ask goes to the clean loop first (cloud brain on by default). When the model
answers without reading anything, the loop's answer is declined and the turn pays again on the
tool lane: two clean_loop_step calls, then tool_intent and answer_generation (staging 23a5bf35,
UsePod test double: 4 paid calls for one turn). Known cost for v0.8; this check shows whether real
models trigger it.

Read-only by default: it reads /api/runtime/events for the sessions you name and spends nothing.
``--ask`` sends one chat turn on ``--model`` first, which SPENDS on a paid model: run it only with
the owner's spend OK.

    python3 ops/paid_calls_per_turn.py --session <chat session id> [--session ...]
    python3 ops/paid_calls_per_turn.py --model <paid model id> --ask    # one fresh paid-pinned file ask

Prints one JSON object. Exit 0, or 3 with --fail-on-double when any turn paid twice.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:11535"
DEFAULT_ASK = "List the files in my Documents folder, then read the smallest text file and quote its first line."
LOOP_ROLE = "clean_loop_step"


def _turn_of(event: dict[str, Any]) -> str:
    for key in ("turn_key", "client_turn_id", "request_id", "turn_id"):
        value = str(event.get(key) or "").strip()
        if value:
            return value
    return ""


def count_paid_calls(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per turn, in order: paid calls by role, and whether the turn paid twice (loop + another lane)."""
    turns: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for event in events:
        if str(event.get("event_type") or "") != "model.call_started":
            continue
        if str(event.get("cost_class") or "") != "paid_cloud":
            continue
        turn = _turn_of(event) or "(no turn key)"
        row = turns.setdefault(turn, {"turn": turn, "paid_calls": 0, "by_role": {}, "models": []})
        role = str(event.get("call_role") or event.get("model_call_role") or "unlabelled")
        row["paid_calls"] += 1
        row["by_role"][role] = row["by_role"].get(role, 0) + 1
        model = str(event.get("provider_id") or event.get("model_id") or "")
        if model and model not in row["models"]:
            row["models"].append(model)
    for row in turns.values():
        loop = row["by_role"].get(LOOP_ROLE, 0)
        row["paid_twice"] = bool(loop) and row["paid_calls"] > loop
    return list(turns.values())


def _call(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 600.0) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        raw = exc.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}


def _events(base_url: str, session: str) -> list[dict[str, Any]]:
    payload = _call(base_url, "GET", f"/api/runtime/events?session={session}&limit=500")
    return list((payload or {}).get("events") or []) if isinstance(payload, dict) else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", action="append", default=[], help="chat session id to read (repeatable)")
    parser.add_argument("--ask", action="store_true", help="send one paid-pinned file ask first (SPENDS)")
    parser.add_argument("--model", default="", help="the paid model to pin for --ask")
    parser.add_argument("--text", default=DEFAULT_ASK)
    parser.add_argument("--fail-on-double", action="store_true")
    args = parser.parse_args(argv)

    sessions = list(args.session)
    if args.ask:
        if not args.model:
            parser.error("--ask needs --model (the paid model to pin)")
        session = f"paid-calls-check-{uuid.uuid4().hex[:10]}"
        reply = _call(args.base_url, "POST", "/api/chat", {
            "messages": [{"role": "user", "content": args.text}], "stream": False,
            "session_id": session, "model": args.model, "mode": "auto",
        })
        sessions.append(str((reply or {}).get("vool_session_id") or session) if isinstance(reply, dict) else session)
    if not sessions:
        parser.error("name a --session, or use --ask with --model")

    report = {"base_url": args.base_url, "sessions": []}
    for session in sessions:
        turns = count_paid_calls(_events(args.base_url, session))
        report["sessions"].append({"session": session, "turns": turns})
    doubles = sum(1 for s in report["sessions"] for t in s["turns"] if t["paid_twice"])
    report["turns_that_paid_twice"] = doubles
    print(json.dumps(report, indent=2, sort_keys=True))
    return 3 if (args.fail_on_double and doubles) else 0


if __name__ == "__main__":
    sys.exit(main())
