"""Real-app conversation gate for an installed VOOL build (run on the Mac, against the app on 11435).

Drives the app's own chat page the way a person does: several chats, tool tasks approved with the
app's own buttons (allow for the task, allow once, task and session bypass), follow-ups, silly,
vague and basic questions, model switches (free cloud and local qwen3:8b), chats reopened from the
sidebar, memory told in one chat and asked in another, reminders and contacts made, looked up,
cancelled and deleted next to a same-name decoy, an Apple Calendar read, a real app restart, a
chat delete.

NEVER THE SAME QUESTIONS: every message is newly worded (pools of phrasings, random values,
openers, endings, casing, the odd typo); a message any earlier run sent is never sent again, and
the least-used sentence patterns go first (gate-history.json beside this file).

GRADED, NOT TRUSTED: every planned step ends PASS, FAIL, BLOCKED, ERROR, REVIEW or NOT RUN.
Answers are bound to their own turn and graded against keys fixed before asking; reminders,
contacts and chat deletion are re-read from the app's own stores, never taken from the reply.
Mail and calendar answers are REVIEW (no independent reader here). Exit 0 only when every
planned step PASSES.

Raw turns: <out_dir>/gate-turns.json, verdicts: <out_dir>/gate-result.json. The printout hides
the words of inbox and calendar replies.

usage: python real_app_gate.py <out_dir> [--seed=N]
"""

import contextlib
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

OPENERS = ["", "", "", "ok ", "hey ", "so ", "quick q: ", "um ", "right, ", "btw ", "hmm ", "alright "]
ENDINGS = ["", "", "", "?", " pls", " please", " thx", " thanks", "!", " :)", " ty"]

POOLS = {
    "greet": ["hi", "hey", "hello", "yo", "hiya", "good evening", "hey there", "sup", "hi vool", "hello there",
              "heya", "evening", "hi again", "howdy", "hey hey", "hello vool", "morning", "hi, how are you"],
    "email_count": ["check my inbox, how many unread emails?", "how many unread mails do I have",
                    "any new emails? how many are unread", "look at my email and tell me how many are unread",
                    "count my unread emails", "do i have unread mail? how many", "what's my unread email count",
                    "go through my inbox and tell me the number of unread messages", "how many emails haven't I read yet",
                    "peek at my mail, how many unread?", "how many unopened emails are waiting for me",
                    "tell me how many unread emails sit in my inbox", "check mail: number of unread ones"],
    "email_from": ["from whom ?", "who sent them", "who are they from", "and the senders?", "who're those from",
                   "list who sent those", "which people sent them", "senders?", "who wrote those", "from who tho",
                   "and who are the senders", "tell me who they came from"],
    "email_urgent": ["ok which one looks most urgent", "any of them important?", "which should i answer first",
                     "is any of those urgent", "what's the most pressing one", "which one matters most",
                     "anything there that needs a reply today", "which is the most important of those",
                     "if i only read one, which one"],
    "email_again": ["and how many were unread again?", "remind me how many unread there were",
                    "what was the unread number again", "how many unread did you say", "unread count again?",
                    "sorry, how many unread emails was it", "what number of unread mails did you find"],
    "add": ["whats {a}+{b}", "lol whats {a} plus {b}", "{a} + {b} = ?", "random but {a}+{b}?", "add {a} and {b}",
            "how much is {a} plus {b}", "quick maths: {a}+{b}", "{a} plus {b}?"],
    "last_q": ["what did I ask you just before this?", "what was my last question", "what did i just ask you",
               "repeat my previous question back to me", "what was the question right before this one",
               "which sum did i ask you about a second ago"],
    "recap": ["summarize everything i asked in this chat so far", "recap this whole chat for me", "list all the things we covered here",
              "give me a rundown of this conversation", "what topics have we gone through in this chat", "sum up our chat so far"],
    "weather": ["whats the weather in {c} right now", "how's the weather in {c}", "weather {c}?", "is it raining in {c} today",
                "what's it like outside in {c} now", "temperature in {c} at the moment?", "tell me the current weather for {c}",
                "how cold is it in {c} now", "current conditions in {c}?", "what's the weather doing in {c}"],
    "weather_fu": ["is that cold for this time of year?", "is that normal for november?", "should i bring a jacket?",
                   "would you call that warm?", "is that colder than usual?", "good day for a walk then?",
                   "do i need an umbrella?", "is that typical there?"],
    "vague": ["banana?", "hmm", "eh?", "???", "what", "ok and?", "huh", "potato", "idk", "meh", "so...", "and?",
              "wat", "hmm ok", "uhh", "ok", "?", "blah", "pineapple", "lol what"],
    "capital": ["what's the capital of {x}", "capital of {x}?", "which city is {x}'s capital", "tell me {x}'s capital city",
                "whats the capital city of {x}", "{x} capital?", "name the capital of {x}", "where's the capital of {x}"],
    "weather2": ["and what's the weather in {c}", "now {c}?", "same for {c} please", "what about {c}", "and in {c}?",
                 "how about {c} then", "check {c} too"],
    "weather_tomorrow": ["and what about tomorrow there?", "and tomorrow?", "what's tomorrow looking like there",
                         "forecast for tomorrow there?", "will it be any different tomorrow", "and the day after today?",
                         "tomorrow's weather there?"],
    "web": ["search the web for {t}", "look up {t} online", "find {t} on the internet", "search online: {t}",
            "can you search {t}", "web search {t}", "look online for {t}"],
    "summarize": ["summarize the second one in one line", "give me the first one in a sentence", "tl;dr the top result",
                  "one line on the third one please", "short summary of the first result?", "what's the gist of the second one",
                  "sum up the top one briefly"],
    "shorter": ["say that again but shorter", "shorter pls", "can you make that more brief", "too long, shorten it",
                "in fewer words?", "make it shorter", "say it in one line", "condense that"],
    "mul": ["Calculate {a} * {b}", "what's {a} times {b}", "{a} x {b} = ?", "multiply {a} by {b}", "how much is {a}*{b}",
            "{a} times {b}?", "work out {a} multiplied by {b}", "compute {a}*{b}"],
    "divide": ["now divide that by {d}", "and that divided by {d}?", "split that into {d} equal parts", "ok now / {d}",
               "take that and divide by {d}", "divide the result by {d}", "that over {d}?", "and if i divide it by {d}"],
    "first_q": ["thanks. what was the first thing I asked in this chat?", "what did i ask first here",
                "scroll back, what was my first question in this chat?", "what was the very first thing I asked you here",
                "what did this chat start with", "which question opened this chat"],
    "small_mul": ["whats {a} times {b}", "{a}x{b}?", "{a} * {b}", "how much is {a} times {b}", "{a} multiplied by {b}?"],
    "resume": ["remind me what we were talking about", "where did we leave off?", "what was this chat about again",
               "catch me up on this chat", "what were we doing here", "recap this chat for me"],
    "mem_tell": ["fyi {rel} is called {n}", "remember that {rel} is named {n}", "just so you know, {rel}'s name is {n}",
                 "please keep in mind: {rel} goes by {n}", "note this: {rel} is {n}", "for later: {rel} is called {n}"],
    "mem_ask": ["what's {rel} called again?", "do you remember the name of {rel}?", "what name did I tell you for {rel}?",
                "{rel}, what was the name?", "remind me what {rel} is called", "i forgot, what's the name of {rel}?",
                "whats the name of {rel}"],
    "rem_make": ["remind me to {t} {w}", "set a reminder {w} to {t}", "can you remind me {w} to {t}",
                 "i need a reminder to {t} {w}", "please remind me {w}: {t}", "make a reminder to {t} {w}"],
    "rem_list": ["what reminders do I have?", "show my reminders", "which reminders are set", "anything on my reminder list?",
                 "list my reminders", "what am I being reminded about"],
    "rem_cancel": ["cancel the reminder about {k}", "delete the {k} reminder", "never mind, remove the {k} reminder",
                   "scrap the reminder to {t}", "i don't need the {k} reminder anymore, cancel it"],
    "con_save": ["save a new contact: {full}, phone {ph}", "add {full} to my contacts, number {ph}",
                 "create a contact for {full} with phone {ph}", "new contact {full}, {ph}", "please store {full}'s number: {ph}",
                 "put {full} in my contacts, their phone is {ph}"],
    "con_ask": ["what's {first}'s number?", "how do I reach {first}?", "give me {full}'s phone", "do i have a number for {first}?",
                "what phone number do i have for {first}", "{first}'s number?"],
    "con_delete": ["delete the contact {full}", "remove {full} from my contacts", "get rid of {full} in contacts",
                   "please delete {first} from my contacts", "erase the contact for {full}"],
    "confirm": ["yes", "yes do it", "yep, go ahead", "sure", "confirm", "ok do it", "go for it", "yes please", "do it",
                "yeah", "correct, go", "approved"],
    "cal": ["what's on my calendar tomorrow?", "do i have anything scheduled tomorrow", "check my apple calendar for tomorrow",
            "any meetings tomorrow?", "what does my day look like tomorrow", "look at my calendar, anything tomorrow?",
            "is my calendar busy tomorrow", "what events do i have tomorrow"],
    "cal_fu": ["am i free in the afternoon?", "any free slot after lunch?", "what about the day after?", "and on friday?",
               "anything in the evening?", "when am i free that day"],
}

CITIES = ["Vilnius", "Kaunas", "Riga", "Tallinn", "Warsaw", "Berlin", "Lisbon", "Oslo", "Prague", "Vienna", "Helsinki",
          "Madrid", "Dublin", "Amsterdam", "Copenhagen", "Budapest", "Zurich", "Athens", "Krakow", "Gdansk"]
CAPITALS = {"Australia": ["canberra"], "Canada": ["ottawa"], "Brazil": ["brasilia"], "Turkey": ["ankara"],
            "Switzerland": ["bern"], "New Zealand": ["wellington"], "Morocco": ["rabat"], "Vietnam": ["hanoi"],
            "Kenya": ["nairobi"], "Peru": ["lima"], "Norway": ["oslo"], "Egypt": ["cairo"], "Pakistan": ["islamabad"],
            "Nigeria": ["abuja"], "Kazakhstan": ["astana"], "Myanmar": ["naypyidaw", "nay pyi taw"], "Bolivia": ["sucre", "la paz"],
            "Sri Lanka": ["colombo", "kotte"], "Tanzania": ["dodoma"], "Mongolia": ["ulaanbaatar", "ulan bator"]}
TOPICS = {"today's top tech news": ["tech"], "the latest football results": ["football", "soccer"],
          "news about electric cars this week": ["electric", "car", "ev"], "what's new in space exploration": ["space"],
          "the newest smartphone releases": ["phone", "smartphone"], "today's science headlines": ["science"],
          "recent news about renewable energy": ["energy", "renewable", "solar", "wind"],
          "the latest chess tournament results": ["chess"]}
PETS = ["Biscuit", "Pickle", "Mango", "Ziggy", "Waffles", "Nugget", "Pepper", "Bramble", "Tofu", "Rocket", "Juniper",
        "Clover", "Pumpkin", "Noodle", "Sprocket", "Marbles", "Truffle", "Gizmo", "Paprika", "Dumpling"]
RELS = ["my sister's dog", "my neighbour's cat", "my parrot", "my uncle's horse", "my friend's hamster", "my cousin's rabbit",
        "my grandma's goldfish", "my boss's dog", "my turtle"]
TASKS = {"water the plants": "plants", "call the dentist": "dentist", "buy oat milk": "oat milk",
         "take out the recycling": "recycling", "pay the electricity bill": "electricity", "stretch for ten minutes": "stretch",
         "renew my library books": "library", "book a haircut": "haircut", "send the birthday card": "birthday card"}
# Spoken time -> (days from today, hour) on the Mac's own clock: the reminder store must say exactly this.
WHENS = {"tomorrow at 9am": (1, 9), "tomorrow at 6pm": (1, 18), "at 10am tomorrow": (1, 10), "tomorrow at noon": (1, 12),
         "tomorrow evening at 8": (1, 20), "the day after tomorrow at 11am": (2, 11), "tomorrow at 3pm": (1, 15),
         "the day after tomorrow at 7pm": (2, 19)}
FIRST = ["Marta", "Jonas", "Elif", "Tomas", "Ines", "Kofi", "Lina", "Aron", "Mira", "Sven", "Noor", "Pavel", "Greta", "Idris"]
LAST = ["Kalnin", "Brightwater", "Okafor", "Vale", "Duarte", "Lindqvist", "Moreau", "Tanaka", "Ferreira", "Novak", "Halloran"]


# Words the earlier fixed-question gate sent: never sent again either.
EARLIER_FIXED = ['hi', 'check my inbox, how many unread emails?', 'from whom ?', 'ok which one looks most urgent', 'lol whats 2+2', 'what did I ask you about a minute ago?', 'whats the weather in Vilnius right now', 'is that cold for this time of year?', 'banana?', "what's the capital of Australia", "and what's the weather in Kaunas", 'and how many were unread again?', "search the web for today's top tech news", 'summarize the second one in one line', 'eh?', 'say that again but shorter', 'Calculate 1234 * 56', 'now divide that by 8', 'remind me what we were talking about', 'and what about tomorrow there?', 'thanks. what was the first thing I asked in this chat?', 'whats 7 times 8', 'hi there, this chat is about to be deleted']


def fold(text: str) -> str:
    """Lower case, accents off, spaces collapsed: what 'the same message' means."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def digits_only(text: str) -> str:
    return re.sub(r"(?<=\d)[,\s  '.](?=\d{3}\b)", "", text)


class Wording:
    def __init__(self, history_path: Path, seed: int):
        self.rng = random.Random(seed)
        self.seed = seed
        self.path = history_path
        self.history_ok = True
        self.patterns: dict = {}
        self.reused: list = []
        if history_path.exists():
            try:
                data = json.loads(history_path.read_text())
                self.seen = set(data.get("sent", []))
                self.patterns = dict(data.get("patterns", {}))
            except Exception as exc:
                print(f"gate-history.json unreadable ({exc}): novelty of this run's wording is UNVERIFIED", flush=True)
                self.history_ok = False
                self.seen = set()
        else:
            self.seen = set()
        self.seen |= {self._key(t) for t in EARLIER_FIXED}
        self.before = len(self.seen)
        self.used_templates: dict[str, set] = {}
        self.log: list = []

    def _key(self, text: str) -> str:
        return hashlib.sha256(fold(text).encode()).hexdigest()[:20]

    def _typo(self, text: str, protected: list[str]) -> str:
        words = text.split(" ")
        keep = {fold(x) for p in protected for x in re.split(r"[\s']", p)}
        idx = [i for i, w in enumerate(words) if w.isalpha() and len(w) >= 5 and fold(w) not in keep]
        if not idx:
            return text
        i = self.rng.choice(idx)
        w = words[i]
        j = self.rng.randrange(1, len(w) - 2)
        words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
        return " ".join(words)

    def say(self, intent: str, *, decorate: bool = True, **slots) -> str:
        pool = POOLS[intent]
        protected = [str(v) for v in slots.values()]
        used = self.used_templates.setdefault(intent, set())
        for attempt in range(400):
            fresh = [t for t in pool if t not in used] or pool
            # Least-exposed sentence pattern first: a pattern earlier runs used is the last resort.
            low = min(self.patterns.get(self._key(intent + "|" + t), 0) for t in fresh)
            template = self.rng.choice([t for t in fresh if self.patterns.get(self._key(intent + "|" + t), 0) == low])
            text = template.format(**slots)
            if decorate:
                opener = self.rng.choice(OPENERS) if attempt < 200 else self.rng.choice(OPENERS[3:])
                ending = "" if text.rstrip().endswith("?") and self.rng.random() < 0.7 else self.rng.choice(ENDINGS)
                text = opener + text + ending
                roll = self.rng.random()
                if roll < 0.45:
                    text = text.lower()
                elif roll < 0.75:
                    text = text[:1].upper() + text[1:]
                if self.rng.random() < 0.2:
                    text = self._typo(text, protected)
            elif attempt >= 12:
                text = text + self.rng.choice([".", "!", " then", " now", " please", " :)", "!!"])
            if attempt >= 300:
                text = text + " " + self.rng.choice(["(again)", "- quick one", "real quick", "for real", "tho"])
            key = self._key(text)
            if key not in self.seen:
                self.seen.add(key)
                used.add(template)
                pkey = self._key(intent + "|" + template)
                if self.patterns.get(pkey):
                    self.reused.append({"intent": intent, "pattern": template, "earlier_runs": self.patterns[pkey]})
                self.patterns[pkey] = self.patterns.get(pkey, 0) + 1
                self.log.append({"intent": intent, "template": template, "message": text})
                self._persist()
                return text
        raise RuntimeError(f"no unused wording left for {intent}")

    def _persist(self) -> None:
        try:
            self.path.write_text(json.dumps({"sent": sorted(self.seen), "patterns": self.patterns}))
        except Exception as exc:
            if self.history_ok:
                print(f"gate-history.json not writable ({exc}): later runs cannot avoid these words", flush=True)
            self.history_ok = False

    # Random values for one run.
    def plan(self) -> dict:
        r = self.rng
        city1, city2 = r.sample(CITIES, 2)
        country = r.choice(sorted(CAPITALS))
        topic = r.choice(sorted(TOPICS))
        a, b = r.randint(120, 9800), r.randint(13, 97)
        d = r.choice([2, 4, 5, 8])
        while (a * b) % d:
            a += 1
        task, task2 = r.sample(sorted(TASKS), 2)
        when, when2 = r.sample(sorted(WHENS), 2)
        first = r.choice(FIRST)
        last, last2 = r.sample(LAST, 2)
        tail = f"{r.randint(0, 99):02d}"
        return {
            "city1": city1, "city2": city2, "country": country, "capital": CAPITALS[country],
            "topic": topic, "topic_words": TOPICS[topic],
            "add": (r.randint(11, 99), r.randint(11, 99)), "mul": (a, b), "div": d,
            "small": (r.randint(3, 12), r.randint(3, 12)),
            "pet": r.choice(PETS), "rel": r.choice(RELS),
            "task": task, "task_key": TASKS[task], "when": when,
            "task2": task2, "task2_key": TASKS[task2], "when2": when2,
            "first": first, "last": last, "full": f"{first} {last}",
            # Both numbers are reserved for fiction (US 555-0100..0199, UK 020 7946 0000..0999) and share their
            # last four digits, so a lookup that answers with the wrong same-name contact cannot pass.
            "phone": f"+1 555 01{tail}", "decoy_full": f"{first} {last2}", "decoy_phone": f"+44 20 7946 01{tail}",
        }


BASE = os.environ.get("VOOL_GATE_BASE", "http://127.0.0.1:11435")
REFUSED = "did not run this turn"
NOT_SENT = ("could not be sent", "wasn't sent", "was not sent", "too busy to take this message", "answering too slowly")
OUT = Path(".")
TURNS: list = []
RESULTS: dict = {}  # label -> {"status", "reasons"}; frozen plan first, every planned label ends with a status
PAGE_ERRORS: list = []

STATE_JS = """() => { const r = (typeof view !== 'undefined') ? view.run : null; const bar = document.getElementById('permBar');
  return { turn: r ? String(r.turnId || '') : '', released: !!(r && r.released), status: r ? String(r.status || '') : '',
           perm: !!(bar && !bar.hidden), permMsg: bar && !bar.hidden ? (bar.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 300) : '',
           chat: (typeof displayedChat !== 'undefined') ? String(displayedChat || '') : '',
           model: ((document.getElementById('modelLbl') || {}).textContent || '').trim(),
           mode: (typeof view !== 'undefined') ? String(view.mode || '') : '' }; }"""
# Every bubble in order, with only its answer body (.msg-text): never the model/token footer or buttons.
BUBBLES_JS = """() => Array.from(document.querySelectorAll('#log .msg')).map((el) => ({
  user: el.classList.contains('user'),
  text: (((el.querySelector('.msg-text') || el).textContent) || '').trim() }))"""

PLAN = [
    "A1-greet", "A2-inbox", "A3-from", "A4-urgent", "A5-add", "model-cloud", "A6-last-question", "A6b-recap",
    "B1-weather", "B1-task-bypass", "B2-followup", "B3-vague", "B4-capital", "bypass-session", "B5-under-session-bypass",
    "A7-back-in-chat",
    "C1-web", "C2-followup", "C3-vague", "model-local", "C4-shorter", "C5-multiply", "C6-divide-that",
    "E1-tell-fact", "E2-reminder", "E2b-reminder-decoy",
    "F1-fact-in-other-chat", "F2-reminder-list", "F3-contact-save", "F3b-contact-decoy", "F4-contact-lookup",
    "F5-calendar", "F6-calendar-followup",
    "restart",
    "A8-after-restart", "B6-after-restart", "C7-after-restart", "C8-basic", "model-cloud-2",
    "G1-fact-after-restart", "G2-contact-after-restart", "G3-reminder-cancel", "G4-reminders-after-cancel",
    "G5-contact-delete", "G6-cleanup-decoys", "D1-greet", "D2-delete-chat", "page-errors",
]


def version() -> dict:
    try:
        with urlopen(BASE + "/api/runtime/version", timeout=3) as r:
            return json.load(r)
    except Exception:
        return {}


def api(page, path: str):
    """A read through the app's own page (same origin, same session), independent of any chat answer."""
    return page.evaluate("async (p) => { const r = await fetch(p, {cache: 'no-store'}); return {status: r.status, body: await r.text()}; }", path)


def save() -> None:
    (OUT / "gate-turns.json").write_text(json.dumps(TURNS, indent=2, ensure_ascii=False))
    (OUT / "gate-result.json").write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False))


def mark(label: str, status: str, *reasons: str) -> None:
    RESULTS[label] = {"status": status, "reasons": [r for r in reasons if r]}
    save()
    print(f"  => {label}: {status}" + (f" ({'; '.join(r for r in reasons if r)})" if any(reasons) else ""), flush=True)


def listener_pid() -> str:
    try:
        return subprocess.run(["lsof", "-ti", "tcp:" + BASE.rsplit(":", 1)[-1], "-sTCP:LISTEN"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def approve(page, how: str) -> str:
    if how == "bypass_task":
        try:
            page.click("#modeBtn", timeout=5000)
            page.click('#modePop .pop-item[data-mode="bypass_permissions"]', timeout=5000)
            page.wait_for_selector("#bypassOverlay:not([hidden])", timeout=8000)
            page.select_option("#bypassScope", "task")
            time.sleep(1.5)
            if page.is_enabled("#bypassConfirm"):
                page.click("#bypassConfirm", timeout=5000)
                time.sleep(3)
                if not page.evaluate(STATE_JS)["perm"]:
                    return "bypass_task"
                note = "bypass_task_then_"
            else:
                ready = page.text_content("#bypassReadiness") or ""
                page.click("#bypassCancel", timeout=5000)
                note = "bypass_unavailable(" + ready.strip()[:120] + ")_then_"
        except Exception as exc:
            note = "bypass_error(" + str(exc).splitlines()[0][:120] + ")_then_"
        page.click("#permTask", timeout=5000)
        return note + "allow_for_task"
    target = {"once": "#permOnce", "task": "#permTask"}[how]
    page.click(target, timeout=5000)
    return "allow_" + how


def send(page, text: str, *, label: str, approve_how: str = "task", private: bool = False, limit: float = float(os.environ.get("VOOL_GATE_TURN_LIMIT", "300"))) -> dict:
    before = page.evaluate(STATE_JS)
    before_count = len(page.evaluate(BUBBLES_JS))
    started = time.time()
    page.fill("#input", text)
    page.click("#send")
    approvals: list = []
    approved_at = 0.0
    state = before
    while time.time() - started < limit:
        time.sleep(0.5)
        state = page.evaluate(STATE_JS)
        if state["perm"] and len(approvals) < 3:
            approvals.append({"asked": state["permMsg"], "answer": approve(page, approve_how), "at": round(time.time() - started, 1)})
            approved_at = time.time()
            continue
        new_turn = state["turn"] and state["turn"] != before["turn"]
        if new_turn and state["released"] and not state["perm"] and state["status"] != "awaiting_approval":
            if approved_at and time.time() - approved_at < 4:
                continue
            if len(page.evaluate(BUBBLES_JS)) >= before_count + 2:
                break
    time.sleep(1.0)
    # Bind the answer to THIS turn: only bubbles after this message's own user bubble count.
    bubbles = page.evaluate(BUBBLES_JS)[before_count:]
    first_user = next((i for i, b in enumerate(bubbles) if b["user"]), None)
    after = bubbles[first_user + 1:] if first_user is not None else bubbles
    answers = [b["text"] for b in after if not b["user"]]
    state = page.evaluate(STATE_JS)
    turn = {
        "label": label, "chat": state["chat"], "model": state["model"], "mode_after": state["mode"],
        "message": text, "reply": answers[-1] if answers else "", "all_replies": answers,
        "user_bubble_seen": first_user is not None, "status": state["status"],
        "seconds": round(time.time() - started, 1), "approvals": approvals,
        "refused_shown": any(REFUSED in a for a in answers), "timed_out": time.time() - started >= limit, "private": private,
    }
    TURNS.append(turn)
    save()
    shown = f"[private reply, {len(turn['reply'])} chars]" if private else turn["reply"][:220].replace("\n", " ")
    print(f"{label} | {turn['model']} | {turn['seconds']}s | {turn['status']} | approvals={[a['answer'] for a in approvals]} | {text!r} -> {shown}", flush=True)
    return turn


def base_problems(turn: dict) -> list[str]:
    """What makes a turn a failure whatever it says."""
    out = []
    if not turn.get("user_bubble_seen"):
        out.append("the message never appeared in the chat")
    if turn.get("timed_out"):
        out.append(f"no finished answer within {int(turn.get('seconds', 0))} s")
    if not turn.get("reply"):
        out.append("no answer")
    if turn.get("refused_shown"):
        out.append("'did not run this turn' shown")
    if str(turn.get("status", "")).lower() in {"failed", "error", "cancelled"}:
        out.append(f"turn status {turn['status']}")
    if any(p in fold(turn.get("reply", "")) for p in NOT_SENT):
        out.append("'message could not be sent' shown")
    return out


def check(reply: str, expect: dict | None) -> list[str]:
    """Answer-body checks against a key fixed before the question was asked. Returns what is wrong."""
    if not expect:
        return []
    text = fold(reply)
    nums = digits_only(reply)
    bare = re.sub(r"\D", "", reply)
    wrong = []
    for group in expect.get("all", []):
        if not any(fold(w) in text for w in group):
            wrong.append("missing " + "/".join(group))
    if expect.get("any") and not any(fold(w) in text for w in expect["any"]):
        wrong.append("expected one of " + "/".join(expect["any"]))
    for n in expect.get("numbers", []):
        if not re.search(rf"(?<!\d){n}(?!\d)", nums):
            wrong.append(f"expected the number {n}")
    if expect.get("digits") and expect["digits"] not in bare:
        wrong.append(f"expected the full number {expect['digits']}")
    for bad in expect.get("none", []):
        if fold(bad) in text:
            wrong.append(f"should not say {bad}")
    for bad in expect.get("no_digits", []):
        if bad in bare:
            wrong.append(f"gave the other contact's number ({bad})")
    if expect.get("temperature") and not re.search(r"-?\d+(?:[.,]\d+)?\s*(?:°|degrees?\b|º)", reply, re.IGNORECASE):
        wrong.append("no temperature in the answer")
    return wrong


CONFIRM_ASK = ("confirm", "should i", "shall i", "do you want", "want me to", "ok to", "go ahead", "is that right", "save it?")


def needs_confirm(reply: str) -> bool:
    t = fold(reply)
    return "?" in t and any(w in t for w in CONFIRM_ASK)


def pick_model(page, kind: str) -> str:
    page.click("#modelBtn", timeout=5000)
    time.sleep(1.5)
    items = page.evaluate("() => Array.from(document.querySelectorAll('#modelPop .pop-item[data-model]')).map((b) => ({ m: b.dataset.model, t: (b.textContent || '').trim() }))")
    choice = None
    if kind == "local":
        choice = next((i for i in items if "qwen3:8b" in (i["m"] + i["t"]).lower()), None)
    else:
        # A free cloud model only: a paid or unknown-price pick opens the price confirmation, which
        # is a person's decision, never the gate's.
        free = [i for i in items if "/" in i["m"] and (":free" in i["m"] or "free" in i["t"].lower())]
        choice = next((i for i in free if "glm" in (i["m"] + i["t"]).lower()), None) or (free[0] if free else None)
    if not choice:
        page.keyboard.press("Escape")
        TURNS.append({"label": "model-switch", "wanted": kind, "result": "not in picker", "items": items})
        save()
        print(f"model switch to {kind}: NOT IN PICKER; items={[i['m'] for i in items]}", flush=True)
        return ""
    page.click(f'#modelPop .pop-item[data-model="{choice["m"]}"]', timeout=5000)
    time.sleep(1.5)
    if page.evaluate("() => { const o = document.getElementById('vgOverlay'); return !!(o && !o.hidden && o.offsetParent !== null); }"):
        page.keyboard.press("Escape")
        time.sleep(0.5)
        TURNS.append({"label": "model-switch", "wanted": kind, "result": "price confirmation asked; left to a person", "model": choice["m"]})
        save()
        print(f"model switch to {kind}: {choice['m']} asked for a price confirmation; skipped", flush=True)
        return ""
    TURNS.append({"label": "model-switch", "wanted": kind, "result": choice["m"]})
    save()
    print(f"model switch to {kind}: {choice['m']}", flush=True)
    return choice["m"]


def open_chat(page, chat_id: str) -> bool:
    """Open a chat by clicking its sidebar row, as a person does."""
    row = page.locator(f'#sessions .session[data-id="{chat_id}"] .s-title').first
    try:
        row.wait_for(state="attached", timeout=15000)
        row.scroll_into_view_if_needed(timeout=5000)
        row.click(timeout=5000)
    except Exception:
        return False
    deadline = time.time() + 15
    while time.time() < deadline and page.evaluate("displayedChat") != chat_id:
        time.sleep(0.3)
    time.sleep(1.5)
    return page.evaluate("displayedChat") == chat_id


def new_chat(page) -> str:
    before = page.evaluate("displayedChat")
    page.click("#newChat")
    deadline = time.time() + 10
    while time.time() < deadline and page.evaluate("displayedChat") == before:
        time.sleep(0.3)
    return page.evaluate("displayedChat")


def bypass_session(page) -> dict:
    """Bypass permissions for this chat session through the dialog, as a person does."""
    t0 = time.time()
    page.click("#modeBtn", timeout=5000)
    page.click('#modePop .pop-item[data-mode="bypass_permissions"]', timeout=5000)
    page.wait_for_selector("#bypassOverlay:not([hidden])", timeout=8000)
    page.select_option("#bypassScope", "session")
    page.click("#bypassConfirm", timeout=5000)
    deadline = time.time() + 30
    while time.time() < deadline:
        if page.evaluate("view.mode") == "bypass_permissions" or not page.is_hidden("#bypassError"):
            break
        time.sleep(0.3)
    out = {"label": "bypass-session", "mode": page.evaluate("view.mode"), "seconds": round(time.time() - t0, 1),
           "hint": (page.text_content("#bypassReadiness") or "").strip(),
           "error": "" if page.is_hidden("#bypassError") else (page.text_content("#bypassError") or "").strip()}
    if not page.is_hidden("#bypassOverlay"):
        page.click("#bypassCancel", timeout=5000)
    TURNS.append(out)
    save()
    return out


def delete_chat(page, chat_id: str) -> dict:
    """Delete a chat from the sidebar, then check the app's own chat list no longer has it."""
    open_chat(page, chat_id)
    t0 = time.time()
    page.locator("#sessions .session.active").first.hover()
    page.locator("#sessions .session.active .s-act[title^='Delete chat']").first.click(timeout=5000)
    page.wait_for_selector("#confirmPromptDialog[open]", timeout=5000)
    page.locator("#confirmPromptDialog .confirm-yes").click()
    shown = None
    gone = False
    deadline = time.time() + 120
    while time.time() < deadline:
        if shown is None and page.evaluate("() => !!document.querySelector('#sessions .session.deleting')"):
            shown = round(time.time() - t0, 1)
        gone = page.evaluate("(id) => displayedChat !== id && ![...document.querySelectorAll('#sessions .session')].some((el) => el.dataset.id === id)", chat_id)
        if gone:
            break
        time.sleep(0.3)
    listed = api(page, "/api/chat/sessions")
    out = {"label": "delete-chat", "deleting_shown_s": shown, "gone_s": round(time.time() - t0, 1), "gone_from_sidebar": bool(gone),
           "still_in_chat_list": chat_id in listed["body"], "list_status": listed["status"]}
    TURNS.append(out)
    save()
    return out


def restart_app() -> dict:
    """Quit and reopen VOOL. The installed app by default; a throwaway server copy when
    VOOL_GATE_START_CMD (a shell line that starts it) is set: its listening process is stopped
    with SIGTERM and the line is run again."""
    t0 = time.time()
    start_cmd = os.environ.get("VOOL_GATE_START_CMD", "")
    if start_cmd:
        pid = listener_pid().split("\n")[0]
        if not pid:
            return {"restart": "no process listening on " + BASE}
        subprocess.run(["kill", "-TERM", pid], check=False)
        while time.time() - t0 < 90 and version():
            time.sleep(1)
        down = round(time.time() - t0, 1)
        if version():
            return {"restart": "server did not stop within 90 s", "down_s": down}
        t1 = time.time()
        subprocess.Popen(["/bin/sh", "-c", start_cmd], start_new_session=True)
        while time.time() - t1 < 300 and not version():
            time.sleep(0.5)
        return {"quit_s": down, "start_s": round(time.time() - t1, 1), "version": version()}
    if urlparse(BASE).port not in (None, 11435):
        # Pointed at a second copy (a throwaway profile): never quit the person's installed app.
        return {"restart": "refused: VOOL_GATE_BASE is not the installed app and VOOL_GATE_START_CMD is unset"}
    subprocess.run(["osascript", "-e", 'quit app "VOOL"'], check=False)
    while time.time() - t0 < 90 and version():
        time.sleep(1)
    down = round(time.time() - t0, 1)
    if version():
        return {"restart": "app did not quit within 90 s", "down_s": down}
    t1 = time.time()
    subprocess.run(["open", "/Applications/VOOL.app"], check=False)
    while time.time() - t1 < 300 and not version():
        time.sleep(0.5)
    up = version()
    return {"quit_s": down, "start_s": round(time.time() - t1, 1), "version": up}


def page_open(browser_ctx):
    page = browser_ctx.new_page()
    page.on("pageerror", lambda e: PAGE_ERRORS.append(str(e)[:300]))
    page.goto(BASE + "/chat", wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=20000)
    deadline = time.time() + 20
    while time.time() < deadline and not page.evaluate("typeof displayedChat === 'string' && !!displayedChat"):
        time.sleep(0.3)
    return page


STATUSES = ("PASS", "FAIL", "BLOCKED", "ERROR", "REVIEW", "NOT RUN")


def main() -> int:
    global OUT
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    OUT = Path(args[0] if args else ".").resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    seed_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--seed=")), "")
    seed = int(seed_arg) if seed_arg else random.SystemRandom().randrange(1, 2**31)
    W = Wording(Path(__file__).resolve().with_name("gate-history.json"), seed)
    P = W.plan()
    for label in PLAN:
        RESULTS[label] = {"status": "NOT RUN", "reasons": []}
    v = version()
    print("version:", json.dumps(v)[:300], flush=True)
    print(f"seed={seed} earlier_messages_never_reused={W.before} history={'ok' if W.history_ok else 'UNREADABLE: novelty unverified'}", flush=True)
    TURNS.append({"label": "plan", "seed": seed, "plan": P, "planned": PLAN, "version": v})
    save()
    if not v:
        print(f"app not answering on {BASE}", flush=True)
        return 2

    def ask(page, intent, label, *, expect=None, approve_how="task", private=False, decorate=True, review="", verify=None, **slots):
        """One turn: fresh words, the turn's own answer, the answer key, then an independent read where one exists."""
        text = W.say(intent, decorate=decorate, **slots)
        turn = send(page, text, label=label, approve_how=approve_how, private=private)
        turn["intent"] = intent
        problems = base_problems(turn)
        if not problems:
            problems += check(turn.get("reply", ""), expect)
        if not problems and verify:
            problems += verify()
        turn["problems"] = problems
        turn["expect"] = expect or {}
        save()
        if problems:
            mark(label, "FAIL", *problems)
        elif review:
            mark(label, "REVIEW", review)
        else:
            mark(label, "PASS")
        return turn

    def ask_confirming(page, intent, label, **kw):
        turn = ask(page, intent, label, **kw)
        if needs_confirm(turn.get("reply", "")) and RESULTS[label]["status"] == "FAIL":
            # VOOL asked to confirm: confirm once, then grade again on the confirmed result.
            conf = send(page, W.say("confirm", decorate=False), label=label + "-confirm")
            problems = base_problems(conf) or (kw.get("verify")() if kw.get("verify") else [])
            conf["problems"] = problems
            save()
            mark(label, "FAIL" if problems else "PASS", *problems, "needed a confirmation")
        return turn

    def contacts(page, query: str) -> str:
        r = api(page, "/api/contacts?q=" + quote(query))
        return r["body"] if r["status"] == 200 else ""

    def reminders(page, chat: str) -> list:
        r = api(page, "/api/reminders?session_id=" + quote(chat))
        try:
            return json.loads(r["body"]).get("reminders", []) if r["status"] == 200 else []
        except Exception:
            return []

    def due_ok(row: dict, when: str) -> bool:
        days, hour = WHENS[when]
        want = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        wall = str(row.get("due_wall") or "")
        return want in wall and f"{hour:02d}:00" in wall

    def switch(page, kind: str, label: str) -> None:
        before = page.evaluate(STATE_JS)["model"]
        try:
            got = pick_model(page, kind)
        except Exception as exc:
            mark(label, "ERROR", f"picker: {str(exc).splitlines()[0][:150]}")
            return
        after = page.evaluate(STATE_JS)["model"]
        if not got:
            mark(label, "BLOCKED", f"no usable {kind} model in the picker (or it asked for a price confirmation)")
        elif after == before:
            mark(label, "FAIL", f"picked {got} but the model label stayed '{after}'")
        else:
            mark(label, "PASS", f"{before} -> {after}")

    a1, a2 = P["add"]
    m1, m2 = P["mul"]
    s1, s2 = P["small"]
    pet = dict(rel=P["rel"], n=P["pet"])
    phone_digits = re.sub(r"\D", "", P["phone"])[1:]
    decoy_digits = re.sub(r"\D", "", P["decoy_phone"])
    chats: dict = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = page_open(ctx)
        try:
            # A: email task approved for the task, follow-ups, silly maths, free cloud model, two context asks.
            chats["a"] = new_chat(page)
            ask(page, "greet", "A1-greet", expect={"none": ["hola", "bonjour", "hallo", "ciao", "privet"]})
            mailbox = "grade against your own inbox (no independent mailbox reader on this Mac)"
            ask(page, "email_count", "A2-inbox", approve_how="task", private=True, review=mailbox)
            ask(page, "email_from", "A3-from", private=True, review=mailbox)
            ask(page, "email_urgent", "A4-urgent", private=True, review=mailbox)
            ask(page, "add", "A5-add", a=a1, b=a2, expect={"numbers": [a1 + a2]})
            switch(page, "cloud", "model-cloud")
            ask(page, "last_q", "A6-last-question", expect={"all": [[str(a1), str(a1 + a2)], [str(a2), str(a1 + a2)]]})
            ask(page, "recap", "A6b-recap", private=True, expect={"all": [["mail", "inbox", "unread"], [str(a1 + a2), "add", "sum", "plus", str(a1)]]})

            # B: weather under a task bypass, follow-ups, vague, basic, session bypass, second city.
            chats["b"] = new_chat(page)
            t = ask(page, "weather", "B1-weather", approve_how="bypass_task", c=P["city1"], expect={"temperature": True, "any": [P["city1"]]})
            asked = [a["answer"] for a in t.get("approvals", [])]
            if not asked:
                mark("B1-task-bypass", "BLOCKED", "the weather question asked for no approval, so the task bypass was not exercised")
            elif asked[0] == "bypass_task":
                mark("B1-task-bypass", "PASS")
            else:
                mark("B1-task-bypass", "FAIL", f"asked for a task bypass, the app gave {asked[0]}")
            ask(page, "weather_fu", "B2-followup")
            ask(page, "vague", "B3-vague")
            ask(page, "capital", "B4-capital", x=P["country"], expect={"any": P["capital"]})
            bp = bypass_session(page)
            mark("bypass-session", "PASS" if bp["mode"] == "bypass_permissions" else "FAIL",
                 "" if bp["mode"] == "bypass_permissions" else f"mode stayed {bp['mode']}: {bp['error'] or bp['hint']}")
            t = ask(page, "weather2", "B5-under-session-bypass", c=P["city2"], expect={"temperature": True, "any": [P["city2"]]})
            if t.get("approvals") and RESULTS["B5-under-session-bypass"]["status"] == "PASS":
                mark("B5-under-session-bypass", "FAIL", "asked for approval while the session bypass was on")
            with contextlib.suppress(Exception):
                page.click("#bypassRevoke", timeout=5000)

            if open_chat(page, chats["a"]):
                ask(page, "email_again", "A7-back-in-chat", private=True, review=mailbox)
            else:
                mark("A7-back-in-chat", "FAIL", "chat A not openable from the sidebar")

            # C: web search allowed once, follow-ups, local qwen3:8b, tool maths and a follow-up on it.
            chats["c"] = new_chat(page)
            ask(page, "web", "C1-web", approve_how="once", t=P["topic"], review="check the results are about the topic and current")
            ask(page, "summarize", "C2-followup")
            ask(page, "vague", "C3-vague")
            switch(page, "local", "model-local")
            ask(page, "shorter", "C4-shorter")
            ask(page, "mul", "C5-multiply", a=m1, b=m2, expect={"numbers": [m1 * m2]})
            ask(page, "divide", "C6-divide-that", d=P["div"], expect={"numbers": [m1 * m2 // P["div"]]})

            # E: tell a fact, make a reminder and a decoy. Each is checked in the app's own reminder store.
            chats["e"] = new_chat(page)
            ask(page, "mem_tell", "E1-tell-fact", **pet)

            def has_reminder(key, when, *, present=True):
                def v():
                    rows = reminders(page, chats["e"])
                    hit = [r for r in rows if fold(key) in fold(str(r.get("note", "")))]
                    if present and not hit:
                        return [f"no reminder about '{key}' in the reminder store"]
                    if present and when and not any(due_ok(r, when) for r in hit):
                        return [f"reminder '{key}' stored for {[r.get('due_wall') for r in hit]}, asked for {when}"]
                    if not present and hit:
                        return [f"reminder '{key}' still scheduled: {[r.get('status') for r in hit]}"]
                    return []
                return v

            ask_confirming(page, "rem_make", "E2-reminder", t=P["task"], w=P["when"], verify=has_reminder(P["task_key"], P["when"]))
            ask_confirming(page, "rem_make", "E2b-reminder-decoy", t=P["task2"], w=P["when2"], verify=has_reminder(P["task2_key"], P["when2"]))

            # F: a new chat must know the fact and the reminders; contacts checked in the contact book.
            chats["f"] = new_chat(page)
            ask(page, "mem_ask", "F1-fact-in-other-chat", rel=P["rel"], expect={"any": [P["pet"]]})
            ask(page, "rem_list", "F2-reminder-list", expect={"any": [P["task_key"]]})

            def in_book(name, digits, *, present=True):
                def v():
                    body = contacts(page, name)
                    found = fold(name) in fold(body) and digits in re.sub(r"\D", "", body)
                    if present and not found:
                        return [f"{name} with that number is not in the contact book"]
                    if not present and fold(name) in fold(body):
                        return [f"{name} is still in the contact book"]
                    return []
                return v

            ask_confirming(page, "con_save", "F3-contact-save", full=P["full"], ph=P["phone"], verify=in_book(P["full"], phone_digits))
            ask_confirming(page, "con_save", "F3b-contact-decoy", full=P["decoy_full"], ph=P["decoy_phone"], verify=in_book(P["decoy_full"], decoy_digits))
            look = {"digits": phone_digits, "no_digits": [decoy_digits]}
            ask(page, "con_ask", "F4-contact-lookup", first=P["first"], full=P["full"], expect=look)
            cal = "compare with Apple Calendar for that day (no independent calendar reader yet)"
            ask(page, "cal", "F5-calendar", private=True, review=cal)
            ask(page, "cal_fu", "F6-calendar-followup", private=True, review=cal)

            page.close()
            pid_before = listener_pid()
            r = restart_app()
            pid_after = listener_pid()
            r.update({"pid_before": pid_before, "pid_after": pid_after})
            TURNS.append({"label": "restart", **r})
            save()
            if not r.get("version"):
                mark("restart", "FAIL", f"app did not come back: {r}")
            elif pid_before and pid_after and pid_before == pid_after:
                mark("restart", "FAIL", "same process still listening: the app never restarted")
            else:
                mark("restart", "PASS", f"quit {r.get('quit_s')} s, started {r.get('start_s')} s")
            page = page_open(ctx)

            # After the restart: every chat reopens from the sidebar and continues; memory, contact, reminders survive.
            for chat, label, intent, kw in (
                ("a", "A8-after-restart", "resume", {"private": True, "expect": {"any": ["mail", "inbox", "unread", "email"]}}),
                ("b", "B6-after-restart", "weather_tomorrow", {"expect": {"any": [P["city2"], P["city1"]]}}),
                ("c", "C7-after-restart", "first_q", {"expect": {"any": P["topic_words"]}}),
            ):
                if open_chat(page, chats[chat]):
                    ask(page, intent, label, **kw)
                else:
                    mark(label, "FAIL", "chat not in the sidebar after the restart")
            ask(page, "small_mul", "C8-basic", a=s1, b=s2, expect={"numbers": [s1 * s2]})
            switch(page, "cloud", "model-cloud-2")
            chats["g"] = new_chat(page)
            ask(page, "mem_ask", "G1-fact-after-restart", rel=P["rel"], expect={"any": [P["pet"]]})
            ask(page, "con_ask", "G2-contact-after-restart", first=P["first"], full=P["full"], expect=look)

            def cancel_ok():
                return has_reminder(P["task_key"], "", present=False)() + has_reminder(P["task2_key"], P["when2"])()

            ask_confirming(page, "rem_cancel", "G3-reminder-cancel", k=P["task_key"], t=P["task"], verify=cancel_ok)
            ask(page, "rem_list", "G4-reminders-after-cancel", expect={"none": [P["task_key"]], "any": [P["task2_key"]]})
            ask_confirming(page, "con_delete", "G5-contact-delete", full=P["full"], first=P["first"],
                           verify=lambda: in_book(P["full"], phone_digits, present=False)() + in_book(P["decoy_full"], decoy_digits)())
            # Clean up the decoys so the next run starts clean; a failure here is reported, not hidden.
            t1 = ask_confirming(page, "con_delete", "G6-cleanup-decoys", full=P["decoy_full"], first=P["decoy_full"].split()[0],
                                verify=in_book(P["decoy_full"], decoy_digits, present=False))
            if RESULTS["G6-cleanup-decoys"]["status"] == "PASS":
                ask_confirming(page, "rem_cancel", "G6-cleanup-decoys", k=P["task2_key"], t=P["task2"],
                               verify=has_reminder(P["task2_key"], "", present=False))
            del t1

            chats["d"] = new_chat(page)
            ask(page, "greet", "D1-greet", expect={"none": ["hola", "bonjour", "hallo", "ciao", "privet"]})
            d = delete_chat(page, chats["d"])
            if d["gone_from_sidebar"] and not d["still_in_chat_list"] and d["list_status"] == 200:
                mark("D2-delete-chat", "PASS", f"deleting shown at {d['deleting_shown_s']} s, gone at {d['gone_s']} s")
            else:
                mark("D2-delete-chat", "FAIL", f"sidebar gone={d['gone_from_sidebar']}, still in the app's chat list={d['still_in_chat_list']}")
        except Exception as exc:
            # The gate itself broke: say where, and every step it never reached stays NOT RUN.
            reached = [k for k, r in RESULTS.items() if r["status"] != "NOT RUN"]
            mark("gate-error", "ERROR", f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]} (after {reached[-1] if reached else 'start'})")
        finally:
            mark("page-errors", "FAIL" if PAGE_ERRORS else "PASS", *PAGE_ERRORS[:5])
            TURNS.append({"label": "wording", "seed": seed, "messages": W.log, "template_reuse": W.reused})
            save()
            browser.close()

    counts = {s: sum(1 for r in RESULTS.values() if r["status"] == s) for s in STATUSES}
    accepted = all(r["status"] == "PASS" for r in RESULTS.values())
    print("", flush=True)
    print(f"DECISION: {'PASS' if accepted else 'NOT PASSED'}  " + "  ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
    for label, r in RESULTS.items():
        if r["status"] != "PASS":
            print(f"  {r['status']:8} {label}: {'; '.join(r['reasons'])}", flush=True)
    if W.reused:
        print(f"  note: {len(W.reused)} phrasings reuse a sentence pattern an earlier run used (new words, same pattern)", flush=True)
    print(f"test contacts: {P['full']} {P['phone']} and {P['decoy_full']} {P['decoy_phone']} (fictional numbers); "
          f"reminders: {P['task']} {P['when']} / {P['task2']} {P['when2']}", flush=True)
    print("raw:", OUT / "gate-turns.json", "| verdicts:", OUT / "gate-result.json", flush=True)
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
