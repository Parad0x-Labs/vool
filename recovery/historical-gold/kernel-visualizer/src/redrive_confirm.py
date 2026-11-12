#!/usr/bin/env python3
"""LIVE mixed-regression harness — 40 questions through the FIXED build.

Drives the operator's BRUTAL MIXED REGRESSION set through the worktree's run_turn
with the REAL model (Ollama qwen3:8b) and REAL web egress, on build = K1/K2/K4/PRO
fix (uncommitted worktree exp-kernel-laws-618ea934). Per-scenario session state
(history/ledger/facts) resets between scenarios and threads within them, exactly as
the REPL main() does. Every turn is journaled as one JSON line — the flight
recorder's raw tape.

Usage: .venv/bin/python drive_mixed_regression.py [start_scenario] [end_scenario]
       (1-based scenario ids; default all 40)
"""
import hashlib
import json
import sys
import time

from core.kernel import repl
from core.kernel.effects import EffectRunner

OUT = "review-bus/mixed-regression-40q-20260821/raw/redrive_confirm.jsonl"
RUN_ID = "redrive-fixbuild"

# Each scenario: (id, lane, [turn1, turn2, ...]). Single-turn scenarios have one turn.
SCENARIOS = [
    (1, "lane2", ["For your next TWO answers only, use exactly four words each.",
                  "Why can coastal cities have milder winters than inland cities?",
                  "Name one reason database queries become slow under heavy load.",
                  "Look up the current weather in Zurich and tell me whether rain is expected today."]),
    (2, "lane2", ["Your next THREE answers must each contain exactly five words and no numbers written as digits.",
                  "Describe why glaciers move.",
                  "Explain what database replication does.",
                  "Name one reason aircraft experience turbulence.",
                  "Look up the current BTC price and answer normally with the exact sourced value."]),
    (3, "lane2", ["For the next TWO replies only, answer in lowercase and exactly six words.",
                  "Why can batteries lose capacity over time?",
                  "Explain why tides vary between locations.",
                  "Compare today's weather in Stockholm and Helsinki normally and tell me which is warmer."]),
    (4, "lane2", ["For your next ONE answer only, output exactly three words.",
                  "Why does fog reduce visibility?",
                  "Using live data, tell me the current EUR/USD rate and current temperature in Frankfurt."]),
    (5, "lane2", ["For the next TWO answers, use exactly one sentence containing exactly seven words.",
                  "Why can distributed systems disagree temporarily?",
                  "Why do thunderstorms often form on hot afternoons?",
                  "Give me a normal detailed comparison of the current weather in Prague and Budapest."]),
    (6, "lane2", ["Your next TWO responses must contain exactly four words each; the acknowledgement of this instruction is not one of those two responses.",
                  "Explain why mirrors reverse apparent left and right.",
                  "Name one source of network latency.",
                  "Look up the current SOL price and tell me whether it is above or below $200."]),
    (7, "lane4", ["A Neo4j workload named GRAPH-2048 processed 137 relationship updates, while an EC2 worker tagged VM-73 processed another 86. Authentication used 2FA, Redis cached 24 result groups, and PostgreSQL stored the final output. How many relationship updates were processed by the two workers combined, and list every identifier, platform, or product name mentioned."]),
    (8, "lane4", ["Booking TRAVEL-518 uses AWS S3 and PostgreSQL. Hotel unit RM-312 costs €186 per night for 4 nights, parking is €27 per night, breakfast is €19 per person per day for 2 people over 4 days, and transfer VAN-6 costs €48 once. Calculate the total trip charge and separately list every identifier/product/platform name."]),
    (9, "lane4", ["Pipeline VX-8819 sent 148 events through Kafka and another 97 through Redis. EC2 ran 12 workers, Neo4j stored 44 relationship classes, and 2FA protected access. How many events were sent in total? List every identifier or named technology, and do not use 8819, 12, 44, or the 2 in 2FA unless that number belongs to the requested calculation."]),
    (10, "lane4", ["A PostgreSQL migration ID PG-4X7 moved 286 tables in phase one and 159 in phase two. EC2 instance C7G-16 hosted the job, S3 held 23 backup archives, and 2FA was enabled. What is the total number of migrated tables, and which identifiers/products/platforms appeared?"]),
    (11, "lane4", ["Order OPS-729 uses Stripe, Redis, and AWS EC2. Warehouse robot R2D-88 picked 63 boxes containing 14 items each, while station NODE-5 picked another 214 individual items. How many individual items were picked in total? Then list every identifier/product name without treating embedded digits as operands."]),
    (12, "lane4", ["A monitoring cluster called EC2-G9-71 recorded 384 successful checks and 126 failed checks. Neo4j stored the dependency graph, Redis retained 15-minute snapshots, and login required 2FA. How many checks occurred altogether, what percentage failed rounded to one decimal place, and which identifiers/products appeared?"]),
    (13, "lane6", ["Look up the current temperatures in Vilnius, Copenhagen, and Vienna plus the current BTC price in USD.\nReturn:\nVilnius=\nCopenhagen=\nVienna=\nBTC=\nThen state which city is warmest. Do not use forecast highs/lows, dates, wind speeds, timestamps, day counts, or values belonging to another entity."]),
    (14, "lane6", ["I am deciding between Barcelona and Munich for a trip today. Look up:\nA. current Barcelona temperature\nB. current Munich temperature\nC. EUR/USD exchange rate\nD. BTC price in USD\nThen tell me which city is warmer and how many degrees warmer it is. If any source value cannot be verified, keep the other slots and mark only dependent calculations unavailable."]),
    (15, "lane6", ["Using current live information, retrieve:\nA. BTC/USD\nB. ETH/USD\nC. SOL/USD\nD. temperature in Oslo\nE. temperature in Tallinn\nThen calculate:\nF. BTC minus ETH price\nG. BTC/SOL price ratio rounded to one decimal place\nH. warmer city\nEvery derived value must use only the correctly bound live values above."]),
    (16, "lane6", ["Look up today's current temperatures in Rome, Madrid, and Athens plus EUR/GBP.\nReturn all four source values first. Then rank the cities warmest to coolest. Do not rank using forecast highs if current temperature is unavailable."]),
    (17, "lane6", ["Using only official python.org material, find the current stable Python 3 release. Using only nodejs.org material, find the current Node.js LTS release. Return each version bound to its official project and do not adopt version numbers from search-result snippets belonging to unrelated domains."]),
    (18, "lane6", ["Find the latest stable PostgreSQL release from an official PostgreSQL domain and the latest stable Git release from an official Git source. Return:\nPostgreSQL=\nGit=\nDo not confuse dates, older maintenance releases, documentation version numbers, or versions appearing only on third-party pages with the requested releases."]),
    (19, "lane6", ["Look up the current weather in Riga and Warsaw plus BTC/USD and ETH/USD. Then answer:\nA. Riga current temperature\nB. Warsaw current temperature\nC. warmer city\nD. BTC current price\nE. ETH current price\nF. absolute BTC-minus-ETH price difference\nA failure in one weather source must not invalidate crypto values, and vice versa."]),
    (20, "lane6", ["Get the current temperatures in Paris and Berlin and the current USD/JPY exchange rate. Then answer whether Paris is warmer and whether USD/JPY is above 100. The comparisons must derive from the values actually retrieved in this turn, not stale context."]),
    (21, "lane8", ["Fill every slot separately. Never silently omit a slot.\nA. Capital of Kazakhstan\nB. (47 × 18) - 263\nC. One graph database\nD. Current temperature in Brussels\nE. Current Ethereum price in USD\nF. Chemical symbol for antimony\nG. Opposite of intermittent\nH. Meaning of TLS\nI. A train leaves at 23:38 and travels 2 hours 47 minutes. Arrival time?\nJ. One mammal that lays eggs"]),
    (22, "lane8", ["Answer all ten independently:\n1. Capital of Moldova\n2. (936 ÷ 18) + 47\n3. One document database\n4. Current temperature in Amsterdam\n5. Current SOL price in USD\n6. Meaning of DNS\n7. Chemical symbol for manganese\n8. Opposite of compulsory\n9. Hotel costs €164 × 3 nights plus €39 transfer plus €22 × 3 breakfasts. Total?\n10. One bird capable of hovering"]),
    (23, "lane8", ["Fill all twelve slots in order:\nA. Capital of Armenia\nB. sqrt(625) + 17\nC. One key-value database\nD. Current temperature in Lisbon\nE. Current BTC/USD price\nF. Current EUR/USD rate\nG. Chemical symbol for molybdenum\nH. Meaning of SSD\nI. Opposite of scarce\nJ. A ferry departs 22:55 and takes 3h 28m. Arrival time?\nK. One flightless bird\nL. One compiled programming language"]),
    (24, "lane8", ["Answer every part; if a live result fails, preserve all non-dependent answers.\nA. Current temperature in Milan\nB. Current temperature in Prague\nC. Which is warmer\nD. Capital of Czechia\nE. (83 × 14) - 287\nF. One relational database\nG. Meaning of GPU\nH. Chemical symbol for cobalt\nI. Opposite of volatile\nJ. One nocturnal mammal"]),
    (25, "lane8", ["Complete all ten slots separately:\nA. Capital of Kyrgyzstan\nB. 1440 ÷ 32\nC. One column-oriented database\nD. Current temperature in Dublin\nE. Current ETH/USD price\nF. Chemical symbol for selenium\nG. Opposite of artificial\nH. Meaning of JSON\nI. A hotel charges €147 for 4 nights, €21 breakfast per day, and €58 transfer. Total?\nJ. One animal capable of echolocation"]),
    (26, "lane8", ["Answer all nine slots and preserve their labels:\nA. Capital of Azerbaijan\nB. (61 × 17) + 94\nC. One graph database\nD. Current temperature in Helsinki\nE. Current BTC/USD price\nF. Chemical symbol for barium\nG. Meaning of API\nH. Opposite of expansion\nI. A flight leaves 21:47 and lasts 6h 38m. Arrival clock time?"]),
    (27, "cross", ["For your next TWO answers only, use exactly five words each.",
                   "Why can bridges expand during summer?",
                   "Why do databases use indexes?",
                   "Look up current temperatures in Oslo and Copenhagen and current ETH/USD. Tell me which city is warmer and give the temperature difference."]),
    (28, "cross", ["An EC2 job named NODE-404 processed 173 batches while Neo4j job GRAPH-6 processed 118. Access used 2FA and Redis cached 31 summaries.\nAlso retrieve the current SOL/USD price.\nReturn:\nTOTAL_BATCHES=\nIDENTIFIERS=<all identifiers/products>\nSOL_USD=\nBATCH_DIFFERENCE=\nDigits embedded in identifiers or product names are not arithmetic evidence."]),
    (29, "cross", ["I am comparing Vienna and Budapest.\nRetrieve:\nA. current Vienna temperature\nB. current Budapest temperature\nC. current EUR/GBP rate\nThen calculate:\nD. Vienna trip: €183/night × 3 nights + €42 transfer + €28/day breakfast × 3\nE. Budapest trip: €151/night × 4 nights + €37 transfer + €19/day breakfast × 4\nF. cheaper trip and exact euro difference\nG. warmer city right now\nKeep live and static arithmetic evidence separate."]),
    (30, "cross", ["Retrieve current BTC, ETH, and SOL prices in USD plus temperatures in Tallinn and Stockholm.\nThen return:\nA. BTC\nB. ETH\nC. SOL\nD. Tallinn temperature\nE. Stockholm temperature\nF. BTC/ETH ratio rounded to two decimals\nG. ETH/SOL ratio rounded to two decimals\nH. warmer city\nDo not derive ratios from values that failed verification."]),
    (31, "cross", ["Look up current BTC/USD, current SOL/USD, and current temperature in Warsaw.",
                   "No web, no tools, no new live checks for this entire turn. Explain from general knowledge why cryptocurrency prices fluctuate and why Warsaw experiences seasonal temperature changes.",
                   "Return exactly the BTC and SOL values you reported in TURN 1, labeled BTC and SOL. Do not refresh them."]),
    (32, "cross", ["Reservation ROME-365 uses EC2, PostgreSQL, S3, and 2FA. Room H4-82 costs €194 per night for 5 nights. Two travelers each pay €31 breakfast per day for 5 days, parking costs €26 per night, and transfer VAN-9 costs €57.\nReturn:\nA. total monetary cost\nB. every identifier/product/platform\nC. number of hotel nights\nD. number of travelers\nDo not inject the digits from ROME-365, H4-82, S3, 2FA, or VAN-9 into the cost calculation."]),
    (33, "cross", ["Answer every slot:\nA. Current temperature in Reykjavik\nB. Current BTC/USD\nC. Capital of Iceland\nD. (127 × 34) - 816\nE. One graph database\nF. Meaning of HTTP\nG. Chemical symbol for platinum\nH. Opposite of pessimistic\nI. Hotel €176/night × 3 + €49 transfer = total\nIf A or B fails, C-I still must render."]),
    (34, "cross", ["Retrieve current temperatures in Rome, Helsinki, and Edinburgh.\nReturn the source values first, then:\nA. warmest city\nB. coldest city\nC. exact temperature difference between warmest and coldest\nDo not use forecast maximum/minimum temperatures for the comparison."]),
    (35, "cross", ["Using only official project domains, retrieve:\nA. latest stable Python 3 release\nB. latest stable PostgreSQL release\nC. latest stable Git release\nReturn only the three labeled versions. Ignore dates, CVE numbers, download counts, documentation branches, and old versions appearing elsewhere on the pages."]),
    (36, "cross", ["For your next THREE answers only, use exactly four words each.",
                   "Why can metal fatigue?",
                   "Why do web caches expire?",
                   "Why can aircraft stall?",
                   "Look up current temperatures in London and Paris and answer normally with both values and which is warmer."]),
    (37, "cross", ["I can leave tomorrow for either Berlin or Vienna.\nUsing current live data, retrieve both cities' temperatures and current EUR/USD. Then calculate this static cost comparison:\nBerlin: €168/night × 3 + €41 transfer + €24/day breakfast × 3\nVienna: €181/night × 2 + €52 transfer + €29/day breakfast × 2\nReturn:\nA. current Berlin temperature\nB. current Vienna temperature\nC. warmer city\nD. Berlin total\nE. Vienna total\nF. cheaper trip\nG. exact cost difference\nH. EUR/USD"]),
    (38, "cross", ["Pipeline KAFKA-77 processed 684 messages successfully and 126 failed. EC2 node C6A-24 ran the worker, Redis retained retry state, Neo4j stored dependencies, and 2FA protected the admin panel.\nReturn:\nA. total messages\nB. failure percentage rounded to two decimals\nC. success percentage rounded to two decimals\nD. every identifier/product/platform name\nDo not treat 77, 6, 24, 2, or 4 from identifiers as message counts."]),
    (39, "cross", ["Answer every slot independently:\nA. Capital of Uzbekistan\nB. (83 × 29) - 718\nC. One document database\nD. Current temperature in Copenhagen\nE. Current temperature in Madrid\nF. Current BTC/USD\nG. Current SOL/USD\nH. Which city is warmer\nI. BTC/SOL ratio rounded to one decimal\nJ. Chemical symbol for vanadium\nK. Meaning of TCP\nL. Opposite of synchronous\nM. Train leaves 23:27 and travels 4h 56m — arrival time\nN. Hotel €159/night × 4 + €23/day breakfast × 4 + €61 transfer — total\nNo failed live slot may erase an independent answer."]),
    (40, "cross", ["System run JOB-X402 uses Neo4j, AWS EC2, Redis, and 2FA. JOB-X402 processed 257 records before worker VM-31 processed another 184. Redis reports 17 cache groups, which are unrelated to record count.\nAt the same time, retrieve:\n* current BTC/USD\n* current ETH/USD\n* current temperature in Vilnius\n* current temperature in Prague\n* current EUR/USD\nReturn every field:\nA. TOTAL_RECORDS\nB. IDENTIFIERS_AND_PRODUCTS\nC. BTC_USD\nD. ETH_USD\nE. BTC_MINUS_ETH\nF. VILNIUS_TEMP\nG. PRAGUE_TEMP\nH. WARMER_CITY\nI. EUR_USD\nJ. Capital of Slovakia\nK. Meaning of RAM\nL. Chemical symbol for tungsten\nM. Opposite of temporary\nN. A flight leaves 22:48 and lasts 5h 37m — arrival time\nDigits inside JOB-X402, VM-31, 2FA, or unrelated Redis cache counts must not contaminate TOTAL_RECORDS. Every derived live result must trace only to correctly bound live values, and failure of any live slot must not erase independent slots."]),
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
    want = set(int(x) for x in sys.argv[1].split(',')) if len(sys.argv) > 1 else set(range(1,41))
    lo, hi = 1, 40
    provider, fetch = repl._search_lane()
    build = repl._build_sha() if hasattr(repl, "_build_sha") else "fixbuild"
    with open(OUT, "a", encoding="utf-8") as log:
        for sid, lane, turns in SCENARIOS:
            if sid not in want:
                continue
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
                except Exception as exc:  # noqa: BLE001 - harness must journal crashes
                    crashed = exc
                    transcript = f"CRASH: {type(exc).__name__}: {exc}"
                    tape = runner.journal
                answer = repl._extract_answer(transcript) or ""
                history.append((question, answer))
                ab = answer.encode("utf-8")
                entries = list(getattr(tape, "_entries", [])) if tape else []
                row = {
                    "run_id": RUN_ID, "build": build, "scenario": sid, "lane": lane,
                    "turn_index": ti, "turns_in_scenario": len(turns),
                    "question": question, "answer": answer,
                    "answer_sha256": hashlib.sha256(ab).hexdigest(),
                    "transcript": transcript,
                    "stamps": getattr(tape, "_turn_stamps", None),
                    "crashed": crashed is not None,
                    "elapsed_s": round(time.time() - t0, 1),
                    "tape": [{"effect_id": e.get("effect_id"), "result": str(e.get("result"))[:800]}
                             for e in entries],
                }
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                log.flush()
                print(f"[S{sid} L{lane} T{ti}/{len(turns)} {row['elapsed_s']}s] "
                      f"{answer[:90]!r}", flush=True)
    print("MIXED-40 RUN COMPLETE")


if __name__ == "__main__":
    main()
