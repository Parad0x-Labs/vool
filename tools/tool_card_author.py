"""Draft tool cards for builtin tools that have none, with a model, from each tool's own contract.

    python -m tools.tool_card_author --out <dir> --model-cmd "<command>" [--only machine,operator] [--limit N]

``--model-cmd`` is any command that reads one prompt on stdin and prints the model's reply on stdout
(for example the owner's API helper with its spending cap). For each model-visible builtin without a
card the prompt carries only that tool's contract (intent, description, arguments, side effect,
approval) and the card rules; it never sees any evaluation asks. Each reply is parsed and checked
with the real validator (core.tool_cards.parse_card); a draft that fails is retried once with the
error, then recorded as failed. Drafts are written to ``<dir>/<family>.json`` in the
core/tool_card_data format, resumable (an intent already drafted is skipped), with one ledger line
per model call in ``<dir>/ledger.jsonl``. Drafts are proposals: a person or an independent reviewer
reads them before they are copied into core/tool_card_data.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

_RULES = """You write a "tool card" for one tool of VOOL, a personal AI assistant that runs on a
person's own computer. The card tells VOOL WHEN a person needs this tool. Reply with ONE JSON object
and nothing else:

{"use_when": "...", "examples": ["...", ...], "not_for": ["...", ...]}

- use_when: one plain sentence, 12-300 characters, about the person's situation, not the code.
- examples: 12 to 16 different short asks (under 120 characters) a normal person would really type
  to VOOL when they need this tool. Vary wording, length, politeness and slang; some with typos or
  no punctuation. Write 9 or 10 in English and one each in Spanish, French, German and Lithuanian.
  Do not name the tool or any internal word (intent, tool, API, workspace, operator).
- not_for: 4 to 6 asks that LOOK close but must NOT use this tool: how-to or advice questions,
  general knowledge about the topic, asking to write text about it, small talk mentioning it.
- No ask may appear in both lists. No personal data of real people.
"""


def _contract_prompt(contract) -> str:
    schema = getattr(contract, "json_schema", None) or getattr(contract, "input_schema", {})
    return (
        f"{_RULES}\nThe tool:\n"
        f"- name: {contract.intent}\n"
        f"- what it does: {contract.description}\n"
        f"- arguments: {json.dumps(schema, sort_keys=True)[:1500]}\n"
        f"- effect: {contract.side_effect_class}; approval: {contract.approval_requirement}\n"
    )


def _parse_reply(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[start : end + 1])


def _call(cmd: str, prompt: str, timeout: int) -> tuple[str, float]:
    started = time.monotonic()
    done = subprocess.run(shlex.split(cmd), input=prompt, capture_output=True, text=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"model command exited {done.returncode}: {done.stderr.strip()[:300]}")
    return done.stdout, time.monotonic() - started


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model-cmd", required=True)
    parser.add_argument("--only", default="", help="comma-separated families (intent prefixes)")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many tools (0 = all)")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    from core.runtime_tool_contracts import runtime_tool_contracts
    from core.tool_cards import ToolCardError, builtin_cards, parse_card

    have = builtin_cards()
    families = {f.strip() for f in args.only.split(",") if f.strip()}
    todo = [
        c for c in runtime_tool_contracts()
        if c.model_visible and c.intent not in have and (not families or c.intent.split(".", 1)[0] in families)
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    ledger = args.out / "ledger.jsonl"
    done_count = failed = 0
    for contract in todo:
        if args.limit and done_count >= args.limit:
            break
        family = contract.intent.split(".", 1)[0]
        path = args.out / f"{family}.json"
        drafts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if contract.intent in drafts:
            continue
        prompt = _contract_prompt(contract)
        card = None
        error = ""
        for attempt in (1, 2):
            try:
                reply, seconds = _call(args.model_cmd, prompt, args.timeout)
                raw = _parse_reply(reply)
                card = parse_card(raw, owner=contract.intent)
            except (ValueError, ToolCardError, RuntimeError, subprocess.TimeoutExpired) as exc:
                error, seconds = str(exc)[:300], 0.0
                prompt = f"{_contract_prompt(contract)}\nYour previous reply was refused: {error}\nReply again."
            with ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"intent": contract.intent, "attempt": attempt, "ok": card is not None,
                                     "seconds": round(seconds, 1), "error": "" if card else error}) + "\n")
            if card is not None:
                break
        if card is None:
            failed += 1
            continue
        drafts[contract.intent] = card.to_dict()
        path.write_text(json.dumps(drafts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        done_count += 1
        print(f"{contract.intent}: {len(card.examples)} examples, {len(card.not_for)} near misses", flush=True)
    print(json.dumps({"todo": len(todo), "drafted": done_count, "failed": failed}))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
