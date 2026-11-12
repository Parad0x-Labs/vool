#!/usr/bin/env python3
"""FRESH UNSEEN test set — generalization drive for the K1/K2/K4/PRO + mixed-40 fix.
All specifics (cities, tickers, FX pairs, identifiers, projects, numbers) are NEW —
nothing overlaps the tuned 40. Real qwen3:8b + real web on the fixed build.
"""
import hashlib
import json
import time

from core.kernel import repl
from core.kernel.effects import EffectRunner

OUT = "review-bus/mixed-regression-40q-20260821/raw/fresh_set_sessions.jsonl"
RUN_ID = "fresh-fixbuild"

SCENARIOS = [
    # --- Lane 2: TTL / scoped constraints ---
    (1, "lane2", ["For your next TWO answers only, use exactly five words each.",
                  "Why do rivers meander across flat land?",
                  "Look up the current weather in Oslo and tell me whether rain is expected today."]),
    (2, "lane2", ["Your next THREE replies must each be lowercase and exactly six words.",
                  "Explain why bridges have expansion joints.",
                  "Name one cause of packet loss in networks.",
                  "Look up the current LTC price in USD and say whether it is above or below $100."]),
    # --- Lane 4: identifier / number binding (new identifiers) ---
    (3, "lane4", ["A Cassandra job tagged DB-9910 processed 214 rows, while an EC2 worker VM-77 processed another 189. Redis cached 33 result groups, 2FA guarded access, and PostgreSQL stored the output. How many rows were processed by the two workers combined, and list every identifier, platform, or product name mentioned."]),
    (4, "lane4", ["Order SHIP-42X uses Stripe and AWS S3. Robot RX-6 packed 47 crates holding 12 items each, and station BAY-3 packed another 138 individual items. How many individual items were packed in total? Then list every identifier/product name without treating embedded digits (42, 6, 3) as operands."]),
    # --- Lane 6: live harvest / value binding (the fix class — new cities/tickers/FX) ---
    (5, "lane6", ["Look up the current temperatures in Naples, Lisbon, and Riga plus the current XRP price in USD.\nReturn:\nNaples=\nLisbon=\nRiga=\nXRP=\nThen state which city is warmest. Do not use forecast highs/lows, dates, or values belonging to another entity."]),
    (6, "lane6", ["Get the current GBP/JPY exchange rate and the current temperature in Manchester. Then answer whether GBP/JPY is above 180 and give both values, each labeled. The comparisons must derive from the values retrieved this turn, not stale context."]),
    (7, "lane6", ["Using current live data, retrieve:\nA. ADA/USD\nB. DOGE/USD\nC. temperature in Reykjavik\nThen return each labeled value and the ADA/DOGE price ratio rounded to two decimals. Every derived value must use only the correctly bound live values."]),
    (8, "lane6", ["Using only official go.dev material, find the current stable Go release. Using only official ruby-lang.org material, find the current stable Ruby release. Return each version bound to its official project; do not adopt version numbers from unrelated domains or documentation branches."]),
    # --- Lane 8: multipart ---
    (9, "lane8", ["Fill every slot separately; never silently omit one.\nA. Capital of Georgia (the country)\nB. (58 × 13) − 194\nC. One time-series database\nD. Current temperature in Valencia\nE. Current XRP/USD price\nF. Chemical symbol for tin\nG. Meaning of SSH\nH. A train leaves at 21:15 and travels 3 hours 50 minutes. Arrival time?\nI. One reptile that is venomous"]),
    (10, "lane8", ["Answer all ten independently:\n1. Capital of Croatia\n2. (992 ÷ 16) + 38\n3. One in-memory cache\n4. Current temperature in Turin\n5. Current LTC/USD price\n6. Meaning of TLS\n7. Chemical symbol for zinc\n8. Opposite of transparent\n9. Hotel €138/night × 3 + €44 transfer + €18/day breakfast × 3. Total?\n10. One bird that cannot fly"]),
    # --- Cross-lane breakers ---
    (11, "cross", ["Pipeline KAFKA-9 processed 512 messages successfully and 88 failed. EC2 node C7-11 ran the worker, Redis retained retry state, and 2FA protected the panel.\nAlso retrieve the current DOGE/USD price.\nReturn:\nTOTAL_MESSAGES=\nFAIL_PCT= (two decimals)\nDOGE_USD=\nIDENTIFIERS=\nDigits embedded in identifiers are not message counts."]),
    (12, "cross", ["I am comparing Naples and Valencia.\nRetrieve: A. current Naples temperature B. current Valencia temperature C. current EUR/GBP rate\nThen calculate: D. Naples trip €142/night × 3 + €35 transfer + €26/day breakfast × 3 ; E. Valencia trip €128/night × 4 + €40 transfer + €21/day breakfast × 4 ; F. cheaper trip and exact euro difference ; G. warmer city right now.\nKeep live and static arithmetic evidence separate."]),
    (13, "cross", ["TURN 1: Look up the current BTC/USD and the current temperature in Helsinki.\nTURN 2 (separate): No web, no tools this turn. Explain from general knowledge why crypto prices are volatile.\nTURN 3 (separate): Return exactly the BTC value you reported in TURN 1, labeled BTC. Do not refresh it."]),
    (14, "cross", ["Answer every slot; if a live result fails, preserve all non-dependent answers.\nA. Current temperature in Edinburgh\nB. Current BTC/USD\nC. Capital of Portugal\nD. (127 × 19) − 512\nE. One graph database\nF. Meaning of DNS\nG. Chemical symbol for lead\nH. Opposite of expand\nI. Hotel €166/night × 2 + €39 transfer — total\nIf A or B fails, C–I must still render."]),
]


def build_context(history, ledger):
    parts = []
    if history:
        turns = "\n".join(f"user: {q}\nassistant: {a[:400]}" for q, a in history[-4:])
        parts.append("Conversation so far:\n" + turns)
        prev_lines = [ln.strip() for ln in history[-1][1].splitlines() if ln.strip()][:10]
        if prev_lines:
            numbered = "\n".join(f"  {i}. {ln[:120]}" for i, ln in enumerate(prev_lines, 1))
            parts.append("PREVIOUS ANSWER, ADDRESSABLE BY ITEM NUMBER:\n" + numbered)
    if ledger:
        rows = "\n".join(f"{c['id']}: {c['description']} ({c['reason']})" for c in ledger)
        parts.append("OPEN WORK from earlier turns (INTERNAL STATE):\n" + rows)
    return "\n\n".join(parts) + ("\n\n" if parts else "")


def main():
    provider, fetch = repl._search_lane()
    build = repl._build_sha() if hasattr(repl, "_build_sha") else "fixbuild"
    with open(OUT, "a", encoding="utf-8") as log:
        for sid, lane, turns in SCENARIOS:
            history, ledger, facts = [], [], {}
            for ti, question in enumerate(turns, 1):
                t0 = time.time()
                context = build_context(history, ledger)
                runner = EffectRunner(mode="record")
                crashed = None
                try:
                    transcript, tape, ledger, facts = repl.run_turn(
                        question, runner, fetch, provider, context=context,
                        carryover=ledger, session_facts=facts)
                except Exception as exc:  # noqa: BLE001
                    crashed = exc
                    transcript = f"CRASH: {type(exc).__name__}: {exc}"
                    tape = runner.journal
                answer = repl._extract_answer(transcript) or ""
                history.append((question, answer))
                entries = list(getattr(tape, "_entries", [])) if tape else []
                row = {
                    "run_id": RUN_ID, "build": build, "scenario": sid, "lane": lane,
                    "turn_index": ti, "turns_in_scenario": len(turns),
                    "question": question, "answer": answer,
                    "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                    "transcript": transcript, "crashed": crashed is not None,
                    "elapsed_s": round(time.time() - t0, 1),
                    "tape": [{"effect_id": e.get("effect_id"), "result": str(e.get("result"))[:800]}
                             for e in entries],
                }
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                log.flush()
                print(f"[S{sid} {lane} T{ti}/{len(turns)} {row['elapsed_s']}s] {answer[:90]!r}", flush=True)
    print("FRESH-SET RUN COMPLETE")


if __name__ == "__main__":
    main()
