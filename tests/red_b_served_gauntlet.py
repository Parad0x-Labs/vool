#!/usr/bin/env python3
"""RED-B — the A13/A14 served gauntlet (AUD-20260829-003 repair round).

Turns question.md's 12 acceptance criteria into DETERMINISTIC checks over served output.

DESIGN RULES THIS HARNESS OBEYS
-------------------------------
* Never assert on expected prose.  Every check is slot accounting, provenance presence,
  structural token presence, date arithmetic, or a contradiction between two served
  claims.  No check compares the answer to a stored "right answer" string.
* A silently-dropped slot must FAIL.  `--selftest` proves it: the detectors are run
  against synthetic served bodies, including one that drops a slot with no failure row,
  and the harness must report that as a failure.  If --selftest ever goes green on the
  dropping fixture, the harness is broken, not the runtime.
* Multiple entrances.  verdict-correction-001 D4: five `finalize_answer` call sites
  besides the `/api/chat` `_response_commit` path pass no `closure=` and catch neither
  `FinalizationRejected` nor `NoAnswerContent`.  A gauntlet that only drives `/api/chat`
  certifies one door of six.  Entrances are pluggable: `api_chat`, `api_generate`
  (service.py:4348 — a live HTTP door with NO closure handoff), and `frontdoor`
  (in-process `VoolAgent._handle_turn_frontdoor`, the surface the frozen contract tests
  drive).  Select with --entrance.

Live daemon probes carry chat ids prefixed `audit-AUD-20260829-003-RED1-` per the
audit's rules of engagement.

USAGE
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/red_b_served_gauntlet.py --selftest
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/red_b_served_gauntlet.py \
        --entrance api_chat --base-url http://127.0.0.1:11435 --out baseline.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

AUDIT_PREFIX = "audit-AUD-20260829-003-RED1-"

CANONICAL_4SLOT = (
    "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in "
    "Rome, and what is the water temperature in the Baltic Sea?"
)

# E001's evening turn 10, verbatim (double spaces preserved — they are in the operator's
# own paste and this harness must not clean up the input it is judging).
OPERATOR_EVENING = (
    "ok so u think u so cool heh? ok what about 1500 eur to usd? then how much and how "
    "much  god i can buy iwth it? and thne tell me the water tempperature in baltc sea "
    "and in  berling now :D"
)

MAX_FX_STALENESS_DAYS = 7

# ---------------------------------------------------------------------------------------
# Slot model
# ---------------------------------------------------------------------------------------

_TEMP_RE = re.compile(r"(-?\d{1,3}(?:[.,]\d+)?)\s*(?:°\s*)?(?:C\b|F\b|celsius|fahrenheit)", re.I)
_ISO_DATE_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_MASS_UNIT_RE = re.compile(
    r"(\d[\d,\.]*)\s*(troy\s+ounce|ounces?|oz|grams?|g\b|kilograms?|kg)", re.I
)
_NUMBER_RE = re.compile(r"\d[\d,\. ]*\d|\d")
# Provenance: a named source, or an explicit source/observed marker.
_SOURCE_RE = re.compile(
    r"source\s*[:=]|observed\s*[:=]|according to|open-meteo|wttr\.in|frankfurter|"
    r"metals?[-\. ]api|exchangerate|yahoo|coingecko|noaa|copernicus|smhi|dmi|"
    r"live rate|institutional rates|per\s+api",
    re.I,
)


@dataclass
class Slot:
    slot_id: str
    label: str
    #: tokens that identify this slot's SUBJECT anywhere in served text.  Used for loose
    #: "is this slot mentioned at all" reads (follow-ups), NOT for failure-row ownership.
    subject_tokens: tuple[str, ...]
    #: an additional structural requirement for the slot to count as ANSWERED
    answered: Callable[[str], bool]
    #: what "current source" means for this slot
    grounded: Callable[[str], bool]
    singleton_prompt: str
    #: DISTINCTIVE tokens — tokens that belong to this slot and to no other slot in the
    #: prompt.  Failure rows are attributed by these and assigned to exactly ONE slot,
    #: because loose attribution both invents contradictions (a gold row mentioning "EUR"
    #: read as an FX failure) and, far worse, can let a genuinely dropped slot borrow
    #: another slot's row and disappear from the accounting.
    distinctive: tuple[str, ...] = ()


def _has(text: str, *tokens: str) -> bool:
    low = text.lower()
    return any(tok.lower() in low for tok in tokens)


def _word(text: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", text, re.I) is not None


def _fresh_iso_date(text: str, max_days: int = MAX_FX_STALENESS_DAYS) -> bool:
    """A served ISO date no older than max_days.  Date arithmetic, not prose matching."""
    today = _dt.date.today()
    for y, m, d in _ISO_DATE_RE.findall(text):
        try:
            when = _dt.date(int(y), int(m), int(d))
        except ValueError:
            continue
        if 0 <= (today - when).days <= max_days:
            return True
    return False


def _fx_answered(body: str) -> bool:
    # A converted VALUE in the target currency: the RUB token and a number, plus the
    # source currency so a bare "RUB" mention cannot pass.
    return _word(body, "RUB") and _word(body, "EUR") and bool(_NUMBER_RE.search(body))


def _fx_grounded(body: str) -> bool:
    return bool(_SOURCE_RE.search(body)) and _fresh_iso_date(body)


def _gold_answered(body: str) -> bool:
    """An AMOUNT of gold, not merely a price quote.

    question.md criterion 2 asks for how much gold the money buys, with the unit and the
    assumption stated.  A quantity bound to a mass unit is the structural signature of an
    amount; a price with no quantity is not.
    """
    if not _has(body, "gold", "XAU"):
        return False
    return bool(_MASS_UNIT_RE.search(body))


def _gold_grounded(body: str) -> bool:
    unit_or_assumption = _has(
        body, "per ounce", "per oz", "per troy", "per gram", "spot", "assum", "troy",
        "/oz", "/ounce", "/g", "at a price of",
    )
    return unit_or_assumption and bool(_SOURCE_RE.search(body))


def _rome_answered(body: str) -> bool:
    if not _has(body, "Rome", "Roma"):
        return False
    # The temperature must be on the SAME line as Rome, or the Rome mention is only the
    # restated question while some other town's reading stands in for it (the S1 defect).
    return any(_has(line, "Rome", "Roma") and _TEMP_RE.search(line) for line in body.splitlines())


def _rome_grounded(body: str) -> bool:
    for line in body.splitlines():
        if _has(line, "Rome", "Roma") and _TEMP_RE.search(line):
            return bool(_SOURCE_RE.search(line)) or bool(_SOURCE_RE.search(body))
    return False


def _water_answered(body: str) -> bool:
    """Water temperature for the Baltic, on the same line, with a water marker.

    Criterion 4 forbids an AIR reading for a random town standing in for sea-surface
    temperature.  Requiring the water marker and the Baltic token on the same line as the
    number is the structural form of that.
    """
    for line in body.splitlines():
        if not _has(line, "Baltic", "baltc"):
            continue
        if not _TEMP_RE.search(line):
            continue
        if _has(line, "water", "sea surface", "sea-surface", "sst"):
            return True
    return False


def _water_grounded(body: str) -> bool:
    """A location/source caveat, not one universal number."""
    for line in body.splitlines():
        if _has(line, "Baltic", "baltc") and _TEMP_RE.search(line):
            caveat = _has(
                line, "varies", "depend", "approx", "around", "near", "at ", "station",
                "coast", "measured", "buoy", "average",
            ) or _has(body, "varies by location", "depends on location", "measurement point")
            return caveat and bool(_SOURCE_RE.search(body))
    return False


CANONICAL_SLOTS: tuple[Slot, ...] = (
    Slot(
        "fx", "EUR->RUB conversion",
        ("EUR", "RUB", "rouble", "ruble", "euro"),
        _fx_answered, _fx_grounded,
        "What is 1000 EUR to RUB?",
        distinctive=("RUB", "rouble", "ruble", "roubles", "rubles"),
    ),
    Slot(
        "gold", "gold purchasable amount",
        ("gold", "XAU"),
        _gold_answered, _gold_grounded,
        "How much gold can I buy with 1000 EUR?",
        distinctive=("gold", "XAU", "god i can buy"),
    ),
    Slot(
        "weather", "Rome weather",
        ("Rome", "Roma", "weather"),
        _rome_answered, _rome_grounded,
        "What is the weather in Rome?",
        distinctive=("Rome", "Roma"),
    ),
    Slot(
        "water", "Baltic Sea water temperature",
        ("Baltic", "water temperature", "sea"),
        _water_answered, _water_grounded,
        "What is the water temperature in the Baltic Sea?",
        distinctive=("Baltic", "baltc", "water temperature", "water tempperature",
                     "sea temperature"),
    ),
)

def _fx_usd_answered(body: str) -> bool:
    return _word(body, "USD") and _word(body, "EUR") and bool(_NUMBER_RE.search(body))


def _berlin_answered(body: str) -> bool:
    return any(_has(line, "Berlin", "berling") and _TEMP_RE.search(line) for line in body.splitlines())


def _berlin_grounded(body: str) -> bool:
    return bool(_SOURCE_RE.search(body))


#: The operator's evening variant asks for DIFFERENT slots than the canonical prompt
#: (EUR->USD not EUR->RUB; Berlin not Rome).  Judging it with the canonical slot set
#: reported four silent drops where the FX leg had in fact been answered -- a wrong
#: number, and one that flattered the red team.  Slot sets are per-prompt.
OPERATOR_EVENING_SLOTS: tuple[Slot, ...] = (
    Slot("fx_usd", "1500 EUR -> USD", ("USD", "dollar", "usd"),
         _fx_usd_answered, _fx_grounded, "what about 1500 eur to usd?",
         distinctive=("USD", "dollar", "dollars")),
    Slot("gold", "gold purchasable amount", ("gold", "god", "XAU"),
         _gold_answered, _gold_grounded, "how much god i can buy iwth it?",
         distinctive=("gold", "god i can buy", "XAU")),
    Slot("water", "Baltic water temperature", ("baltc", "Baltic", "water temp"),
         _water_answered, _water_grounded,
         "tell me the water tempperature in baltc sea",
         distinctive=("baltc", "Baltic")),
    Slot("berlin", "Berlin reading", ("berling", "Berlin"),
         _berlin_answered, _berlin_grounded, "water tempperature in berling now",
         distinctive=("berling", "Berlin")),
)

PROMPTS: dict[str, tuple[str, tuple[Slot, ...]]] = {}  # filled after CANONICAL_SLOTS


PROMPTS.update({
    "canonical": (CANONICAL_4SLOT, CANONICAL_SLOTS),
    "operator-evening": (OPERATOR_EVENING, OPERATOR_EVENING_SLOTS),
})

# MEASURED 2026-09-05: the runtime's own disclosure header is ACTIVE voice --
# "I could not answer these parts of your message:" (core/agent_runtime/turn_planner.py:460).
# Every alternative here was passive or nominal, so it matched none of them, and a turn that
# explicitly disclosed three dropped slots was scored as three SILENT DROPS. An instrument that
# cannot see a correct disclosure manufactures the defect it was built to detect.
_FAILURE_HEADER_RE = re.compile(
    r"(could not be answered|could not answer|unable to answer|unavailable|not answered|"
    r"couldn'?t be answered|couldn'?t answer|no answer for)\b[^:\n]*:?", re.I
)
_ROW_RE = re.compile(r"^\s*[-*•]\s+(.*)$")

#: Marker for "this entrance did not claim the turn" -- distinct from "it served nothing".
_DECLINED = "entrance declined to claim this turn"

# A reason that is a raw internal failure is itself a criterion violation (verdict R3).
_RAW_INTERNAL_RE = re.compile(
    r"Traceback|ImportError|cannot import name|AttributeError|KeyError|TypeError|"
    r"/Users/|/private/|\.py\b|Exception\b|line \d+, in ", re.I
)


@dataclass
class Served:
    """One served turn, parsed into the pieces the criteria are about."""

    entrance: str
    prompt: str
    chat_id: str
    text: str
    commit: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    latency_s: float = 0.0
    transport_error: str = ""
    #: the slot set THIS prompt asks for.  A fixed four-slot model applied to the
    #: operator's evening variant reported four silent drops where the runtime had in
    #: fact answered the FX leg (in USD, not RUB) -- a wrong number in the red team's
    #: own favour.  Slots are per-prompt for that reason.
    slots: tuple[Slot, ...] = field(default_factory=lambda: CANONICAL_SLOTS)

    # -- parsing ------------------------------------------------------------------
    @property
    def failure_section(self) -> str:
        m = _FAILURE_HEADER_RE.search(self.text)
        return self.text[m.end():] if m else ""

    @property
    def body(self) -> str:
        """Everything before the failure section: the part that CLAIMS to be answers."""
        m = _FAILURE_HEADER_RE.search(self.text)
        return self.text[: m.start()] if m else self.text

    @property
    def failure_rows(self) -> list[str]:
        rows: list[str] = []
        for line in self.failure_section.splitlines():
            m = _ROW_RE.match(line)
            if m and m.group(1).strip():
                rows.append(m.group(1).strip())
            elif rows and line.strip() and not _ROW_RE.match(line):
                rows[-1] += " " + line.strip()
        if not rows and self.failure_section.strip():
            # An unbulleted failure section still names things; keep it as one row so a
            # runtime cannot dodge accounting by dropping the bullet character.
            rows = [self.failure_section.strip()]
        return rows

    def _row_owner(self, row: str) -> str:
        """The ONE slot a failure row belongs to, by distinctive-token count.

        Exactly-one ownership is deliberate.  If a row could count for two slots, a
        runtime that drops one slot entirely would still show it as "named unavailable"
        by borrowing a neighbour's row, and the silent-drop detector would go blind.
        """
        scores = {
            slot.slot_id: sum(1 for tok in slot.distinctive if tok.lower() in row.lower())
            for slot in self.slots
        }
        best = max(scores.values()) if scores else 0
        if best == 0:
            return ""
        winners = [sid for sid, n in scores.items() if n == best]
        return winners[0] if len(winners) == 1 else ""

    def named_failed(self, slot: Slot) -> str:
        """The failure row that OWNS this slot, or ''."""
        for row in self.failure_rows:
            if self._row_owner(row) == slot.slot_id:
                return row
        return ""

    @property
    def unattributed_failure_rows(self) -> list[str]:
        """Failure rows naming no requested slot distinctly.

        S1 served exactly this — 'what is in rome now and in paris?' listed as a failed
        ITEM alongside its own sub-clauses.  A row that maps to no slot is accounting
        noise, and is reported rather than quietly discarded.
        """
        return [row for row in self.failure_rows if not self._row_owner(row)]

    def slot_state(self, slot: Slot) -> str:
        answered = slot.answered(self.body)
        named = bool(self.named_failed(slot))
        if answered and named:
            return "CONTRADICTED"  # criterion 8
        if answered:
            return "ANSWERED"
        if named:
            return "NAMED_FAILED"
        return "SILENTLY_DROPPED"  # criteria 5 and 7

    # -- receipts -----------------------------------------------------------------
    @property
    def closure(self) -> dict[str, Any]:
        c = self.commit.get("closure_verdict")
        return c if isinstance(c, dict) else {}

    @property
    def provenance(self) -> dict[str, Any]:
        dm = self.commit.get("display_metadata")
        if isinstance(dm, dict) and isinstance(dm.get("provenance"), dict):
            return dm["provenance"]
        return {}


# ---------------------------------------------------------------------------------------
# Entrances
# ---------------------------------------------------------------------------------------


def _post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class ApiChat:
    """The `/api/chat` completions surface — the `_response_commit` path (site 5 of 6)."""

    name = "api_chat"

    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def send(self, prompt: str, chat_id: str) -> Served:
        assert chat_id.startswith(AUDIT_PREFIX), f"probe chat_id must carry {AUDIT_PREFIX}"
        t0 = time.time()
        try:
            raw = _post(
                f"{self.base_url}/api/chat",
                {
                    "model": "vool",
                    "chat_id": chat_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
                self.timeout,
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Served(self.name, prompt, chat_id, "", {}, {}, time.time() - t0,
                          f"{type(exc).__name__}: {exc}")
        msg = raw.get("message") if isinstance(raw.get("message"), dict) else {}
        commit = raw.get("vool_response_commit")
        return Served(
            self.name, prompt, chat_id,
            str((msg or {}).get("content") or ""),
            commit if isinstance(commit, dict) else {},
            raw, time.time() - t0,
        )


class ApiGenerate:
    """`/api/generate` (service.py:4348) — a LIVE HTTP door whose `finalize_answer` call
    passes no `closure=` and catches no `FinalizationRejected`.  D4's exact hazard."""

    name = "api_generate"

    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def send(self, prompt: str, chat_id: str) -> Served:
        assert chat_id.startswith(AUDIT_PREFIX), f"probe id must carry {AUDIT_PREFIX}"
        t0 = time.time()
        try:
            raw = _post(
                f"{self.base_url}/api/generate",
                {"model": "vool", "prompt": prompt, "stream": False, "turn_id": chat_id[:80]},
                self.timeout,
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return Served(self.name, prompt, chat_id, "", {}, {}, time.time() - t0,
                          f"{type(exc).__name__}: {exc}")
        commit = raw.get("vool_response_commit")
        return Served(
            self.name, prompt, chat_id,
            str(raw.get("response") or ""),
            commit if isinstance(commit, dict) else {},
            raw, time.time() - t0,
        )


class InProcessFrontDoor:
    """`VoolAgent._handle_turn_frontdoor` in the measured tree — the surface the frozen
    contract tests drive, and the one where a closed-contract reroute shows up first."""

    name = "frontdoor"

    def __init__(self, tree: str) -> None:
        tree = os.path.realpath(tree)
        sys.path.insert(0, tree)
        import core

        assert os.path.realpath(core.__file__).startswith(tree + os.sep), core.__file__
        self.core_file = core.__file__
        # The daemon runs migrations at boot; an in-process entrance on a cold VOOL_HOME has
        # no schema at all. Measured 2026-09-05: without this the A0 ingress bind dies on
        # "no such table: invocation_requests", and -- more quietly -- the runtime's own
        # `_arm_demand_set_for_entrance` swallows "no such table: obligation_sets" and returns
        # None, so entrance certification silently never runs on a first-ever turn.
        from storage.migrations import run_migrations

        run_migrations()

        from apps.vool_agent import VoolAgent

        self.agent = VoolAgent(
            backend_name="red1-audit", device="openclaw-test", persona_id="default"
        )

    def send(self, prompt: str, chat_id: str) -> Served:
        import tempfile

        t0 = time.time()
        with tempfile.TemporaryDirectory() as workspace:
            ctx = {
                "workspace": workspace,
                "workspace_root": workspace,
                "session_id": chat_id,
                "operating_mode": "auto",
                "surface": "api",
                # THE CRITERIA UNDER TEST ARE ABOUT CURRENT SOURCES. C1-C4 ask whether the
                # answer uses live FX, a live gold price, a current weather source and a real
                # marine reading, bound to fetched receipts and timestamps. With retrieval
                # forbidden this entrance measured a runtime DENIED the capability the
                # criterion is about, and scored it FAIL for declining honestly -- while the
                # other entrance ran with retrieval allowed and fetched from Yahoo Finance and
                # open-meteo in every capture in this lane. Two entrances graded on opposite
                # permissions are not comparable, and the disagreement was read as a product
                # gap for four criteria.
                "allow_remote_fetch": True,
                "workspace_binding": "default",
                "project_id": "",
            }
            # A0 INGRESS BINDING, exactly as the transport door does it
            # (core/web/api/service.py:2340). `run_once` refuses to mint an execution identity
            # without one -- "execution identity refused: no A0 request bound at ingress" --
            # because binding the caller's request is the ingress's job, not the runtime's. A
            # harness that skips it is not embedding the runtime, it is bypassing its front
            # door.
            from core.invocation.ledger import accept_invocation
            from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

            accepted = accept_invocation(
                external_kind="http",
                external_value=f"redb-{chat_id}-{int(t0 * 1000000)}",
                principal="owner_local",
                session_binding="",
                privacy_local_only=True,
            )
            ctx[TURN_REQUEST_KEY] = TurnRequest.from_ingress(
                user_text=prompt,
                source_context=ctx,
                request_id=str(accepted["request_id"]),
                turn_id=f"turn-{int(t0 * 1000000)}",
                session_id=chat_id,
            )
            try:
                # THE IN-PROCESS ENTRANCE IS `run_once`, not the frontdoor gate.
                #
                # This called `_handle_turn_frontdoor` directly. Measured 2026-09-05: that is
                # the LAST of five gates inside `run_once`, and the four lanes that answer a
                # mixed live-data turn all sit ABOVE it -- conductor, demand-owned, live-data,
                # planned. For every prompt in this gauntlet the frontdoor correctly returns
                # {"result": None}, its "no fast path claimed this turn, caller continues"
                # signal, and the old `or {}` read that decline as an empty ANSWER: four silent
                # drops, no closure verdict, no provenance, on both prompts, at every SHA. The
                # same prompt through `run_once` routes to demand_owned_mixed_turn and renders
                # per-slot failure rows.
                #
                # A caller embedding this runtime calls `run_once`. That is the entrance; the
                # frontdoor is a gate inside it. Driving the gate measured a door that is not
                # the one that answers this class of turn.
                result = self.agent.run_once(
                    prompt, session_id_override=chat_id, source_context=ctx
                )
            except Exception as exc:
                served = Served(self.name, prompt, chat_id, "", {}, {}, time.time() - t0)
                served.transport_error = f"{type(exc).__name__}: {exc}"
                return served
        result = result if isinstance(result, dict) else {}
        commit = result.get("vool_response_commit")
        if not isinstance(commit, dict):
            # The transport door mints the commit from the turn's stashed verdict. In-process
            # there is no transport, so the same authority is invoked directly -- never a
            # second finalizer, and never a hand-built certificate.
            try:
                from core.web.api.runtime import _response_commit

                commit = _response_commit(result, source_context=ctx)
            except Exception:
                commit = {}
        served = Served(
            self.name, prompt, chat_id,
            str(result.get("response") or ""),
            commit if isinstance(commit, dict) else {},
            result,
            time.time() - t0,
        )
        raw = result or None
        served.transport_error = "" if raw is not None else _DECLINED
        return served


# ---------------------------------------------------------------------------------------
# The 12 criteria
# ---------------------------------------------------------------------------------------

PASS, FAIL, INCONC = "PASS", "FAIL", "INCONCLUSIVE"


@dataclass
class Check:
    cid: str
    title: str
    status: str
    evidence: str


def evaluate(
    main: Served,
    *,
    slots: tuple[Slot, ...] | None = None,
    singletons: dict[str, Served],
    why_failed: Served | None,
    retry: Served | None,
    control_sea: Served | None,
    restart: Served | None,
) -> list[Check]:
    out: list[Check] = []
    slots = slots or main.slots
    main.slots = slots
    states = {s.slot_id: main.slot_state(s) for s in slots}
    n_slots = len(slots)

    def by_id(sid: str) -> Slot | None:
        return next((s for s in slots if s.slot_id == sid), None)

    def add(cid: str, title: str, status: str, ev: str) -> None:
        out.append(Check(cid, title, status, ev))

    # --- C1 the conversion slot uses current FX ---------------------------------------
    fx = by_id("fx") or by_id("fx_usd") or slots[0]
    if states[fx.slot_id] != "ANSWERED":
        add("C1", "conversion slot uses current FX", FAIL,
            f"slot {fx.slot_id} state={states[fx.slot_id]}; not answered, "
            f"so there is no FX value to judge")
    elif not fx.grounded(main.body):
        add("C1", "conversion slot uses current FX", FAIL,
            f"answered but no source marker and/or no ISO date within "
            f"{MAX_FX_STALENESS_DAYS}d of {_dt.date.today()}")
    else:
        add("C1", "conversion slot uses current FX", PASS,
            "converted value + source + fresh date")

    # --- C2 gold amount with unit/assumption ------------------------------------------
    gold = by_id("gold")
    if gold is None:
        add("C2", "gold amount, current price, unit/assumption stated", INCONC,
            "this prompt asks for no gold slot")
    elif states["gold"] != "ANSWERED":
        add("C2", "gold amount, current price, unit/assumption stated", FAIL,
            f"slot state={states['gold']}"
            + ("; body mentions gold but carries no quantity bound to a mass unit "
               "(a price quote is not an amount)" if _has(main.body, "gold", "XAU") else ""))
    elif not gold.grounded(main.body):
        add("C2", "gold amount, current price, unit/assumption stated", FAIL,
            "amount present but no unit/assumption statement and/or no source marker")
    else:
        add("C2", "gold amount, current price, unit/assumption stated", PASS,
            "quantity+mass unit, unit/assumption stated, source present")

    # --- C3 Rome weather from a current source ----------------------------------------
    weather = by_id("weather") or by_id("berlin")
    if weather is None:
        add("C3", "weather slot from a current source", INCONC, "no weather slot")
    elif states[weather.slot_id] != "ANSWERED":
        add("C3", "weather slot from a current source", FAIL,
            f"slot {weather.slot_id} state={states[weather.slot_id]}")
    elif not weather.grounded(main.body):
        add("C3", "weather slot from a current source", FAIL,
            "reading present but no source marker")
    else:
        add("C3", "weather slot from a current source", PASS,
            "requested city + temperature + source")

    # --- C4 Baltic water temperature, caveated, not air, not universal -----------------
    ev4: list[str] = []
    status4 = PASS
    water = by_id("water")
    if water is None:
        status4 = INCONC
        ev4.append("this prompt asks for no water slot")
    elif states["water"] != "ANSWERED":
        status4 = FAIL
        ev4.append(f"slot state={states['water']}")
    else:
        if not water.grounded(main.body):
            status4 = FAIL
            ev4.append("answered without a location/source caveat")
        if control_sea is not None and control_sea.text:
            baltic_nums = set(_TEMP_RE.findall(main.body))
            ctrl_nums = set(_TEMP_RE.findall(control_sea.body))
            shared = baltic_nums & ctrl_nums
            if shared:
                status4 = FAIL
                ev4.append(f"UNIVERSAL NUMBER: same reading {sorted(shared)} served for the "
                           f"Baltic and for the control sea")
        else:
            ev4.append("universal-number control not run")
    # air-reading-for-a-random-town: a weather/temperature line naming a location that is
    # neither Rome nor a Baltic locality, standing in the answered body.
    strays = _stray_location_lines(main.body, slots)
    if strays:
        ev4.append(f"stray location line(s) in the answered body: {strays}")
        if water is not None and states["water"] != "ANSWERED":
            status4 = FAIL
    add("C4", "Baltic water temp: caveated, not an air reading, not universal",
        status4, "; ".join(ev4) or "caveated water reading with source")

    # --- C5 all four slots preserved ---------------------------------------------------
    dropped = [k for k, v in states.items() if v == "SILENTLY_DROPPED"]
    add("C5", f"all {n_slots} requested slots preserved", FAIL if dropped else PASS,
        f"states={states}" + (f"; SILENTLY DROPPED: {dropped}" if dropped else ""))

    # --- C5b the certificate tracks the request (S5) ------------------------------------
    unresolved = [k for k, v in states.items() if v != "ANSWERED"]
    closure = main.closure
    covered = bool(closure.get("covered")) if closure else None
    open_count = closure.get("open_count")
    # `open_count` answers "is this turn structurally terminal?", not "how many slots went
    # unanswered". It is 0 on EVERY commit that exists: the sweep drives every demand out of
    # {planned, absent}, and finalization refuses to mint a commit for a turn that is not
    # terminal. Scoring coverage from it failed every honest partial turn -- including one that
    # disclosed each dropped slot with a reason -- so the cell could not be satisfied by a
    # correct runtime, and a criterion whose instrument cannot pass measures nothing.
    #
    # The census the certificate carries is the disclosure. Ruling:
    # docs/SLOT_CLOSURE_OPEN_COUNT_R1_2026-09-05.md.
    disclosed = int(closure.get("demand_unanswered") or 0) + int(
        closure.get("demand_indeterminate") or 0
    )
    has_census = "demand_unanswered" in closure or "demand_indeterminate" in closure
    if closure == {}:
        add("C5b", "closure verdict tracks unanswered slots", INCONC,
            "no closure_verdict on the served commit")
    elif unresolved and covered:
        add("C5b", "closure verdict tracks unanswered slots", FAIL,
            f"closure_verdict={closure} certifies coverage while "
            f"{len(unresolved)} of {n_slots} slots are unanswered: {unresolved}")
    elif unresolved and not has_census:
        add("C5b", "closure verdict tracks unanswered slots", FAIL,
            f"closure_verdict={closure} carries no demand census, so it discloses nothing "
            f"about {len(unresolved)} unanswered slots: {unresolved}")
    elif unresolved and disclosed < len(unresolved):
        add("C5b", "closure verdict tracks unanswered slots", FAIL,
            f"census discloses {disclosed} unresolved demands, understating "
            f"{len(unresolved)} unanswered slots: {unresolved}")
    else:
        add("C5b", "closure verdict tracks unanswered slots", PASS, f"closure={closure}")

    # --- C6 one slot failing does not stop the others -----------------------------------
    solo_ok = {
        sid: (srv.slot_state(next(s for s in slots if s.slot_id == sid)) == "ANSWERED")
        for sid, srv in singletons.items()
        if any(s.slot_id == sid for s in slots)
    }
    if not solo_ok:
        add("C6", "one slot failing does not stop the others", INCONC,
            "singleton controls not run")
    else:
        regressed = [
            sid for sid, ok in solo_ok.items()
            if ok and states.get(sid) == "SILENTLY_DROPPED"
        ]
        add("C6", "one slot failing does not stop the others",
            FAIL if regressed else PASS,
            f"answerable alone={sorted(k for k, v in solo_ok.items() if v)}; "
            f"lost in the 4-slot turn={regressed}")

    # --- C7 a failed slot is named unavailable WITH a reason ----------------------------
    ev7: list[str] = []
    status7 = PASS
    for slot in slots:
        if states[slot.slot_id] == "SILENTLY_DROPPED":
            status7 = FAIL
            ev7.append(f"{slot.slot_id}: dropped with no failure row")
            continue
        row = main.named_failed(slot)
        if not row:
            continue
        reason = _reason_of(row, slot)
        if not reason:
            status7 = FAIL
            ev7.append(f"{slot.slot_id}: named unavailable with no reason ({row!r})")
        elif _RAW_INTERNAL_RE.search(reason):
            status7 = FAIL
            ev7.append(f"{slot.slot_id}: reason leaks internal failure text: {reason!r}")
    if main.unattributed_failure_rows:
        status7 = FAIL
        ev7.append(
            f"{len(main.unattributed_failure_rows)} failure row(s) map to no requested "
            f"slot (mis-attributed accounting): {main.unattributed_failure_rows[:3]}"
        )
    add("C7", "failed slot named unavailable WITH a reason, never silently dropped",
        status7, "; ".join(ev7) or "every unanswered slot named with a clean reason")

    # --- C7b one failed slot, one row, one reason ---------------------------------------
    # Measured live at 0b4c2eb4: the canonical turn served FIVE failure rows for TWO
    # slots, and the gold slot got two rows with mutually exclusive reasons ("plan
    # deadline expired" and "was attempted and did not come back").  A certificate that
    # cannot say how many things failed, or why, is not accounting.
    owners: dict[str, list[str]] = {}
    for row in main.failure_rows:
        owner = main._row_owner(row)
        if owner:
            owners.setdefault(owner, []).append(row)
    dupes = {k: v for k, v in owners.items() if len(v) > 1}
    if not main.failure_rows:
        add("C7b", "one failed slot -> one row -> one reason", PASS, "no failure rows")
    elif dupes:
        detail = "; ".join(
            f"{k}: {len(v)} rows, reasons={[_reason_of(r, _slot(k, slots))[:60] for r in v]}"
            for k, v in dupes.items()
        )
        add("C7b", "one failed slot -> one row -> one reason", FAIL,
            f"{len(main.failure_rows)} rows for {len(owners)} slot(s); {detail}")
    else:
        add("C7b", "one failed slot -> one row -> one reason", PASS,
            f"{len(main.failure_rows)} rows for {len(owners)} slot(s)")

    # --- C8 no slot listed unanswered when it was answered ------------------------------
    contradicted = [k for k, v in states.items() if v == "CONTRADICTED"]
    add("C8", "no slot listed unanswered when it was answered",
        FAIL if contradicted else PASS,
        f"contradicted={contradicted}" if contradicted else "no slot both answered and listed failed")

    # --- C9 "why did that fail?" points at the exact failed slot/tool -------------------
    failed_ids = [k for k, v in states.items() if v in ("NAMED_FAILED", "SILENTLY_DROPPED")]
    if why_failed is None:
        add("C9", "'why did that fail?' names the exact failed slot/tool", INCONC,
            "follow-up not run")
    elif not failed_ids:
        add("C9", "'why did that fail?' names the exact failed slot/tool", INCONC,
            "no slot failed in the main turn, so nothing to attribute")
    else:
        hit = [sid for sid in failed_ids
               if _has(why_failed.text, *next(s for s in slots if s.slot_id == sid).subject_tokens)]
        wrong = [
            sid for sid, st in states.items()
            if st == "ANSWERED"
            and _has(why_failed.text,
                     *next(s for s in slots if s.slot_id == sid).subject_tokens)
            and _has(why_failed.text, "fail", "could not", "unavailable", "error")
        ]
        names_tool = _has(
            why_failed.text, "tool", "source", "api", "search", "lookup", "provider",
            "web.research", "plan", "timeout", "deadline", "wttr", "open-meteo",
        )
        ok = bool(hit) and names_tool and not wrong
        add("C9", "'why did that fail?' names the exact failed slot/tool",
            PASS if ok else FAIL,
            f"failed={failed_ids}; named in follow-up={hit}; tool/source named={names_tool}; "
            f"mis-blamed answered slots={wrong}")

    # --- C10 "retry that exact failed request" retries THAT slot ------------------------
    if retry is None:
        add("C10", "'retry that exact failed request' retries that slot, not a fresh search",
            INCONC, "follow-up not run")
    elif not failed_ids:
        add("C10", "'retry that exact failed request' retries that slot, not a fresh search",
            INCONC, "no slot failed in the main turn")
    else:
        addressed = [sid for sid in failed_ids
                     if _has(retry.text,
                             *next(s for s in slots if s.slot_id == sid).subject_tokens)]
        # A fresh search re-answers everything; a targeted retry does not.
        re_answered_others = [
            sid for sid, st in states.items()
            if st == "ANSWERED"
            and next(s for s in slots if s.slot_id == sid).answered(retry.body)
        ]
        ok = bool(addressed) and len(re_answered_others) <= 1
        add("C10", "'retry that exact failed request' retries that slot, not a fresh search",
            PASS if ok else FAIL,
            f"failed={failed_ids}; addressed by retry={addressed}; "
            f"already-answered slots re-served (fresh-search signature)={re_answered_others}")

    # --- C11 receipts show which tool/source/model handled each slot --------------------
    prov = main.provenance
    per_slot = _per_slot_attribution(main, slots)
    # EVERY requested slot needs attribution, answered or not: "which tool handled this"
    # is exactly the question a failed slot raises.  Counting only non-dropped slots let
    # a turn that dropped everything pass vacuously -- measured, on the operator's
    # evening variant, in this harness's own first live run.
    # The criterion is PER SLOT. The turn-grain block was used as a precondition, so a commit
    # carrying complete per-slot receipts still scored "no provenance block" when the turn-grain
    # dict happened to be empty -- measured 2026-09-05 on a commit whose display_metadata held
    # slot_receipts for all four slots beside a `provenance: {}`. Turn-grain provenance is what
    # this criterion exists to REPLACE; requiring it to be present first is the defect wearing
    # the instrument's clothes.
    if not per_slot and not prov:
        add("C11", "served receipts show tool/source/model per slot", FAIL,
            "no provenance and no per-slot receipts on the served commit")
    elif len(per_slot) < n_slots:
        add("C11", "served receipts show tool/source/model per slot", FAIL,
            f"provenance is turn-grain only: keys={sorted(prov)[:12]}; "
            f"per-slot attributions found {len(per_slot)}/{n_slots}: {per_slot}")
    else:
        add("C11", "served receipts show tool/source/model per slot", PASS, str(per_slot))

    # --- C12 restart/retry never turns prior failed state into semantic content ---------
    later = [("why", why_failed), ("retry", retry), ("restart", restart)]
    later = [(name, srv) for name, srv in later if srv is not None]
    if not later:
        add("C12", "restart/retry never converts prior failed state into content", INCONC,
            "no follow-up turns run")
    else:
        offenders: list[str] = []
        for name, srv in later:
            srv.slots = slots
            for slot in slots:
                if states[slot.slot_id] == "ANSWERED":
                    continue
                # The slot had NO retrieved value in the main turn.  A later turn that
                # asserts a value for it with no source is prior no-answer state turned
                # into semantic content -- criterion 12, whichever follow-up does it.
                if slot.answered(srv.body) and not slot.grounded(srv.body):
                    offenders.append(
                        f"[{name}] {slot.slot_id}: value asserted with no source/caveat")
                elif (
                    _has(srv.body, *slot.subject_tokens)
                    and _TEMP_RE.search(srv.body)
                    and not _SOURCE_RE.search(srv.body)
                    and slot.slot_id in ("water", "weather", "berlin")
                ):
                    offenders.append(
                        f"[{name}] {slot.slot_id}: a reading served with no source at all")
                row = main.named_failed(slot)
                reason = _reason_of(row, slot) if row else ""
                core_reason = reason.strip(" .").lower()
                if len(core_reason) > 20 and core_reason in srv.body.lower():
                    offenders.append(
                        f"[{name}] {slot.slot_id}: prior failure text served as body content")
        add("C12", "restart/retry never converts prior failed state into content",
            FAIL if offenders else PASS,
            "; ".join(sorted(set(offenders))) or "no prior-state leakage detected")

    return out


_KNOWN_LOCATION_TOKENS = ("rome", "roma", "baltic", "baltc")
_LOCATION_LINE_RE = re.compile(r"^\s*([A-ZÀ-Ý][\wÀ-ÿ'’\-\. ]{2,40}),\s*([A-ZÀ-Ý][\wÀ-ÿ \-]{2,30}):")


def _stray_location_lines(body: str, slots: tuple[Slot, ...] = ()) -> list[str]:
    """Weather-shaped lines naming a place nobody asked about (the Saint-Merri / Newberry
    Springs / Össby pollution — RSS property 5's direction)."""
    out: list[str] = []
    for line in body.splitlines():
        m = _LOCATION_LINE_RE.match(line)
        if not m or not _TEMP_RE.search(line):
            continue
        wanted = list(_KNOWN_LOCATION_TOKENS) + [
            t.lower() for s in slots for t in s.distinctive
        ]
        if any(tok in line.lower() for tok in wanted):
            continue
        out.append(line.strip()[:90])
    return out


def _slot(slot_id: str, slots: tuple[Slot, ...] = ()) -> Slot:
    for s in (slots or CANONICAL_SLOTS):
        if s.slot_id == slot_id:
            return s
    raise KeyError(slot_id)


def _reason_of(row: str, slot: Slot) -> str:
    """The reason text in a failure row, stripped of the restated request.

    A row that only restates the ask is 'named' but carries no reason — criterion 7.
    """
    for sep in ("—", " - ", "—", ":", "--"):
        if sep in row:
            head, _, tail = row.partition(sep)
            tail = tail.strip()
            # peel a leading "could not be answered" boilerplate
            tail = re.sub(r"^(could not be answered|failed|unavailable)\s*:?\s*", "", tail,
                          flags=re.I).strip()
            if tail and len(tail) > 3:
                return tail
            _ = head
    return ""


def _per_slot_attribution(served: Served,
                          slots: tuple[Slot, ...] = ()) -> dict[str, str]:
    """Slot -> tool/source, as far as the SERVED receipt allows it to be read.

    Criterion 11 asks the receipt to say who handled each slot.  This reads the commit
    for any structure that maps a slot/clause to a tool, source or model.  Turn-grain
    fields (route, tool, participating_models) are NOT per-slot and are not counted.
    """
    out: dict[str, str] = {}
    commit = served.commit or {}
    for key in ("slot_receipts", "per_slot", "slice_receipts", "coverage", "slots"):
        blob = commit.get(key)
        if isinstance(blob, dict) and blob:
            for k, v in blob.items():
                out[str(k)] = str(v)[:60]
    dm = commit.get("display_metadata")
    if isinstance(dm, dict):
        for key in ("slot_receipts", "per_slot", "slice_receipts", "slots"):
            blob = dm.get(key)
            if isinstance(blob, dict) and blob:
                for k, v in blob.items():
                    out[str(k)] = str(v)[:60]
    # A source named ON the slot's own served line is per-slot attribution the reader can
    # actually see, so it counts.
    for slot in (slots or CANONICAL_SLOTS):
        for line in served.body.splitlines():
            if _has(line, *slot.subject_tokens) and _SOURCE_RE.search(line):
                out.setdefault(slot.slot_id, "inline-source:" + line.strip()[:50])
    return out


# ---------------------------------------------------------------------------------------
# Self-test: the harness must FAIL a runtime that silently drops a slot
# ---------------------------------------------------------------------------------------

_SELFTEST_DROP = (
    "EUR/RUB: 1000 EUR x 99.8 = 99800.0 RUB (source: Frankfurter public institutional "
    "rates; observed: {today}).\n"
)
_SELFTEST_E007 = (
    "EUR/RUB: 1000 EUR x 99.8 = 99800.0 RUB (source: Frankfurter public institutional "
    "rates; observed: {today}).\n"
    "Rome, Italy: Partly cloudy, 29.8C (source: open-meteo.com)\n"
    "Newberry Springs, United States: Partly cloudy, 35.7C (source: open-meteo.com)\n"
    "Ossby, Sweden: Overcast, 17.0C (source: wttr.in)\n\n"
    "Could not be answered:\n"
    "- How much gold can I buy with 1000 EUR? — could not be answered: plan deadline "
    "expired before this request could be answered (missing: steps, values)\n"
    "- What is the water temperature in the Baltic Sea? — the available answer path "
    "does not safely match this request"
)
_SELFTEST_LEAK = (
    "Saint-Merri, France: Overcast, 18.0C (source: wttr.in)\n\n"
    "Could not be answered:\n"
    "- what time is in rome now — machine_observation found nothing to act on\n"
    "- what is the water temperature in the Baltic Sea — could not be answered: cannot "
    "import name '_weather_subtask' from 'core.agent_runtime.live_data_plan' "
    "(/Users/x/code/core/agent_runtime/live_data_plan.py)"
)
_SELFTEST_GOOD = (
    "EUR/RUB: 1000 EUR x 99.8 = 99800.0 RUB (source: Frankfurter institutional rates; "
    "observed: {today}).\n"
    "Gold: at a spot price of 2400 EUR per troy ounce (source: metals-api, observed "
    "{today}), 1000 EUR buys about 0.42 oz of gold (troy ounce assumed).\n"
    "Rome, Italy: Partly cloudy, 29.8C (source: open-meteo.com)\n"
    "Baltic Sea water temperature: around 19.4C at the Arkona station, measured buoy "
    "reading (source: copernicus marine); varies by location.\n"
)


# The canonical turn as actually served at 0b4c2eb4 on 2026-08-29T19:20Z by this harness:
# FIVE failure rows for TWO slots, with two mutually exclusive reasons for the gold slot.
_SELFTEST_DUPE_ROWS = (
    "EUR/RUB: 1000 EUR x 99.8 = 99800.0 RUB (source: Frankfurter public institutional "
    "rates; observed: {today}).\n"
    "Rome, Italy: Partly cloudy, 28.8C (source: open-meteo.com)\n\n"
    "Could not be answered:\n"
    "- How much gold can I buy with 1000 EUR? — could not be answered: plan deadline "
    "expired before this request could be answered (missing: steps, values)\n"
    "- What is the water temperature in the Baltic Sea? — the available answer path does "
    "not safely match this request\n"
    "- how much gold can I buy with it — could not be answered: plan deadline expired "
    "before this request could be answered (missing: steps, values)\n"
    "- how much gold can I buy with it — was attempted and did not come back\n"
    "- water temperature: in the Baltic Sea — is not something this runtime can look up"
)
# The `why did that fail?` follow-up as actually served at 0b4c2eb4 on the operator's
# evening chat: numbers invented for slots that were never retrieved, with no source.
_SELFTEST_FABRICATING_FOLLOWUP = (
    "1,500 EUR is about 1,748 USD. With that, you could buy various items. Baltic Sea "
    "water temp is around 12-13C in summer. Berlin river temp is similar."
)


def selftest() -> int:
    today = _dt.date.today().isoformat()
    failures = 0

    def _mk(text: str) -> Served:
        return Served("selftest", CANONICAL_4SLOT, AUDIT_PREFIX + "selftest",
                      text.format(today=today),
                      {"closure_verdict": {"covered": True, "open_count": 0},
                       "display_metadata": {"provenance": {"route": "x", "tool": ""}}})

    print("SELFTEST 1 — a runtime that answers FX only and drops three slots silently")
    checks = evaluate(_mk(_SELFTEST_DROP), singletons={}, why_failed=None, retry=None,
                      control_sea=None, restart=None)
    c5 = next(c for c in checks if c.cid == "C5")
    c7 = next(c for c in checks if c.cid == "C7")
    c5b = next(c for c in checks if c.cid == "C5b")
    for c in (c5, c7, c5b):
        print(f"  {c.cid}: {c.status}  {c.evidence[:120]}")
    if c5.status != FAIL or c7.status != FAIL or c5b.status != FAIL:
        print("  !! HARNESS BROKEN: a silent 3-slot drop did not fail C5/C7/C5b")
        failures += 1

    print("SELFTEST 2 — the real E007 served body (2 answered, 2 named, 2 stray locations)")
    checks = evaluate(_mk(_SELFTEST_E007), singletons={}, why_failed=None, retry=None,
                      control_sea=None, restart=None)
    for c in checks:
        print(f"  {c.cid}: {c.status}  {c.evidence[:110]}")
    if next(c for c in checks if c.cid == "C5b").status != FAIL:
        print("  !! HARNESS BROKEN: covered:true over 2 unanswered slots did not fail C5b")
        failures += 1
    if next(c for c in checks if c.cid == "C2").status != FAIL:
        print("  !! HARNESS BROKEN: a missing gold amount did not fail C2")
        failures += 1
    if "stray" not in next(c for c in checks if c.cid == "C4").evidence:
        print("  !! HARNESS BROKEN: Newberry Springs / Ossby pollution not detected")
        failures += 1

    print("SELFTEST 3 — the S1 body: wrong-city answer + a leaked ImportError as a reason")
    checks = evaluate(_mk(_SELFTEST_LEAK), singletons={}, why_failed=None, retry=None,
                      control_sea=None, restart=None)
    c7 = next(c for c in checks if c.cid == "C7")
    print(f"  C7: {c7.status}  {c7.evidence[:200]}")
    if c7.status != FAIL or "internal" not in c7.evidence:
        print("  !! HARNESS BROKEN: a raw ImportError served as a reason did not fail C7")
        failures += 1

    print("SELFTEST 4 — a compliant body must NOT fail the slot-accounting criteria")
    checks = evaluate(_mk(_SELFTEST_GOOD), singletons={}, why_failed=None, retry=None,
                      control_sea=None, restart=None)
    for c in checks:
        if c.cid in ("C1", "C2", "C3", "C4", "C5", "C7", "C8"):
            print(f"  {c.cid}: {c.status}  {c.evidence[:110]}")
    bad = [c.cid for c in checks
           if c.cid in ("C1", "C2", "C3", "C4", "C5", "C7", "C8") and c.status == FAIL]
    if bad:
        print(f"  !! HARNESS OVER-STRICT: a compliant body failed {bad}")
        failures += 1

    print("SELFTEST 5 — five failure rows for two slots, two contradictory gold reasons")
    checks = evaluate(_mk(_SELFTEST_DUPE_ROWS), singletons={}, why_failed=None, retry=None,
                      control_sea=None, restart=None)
    c7b = next(c for c in checks if c.cid == "C7b")
    print(f"  C7b: {c7b.status}  {c7b.evidence[:200]}")
    if c7b.status != FAIL:
        print("  !! HARNESS BROKEN: duplicated contradictory failure rows did not fail C7b")
        failures += 1

    print("SELFTEST 6 — a follow-up inventing a value for a slot that was never retrieved")
    main6 = _mk(_SELFTEST_DROP)
    fu = Served("selftest", "why did that fail?", AUDIT_PREFIX + "selftest",
                _SELFTEST_FABRICATING_FOLLOWUP)
    checks = evaluate(main6, singletons={}, why_failed=fu, retry=None,
                      control_sea=None, restart=None)
    c12 = next(c for c in checks if c.cid == "C12")
    print(f"  C12: {c12.status}  {c12.evidence[:220]}")
    if c12.status != FAIL:
        print("  !! HARNESS BROKEN: an invented follow-up value did not fail C12")
        failures += 1

    print()
    print("SELFTEST RESULT:", "OK" if not failures else f"{failures} HARNESS DEFECTS")
    return 1 if failures else 0


# ---------------------------------------------------------------------------------------


def run_gauntlet(entrance: Any, *, tag: str, prompt: str, slots: tuple[Slot, ...],
                 singletons: bool, followups: bool) -> dict[str, Any]:
    stamp = time.strftime("%H%M%S")
    cid = f"{AUDIT_PREFIX}{tag}-{stamp}"
    print(f"\n--- driving {entrance.name} :: {tag} ---")
    print(f"    chat_id={cid}")
    main = entrance.send(prompt, cid)
    main.slots = slots
    print(f"    {main.latency_s:.1f}s  {len(main.text)} chars"
          + (f"  TRANSPORT ERROR: {main.transport_error}" if main.transport_error else ""))
    print("    ----- served text -----")
    for line in main.text.splitlines():
        print(f"    | {line}")
    print("    -----------------------")
    print(f"    closure_verdict={main.closure}")
    print(f"    provenance_footer={(main.commit.get('display_metadata') or {}).get('provenance_footer')!r}")

    solo: dict[str, Served] = {}
    if singletons:
        for slot in slots:
            s_cid = f"{AUDIT_PREFIX}{tag}-solo-{slot.slot_id}-{stamp}"
            srv = entrance.send(slot.singleton_prompt, s_cid)
            srv.slots = slots
            solo[slot.slot_id] = srv
            print(f"    [solo {slot.slot_id}] {srv.latency_s:5.1f}s "
                  f"state={srv.slot_state(slot):16} :: {srv.text.replace(chr(10),' / ')[:130]}")

    why = retry = restart = control = None
    if followups:
        why = entrance.send("why did that fail?", cid)
        print(f"    [why]     {why.text.replace(chr(10), ' / ')[:200]}")
        retry = entrance.send("retry that exact failed request", cid)
        print(f"    [retry]   {retry.text.replace(chr(10), ' / ')[:200]}")
        restart = entrance.send(prompt, cid)
        print(f"    [restart] {restart.text.replace(chr(10), ' / ')[:200]}")
        control = entrance.send(
            "What is the water temperature in the Mediterranean Sea?",
            f"{AUDIT_PREFIX}{tag}-ctrlsea-{stamp}",
        )
        print(f"    [ctrl-sea] {control.text.replace(chr(10), ' / ')[:200]}")

    checks = evaluate(main, slots=slots, singletons=solo, why_failed=why, retry=retry,
                      control_sea=control, restart=restart)
    print()
    for c in checks:
        print(f"    {c.status:12} {c.cid:4} {c.title}")
        print(f"                      {c.evidence[:220]}")
    return {
        "tag": tag,
        "entrance": entrance.name,
        "prompt": prompt,
        "chat_id": cid,
        "served_text": main.text,
        "closure_verdict": main.closure,
        "provenance": main.provenance,
        "latency_s": main.latency_s,
        "transport_error": main.transport_error,
        "slot_states": {s.slot_id: main.slot_state(s) for s in slots},
        "failure_rows": main.failure_rows,
        "singleton_states": {
            sid: srv.slot_state(_slot(sid, slots)) for sid, srv in solo.items()
        },
        "singleton_texts": {sid: srv.text for sid, srv in solo.items()},
        "followups": {
            "why": why.text if why else None,
            "retry": retry.text if retry else None,
            "restart": restart.text if restart else None,
            "control_sea": control.text if control else None,
        },
        "checks": [c.__dict__ for c in checks],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--entrance", default="api_chat",
                    choices=["api_chat", "api_generate", "frontdoor"])
    ap.add_argument("--base-url", default="http://127.0.0.1:11435")
    ap.add_argument("--tree", default="/Users/example-user/Desktop/vool-checkout")
    ap.add_argument("--prompt", default="canonical",
                    choices=["canonical", "operator_evening", "both"])
    ap.add_argument("--no-singletons", action="store_true")
    ap.add_argument("--no-followups", action="store_true")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.entrance == "api_chat":
        entrance: Any = ApiChat(args.base_url, args.timeout)
    elif args.entrance == "api_generate":
        entrance = ApiGenerate(args.base_url, args.timeout)
    else:
        entrance = InProcessFrontDoor(args.tree)

    print("=" * 78)
    print("RED-B  A13/A14 SERVED GAUNTLET")
    print("=" * 78)
    print(f"entrance   : {entrance.name}")
    print(f"target     : {getattr(entrance, 'base_url', getattr(entrance, 'core_file', '?'))}")
    print(f"captured   : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"audit ids  : {AUDIT_PREFIX}*")

    runs = []
    plan = []
    if args.prompt in ("canonical", "both"):
        plan.append("canonical")
    if args.prompt in ("operator_evening", "both"):
        plan.append("operator-evening")
    for tag in plan:
        prompt, slots = PROMPTS[tag]
        runs.append(run_gauntlet(
            entrance, tag=tag, prompt=prompt, slots=slots,
            singletons=not args.no_singletons, followups=not args.no_followups,
        ))

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for run in runs:
        tally = {}
        for c in run["checks"]:
            tally[c["status"]] = tally.get(c["status"], 0) + 1
        print(f"{run['entrance']:14} {run['tag']:18} {tally}  slots={run['slot_states']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"instrument": "RED-B", "runs": runs,
                       "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                      fh, indent=1)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
