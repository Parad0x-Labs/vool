#!/usr/bin/env python3
"""Run the acceptance scenario library against a running VOOL through its real chat page.

Every graded turn is typed into the composer of the chat page the app shows, sent with Send, and
read back from the answer on screen. Approval cards are answered the way the scenario says, Stop is
pressed when the scenario says, and the page is reloaded when the scenario says. The result is a
graded pass table (right / helpful / plain per answer) plus the raw turns and screenshots.

Standard: docs/acceptance/STANDARD.md. Library format: docs/acceptance/scenarios/README.md.

Usage (from a checkout with the dev extras, which include playwright):

    # throwaway profile, started by the runner from the app code under test
    python ops/acceptance/run_acceptance.py --launch --source /path/to/vool-code --out ./acc-run

    # an app that is already running (a throwaway instance you started yourself)
    python ops/acceptance/run_acceptance.py --base-url http://127.0.0.1:11635 --out ./acc-run

    # the installed app on the person's own profile: only `profile: any` scenarios run
    python ops/acceptance/run_acceptance.py --base-url http://127.0.0.1:11435 --real-profile --out ./acc-run

    # check the library and list the cases without touching any app
    python ops/acceptance/run_acceptance.py --list

    # regrade a finished run after a reviewer filled review.json
    python ops/acceptance/run_acceptance.py --apply-review ./acc-run
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import random
import re
import signal
import socket
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_DIR = REPO_ROOT / "docs" / "acceptance" / "scenarios"
DEFAULT_BASE_URL = "http://127.0.0.1:11435"
DEFAULT_MAX_SECONDS = 180.0
TURN_TIMEOUT_SECONDS = 900.0

# --------------------------------------------------------------------------------------- grading

#: Developer text a person must never see. Each entry has an id a scenario can allow with
#: `plain_allow` when the words are legitimately part of the answer (a saved PDF's file path).
PLAIN_BANNED: list[tuple[str, str, str]] = [
    ("provided_context", r"(?i)\b(provided|given|supplied|above|current) (context|information|documents?|data)\b|\bin (the|this) context\b",
     "talks about 'context' instead of the person's chats"),
    ("ai_disclaimer", r"(?i)\bas an ai\b|\blanguage model\b|\bmy training data\b", "AI disclaimer"),
    ("tool_name", r"\b(email|memory|calendar|reminders?|browser|pdf|skills?|web|files?|chat)\.[a-z_]{3,}(\.[a-z_]+)*\b",
     "internal tool name"),
    ("json", r"(?m)^\s*[\{\[]\s*\"|\"[a-z_]{2,}\"\s*:\s*[\"\[\{\dtfn]", "raw JSON"),
    ("traceback", r"Traceback \(most recent call last\)|\b[A-Z][A-Za-z]+(Error|Exception)\b(:|\s+at\b)", "error trace"),
    ("internal_ids", r"\b(session_id|turn_id|request_id|tool_call|tool_calls|payload|endpoint|status code|HTTP [45]\d\d|stack trace)\b",
     "internal words"),
    ("path", r"(/Users/[^\s]+|/home/[^\s]+|~/\.[^\s]+|\bVOOL_[A-Z_]{3,}\b)", "file path or setting name"),
    ("cant_publish", r"(?i)can'?t publish this answer|cannot publish this answer", "internal refusal text"),
    ("internal_check", r"(?i)single well-known answer|\(tried \d+x\)|\(asked: '", "VOOL's internal check text"),
    ("placeholder", r"\{\{[^}]*\}\}|\$\{[^}]*\}|<\s*(answer|name|value)\s*>", "unfilled placeholder"),
]

#: Answers that mean the app or the run is broken, not that VOOL answered badly. Used by the early
#: stop gate so a broken setup is caught in minutes.
BROKEN_ANSWER = re.compile(
    r"(?i)^\s*$|\bnot ready\b|isn'?t ready|no model (is )?(available|selected|connected)|no ai model is set up|"
    r"can'?t answer yet|needs an ai model|couldn'?t reach|"
    r"something went wrong|internal server error|\(stopped showing this answer\)"
)

SEND_LIKE = re.compile(r"(?i)\b(send|post|publish|pay|transfer|delete|purchase|buy|reply to)\b")

STEP_KEYS = {
    "chat", "project", "say", "action", "approve", "allow_send", "stop_after", "restart_after",
    "expect", "grade", "plain_allow", "note",
}
EXPECT_KEYS = {"all", "any", "none", "helpful_any", "clarify_ok", "max_seconds", "review", "ui"}
UI_KEYS = {"selector", "text_any", "text_none", "count_min", "count_max", "note"}
SCENARIO_KEYS = {"id", "title", "from_bug", "profile", "needs", "vars", "steps", "note", "min_phrasings"}
ACTIONS = {"reload", "open_chat", "archive_chat", "restart_daemon"}
CAPABILITIES = {"email_account", "calendar_selected", "online", "update_feed", "skills_installed", "browser"}


class LibraryError(ValueError):
    pass


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def load_library(directory: Path = LIBRARY_DIR) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Load and validate every lane file. Returns (scenarios, {file: sha256})."""
    import yaml

    scenarios: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    seen: set[str] = set()
    files = sorted(directory.glob("*.yaml"))
    if not files:
        raise LibraryError(f"no scenario files in {directory}")
    for path in files:
        raw = path.read_bytes()
        hashes[path.name] = hashlib.sha256(raw).hexdigest()
        doc = yaml.safe_load(raw) or {}
        lane = str(doc.get("lane") or "").strip()
        if not lane:
            raise LibraryError(f"{path.name}: missing lane")
        for sc in doc.get("scenarios") or []:
            sc = dict(sc)
            sid = str(sc.get("id") or "")
            where = f"{path.name}:{sid or '?'}"
            if not sid or sid in seen:
                raise LibraryError(f"{where}: missing or duplicate id")
            seen.add(sid)
            unknown = set(sc) - SCENARIO_KEYS
            if unknown:
                raise LibraryError(f"{where}: unknown keys {sorted(unknown)}")
            if sc.get("profile", "throwaway") not in {"throwaway", "any"}:
                raise LibraryError(f"{where}: profile must be throwaway or any")
            for need in _as_list(sc.get("needs")):
                if need not in CAPABILITIES:
                    raise LibraryError(f"{where}: unknown need {need!r}")
            steps = sc.get("steps") or []
            if not steps:
                raise LibraryError(f"{where}: no steps")
            graded = 0
            most_phrasings = 1
            for i, step in enumerate(steps):
                unknown = set(step) - STEP_KEYS
                if unknown:
                    raise LibraryError(f"{where} step {i}: unknown keys {sorted(unknown)}")
                if not step.get("say") and not step.get("action"):
                    raise LibraryError(f"{where} step {i}: needs say or action")
                if step.get("action") and step["action"] not in ACTIONS:
                    raise LibraryError(f"{where} step {i}: unknown action {step['action']!r}")
                most_phrasings = max(most_phrasings, len(_as_list(step.get("say"))))
                expect = step.get("expect") or {}
                unknown = set(expect) - EXPECT_KEYS
                if unknown:
                    raise LibraryError(f"{where} step {i}: unknown expect keys {sorted(unknown)}")
                for check in expect.get("ui") or []:
                    if set(check) - UI_KEYS or "selector" not in check:
                        raise LibraryError(f"{where} step {i}: bad ui check {check}")
                for key in ("all", "any", "none", "helpful_any"):
                    for pattern in _as_list(expect.get(key)):
                        try:
                            re.compile(_render_pattern(pattern, _sample_vars(sc)))
                        except re.error as exc:
                            raise LibraryError(f"{where} step {i}: bad regex {pattern!r}: {exc}") from exc
                for allow in _as_list(step.get("plain_allow")):
                    if allow not in {b[0] for b in PLAIN_BANNED}:
                        raise LibraryError(f"{where} step {i}: unknown plain_allow {allow!r}")
                if step.get("grade", True):
                    if step.get("say") and not (expect.get("all") or expect.get("any") or expect.get("none") or expect.get("ui")):
                        raise LibraryError(f"{where} step {i}: a graded turn needs a right check (all/any/none/ui)")
                    graded += 1
            if graded == 0:
                raise LibraryError(f"{where}: no graded step")
            need_phrasings = int(sc.get("min_phrasings", 3))
            if most_phrasings < need_phrasings:
                raise LibraryError(f"{where}: needs at least {need_phrasings} phrasings on its key step, has {most_phrasings}")
            sc["lane"] = lane
            sc["file"] = path.name
            scenarios.append(sc)
    return scenarios, hashes


# ----------------------------------------------------------------------------------- variables


def _make_value(spec: str, rng: random.Random) -> str:
    """`#` digit, `@` upper-case letter, `pick:a|b|c` one of the choices, `date:+N` a date N days out."""
    if spec.startswith("pick:"):
        return rng.choice(spec[5:].split("|"))
    if spec.startswith("date:"):
        days = int(spec[5:])
        day = dt.date.today() + dt.timedelta(days=days)
        return f"{day.day} {day.strftime('%B')}"
    out = []
    for ch in spec:
        if ch == "#":
            out.append(rng.choice(string.digits))
        elif ch == "@":
            out.append(rng.choice(string.ascii_uppercase))
        else:
            out.append(ch)
    return "".join(out)


def _sample_vars(sc: dict[str, Any]) -> dict[str, str]:
    rng = random.Random(0)
    return {k: _make_value(str(v), rng) for k, v in (sc.get("vars") or {}).items()}


_VAR = re.compile(r"\$\{([a-z_][a-z0-9_]*)\}")


def _render_text(text: str, values: dict[str, str]) -> str:
    return _VAR.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def _render_pattern(pattern: str, values: dict[str, str]) -> str:
    """Variables inside a regex are matched literally, and a code's `-` also matches a space or nothing."""
    def sub(m: re.Match[str]) -> str:
        value = values.get(m.group(1))
        if value is None:
            return m.group(0)
        return re.escape(value).replace(r"\-", r"[-\s]?")
    return _VAR.sub(sub, pattern)


@dataclass
class Case:
    case_id: str
    scenario: dict[str, Any]
    phrasing: int
    values: dict[str, str]


def expand(scenarios: list[dict[str, Any]], seed: int) -> list[Case]:
    """One case per phrasing of the scenario's key step; fresh variable values for every case."""
    rng = random.Random(seed)
    cases: list[Case] = []
    for sc in scenarios:
        count = max(len(_as_list(step.get("say"))) for step in sc["steps"])
        for i in range(count):
            values = {k: _make_value(str(v), rng) for k, v in (sc.get("vars") or {}).items()}
            cases.append(Case(f"{sc['id']}.p{i + 1}", sc, i, values))
    return cases


def _phrasing(step: dict[str, Any], index: int) -> str:
    options = _as_list(step.get("say"))
    return options[index % len(options)] if options else ""


@dataclass
class Verdict:
    right: str = "n/a"
    helpful: str = "n/a"
    plain: str = "n/a"
    reasons: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        axes = [self.right, self.helpful, self.plain]
        if "ERROR" in axes:
            return "ERROR"
        if "FAIL" in axes:
            return "FAIL"
        if "REVIEW" in axes:
            return "REVIEW"
        return "PASS"


def grade_turn(step: dict[str, Any], answer: str, elapsed: float, values: dict[str, str],
               ui_results: list[dict[str, Any]] | None = None, timed_out: bool = False) -> Verdict:
    """Grade one answer on the three axes. Pure: depends only on its arguments."""
    expect = step.get("expect") or {}
    v = Verdict()
    text = answer or ""

    if timed_out:
        v.reasons.append("no finished answer before the turn timeout")

    # Right
    right_ok = True
    for pattern in _as_list(expect.get("all")):
        if not re.search(_render_pattern(pattern, values), text):
            right_ok = False
            v.reasons.append(f"missing: {pattern}")
    any_list = _as_list(expect.get("any"))
    if any_list and not any(re.search(_render_pattern(p, values), text) for p in any_list):
        right_ok = False
        v.reasons.append(f"none of: {any_list}")
    for pattern in _as_list(expect.get("none")):
        if re.search(_render_pattern(pattern, values), text):
            right_ok = False
            v.reasons.append(f"must not say: {pattern}")
    for check in ui_results or []:
        if not check.get("ok"):
            right_ok = False
            v.reasons.append(f"screen: {check.get('why')}")
    if timed_out:
        right_ok = False
    v.right = "PASS" if right_ok else "FAIL"

    # Plain
    allowed = set(_as_list(step.get("plain_allow")))
    plain_ok = True
    for ban_id, pattern, why in PLAIN_BANNED:
        if ban_id in allowed:
            continue
        hit = re.search(pattern, text)
        if hit:
            plain_ok = False
            v.reasons.append(f"not plain ({why}): {hit.group(0)[:60]!r}")
    v.plain = "PASS" if plain_ok else "FAIL"

    # Helpful
    helpful_ok = True
    if not text.strip() and not expect.get("ui"):
        helpful_ok = False
        v.reasons.append("empty answer")
    limit = float(expect.get("max_seconds") or DEFAULT_MAX_SECONDS)
    if elapsed > limit:
        helpful_ok = False
        v.reasons.append(f"too slow: {elapsed:.0f}s > {limit:.0f}s")
    helpful_list = _as_list(expect.get("helpful_any"))
    if helpful_list and not any(re.search(_render_pattern(p, values), text) for p in helpful_list):
        helpful_ok = False
        v.reasons.append(f"not helpful, expected one of: {helpful_list}")
    if not expect.get("clarify_ok") and not right_ok and text.strip().endswith("?") and len(text) < 400:
        helpful_ok = False
        v.reasons.append("asked back instead of answering")
    if helpful_ok and expect.get("review"):
        v.helpful = "REVIEW"
        v.reasons.append(f"reviewer checks: {expect['review']}")
    else:
        v.helpful = "PASS" if helpful_ok else "FAIL"
    return v


# ------------------------------------------------------------------------------------ the app


def _http_json(url: str, body: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Daemon:
    """A throwaway VOOL started by the runner: its own home, its own port, no cloud keys."""

    def __init__(self, source: Path, python: str, home: Path, port: int, certify: str = "") -> None:
        self.source, self.python, self.home, self.port, self.certify = source, python, home, port, certify
        self.process: subprocess.Popen[bytes] | None = None
        self.log_path = home / "daemon.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items()
               if not re.search(r"(API_KEY|_TOKEN|_SECRET)$", k) and not k.startswith("VOOL_")}
        env.update({
            "HOME": str(self.home),
            "VOOL_HOME": str(self.home / "vool"),
            "VOOL_WORKSPACE_ROOT": str(self.home / "workspace"),
            "TMPDIR": str(self.home / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(self.source),
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": hashlib.sha256(str(self.home).encode()).hexdigest()[:24],
            "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
            "VOOL_DISABLE_MESH_DAEMON": "1",
        })
        return env

    def start(self) -> None:
        for sub in ("", "workspace", "tmp"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        self.process = subprocess.Popen(
            [self.python, "-m", "apps.vool_api_server", "--bind", "127.0.0.1", "--port", str(self.port)],
            cwd=str(self.source), env=self.env(), stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        deadline = time.time() + 240
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"throwaway VOOL exited with {self.process.returncode}; see {self.log_path}")
            try:
                if _http_json(self.base_url + "/healthz", timeout=5).get("ok"):
                    return
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError(f"throwaway VOOL did not answer /healthz in 240s; see {self.log_path}")

    def certify_model(self) -> dict[str, Any]:
        if not self.certify:
            return {}
        provider, _, model = self.certify.partition(":")
        registered: dict[str, Any] = {}
        if provider == "ollama-local":
            # A fresh profile has no local model on record; this is the call Settings makes.
            registered = _http_json(self.base_url + "/api/models/local/register", {"model_name": model}, timeout=120)
        return {"registered": registered, "certification": _http_json(self.base_url + "/api/model-tool-certification/run",
                          {"provider_name": provider, "model_name": model, "timeout_seconds": 600}, timeout=700)}

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=30)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
        self.process = None


class ChatPage:
    """The real chat page in headless Chromium. Every graded turn goes through the composer."""

    def __init__(self, base_url: str, shots: Path, headless: bool = True) -> None:
        from playwright.sync_api import sync_playwright

        self.base_url = base_url.rstrip("/")
        self.shots = shots
        self._pw = sync_playwright().start()
        executable = os.environ.get("VOOL_ACCEPTANCE_CHROMIUM") or None
        self.browser = self._pw.chromium.launch(headless=headless, executable_path=executable)
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.page_errors: list[str] = []
        self.page.on("pageerror", lambda e: self.page_errors.append(str(e)[:300]))
        self.open()

    def open(self) -> None:
        self.page.goto(self.base_url + "/chat", wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_selector("#input", timeout=60000)
        time.sleep(1.0)

    def close(self) -> None:
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def current_chat(self) -> str:
        return str(self.page.evaluate("localStorage.getItem('vool.sessionId') || ''"))

    def new_chat(self) -> str:
        self.page.click("#newChat")
        time.sleep(0.5)
        return self.current_chat()

    def new_chat_in_project(self, name: str, root: Path) -> str:
        root.mkdir(parents=True, exist_ok=True)
        # Setup only: the folder picker is a native dialog, so the project is made through the
        # same /api/projects call the page makes after the picker returns.
        _http_json(self.base_url + "/api/projects", {"name": name, "root": str(root)})
        self.open()
        head = self.page.locator(".proj-head", has=self.page.locator(".proj-name", has_text=name)).first
        head.hover()
        button = head.locator("button[aria-label='New chat in this project'], button[title='New chat in this project']").first
        button.click(force=True)
        time.sleep(0.7)
        return self.current_chat()

    def open_chat(self, chat_id: str) -> None:
        row = self.page.locator(f"#sessions .session[data-id='{chat_id}'] .s-title").first
        if row.count():
            row.click()
        else:
            # The row can sit inside a collapsed project group; this is the function the row calls.
            self.page.evaluate("id => openSession(id)", chat_id)
        time.sleep(1.0)

    def delete_chat(self, chat_id: str) -> bool:
        """Remove a chat the runner made, with the page's own call (the row's Delete, minus the dialog).
        Deleting a chat also removes what VOOL learned in it, so one case cannot leak into the next."""
        return bool(self.page.evaluate(
            "async id => { const r = await postSession(id, { delete: true }); return !!(r && r.ok); }", chat_id))

    def archive_chat(self, chat_id: str) -> None:
        self.page.evaluate("id => archiveSession(id, true)", chat_id)
        time.sleep(0.5)

    def reload(self, chat_id: str) -> None:
        self.open()
        if chat_id and self.current_chat() != chat_id:
            self.open_chat(chat_id)

    def _assistant_count(self) -> int:
        return int(self.page.locator("#log .msg.assistant").count())

    def _last_state(self) -> dict[str, Any]:
        return self.page.evaluate("""() => {
          const all = document.querySelectorAll('#log .msg.assistant');
          const el = all[all.length - 1];
          if (!el) return {text: '', done: false, full: ''};
          const t = el.querySelector('.msg-text');
          return {
            text: t ? t.innerText : '',
            full: el.innerText,
            done: !el.classList.contains('pending') && !(t && t.dataset.streaming) && !!el.querySelector('.msg-time'),
          };
        }""")

    def say(self, text: str, *, approve: str, allow_send: bool, stop_after: float | None,
            restart_after: float | None, restart: Any, timeout: float) -> dict[str, Any]:
        before = self._assistant_count()
        users_before = int(self.page.locator("#log .msg.user").count())
        self.page.fill("#input", text)
        start = time.time()
        self.page.click("#send")
        try:
            self.page.wait_for_function(
                "n => document.querySelectorAll('#log .msg.user').length > n", arg=users_before, timeout=20000)
        except Exception as exc:
            raise RuntimeError("the message did not show in the chat within 20s of pressing Send") from exc
        approvals: list[dict[str, str]] = []
        stopped = restarted = False
        state: dict[str, Any] = {"text": "", "full": "", "done": False}
        while time.time() - start < timeout:
            time.sleep(0.5)
            if self.page.locator("#permBar:not([hidden])").count():
                what = self.page.locator("#permBar").inner_text()[:500]
                is_send = bool(SEND_LIKE.search(what))
                decision = "deny"
                if approve == "once" and (allow_send or not is_send):
                    decision = "once"
                self.page.click("#permOnce" if decision == "once" else "#permDeny")
                approvals.append({"card": what, "decision": decision})
                time.sleep(0.7)
                continue
            elapsed = time.time() - start
            if stop_after is not None and not stopped and elapsed >= stop_after:
                stop = self.page.locator("#log .task-card .tc-stop:visible").last
                if stop.count():
                    stop.click()
                stopped = True
            if restart_after is not None and not restarted and elapsed >= restart_after:
                restarted = True
                chat_id = self.current_chat()
                restart()
                self.reload(chat_id)
                state = self._last_state()
                break
            if self._assistant_count() > before:
                state = self._last_state()
                if state["done"]:
                    time.sleep(1.0)
                    state = self._last_state()
                    break
        elapsed = time.time() - start
        timed_out = not state.get("done") and not restarted and not stopped
        return {"answer": state.get("text", ""), "bubble": state.get("full", ""), "elapsed": round(elapsed, 1),
                "approvals": approvals, "stopped": stopped, "restarted": restarted, "timed_out": timed_out}

    def ui_checks(self, checks: list[dict[str, Any]], values: dict[str, str]) -> list[dict[str, Any]]:
        results = []
        for check in checks:
            selector = _render_text(str(check["selector"]), values)
            texts = self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText || e.textContent || '')")
            joined = "\n".join(texts)
            ok, why = True, ""
            if "count_min" in check and len(texts) < int(check["count_min"]):
                ok, why = False, f"{selector}: {len(texts)} found, want at least {check['count_min']}"
            if "count_max" in check and len(texts) > int(check["count_max"]):
                ok, why = False, f"{selector}: {len(texts)} found, want at most {check['count_max']}"
            any_list = [_render_pattern(p, values) for p in _as_list(check.get("text_any"))]
            if ok and any_list and not any(re.search(p, joined) for p in any_list):
                ok, why = False, f"{selector}: shows {joined[:120]!r}, want one of {check.get('text_any')}"
            for p in _as_list(check.get("text_none")):
                if ok and re.search(_render_pattern(p, values), joined):
                    ok, why = False, f"{selector}: shows {p!r}"
            results.append({"selector": selector, "ok": ok, "why": why, "seen": joined[:300]})
        return results

    def screenshot(self, name: str) -> str:
        path = self.shots / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=False)
        return str(path)


# ------------------------------------------------------------------------------------ the run


class EarlyStop(RuntimeError):
    pass


@dataclass
class Gate:
    """Stop a broken run early: first 3 answers all broken, or > 1/3 broken in any 10."""
    window: list[bool] = field(default_factory=list)
    total: int = 0

    def record(self, answer: str, timed_out: bool) -> None:
        broken = timed_out or bool(BROKEN_ANSWER.search(answer or ""))
        self.window.append(broken)
        self.total += 1
        if self.total == 3 and all(self.window[-3:]):
            raise EarlyStop("the first 3 answers were empty, errors or 'not ready': the setup is wrong")
        last = self.window[-10:]
        if len(last) == 10 and sum(last) > 10 / 3:
            raise EarlyStop(f"{sum(last)} of the last 10 answers were empty, errors or 'not ready'")


def run_case(case: Case, page: ChatPage, daemon: Daemon | None, work: Path, real_profile: bool,
             gate: Gate, log: Any) -> list[dict[str, Any]]:
    chats: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    created: list[str] = []
    try:
        for n, step in enumerate(case.scenario["steps"], start=1):
            label = str(step.get("chat") or "A")
            project = step.get("project")
            row: dict[str, Any] = {"case": case.case_id, "scenario": case.scenario["id"], "lane": case.scenario["lane"],
                                   "turn": n, "chat": label, "graded": bool(step.get("grade", True))}
            if label not in chats:
                if project:
                    name = _render_text(str(project), case.values)
                    chats[label] = page.new_chat_in_project(name, work / "projects" / re.sub(r"\W+", "-", name))
                else:
                    chats[label] = page.new_chat()
                created.append(label)
            elif chats[label] != page.current_chat():
                page.open_chat(chats[label])

            action = step.get("action")
            if action == "reload":
                page.reload(chats[label])
            elif action == "open_chat":
                page.open_chat(chats[label])
            elif action == "archive_chat":
                page.archive_chat(chats[label])
            elif action == "restart_daemon":
                if not daemon:
                    raise EarlyStop("restart_daemon needs a runner-launched throwaway VOOL (--launch)")
                daemon.stop()
                daemon.start()
                page.reload(chats[label])

            result: dict[str, Any] = {"answer": "", "elapsed": 0.0, "timed_out": False}
            if step.get("say"):
                text = _render_text(_phrasing(step, case.phrasing), case.values)
                row["said"] = text
                restart = None
                if step.get("restart_after") is not None:
                    if not daemon:
                        raise EarlyStop("restart_after needs a runner-launched throwaway VOOL (--launch)")
                    restart = lambda: (daemon.stop(), daemon.start())  # noqa: E731
                result = page.say(
                    text,
                    approve=str(step.get("approve") or "deny"),
                    allow_send=bool(step.get("allow_send")) and not real_profile,
                    stop_after=step.get("stop_after"),
                    restart_after=step.get("restart_after"),
                    restart=restart,
                    timeout=TURN_TIMEOUT_SECONDS,
                )
                gate_answer = result["answer"] if not (result.get("stopped") or result.get("restarted")) else "stopped"
                gate.record(gate_answer, result["timed_out"])
            expect = step.get("expect") or {}
            ui_results = page.ui_checks(expect.get("ui") or [], case.values) if expect.get("ui") else []
            row.update(result)
            row["ui"] = ui_results
            row["shot"] = page.screenshot(f"{case.case_id}.t{n}")
            if row["graded"]:
                verdict = grade_turn(step, result.get("answer", ""), float(result.get("elapsed") or 0.0),
                                     case.values, ui_results, bool(result.get("timed_out")))
                row.update({"right": verdict.right, "helpful": verdict.helpful, "plain": verdict.plain,
                            "status": verdict.status, "reasons": verdict.reasons})
            else:
                row["status"] = "setup"
            rows.append(row)
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
    finally:
        # Clean up only the chats this case made, on either profile. A failed clean-up is recorded:
        # what it leaves behind can change the next case's answers.
        cleanup = {}
        for label in created:
            try:
                cleanup[label] = page.delete_chat(chats[label])
            except Exception:  # the page may be the thing that broke
                cleanup[label] = False
        if not all(cleanup.values()) and rows:
            rows[-1].setdefault("reasons", []).append(f"clean-up failed for chats {[k for k, ok in cleanup.items() if not ok]}")
        log.write(json.dumps({"case": case.case_id, "cleanup": cleanup}) + "\n")
    return rows


def case_status(rows: list[dict[str, Any]]) -> str:
    statuses = [r["status"] for r in rows if r.get("graded")]
    for s in ("ERROR", "FAIL", "REVIEW", "BLOCKED"):
        if s in statuses:
            return s
    return "PASS" if statuses else "ERROR"


def write_table(out: Path, rows: list[dict[str, Any]], source: dict[str, Any], note: str = "") -> str:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_case.setdefault(r["case"], []).append(r)
    lanes: dict[str, dict[str, int]] = {}
    for case_rows in by_case.values():
        lane = case_rows[0]["lane"]
        status = case_status(case_rows)
        counts = lanes.setdefault(lane, {"PASS": 0, "FAIL": 0, "REVIEW": 0, "BLOCKED": 0, "ERROR": 0})
        counts[status] = counts.get(status, 0) + 1
    lines = [f"# Acceptance run {source.get('started', '')}", "",
             f"App: {source.get('base_url', '')}, build {source.get('build', 'unknown')}, profile {source.get('profile', '')}.",
             f"Runner commit {source.get('runner_commit', 'unknown')}, seed {source.get('seed')}.", ""]
    if note:
        lines += [f"**Run stopped early: {note}**", ""]
    total = {k: sum(c[k] for c in lanes.values()) for k in ("PASS", "FAIL", "REVIEW", "BLOCKED", "ERROR")}
    cases = sum(total.values())
    lines += [f"**{total['PASS']}/{cases} cases pass** (FAIL {total['FAIL']}, REVIEW {total['REVIEW']}, "
              f"BLOCKED {total['BLOCKED']}, ERROR {total['ERROR']}).", "",
              "| Lane | Pass | Fail | Review | Blocked | Error | Cases |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for lane, c in sorted(lanes.items()):
        lines.append(f"| {lane} | {c['PASS']} | {c['FAIL']} | {c['REVIEW']} | {c['BLOCKED']} | {c['ERROR']} | {sum(c.values())} |")
    lines += ["", "| Case | Turn | Status | Right | Helpful | Plain | Time | Said | Answer (start) | Why |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]

    def cell(s: Any, limit: int = 90) -> str:
        return str(s or "").replace("|", "\\|").replace("\n", " ")[:limit]

    for case_id, case_rows in by_case.items():
        for r in case_rows:
            if not r.get("graded"):
                continue
            lines.append("| " + " | ".join([
                case_id, str(r["turn"]), r["status"], r.get("right", ""), r.get("helpful", ""), r.get("plain", ""),
                f"{r.get('elapsed', 0)}s", cell(r.get("said"), 70), cell(r.get("answer"), 110),
                cell("; ".join(r.get("reasons") or []), 160),
            ]) + " |")
    text = "\n".join(lines) + "\n"
    (out / "table.md").write_text(text, encoding="utf-8")
    return text


def apply_review(out: Path) -> str:
    rows = [json.loads(line) for line in (out / "raw.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if "status" in r]
    review_path = out / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    for r in rows:
        key = f"{r['case']}.t{r['turn']}"
        decided = review.get(key)
        if r.get("status") == "REVIEW" and decided:
            verdict = str(decided.get("verdict", "")).lower()
            if verdict not in {"pass", "fail"} or not str(decided.get("reason", "")).strip():
                raise SystemExit(f"review.json {key}: needs verdict pass|fail and a reason")
            r["helpful"] = verdict.upper()
            r["status"] = "PASS" if verdict == "pass" and r.get("right") == "PASS" and r.get("plain") == "PASS" else "FAIL"
            r.setdefault("reasons", []).append(f"reviewed: {decided['reason']}")
    source = json.loads((out / "source.json").read_text(encoding="utf-8"))
    return write_table(out, rows, source, source.get("stopped_early", ""))


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="")
    ap.add_argument("--launch", action="store_true", help="start a throwaway VOOL from --source")
    ap.add_argument("--source", default=str(REPO_ROOT), help="app code to launch (with --launch)")
    ap.add_argument("--python", default=sys.executable, help="python that runs the app (with --launch)")
    ap.add_argument("--certify", default="", help="PROVIDER:MODEL to set up in the throwaway profile, e.g. ollama-local:qwen3:8b")
    ap.add_argument("--real-profile", action="store_true", help="the app runs on the person's own profile")
    ap.add_argument("--have", default="", help=f"comma list the profile has: {sorted(CAPABILITIES)}")
    ap.add_argument("--lane", action="append", default=[], help="only these lanes (repeatable)")
    ap.add_argument("--only", action="append", default=[], help="only these scenario ids (repeatable)")
    ap.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    ap.add_argument("--out", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--apply-review", default="")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args(argv)

    if args.apply_review:
        print(apply_review(Path(args.apply_review)))
        return 0

    scenarios, hashes = load_library()
    if args.lane:
        scenarios = [s for s in scenarios if s["lane"] in args.lane]
    if args.only:
        scenarios = [s for s in scenarios if s["id"] in args.only]
    if args.real_profile:
        scenarios = [s for s in scenarios if s.get("profile") == "any"]
    cases = expand(scenarios, args.seed)
    if args.list:
        for c in cases:
            print(f"{c.case_id:28} {c.scenario['lane']:14} {c.scenario.get('title', '')}")
        print(f"{len(cases)} cases from {len(scenarios)} scenarios")
        return 0
    if not args.out:
        ap.error("--out is required for a run")
    if args.real_profile and args.launch:
        ap.error("--real-profile and --launch are opposites")

    out = Path(args.out).resolve()
    (out / "shots").mkdir(parents=True, exist_ok=True)
    have = {h.strip() for h in args.have.split(",") if h.strip()}
    daemon: Daemon | None = None
    if args.launch:
        daemon = Daemon(Path(args.source).resolve(), args.python, out / "home", _free_port(), args.certify)
        daemon.start()
        cert = daemon.certify_model()
        (out / "certification.json").write_text(json.dumps(cert, indent=1), encoding="utf-8")
        base_url = daemon.base_url
    else:
        base_url = args.base_url or DEFAULT_BASE_URL

    try:
        health = _http_json(base_url + "/healthz", timeout=10)
    except (OSError, ValueError) as exc:
        print(f"VOOL is not answering at {base_url}: {exc}", file=sys.stderr)
        return 2
    runtime = health.get("runtime") or {}
    source = {
        "started": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_url": base_url,
        "build": runtime.get("build_id") or runtime.get("version") or "unknown",
        "runtime": runtime,
        "profile": "real" if args.real_profile else "throwaway",
        "have": sorted(have),
        "runner_commit": _git_commit(),
        "seed": args.seed,
        "scenario_files": hashes,
        "cases": len(cases),
    }
    (out / "source.json").write_text(json.dumps(source, indent=1), encoding="utf-8")

    rows: list[dict[str, Any]] = []
    note = ""
    page = ChatPage(base_url, out / "shots", headless=not args.headed)
    gate = Gate()
    try:
        with (out / "raw.jsonl").open("a", encoding="utf-8") as log:
            for case in cases:
                missing = sorted(set(_as_list(case.scenario.get("needs"))) - have)
                if missing:
                    row = {"case": case.case_id, "scenario": case.scenario["id"], "lane": case.scenario["lane"],
                           "turn": 0, "graded": True, "status": "BLOCKED", "reasons": [f"profile lacks {missing}"]}
                    rows.append(row)
                    log.write(json.dumps(row) + "\n")
                    continue
                try:
                    rows += run_case(case, page, daemon, out, args.real_profile, gate, log)
                except EarlyStop:
                    raise
                except Exception as exc:  # one broken case is recorded, the run goes on
                    row = {"case": case.case_id, "scenario": case.scenario["id"], "lane": case.scenario["lane"],
                           "turn": 0, "graded": True, "status": "ERROR", "reasons": [f"{type(exc).__name__}: {exc}"[:300]]}
                    rows.append(row)
                    log.write(json.dumps(row) + "\n")
                    page.open()
                print(f"{case.case_id}: {case_status([r for r in rows if r['case'] == case.case_id])}", flush=True)
    except EarlyStop as exc:
        note = str(exc)
        source["stopped_early"] = note
        (out / "source.json").write_text(json.dumps(source, indent=1), encoding="utf-8")
    finally:
        source["page_errors"] = page.page_errors[:50]
        (out / "source.json").write_text(json.dumps(source, indent=1), encoding="utf-8")
        page.close()
        if daemon:
            daemon.stop()
    print(write_table(out, rows, source, note))
    return 1 if note else 0


if __name__ == "__main__":
    sys.exit(main())
