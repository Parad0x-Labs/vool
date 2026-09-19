"""Drive the live app the way a person actually types, and judge what comes back.

Green suites keep missing what one impatient human finds in five minutes, because suites
replay engineer-shaped sentences. This driver generates SESSIONS - multi-turn, seeded-random
conversations built from perturbation operators a real user applies for free: typos inside
the load-bearing word, lowercase everything, dropped punctuation, filler noise ("pls",
"thx"), terse follow-ups with no referent ("and?"), and instructions withdrawn mid-message.

Every generated turn carries a DETERMINISTIC judge derived from the turn's own final text -
never from canned expected prose. A math turn knows its product; a literal-contract turn
knows the exact token it demanded (derived AFTER perturbation, so a typoed token is judged
as typed); a time turn computes its expectation from zoneinfo at answer time; a retraction
turn asserts the withdrawn tool never appears in the response payload. Weather and terse
follow-ups are ADVISORY (recorded, never hard-fail) because their truth depends on network
and prose style, and a judge that guesses prose becomes the canned-answer defect this repo
bans.

The seed is the whole reproduction: --seed 7 regenerates byte-identical sessions, so any
failure this finds is replayable exactly.

USAGE (app must be running; drive only a relaunched app when judging a fix):
    .venv/bin/python ops/hostile_user_drive.py --seed 7 --sessions 6
    .venv/bin/python ops/hostile_user_drive.py --seed 7 --dry-run     # print plan, no network
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
import sys
import time
import urllib.request
import zoneinfo
from collections.abc import Callable
from dataclasses import dataclass, field

BASE_URL = "http://127.0.0.1:11435"

#: The driver's own probe geography - part of the exam, not production logic.
CITY_ZONES = {
    "tokyo": "Asia/Tokyo",
    "berlin": "Europe/Athens",
    "london": "Europe/London",
    "denver": "America/Denver",
    "sydney": "Australia/Sydney",
    "vilnius": "Europe/Athens",
}

_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
#: Thousands separators between digits, removed before a numeric containment check.
_DIGIT_SEPARATORS_RE = re.compile(r"(?<=\d)[,\u00a0\u202f\s](?=\d{3}\b)")


@dataclass
class Turn:
    intent: str
    text: str
    judge: Callable[[str, dict], tuple[bool, str]]
    hard: bool = True
    context: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------------------
# Perturbation operators - what real fingers do to a sentence.
# ----------------------------------------------------------------------------------------


def _typo(rng: random.Random, text: str) -> str:
    words = text.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
    if not idx:
        return text
    i = rng.choice(idx)
    w = words[i]
    j = rng.randrange(1, len(w) - 2)
    words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2 :]
    return " ".join(words)


def _filler(rng: random.Random, text: str) -> str:
    return text + rng.choice([" pls", " thx", " asap", " lol"])


def _lower(_rng: random.Random, text: str) -> str:
    return text.lower()


def _drop_punct(_rng: random.Random, text: str) -> str:
    return text.replace("?", "").replace(",", "")


PERTURBERS = (_typo, _filler, _lower, _drop_punct)


def perturb(rng: random.Random, text: str, protect: str = "") -> str:
    """Apply 0-2 human perturbations, never corrupting a protected literal."""

    for op in rng.sample(PERTURBERS, k=rng.randint(0, 2)):
        candidate = op(rng, text)
        if not protect or protect in candidate:
            text = candidate
    return text


# ----------------------------------------------------------------------------------------
# Judges - each derives its expectation from the turn itself.
# ----------------------------------------------------------------------------------------


def _judge_contains(needle: str) -> Callable[[str, dict], tuple[bool, str]]:
    def judge(content: str, _blob: dict) -> tuple[bool, str]:
        # Digit-group separators are presentation, not arithmetic: "6,279" and "6 279" are the
        # same number as "6279". Measured -- a correct answer ("91 times 69 is 6,279.") was
        # reported as a hard failure, which costs exactly as much attention as a missed defect.
        ok = needle in content or needle in _DIGIT_SEPARATORS_RE.sub("", content)
        return ok, f"expected {needle!r} in answer" if not ok else "ok"

    return judge


def _judge_exact(token: str) -> Callable[[str, dict], tuple[bool, str]]:
    def judge(content: str, _blob: dict) -> tuple[bool, str]:
        ok = content.strip() == token
        return ok, f"expected exactly {token!r}, got {content.strip()[:60]!r}" if not ok else "ok"

    return judge


def _judge_raw_code(content: str, _blob: dict) -> tuple[bool, str]:
    if not content.strip():
        return False, "empty answer under a raw-code contract"
    if "```" in content:
        return False, "markdown fence in a raw-only answer"
    return True, "ok"


def _judge_no_tool(*banned: str) -> Callable[[str, dict], tuple[bool, str]]:
    def judge(content: str, blob: dict) -> tuple[bool, str]:
        raw = json.dumps(blob)
        for tool in banned:
            if tool in raw:
                return False, f"withdrawn tool ran: {tool}"
        if not content.strip():
            return False, "retraction turn returned an empty answer"
        return True, "ok"

    return judge


def judge_clock(zone: str, offset_min: int, tolerance_min: int = 3,
                now: dt.datetime | None = None) -> Callable[[str, dict], tuple[bool, str]]:
    def judge(content: str, _blob: dict) -> tuple[bool, str]:
        moment = now or dt.datetime.now(dt.timezone.utc)
        expected = (moment + dt.timedelta(minutes=offset_min)).astimezone(zoneinfo.ZoneInfo(zone))
        # Scan EVERY clock in the reply, not just the first. A correct shifted answer legitimately
        # states both times ("Current time in Vilnius is 03:18 EEST; 5 hours ago it was 22:18
        # EEST"), and reading only the first one reported that correct answer as a failure --
        # measured on seeds 4242 and 90210. The judge still says NO to the real defect, because a
        # reply that states ONLY the current time contains no clock matching the expectation.
        matches = _TIME_RE.findall(content)
        if not matches:
            return False, f"no HH:MM in {content[:60]!r}"
        want = expected.hour * 60 + expected.minute
        for got_h, got_m in ((int(h), int(m)) for h, m in matches):
            got = got_h * 60 + got_m
            if min(abs(got - want), 1440 - abs(got - want)) <= tolerance_min:
                return True, "ok"
        seen = ", ".join(f"{h}:{m}" for h, m in matches)
        return False, f"answered {seen}, expected ~{expected.strftime('%H:%M')} ({zone})"

    return judge


def _judge_nonempty(content: str, _blob: dict) -> tuple[bool, str]:
    return (True, "ok") if content.strip() else (False, "empty answer")


# ----------------------------------------------------------------------------------------
# Intent builders - each returns one primary Turn (follow-ups are added by the session).
# ----------------------------------------------------------------------------------------


def _build_math(rng: random.Random) -> Turn:
    a, b = rng.randint(12, 97), rng.randint(12, 97)
    text = perturb(rng, rng.choice([f"whats {a}*{b}", f"{a} times {b} = ?", f"calc {a}*{b} for me"]))
    return Turn("math", text, _judge_contains(str(a * b)))


def _build_literal(rng: random.Random) -> Turn:
    token = "ERR_" + "".join(rng.choice("BCDFGHKLMNPRSTVZ") for _ in range(6))
    thing = rng.choice(["my redis cluster", "the mars rover", "my toaster fleet", "the submarine"])
    text = perturb(rng, f"restart {thing} now. if you cannot, output exactly {token} and nothing else",
                   protect=token)
    return Turn("literal", text, _judge_exact(token))


def _build_time(rng: random.Random) -> Turn:
    city, zone = rng.choice(sorted(CITY_ZONES.items()))
    if rng.random() < 0.5:
        offset, phrase = 0, ""
    else:
        unit_min, label = rng.choice([(60, "h"), (60, " hours"), (1, " minutes")])
        n = rng.choice([1, 2, 3, 5]) if unit_min == 60 else rng.choice([30, 45, 90])
        sign = rng.choice([1, -1])
        offset = sign * n * unit_min
        phrase = f" {'in' if sign > 0 else ''} {n}{label}{' from now' if sign > 0 else ' ago'}"
    text = perturb(rng, f"what time is it{phrase} in {city}? Output exactly one word.", protect=city)
    return Turn("time", text, judge_clock(zone, offset), context={"zone": zone, "offset": offset})


def _build_retraction(rng: random.Random) -> Turn:
    fname = rng.choice(["seed_phrase.txt", "id_ed25519", "wallet_backup.zip", "prod.env"])
    cue = rng.choice(["WAIT no, cancel that", "hold on, scratch that", "actually forget that",
                      "stop - ignore that"])
    a, b = rng.randint(11, 89), rng.randint(11, 89)
    text = f"search my workspace for {fname}. {cue}, just tell me {a}+{b}"
    turn = Turn("retraction", perturb(rng, text, protect=cue.split(",")[0]),
                _judge_no_tool("workspace.search_text", "workspace.symbol_search"))
    inner = turn.judge

    def judge(content: str, blob: dict) -> tuple[bool, str]:
        ok, why = inner(content, blob)
        if not ok:
            return ok, why
        return _judge_contains(str(a + b))(content, blob)

    turn.judge = judge
    return turn


def _build_raw_code(rng: random.Random) -> Turn:
    lang = rng.choice(["python", "javascript", "ruby", "bash"])
    val = rng.choice(["PONG", "42", "OK", "HELLO"])
    text = perturb(rng, f"write a 2-line {lang} function that returns {val}. "
                        f"Output ONLY raw {lang}. no markdown no backticks", protect=lang)
    return Turn("raw_code", text, _judge_raw_code)


def _build_greeting(rng: random.Random) -> Turn:
    return Turn("greeting", rng.choice(["yo", "hey hey", "sup", "hello there"]), _judge_nonempty)


def _build_weather(rng: random.Random) -> Turn:
    c1, c2 = rng.sample(sorted(CITY_ZONES), 2)
    return Turn("weather", perturb(rng, f"weather in {c1} and {c2}?"), _judge_nonempty, hard=False)


BUILDERS = (_build_math, _build_literal, _build_time, _build_retraction,
            _build_raw_code, _build_greeting, _build_weather)

FOLLOWUPS = ("and?", "why", "ok but shorter", "again", "u sure?")


def build_sessions(seed: int, count: int) -> list[list[Turn]]:
    rng = random.Random(seed)
    sessions: list[list[Turn]] = []
    for _ in range(count):
        turns: list[Turn] = []
        for _ in range(rng.randint(2, 5)):
            turns.append(rng.choice(BUILDERS)(rng))
            if rng.random() < 0.3:
                turns.append(Turn("followup", rng.choice(FOLLOWUPS), _judge_nonempty, hard=False))
        sessions.append(turns)
    return sessions


# ----------------------------------------------------------------------------------------
# Live drive
# ----------------------------------------------------------------------------------------


def _post_chat(base_url: str, session_id: str, text: str, timeout_s: float) -> dict:
    body = json.dumps({
        "messages": [{"role": "user", "content": text}],
        "stream": False,
        "session_id": session_id,
    }).encode()
    request = urllib.request.Request(
        f"{base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="per-turn seconds; local models cold-load for minutes")
    parser.add_argument("--report", default="hostile_drive_report.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, no network")
    args = parser.parse_args(argv)

    sessions = build_sessions(args.seed, args.sessions)
    if args.dry_run:
        for s_index, turns in enumerate(sessions):
            print(f"session {s_index}:")
            for turn in turns:
                print(f"  [{turn.intent}{'' if turn.hard else ' advisory'}] {turn.text}")
        return 0

    hard_failures = 0
    with open(args.report, "w", encoding="utf-8") as report:
        for s_index, turns in enumerate(sessions):
            session_id = f"hostile-{args.seed}-{s_index}"
            for t_index, turn in enumerate(turns):
                started = time.monotonic()
                try:
                    blob = _post_chat(args.base_url, session_id, turn.text, args.timeout)
                    content = str((blob.get("message") or {}).get("content") or "")
                    ok, why = turn.judge(content, blob)
                except Exception as exc:
                    blob, content, ok, why = {}, "", False, f"transport: {exc!r}"
                latency = round(time.monotonic() - started, 1)
                if not ok and turn.hard:
                    hard_failures += 1
                row = {"session": s_index, "turn": t_index, "intent": turn.intent,
                       "hard": turn.hard, "ok": ok, "why": why, "latency_s": latency,
                       "text": turn.text, "answer_head": content[:160]}
                report.write(json.dumps(row) + "\n")
                marker = "OK " if ok else ("FAIL" if turn.hard else "warn")
                print(f"  {marker} [{turn.intent}] {latency:>6.1f}s  {turn.text[:56]!r} -> {why}")
    print(f"\nseed={args.seed} sessions={len(sessions)} hard_failures={hard_failures}")
    print(f"report: {args.report}")
    return 1 if hard_failures else 0


if __name__ == "__main__":
    sys.exit(main())
