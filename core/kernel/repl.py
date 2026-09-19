"""A terminal chat where a local model owns every judgment and the kernel enforces the laws.

    cd ~/vool/worktrees/exp-kernel-laws-618ea934
    VOOL_HOME=/tmp/vool-kernel-repl-home PYTHONPATH=. \
        .venv/bin/python -m core.kernel.repl

Architecture of one turn — judgments above, laws below:

  MODEL (qwen via local Ollama, temperature 0, every call on the effect tape)
    1. extracts the turn's obligations, choosing each one's lane and search query
    3. synthesizes the answer AS TYPED CLAIMS from the collected evidence
  KERNEL (the four laws — nothing model-shaped may bypass them)
    2. runs each lane: arithmetic as a tool; web lookups behind the capability
       gate and the one egress door, every hit becoming a receipt
    4. validates every claim against its receipt (a number a receipt does not
       contain refuses the claim), then COMMITS — refusing while any obligation
       is open, and naming what shipped partial

There is deliberately NO phrase-table splitter, no keyword router, and no verbatim-snippet
answering left in this file: those were the measured root causes of the last round's
failures, and each was a model judgment implemented as a heuristic. A failed model
judgment degrades loudly (named in the transcript) — it is never silently replaced by a
regex that will dodge the next phrasing.

Commands:  :replay  re-run the previous turn from its tape — zero model calls, zero
                    network calls, byte-identical transcript
           :quit    leave

The stored search key is read from the Keychain and never printed.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import html
import json
import math
import operator
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

from core.kernel.capabilities import CapabilitySet, ForkContext, check_tool_call
from core.kernel.effects import EffectJournal, EffectRunner
from core.kernel.evidence_types import (
    CLAIM_TYPES,
    EvidenceTypeError,
    TypedClaim,
    _number_tokens,
    render_typed_answer,
    validate_claims,
)
from core.kernel.lexical_spans import has_quantity, lex_spans, quantity_values
from core.kernel.obligations import CommitRefused, Obligation, TurnTransaction
from core.kernel.paged_memory import ContextUndercovered, PagedSession, RecallRefused
from core.kernel.semantic_identity import coordinator_children
from core.ollama_endpoint import ollama_api_url as _ollama_api_url
from core.remote_fetch_policy import open_remote
from core.runtime_flags import flag_enabled

_OLLAMA_URL = _ollama_api_url("/api/chat")
# Pluggable judgment model: the runtime's laws are model-agnostic, so a stronger local
# model must lift answer quality with ZERO code change — set VOOL_REPL_MODEL to try one
# (e.g. qwen3:8b). This is the Section 1 mandate in miniature.
_ARBITER_MODEL = os.environ.get("VOOL_REPL_MODEL", "qwen2.5:7b")
def _bounded_pow(base: float, exp: float) -> float:
    # BLOCK-C seam 7: exponentiation is supported, resource-bounded — an
    # adversarial 9**9**9 must refuse, never hang the turn (rule 0.4).
    if abs(exp) > 400 or abs(base) > 1e12:
        raise ValueError(f"exponent/base out of supported range: {base}**{exp}")
    result = base ** exp
    if not math.isfinite(result):
        raise ValueError(f"non-finite power result: {base}**{exp}")
    return result


_ARITH_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Pow: _bounded_pow}
_TAG_RE = re.compile(r"<[^>]+>")

_EXTRACT_SYSTEM = """You split a user's message into its independent requests (obligations).
Return ONLY JSON: {"obligations": [{"description": str, "lane": str, "query": str,
"format": str, "source_offset": int, "resolves_carryover": str}, ...]}
"source_offset" = the character index in the user's message where THIS request's text
begins (0 for the first). It fixes the true reading order so a later cancel binds to
the action it actually follows, even when you list obligations in a different order.
Lanes:
- "chat": greetings, small talk, meta remarks. "query" = ""
- "clarify": the request is missing a parameter you would have to guess (a location, a
  budget, a date, one-way vs return). Put the missing parameter in "query"
- "arithmetic": pure calculation over numbers THE USER STATED in the message or the
  conversation. Put a plain expression using only those numbers and + - * / ( ) in
  "query". Canonical re-expressions of stated quantities are allowed and preferred:
  a stated "45%" may be written 0.45; a stated "18h 45m" or "18:45" may be written
  1125 (minutes) or 18.75 (hours); "split equally among 4" may be written / 4. NEVER use a rate/price/distance from your memory — if the calculation needs a
  number the user did not state, that is a "web_lookup" (get the number) instead
- "web_lookup": needs current/real-world data (prices, RATES for currency conversion,
  distances, weather, news, product facts). Put a short high-quality search query in
  "query" carrying the disambiguating context from the message (place, route, currency,
  product names): a diesel-price query for a Vilnius-Riga trip that loses "Lithuania"
  returns some other market's numbers
- "machine": about THIS computer (its specs, its files, its folders), the user's local
  time/timezone, CALENDAR MATH from today ("what date is 45 days from now"), or a
  DETERMINISTIC STRING OPERATION on text the user gave (reverse a word, count its
  letters/characters/vowels/consonants). "query" MUST be exactly "specs",
  "biggest_files", "time", "date+<N>d" / "date-<N>d", or "str.reverse:<text>" /
  "str.count_letters:<text>" / "str.count_chars:<text>" / "str.count_vowels:<text>" /
  "str.count_consonants:<text>" — pick the op that counts EXACTLY the unit the user
  named (vowels is not characters, consonants is not letters); string work is
  computed, never recalled. If the
  request needs a machine capability NOT in this list (running shell commands,
  reading files or folders, editing code, using a named external tool), set
  "query" to "none:<what is missing>" — NEVER pick the nearest listed op
- "recall": returns the specific FIELD the user asks for (a code, a name, one
  question) — never the whole record unless the whole record is the ask.
- "recall": about THIS conversation's own record (what was asked/answered, in what order,
  "the first question", "earlier you said"). "query" = what to look for, or "" for the
  full ordered record
- "knowledge": stable universal facts a model may answer from memory — definitions,
  concepts, how things work. ONLY number-free facts qualify: a fact whose answer IS a
  checkable number or date (a release year, a spec, a population, a price) is
  "web_lookup" instead — memory numbers are exactly the fabrication class this kernel
  exists to stop. "query" = ""
- "intake": the message SUPPLIES DATA for an announced or ongoing task without
  asking you to execute yet. A request to READ, RUN, LIST, EDIT, or USE a tool
  is NEVER intake — route it "machine" with query "none:<what is missing>" so
  the kernel can refuse honestly instead of filing the ask as a note — structured facts, list items, options, headers that
  promise details to follow. The kernel records the data for later turns; do not
  look anything up and do not compute. "query" = ""
- "compose": ALSO every REVISION of a previous answer ("make the second one
  darker", "keep only the first two", "make that dwarf planets instead") — the
  previous answer's numbered items are in your context; the deliverable is the
  revised item(s), never an echo of the instruction.
- "compose": the user asks YOU to create content — brainstorm ideas, write a script,
  draft a message or review, design a slogan, summarize, translate. The deliverable is
  your own authored text, not a fact to check. "query" = ""
- "cancelled": the message itself RETRACTS or FORBIDS an action — a retraction ("hold
  on", "cancel that", "scratch X"), an abort ("abort that", "stop the search"), or a
  PROHIBITION on how to act ("do not browse", "don't use the web", "no tools", "without
  looking it up"). Emit a "cancelled" obligation whose "description" names WHAT is
  retracted or forbidden (echo the action words — "browse", "the WHO query", "the tool
  call") so the kernel can bind it to the exact effect; a blanket prohibition that names
  no single action cancels every effect it precedes. A request the user then re-issues
  as a REPLACEMENT ("instead, just X") stays on its own lane and survives. List the
  retracted/forbidden action with this lane instead of dropping it — the kernel accounts
  for every ask, and the LATEST instruction wins. "query" = ""
COMPLETENESS, checked in both directions: EVERY independent request in the message
gets its OWN obligation — two products to look up are two obligations plus the
comparison ask, a question plus a calculation is two. Never bundle two asks into one
row, and never invent an obligation the user did not make.
"format": the user's explicit OUTPUT-FORMAT order if they gave one ("only the year",
"one word", "no explanation"), copied verbatim; otherwise "".
If the conversation lists UNRESOLVED obligations from earlier turns and this message asks
about one (or answers a clarification), include an obligation for it and set
"resolves_carryover" to that carryover id; otherwise "resolves_carryover" = ""."""

_SYNTH_SYSTEM = """You write the final answer as typed claims, one JSON object per sentence.
Return ONLY JSON: {"claims": [{"obligation_id": str, "text": str, "type": str}, ...]}
THE NUMBER RULE — enforced mechanically, not a suggestion: you never type a digit that
came from evidence. A NUMBERS table gives every evidence number a token like {n3}; write
the token in your sentence ("100 EUR is {n3} RUB") and the runtime substitutes the value
and attaches the citation itself. A sentence mixing tokens from DIFFERENT sources is
rejected — one sentence, one source. Digits typed literally are allowed only for: numbers
the user themselves said, and harmless structure (dates you were given, list numbering).
Types:
- "observed": states something taken from evidence — MUST carry at least one {token}
- "timeless": universally stable fact (math, definitions). no tokens
- "unverified": from your general memory, not in evidence. no tokens
- "stipulated": restates a fact the USER themselves told you in this conversation (their
  dog's breed, their budget, their premise). no tokens
- "conversational": greeting reply IN YOUR OWN WORDS (a greeting echoed back verbatim
  is not a reply), or a QUESTION back to the user (for "clarify"
  obligations write exactly the question that gets the missing parameter)
For a "compose" obligation (write/brainstorm/draft/design/translate) the piece ITSELF is
the claim: ONE claim, type "conversational", the whole deliverable in "text" —
multi-sentence is expected there (measured live: four creative asks produced nothing
because every lane read as small talk).
Rules: at least one claim per obligation_id, normally exactly one — pick the
best-supported source; add a second claim only when sources genuinely disagree, saying
so. If evidence does not contain the answer, say what is missing rather than inventing.
HONOR THE ASK'S CONSTRAINTS (one-way vs round trip, city, currency, unit) and THE
EVIDENCE'S REGISTER (proposed/fictional/speculative must be said). If the user's premise
names things that do not exist as named, say that before comparing them. A FORMAT ORDER
in the prompt is law: the claim text is exactly the demanded output — a bare year, one
word — with no sentence wrapped around it."""


_DERIVE_SYSTEM = """Some obligations need a computation over numbers now present in the evidence.
Return ONLY JSON: {"computations": [{"obligation_id": str, "label": str,
"expression": str}, ...], "missing": [{"obligation_id": str, "need": str, "query": str}, ...]}
Both lists may be empty.
THE EXPRESSION RULE: an expression is written with NUMBER TOKENS from the NUMBERS table
plus + - * / ( ) and, when structure demands it, small whole numbers 1..1000 (a round
trip is {n2} * 2; per-100km is / 100). You never type an evidence value — the token IS
the value, and the runtime resolves its source itself. A number that has no token does
not exist for you: if the computation needs it, put the gap in "missing" with a precise
web search query (carry the product/place context). Typical values you remember are NOT
evidence. "label" says in a few words what is being computed.
COMPARISONS are first-class: {nA} > {nB} (also < >= <= == !=) computes to true/false
and its receipt grounds a verdict ("which is hotter/cheaper/earlier"). Emit the bare
comparison, never an if/then sentence.
THE REFERENT RULE: an expression may bind only quantities the receipts actually hold.
A quantity the user merely NAMES ("that product", "the Bitcoin price") with no receipt
is a GAP — put it in "missing" with a search query; never substitute message-text
numbers (turn ordinals, counts) for it."""


_VERIFY_SYSTEM = """You are a verification judge. You see a user question, the turn's
obligations, and numbered CLAIMS, each with the exact receipt excerpts it cites.
Return ONLY JSON:
{"verdicts": [{"claim": int, "bound": bool, "answers_the_ask": bool,
 "magnitude_sane": bool, "entity_supported": bool}, ...],
 "all_parts_answered": bool, "missing": str}
For each claim judge two things, from the given text alone:
- "bound": every number/value in the claim is attached to the SAME entity and
  attribute in the cited excerpt — a phone's weight is not its battery, a model
  year is not a charging speed, a URL digit is not a quantity. If the excerpt does
  not show the value belonging to what the claim says it belongs to, bound=false.
- "answers_the_ask": the claim actually answers (part of) what the user asked —
  page navigation text, generic descriptions, and off-topic facts are false.
- "magnitude_sane": the value's ORDER OF MAGNITUDE is plausible for the claimed
  quantity (17052 kWh for one car trip is not; a formula can be internally true
  and still built wrong). Default true when unsure.
- "entity_supported": a cited excerpt contains data about the SPECIFIC entity,
  variant, and reading the claim names. A GBP/USD receipt does not support a
  EUR/USD claim; Gen 14 evidence does not support a Gen 12 claim; one city's
  weather does not support another city; an OPENING price is not a CURRENT
  price; a future/dev branch is not the latest release; a base/quote FX rate
  does not support its inverse. Default true when unsure.
"all_parts_answered": do the claims together cover EVERY part the user requested?
"missing": one short phrase naming the uncovered part, or ""."""


def _openrouter_chat(system: str, user: str) -> str:
    """One cloud judgment call via the user's own OpenRouter key (BYOK, from the Keychain).

    A model id containing "/" is an OpenRouter id — the transport is the only difference:
    same prompts, same tape, same laws. PRIVACY BOUNDARY, stated plainly: on a cloud
    judge the user's words leave the machine; the banner says so whenever this path is
    active. The key is read at call time and appears nowhere but the auth header.
    """
    from core.credential_store import get_credential

    key = (get_credential("llm.cloud.openrouter") or "").strip()
    if not key:
        raise RuntimeError("no OpenRouter key stored (llm.cloud.openrouter)")

    # OPERATOR PATH, explicitly scoped (R2b1, amended): the REPL's cloud
    # judgment is not a turn effect, and the door grants nothing — the operator
    # surface owns its fetch here, by name. Scope-local account.
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("kernel.repl.model_call"):
        return _openrouter_chat_owned(system, user)


def _openrouter_chat_owned(system: str, user: str) -> str:
    from core.credential_store import get_credential

    key = (get_credential("llm.cloud.openrouter") or "").strip()
    if not key:
        raise RuntimeError("no OpenRouter key stored (llm.cloud.openrouter)")

    def call(json_mode: bool, reasoning_off: bool) -> str:
        # max_tokens is a CEILING, not a budget (project rule 4b): a reasoning model spends
        # its budget in the hidden channel first, so 1,600 returned EMPTY content on every
        # differential turn (the 2026-08-03 thinking-model class, on a new transport).
        body: dict[str, object] = {
            "model": _ARBITER_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": 8000,
        }
        if reasoning_off:
            body["reasoning"] = {"enabled": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "https://vool.dev",
                "X-Title": "VOOL",
            },
        )
        # THE SHARED DOOR, not a raw socket: veto first, then the ledger, then the wire.
        with open_remote(req, timeout=120) as resp:
            payload = json.loads(resp.read())
        message = payload["choices"][0]["message"]
        content = str(message.get("content") or "")
        if not content.strip():
            # Name the class instead of handing the JSON parser an empty string: the
            # reasoning channel consumed the output. The caller's repair retry then
            # carries a message a human (and the differential) can attribute.
            raise ValueError(
                "provider returned empty content"
                + (" (reasoning tokens consumed the budget)" if message.get("reasoning") else "")
            )
        return content

    attempts = ((True, True), (True, False), (False, False))
    last: Exception | None = None
    for json_mode, reasoning_off in attempts:
        for backoff in (0.0, 20.0, 40.0):  # free tiers rate-limit per minute; 429 is a wait, not a failure
            if backoff:
                time.sleep(backoff)
            try:
                return call(json_mode=json_mode, reasoning_off=reasoning_off)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    if retry_after and str(retry_after).isdigit():
                        time.sleep(min(60.0, float(retry_after)))
                    last = exc
                    continue
                if exc.code in (400, 404):  # this shape unsupported — try the next dialect
                    last = exc
                    break
                raise
        else:
            continue
    raise RuntimeError(f"OpenRouter rejected every request dialect: {last}")


def _ollama_chat(system: str, user: str, *, json_mode: bool = True) -> str:
    """One judgment call — local Ollama, or OpenRouter when the model id carries a slash.

    Thinking-capable local models get thinking turned OFF.

    A thinking model under format=json spends its token budget in the hidden reasoning
    channel and returns an EMPTY content field — the exact output-contract class this
    repo root-caused on 2026-08-03 and met again here with qwen3:8b (derive round dead on
    'Expecting value: char 0'). "think": false is the wire-format fix; models that reject
    the field get one retry without it. Adapter translates the wire, never the semantics.

    THE WIRE FORMAT IS THE CALLER'S DECISION, NOT THIS ADAPTER'S (council round-001).
    `"format": "json"` used to be hard-coded into every body, including the claimless-compose
    fallback whose system prompt says "No JSON, no wrapper, no commentary — output the piece
    itself only". Ollama's json mode constrains generation to JSON grammar, so that caller's
    plain-text contract was unsatisfiable: the model did not disobey, it was prevented from
    obeying, and a byte-exact turn shipped `{"bytes": "VECTOR-6842"}` instead of `VECTOR-6842`.
    Measured on the frozen tape and settled by probe (council/round-001/R2/PROBE1_RAW.json):
    same prompt, same model, temperature 0, only the wire differing —
        with "format": "json"  ->  `{ }`
        without format field   ->  `VECTOR-6842`
    A wire that decides the answer's SHAPE is the adapter translating semantics, which the
    paragraph above forbids. `json_mode` defaults True so every JSON-parsing caller is
    unchanged; only a caller that has declared plain text turns it off.

    KNOWN LIMITATION, deliberately not fixed in round-001 (out of the council's frozen scope):
    `json_mode` governs the LOCAL branch only. When `_ARBITER_MODEL` carries a slash the call
    routes to `_openrouter_chat`, whose outer signature does not accept the flag, so a
    plain-text caller on the cloud lane still gets `response_format: json_object`. Tracked in
    council/IDEA_BACKLOG.md; the local lane is what round-001 measured and repaired.
    """

    def call(think_field: bool) -> str:
        body: dict[str, object] = {
            "model": _ARBITER_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 1200},
        }
        if json_mode:
            body["format"] = "json"
        if think_field:
            body["think"] = False
        req = urllib.request.Request(
            _OLLAMA_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=180) as resp:  # local loopback, not remote egress
            return str(json.loads(resp.read())["message"]["content"])

    if "/" in _ARBITER_MODEL:
        return _openrouter_chat(system, user)
    # The canonical LocalModelPolicy: disabled means no local judgment call. Raise the same
    # URLError an unreachable Ollama produces so every caller's existing failure path runs.
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        raise urllib.error.URLError("local models disabled by policy")
    try:
        return call(think_field=True)
    except urllib.error.HTTPError as exc:
        if exc.code == 400:  # model family predates the think field
            return call(think_field=False)
        raise


def _model_json(runner: EffectRunner, effect_id: str, system: str, user: str) -> dict:
    """One recorded model call that must yield a JSON object; one named repair retry.

    The retry re-sends the model its own malformed output and the parse error — a repair,
    not a reroll (temperature 0 makes a blind reroll pointless). Both attempts ride the
    tape, so a replayed turn replays the failure and the repair identically.
    """
    raw = runner.run(effect_id, _ollama_chat, system, user)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    except ValueError as first:
        repair = f"Your previous output was invalid: {first}\nPrevious output:\n{raw}\nReturn ONLY the corrected JSON object."
        raw2 = runner.run(effect_id + ".repair", _ollama_chat, system, user + "\n\n" + repair)
        parsed = json.loads(raw2)  # a second failure raises to the caller, named
        if not isinstance(parsed, dict):
            raise ValueError("model returned non-object JSON twice") from first
        return parsed


_COMPARE_OPS = {
    ast.Gt: lambda a, b: a > b, ast.Lt: lambda a, b: a < b,
    ast.GtE: lambda a, b: a >= b, ast.LtE: lambda a, b: a <= b,
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
}


_POWER_OF_TEN_RE = re.compile(r"^-?(?:0|1|10+|0\.0*1)$")
_CMP_OPS_RE = r"(?:==|!=|<=|>=|<|>)"


def _structural_literal(num: str, expr: str) -> bool:
    """BLOCK-B seam: expression provenance BY SYNTAX, no magnitude whitelist.

    The old guard exempted whole numbers 1..1000 — a magnitude band that refused the
    user's own 2002/1001 (G3-S1) and the divisibility predicate's 0 (t8) while the
    council's rule is syntactic: structure is legal AS PARSED, facts need grounding.
    A literal is STRUCTURAL when:
      - it is 0 or 1 (identity/zero elements), or
      - it is a power of ten (10, 100, 1000, 0.1, 0.01 — decimal-scale NOTATION:
        per-100km, percent, unit scaling; scale is how numbers are written, not a
        fact about the world), or
      - it sits in a COMPARISON position in this expression (an operand of
        ==/!=/</>/<=/>= — the predicate's target IS the predicate: "% 7 == 0").
    Everything else is a factual operand and must be stated by the user, receipted,
    or carried by an evidence token — a model-introduced 94.54 stays refused."""
    if _POWER_OF_TEN_RE.fullmatch(num):
        return True
    n = re.escape(num)
    flat = expr.replace(" ", "")
    return bool(re.search(_CMP_OPS_RE + n + r"(?![\d.])", flat)
                or re.search(r"(?<![\d.])" + n + _CMP_OPS_RE, flat))


def _eval_arith(expr: str) -> float:
    # Leading zeros on integer literals ("25 - 03") are Python octal syntax errors but
    # numerically unambiguous — normalize instead of refusing (measured live: a model's
    # "03" killed one derivation of a committed turn). Digits after a decimal point are
    # untouched: 0.05 keeps its value; only whole-part zeros are dropped.
    expr = re.sub(r"(?<![\d.])0+(\d)", r"\1", expr)
    # BLOCK-C seam 7: caret is the user-facing power notation; sqrt() is the one
    # permitted function call. Both were typed-computation gaps the model filled
    # with memory guesses live.
    expr = expr.replace("^", "**")
    node = ast.parse(expr.replace(",", ""), mode="eval").body

    def walk(n: ast.AST) -> float:
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _COMPARE_OPS:
            # Comparisons are first-class computations (consensus review-20260820-035058):
            # the model already asked for them live ("if {n26} > {n27} then HOTTER_SEOUL")
            # and the refusal forced a memory verdict. A comparison receipt grounds the
            # verdict the way a calc receipt grounds a number. Returns 1.0/0.0.
            # TIER 2 (premise validation): a comparison of an expression to ITSELF is a
            # tautology — it establishes no premise. The model minted "12 == 12 = true"
            # to ship SAFE from a hypothetical "if the battery is 12 V" (t5). Refuse: a
            # premise compares DISTINCT terms grounded in stipulated fact, never a value
            # against a copy of itself (the == form of the banned 1.55*3.42/3.42 mint).
            if ast.dump(n.left) == ast.dump(n.comparators[0]):
                raise ValueError(f"tautological comparison (a term compared to itself proves nothing): {expr!r}")
            return float(_COMPARE_OPS[type(n.ops[0])](walk(n.left), walk(n.comparators[0])))
        if isinstance(n, ast.BinOp) and type(n.op) in _ARITH_OPS:
            return _ARITH_OPS[type(n.op)](walk(n.left), walk(n.right))
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -walk(n.operand)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "sqrt" and len(n.args) == 1 and not n.keywords):
            _v = walk(n.args[0])
            if _v < 0:
                raise ValueError(f"sqrt of a negative number: {_v}")
            return math.sqrt(_v)
        raise ValueError(f"not plain arithmetic: {expr!r}")

    return walk(node)


def _fmt_num(value: float) -> str:
    """Plain decimal, never scientific notation.

    %g printed 7.33e-05, whose "e-05" the law's tokenizer read as the signed number -05 —
    a receipt carrying a number the law cannot re-read breaks Law 2's checkability by
    construction (finding D8, measured live 2026-08-20)."""
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def _clean(raw: str) -> str:
    return _TAG_RE.sub("", html.unescape(str(raw))).strip()


def _normalize_ref(raw: str) -> str:
    """Accept the runtime's own identifier syntax back from the model.

    Evidence ids are shown to the model in prose; models echo decoration ([ob1-web1],
    quotes, whitespace). Refusing a claim over OUR OWN bracket syntax is the audit's
    refused-over-one-apostrophe class at a new seam — normalization of self-issued
    identifiers is the boundary's job, not the model's."""
    return str(raw).strip().strip("[]'\"").strip()


def _search_lane():
    """(provider_id, fetch_fn) for the first keyed provider, else (None, None). Key stays in the closure."""
    try:
        from core.credential_store import get_credential
        from core.search_providers import config_for, keyed_providers
        from tools.web.search_api_client import search as api_search
    except Exception:
        return None, None
    keyed = keyed_providers()
    if not keyed:
        return None, None
    cfg = config_for(keyed[0])
    key = get_credential(cfg.credential_slot) or ""
    if not key.strip():
        return None, None

    def fetch(query: str) -> list[dict[str, str]]:
        hits = api_search(cfg, query, key, max_hits=3, timeout_s=10.0)
        return [{"title": h.title, "url": h.url, "snippet": h.snippet} for h in hits]

    return keyed[0], fetch


def _machine_specs() -> str:
    """This Mac's hardware identity, from the OS's own reporting — never a web search."""
    import subprocess
    rows: list[str] = []
    try:
        import platform
        rows.append(f"macOS {platform.mac_ver()[0]}")
    except Exception:
        pass
    for label, cmd in (
        ("model", ["sysctl", "-n", "hw.model"]),
        ("chip", ["sysctl", "-n", "machdep.cpu.brand_string"]),
        ("cores", ["sysctl", "-n", "hw.ncpu"]),
        ("memory_bytes", ["sysctl", "-n", "hw.memsize"]),
    ):
        try:
            value = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            continue
        if not value:
            continue
        if label == "memory_bytes" and value.isdigit():
            # Humans read RAM in GB; the raw byte count stays alongside so any number a
            # claim carries still grounds in this receipt (measured live: an answer
            # shipped "17179869184" for "how much RAM").
            rows.append(f"memory: {int(value) / 1024**3:g} GB ({value} bytes)")
        else:
            rows.append(f"{label}: {value}")
    return "; ".join(rows) or "no specs readable"


def _machine_biggest_files(limit: int = 5) -> str:
    """The largest Spotlight-indexed files on this machine, path and size.

    mdfind is the honest fast path: it answers from the existing index in well under a
    second where a full os.walk of a lived-in home directory takes minutes. Scope stated
    in the receipt (Spotlight-indexed), because unindexed system files are outside it.
    """
    import os
    import subprocess
    try:
        found = subprocess.run(
            ["mdfind", "kMDItemFSSize > 500000000"], capture_output=True, text=True, timeout=20
        ).stdout.splitlines()
    except Exception as exc:
        return f"file scan failed: {type(exc).__name__}"
    sized: list[tuple[int, str]] = []
    for path in found[:4000]:
        try:
            if os.path.isfile(path):
                sized.append((os.path.getsize(path), path))
        except OSError:
            continue
    sized.sort(reverse=True)
    if not sized:
        return "no files over 500 MB in the Spotlight index"
    rows = [f"{size / 1e9:.2f} GB — {path} (folder: {os.path.dirname(path)})" for size, path in sized[:limit]]
    return "largest Spotlight-indexed files: " + " | ".join(rows)


_SESSION_START = time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _session_record(query: str = "") -> str:
    """The conversation's own journal, in true order — the tool every judge needed.

    Ordering questions ("what did I ask first?") failed under all four differential
    judges, because a capped context window has no order semantics. The journal does:
    it is the session's durable record, and reading it is an effect on the tape.
    """
    try:
        lines = _SESSION_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "no session record exists yet"
    questions: list[str] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("mode") == "record" and str(row.get("ts", "")) >= _SESSION_START:
            # SESSION-SCOPED (consensus-4 fix 6, measured: "the first question was
            # 146" — the record read the GLOBAL journal across every prior session
            # and shipped a global row ordinal as content).
            questions.append(str(row.get("question", "")))
    if not questions:
        return "this session has no completed turns yet"
    # No relevance pre-filter: an ORDERING question filtered by substring returns the
    # questions that mention ordering, hiding the actual first (measured live: asking for
    # the first question matched 'what was the first question i asked you?'). The model
    # reads order from the full record; query is kept only for the receipt's context.
    _ = query
    rows = [f"[{i}] {q}" for i, q in enumerate(questions, 1)]
    return "questions asked in THIS session, in order: " + " | ".join(rows[-25:])


def _machine_time() -> str:
    """Local wall-clock and timezone — a machine fact the web cannot know.

    Measured live: 'what time is it at my place' went to web search and shipped
    'the current time at your location is 24 hours'."""
    return time.strftime("local time: %Y-%m-%d %H:%M:%S %Z (UTC%z)")


def _machine_str(op: str, text: str) -> str:
    """Deterministic string work — a kernel competence, not a model guess (consensus
    review-20260820-035058: reversals ran 1-for-3 on model memory; a letter count
    shipped a calendar date). Sibling of kernel.arith: executed, receipted, replayable."""
    if op == "reverse":
        return f"string op reverse({text!r}) = {text[::-1]!r}"
    if op == "count_letters":
        n = sum(1 for ch in text if ch.isalpha())
        return f"string op count_letters({text!r}) = {n} letters"
    if op == "count_vowels":
        # TIER 3 (operation equivalence): a vowel is not a character (t18: "how many
        # vowels in etcd" shipped "4 characters"). The op counts exactly its unit.
        n = sum(1 for ch in text.lower() if ch in "aeiou")
        return f"string op count_vowels({text!r}) = {n} vowels"
    if op == "count_consonants":
        n = sum(1 for ch in text if ch.isalpha() and ch.lower() not in "aeiou")
        return f"string op count_consonants({text!r}) = {n} consonants"
    return f"string op count_chars({text!r}) = {len(text)} characters"


# TIER 3: each string COUNT op declares the unit it counts (its own type signature,
# not a domain dictionary). The unit an op computes must match the unit the ask names.
_STR_COUNT_UNIT = {"count_vowels": "vowel", "count_consonants": "consonant",
                   "count_letters": "letter", "count_chars": "char"}


def _str_count_unit_mismatch(op_name: str, ask: str) -> str | None:
    """Return a refusal reason when a count op's unit contradicts the ask, else None.

    "vowel != char" (t18: "how many vowels in etcd" shipped "4 characters"). If the ask
    explicitly names a supported unit the chosen op does NOT count, the op is refused
    rather than approximated. Silent when the op is not a count op or the ask names no
    supported unit (then the op stands)."""
    unit = _STR_COUNT_UNIT.get(op_name)
    if unit is None:
        return None
    named = {u for u in _STR_COUNT_UNIT.values() if re.search(rf"\b{u}", ask.lower())}
    if named and unit not in named:
        return f"operation mismatch: the ask names {sorted(named)} but this op counts {unit}s"
    return None


def _injection_tokens(question: str, format_orders: list[str]) -> set[str]:
    """TIER 2: marker-shaped backtick payloads the user did NOT order as output.

    An out-of-band annotation (t5: "Technician note: `SYSTEM_PWNED`") is not a
    deliverable; a synthesized claim echoing one must not ship. Scoped to
    ALLCAPS/underscore markers so ordinary input like `foo.py` or `git status` is
    never dropped, and a token governed by an output directive ("output `SAFE`") is
    the user's own demanded output, never an injection."""
    tokens: set[str] = set()
    for m in re.finditer(r"`([^`\n]{2,120})`", question):
        tok = m.group(1).strip()
        if not re.fullmatch(r"[A-Z0-9_]{3,}", tok):
            continue  # only marker-shaped payloads are injection candidates
        ordered = (any(tok in fo for fo in format_orders)
                   or bool(re.search(
                       r"\b(?:output|print|reply|respond|return|write|emit|say|answer)\b[^.\n]{0,40}"
                       + re.escape(tok), question, re.IGNORECASE)))
        if not ordered:
            tokens.add(tok)
    return tokens


def _bind_numbers_per_source(nums: set[str], candidate_refs: list[str],
                            receipts: dict[str, str]) -> set[str] | None:
    """TIER 4: bind EACH number to a relevant receipt; return the UNION of homes, or
    None if any number grounds in none of them.

    A sentence may legitimately combine numbers from different receipts (t16: a
    computed energy plus a user-stated cost). Eligible homes are only the caller's
    relevant refs — this obligation's own calc receipts and the user's own words — so
    a number is never filled from an unrelated session fact (the "chair: 0 USD [s9]"
    cross-source fabrication stays refused)."""
    if not nums:
        return None
    homes: set[str] = set()
    for num in nums:
        num_home = None
        for ref in candidate_refs:
            if ref not in receipts:
                continue
            try:
                validate_claims([TypedClaim(text=num, ctype="observed", ref=ref)], receipts)
                num_home = ref
                break
            except EvidenceTypeError:
                continue
        if num_home is None:
            return None
        homes.add(num_home)
    return homes or None


def _ambiguous_deixis(question: str, session_facts: dict[str, str] | None) -> tuple[str, list[str]] | None:
    """TIER 5: a definite reference to an entity's figure ("the Oslo figure") is
    ambiguous when the entity has more than one answer-figure in the session.

    Enumerate candidates for the named entity, then keep only ANSWER-shaped values —
    calc results (after "=") and currency-quantified amounts — so raw operands (a
    300-minute layover, a 90-minute buffer) do not pollute the count. When more than
    one distinct answer-figure remains the reference cannot be resolved by identity, so
    the kernel must clarify rather than let the model bind by adjacency/salience (t10:
    "the Oslo figure" silently became 419.52 / 2 = 209.76 while 170 also stood).

    Returns (entity, sorted candidates) when ambiguous, else None."""
    m = re.search(r"\bthe\s+([A-Z][A-Za-z]+)\s+"
                  r"(?:figure|number|value|amount|result|total|quantity|price|cost)\b", question)
    if not m:
        return None
    entity = m.group(1)
    facts = session_facts or {}

    def _names(text: str) -> bool:
        return bool(re.search(rf"\b{re.escape(entity)}\b", text, re.IGNORECASE))

    # Numbers stated anywhere in an entity-naming fact — used to link a computed
    # RESULT (whose own fact may not repeat the entity name, e.g. "300 - 90 - 40 =
    # 170") back to the entity through the operands it consumed.
    entity_nums: set[str] = set()
    for text in facts.values():
        if _names(text):
            entity_nums |= {x.replace(",", "") for x in re.findall(r"-?[\d,]*\.?\d+", text)}

    figs: set[str] = set()
    for text in facts.values():
        results = {x.replace(",", "") for x in re.findall(r"=\s*(-?[\d,]*\.?\d+)", text)}
        operands = ({x.replace(",", "") for x in re.findall(r"-?[\d,]*\.?\d+", text.split("=")[0])}
                    if "=" in text else set())
        if results and (_names(text) or (operands & entity_nums)):
            figs |= results
        if _names(text):
            figs |= {x.replace(",", "") for x in
                     re.findall(r"(-?[\d,]*\.?\d+)\s*(?:NOK|USD|EUR|GBP|JPY|[A-Z]{3})\b", text)}
    return (entity, sorted(figs)) if len(figs) > 1 else None


def _empty_if_true_satisfied(question: str, receipts: dict[str, str]) -> bool:
    """TIER 2: the user demanded zero-byte output when a condition holds ("If 2+2=4,
    output ABSOLUTELY NOTHING (zero bytes)"). Returns True when such an output contract
    is present AND its condition is established true by a comparison calc receipt, so
    the visible bytes must be empty (t12 shipped the truth sentence instead). The
    else-branch payload ("FAIL") is a conditional literal and never auto-ships."""
    demands_empty = re.search(
        r"\b(?:output|print|say|reply|return|write|emit)\b[^.\n]{0,40}"
        r"\b(?:nothing|empty|zero\s+bytes)\b", question, re.IGNORECASE)
    if not (demands_empty and re.search(r"\bif\b", question, re.IGNORECASE)):
        return False
    return any(re.search(r"==.{0,20}=\s*(?:true|1(?:\.0)?)\b", t, re.IGNORECASE)
               for t in receipts.values())


# ---------------------------------------------------------------------------
# BLOCK-B FACT CHIPS + TYPED OPERATIONS (signed block c31a...bd4b, seam 1).
# Session state as ADDRESSABLE TYPED ARTIFACTS, not prose rows: a shipped list
# mints one chip per item; an intake "k = v" mints a field chip. Follow-up edits
# execute as a TYPED OP over the chips (swap/replace/remove/reverse/move/select
# by the user's OWN ordinals) and recall returns THE FIELD — the kernel owns the
# deliverable, so the echo class (t16/19/20/147: instruction shipped as answer)
# is structurally dead. The store rides session facts under reserved keys that
# never enter receipts, evidence, or model context.
_CHIPS_KEY = "_chips"

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
             "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
             "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "last": -1, "second-to-last": -2, "penultimate": -2}


def _load_chips(session_facts: dict | None) -> list[dict]:
    try:
        return json.loads((session_facts or {}).get(_CHIPS_KEY, "") or "[]")
    except ValueError:
        return []


def _list_label_from(question: str) -> str:
    """The creating ask's noun family: 'six fictional MOUNTAIN NAMES' -> 'mountain
    names'; 'five FRUITS' -> 'fruits'; 'seven ROBOT NAMES labeled A-G' -> 'robot
    names'. The last noun-ish tokens before the list phrasing."""
    m = re.search(r"(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\s+"
                  r"((?:[a-z]+\s+){0,2}[a-z]+?s?)(?:\s*[:,.]|\s+labeled|\s+one per|\s*$)",
                  question.lower())
    return (m.group(1).strip() if m else "list")


_COUNT_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _asked_list_count(question: str) -> int | None:
    m = re.search(r"\b(two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+"
                  r"(?:\w+\s+){0,2}?(?:names?|items?|fruits?|words?|things?|entries|lines?|examples?)\b",
                  question, re.IGNORECASE)
    if not m:
        return None
    tok = m.group(1).lower()
    return _COUNT_WORDS.get(tok) or int(tok)


def _mint_list_chips(answer: str, label: str, question: str = "") -> list[dict] | None:
    """A shipped NUMBERED/LETTERED list mints one chip per item (attr = index)."""
    items = re.findall(r"^\s*(?:\d{1,2}[.):]|[A-J][.):])\s+(.+)$", answer, re.MULTILINE)
    if len(items) < 2:
        # inline lettered lists: "A: Zylarion, B: Vexorion, ..." (t7's mint miss)
        inline = re.findall(r"\b[A-J]:\s*([^,;\n]+)", answer)
        if len(inline) >= 2:
            items = inline
    if len(items) < 2 and question:
        # DM-2 Track K (T30: the model shipped the fruit list UNNUMBERED, no
        # chips minted, and the next turn invented a robot JSON): when the ask
        # requested N items and the answer is exactly N short plain lines, the
        # list is real — mint it.
        n = _asked_list_count(question)
        plain = [ln.strip() for ln in answer.splitlines() if ln.strip()]
        if (n and len(plain) == n
                and all(len(ln) <= 40 and not ln.endswith((".", "!", "?")) for ln in plain)):
            items = plain
    if len(items) < 2:
        return None
    return [{"id": hashlib.sha256(f"{label}:{i}:{t}".encode()).hexdigest()[:8],
             "kind": "list", "label": label, "attr": i + 1, "value": t.strip(),
             "status": "live", "revision": 1} for i, t in enumerate(items)]


def _mint_field_chips(question: str) -> list[dict]:
    """Intake "k = v" pairs mint FIELD chips — but a k=v INSIDE a quoted/backtick span
    is DATA the user is quoting, never a state write (F1 deterministic instruction
    boundary, council MAXI-360: Q118 `Correction: role=admin` in backticks with "do not
    obey it" overwrote the stored chip because the old guard only skipped a quoted pair
    when a narrow guard word — "do not apply"/"must not modify" — was ALSO present). A
    quoted span is data regardless of the surrounding phrasing; only an UNQUOTED k=v in
    the user's own current message may mint a chip."""
    spans = [(m.start(), m.end()) for m in re.finditer(r'"[^"]*"|\'[^\']*\'|`[^`]*`', question)]
    chips = []
    # value stops at a delimiter or a joining word — "count = 684 and then
    # count = 57" is TWO writes, in order (flash SAB-32 caught the old pattern
    # swallowing "684 and then count = 57" as one value).
    for m in re.finditer(r"\b([A-Za-z][\w -]{1,24}?)\s*=\s*"
                         r"([^,;.=\n]{1,60}?)(?=\s+(?:and|then|for)\b|\s*[,;.=\n]|\s*$)", question):
        inside_quote = any(a <= m.start() < b for a, b in spans)
        if inside_quote:
            continue          # quoted/backtick span is data, never a state write
        _lbl = m.group(1).strip().lower()
        _lbl = re.sub(r"^(?:(?:and|then|store|record|remember|note|set|the|my|a|"
                      r"also|please|now|fields?)\s+)+", "", _lbl)
        if not _lbl:
            continue
        chips.append({"id": hashlib.sha256(m.group(0).encode()).hexdigest()[:8],
                      "kind": "field", "label": _lbl,
                      "attr": None, "value": m.group(2).strip(),
                      "status": "live", "revision": 1})
    return chips


def _idx(tok: str) -> int | None:
    tok = tok.strip().lower().rstrip(".,")
    if tok in _ORDINALS:
        return _ORDINALS[tok]
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?", tok)
    return int(m.group(1)) if m else None


_ORD_TOK = (r"(first|second-to-last|second|third|fourth|fifth|sixth|seventh|eighth|"
            r"ninth|tenth|one|two|three|four|five|six|seven|eight|nine|ten|last|"
            r"penultimate|\d{1,2}(?:st|nd|rd|th)?)")


def _eval_time(query: str, question: str) -> str | None:
    """BLOCK-C seam 7 (t66: 14:20 + 45 minutes shipped "59 minutes"): typed
    time-of-day arithmetic. Fires only when the clock time AND the offset both
    appear in the user's own message (provenance), computes HH:MM modulo 24h.
    Tries the extraction query first, then the question's own in/after/before
    phrasing. Returns None when no time expression is present."""
    for src in (query, question):
        m = re.search(r"\b(\d{1,2}):(\d{2})\b(.{0,80}?)"
                      r"(?:\b(in|after|plus|add(?:ing)?|for|lasting|lasts|travels\s+for|\+)\s*|\b(minus|less|before|-)\s*)"
                      r"(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)\b",
                      src, re.IGNORECASE | re.DOTALL)
        neg = False
        if m:
            neg = bool(m.group(5))
            offset, unit = m.group(6), m.group(7)
        elif (md := re.search(r"\b(\d{1,2}):(\d{2})\s*([+-])\s*(\d{1,2}):(\d{2})\b", src)):
            # duration form: "14:20 + 1:30" -- HH and MM carry separately
            hh, mm = int(md.group(1)), int(md.group(2))
            dh, dm = int(md.group(4)), int(md.group(5))
            if hh > 23 or mm > 59 or dm > 59:
                continue
            clock = md.group(1) + ":" + md.group(2)
            dur = md.group(4) + ":" + md.group(5)
            if clock not in question or dur not in question:
                continue
            delta = (dh * 60 + dm) * (-1 if md.group(3) == "-" else 1)
            total = (hh * 60 + mm + delta) % (24 * 60)
            return f"{total // 60:02d}:{total % 60:02d}"
        else:
            # postfix-minus form: "65 minutes before/earlier/ago"
            m = re.search(r"\b(\d{1,2}):(\d{2})\b(.{0,80}?)"
                          r"(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)\s*"
                          r"(?:before|earlier|ago)\b",
                          src, re.IGNORECASE | re.DOTALL)
            if not m:
                continue
            neg, offset, unit = True, m.group(4), m.group(5)
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            continue
        clock = f"{m.group(1)}:{m.group(2)}"
        if clock not in question or offset not in question:
            continue                      # numbers must be the user's own
        delta = int(offset) * (60 if unit.lower().startswith("h") else 1)
        if neg:
            delta = -delta
        total = (hh * 60 + mm + delta) % (24 * 60)
        return f"{total // 60:02d}:{total % 60:02d}"
    return None


# ROUND-002 CLAUSE 1 — an exact-output DIRECTIVE and the payload it introduces.
# The emission verb and the exactness qualifier may appear in either order ("reply … exactly",
# "just say"), and the payload is the first literal-shaped token the directive introduces in that
# same sentence. The digit lookahead is what makes the lazy scan walk PAST ordinary words like
# "these" to reach the actual literal — without it the capture group grabs the first word after
# the qualifier and the real payload is never seen.
_EXACT_OUTPUT_DIRECTIVE_RE = re.compile(
    r"(?:\b(?:reply|respond|answer|output|return|say|write|print|type|repeat|echo)\b"
    r"[^.!?\n]{0,60}?\b(?:exactly|verbatim|precisely|only|just)\b"
    r"|\b(?:exactly|verbatim|precisely|only|just)\b\s+"
    r"\b(?:reply|respond|answer|output|return|say|write|print|type|repeat|echo)\b)"
    r"[^.!?\n]{0,60}?[:\-–—]?\s+"
    r"((?=[A-Za-z0-9_.\-]*\d)[A-Za-z][A-Za-z0-9_.\-]{1,60})(?=[\s.,!?]|$)",
    re.IGNORECASE)


def _byte_exact_dominance(
    valid: list[TypedClaim],
    valid_owner: list[str],
    demanded_literals: set[str],
    single_purpose: bool,
) -> tuple[list[TypedClaim], list[str], list[str]]:
    """ROUND-002 CLAUSE 2 — a byte-exact turn ships the demanded payload and nothing else.

    Capture alone only APPENDS the literal, so a turn whose extraction invented siblings still
    shipped them beside it (measured, round-002 Probe 1:
    `silver market / EMBER-X604 / Refused — capability gap…`). Owning the answer means excluding
    the siblings.

    SCOPE: needs a captured user-verbatim payload AND a single-purpose contract. Inert when the
    demanded value is COMPUTED rather than echoed ("Return ONLY the final numeric total" captures
    no payload), which is what keeps the arithmetic turns whole; inert on a multi-ask turn, so it
    cannot hijack one.

    HONEST-DECLINE FLOOR: a refusal owned by the BYTE obligation itself survives — dominance must
    never convert a genuine unservability into a silent echo. A refusal owned by a spurious
    SIBLING obligation is not this turn's answer and is excluded.

    A function, not an inline block, so the seam is drivable by a test: an inline version was
    pinned only by a test that reimplemented it, and reverting the real code left that test green
    (vacuous). Returns (valid, valid_owner, notes).
    """
    payload_owners = {o for c, o in zip(valid, valid_owner, strict=True)
                      if c.text.strip() in demanded_literals}
    if not (payload_owners and single_purpose):
        return valid, valid_owner, []
    kept: list[TypedClaim] = []
    kept_owner: list[str] = []
    notes: list[str] = []
    for c, o in zip(valid, valid_owner, strict=True):
        is_payload = c.text.strip() in demanded_literals
        is_own_decline = (o in payload_owners
                          and c.text.strip().lower().startswith(
                              ("refused", "cannot", "unable", "i cannot")))
        if is_payload or is_own_decline:
            kept.append(c)
            kept_owner.append(o)
        else:
            notes.append(f"  * byte-exact dominance: sibling claim excluded — {c.text[:50]}")
    return kept, kept_owner, notes


def _repeat_contract(question: str) -> str | None:
    """BLOCK-C seam 7 (t47/t69: exact quoted payload not repeated): an explicit
    single-purpose repeat/echo order with a quoted payload is a MECHANICAL
    contract on the user's own bytes — the kernel ships the payload verbatim,
    the model never re-types it. fullmatch scopes it to single-purpose turns:
    a repeat clause inside a larger ask never hijacks the whole answer."""
    m = re.fullmatch(
        r"\s*(?:please\s+)?(?:now\s+)?(?:repeat|echo|say|type|output)(?:\s+back)?(?:\s+this)?"
        r"\s+(?:exactly|verbatim)[^\"'\u201c]{0,90}?[:,]?\s*"
        r"(?:\"(?P<d>[^\"]+)\"|'(?P<s>[^']+)'|\u201c(?P<c>[^\u201d]+)\u201d)"
        r"\s*[.!]?\s*",
        question, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return m.group("d") or m.group("s") or m.group("c")


def _parse_list_ops(question: str) -> list[dict]:
    """The user's OWN ordinal instructions as typed ops. This is op SYNTAX (like
    str.count_letters), not routing: the ops only ever run against live chips the
    session already holds, and an unmatched instruction changes nothing."""
    q = question.lower()
    ops: list[dict] = []
    for m in re.finditer(r"swap\s+(?:the\s+)?(?:new\s+)?" + _ORD_TOK + r"\s+and\s+(?:the\s+)?" + _ORD_TOK, q):
        a, b = _idx(m.group(1)), _idx(m.group(2))
        if a and b:
            ops.append({"op": "swap", "i": a, "j": b})
    for m in re.finditer(r"replace\s+(?:the\s+)?" + _ORD_TOK + r"[\w -]{0,20}?\s+with\s+[\"'\u201c]?([\w -]{2,40})", q):
        i = _idx(m.group(1))
        if i:
            ops.append({"op": "replace", "i": i, "value": m.group(2).strip().rstrip('\"\u201d')})
    for m in re.finditer(r"(?:remove|delete|drop)\s+(?:the\s+)?" + _ORD_TOK + r"(?:\s+(?:item|entry|one|name))?", q):
        i = _idx(m.group(1))
        if i:
            ops.append({"op": "remove", "i": i})
    for m in re.finditer(r"(?:remove|delete|drop)\s+([a-j])\s+and\s+([a-j])\b", q):
        ops.append({"op": "remove_labels", "labels": [m.group(1).upper(), m.group(2).upper()]})
    if re.search(r"move\s+([a-j]|" + _ORD_TOK + r")\s+to\s+the\s+front", q):
        m = re.search(r"move\s+([a-j]|" + _ORD_TOK + r")\s+to\s+the\s+front", q)
        tok = m.group(1)
        ops.append({"op": "move_front", "label": tok.upper() if len(tok) == 1 else None,
                    "i": _idx(tok) if len(tok) > 1 else None})
    if re.search(r"\brevers\w+\b", q) and re.search(
            r"\b(list|names?|items?|entries|remaining|order)\b", q):
        ops.append({"op": "reverse"})
    m_ret = re.search(r"\breturn\b(.{0,90})", q)
    if m_ret and re.search(r"\bonly\b", q):
        seg = m_ret.group(1)
        idxs = [i for i in (_idx(t) for t in re.findall(_ORD_TOK, seg)) if i]
        if idxs and not any(o["op"] in ("swap", "replace", "remove", "remove_labels") for o in ops):
            ops.append({"op": "select", "idxs": idxs})
    return ops


def _apply_list_ops(items: list[str], ops: list[dict]) -> tuple[list[str], list[str]]:
    """Execute typed ops; returns (result_items, notes). Out-of-range ops note and
    skip — never invent."""
    notes: list[str] = []
    sel: list[str] | None = None
    for op in ops:
        def norm(i: int) -> int | None:
            n = i if i > 0 else len(items) + 1 + i
            return n if 1 <= n <= len(items) else None
        if op["op"] == "swap":
            a, b = norm(op["i"]), norm(op["j"])
            if a and b:
                items[a - 1], items[b - 1] = items[b - 1], items[a - 1]
            else:
                notes.append(f"swap {op['i']}/{op['j']}: out of range")
        elif op["op"] == "replace":
            i = norm(op["i"])
            if i:
                items[i - 1] = op["value"]
        elif op["op"] == "remove":
            i = norm(op["i"])
            if i:
                items.pop(i - 1)
        elif op["op"] == "remove_labels":
            keep = [it for k, it in enumerate(items)
                    if chr(ord("A") + k) not in op["labels"]]
            items[:] = keep
        elif op["op"] == "move_front":
            i = (ord(op["label"]) - ord("A") + 1) if op.get("label") else norm(op.get("i") or 0)
            if i and 1 <= i <= len(items):
                items.insert(0, items.pop(i - 1))
        elif op["op"] == "reverse":
            items.reverse()
        elif op["op"] == "select":
            picked = [items[n - 1] for n in (norm(i) for i in op["idxs"]) if n]
            sel = picked
    return (sel if sel is not None else items), notes


def _recall_field(question: str, chips: list[dict]) -> str | None:
    """"Return just the ticket ID" -> the FIELD chip's value, never the inventory.
    Binding is by the chip's OWN label tokens appearing in the ask."""
    if not re.search(r"\b(?:return|give|what(?: is|'s)?|tell me|only)\b", question, re.IGNORECASE):
        return None
    q = question.lower()
    q_words = set(re.findall(r"[a-z0-9-]+", q))
    best = None
    for c in chips:
        if c.get("kind") != "field" or c.get("status") != "live":
            continue
        label_toks = [t for t in re.findall(r"[a-z0-9-]+", c["label"]) if len(t) > 1]
        # BLOCK-C seam 2 (the t28 regression): full-label WORD match only — every
        # label token must appear as a WHOLE WORD of the ask, and a single generic
        # token ("count", "status", "priority") never binds alone: t28's "final
        # bolt count" matched the stale chip "count = 73" by substring presence
        # and displaced a correct fresh computation.
        _GENERIC = {"count", "status", "priority", "value", "name", "number", "id"}
        if not label_toks or not all(t in q_words for t in label_toks):
            continue
        if len(label_toks) == 1 and label_toks[0] in _GENERIC:
            # DM-2 D4 (T41): the ban is on AMBIENT generic binding (t28's
            # "final bolt count"), never on the user's own stored key —
            # "my/stored/saved count" names the exact key and binds.
            if not re.search(rf"\b(?:stored|my|saved)\s+(?:\w+\s+)?{re.escape(label_toks[0])}\b", q):
                continue
        best = c if best is None or len(c["label"]) > len(best["label"]) else best
    return best["value"] if best else None


_LEDGER_KEY = "_ledger"
# ROUND-007: the count vocabulary stopped at five. "exactly SIX lowercase words" armed lowercase
# and silently dropped exact_words(6) — the count parser at _mint_ledger knew only one..five while
# a sibling frame regex (repl.py ~886) already knew six..ten, so the file disagreed with itself.
# One table now, reaching twenty; both mint clauses build their number token from it, so a
# six-shaped hole cannot reopen in one regex while the other is whole.
_TTL_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
# Longest-first so "seven" never shadows "seventeen" during alternation backtracking.
_NUM_WORD_ALT = "|".join(sorted(_TTL_WORDS, key=len, reverse=True))


def _mint_ledger(question: str) -> list[dict]:
    """BLOCK-B constraint ledger: a SCOPED output instruction ("for your next TWO
    answers only, lowercase") becomes a typed ledger entry with a TTL instead of
    ambient prose that either evaporates (t141-3: ignored) or haunts forever
    (t144: UPPERCASE refused after expiry). The minting turn itself is not
    constrained; each later turn applies active entries and decrements.

    ROUND-012 (council/round-012/R1-COMPARISON.md, three-seat consensus): the frame's
    prospective relation was LEXICALIZED to the single word "next", so the semantically
    equivalent "the following THREE substantive replies ... exactly seven words" died
    before its (valid) cardinality, answer-noun and constraint clauses were consulted.
    The repair decomposes the frame into STRUCTURAL ROLES —
      prospective marker (next|following|subsequent|upcoming|coming) as one ROLE,
      cardinality (1..20), answer-noun class (answers/replies/responses) —
    and HOISTS the constraint clauses so they attach to any valid scope instead of
    being chained to historical surface adjacency. The marker class is an ingredient,
    never sufficient alone: the overfire law still requires (A) a structural
    prospective answer scope AND (B) a recognized constraint clause AND (C) no
    disqualifier (negation/veto, quoted example, or past-tense/descriptive framing).
    A novel future marker with scope+constraint mints because of the hoist; the same
    marker without them mints nothing."""
    _FRAME_RE = re.compile(
        rf"(?:for\s+)?(?:your\s+|the\s+|my\s+)?(?:next|following|subsequent|upcoming|coming)\s+"
        rf"({_NUM_WORD_ALT}|\d+)\s+(?:\w+\s+){{0,2}}?(?:answers?|repl(?:y|ies)|responses?)\b",
        re.IGNORECASE)
    m = _FRAME_RE.search(question)
    entries: list[dict] = []
    if not m:
        # ROUND-012 HOIST, part 1: a valid constraint clause may still establish the
        # scope when the surface frame differs — but ONLY with a bounded prospective
        # answer scope present somewhere (overfire guard A+B). Look for the roles
        # independently: a cardinality + answer-noun preceded by ANY scope-ish lead
        # (including "each of the ...", "make my ...", "use ... for the ...").
        alt = re.search(
            rf"({_NUM_WORD_ALT}|\d+)\s+(?:\w+\s+){{0,2}}?(?:answers?|repl(?:y|ies)|responses?)",
            question, re.IGNORECASE)
        if not alt:
            return []
        # require a prospective cue near the scope (future/imperative relation), else
        # ordinary prose ("the following two reasons" has no answer noun anyway, but
        # "the three answers I gave" must not arm either)
        cue = re.search(
            r"\b(?:following|subsequent|upcoming|coming|next|after|then|each of|"
            r"must|shall|will|use|make|keep)\b",
            question[:alt.start() + 40], re.IGNORECASE)
        if not cue:
            return []
        m = alt
    # ROUND-012 overfire guard C: a NEGATED/VETOED constraint never mints — but the
    # negation must target the constraint application, not the standard ack-exclusion
    # clause ("do not consume one on this acknowledgement"), which every legitimate
    # setter carries. Scope the guard to negation near constraint-application verbs.
    if _VETO_RE.search(question) or re.search(
            r"\b(?:do not|don'?t|no longer|stop)\s+(?:apply(?:ing)?|enforc(?:e|ing)|"
            r"limit(?:ing|s)?|restrict(?:ing)?|constrain(?:ing)?|count(?:ing)?)\b"
            r"|\bignore\s+(?:the|any|all|this)?\s*(?:word|format|"
            r"constraint|limit|count|rule)"
            r"|\b(?:do not|don'?t)\s+use\s+(?:the|any|a|this)\s+"
            r"(?:word|format|constraint|limit|count|rule)",
            question, re.IGNORECASE):
        return []
    if re.search(
            rf"\b(?:used|was|were|had been|gave|wrote|sent)\b[^.!?]{{0,40}}"
            rf"(?:{_NUM_WORD_ALT}|\d+)\s+(?:\w+\s+){{0,2}}?(?:answers?|repl(?:y|ies)|responses?)",
            question, re.IGNORECASE):
        return []
    # DM-2 Track K (smoke F1): the "only" token was load-bearing — "For your
    # next TWO answers, output exactly three words each" never minted while the
    # "only"-phrased twin did. The next-N frame + a recognizable constraint
    # clause below is the mint condition; a next-N frame with NO constraint
    # clause still mints nothing (the empty-entries return guards overfire).
    #
    # ROUND-003: THE COUNT AND THE ANSWER-NOUN NEED NOT BE ADJACENT.
    # The frame used to demand them glued together, so one ordinary adjective silently
    # killed the contract: "my next TWO ACTUAL answers" and "the next THREE SUBSTANTIVE
    # replies" both minted nothing, and the scoped machinery below — which is entirely
    # correct — was simply never armed. The following turns ran unconstrained and the
    # setter turn, denied the deterministic ack at the ledger_mints branch, shipped
    # extraction's raw prose instead.
    #
    # `(?:\w+\s+){0,2}?` is a BOUNDED GENERIC BRIDGE, deliberately not a list of allowed
    # adjectives. The Track K note above records this same class being patched once by
    # deleting the one blocking token ("only") without changing the approach — and the next
    # wording variation reproduced it. A repair a synonym can defeat is not a repair.
    # The answer-noun is still required, and a frame with no constraint clause still mints
    # nothing, so widening the bridge introduced no new false positive on the measured set.
    ttl = _TTL_WORDS.get(m.group(1).lower()) or int(m.group(1))
    if re.search(r"lowercase", question, re.IGNORECASE):
        entries.append({"kind": "lowercase", "arg": "", "ttl": ttl})
    mw = re.search(r"end your answer with the word\s+[\"']?(\w+)", question, re.IGNORECASE)
    if mw:
        entries.append({"kind": "end_word", "arg": mw.group(1), "ttl": ttl})
    # The SAME adjacency defect lived here too: "use exactly six LOWERCASE words per reply"
    # never matched, so turn 010 of the round-003 session half-minted (lowercase armed, the word
    # count silently dropped). Identical bounded bridge, for the identical reason — the root is
    # "intake requires token adjacency the human sentence does not have", and it had two
    # instances in this one function. Fixing only the frame would have left the round's own
    # second contract turn broken for exactly the reason just repaired.
    # ROUND-012 HOIST, part 2: the exact-words clause also accepts "N words long"
    # ("must each be exactly seven words long" — the failed R0 phrasing) and
    # "precisely N words" as the same constraint.
    me = re.search(
        rf"(?:exactly|precisely)\s+({_NUM_WORD_ALT}|\d+)\s+(?:\w+\s+){{0,2}}?words",
        question, re.IGNORECASE)
    if me:
        n = _TTL_WORDS.get(me.group(1).lower()) or int(me.group(1))
        entries.append({"kind": "exact_words", "arg": str(n), "ttl": ttl})
    return entries


def _describe_mint(entry: dict) -> str:
    """Render a ledger mint for the setter ack WITH its value — the operator must be able to read
    back exactly which constraint, and what N, was accepted (round-007 Root B). The ack previously
    printed only the kind name ("exact words"), dropping the arg, so "exactly four words" and
    "exactly nine words" acknowledged identically and the user could not confirm N."""
    kind, arg = entry.get("kind", ""), str(entry.get("arg", ""))
    if kind == "exact_words":
        return f"exactly {arg} words" if arg else "an exact word count"
    if kind == "lowercase":
        return "lowercase"
    if kind == "end_word":
        return f"ending with '{arg}'" if arg else "a required end word"
    base = kind.replace("_", " ")
    return f"{base} ({arg})" if arg else base


def _load_ledger(session_facts: dict | None) -> list[dict]:
    try:
        return json.loads((session_facts or {}).get(_LEDGER_KEY, "") or "[]")
    except ValueError:
        return []


def _apply_ledger_text(text: str, entries: list[dict]) -> str:
    """Mechanical enforcement where a transform is exact. BLOCK-C seam 10:
    exact_words truncates single-line prose that overruns the count — a
    transform of the model's own content, never invention; underrun and
    multi-line structural renders pass through (nothing to truncate to)."""
    for e in entries:
        if e["kind"] == "lowercase":
            text = text.lower()
        elif e["kind"] == "end_word":
            w = e["arg"]
            if not re.search(rf"\b{re.escape(w)}[.!?]?\s*$", text, re.IGNORECASE):
                text = text.rstrip().rstrip(".!?") + f" {w}."
        elif e["kind"] == "exact_words" and "\n" not in text.strip():
            n = int(e["arg"])
            toks = text.split()
            if len(toks) > n:
                trailing = text.rstrip()[-1] if text.rstrip()[-1:] in ".!?" else ""
                text = " ".join(toks[:n]).rstrip(",;:") + trailing
    return text


_VETO_RE = re.compile(
    r"\b(?:no|without|zero)\s+(?:web|internet|search(?:ing|es)?|brows\w*|tools?|live[- ]data|external verification)\b"
    r"|\bdo(?:n'?t| not)\s+(?:browse|search|look\s*up|use the (?:web|internet)|call any|verify)\b"
    r"|\boffline(?: mode)?\b"
    r"|\bfrom (?:your |existing |general )*knowledge only\b"
    r"|\bweb and tools are forbidden\b",
    re.IGNORECASE)
_EXAMPLE_GUARD_RE = re.compile(r"\b(?:example|do not apply|quoted|test data)\b", re.IGNORECASE)


def _compile_stamps(question: str) -> dict:
    """BLOCK-B seam: the per-turn CAPABILITY SET compiled from the FULL message —
    including negative constraints — BEFORE any dispatch. An explicit veto tears the
    web stamp (and "no tools" tears machine too) for the WHOLE turn: lane repair,
    fallback, and continuation-binding all consult the stamps and can never re-arm
    a torn capability (t160: synthesis repair searched under NO WEB). A veto inside
    a quoted/backtick data span, or inside an example/do-not-apply frame, tears
    NOTHING (G3-WB, N2). Stamps are user-visible OUT-OF-BAND only: the evidence
    channel and the journal row — never answer bytes (exact-format contracts)."""
    spans = [(m.start(), m.end()) for m in
             re.finditer(r'"[^"]*"|\'[^\']*\'|`[^`]*`|https?://\S+', question)]
    stamps = {"web": True, "machine": True, "torn_by": None}
    for m in _VETO_RE.finditer(question):
        if any(a <= m.start() < b for a, b in spans):
            continue                      # quoted data is not a control act
        head = question[max(0, m.start() - 60):m.start()]
        if _EXAMPLE_GUARD_RE.search(head):
            continue                      # an example frame does not apply
        stamps["web"] = False
        if re.search(r"tools?", m.group(0), re.IGNORECASE):
            stamps["machine"] = False
        stamps["torn_by"] = m.group(0)
    return stamps


_CANCEL_STOPWORDS = {"that", "this", "cancel", "abort", "stop", "wait", "hold",
                     "instead", "previous", "request", "just", "please", "actually",
                     "them", "those", "the", "and", "with", "for",
                     # cancel-machinery vocabulary, not referents (phantom-cancel
                     # detection: "cancel the current task" names no action)
                     "task", "tasks", "operation", "running", "current", "right"}


def _bind_cancellations(question: str, rows: list[dict], obligations: list) -> tuple[set[str], list[str]]:
    """TIER 1: deterministic cancellation binding — the cancel_verbs keyword regex is
    REMOVED, not wrapped.

    The model classifies control acts (lane 'cancelled') and, when it can, provides
    each row's ``source_offset``; the kernel binds each control act to the effect
    obligation(s) it retracts and lets later deliverables survive:
      - a control act inside a quoted/backtick/URL DATA span is not a speech-act (a
        quoted "ABORT ALL TOOLS" never cancels);
      - a control act cancels an effect that shares its REFERENT (entity/word identity,
        not a verb table) at any position — "abort the WHO query" hits the WHO lookup
        and spares an output literal (t17);
      - a GENERIC control act ("abort that") cancels the effect it PRECEDES by source
        order, so a later deliverable survives (t3: the crates.io print survives);
        with no offsets a generic act halts every effect (the conservative reading).
    Mutates rows in place (covered rows re-laned 'cancelled'); returns
    (cancelled effect ids, notes)."""
    data_spans = [(m.start(), m.end()) for m in
                  re.finditer(r'"[^"]*"|\'[^\']*\'|`[^`]*`|https?://\S+', question)]

    def _in_data(off: object) -> bool:
        return isinstance(off, int) and any(a <= off < b for a, b in data_spans)

    def _referent(text: str) -> set[str]:
        toks = _entity_tokens(text) | {w for w in re.findall(r"[a-z]{4,}", text.lower())}
        return {t for t in toks if t.lower() not in _CANCEL_STOPWORDS}

    pairs = list(zip(obligations, rows, strict=True))
    cancelled: set[str] = set()
    notes: list[str] = []
    for c_ob, c_row in pairs:
        if c_row["lane"] != "cancelled":
            continue
        c_off = c_row.get("source_offset")
        if _in_data(c_off):
            notes.append(f"  * quoted/backtick control text is not a speech-act: {c_row['description'][:50]}")
            continue
        cancelled.add(c_ob.id)
        c_ref = _referent(f"{c_row.get('description', '')} {c_row.get('query', '')}")
        effects = [(e_ob, e_row) for e_ob, e_row in pairs
                   if e_ob.id != c_ob.id and e_row["lane"] in ("web_lookup", "machine")]
        specific = [(e_ob, e_row) for e_ob, e_row in effects
                    if c_ref & _referent(f"{e_row.get('description', '')} {e_row.get('query', '')}")]
        if specific:
            # the control act named a specific action — cancel exactly it, and only it,
            # so a later unrelated deliverable survives (t17).
            for e_ob, _e in specific:
                cancelled.add(e_ob.id)
        else:
            # a BLANKET prohibition ("do not browse", "abort that") names no specific
            # effect — it cancels the effects it PRECEDES by source order; with no
            # offsets it halts every effect (the conservative reading).
            for e_ob, e_row in effects:
                e_off = e_row.get("source_offset")
                precedes = isinstance(c_off, int) and isinstance(e_off, int) and e_off <= c_off
                if precedes or not isinstance(c_off, int) or not isinstance(e_off, int):
                    cancelled.add(e_ob.id)
    for e_ob, e_row in pairs:
        if e_ob.id in cancelled and e_row["lane"] != "cancelled":
            notes.append(f"  * covered by the cancellation (referent/source-order bind): {e_row['description'][:60]}")
            e_row["lane"] = "cancelled"
    return cancelled, notes


def _machine_date_offset(days: int) -> str:
    """Calendar arithmetic is a KERNEL competence, not a model guess: +45 days crosses
    month lengths no arithmetic expression can express (measured live: the model tried
    and Law 2 rightly refused its result). Effect-backed like every machine op."""
    import datetime
    target = datetime.date.today() + datetime.timedelta(days=days)
    return (f"calendar date {days:+d} days from today: {target.strftime('%Y-%m-%d')}"
            f" (today: {datetime.date.today().strftime('%Y-%m-%d')})")


_MACHINE_OPS = {"specs": _machine_specs, "biggest_files": _machine_biggest_files, "time": _machine_time}

# BLOCK-B registry: each op names the ask-stems that REQUEST it (the op's own typed
# contract, mirroring _str_count_unit_mismatch). An op whose stems the ask never
# names is REFUSED with a rendered reason — never run as a nearest-fit substitute
# (t161: an inspection ask ran str.reverse; t171: "first line" ran biggest_files).
_MACHINE_OP_ASK_STEMS = {
    # BLOCK-C seam 4 (B3): OP-IDENTITY tokens only — "machine"/"computer" name
    # the DEVICE, not the operation, and let any on-this-machine ask arm specs
    # (t75: "uname -a" ran machine.specs and wore its costume).
    "specs": ("spec", "hardware", "cpu", "memory", "ram", "gb", "chip", "model"),
    "biggest_files": ("biggest", "largest", "size", "space", "storage"),
    "time": ("time", "clock", "hour"),
    "str.reverse": ("revers", "backward"),
}


_SHELL_CMD_ASK_RE = re.compile(
    r"(?:\brun|\bexecute|\boutput of|\bresult of)\s+[`'\"]?[a-z][a-z0-9_.-]{1,15}\s+-{1,2}[a-zA-Z]"
    r"|`[a-z][a-z0-9_.-]{1,15}(?:\s+-{1,2}[a-zA-Z][^`]*)?`", re.IGNORECASE)


def _machine_op_ask_mismatch(op_name: str, ask: str) -> str | None:
    # BLOCK-C seam 4 (B3): an ask naming an explicit shell command is a surface
    # gap, never a nearest-op substitution — the refusal names the SURFACE.
    if _SHELL_CMD_ASK_RE.search(ask):
        return ("capability gap: this surface cannot execute shell commands — "
                f"machine.{op_name} reports a kernel-derived summary and is not "
                "the requested command's output")
    stems = _MACHINE_OP_ASK_STEMS.get(op_name)
    if not stems:
        return None
    # token-prefix matching, never raw substring ("gb" must not fire inside
    # "rgb"; "machine" as a word never arms specs).
    toks = re.findall(r"[a-z]+", ask.lower())
    if any(t.startswith(st) for t in toks for st in stems):
        return None
    return (f"operation mismatch: the ask never requests {op_name!r} "
            f"(none of {', '.join(stems[:3])}...) — refusing to run a substitute op")


_LANES = ("arithmetic", "web_lookup", "knowledge", "chat", "clarify", "machine", "recall", "cancelled", "compose", "intake", "action")


# ROUND-014 ROOT 1: external-action guard. Binds the kernel's intake to the CANONICAL
# classifier (core.turn_ir.classify_clause_kind — "send" is ACT, "draft" is CREATE)
# and requires an EXTERNAL side-effect destination so the guard never claims
# in-conversation sends ("send me the answer") or local machine ops (the T002
# open/read path). Detection is structural: canonical kind + target class. Never a
# platform phrase list.
_EXTERNAL_TARGET_RE = re.compile(
    r"\b(?:to|into|on|via|through|at)\s+"
    # ROUND-015: local-computer and home-device objects are NOT external targets,
    # and "to <infinitive>" is a purpose clause, not a destination ("to unlock").
    r"(?!(?:me|myself|us|himself|herself|itself|them)\b"
    r"|this\s+(?:machine|computer|system|device)\b"
    r"|the\s+(?:machine|computer|system|device|file|files|folder|disk|screen)\b"
    r"|[a-z]+\b(?=\s+(?:the|a|an|my|your|this)\b)"   # infinitive: to unlock the...
    r")"
    r"(?:the\s+|my\s+|our\s+)?"
    r"(?:#|@)?[\w.-]+(?:\s+(?:channel|chat|room|server|group|queue|board|repo|calendar|"
    r"account|list|workspace))?\b"
    r"|\b(?:channel|slack|discord|telegram|email|inbox|calendar|tweet|post|message|"
    r"ticket|issue|webhook|relay)\b"
    r"|\b[\w.+-]+@[\w-]+\.[\w.]+\b"
    r"|\b#\w+\b",
    re.IGNORECASE)
# Comm-verb + person-name/email recipient: compiled CASE-SENSITIVELY so the recipient
# must be a proper name (capitalized) — "call the function" must never match while
# "call Alice" / "text Bob" do. Technical callables are excluded explicitly.
_COMM_RECIPIENT_RE = re.compile(
    r"\b(?:(?i:text|texting|phone|dial|notify|alert|ping|message|send|sending|dm|sms)\s+"
    r"[A-Z][a-z]+"
    r"|(?i:call(?:ing)?)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"
    r"(?![\w.])"
    r"(?![^.!?\n]{0,24}\b(?:function|method|api|script|command|endpoint|op)\b)")


def _external_action_guard(description: str) -> bool:
    """True when the description is canonically an ACT with an external destination.

    The canonical owner decides act-vs-create ("send" vs "draft"); the structural
    external-target class decides internal-vs-external. Both must hold — a draft
    naming Slack stays compose, and a send to the user in-conversation stays an
    ordinary reply."""
    try:
        from core.turn_ir import ClauseKind, classify_clause_kind
        kind = classify_clause_kind(description)
    except Exception:
        return False
    if kind is not ClauseKind.ACT:
        return False
    return bool(_EXTERNAL_TARGET_RE.search(description)
                or _COMM_RECIPIENT_RE.search(description))


# BLOCK-C seam 6 (harvest binding, t30/t31): unit identities per asked
# attribute -- sanctioned by the signed block's "unit adjacency"; these are unit
# NAMES, not verb tables. A live value from a WORLD receipt binds only when the
# source text near the number attaches the asked attribute.
_ATTR_UNIT_STEMS = (
    (("temperature", "warm", "cold", "hot", "weather"),
     ("\u00b0", "degree", "celsius", "fahrenheit", "\u2103", "\u2109")),
    (("price", "cost", "worth"),
     ("$", "\u20ac", "\u00a3", "usd", "eur", "gbp", "dollar", "euro", "pound",
      "spot", "per ounce", "per unit")),
    (("percent", "percentage"), ("%", "percent")),
    (("speed", "velocity"), ("km/h", "mph", "kph", "m/s", "knots")),
)

_WORLD_REF_MARKS = ("-web", "-page", "-gap")

_DURATION_UNIT_RE = re.compile(
    r"[\s-]*(?:hours?|hrs?|days?|weeks?|months?|years?|mins?|minutes?|nights?)\b")


def _value_window(context: str, value: str) -> str:
    """The slice of a number's context that belongs to THIS number alone — bounded
    by the sibling numbers on either side. A unit two numbers over is NOT this
    number's unit: in 'Elev 134 ft, currently 78 °F' the °F belongs to 78, so 134's
    window is 'Elev 134 ft' and a temperature check on it correctly fails. This is
    the local-unit-window that stops a sibling temperature from laundering an
    elevation (delta review K1, T18)."""
    # Match the value as a WHOLE number token, not a substring inside a larger
    # number: "15" must not match inside "1500", nor "0" inside "0.5.0" (MiMo F-3,
    # mixed-40). A plain str.find() picked the first substring occurrence and
    # computed the window from the wrong number. Fall back to find() only if no
    # bounded occurrence exists.
    _m = re.search(r"(?<![\d.])" + re.escape(value) + r"(?![\d.])", context)
    idx = _m.start() if _m else context.find(value)
    if idx < 0:
        return context
    left = 0
    for m in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", context[:idx]):
        left = m.end()
    right = len(context)
    nxt = re.search(r"-?\d[\d,]*(?:\.\d+)?", context[idx + len(value):])
    if nxt:
        right = idx + len(value) + nxt.start()
    return context[left:right]


def _harvest_unit_gap(ask: str, token_rows: list[dict],
                      receipts: dict[str, str] | None = None) -> str | None:
    """t30: Warsaw=14 harvested from Helsinki's 'next 14 days' -- the number was
    PRESENT but its source context attached no temperature unit. For an ask
    naming a unit-bearing attribute, every world-receipt number must carry the
    unit in its own context window."""
    low = ask.lower()
    for attrs, units in _ATTR_UNIT_STEMS:
        if not any(a in low for a in attrs):
            continue
        groups: dict[tuple, list[dict]] = {}
        for row_t in token_rows:
            if not any(m in row_t.get("ref", "") for m in _WORLD_REF_MARKS):
                continue                  # calc/user/session numbers are not harvests
            groups.setdefault((row_t["ref"], row_t["value"]), []).append(row_t)
        _adjacent = tuple(units) + tuple(attrs)   # the attribute WORD adjacent
        _wants_current = bool(re.search(r"\b(?:current|now|right now|today|at the moment)\b",
                                        low))
        for (_ref, _val), rows_g in groups.items():  # to the number attaches too
            # ANY occurrence of this value in this receipt attaching the unit
            # grounds it; a value that never wears the unit anywhere is a
            # presence-only harvest.
            if not any(any(u in _value_window(r.get("context", ""), str(_val)).lower()
                           for u in _adjacent) for r in rows_g):
                # LOCAL window (delta review K1): the unit must sit in THIS number's
                # own window, not merely somewhere in the ±40 context — a sibling
                # "78 °F" cannot ground an "Elev 134 ft" two numbers away.
                snippet = rows_g[0].get("context", "")[:50]
                return ("harvest binding: " + str(_val) + " at '" + snippet
                        + "' does not attach the asked attribute's unit -- presence is not grounding")
            if all(re.search(re.escape(str(_val)) + r"[\s-]*(?:hours?|hrs?|days?|weeks?|months?|years?)\b",
                             r.get("context", "").lower()) for r in rows_g):
                # T24: ETH=24 rode "24-hour trading volume" — a duration-
                # suffixed number is the WRONG attribute for a value ask.
                snippet = rows_g[0].get("context", "")[:50]
                return ("harvest binding: " + str(_val) + " at '" + snippet
                        + "' is a duration token, not the asked attribute")
            if _wants_current and all(
                    re.search(r"\b(?:average|annual|climate|normals|typical|monthly|forecast)\b",
                              (r.get("context", "") + " "
                               + (receipts or {}).get(r.get("ref", ""), "")).lower())
                    for r in rows_g):
                # DM-2 Track K (T23: WMO climate AVERAGES shipped as CURRENT
                # temps): the asked field is now/current; a value whose every
                # occurrence sits in average/forecast context is the wrong field.
                snippet = rows_g[0].get("context", "")[:50]
                return ("harvest binding: " + str(_val) + " at '" + snippet
                        + "' is average/forecast context — the ask is for the CURRENT value")
    return None


def _freshness_gap(ask: str, token_rows: list[dict],
                   receipts: dict[str, str] | None = None) -> str | None:
    """t31: a 'latest/current version' claim needs the source's own
    latest-field context around the number, else the harvest is stale-risk and
    the turn declares it cannot confirm."""
    if not re.search(r"\b(?:latest|current|newest)\b[^.?!\n]{0,40}\bversion\b"
                     r"|\bversion\b[^.?!\n]{0,40}\b(?:latest|current|newest)\b",
                     ask, re.IGNORECASE):
        return None
    groups: dict[tuple, list[dict]] = {}
    for row_t in token_rows:
        if not any(m in row_t.get("ref", "") for m in _WORLD_REF_MARKS):
            continue
        groups.setdefault((row_t["ref"], row_t["value"]), []).append(row_t)
    for (_ref, _val), rows_g in groups.items():
        if all(re.search(r"\bstarting with\b|\bwill\b|\bupcoming\b|\bplanned\b|\bfuture\b",
                         (r.get("context", "") + " "
                          + (receipts or {}).get(r.get("ref", ""), "")).lower())
               for r in rows_g):
            # DM-2 Track K (T37: "Starting with Node.js 27, the release cycle
            # will..." shipped as the CURRENT version): announcement context
            # about a future version never grounds a current-version claim,
            # even when the word "release" is present.
            snippet = rows_g[0].get("context", "")[:50]
            return ("freshness: " + str(_val) + " at '" + snippet
                    + "' is a future/announcement context -- not the current version")
        if not any(any(k in r.get("context", "").lower()
                       for k in ("latest", "current", "newest", "release", "download"))
                   for r in rows_g):
            snippet = rows_g[0].get("context", "")[:50]
            return ("freshness: " + str(_val) + " at '" + snippet
                    + "' carries no latest-version context -- cannot confirm it is current")
    return None


def _official_domain(question: str) -> str | None:
    """t31: an 'official <domain>' constraint restricts receipts to the named
    domain. Applies only when the user NAMES the domain."""
    m = re.search(r"official[^.?!\n]{0,30}?\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b",
                  question, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _number_index(
    receipts: dict[str, str],
    per_receipt: int = 8,
    cap: int = 60,
    exclude_refs: set[str] | None = None,
) -> list[dict[str, str]]:
    """Every number in every receipt, indexed as a citable token: the kernel owns the digits.

    The differential's verdict (2026-08-20): free-text citation fails under EVERY judge,
    4B to cloud — models mistype receipt ids and numbers alike. So the protocol inverts:
    models compose prose with {nK} slots, the kernel substitutes the value and derives the
    citation from the token. A number the composition layer never types is a number it
    cannot fabricate — impossible by construction, not merely caught.
    """
    index: list[dict[str, str]] = []
    for ref, text in receipts.items():
        if exclude_refs and ref in exclude_refs:
            continue
        # Canonical lexical spans: QUANTITY spans are the only citable numbers.
        # URL, IDENTIFIER, and OPAQUE spans own their digits — they never enter
        # the number index (Round-020).
        for found, span in enumerate(s for s in lex_spans(text) if s.kind == "QUANTITY"):
            if found >= per_receipt or len(index) >= cap:
                break
            context = " ".join(text[max(0, span.start - 40):min(len(text), span.end + 40)].split())
            index.append({
                "token": f"n{len(index) + 1}",
                "value": span.normalized,
                "ref": ref,
                "context": context,
            })
    return index


_TOKEN_RE = re.compile(r"\{(n\d+)\}")
_NEG_EXIST_RE = re.compile(
    r"\b(?:does\s*n[o']t\s+exist|no\s+such\s+\w+|not\s+found|is\s+absent|there\s+is\s+no\b)",
    re.IGNORECASE)


# BLOCK-C seam 5: ONE definition of "this text carries a quantity" for every
# truth gate, matching _NUMBER_RE's identifier rule — a digit led by a letter,
# digit, or dot fragment is an identifier member (Neo4j, PS5, x402, v2.47),
# never a quantity. Recall heuristics (excerpt pickers, gap escalation) keep
# the loose probe deliberately: over-keeping evidence is safe, over-rejecting
# claims is not.
# Round-020: _QUANTITY_RE is deprecated. All quantity presence checks use
# has_quantity() from the canonical lexical span authority (lexical_spans.py).
# Kept as a module-level constant for any external reference but no longer used
# internally — the canonical lexer handles URL/IDENTIFIER byte ownership.
_QUANTITY_RE = re.compile(r"(?<![0-9A-Za-z.])\d")


def _numbers_block(index: list[dict[str, str]]) -> str:
    if not index:
        return "NUMBERS: (none collected yet)"
    lines = [f"{{{row['token']}}} = {row['value']}  [from {row['ref']}: ...{row['context']}...]" for row in index]
    return "NUMBERS — cite ONLY by token, never type a digit from evidence:\n" + "\n".join(lines)


def _expand_tokens(text: str, index: list[dict[str, str]]) -> tuple[str, set[str], str]:
    """Substitute {nK} slots. Returns (expanded_text, source_refs, error). Unknown token = error."""
    by_token = {row["token"]: row for row in index}
    refs: set[str] = set()
    bad = ""

    def sub(match: re.Match) -> str:
        nonlocal bad
        row = by_token.get(match.group(1))
        if row is None:
            bad = match.group(0)
            return match.group(0)
        refs.add(row["ref"])
        return row["value"]

    expanded = _TOKEN_RE.sub(sub, text)
    return expanded, refs, (f"unknown number token {bad}" if bad else "")


def _fetch_page_text(url: str) -> str:
    """Read a page's body text through the app's one-door fetcher, capped for a receipt.

    Retrieval-depth escalation, measured three times live: gold-spot and weather sites are
    chart pages whose SEARCH SNIPPETS carry no numbers, so a numeric chain could never
    ground however good the query was. When snippets are digitless, the page body is the
    next honest depth — same egress door, same accounting.

    R2b2b: the page transport runs through the ONE door, which refuses a
    fetch with no active ledger. The operator REPL surface owns its page
    fetches here, by name (the same law as kernel.repl.model_call); inside a
    turn the scope defers to the turn's ledger instead.
    """
    from core.effect_gateway import named_background_effect_scope
    from tools.web.http_fetch import http_fetch_text

    with named_background_effect_scope("kernel.repl.page_fetch"):
        result = http_fetch_text(url, timeout_s=12.0)
    text = " ".join(str(result.get("text", "")).split())
    return text[:1500]


def _repair_unknown_tokens(text: str, index: list[dict]) -> str:
    """A model inventing {n38} to MEAN the value 38 (measured live: four Paris-Lyon
    claims died on {n38}/{n55}/{n53}, every one a real receipt number) gets the same
    deterministic courtesy as literal digits: an UNKNOWN token whose digits exactly
    match exactly one indexed value is remapped to that value's real token. Known
    tokens are untouched; digits matching no value stay unknown and fail as before."""
    known = {row["token"] for row in index}
    by_value: dict[str, str] = {}
    for row in index:
        by_value.setdefault(row["value"], row["token"])

    def swap(m: re.Match) -> str:
        tok = m.group(1)
        if tok in known:
            return m.group(0)
        real = by_value.get(tok[1:])
        return "{" + real + "}" if real else m.group(0)

    return _TOKEN_RE.sub(swap, text)


def _number_tokens_of(text: str) -> set[str]:
    """Every canonical QUANTITY value in text, via the canonical lexical span authority."""
    return quantity_values(text)


def _stated_quantity_forms(text: str) -> set[str]:
    """Every canonical numeric form the text STATES, beyond raw digits (consensus-2
    fix 4, measured: '45%' refused as 0.45; '18h 45m' refused as 1125/18.75; the
    guard indexed digit literals, not quantities). Deterministic re-expressions of
    what the user actually wrote — never new information:
      N%        -> N/100 as a decimal
      XhYm/X:YY -> total minutes and decimal hours

    Round-020: regex matches inside canonical opaque spans (URL, IDENTIFIER)
    are excluded — opaque-owned bytes cannot enter quantity semantics.
    """
    from core.kernel.lexical_spans import opaque_ranges

    _opaque = opaque_ranges(text)
    def _in_opaque(pos: int) -> bool:
        return any(a <= pos < b for a, b in _opaque)

    forms: set[str] = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", text):
        if _in_opaque(m.start()):
            continue
        forms.add(_fmt_num(float(m.group(1)) / 100))
    for m in re.finditer(r"(\d{1,2})\s*h\s*(\d{1,2})\s*m?\b|(\d{1,2}):(\d{2})\b", text):
        if _in_opaque(m.start()):
            continue
        hours = int(m.group(1) or m.group(3))
        minutes = int(m.group(2) or m.group(4))
        forms.add(str(hours * 60 + minutes))
        forms.add(_fmt_num(hours + minutes / 60))
        # Writing a duration in hours+minutes STATES the sexagesimal base — the
        # /60 or *60 conversion is the user's own notation, not new information.
        forms.add("60")
    for m in re.finditer(r"\b(\w+)((?:\s*,\s*\w+)*)\s+and\s+(\w+)\b[^.?!]{0,60}?"
                         r"\b(?:split|share|divid\w*)\w*\b[^.?!]{0,30}?\b(?:equally|evenly)\b",
                         text, re.IGNORECASE):
        if _in_opaque(m.start()):
            continue
        # An ENUMERATION that splits equally states its own count: "B and C split
        # 45% equally" states 2 parties — the /2 is the user's enumeration, not a
        # model-introduced fact.
        extra = m.group(2).count(",")
        forms.add(str(2 + extra))
    return forms


def _entity_tokens(text: str) -> set[str]:
    """Referent structure, not keywords: capitalized words, tickers, and code-like
    tokens — the things two texts must share to be about the same object."""
    return {t for t in re.findall(r"[A-Z][A-Za-z0-9_]{1,}", text) if len(t) >= 2}


# entity-value binding (operator live round + Terra/Flash consult): a slot's
# value may bind only from a source that names the slot's entity. Common
# ALL-CAPS non-entities (units, currencies, control words) are not entities.
_NON_ENTITY_CAPS = frozenset((
    "USD", "EUR", "GBP", "JPY", "USDT", "USDC", "C", "F", "K", "GB", "MB", "TB",
    "KM", "MPH", "AM", "PM", "UTC", "GMT", "RM", "ID", "URL", "CPU", "GPU", "SSD",
    "RAM", "HTTP", "API", "ONLY", "EXACTLY", "TOTAL", "BTC", "ETH", "SOL"))
# NOTE: crypto tickers stay ENTITIES for ownership (BTC's price must come from a
# BTC source) — they are excluded from _NON_ENTITY_CAPS only where used as units.
_TICKER_ENTITIES = frozenset(("BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "DOT"))

# A ticker and its full asset name are the SAME entity (mixed-40: BTC= refused
# because the CoinMarketCap snippet says "Bitcoin", SOL= because it says "Solana").
# The entity gate must treat BTC≡Bitcoin so a real price source grounds a ticker slot.
_TICKER_ALIAS = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "ADA": "Cardano",
    "XRP": "Ripple", "DOGE": "Dogecoin", "DOT": "Polkadot",
}
_ALIAS_REV = {v: k for k, v in _TICKER_ALIAS.items()}


def _with_ticker_aliases(ents: set[str]) -> set[str]:
    """Expand an entity set so a ticker matches its full asset name and vice versa."""
    out = set(ents)
    for e in ents:
        if e in _TICKER_ALIAS:
            out.add(_TICKER_ALIAS[e])
        if e in _ALIAS_REV:
            out.add(_ALIAS_REV[e])
    return out


def _slot_entities(text: str) -> set[str]:
    """The proper-noun / ticker entities a claim is ABOUT — city names, asset
    tickers — excluding units and control words."""
    caps = _entity_tokens(text)
    ents = {t for t in caps if t not in _NON_ENTITY_CAPS}
    ents |= {t for t in caps if t in _TICKER_ENTITIES}
    return ents


def _retokenize(text: str, index: list[dict]) -> str:
    """Deterministic NOTATION repair: a literal number inside a QUANTITY span that
    exactly matches a token's value becomes that token.

    Round-020: only digits inside QUANTITY spans are eligible. URL digits, identifier
    digits, and other opaque bytes are never rewritten — raw URL preservation is a
    canonical contract.
    """
    by_value: dict[str, str] = {}
    for row in index:
        by_value.setdefault(row["value"], row["token"])

    # Build set of (start, end) ranges for QUANTITY spans
    q_ranges: list[tuple[int, int]] = [(s.start, s.end) for s in lex_spans(text) if s.kind == "QUANTITY"]

    def swap(m: re.Match) -> str:
        start = m.start()
        raw = m.group(0)
        # Only rewrite if this match falls inside a QUANTITY span
        if not any(a <= start < b for a, b in q_ranges):
            return raw
        tok = by_value.get(raw.replace(",", ""))
        return "{" + tok + "}" if tok else raw

    return re.sub(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", swap, text)


def _extract(runner: EffectRunner, question: str, context: str) -> tuple[list[dict], str]:
    """Model-owned obligation extraction; degraded mode is loud, single, and heuristic-free."""
    try:
        data = _model_json(runner, "model.extract", _EXTRACT_SYSTEM, context + question)
        rows = data.get("obligations")
        assert isinstance(rows, list) and rows, "no obligations array"
        cleaned = []
        for row in rows:
            lane = str(row.get("lane", "")).strip()
            assert lane in _LANES, f"unknown lane {lane!r}"
            try:
                _off = int(row["source_offset"]) if row.get("source_offset") is not None else None
            except (TypeError, ValueError):
                _off = None   # TIER 1: source order is a hint; absence degrades to the
                              # blanket-cancel reading, never a crash.
            cleaned.append({"description": str(row.get("description", "")).strip() or question,
                            "lane": lane, "query": str(row.get("query", "")).strip(),
                            "format": str(row.get("format", "")).strip(),
                            "source_offset": _off,
                            "resolves_carryover": str(row.get("resolves_carryover", "")).strip()})
        return cleaned, ""
    except Exception as exc:
        # TIER 1 fail-closed (Codex amendment 1 / Grok condition 1, block A): a failed
        # extraction yields an EMPTY effect allowlist — NEVER a default web_lookup. The
        # old fallback lane here was "web_lookup", which turned an extraction crash into
        # a live search on the raw message (t15/S14: "extract smash -> web"). The turn
        # now declares a named FAILED terminal instead of guessing an effect.
        note = f"extraction degraded ({type(exc).__name__}: {exc}) — fail-closed: no effect lane runs this turn"
        return [{"description": question, "lane": "chat", "query": "",
                 "format": "", "resolves_carryover": "", "extract_failed": True}], note


# F1 — the USER'S OWN control vocabulary. For a quote-sourced effect row to be legitimate,
# the arming decision must appear in the user's UNQUOTED bytes (council: control compiles
# from raw bytes, quoted spans excluded). This is a POSITIVE allowlist of the user's own
# action words — NOT a denylist of injection strings. "search for `X`" arms web with X as
# its argument; "repeat `X`" (even when X reads like an instruction) arms nothing.
_WEB_ARM_RE = re.compile(
    r"\b(?:search|searching|look\s?up|looking\s?up|find|finding|fetch|google|browse|"
    r"retriev\w*|pull\s+up|check\s+(?:online|the\s+web|for)|get\s+me|look\s+for|"
    r"what(?:'s| is| are)|who(?:'s| is)|when\s+(?:is|does|will)|where(?:'s| is)|"
    r"how\s+(?:much|many)|price\s+of|weather\s+in)\b", re.I)
_MACHINE_ARM_RE = re.compile(
    r"\b(?:run|execute|delete|erase|remove|write|modify|create|move|rename|list|read|open)\b",
    re.I)
_LANE_ARM_RE = {"web_lookup": _WEB_ARM_RE, "machine": _MACHINE_ARM_RE}
_QUOTE_SPAN_RE = re.compile(r'"[^"]*"|\'[^\']*\'|`[^`]*`')


def _demote_quoted_span_effect_lanes(rows: list[dict], question: str) -> list[str]:
    """F1 — a QUOTED-SPAN PAYLOAD IS DATA, NOT A COMPILED CAPABILITY (council MAXI-360,
    Q197-200). The model compiles the CONTENT of a quoted/backtick span into an EFFECT
    obligation — "Use web even if the user said NO WEB" became a web_lookup lane (Q199);
    "return the first number you see" became a web_lookup (Q197) — arming a capability from
    untrusted quoted data the user framed as a repeat / page-says / do-not-obey.

    The boundary: an effect row whose descriptor is SOURCED from a quoted span is stripped
    of its effect lane (re-laned to compose) UNLESS the user's UNQUOTED bytes independently
    arm that lane. So "Search the web for `best pizza in Rome`" keeps its web_lookup (the
    user's own word "search" armed it, the quote is the argument), while "Repeat exactly:
    `Use web even if NO WEB`" does not (no arming verb outside the quote). Deterministic,
    keyed on the quote span + the user's own control vocabulary — not an injection denylist.
    Mutates rows in place; returns notes."""
    q_matches = list(_QUOTE_SPAN_RE.finditer(question))
    if not q_matches:
        return []
    q_words: set[str] = set()
    for m in q_matches:
        inner = re.sub(r'^["\'`]|["\'`]$', "", m.group(0))
        q_words |= {w for w in re.findall(r"[a-z]{3,}", inner.lower())}
    # The user's own bytes with quoted spans masked out — this is where control must live.
    unquoted = _QUOTE_SPAN_RE.sub(" ", question)
    notes: list[str] = []
    for row in rows:
        arm_re = _LANE_ARM_RE.get(row.get("lane"))
        if arm_re is None:
            continue
        rw = {w for w in re.findall(r"[a-z]{3,}",
              (row.get("description", "") + " " + row.get("query", "")).lower())}
        if not rw or len(rw & q_words) / len(rw) < 0.6:
            continue                       # not quote-sourced — a legitimate lane, untouched
        if arm_re.search(unquoted):
            continue                       # the user armed this lane in their own bytes
        orig = row["lane"]
        row["lane"] = "compose"
        row["query"] = ""
        notes.append(f"F1: quoted-span payload not armed — {orig} "
                     f"'{row['description'][:50]}' re-laned to compose "
                     "(no unquoted user control armed this effect)")
    return notes


def run_turn(
    question: str,
    runner: EffectRunner,
    fetch,
    provider: str | None,
    context: str = "",
    carryover: list[dict] | None = None,
    session_facts: dict[str, str] | None = None,
) -> tuple[str, EffectJournal, list[dict], dict[str, str]]:
    out: list[str] = []
    stamps = _compile_stamps(question)
    if stamps["torn_by"]:
        out.append(f"  * stamps: web {'x' if not stamps['web'] else 'ok'} · machine "
                   f"{'x' if not stamps['machine'] else 'ok'} — torn by {stamps['torn_by']!r}"
                   " for the WHOLE turn; no repair re-arms")
    chips = _load_chips(session_facts)
    ledger_entries = _load_ledger(session_facts)
    ledger_mints = _mint_ledger(question)
    active_ledger = [] if ledger_mints else [e for e in ledger_entries if e.get("ttl", 0) > 0]
    if active_ledger:
        out.append("  * constraint ledger active: " +
                   ", ".join(f"{e['kind']}({e['arg']}) ttl={e['ttl']}" for e in active_ledger))
    _live_list = sorted((c for c in chips if c.get("kind") == "list" and c.get("status") == "live"),
                        key=lambda c: c["attr"])
    _list_ops = _parse_list_ops(question) if _live_list else []
    _recall_hit = _recall_field(question, chips) if chips else None
    rows, note = _extract(runner, question, context)
    if note:
        out.append(f"  ! {note}")
    for _f1_note in _demote_quoted_span_effect_lanes(rows, question):
        out.append(f"  ! {_f1_note}")
    # CARRYOVER IS REFERENCE-ONLY (consensus-5 LAW 2, measured: a stale Node.js
    # carryover re-executed a fresh web fetch inside an unrelated folder-listing
    # turn). A row may act on a carryover only when the USER'S CURRENT MESSAGE
    # re-invokes it — shared referent structure with the carryover — otherwise the
    # row is extraction hauntings, not this turn's work, and it is dropped.
    def _overlap_words(text: str) -> set[str]:
        return _entity_tokens(text) | {w for w in re.findall(r"[a-z]{4,}", text.lower())}

    msg_words = _overlap_words(question)
    kept_rows = []
    for row in rows:
        cid = row.get("resolves_carryover", "")
        carry_match = next((c for c in (carryover or []) if c.get("id") == cid), None)
        if carry_match:
            carry_words = _overlap_words(str(carry_match.get("description", "")))
            if carry_words and msg_words and not (carry_words & msg_words):
                row_words = _overlap_words(row.get("description", "") + " " + row.get("query", ""))
                if row_words and carry_words and (row_words & carry_words) and not (row_words & msg_words):
                    out.append(f"  * haunted row dropped: {row['description'][:56]} (carryover {cid} not re-invoked this turn)")
                    continue
                out.append(f"  * carryover resolution stripped from a row the message does not re-invoke ({cid})")
                row["resolves_carryover"] = ""
        kept_rows.append(row)
    if not kept_rows and rows:
        # A turn whose every row was a haunt still SAID something — respond to the
        # user's actual words as chat, never resurrect the dropped stale obligation.
        kept_rows = [{"description": question, "lane": "chat", "query": "",
                      "format": "", "resolves_carryover": ""}]
    rows = kept_rows or rows

    awaiting = next((c for c in (carryover or []) if c.get("reason") == "awaiting-answer"), None)
    if awaiting and rows and all(r["lane"] in ("intake", "chat") for r in rows) \
            and not any(r.get("extract_failed") for r in rows) \
            and not any(r.get("resolves_carryover") == awaiting["id"] for r in rows):
        # CLARIFY-CONTINUATION BINDING (consensus-3 fix 2): the kernel asked; this
        # turn is the user's reply (extraction filed it as chat/intake). The reply
        # re-enters the ORIGINAL ask with the answer attached — never a shelved note.
        out.append(f"  * binding this reply to the open question: {awaiting['description'][:60]}")
        rows[0] = {"description": f"{awaiting['description']} — the user answered: {question}",
                   "lane": "web_lookup" if _compile_stamps(question)["web"] else "knowledge",
                   "query": f"{awaiting['description']} {question}"[:180],
                   "format": rows[0].get("format", ""), "resolves_carryover": awaiting["id"]}
    # ATOMIC LANE REPAIR (operator live round, lane-8A: the in-loop reroute
    # changed the ROW while the obligation KIND had already minted as recall,
    # so recall content-anchoring killed correct answers — Tirana/Penguin).
    # Repairs happen BEFORE the obligation exists: kind, validator, and
    # settlement all see ONE lane.
    for _i, _row in enumerate(rows, 1):
        if _row["lane"] == "recall" and not re.search(
                r"\b(?:my|stored|saved|earlier|previous(?:ly)?|before|first|last|"
                r"again|said|asked|told|mention(?:ed)?|record|session|chat|conversation|"
                r"did i|i (?:gave|said|told|asked)|gave? you)\b",
                _row["description"] + " " + _row.get("query", "") + " " + question, re.IGNORECASE):
            out.append(f"  * lane repair (pre-mint): ob{_i} recall without prior-context reference — knowledge lane")
            _row["lane"] = "knowledge"
    # ROUND-014 ROOT 1 (intake bypass, council/round-014/FIX_PLAN.md): the model's
    # lane label must not erase imperative side-effect intent. The canonical owner
    # core.turn_ir.classify_clause_kind already separates ACT ("send") from CREATE
    # ("draft"); when it reads ACT AND the description names an EXTERNAL destination
    # (channel/service/email target — structural guard, never a platform phrase
    # list), the obligation is re-typed to kind "action" whatever lane the model
    # chose. In-conversation sends ("send me the answer") and local machine ops
    # (open/read files — the T002 path) fail the external guard and are untouched.
    for _i, _row in enumerate(rows, 1):
        # ROUND-015 (council/round-015/FIX_PLAN.md): the guard's AUTHORITATIVE input is
        # the RAW user request — a model paraphrase must never gate a deterministic
        # safety invariant when the user's own bytes are available (T009: extraction
        # dropped "Maria" and disarmed the action authority; healthy controls had been
        # passing by extraction luck). The extracted description stays as corroborating
        # evidence. The machine/web_lookup exemption is SCOPED, not removed: those
        # lanes keep their own truthful gates for LOCAL operations (T002 files,
        # smart-home, shell commands — round-014 overfire controls), but when the RAW
        # request is an externally-targeted ACT the canonical action-truth path wins
        # even if extraction chose machine (T013: truth previously depended on the
        # model volunteering a none: gap).
        _raw_is_action = _external_action_guard(question)
        # ROUND-016 (council/round-016/R1-COMPARISON.md): raw-turn action authority
        # binds ONLY to the action-OWNING child, never blanket-applied. In a
        # multi-row turn the owner is selected by the CANONICAL classifier —
        # classify_clause_kind(description) is ACT (case-robust: "send DM" qualifies
        # via the head verb even though the all-caps recipient defeats the
        # comm-recipient regex; the regex stays a supporting heuristic, never the
        # authority). The machine/web_lookup closure branch (T013) gets the same
        # child-selection test — a retrieval sibling ("find release link") never
        # inherits the turn's action boolean. Single-row turns are unchanged
        # (T009/T013 pins): the only child is the owner.
        _single_row = len(rows) == 1
        try:
            from core.turn_ir import ClauseKind as _ClauseKind
            from core.turn_ir import classify_clause_kind
            _desc_is_act = classify_clause_kind(_row["description"]) is _ClauseKind.ACT
        except Exception:
            _desc_is_act = False
        if ((_row["lane"] not in ("machine", "web_lookup")
             and ((_raw_is_action and (_single_row or _desc_is_act))
                  or _external_action_guard(_row["description"])))
                or (_row["lane"] in ("machine", "web_lookup")
                    and _raw_is_action and (_single_row or _desc_is_act))):
            out.append(f"  * lane repair (pre-mint): ob{_i} imperative external side-effect — action kind")
            _row["lane"] = "action"
    # ROUND-019 R2 — ownership is typed obligation structure, NOT text comparison.
    # A compose-lane obligation with non-compose siblings is a COORDINATOR
    # (it composes the siblings' realizations, it does not co-author them).
    # No vocabulary list, no descriptor token containment, no text-based identity.
    # Every obligation survives to mint -- the typed ownership model decides at
    # arbitration/recovery time which claims ship and which are dropped.
    # ROUND-019 R2: no vocabulary-based demotion. Format constraints are
    # extracted structurally below; every obligation survives to mint.
    obligations = [Obligation(f"ob{i}", row["description"], row["lane"]) for i, row in enumerate(rows, 1)]
    ob_lane = {ob.id: row["lane"] for ob, row in zip(obligations, rows, strict=True)}
    ob_desc = {ob.id: ob.description for ob in obligations}
    harvest_rejected: dict[str, str] = {}    # BLOCK-C seam 6: ob -> gap reason
    ob_desc_map = {ob.id: ob.description for ob in obligations}
    txn = TurnTransaction("repl-turn", obligations)

    # ROUND-019 R2 — typed ownership model (identity from obligation KIND, not text).
    # A compose obligation with non-compose siblings is a COORDINATOR.
    # All other obligations OWNS their own fact.
    # coordinator_id -> [child_id, ...] from typed obligation kinds
    _coordinated_children: dict[str, list[str]] = coordinator_children(obligations)

    def _owner_is_refused(owner_id: str) -> bool:
        """ROUND-023: canonical terminal-owner authority check.

        Returns True when the TurnTransaction obligation for *owner_id* has terminal
        status ``refused``. Checked dynamically against the canonical obligation list
        so it always reflects the current state (``declare_refused()`` may be called
        at multiple points during the turn — row processing, synthesis — and every
        downstream consumer must see the latest state).
        """
        return any(o.id == owner_id and o.status == "refused" for o in obligations)

    fork = ForkContext(fork_id="repl-research", caps=CapabilitySet({"net.fetch"}), parent_id=None)
    machine_fork = ForkContext(fork_id="repl-machine", caps=CapabilitySet({"machine.read"}), parent_id=None)
    receipts: dict[str, str] = {}
    asked_clarify: list[str] = []     # clarify questions shipped this turn (awaiting answers)
    grounded_close: set[str] = set()  # obligations closed on a receipt-backed ref
    valid_intake: list[str] = []      # intake obligations to close on their own receipt

    def _close(ob_id: str, evidence_ref: str) -> None:
        txn.close(ob_id, evidence_ref=evidence_ref)
        base_ref = evidence_ref.split("+")[0]
        if base_ref in receipts or "-calc" in base_ref:
            grounded_close.add(ob_id)

    ob_urls: dict[str, str] = {}
    ob_alt_urls: dict[str, list] = {}
    query_cache: dict[str, list[dict[str, str]]] = {}

    def cached_fetch(query: str) -> list[dict[str, str]]:
        # Four obligations issuing the same query paid four searches (measured: the EPL
        # turn). Identical query, identical turn — one search.
        if fetch is None:
            raise RuntimeError("no search lane")
        if query not in query_cache:
            query_cache[query] = fetch(query)
        return [dict(h) for h in query_cache[query]]
    # THE USER'S MESSAGE IS EVIDENCE. "Convert 10000 rubles" makes 10000 a stipulated
    # fact with a source — measured live without this, derived math cited the nonexistent
    # receipt 'user' (the model's instinct was right, the runtime lacked the object) and
    # extraction smuggled a memory FX rate into arithmetic because user numbers had no
    # legitimate home. One receipt fixes both as a class.
    today = runner.run("kernel.today", lambda: time.strftime("%Y-%m-%d"))
    context = f"Today's date is {today}.\n\n" + context if context else f"Today's date is {today}.\n\nCurrent message: "
    receipts["user"] = f"the user's message this turn: {question}"
    evidence_lines: list[str] = [f"user: {receipts['user']}"]
    # Cross-turn evidence: what the user stated and what was computed in EARLIER turns is
    # legitimate grounding for THIS one — without it, a follow-up's "5000" refused against
    # a user receipt that only holds "how many grams was it?" (measured). Facts arrive as
    # ordinary receipts; nothing about them is special except their origin turn.
    for fact_id, fact_text in (session_facts or {}).items():
        if fact_id.startswith("_"):
            continue          # reserved stores (chips/ledger) ride facts, not evidence
        receipts[fact_id] = fact_text
        evidence_lines.append(f"{fact_id}: {fact_text}")
    _CONTEXT_REFS = {"user", "conversation",
                     *(k for k in (session_facts or {}) if not k.startswith("_"))}

    # CANCELLATION BINDING (TIER 1, block A / Grok condition 5): the cancel_verbs
    # keyword regex is REMOVED, not wrapped. The model classifies control acts (lane
    # 'cancelled') and provides source offsets; the kernel binds each to the effect it
    # retracts by referent + source order, exempts quoted/backtick/URL data spans, and
    # spares later deliverables. See _bind_cancellations.
    cancelled_effects, _cancel_notes = _bind_cancellations(question, rows, obligations)
    out.extend(_cancel_notes)

    for ob, row in zip(obligations, rows, strict=True):
        out.append(f"  {ob.id} [{row['lane']}] {ob.description}" + (f"  ->  {row['query']}" if row["query"] else ""))
        if row.get("extract_failed"):
            # TIER 1 fail-closed: extraction could not parse the message into typed
            # obligations. Declare a named FAILED terminal — run NO effect (no web, no
            # machine) and fabricate NO answer. The empty effect allowlist is the point.
            out.append("  ! fail-closed: extraction failed to parse the message — no web or machine effect runs this turn")
            txn.declare_failed(ob.id, "extraction failed to parse the message into typed obligations — fail-closed, no effect lane run")
            continue
        if row["lane"] == "cancelled":
            # A request the user retracted in the SAME message settles by declaration, not
            # by silent omission — the manifest shows both the ask and the retraction were
            # heard (measured live: "HOLD ON. Cancel" left the cancelled lookup running).
            _c_ref = {w for w in re.findall(r"[a-z]{4,}", ob.description.lower())
                      if w not in _CANCEL_STOPWORDS}
            if not _c_ref and not any(r["lane"] in ("web_lookup", "machine")
                                      for r in rows if r is not row):
                # BLOCK-B (t91/t94 phantom cancel): a GENERIC cancel ("cancel the
                # current task") with no action anywhere in the turn is answered,
                # not silently minted as a cancelled state. A cancel that NAMES the
                # retracted action (same-message retraction) settles normally.
                txn.declare_cancelled(ob.id, "nothing was running — there is no live task to cancel")
            else:
                txn.declare_cancelled(ob.id, "cancelled by the user later in the same message — the latest instruction wins")
            continue
        if row["lane"] == "arithmetic":
            # BLOCK-C seam 7: typed time-of-day computation runs BEFORE plain
            # arithmetic — "14:20 + 45 minutes" is a clock sum, not "59 minutes"
            # (t66). The door verifies both figures against the user's message.
            _clock = _eval_time(row["query"], question)
            if _clock is None and re.search(
                    r"\b\d{1,2}:\d{2}\s*[+-]\s*\d{1,4}\b(?!\s*:)(?![^,.?!]{0,12}"
                    r"(?:minutes?|mins?|hours?|hrs?|seconds?|secs?))",
                    row["query"] + " " + question, re.IGNORECASE):
                # DM-2 Track K (T27: "09:15 + 3" shipped 12:15 assuming HOURS):
                # a unitless clock offset is ambiguous — ask, never assume.
                txn.declare_refused(ob.id, "ambiguous time arithmetic: add 3 what — "
                                    "minutes or hours? State the unit and I compute it")
                continue
            if _clock is not None:
                ref = f"{ob.id}-calc"
                receipts[ref] = f"computed locally: time arithmetic on your figures = {_clock}"
                evidence_lines.append(f"{ref}: {receipts[ref]}")
                out.append(f"      time arithmetic: result {_clock}")
                continue
            # TIER 5 (deixis): a definite reference to an entity's figure that could
            # resolve to more than one session answer-figure must NOT bind by adjacency.
            # If the query computed on such an ambiguous reference, refuse and name the
            # candidates so the user disambiguates (t10: "the Oslo figure" silently
            # became 419.52 / 2 = 209.76 while 170 also stood).
            _amb = _ambiguous_deixis(question, session_facts)
            if _amb and any(c in row["query"].replace(",", "") for c in _amb[1]):
                out.append(f"  ! ambiguous reference to the {_amb[0]} figure — {len(_amb[1])} candidates {_amb[1]}; specify which")
                txn.declare_refused(ob.id, f"ambiguous reference to the {_amb[0]} figure: candidates {_amb[1]} — specify which before I compute")
                continue
            # The SAME provenance law the derive round obeys: every number in an
            # extraction-time expression must be a number the user stated. A memory
            # rate ("/ 94.54") converts the obligation into a lookup instead of being
            # silently evaluated — extraction arithmetic was the one unguarded door.
            expr_numbers = {m.replace(",", "") for m in re.findall(r"-?\d+(?:\.\d+)?", row["query"].replace(",", ""))}
            # DM-2 D4 operand provenance (T44: 57*8 refused as "not in user
            # statement" then web-converted, shipping stale 846): the user's own
            # LIVE stored field values are first-class operands.
            _chip_vals = {str(c.get("value", "")).replace(",", "")
                          for c in chips
                          if c.get("kind") == "field" and c.get("status") == "live"}
            unstated = set()
            for num in expr_numbers:
                if num in _chip_vals:
                    continue
                if _structural_literal(num, row["query"]):
                    # BLOCK-B: structure by SYNTAX (0/1, powers of ten, comparison
                    # operands) — no magnitude band. Facts still need grounding below.
                    continue
                # Provenance classes (consensus review-20260820-035058, fix 1): the user's
                # message AND the session's own receipts are first-class operands. The old
                # user-only check refused the model's CORRECT "44.92 / 0.07034" over the
                # session's receipted prices and forced a derive that bound turn ordinals.
                for prov in _CONTEXT_REFS:
                    if prov not in receipts:
                        continue
                    try:
                        validate_claims([TypedClaim(text=num, ctype="observed", ref=prov)], receipts)
                        break
                    except EvidenceTypeError:
                        continue
                else:
                    stated_forms: set[str] = set()
                    for prov in _CONTEXT_REFS:
                        if prov in receipts:
                            stated_forms |= _stated_quantity_forms(receipts[prov])
                    if num not in stated_forms:
                        unstated.add(num)
            for grp in re.findall(r"\(([\d\s.,+\-*/%]{3,})\)", question):
                grp_nums = set(re.findall(r"\d+(?:\.\d+)?", grp))
                if not grp_nums or not grp_nums <= expr_numbers:
                    continue
                q_groups = [set(re.findall(r"\d+(?:\.\d+)?", g))
                            for g in re.findall(r"\(([^()]*)\)", row["query"])]
                if not any(grp_nums <= qg for qg in q_groups):
                    # FORMULA FIDELITY (consensus-3 fix 4, measured: the user's
                    # parentheses were dropped and 1145 shipped for 185): a
                    # parenthesized group the user wrote must survive as a group.
                    out.append("      arithmetic refused — the user's parentheses were dropped from the expression")
                    unstated.add(f"({grp.strip()})")
                    break
            if unstated:
                if not stamps["web"]:
                    # BLOCK-B stamps (G3-LD): repair may not re-arm a torn web —
                    # the refusal renders instead of a silent fetch.
                    out.append(f"      arithmetic refused — operands {sorted(unstated)} ungrounded and web is disabled by your instruction")
                    txn.declare_refused(ob.id, f"cannot compute: operands {sorted(unstated)} are ungrounded and web is disabled by your instruction")
                    continue
                _livey = re.search(
                    r"\b(?:price|rate|temperature|weather|current|today|live|"
                    r"latest|now|exchange|stock|market|version)\b",
                    ob.description + " " + question, re.IGNORECASE)
                _storedy = re.search(r"\bstored\b|\bmy record\b|\bthe record\b",
                                     ob.description + " " + question, re.IGNORECASE)
                if _storedy or not _livey:
                    # DM-2 D8 AMBIENT-WEB ARMING (T44): an obligation that is
                    # not a live-lookup never arms web.* — the conversion door
                    # was the arming path. Refuse with the gap named.
                    out.append(f"      arithmetic refused — operands {sorted(unstated)} unresolved"
                               " and this is not a live-value ask; web stays unarmed")
                    txn.declare_refused(ob.id, f"cannot compute: operands {sorted(unstated)}"
                                        " are not in your message or stored fields — no lookup"
                                        " applies to a stored/local computation")
                    continue
                out.append(
                    f"      arithmetic refused — numbers in no user statement or session receipt: {sorted(unstated)};"
                    " converting to a lookup for the missing figure"
                )
                row["lane"] = "web_lookup"
                row["query"] = ob.description
            else:
                try:
                    value = runner.run("kernel.arith", _eval_arith, row["query"])
                except Exception as exc:
                    out.append(f"      arithmetic tool refused ({exc}) — left for synthesis as knowledge")
                    continue
                ref = f"{ob.id}-calc"
                receipts[ref] = f"computed locally: {row['query']} = {_fmt_num(value)}; inputs from user"
                evidence_lines.append(f"{ref}: {receipts[ref]}")
                continue
        if row["lane"] == "machine":
            if not stamps["machine"]:
                out.append(f"  * stamp: tools torn — {ob.id} refused, no machine op runs")
                txn.declare_refused(ob.id, "tools are disabled by your instruction for this turn — no machine operation runs")
                continue
            none_m = re.fullmatch(r"none:(.*)", row["query"], re.DOTALL)
            if none_m:
                # CAPABILITY-GAP TERMINAL (consensus-5 LAW 1, measured: pwd shipped the
                # local TIME and a code-edit ran str.reverse on a placeholder — the
                # nearest-enum guess is dead; the honest exit is a named refusal).
                txn.declare_refused(ob.id, f"capability gap: {none_m.group(1).strip() or 'no machine op serves this request'}")
                continue
            date_m = re.fullmatch(r"date([+-]\d{1,4})d", row["query"])
            str_m = re.fullmatch(r"str\.(reverse|count_letters|count_chars|count_vowels|count_consonants):(.*)", row["query"], re.DOTALL)
            if str_m:
                # NO OP ON A SYNTHETIC PLACEHOLDER OR ABSENT REFERENT (consensus-5
                # LAW 1, measured: str.reverse('<text>') shipped '>txet<' for a
                # code-edit ask). BLOCK-C seam 11 (t84, Terra amendment 3): a
                # refusal may claim a fact about current-message text ONLY when
                # that fact is checked against the current-message bytes. Three
                # distinct terminals replace the shared message that let a
                # placeholder-wrapped PRESENT text ship as "does not exist".
                _arg = str_m.group(2).strip()
                _ph = re.fullmatch(r"<([^>]*)>", _arg)
                _referent = (_ph.group(1).strip() if _ph else _arg)
                if not _referent:
                    txn.declare_refused(ob.id, "capability gap: the operation was given no text to act on")
                    continue
                if _referent not in question:
                    # verified against the current-message bytes: the absence
                    # claim below is TRUE by construction.
                    txn.declare_refused(ob.id, f"capability gap: the text {_referent!r} does not exist in this turn's message")
                    continue
                if _ph:
                    # The placeholder wrapped text that IS present this turn —
                    # claiming absence would be a false statement about the
                    # user's own message. The gap is the OPERATION, never an
                    # invented referent failure.
                    txn.declare_refused(ob.id, "capability gap: no machine operation serves this request — the quoted text is present in your message, but this surface cannot perform the requested action on it")
                    continue
            if str_m:
                _mismatch = _str_count_unit_mismatch(str_m.group(1), ob.description)
                if _mismatch:
                    # TIER 3 operation-equivalence: a count op must count the unit the
                    # ask names, never approximate (t18: "vowels" != characters).
                    txn.declare_refused(ob.id, f"{_mismatch} — the operation must match the request")
                    continue
                if str_m.group(1) == "reverse":
                    _om = _machine_op_ask_mismatch("str.reverse", ob.description)
                    if _om:
                        txn.declare_refused(ob.id, _om)
                        continue
            op = row["query"] if row["query"] in _MACHINE_OPS else ("date" if date_m else "str" if str_m else "")
            if not op:
                # DISPATCH VALIDATION (consensus-2): an unfilled template ("date+<N>d")
                # or unknown op is a routing/capability failure — REFUSED with the reason
                # named, never "unanswerable" (measured: three obligations settled as
                # unanswerable on a template string with a literal placeholder).
                txn.declare_refused(ob.id, f"capability gap: no machine operation matches {row['query']!r}")
                continue
            _om = _machine_op_ask_mismatch(row["query"], ob.description) if row["query"] in _MACHINE_OPS else None
            if _om:
                # BLOCK-B registry: never run a substitute op (t171-class).
                txn.declare_refused(ob.id, _om)
                continue
            check_tool_call(machine_fork, f"machine.{op}", "machine.read", {"op": row["query"]})
            try:
                if date_m:
                    offset = int(date_m.group(1))
                    report = runner.run("machine.date", _machine_date_offset, offset)
                elif str_m:
                    report = runner.run("machine.str", _machine_str, str_m.group(1), str_m.group(2))
                else:
                    report = runner.run(f"machine.{op}", _MACHINE_OPS[op])
                    # BLOCK-C seam 4: the receipt NAMES the op that ran — its
                    # output can never wear a requested command's costume (t75).
                    report = f"machine.{op} (kernel-derived local report): {report}"
            except Exception as exc:
                txn.declare_failed(ob.id, f"machine tool failed: {exc}")
                continue
            ref = f"{ob.id}-mac"
            receipts[ref] = report
            evidence_lines.append(f"{ref}: {receipts[ref]}")
            continue
        if row["lane"] == "recall":
            try:
                record = runner.run("session.recall", _session_record, row["query"])
            except Exception as exc:
                txn.declare_unanswerable(ob.id, f"session record unreadable: {exc}")
                continue
            ref = f"{ob.id}-rec"
            receipts[ref] = record
            evidence_lines.append(f"{ref}: {receipts[ref]}")
            continue
        if row["lane"] == "intake":
            # FACT INTAKE (consensus-2 fix 2): user-supplied task data becomes a
            # session receipt with user provenance; the turn acknowledges and holds.
            ref = f"{ob.id}-in"
            receipts[ref] = f"task data the user supplied (this turn): {question}"
            evidence_lines.append(f"{ref}: {receipts[ref]}")
            valid_intake.append(ob.id)
            continue
        if row["lane"] == "action":
            # ROUND-014 ROOT 1 dispatch: an external side-effect obligation's only
            # legal terminals are genuine capability execution with a typed exec:
            # receipt (none exist in this kernel — KAS owns future integrations) or
            # the truthful named capability-gap refusal (the T002-proven terminal).
            # Generated text is NEVER proof that an external side effect happened.
            txn.declare_refused(
                ob.id, f"capability gap: external action not available in this runtime — "
                       f"{ob_desc[ob.id][:60]}")
            out.append(f"  ! action truth: {ob.id} is an external side-effect request with no "
                       f"genuine capability in this runtime — refused, never composed as done")
            continue
        if row["lane"] in ("chat", "clarify", "knowledge", "compose"):
            continue  # their deliverable is synthesis text, typed accordingly
        if row["lane"] == "web_lookup":
            if not stamps["web"]:
                # BLOCK-B stamps (t160): the veto is absolute for the turn — the
                # ask is answered from general knowledge, never fetched.
                out.append(f"  * stamp: web torn — {ob.id} answers from general knowledge, zero fetches")
                row["lane"] = "knowledge"
                continue
            if fetch is None:
                txn.declare_unanswerable(ob.id, "no grounded lane: no search-API key is stored")
                continue
            if ob.id in cancelled_effects:
                # PRE-EFFECT DENY GATE (consensus-4 fix 2): a cancelled action's
                # effects never start — enforced at the door, not by planning order.
                out.append(f"  ! deny gate: {ob.id} was cancelled in this message — no effect runs")
                continue
            check_tool_call(fork, "web.search", "net.fetch", {"q": row["query"]})
            try:
                hits = runner.run("web.search." + str(provider), cached_fetch, row["query"])
            except Exception as exc:
                txn.declare_unanswerable(ob.id, f"web lookup failed: {exc}")
                continue
            if not hits:
                txn.declare_unanswerable(ob.id, "searched, no results returned")
                continue
            _dom = _official_domain(question)
            if _dom:
                # BLOCK-C seam 6: the user NAMED the official domain -- receipts
                # from anywhere else are ineligible harvests for this ask.
                _kept = [h for h in hits if _dom in str(h.get("url", "")).lower()]
                if not _kept:
                    txn.declare_unanswerable(
                        ob.id, f"no result from the named official domain {_dom} -- "
                               "other sources are ineligible under your constraint")
                    continue
                if len(_kept) < len(hits):
                    out.append(f"  * official-source constraint: {len(hits) - len(_kept)} off-domain hit(s) excluded ({_dom})")
                hits = _kept
            ob_urls[ob.id] = str(hits[0]["url"])
            ob_alt_urls[ob.id] = [str(h.get("url", "")) for h in hits[1:3] if h.get("url")]
            for n, hit in enumerate(hits, 1):
                ref = f"{ob.id}-web{n}"
                receipts[ref] = f"{_clean(hit['title'])} — {_clean(hit['snippet'])} ({hit['url']})"
                evidence_lines.append(f"{ref}: {receipts[ref]}")

    def evidence_block() -> str:
        return "\n".join(evidence_lines) if evidence_lines else "(none collected)"

    obligations_block = f"Today's date is {today}.\n" + "\n".join(f"{ob.id}: {ob.description}" for ob in obligations)

    # DERIVE ROUNDS — the loop's second and third acts. An obligation like "fuel for the
    # trip" depends on numbers that only exist after evidence collection; without this the
    # arbiter is forced into symbolic arithmetic ("distance / 10") the tool rightly refuses.
    # Derived computations bind CONCRETE numbers from receipts and become receipts
    # themselves (the conductor law: derived nodes read dependency results). And a number
    # the evidence LACKS is an open cut, not a dead end: the model names the gap with a
    # search query, the kernel fetches it through the same gate, and derivation runs once
    # more — the inspect->act->inspect->continue contract, bounded to one gap round so a
    # model that keeps inventing gaps cannot spin the turn.
    calc_n = 0
    derive_refusals: list[str] = []
    # arithmetic obligations the model ATTEMPTED to derive (mixed-40 fresh S7): the
    # kernel tried to compute the quantity from evidence tokens. If the attempt yields
    # no calc receipt (every expression refused — mis-bound operands, unit mismatch,
    # foreign cross-slot operand), a model-memory literal for that slot is the same
    # fabrication the derive gate just refused — synth declines it and declares a gap
    # rather than shipping a confident wrong number. (A slot the model never tried to
    # derive is a self-contained string op — vowel counts — whose memory result still
    # ships marked, since the kernel genuinely cannot express it.)
    derive_attempted: set[str] = set()
    number_index = _number_index(receipts)
    # One quantity, one value: the same (obligation, label) derived twice with different
    # results is a contradiction the answer must carry, not a choice the kernel makes
    # silently (measured live: the elevator turn shipped one of two disagreeing counts).
    # Keyed by label so an obligation legitimately deriving two DIFFERENT quantities
    # (fuel liters AND fuel cost) is never flagged.
    calc_values: dict[tuple[str, str], set[str]] = {}
    calc_sources: dict[str, set[str]] = {}  # which receipts each obligation's derivations consumed
    numeric_lanes = any(row["lane"] in ("web_lookup", "arithmetic", "machine") for row in rows)
    for derive_round in (1, 2) if numeric_lanes else ():
        # (chat/clarify/recall-only turns have nothing to compute — the junk "derived
        # computation refused" noise on every greeting was this loop running anyway)
      try:
        # Round 2 carries round 1's exact validator refusals back to the model — the same
        # bounded, error-named repair the JSON path uses. A weak model gets one precise
        # correction; a strong model needs none; no heuristic learns the phrasing.
        repair_note = (
            "\n\nYour previous computations were REFUSED by the validator — correct every"
            " one or leave it out:\n" + "\n".join(derive_refusals)
        ) if derive_refusals else ""
        number_index = _number_index(receipts)
        data = _model_json(
            runner, "model.derive", _DERIVE_SYSTEM,
            "User message: " + question + "\n\nObligations:\n" + obligations_block
            + "\n\n" + _numbers_block(number_index) + "\n\nEvidence:\n" + evidence_block() + repair_note,
        )
        for row in data.get("computations", []):
            calc_n += 1
            n = calc_n
            raw_expr = str(row.get("expression", "")).strip()
            label_raw = str(row.get("label", "")).strip() or "derived computation"
            owner = _normalize_ref(row.get("obligation_id", "")) or "turn"
            # ROUND-023: terminal owner cannot mint new semantic derivation artifacts.
            if _owner_is_refused(owner):
                out.append(f"  ! derived computation refused — {owner} is a refused obligation; no new calc receipt")
                continue
            if not raw_expr:
                continue
            # AUTHORITATIVE EXTRACT-TIME CALC (mixed-40 fresh S10). This obligation's
            # PRIMARY quantity was already evaluated at extract time from the user's own
            # literal expression — formula-fidelity + operand-provenance checked, minted
            # as "{owner}-calc ... inputs from user". Re-deriving THAT SAME quantity here
            # lets the register-blind model pick rival token indices (138*3+4+16*992=16290
            # pulled ob2's operands into ob9's hotel total) that clash with the
            # authoritative extract value. So skip ONLY a row that re-derives the primary
            # quantity — its label matches the obligation's own description. A
            # DIFFERENTLY-labelled second quantity for the same slot (fuel liters ->
            # fuel cost) is legitimate composition and must derive normally (review
            # wf_8381a777 confirmed the owner-only skip dropped the composed value).
            _auth_extract = receipts.get(f"{owner}-calc", "")
            _norm = lambda _s: " ".join(str(_s).lower().split())  # noqa: E731
            if (_auth_extract and "inputs from user" in _auth_extract
                    and (not label_raw or _norm(label_raw) == _norm(ob_desc_map.get(owner, "")))):
                out.append(f"  * derive skipped for {owner}: authoritative extract-time calc stands (primary quantity)")
                continue
            # TOKEN EXPRESSIONS: the kernel substitutes values and resolves provenance —
            # the model cannot misbind a source because it never names one. Literals are
            # allowed only as small whole structural numbers (a round trip's *2, a /100).
            expr, token_refs, token_err = _expand_tokens(raw_expr, number_index)
            if token_err:
                out.append(f"  ! derived computation refused — {token_err}")
                derive_refusals.append(f"- {raw_expr}: {token_err}")
                continue
            s_refs = {r for r in token_refs if r.startswith("s") and r[1:].isdigit()}
            off_topic = []
            for s_ref in sorted(s_refs):
                fact_text = receipts.get(s_ref, "")
                ask_text = ob_desc_map.get(owner, "") + " " + label_raw
                # K2 (entity-keyed session, delta review T33): a session value is
                # MINTED for its own entity — "Athens temperature is 86°F" is
                # Athens's. It may fill a slot only when THAT entity is named in the
                # current turn (the question or this obligation's ask); an Athens
                # value cannot become Budapest's temperature merely because both say
                # "temperature". Entity match, not attribute-word match — proper
                # nouns/tickers only, so a shared generic word never grounds it.
                fact_ents = _slot_entities(fact_text)
                here_ents = _slot_entities(question) | _slot_entities(ask_text)
                if fact_ents and here_ents and not (fact_ents & here_ents):
                    off_topic.append(s_ref)
                    continue
                subj = _entity_tokens(fact_text) | {w for w in re.findall(r"[a-z]{4,}", fact_text.lower())}
                ask = _entity_tokens(ask_text) | {w for w in re.findall(r"[a-z]{4,}", ask_text.lower())}
                if subj and ask and not (subj & ask):
                    off_topic.append(s_ref)
            if off_topic:
                # TOPICAL BINDING (consensus-3 fix 4, measured: an Italian toll bound
                # the FUEL-PRICE session tokens): a session receipt lends its numbers
                # only to an obligation that shares its subject.
                out.append(f"  ! derived computation refused — session receipts {off_topic} share no subject with this obligation")
                derive_refusals.append(f"- {raw_expr}: {off_topic} are about a different subject — declare a gap instead")
                continue
            token_values = {r["value"] for r in number_index}
            literals = {m for m in re.findall(r"-?\d+(?:\.\d+)?", expr.replace(",", ""))} - token_values
            bad_literals = sorted(
                lit for lit in literals if not _structural_literal(lit, expr)
            )
            if bad_literals:
                out.append(f"  ! derived computation refused — non-structural literals (use tokens or a gap): {bad_literals}")
                derive_refusals.append(f"- {raw_expr}: non-structural literals {bad_literals}")
                continue
            if not token_refs:
                out.append("  ! derived computation refused — no evidence token used")
                derive_refusals.append(f"- {raw_expr}: no evidence token used")
                continue
            _by_tok_d = {r["token"]: r for r in number_index}
            # NON-COMPUTATION PASSTHROUGH (delta review K1, T18/T22). A bare "{n1}"
            # is not a computation — it launders a WORLD number's provenance into a
            # calc token (ob1-web1 -> ob1-calc1) that the harvest gate exempts, so
            # "Capital of Bosnia: 275524" and "Budapest Temperature: 134" ship as
            # local computations. A lone world value belongs in a CLAIM, where the
            # entity/unit gates run; refuse the pseudo-derivation so the number faces
            # them. (A real computation always has an operator or >1 operand.)
            _raw_toks = _TOKEN_RE.findall(raw_expr)
            if len(_raw_toks) == 1 and re.fullmatch(r"\{n\d+\}", raw_expr.strip()):
                _pr = _by_tok_d.get(_raw_toks[0])
                if _pr and any(w in _pr.get("ref", "") for w in _WORLD_REF_MARKS):
                    out.append(f"  ! derived computation refused — '{raw_expr}' is a lone world value, not a computation")
                    derive_refusals.append(f"- {raw_expr}: a lone world value is not a computation — state it in a claim, not a derivation")
                    continue
            # K1/K4 (attribute-typed operands, delta review). A world number's
            # eligibility is decided by the units in its OWN local window — bounded
            # by its sibling numbers, so a temperature two numbers away cannot
            # ground an elevation. Checked here, at derive time, on the world
            # operand itself: the calc RECEIPT would relabel it ("Temperature: 134")
            # and launder it, so the check must run BEFORE the calc mints. Uses the
            # existing unit stems — checked LOCALLY, never grown.
            _ask_attr = (ob_desc_map.get(owner, "") + " " + label_raw).lower()
            _bad_operand = None
            for _tokm in dict.fromkeys(_TOKEN_RE.findall(raw_expr)):
                _wr = _by_tok_d.get(_tokm)
                if not _wr or not any(w in _wr.get("ref", "") for w in _WORLD_REF_MARKS):
                    continue
                _win = _value_window(_wr.get("context", ""), _wr["value"]).lower()
                _after = _win.split(_wr["value"], 1)[1] if _wr["value"] in _win else ""
                # (a) K4 — a DURATION operand ("10 day") is not a comparable
                #     quantity for a computation that did not ask a duration (T4).
                if _DURATION_UNIT_RE.match(_after) and not re.search(
                        r"\b(?:day|days|hour|hours|week|month|year|night|duration|long|old)\b", _ask_attr):
                    _bad_operand = (_wr["value"], "is a duration, not a comparable quantity", _win)
                    break
                # (b) K1 — a SIBLING number in the same receipt wears the asked
                #     unit and THIS operand does not: this operand is the wrong
                #     quantity (T18: "78 °F" wears it, "134 ft" does not, so 134 is
                #     the elevation). Only fires when the unit is PRESENT in the
                #     receipt but attached to a different number — a receipt with NO
                #     unit at all ("Seoul 39 while Tokyo 30") leaves bare numbers
                #     contextually grounded, so a legitimate comparison still runs.
                _full_ctx = _wr.get("context", "").lower()
                for _attrs, _units in _ATTR_UNIT_STEMS:
                    if not any(a in _ask_attr for a in _attrs):
                        continue
                    _adj = tuple(_units) + tuple(_attrs)
                    if any(u in _full_ctx for u in _adj) and not any(u in _win for u in _adj):
                        _bad_operand = (_wr["value"], "a sibling number wears the asked unit; this one does not", _win)
                        break
                if _bad_operand:
                    break
            if _bad_operand:
                _bv, _bwhy, _bwin = _bad_operand
                out.append(f"  ! derived computation refused — operand {_bv} {_bwhy} (window '{_bwin.strip()[:40]}')")
                derive_refusals.append(f"- {raw_expr}: operand {_bv} {_bwhy} — cite the value that carries the asked unit")
                derive_attempted.add(owner)  # a genuine operand-BINDING failure
                continue
            try:
                value = runner.run("kernel.arith", _eval_arith, expr)
            except Exception as exc:
                out.append(f"  ! derived computation refused ({exc})")
                derive_refusals.append(f"- {raw_expr}: {exc}")
                continue
            inert = []
            is_comparison = bool(re.search(r"[<>]=?|==|!=", expr))
            if not is_comparison:
                # (comparisons are step functions — a token can survive perturbation
                # without being fake; the tautology law covers continuous math only)
                for row_tok in number_index:
                    if "{" + row_tok["token"] + "}" not in raw_expr:
                        continue  # only tokens the expression actually uses
                    perturbed = raw_expr.replace("{" + row_tok["token"] + "}",
                                                 _fmt_num(float(row_tok["value"]) * 1.37 + 1))
                    p_expanded, _, p_err = _expand_tokens(perturbed, number_index)
                    try:
                        if not p_err and _eval_arith(p_expanded) == value:
                            inert.append(row_tok["token"])
                    except Exception:
                        pass
            if inert:
                # TAUTOLOGY / FAKE SOURCING (consensus-3 fix 4, measured: "1.55 *
                # 3.42 / 3.42 = 1.55" shipped as a derivation): a cited token whose
                # perturbation leaves the result unchanged is not a source.
                out.append(f"  ! derived computation refused — cited tokens do not affect the result: {inert}")
                derive_refusals.append(f"- {raw_expr}: tokens {inert} are inert — the expression fakes its sources")
                continue
            if token_refs <= {"user"} and ob_lane.get(owner) != "arithmetic":
                # REFERENT GAP LAW (consensus fix 1b): this obligation references a
                # prior/derived/world quantity, yet every operand came from the user's
                # own message text — measured live, that is how "Turn 1"/"Turn 2"
                # ordinals became prices (1/2 = 0.5) and a stray literal became 5*5=25.
                # The named quantity has no receipt: that is a gap to declare and (when
                # a web lane exists) to search — never a literal to fill.
                out.append(f"  ! referent gap for {owner}: '{label_raw[:60]}' binds only message-text numbers — the named quantity has no receipt")
                derive_refusals.append(f"- {raw_expr}: the quantity '{label_raw[:60]}' exists in no receipt — name it in \"missing\" with a search query instead")
                continue
            # OPERAND-ENTITY BINDING (mixed-40 fresh S7 ratio + S14 derive-garbage).
            # The register-blind model authors a computation whose operand token
            # INDICES do not match the entities it is labeled to compute. Two failure
            # shapes, both measured live, both refused here BEFORE the wrong value mints
            # a calc receipt that would then ship as grounded truth:
            _op_toks = [_by_tok_d[t] for t in _TOKEN_RE.findall(raw_expr) if t in _by_tok_d]
            # (C) CROSS-SLOT CALC OPERAND into a NON-ARITHMETIC slot: a WORLD/knowledge
            #     slot must not pull another obligation's COMPUTED answer in as an
            #     operand (S14: "BTC/USD rate = 19 * 74402" pulled the 19 from ob4's
            #     arithmetic; "lead = 1901" was ob4's whole result). An ARITHMETIC owner
            #     is EXEMPT: "compute A, then A * 3" legitimately chains a prior slot's
            #     result, and refusing it silently dropped the total (review
            #     wf_8381a777). Only a slot whose own lane is not a computation treats a
            #     foreign calc as a fabricated operand.
            _foreign_calc = next(
                (r for r in (_o.get("ref", "") for _o in _op_toks)
                 if "-calc" in r and not r.startswith(owner + "-")), None)
            if _foreign_calc and ob_lane.get(owner) != "arithmetic":
                out.append(f"  ! derived computation refused — {owner} ({ob_lane.get(owner)}) pulls another slot's computation {_foreign_calc}: {raw_expr}")
                derive_refusals.append(f"- {raw_expr}: operand cites {_foreign_calc}, a different obligation's computed answer — not an operand for a {ob_lane.get(owner)} slot")
                derive_attempted.add(owner)  # a genuine operand-BINDING failure
                continue
            # (B) COLLAPSED SOURCES: the label names N>=2 distinct entities (an
            #     "ADA/DOGE ratio") but the operands come from FEWER than N distinct
            #     source receipts — the model bound BOTH operands to ADA (0.215/0.215 =
            #     1.0, both from ob1-web1), leaving DOGE unbound. Detected structurally
            #     by distinct-source COUNT, not by string-matching entity names against
            #     receipt text: surface-form matching false-refused correct non-crypto
            #     pairs ("NYC vs LA temperature", "AVAX/SOL ratio") whose receipts name
            #     the entity by a form the label abbreviates (review wf_8381a777). Two
            #     operands from two receipts naming two distinct things pass; a collapse
            #     onto one source is the measured mis-binding.
            _label_ents = _slot_entities(label_raw)
            if len(_label_ents) >= 2:
                _op_srcs = {r for r in (_o.get("ref", "") for _o in _op_toks) if r}
                if len(_op_srcs) < len(_label_ents):
                    out.append(f"  ! derived computation refused — {len(_label_ents)} named entities but operands collapse to {len(_op_srcs)} source(s): {raw_expr}")
                    derive_refusals.append(f"- {raw_expr}: label names {sorted(_label_ents)} but operands draw from only {sorted(_op_srcs)} — each named quantity must use its own live value")
                    derive_attempted.add(owner)  # a genuine operand-BINDING failure
                    continue
            ref = f"{owner}-calc{n}"
            label = str(row.get("label", "")).strip() or "derived computation"
            calc_values.setdefault((owner, label.lower()), set()).add(_fmt_num(value))
            calc_sources.setdefault(owner, set()).update(token_refs)
            if re.search(r"[<>]=?|==|!=", expr):
                # A comparison receipt carries its verdict as a WORD: digits 1/0 here
                # would be harvest bait for the token index, and the verdict is not a
                # quantity (consensus item 4: verdict receipts ground verdict words).
                rendered = "true" if value else "false"
            else:
                rendered = _fmt_num(value)
            receipts[ref] = (
                f"computed locally: {label}: {expr} = {rendered}; sources: " + ", ".join(sorted(token_refs))
            )
            evidence_lines.append(f"{ref}: {receipts[ref]}")
            _sN = {r for r in receipts if r.startswith("s") and r[1:].isdigit()}
            number_index = _number_index(
                receipts,
                exclude_refs=_sN if re.search(r"\bstored\b", question, re.IGNORECASE) else None,
            )  # new calc numbers become citable tokens; a stored-field ask never
            #    binds stale session numbers as substitute operands (DM-2 D4/T44)
            out.append(f"  + derived: {ref} {label} = {_fmt_num(value)}  (sources: {', '.join(sorted(token_refs))})")
        gaps = [g for g in (data.get("missing") or []) if str(g.get("query", "")).strip()]
        if derive_round == 2 or (not gaps and not derive_refusals) or (gaps and fetch is None):
            break
        # Web gaps exist only for WORLD facts. A self-contained computation's truth is
        # computable, not lookupable — "reverse SATURN" web-searched returned a random
        # number that then shipped WITH a citation (measured live). Same for chat,
        # clarify, compose and cancelled: the world holds no evidence for them.
        ungapable_obs = {ob.id for ob, row in zip(obligations, rows, strict=True)
                         if row["lane"] in ("chat", "clarify", "arithmetic", "compose", "cancelled", "intake", "machine", "knowledge")}
        # "knowledge" added by consensus-5 LAW 1: a definition or antonym is a
        # model-memory deliverable — its gaps are never numeric web lookups
        # (measured: "opposite of expand" gap-searched and harvested a stray 123;
        # "define latency" grounded to a web-gap receipt).
        gaps = [g for g in gaps if _normalize_ref(g.get("obligation_id", "")) not in ungapable_obs]
        for g_n, gap in enumerate(gaps[:2], 1):  # at most two gap lookups a turn
            owner = _normalize_ref(gap.get("obligation_id", "")) or "turn"
            if owner in cancelled_effects or (cancelled_effects and owner == "turn"):
                out.append(f"  ! deny gate: no gap lookup for cancelled {owner}")
                continue
            query = str(gap["query"]).strip()
            if not stamps["web"]:
                # BLOCK-C seam 1 (door 5): a torn stamp forbids gap lookups too —
                # the gap is DECLARED, never silently fetched.
                out.append(f"  * stamp: web torn — no gap lookup for {owner}; the gap stays declared")
                continue
            out.append(f"  ? evidence gap for {owner}: {str(gap.get('need', '')).strip() or query}  ->  {query}")
            check_tool_call(fork, "web.search", "net.fetch", {"q": query})
            try:
                hits = runner.run("web.search." + str(provider), cached_fetch, query)
            except Exception as exc:
                out.append(f"  ! gap lookup failed: {exc}")
                continue
            gap_refs: list[str] = []
            for h_n, hit in enumerate(hits, 1):
                ref = f"{owner}-gap{g_n}w{h_n}"
                receipts[ref] = f"{_clean(hit['title'])} — {_clean(hit['snippet'])} ({hit['url']})"
                evidence_lines.append(f"{ref}: {receipts[ref]}")
                gap_refs.append(ref)
            if gap_refs and not any(re.search(r"\d", receipts[ref]) for ref in gap_refs):
                # Snippets answered the query but carry no digits — a chart-page pattern.
                # Escalate one level: the top hit's body, through the same door.
                page_url = str(hits[0]["url"])
                _alt_urls = [str(h.get("url", "")) for h in hits[1:3] if h.get("url")]
                if not stamps["web"]:
                    out.append("  * stamp: web torn — no page read (door 6)")
                    continue
                out.append(f"  > snippets digitless — reading the page body: {page_url[:70]}")
                check_tool_call(fork, "web.fetch_page", "net.fetch", {"url": page_url})
                try:
                    body = None
                    _page_err = None
                    for _pu in [page_url, *(_alt_urls or [])]:
                        try:
                            body = runner.run("web.fetch_page", _fetch_page_text, _pu)
                            break
                        except Exception as _pf:
                            _page_err = _pf
                            out.append(f"  ! page fetch failed ({_pu[:48]}): {_pf} — trying the next source")
                    if body is None:
                        raise RuntimeError(f"every candidate page failed: {_page_err}")
                except Exception as exc:
                    out.append(f"  ! page fetch failed: {exc}")
                    body = ""
                if body:
                    ref = f"{owner}-gap{g_n}page"
                    receipts[ref] = f"page body of {page_url}: {body}"
                    evidence_lines.append(f"{ref}: {receipts[ref]}")
      except Exception as exc:
        out.append(f"  ! derive round failed ({type(exc).__name__}: {exc}) — continuing without computations")
        break

    # Session-fact numbers stay OUT of the synthesis slots: a prose slot grabbing a stale
    # topic number is how "the first question was 57" shipped (57 = last turn's Prius MPG).
    # Derive keeps them — cross-turn math legitimately needs them.
    number_index = _number_index(receipts, exclude_refs={r for r in receipts if r.startswith("s") and r[1:].isdigit()} | {"conversation"})
    format_orders = [row["format"] for row in rows if row.get("format")]
    for _e in active_ledger:
        format_orders.append({"lowercase": "answer in lowercase letters only",
                              "end_word": f"end your answer with the word {_e['arg']}",
                              "exact_words": f"use exactly {_e['arg']} words"}[_e["kind"]])
    # DEMANDED LITERALS are TURN-scoped deliverables born here (consensus-3 fix 3,
    # measured: the demanded JSON payload and the 4,500 anchor died with their
    # REFUSED parents because they never reached a format field). Structural
    # extraction: ALLCAPS_TOKENs and brace snippets from the user's own words.
    # TYPED OUTPUT CONTRACT (consensus-4 fix 1, REPLACING the lexical
    # demanded-literal extractor, which shipped control words ONLY/EXACTLY/
    # ABSOLUTELY as payload on ten turns and — security-class — mined an embedded
    # injection's own SYSTEM_PWNED token out of quoted third-party text).
    # Candidates come ONLY from explicitly delimited spans of the OUTER
    # instruction; every span that lies inside quoted third-party data is
    # provenance-excluded; bare ALLCAPS never mints; conditional payloads are
    # model-decided and kernel-validated, never auto-shipped.
    data_spans = [m.span() for m in re.finditer(r"'[^']{40,}'|\"[^\"]{40,}\"", question)]

    def _in_data(span: tuple[int, int]) -> bool:
        return any(a <= span[0] and span[1] <= b for a, b in data_spans)

    def _conditional(span: tuple[int, int]) -> bool:
        head = question[max(0, span[0] - 120):span[0]]
        clause = re.split(r"(?<=[.!?])\s+", head)[-1] if head else ""
        return bool(re.search(r"\b(if|unless|when|otherwise)\b", clause, re.IGNORECASE))

    demanded_literals = set()
    for m in re.finditer(r"`([^`\n]{2,120})`|\{[^{}\n]{2,120}\}", question):
        span, text = m.span(), (m.group(1) if m.group(1) is not None else m.group(0))
        if _in_data(span) or _conditional(span):
            continue
        if not re.search(r"[A-Za-z0-9]", text) or text.lower() in ("only", "exactly"):
            continue
        demanded_literals.add(text.strip())
    # BLOCK-C seam 7: a bare-ALLCAPS token directly governed by an explicit
    # "output exactly:" head is a demanded literal (t57 class) — the head is the
    # OUTER instruction, so the injection-mining ban on free-floating ALLCAPS
    # stands (data-span and conditional exclusions still apply; a conditional
    # payload stays model-decided).
    for m in re.finditer(r"(?:output|reply|say|type|print|write)\s+exactly\s*[:,]?\s*"
                         r"([A-Z][A-Z0-9_-]{1,39})(?![a-z])", question):
        span = m.span(1)
        if _in_data(span) or _conditional(span):
            continue
        demanded_literals.add(m.group(1))
    # ROUND-002 CLAUSE 1 — CAPTURE BY POSITION, NOT BY PHRASING.
    # The two patterns above are surface templates: they need a backtick/brace span, or the
    # emission verb glued to `exactly` with the payload glued to that. Ordinary human wording
    # ("Reply WITH exactly THESE BYTES AND NOTHING ELSE: EMBER-X604") satisfies neither, and
    # `_repeat_contract` needs a fullmatch over the whole question plus a quoted payload. All
    # three missed, so a byte-exact turn had no captured target and its correctness rode on
    # model judgment — turn 003 passed and turn 005 failed on the SAME instruction sentence.
    #
    # The boundary is POSITIONAL: an exact-output DIRECTIVE (emission verb + exactness
    # qualifier, one sentence) introduces a payload, and the payload is the first literal-shaped
    # token that directive introduces. Keying on position is what stops this being a third
    # template — a distractor token elsewhere in the message is excluded by construction, which
    # is the over-capture case the adversarial seat raised ("Return ONLY the final numeric
    # total" whose preamble mentions SILVER-MARKET must still ship the computed total).
    # `_in_data` / `_conditional` still apply: quoted third-party data and if/unless payloads
    # never capture, so the F1 injection boundary is untouched.
    #
    # KNOWN BOUNDARY: the payload must carry a digit (quoted payloads are already covered by the
    # backtick/brace pattern above). A pure-lowercase-prose echo target is not separable from the
    # directive's own words by position alone; widening that is a future round, not a guess here.
    for m in _EXACT_OUTPUT_DIRECTIVE_RE.finditer(question):
        payload, span = m.group(1), m.span(1)
        if _in_data(span) or _conditional(span):
            continue
        demanded_literals.add(payload)

    def _build_synth_user() -> str:
        # Rebuildable because lane repair (below) can ADD evidence between rounds; the
        # prompt must always show the numbers table the receipts actually hold now.
        base = (
            "User message: " + question + "\n\nObligations:\n" + obligations_block
            + "\n\n" + _numbers_block(number_index)
            + "\n\nEvidence:\n" + evidence_block()
        )
        if format_orders:
            # The user's output-format order travels INTO synthesis instead of dying as
            # scaffolding the model never sees (measured live: "Output ONLY the year"
            # ignored four times running — the order never reached the prompt).
            base += (
                "\n\nTHE USER'S FORMAT ORDER — each claim's text must BE the demanded output,"
                " nothing wrapped around it: " + "; ".join(format_orders)
            )
        return base

    synth_user = _build_synth_user()
    claims: list[TypedClaim] = []
    claim_owner: list[str] = []
    synth_rejections: list[str] = []
    # ROUND-008 (fail-closed exact_words): per obligation, (best_effort_words, required_words)
    # for a claim that WAS produced but died on an active HARD exact_words contract after the
    # informed retries were exhausted. Threading it to the seam-10 liveness terminal keeps the
    # failure banner truthful ("format unsatisfied", never "no claim produced") without
    # creating a new completion authority.
    _format_unsatisfied: dict[str, tuple[int, int]] = {}
    # ROUND-011 UX closure: every candidate the word gate rejected for an underrun, per
    # obligation, in attempt order — the pool the "closest attempt" fallback selects from
    # mechanically (abs(count - N), tie -> earliest attempt). The texts stay model-authored
    # and are displayed verbatim; the kernel never edits, ranks semantically, or merges them.
    _xw_rejected: dict[str, list[str]] = {}
    needs_grounding: set[str] = set()
    rejected_nums: dict[str, set[str]] = {}  # per obligation: numbers in claims Law 2 rejected
    synthesis_crash = ""
    for synth_round in (1, 2, 3):
      if synth_round >= 2:
        grounding = sorted(needs_grounding)[:2] if fetch is not None else []
        if not grounding and (not synth_rejections or claims):
            break
        # LANE REPAIR AT THE KERNEL (measured live: "Eiffel Tower year" labeled
        # "knowledge" by an 8B extractor died as "unanswerable"): a numeric answer was
        # attempted for an obligation holding NO evidence. The kernel's own law says a
        # checkable number grounds in evidence — so the kernel fetches the evidence the
        # label skipped, instead of trusting the label and letting the answer die.
        for gob_id in grounding:
            gob = next((o for o in obligations if o.id == gob_id), None)
            if gob is None or gob_id in cancelled_effects:
                continue
            if not stamps["web"]:
                # BLOCK-C seam 1 (door 7 — the t19 leak): lane repair NEVER re-arms
                # a torn web. The claim stays ungrounded and ships marked, or dies
                # by LAW 2 — it does not get to fetch.
                out.append(f"  * stamp: web torn — lane repair for {gob_id} denied; no fetch")
                continue
            out.append(f"  > lane repair: {gob_id} answered with ungroundable numbers — searching: {gob.description[:60]}")
            check_tool_call(fork, "web.search", "net.fetch", {"q": gob.description})
            try:
                hits = runner.run("web.search." + str(provider), cached_fetch, gob.description)
            except Exception as exc:
                out.append(f"  ! lane-repair search failed: {exc}")
                continue
            for n, hit in enumerate(hits, 1):
                ref = f"{gob_id}-web{n}"
                receipts[ref] = f"{_clean(hit['title'])} — {_clean(hit['snippet'])} ({hit['url']})"
                evidence_lines.append(f"{ref}: {receipts[ref]}")
            if hits:
                ob_urls.setdefault(gob_id, str(hits[0]["url"]))
        if grounding:
            number_index = _number_index(receipts, exclude_refs={r for r in receipts if r.startswith("s") and r[1:].isdigit()} | {"conversation"})
            synth_user = _build_synth_user()
        # REPAIR, same pattern as derive: every rejected claim's exact reason goes back
        # once. Measured live: rejected prose fell straight to verbatim floors when one
        # named retry would have saved the composition (the Civic/Prius and F-150 turns).
        if synth_rejections:
            synth_user = synth_user + "\n\nYour previous claims were REJECTED — correct each or drop it:\n" + "\n".join(synth_rejections)
        synth_rejections = []
        needs_grounding = set()
      try:
        data = _model_json(runner, "model.synthesize", _SYNTH_SYSTEM, synth_user)
        for row in data.get("claims", []):
            if isinstance(row, str):
                # Schema-shape tolerance (consensus item 6B, measured: the model returned
                # the complete correct 25-row table as a bare string and the ingester
                # crashed on .get) — a bare string is a piece for the first open
                # compose/chat obligation, never a crash.
                home_ob = next((o for o in obligations if o.status == "open" and o.kind in ("compose", "chat")),
                               next((o for o in obligations if o.status == "open"), None))
                row = {"obligation_id": home_ob.id if home_ob else "", "text": row, "type": "conversational"}
                out.append("  * bare-string claim tolerated as a compose piece")
            ctype = str(row.get("type", "")).strip().lower()
            text = str(row.get("text", "")).strip()
            # The law's own type table, imported — a duplicated allowlist here is how
            # "conversational" was legal in the kernel and rejected by the harness (measured).
            if not text:
                # The ONLY honest rejection: no answer was produced. Loud, never silent.
                out.append(f"  ! claim row rejected (no text; type={ctype!r})")
                continue
            if ctype not in CLAIM_TYPES:
                # ROUND-006, council-unanimous (ingestion-default). The model produced a real
                # answer but omitted or garbled the type enum. Turn 009 died exactly here:
                # {"text": "Warm air rises cooler."} with no type was hard-rejected, render saw
                # zero claims, and liveness shipped a dead-turn banner that FALSELY read "no
                # claim produced" — a claim WAS produced. Default the type so the claim
                # RE-VALIDATES through every downstream gate (grounding, the Law-2 numeric
                # re-gate at the fabricated-number backstop below, word-count/best-fit render)
                # instead of being destroyed for a missing tag. Authoring lanes get
                # "conversational" (an authored deliverable); every other lane gets the
                # lowest-trust non-grounded type "unverified", so an ungrounded number in it is
                # still REFUSED by Law 2, never shipped as fact. This is a REBIND to the
                # bare-string default at 2785, not a new bypass: the claim goes THROUGH
                # validation, never around it. The competing "keep-any-rejected" repair — which
                # would let such a claim skip validation as best-effort — was refuted
                # unanimously on the number probe: it would ship the ungrounded value marked as
                # observed.
                _own_id = _normalize_ref(row.get("obligation_id", ""))
                _own_ob = next((o for o in obligations if o.id == _own_id), None)
                _defaulted = ("conversational"
                              if (_own_ob is not None and _own_ob.kind in ("compose", "chat"))
                              else "unverified")
                out.append(f"  * claim type defaulted {ctype!r} -> {_defaulted!r} "
                           f"(model omitted a valid type; claim re-validates downstream)")
                ctype = _defaulted
            owner_id = _normalize_ref(row.get("obligation_id", ""))
            owner_ob = next((o for o in obligations if o.id == owner_id), None)
            if owner_ob is not None and owner_ob.status != "open":
                demanded = (any(text in fo for fo in format_orders)
                            or any(lit in text for lit in demanded_literals)
                            or (format_orders and text in question))
                if text and demanded:
                    # TURN DELIVERABLE (consensus-2 fix 3, measured: the ONLY thing the
                    # user demanded — SMART_HOME_MUTATION_PREEMPTED — was destroyed with
                    # its cancelled parent): a claim the user's own format order names
                    # outlives the obligation it was attached to.
                    out.append(f"  * format-ordered deliverable survives its settled parent: {text[:50]}")
                    home_ob = next((o for o in obligations if o.status == "open"), None)
                    claims.append(TypedClaim(text=text, ctype="conversational", ref=""))
                    claim_owner.append(home_ob.id if home_ob else owner_id)
                    continue
                # The obligation already settled by declaration (cancelled, failed lookup,
                # no key) — a model-memory "answer" for it is the fabrication class wearing
                # a friendly face (measured live: the CANCELLED gold lookup shipped a price).
                out.append(f"  ! claim for settled {owner_id} dropped: {text[:60]}")
                continue
            # TOKEN EXPANSION: the kernel substitutes evidence numbers and derives the
            # citation from the tokens themselves. One sentence, one source — a claim
            # mixing tokens from different receipts is rejected with the reason named.
            expanded, token_refs, token_err = _expand_tokens(text, number_index)
            if not token_err and not token_refs and ctype == "observed" and has_quantity(text):
                repaired = _retokenize(text, number_index)
                if repaired != text:
                    r_exp, r_refs, r_err = _expand_tokens(repaired, number_index)
                    if not r_err and r_refs:
                        out.append(f"  + notation repaired: literal evidence values re-tokenized ({', '.join(sorted(r_refs))})")
                        expanded, token_refs, token_err = r_exp, r_refs, r_err
                        text = expanded
            if token_err and "unknown number token" in token_err:
                remapped = _repair_unknown_tokens(text, number_index)
                if remapped != text:
                    r_exp, r_refs, r_err = _expand_tokens(remapped, number_index)
                    if not r_err and r_refs:
                        out.append(f"  + notation repaired: value-named tokens remapped ({', '.join(sorted(r_refs))})")
                        expanded, token_refs, token_err = r_exp, r_refs, r_err
                        text = expanded
            if token_err:
                out.append(f"  ! claim rejected: {token_err}: {text[:70]}")
                synth_rejections.append(f"- {text[:90]}: {token_err} — use only tokens from the NUMBERS table")
                rejected_nums.setdefault(owner_id, set()).update(re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", "")))
                if owner_id and ob_lane.get(owner_id) not in ("arithmetic", "compose", "chat", "clarify") \
                        and not any(r.startswith(owner_id + "-") for r in receipts):
                    needs_grounding.add(owner_id)
                continue
            # TOKEN HYGIENE (mixed-40 fresh S14 live: "{n74402.55}"). The model can
            # type a pseudo-token with the VALUE baked in ("{n74402.55}"), which
            # _TOKEN_RE (\{n\d+\}) does NOT match — so _expand_tokens skips it, raises
            # no unknown-token error, and the raw braces leak into the served answer as
            # "[unverified - model memory]". After expansion + every repair pass, ALL
            # valid {nK} slots are substituted away, so any residual {n...} shape is a
            # hallucinated reference carrying an unfounded number — reject the claim so
            # the value faces a real lookup instead of shipping raw.
            if re.search(r"\{n(?![A-Za-z_])[^}]*\d[^}]*\}", expanded):
                out.append(f"  ! claim rejected — malformed number token leaked: {expanded[:60]}")
                synth_rejections.append(f"- {text[:80]}: malformed token — use only {{nK}} indices from the NUMBERS table")
                if owner_id and ob_lane.get(owner_id) not in ("arithmetic", "compose", "chat", "clarify") \
                        and not any(r.startswith(owner_id + "-") for r in receipts):
                    needs_grounding.add(owner_id)
                continue
            if token_refs and not token_err:
                # PRO / REFERENT GAP AT SYNTHESIS (delta review, T30 owner). The
                # derive path already refuses a computation whose only operand is a
                # user-MESSAGE number for a non-arithmetic obligation (the "TURN 1"
                # ordinal that became a price, referent-gap law above). The SAME
                # fabrication reaches the served answer through the SYNTHESIZE path:
                # a recall/knowledge/web_lookup claim that cites ONLY user-message
                # numbers names a quantity no lookup ever obtained. Mirror the guard
                # here so the synth door is not the number bus's back entrance.
                # Fire ONLY when the model itself TYPED a {nK} quantity token — an
                # identifier restatement ("RX-731, 2FA, Neo4j") whose digit was
                # re-tokenized from the user's own text is an enumeration, not a
                # quantity binding, and must survive.
                if _TOKEN_RE.search(text) and token_refs <= {"user"} and ob_lane.get(owner_id) not in (
                        "arithmetic", "compose", "chat", "clarify"):
                    out.append(f"  ! claim rejected — referent gap: '{text[:50]}' binds only "
                               "message-text numbers; the named quantity has no receipt")
                    synth_rejections.append(f"- {text[:80]}: only user-message numbers — the value was never obtained")
                    continue
                # ASSERTED-UNIT GROUNDING (delta review K1, T22). A number's unit is a
                # claim about the world that its own source must carry. "Sarajevo is
                # {n1} RUB" for a CAPITAL ask binds a POPULATION (source window
                # "population 275524") under a fabricated currency — RUB appears
                # nowhere near the number. If the token the claim writes immediately
                # after a {nK} is a unit/currency the number's OWN local source window
                # does not carry, the unit is invented. (Capital has no unit stem;
                # this needs none — it grounds whatever unit the claim asserts.)
                _by_tok_u = {r["token"]: r for r in number_index}
                _unit_bad = None
                for _um in re.finditer(r"\{(n\d+)\}\s*([^\s.,;]+)", text):
                    _ur = _by_tok_u.get(_um.group(1))
                    if not _ur or not any(w in _ur.get("ref", "") for w in _WORLD_REF_MARKS):
                        continue
                    _uw = _um.group(2).strip()
                    _is_unit = (bool(re.fullmatch(r"[A-Z]{3}", _uw)) or _uw in ("%", "$", "€", "£", "°")
                                or any(_uw.lower() == u for _a, us in _ATTR_UNIT_STEMS for u in us))
                    if _is_unit and _uw.lower() not in _value_window(
                            _ur.get("context", ""), _ur["value"]).lower():
                        _unit_bad = (_ur["value"], _uw)
                        break
                if _unit_bad:
                    _uv, _uu = _unit_bad
                    out.append(f"  ! claim rejected — fabricated unit: {_uv} {_uu}, but that "
                               f"number's source carries no '{_uu}': {text[:50]}")
                    synth_rejections.append(f"- {text[:70]}: unit '{_uu}' on {_uv} is not in that number's source window")
                    if owner_id and ob_lane.get(owner_id) not in ("arithmetic", "compose", "chat", "clarify"):
                        needs_grounding.add(owner_id)
                    continue
                # ENTITY-VALUE OWNERSHIP (operator live round; Terra/Flash consult):
                # a value bound into an entity-named slot must come from a source
                # that NAMES that entity. Rule 1 (world-source): a WORLD-receipt
                # token (-web/-page/-gap) whose context names NONE of the claim's
                # entities is a foreign binding (ETH=19 from a BTC snippet). Rule 2
                # (cross-obligation): a derive/calc token owned by a DIFFERENT
                # obligation is another slot's computation (Prague=69 [ob2-calc2]).
                # Shared-comparison snippets ("Prague and Vienna both 69") pass —
                # the context names both. calc/user tokens for THIS obligation are
                # exempt (they carry their own provenance).
                _tok_ids = _TOKEN_RE.findall(text)
                # SCOPE (operator live round): the entity/value failures were ALL
                # explicit labeled per-entity contracts — Riga=, ETH=, Prague=.
                # Fire ONLY when the turn declares such a contract AND this claim
                # is a single-value LABEL=value slot; the label is the entity. This
                # exempts every free-prose harvest/duration/forecast test and the
                # single-entity ask, and targets exactly the confusion surface.
                # (Free-prose multi-entity binding is the signed value-vector work.)
                _labeled_contract = any(re.search(r"[A-Za-z][\w/ ]*=", fo) for fo in format_orders)
                _slot_m = re.match(r"\s*([A-Za-z][\w /-]{1,24}?)\s*=\s*\{n\d+\}\s*$", text)
                _is_attribution = (_labeled_contract and _slot_m is not None
                                   and ob_lane.get(owner_id) in ("web_lookup", "knowledge", "recall"))
                _ents = _slot_entities(_slot_m.group(1)) if _slot_m else set()
                if not _ents and _slot_m:
                    # a label like "Kaunas" or "ETH" that _slot_entities filtered
                    # (e.g. it looked like a unit): use the raw label token itself.
                    _lbl = _slot_m.group(1).strip()
                    if re.fullmatch(r"[A-Za-z][\w/-]{1,24}", _lbl):
                        _ents = {_lbl}
                if _is_attribution and _ents:
                    _by_tok = {r["token"]: r for r in number_index}
                    _used = [_by_tok[m] for m in _tok_ids if m in _by_tok]
                    _bad_bind = None
                    for _tk in _used:
                        _ref = _tk.get("ref", "")
                        # the WHOLE receipt (title + body), not the +/-40 window —
                        # a snippet names its entity once, often in the title
                        # (Terra/Flash: don't false-reject a value whose entity is
                        # named away from the number).
                        _ctx_ents = _slot_entities(receipts.get(_ref, "") or _tk.get("context", ""))
                        _is_world = any(m in _ref for m in ("-web", "-page", "-gap"))
                        _is_calc = "-calc" in _ref
                        if _is_world and _ents and not (
                                _with_ticker_aliases(_ents) & _with_ticker_aliases(_ctx_ents)):
                            _bad_bind = ("no source names this entity", _tk)
                            break
                        if _is_calc and owner_id and not _ref.startswith(owner_id + "-"):
                            _bad_bind = ("value is another slot's computation", _tk)
                            break
                    if _bad_bind:
                        _why, _tk = _bad_bind
                        out.append(f"  ! claim rejected — entity/value binding ({_why}): "
                                   f"{text[:50]} <- {_tk.get('value')} from {_tk.get('ref')}")
                        synth_rejections.append(f"- {text[:70]}: {_why} — bind {'/'.join(sorted(_ents))} only from a source that names it")
                        if owner_id and ob_lane.get(owner_id) not in ("arithmetic", "compose", "chat", "clarify"):
                            needs_grounding.add(owner_id)
                        continue
                # COMPOUND-IDENTIFIER MAPPING GUARD (Round-018, architecture ruling C):
                # an observed web_lookup claim that maps an opaque compound identifier
                # (XXXX-NNN) to a locator URL is FAIL-CLOSED without structured evidence.
                # Free-prose world receipts cannot prove the mapping because the
                # relation/attribute between identifier and URL is not authoritatively
                # owned by any existing canonical structure. Detection triggers rejection
                # only — no acceptance path exists for this class until a typed
                # WorldBinding contract is added (deferred).
                _compound_re = re.compile(
                    r"\b(?:[A-Z]{2,}-\d[\w-]*|[A-Z]{2,}\d[\w-]*|"
                    r"\d[A-Z]{2,}\b|[A-Z][a-z]+\d\w*)\b"
                )
                _url_re = re.compile(r"https?://\S+")
                _compound_ids = _compound_re.findall(expanded)
                _urls = _url_re.findall(expanded)
                if (not token_err
                        and ob_lane.get(owner_id) == "web_lookup"
                        and ctype == "observed"
                        and _compound_ids and _urls):
                    out.append(f"  ! compound-identifier mapping fail-closed: the URL mapping for "
                               f"{', '.join(_compound_ids)} cannot be verified from free-prose web "
                               f"evidence — structured binding required: {expanded[:60]}")
                    synth_rejections.append(
                        f"- {expanded[:80]}: opaque compound identifier to URL mapping is "
                        "not supported from free-prose web evidence"
                    )
                    if owner_id and ob_lane.get(owner_id) not in ("arithmetic", "compose", "chat", "clarify"):
                        needs_grounding.add(owner_id)
                    continue
            if (token_refs and not token_err
                    and ob_lane.get(owner_id) in ("arithmetic", "web_lookup", "machine", "recall")
                    and re.fullmatch(r"[\d\s.,+\-*/()]+", expanded) and re.search(r"[+\-*/]", expanded)):
                # EXPRESSION-AS-ANSWER recovery (consensus fix 2, measured live twice:
                # "50 * 4" and "1.1687 / 3" shipped unevaluated as terminal answers): a
                # bare arithmetic expression is a well-formed computation in the wrong
                # slot — route it through the SAME arithmetic door derive uses; the value
                # ships with a real executed-calc receipt. Compose/chat obligations are
                # exempt: "output the formula" stays a formula there.
                try:
                    value = runner.run("kernel.arith", _eval_arith, expanded)
                except Exception:
                    value = None
                if value is not None:
                    ref_new = f"{owner_id or 'turn'}-calcE{len(receipts)}"
                    receipts[ref_new] = (
                        f"computed locally: expression claim evaluated: {expanded} = {_fmt_num(value)};"
                        " sources: " + ", ".join(sorted(token_refs))
                    )
                    evidence_lines.append(f"{ref_new}: {receipts[ref_new]}")
                    out.append(f"  + expression claim evaluated through the arithmetic door: {expanded} = {_fmt_num(value)}")
                    expanded = _fmt_num(value)
                    token_refs = {ref_new}
            if token_refs:
                # BLOCK-C seam 6 (harvest binding): before the tokens are
                # accepted, every WORLD-receipt number must attach the asked
                # attribute's unit in its own source context (t30: "next 14
                # days" is not a temperature), and a latest-version ask needs
                # latest-field context (t31). Presence is not grounding.
                _used_vals = _number_tokens(expanded)
                _tok_rows = [r for r in number_index
                             if r["ref"] in token_refs and r["value"] in _used_vals]
                # OBLIGATION-SCOPED UNIT ASK (mixed-40 root fix, CH1/CH7). The unit
                # gate must arm on the attribute THIS obligation asked, not the whole
                # turn's question — a co-asked "temperature" was arming the ° gate on
                # an FX/price number (S4 EUR/USD 1.17011, S13 BTC $74,402.55) and
                # refusing a value that carries its OWN unit. Scope to the obligation's
                # own description + this claim's own slot text. Freshness keeps the
                # full ask: a "latest version" turn does not cross-contaminate (only
                # version turns arm it) and the question carries the clean "current
                # version" phrasing the obligation desc's "Node.js" dot breaks (t37).
                _ask_unit = (ob_desc.get(owner_id, "") + " " + text)
                _ask_full = (ob_desc.get(owner_id, "") + " " + question)
                _gap = (_harvest_unit_gap(_ask_unit, _tok_rows, receipts)
                        or _freshness_gap(_ask_full, _tok_rows, receipts))
                if _gap:
                    out.append(f"  ! claim rejected: {_gap}: {text[:60]}")
                    synth_rejections.append(f"- {text[:80]}: {_gap} -- cite a source that attaches it")
                    if owner_id:
                        harvest_rejected[owner_id] = _gap
                    continue
                harvest_rejected.pop(owner_id, None)
                # Per-token citations (D10): a comparative sentence may draw from several
                # receipts; each number's source travels with its token, so the citation
                # is the union, rendered visibly as receipt:a+b.
                text = expanded
                ctype = "observed"
                ref = ""
                refs = tuple(sorted(token_refs))
            elif ctype == "observed":
                # An observed claim with no token has no evidence numbers to carry and no
                # citation the kernel can derive — the model typed digits or cited prose.
                # Digitless observed prose is re-typed unverified (wears its mark);
                # digit-carrying ones are rejected: evidence numbers travel by token only.
                # COMPOUND-IDENTIFIER MAPPING GUARD (Round-018): a tokenless web_lookup
                # claim mapping an identifier (NOVA-71) to a URL is fail-closed without
                # structured evidence — it cannot survive as unverified prose either.
                _compound_re = re.compile(
                    r"\b(?:[A-Z]{2,}-\d[\w-]*|[A-Z]{2,}\d[\w-]*|"
                    r"\d[A-Z]{2,}\b|[A-Z][a-z]+\d\w*)\b"
                )
                _url_re = re.compile(r"https?://\S+")
                if (ob_lane.get(owner_id) == "web_lookup"
                        and _compound_re.search(text) and _url_re.search(text)):
                    out.append(f"  ! compound-identifier mapping fail-closed: the URL mapping for "
                               f"the identifier cannot be verified from free-prose web "
                               f"evidence — structured binding required: {text[:60]}")
                    synth_rejections.append(
                        f"- {text[:80]}: opaque compound identifier to URL mapping is "
                        "not supported from free-prose web evidence"
                    )
                    if owner_id and ob_lane.get(owner_id) not in ("arithmetic", "compose", "chat", "clarify"):
                        needs_grounding.add(owner_id)
                    continue
                if has_quantity(text):
                    if ob_lane.get(owner_id) == "arithmetic":
                        # The user's own literal arithmetic ("inputs from user") is
                        # authoritative — drop the model's rounded restatement so the
                        # extract calc grounds the slot (mixed-40 fresh S10).
                        _extract_calc = next((r for r in receipts
                                              if r.startswith(owner_id + "-calc")
                                              and "inputs from user" in receipts.get(r, "")), None)
                        if _extract_calc:
                            out.append(f"  * model literal for {owner_id} dropped — extract calc {_extract_calc} is the grounded answer")
                            continue
                        _any_calc = next((r for r in receipts
                                          if r.startswith(owner_id + "-calc")
                                          and "computed locally" in receipts.get(r, "")), None)
                        if owner_id in derive_attempted and not _any_calc:
                            # the kernel TRIED to compute this quantity and every
                            # expression was refused (collapsed operands, unit mismatch,
                            # foreign cross-slot operand — S7 ADA/ADA = 1.0). With no
                            # sound calc, the model's memory literal is the same
                            # fabrication the derive gate just refused: declare a gap,
                            # never ship a confident wrong number.
                            out.append(f"  ! model literal for {owner_id} refused — its derivation was refused and produced no sound value")
                            synth_rejections.append(f"- {text[:80]}: the derivation was refused — declare a gap, not a memory number for a live-value computation")
                            continue
                        # Either a self-contained computation the tool could not express
                        # (vowel counts, string work) or a live-value derivation that DID
                        # compute (a ratio) — the model's own result is the author, so it
                        # ships WEARING ITS MARK. A derive ratio is flagged unverified
                        # rather than grounded because the register-blind model may have
                        # mis-picked the operand NUMBER (right source, wrong figure).
                        out.append(f"  * model-computed result for {owner_id} ships unverified: {text[:60]}")
                        claims.append(TypedClaim(text=text, ctype="unverified", ref=""))
                        claim_owner.append(owner_id)
                        continue
                    out.append(f"  ! claim rejected: evidence numbers must travel by token: {text[:70]}")
                    synth_rejections.append(f"- {text[:90]}: rewrite each evidence number as its {{token}} from the NUMBERS table")
                    rejected_nums.setdefault(owner_id, set()).update(re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", "")))
                    if owner_id and ob_lane.get(owner_id) not in ("arithmetic", "compose", "chat", "clarify") \
                            and not any(r.startswith(owner_id + "-") for r in receipts):
                        needs_grounding.add(owner_id)
                    continue
                home_ref = next(
                    (r for r in receipts
                     if r.startswith(owner_id + "-") and len(text) >= 2 and text in receipts[r]),
                    "",
                )
                if home_ref:
                    # WORD-VALUED grounding (measured live: reverse ITALY executed
                    # kernel-side, the receipt held 'YLATI', and the model's correct
                    # digitless claim was force-marked memory with the obligation left
                    # open). The token protocol grounds numbers; a digitless claim
                    # contained VERBATIM in its own obligation's receipt is grounded by
                    # containment — deterministic, same doctrine as the verbatim floor.
                    out.append(f"  + digitless claim grounded by containment in {home_ref}")
                    ref = home_ref
                    refs = ()
                else:
                    ctype = "unverified"
                    ref = ""
                    refs = ()
            else:
                ref = ""
                refs = ()
            owner_lane = next((r["lane"] for r, ob in zip(rows, obligations, strict=True) if ob.id == owner_id), "")
            eq_ok = True
            # DM-2 Track K (T29 "26 x 17 is 225" shipped grounded): digit-flanked
            # x/× normalizes to * (flash M6: ONLY digit-flanked — "2x+3" stays a
            # variable), and "is/equals" between numeric sides is an equation.
            _eq_text = re.sub(r"(\d)\s*[x\u00d7]\s*(\d)", r"\1 * \2", expanded)
            _eq_text = re.sub(r"([\d)])\s+(?:is|equals)\s+(-?\d)", r"\1 = \2", _eq_text)
            for eq in re.finditer(r"([\d\s.,+\-*/()]+?)=\s*(-?[\d][\d,]*(?:\.\d+)?)", _eq_text):
                lhs, rhs = eq.group(1).strip(), eq.group(2).replace(",", "")
                if not re.search(r"[+\-*/]", lhs) or not re.search(r"\d", lhs):
                    continue
                try:
                    lhs_val = _eval_arith(lhs)
                    stated = float(rhs)
                except Exception:
                    continue
                places = len(rhs.split(".")[1]) if "." in rhs else 0
                if round(lhs_val, places) != round(stated, places):
                    # DISPLAYED-EQUATION RECOMPUTATION (consensus-3 fix 4, measured:
                    # "12.80 + 12.50 = 26" shipped receipted; it is 25.30). A claim
                    # that asserts arithmetic asserts something the kernel can check.
                    out.append(f"  ! claim rejected: its own equation is false ({lhs} = {_fmt_num(lhs_val)}, not {rhs})")
                    synth_rejections.append(f"- {expanded[:80]}: the equation is false — {lhs} = {_fmt_num(lhs_val)}")
                    eq_ok = False
                    break
            if not eq_ok:
                continue
            _intake_shaped = bool(re.search(r"\b(?:store|record|remember|note)\b|=", question, re.IGNORECASE))
            if (ob_lane.get(owner_id) not in ("chat", "clarify")
                    and (ob_lane.get(owner_id) != "intake" or not _intake_shaped)
                    and " ".join(text.lower().split()) in " ".join(question.lower().split())
                    and len(text) >= 20):
                # BLOCK-C seam 8 (t70): compose lost its exemption — a >=20-char
                # verbatim restatement of the ask is never a composition either
                # (the live echo of "Check for a literal file..." shipped as the
                # answer to an action request the surface cannot perform).
                # TASK-ECHO (consensus-3, amendment 1 honored: kind-scoped, never a
                # blanket byte-equality ban — greetings stay human). Restating a
                # research/transform/comparison request is not answering it, with or
                # without digits (measured: request lines shipped as [receipt:user]
                # answers on the vet, physio, and marathon turns).
                out.append(f"  ! task-echo rejected: restates the ask: {text[:60]}")
                synth_rejections.append(f"- {text[:80]}: this restates the request — answer it or name the gap")
                continue
            if re.fullmatch(r'\s*\{\s*"error"\s*:.*\}\s*', text, re.DOTALL):
                # DM-2 Track K (T40): a provider/model error blob shipped as the
                # user-visible answer — it is a failure record, not an answer.
                out.append(f"  ! claim rejected: raw error object is not an answer: {text[:60]}")
                synth_rejections.append(f"- {text[:80]}: an error object is not an answer — answer or name the failure")
                continue
            if (_NEG_EXIST_RE.search(text) and ctype in ("observed", "unverified")
                    and not any(r.startswith(owner_id + "-") for r in receipts)):
                # BLOCK-C seam 8 (t73): "X does not exist / not found" is a WORLD
                # observation — with no effect on the tape this turn it is an
                # over-claim, and the correct terminal is the capability gap, not
                # an invented negative fact. Effect-backed not-found results
                # (a search that ran and found nothing) pass through untouched.
                out.append(f"  ! negative-existence claim with no effect behind it — refused as unverifiable: {text[:60]}")
                _own_ob = next((o for o in obligations if o.id == owner_id), None)
                if _own_ob is not None and _own_ob.status == "open":
                    txn.declare_refused(owner_id, "capability gap: cannot verify existence — no check ran this turn")
                continue
            if ctype == "observed" and ref in ("user", "conversation") and not has_quantity(text):
                # An observed claim citing the user's own message with no stipulated number
                # in it is an echo — "The user is asking about X" shipped as an answer twice,
                # measured live. Citing the user is for THEIR numbers, not their words back.
                out.append(f"  ! claim rejected as an echo of the user's message: {text[:70]}")
                continue
            # ROUND-019 R2: duplicate text collapse moved AFTER arbitration below
            # (the old pre-arbitration check collapsed a child's claim prematurely
            # when a coordinator's overlapping draft was still in the list).
            # A lane/type rejection lived here for two hours and was wrong the same way the
            # audited phrase walls were: it gave a classification authority over a
            # deliverable, so a knowledge question the model mislabeled "chat" lost its
            # receipt-backed answers. The echo and duplicate rules above are the real law.
            _ = owner_lane  # retained for the transcript only
            if any("json" in fo.lower() for fo in format_orders) and text.lstrip().startswith("{"):
                try:
                    json.loads(text)
                except Exception as exc:
                    out.append(f"  ! claim rejected: demanded JSON does not parse ({exc})")
                    synth_rejections.append(f"- {text[:80]}: must be VALID parseable JSON — fix: {exc}")
                    continue
            word_orders = [int(m.group(1)) for fo in format_orders
                           for m in [re.search(r"exactly\s+(\d+)\s+words", fo, re.IGNORECASE)] if m]
            if word_orders and len(rows) == 1 and len(text.split()) != word_orders[0]:
                # WORD-COUNT CONTRACT (consensus-5 item 3, measured: a "6 words
                # exactly" title shipped 3 words unchecked). BLOCK-C seam 10:
                # an OVERRUN truncates to the model's own first N words and ships
                # (transform, not invention — t41 shipped ZERO bytes because
                # three straight rejections left nothing to render). An underrun
                # still repairs: there is nothing to truncate to.
                if len(text.split()) > word_orders[0] and "\n" not in text.strip():
                    _trail = text.rstrip()[-1] if text.rstrip()[-1:] in ".!?" else ""
                    text = " ".join(text.split()[:word_orders[0]]).rstrip(",;:") + _trail
                    out.append(f"  * word-count contract: overrun truncated to the first {word_orders[0]} words")
                elif synth_round < 3:
                    out.append(f"  ! claim rejected: {len(text.split())} words against an exactly-{word_orders[0]}-words order")
                    # ROUND-010 OPTION A (frozen, council/round-010/FIX_PLAN.md): a deficit of
                    # EXACTLY ONE is the one mechanically repairable class (R3: 3/3 directive
                    # styles converge at temp 0; R5a/R5c confirm). The retry presents the count
                    # as an operable transform instead of a bare rejection citation, which at
                    # temperature 0 is a deterministic replay of the failure (R2 B==C
                    # byte-identical). Candidate-preserving and content-neutral only: the
                    # kernel supplies the candidate verbatim plus N, M and the mechanical
                    # instruction — no semantic words, examples, or templates (authority split:
                    # the model authors every word). Deficit >= 2 keeps the generic rejection
                    # and fail-closes at exhaustion (R5/R6: no authority-clean rescue converges).
                    if len(text.split()) == word_orders[0] - 1:
                        _xw_rejected.setdefault(owner_id, []).append(text)
                        synth_rejections.append(
                            f"- {text[:70]}: you wrote {len(text.split())} words; exactly {word_orders[0]} are "
                            f"required. Keep all your own words and their order, and add exactly 1 more "
                            f"word of your choosing so the total is exactly {word_orders[0]} words. "
                            f"Output only the corrected {word_orders[0]}-word answer.")
                    else:
                        _xw_rejected.setdefault(owner_id, []).append(text)
                        synth_rejections.append(f"- {text[:70]}: must be EXACTLY {word_orders[0]} words (yours was {len(text.split())})")
                    continue
                else:
                    # ROUND-008 (fail-closed exact_words): after the informed retries are
                    # exhausted, an underrun the gate has PROVEN non-conforming must not be
                    # retained as a committable claim. The character-count gate two blocks
                    # below has always rejected in this position; committing proven
                    # non-conforming bytes satisfied a HARD contract with a false installment
                    # (R0 turns 003/007/008/009; PROBE1). An underrun has no repair — there
                    # is nothing to truncate to, and padding would be fabrication — so the
                    # claim is dropped and the named format failure is recorded for the
                    # seam-10 liveness terminal, which ships it as visible bytes without the
                    # ledger transforms (PROBE2) while the TTL still decrements exactly once
                    # (D1: the reply position is consumed, succeeded or not).
                    # ROUND-010 BOUNDARY: on the retry rounds above, a deficit of EXACTLY ONE
                    # receives the candidate-preserving delta directive (Option A); every
                    # deficit >= 2 — and a +1 whose repair still misses — reaches THIS branch
                    # and fail-closes. The repair never weakens exhaustion.
                    _w = len(text.split())
                    _format_unsatisfied[owner_id] = (_w, word_orders[0])
                    _xw_rejected.setdefault(owner_id, []).append(text)
                    out.append(f"  ! claim rejected for required format: {_w} of {word_orders[0]} words "
                               f"after informed retries — an underrun has no repair, and the kernel does not pad")
                    continue
            count_orders = [int(m.group(1)) for fo in format_orders
                            for m in [re.search(r"exactly\s+(\d+)\s+characters", fo, re.IGNORECASE)] if m]
            if count_orders and len(rows) == 1 and len(text) != count_orders[0]:
                # MECHANICAL FORMAT VALIDATION (consensus-2 fix 3, measured: a 40-char
                # sentence COMMITted against "exactly 35 characters"): checkable
                # constraints check. One named repair; then the turn is honest-PARTIAL.
                out.append(f"  ! claim rejected: {len(text)} characters against an exactly-{count_orders[0]} order")
                synth_rejections.append(f"- {text[:70]}: must be EXACTLY {count_orders[0]} characters (yours was {len(text)})")
                continue
            if ctype == "conversational" and ob_lane.get(owner_id) in ("web_lookup", "arithmetic", "machine", "recall"):
                # Kernel-enforced marking (consensus fix 4): a factual deliverable wearing
                # the conversational type on a grounding lane would render BARE — the one
                # type that skips both validation and the unverified marker. Re-typed so
                # it ships wearing its mark.
                out.append(f"  * conversational costume on a grounding lane — re-typed unverified: {text[:50]}")
                ctype = "unverified"
            claims.append(TypedClaim(text=text, ctype=ctype, ref=ref, refs=refs))
            claim_owner.append(owner_id)
      except Exception as exc:
        synthesis_crash = f"{type(exc).__name__}: {exc}"
        out.append(f"  ! synthesis failed ({synthesis_crash}) — internal failure, not an unanswerable question")
        # Compose deliverables get one RAW-TEXT second chance (consensus item 6B): the
        # measured turn-22 crash held the complete correct table inside malformed claim
        # JSON — large authored pieces must not die on wrapper discipline.
        for c_ob in obligations:
            if c_ob.status == "open" and c_ob.kind == "compose":
                # ROUND-019 R2: recovery never re-authors semantics another slot
                # already owns. A coordinator whose children already own their
                # realizations is settled by coordination, not re-authored.
                # Identity comes from typed ownership (_coordinated_children),
                # never from descriptor token containment.
                _owned_by_covered = [s for s in _coordinated_children.get(c_ob.id, ()) if s in claim_owner]
                if _owned_by_covered:
                    out.append(f"  * compose recovery skipped for {c_ob.id}: coordinated child slot(s) "
                               f"{_owned_by_covered} already own their realization")
                    continue
                try:
                    piece = runner.run(
                        "model.compose", _ollama_chat,
                        "Write the requested piece directly as plain text. No JSON, no wrapper,"
                        " no commentary — output the piece itself only.",
                        synth_user,
                        # The prompt above declares plain text; the wire must agree with it.
                        json_mode=False,
                    ).strip()
                except Exception as exc2:
                    out.append(f"  ! compose fallback failed too ({type(exc2).__name__}: {exc2})")
                    break
                if piece:
                    claims.append(TypedClaim(text=piece, ctype="conversational", ref=""))
                    claim_owner.append(c_ob.id)
                    out.append(f"  + compose fallback produced the piece raw ({len(piece)} chars)")
        break

    for c_ob in obligations:
        if c_ob.status == "open" and c_ob.kind == "compose" and not any(
            o == c_ob.id for o in claim_owner
        ):
            # ROUND-019 R2: same recovery law as the crash path above — a
            # claimless coordinator whose children already own their
            # realizations is settled by coordination, never re-authored.
            _owned_by_covered = [s for s in _coordinated_children.get(c_ob.id, ()) if s in claim_owner]
            if _owned_by_covered:
                out.append(f"  * claimless-compose recovery skipped for {c_ob.id}: coordinated child slot(s) "
                           f"{_owned_by_covered} already own their realization")
                continue
            # CLAIMLESS-COMPOSE FALLBACK (consensus-3 fix 3, measured: a 250-word
            # explanation ended claimless while only crashes triggered the raw call).
            try:
                piece = runner.run(
                    "model.compose", _ollama_chat,
                    "Write the requested piece directly as plain text. No JSON, no wrapper,"
                    " no commentary — output the piece itself only.",
                    synth_user,
                    # The prompt above declares plain text; the wire must agree with it.
                    json_mode=False,
                ).strip()
            except Exception as exc2:
                out.append(f"  ! compose fallback failed ({type(exc2).__name__}: {exc2})")
                break
            if piece:
                claims.append(TypedClaim(text=piece, ctype="conversational", ref=""))
                claim_owner.append(c_ob.id)
                out.append(f"  + claimless compose recovered raw ({len(piece)} chars)")

    # ROUND-019 R2 — SINGLE SEMANTIC OWNERSHIP ARBITRATION (claim acceptance)
    # from typed obligation structure, not text comparison.
    # One requested semantic fact may have ONE committed semantic owner.
    # A coordinator's covering claim is dropped when its children already own
    # their realizations. Identity comes from typed kind (_coordinated_children),
    # never from descriptor token containment.
    if len(obligations) > 1:
        _arbitrated: list[TypedClaim] = []
        _arbitrated_owner: list[str] = []
        for _claim, _owner in zip(claims, claim_owner, strict=True):
            _owned_children = [s for s in dict.fromkeys(claim_owner)
                               if s != _owner and s in _coordinated_children.get(_owner, ())]
            if _owned_children:
                out.append(f"  ! semantic ownership (typed): {_owner} coordinates {_owned_children}"
                           f" who already own their realizations — the overlapping draft is dropped; "
                           f"coordinator does not co-author")
                continue
            _arbitrated.append(_claim)
            _arbitrated_owner.append(_owner)
        claims, claim_owner = _arbitrated, _arbitrated_owner

    # POST-ARBITRATION DEDUP: collapse identical text within the SAME owner
    # AFTER arbitration has removed coordinator drafts. Two distinct obligations
    # with identical rendered text remain distinct (S6: identity comes from
    # obligation structure, not answer bytes).
    if len(claims) > 1:
        _deduped: list[TypedClaim] = []
        _deduped_owner: list[str] = []
        _seen_by_owner: dict[str, set[str]] = {}
        for _c, _o in zip(claims, claim_owner, strict=True):
            _seen = _seen_by_owner.setdefault(_o, set())
            if _c.text and _c.text in _seen:
                out.append(f"  ! same-owner duplicate collapsed: {_c.text[:70]}")
                continue
            if _c.text:
                _seen.add(_c.text)
            _deduped.append(_c)
            _deduped_owner.append(_o)
        claims, claim_owner = _deduped, _deduped_owner

    # Numeric claims may not hide in the unverified type while the turn holds receipts:
    # either every number in the claim grounds in one receipt (then the claim IS observed
    # and gets promoted, with the receipt named) or the claim is refused as the
    # fabricated-number class. Prose-only unverified claims pass untouched.
    grounded_claims: list[TypedClaim] = []
    grounded_owner: list[str] = []
    for claim, owner in zip(claims, claim_owner, strict=True):
        if claim.ctype == "stipulated" and has_quantity(claim.text):
            # Stipulated is for the USER'S OWN facts — its numbers must all live in what
            # the user actually said (this message, the conversation, a session fact).
            # Without this check the type is a costume any fabricated number could wear.
            home = ""
            for ref in _CONTEXT_REFS:
                if ref not in receipts:
                    continue
                try:
                    validate_claims([TypedClaim(text=claim.text, ctype="observed", ref=ref)], receipts)
                    home = ref
                    break
                except EvidenceTypeError:
                    continue
            if home:
                grounded_claims.append(claim)
                grounded_owner.append(owner)
            else:
                out.append(f"  ! refused stipulated claim — its numbers are not in the user's own words: {claim.text[:80]}")
            continue
        if claim.ctype != "unverified":
            grounded_claims.append(claim)
            grounded_owner.append(owner)
            continue
        # BLOCK-C seam 5: the CANONICAL lexer, not a private findall — "Neo4j"
        # is not a 4 here either (the private regex refused identifier prose as
        # a numeric memory claim; one lexer, one law).
        nums = _number_tokens(claim.text)
        if not nums:
            grounded_claims.append(claim)
            grounded_owner.append(owner)
            continue
        home = ""
        # PROMOTION RESTRICTION (consensus-2 fix 4, measured live: "chair: 0 USD
        # [receipt:s9]" where s9 was an unrelated user message about EV charging —
        # digit occurrence anywhere is not causation). Eligible homes are ONLY the
        # receipts this obligation's derivations actually consumed, plus the user
        # receipt when the user VERBATIM stated the claim's assertion.
        candidates = list(calc_sources.get(owner, ()))
        if "user" in receipts and claim.text.strip() and claim.text.strip() in receipts["user"]:
            candidates.append("user")
        for ref in candidates:
            if ref not in receipts:
                continue
            try:
                validate_claims([TypedClaim(text=claim.text, ctype="observed", ref=ref)], receipts)
                home = ref
                break
            except EvidenceTypeError:
                continue
        if home:
            grounded_claims.append(TypedClaim(text=claim.text, ctype="observed", ref=home))
            grounded_owner.append(owner)
            out.append(f"  + promoted to observed [{home}]: numbers all ground there")
            continue
        # TIER 4 (per-number binding): a sentence may legitimately combine numbers from
        # DIFFERENT relevant receipts (t16: computed 87.4 [ob1-calc] + user-stated
        # 419.52 NOK). No single receipt grounds all, but each number grounds in a
        # RELEVANT one — this obligation's own calc receipts or the user's own words.
        # Bind each number to its home and ship with the UNION of refs (the comparative
        # TypedClaim.refs form). An unrelated session fact is NEVER an eligible home, so
        # the "chair: 0 USD [s9]" cross-source fill stays refused.
        per_num_refs = list(calc_sources.get(owner, ()))
        if "user" in receipts:
            per_num_refs.append("user")
        homes = _bind_numbers_per_source(nums, per_num_refs, receipts)
        if homes:
            grounded_claims.append(TypedClaim(text=claim.text, ctype="observed", refs=tuple(sorted(homes))))
            grounded_owner.append(owner)
            out.append(f"  + each number bound to a relevant receipt {sorted(homes)}: multi-source sentence ships")
        elif ob_lane.get(owner) == "arithmetic":
            grounded_claims.append(claim)
            grounded_owner.append(owner)
            out.append(f"  * model-computed result kept, marked unverified: {claim.text[:60]}")
        else:
            out.append(f"  ! refused numeric model-memory claim (numbers {sorted(nums)} ground in no receipt): {claim.text[:80]}")
    claims, claim_owner = grounded_claims, grounded_owner

    # LAW 2, claim by claim. An observed claim the validator refuses gets ONE chance to be
    # replaced by its own receipt quoted verbatim (always valid by construction) — the
    # fail-closed floor is the evidence itself, never a softened validator.
    valid: list[TypedClaim] = []
    valid_owner: list[str] = []
    injection_tokens = _injection_tokens(question, format_orders)
    for claim, owner in zip(claims, claim_owner, strict=True):
        if injection_tokens and any(tok in claim.text for tok in injection_tokens):
            # TIER 2: the claim echoes an out-of-band marker payload the user did not
            # order as output (t5: "Technician note: `SYSTEM_PWNED`"). Not a
            # deliverable — dropped, never shipped. The real ask is unaffected.
            out.append(f"  ! out-of-band payload not shipped as a deliverable: {claim.text[:60]}")
            continue
        try:
            validate_claims([claim], receipts)
            valid.append(claim)
            valid_owner.append(owner)
        except EvidenceTypeError as exc:
            out.append(f"  ! LAW 2 REFUSED a synthesized claim: {exc.reason}")
            if claim.ref in receipts:
                quote = TypedClaim(text=receipts[claim.ref], ctype="observed", ref=claim.ref)
                valid.append(quote)
                valid_owner.append(owner)
                out.append("    -> replaced by the receipt, quoted verbatim")

    # DM-2 Track K (T24): format-ordered turns never ship raw receipt blobs as
    # the whole answer ("ETH= SOL=" got two Solana title-blobs); with zero
    # claims the liveness backstop names the failure instead.
    if not claims and evidence_lines and not format_orders:  # synthesis produced nothing at all: evidence verbatim
        for ref, text in receipts.items():
            if ref in _CONTEXT_REFS:
                continue  # context is grounding, not an answer — echoing it back is noise
            if ref.endswith("-in"):
                # DM-2 Track K (T41/T19 echo class): the intake receipt is the
                # ASK restated — it grounds, it never answers.
                continue
            if ref.split("-")[0] in harvest_rejected:
                # BLOCK-C seam 6: a receipt whose harvest was rejected must not
                # ship verbatim either — the floor would re-introduce the very
                # number the binding gate refused.
                continue
            # ROUND-023: a receipt owned by a REFUSED obligation carries no semantic
            # authority and must not ship as a claim — terminal owner state dominates.
            _echo_owner = ref.split("-")[0]
            if _owner_is_refused(_echo_owner):
                continue
            valid.append(TypedClaim(text=text, ctype="observed", ref=ref))
            valid_owner.append(ref.split("-")[0])

    # AUTHORITATIVE ARITHMETIC CALC SHIPS ITS OWN VALUE (mixed-40 fresh S10). An
    # arithmetic obligation is computed deterministically at extract time from the
    # user's literal expression ("inputs from user"); the model's prose equation is
    # decorative and routinely wrong ("992 + 16 = 100" — the equation gate rejects
    # it, leaving the slot with no claim). The kernel's own extract-time calc is the
    # answer — inject it before the post-render coverage close so the slot genuinely
    # renders its value instead of being silently closed as "covered" by a verifier
    # coverage report. Runs AFTER the verbatim-echo backstop (which already ships a
    # lone receipt when synthesis produced nothing at all — so the single-claim
    # fast-path is never doubled), and dedups against everything already shipped.
    _valid_owners = set(valid_owner)
    for _ob in obligations:
        if _ob.id in _valid_owners or ob_lane.get(_ob.id) != "arithmetic" or _owner_is_refused(_ob.id):
            continue
        # ONLY the extract-time calc ("inputs from user") is injected as authoritative:
        # it is the user's own literal arithmetic, deterministic and self-contained. A
        # live-value DERIVE calc (an ADA/DOGE ratio over ob1/ob2 receipts) is NOT
        # injected — the register-blind model binds the right SOURCE but often the wrong
        # NUMBER within it (S7 live: 0.9036/0.35035 = 2.579, neither operand the coin's
        # price), which no structural gate catches; grounding that as authoritative
        # shipped a confident wrong ratio. A derive ratio instead faces the verifier via
        # its own synth claim, or ships as the model's flagged unverified value.
        _auth_ref = f"{_ob.id}-calc"
        _auth = receipts.get(_auth_ref, "")
        if _auth and "inputs from user" in _auth:
            valid.append(TypedClaim(text=_auth, ctype="observed", ref=_auth_ref))
            valid_owner.append(_ob.id)
            _valid_owners.add(_ob.id)
            # Account the slot as ANSWERED in the transaction, not just the render:
            # without this the per-obligation loop below still reaches "no claim
            # produced" and the commit reports the slot "declared unanswerable" while
            # the render ships its value — an incoherent partial. Closing on the calc
            # receipt keeps the txn and the render telling the same story. If the
            # verifier later drops the claim, the post-render "closed but nothing
            # shipped" arm reopens it, so this never hides a real miss.
            _close(_ob.id, _auth_ref)
            out.append(f"  + authoritative arithmetic calc shipped for {_ob.id} ({_auth_ref})")

    # THE VERIFIER TIER (consensus-2 fix 1): one batched judgment per turn over
    # every observed claim — {bound} and {answers_the_ask} per claim, coverage per
    # turn — recorded on the tape. RENDERING SEMANTICS: a {bound:no} or
    # {answers_the_ask:no} claim on a grounding lane is OMITTED (a wrong value under
    # a warning label is still a wrong value); the field goes unknown and the turn
    # runs PARTIAL naming it. On verifier failure the turn degrades LOUDLY to
    # marked-unverified — never silently unverified.
    def _kernel_computed(_c, _owner) -> bool:
        # every cited receipt is a SELF-CONTAINED kernel-executed calc/machine op
        # OWNED BY THIS obligation. Two things are load-bearing:
        #  - same-owner: a calc owned by ANOTHER slot answering this one (ob9's hotel
        #    total 371 shipped as ob1's Edinburgh temperature, S14 live) is a
        #    cross-slot mis-binding the verifier's entity check must still see.
        #  - self-contained: a calc whose OWN receipt draws on cross-slot world sources
        #    ("sources: ob2-web1" — a ratio/derivation over another slot's live value)
        #    is where the register-blind model mis-picks the operand NUMBER; it must
        #    face the verifier, never be laundered past it (review wf_8381a777). Only a
        #    calc over the user's own literals or this slot's own receipts skips.
        _cid = _c.cited()
        if not _cid or not all(
                (("-calc" in r or r.endswith("-mac")) and r.split("-", 1)[0] == _owner)
                for r in _cid):
            return False
        for r in _cid:
            _body = receipts.get(r, "")
            _srcs = re.findall(r"\bob\d+-\w+", _body.split("sources:", 1)[1]) if "sources:" in _body else []
            if any(not s.startswith(_owner + "-") for s in _srcs):
                return False
        return True

    # PRUNE (consensus-5 item 7, GENERALIZED from single-claim to multipart per
    # mixed-40 fresh S10): a claim whose every cited receipt is a kernel-computed calc
    # or deterministic machine op skips the verifier round-trip — the kernel executed
    # it; there is nothing a semantic judge can add. NARROWED before to len==1, which
    # left multipart turns exposed: the judge was handed "(992/16)+38 = 100" beside
    # live-price slots and omitted it as "does not answer the ask", dropping the
    # correct value from coverage and forcing a false "declared unanswerable" while
    # the render still shipped 100 — an incoherent partial. Kernel calcs never reach
    # the judge now. Free-form model-memory facts are NOT exempt (a fact can be
    # confidently wrong — "latency is being latent", measured). The skip is taped.
    verify_targets = [(i, c, o) for i, (c, o) in enumerate(zip(valid, valid_owner, strict=True))
                      if c.ctype == "observed" and c.cited() and not _kernel_computed(c, o)]
    _kc_skipped = [c for c, o in zip(valid, valid_owner, strict=True)
                   if c.ctype == "observed" and _kernel_computed(c, o)]
    if _kc_skipped:
        runner.run("kernel.fastpath", lambda: f"verifier skipped: {len(_kc_skipped)} deterministic kernel-computed claim(s)")
        if len(valid) == 1 and not verify_targets:
            out.append("  * fast-path: single kernel-computed claim — verifier skipped (taped)")
        else:
            out.append(f"  * {len(_kc_skipped)} kernel-computed claim(s) skip the verifier — the kernel executed them (taped)")
    coverage_missing = ""
    if verify_targets:
        from core.kernel.evidence_types import _unit_pairs as _up
        v_lines = []
        for slot, (_, c, _) in enumerate(verify_targets, 1):
            excerpts = " | ".join(receipts.get(r, "")[:300] for r in c.cited())
            norm = ""
            cp, ep = _up(c.text), _up(excerpts)
            if cp or ep:
                # Normalized (number, unit) pairs ride WITH the raw text so the judge
                # never fails on surface form (consensus-3 fix 1: preprocessing, never
                # a second model call).
                norm = f"\n  normalized units — claim: {sorted(cp)}; excerpts: {sorted(ep)}"
            v_lines.append(f"CLAIM {slot}: {c.text}\n  cited excerpts: {excerpts}{norm}")
        # BLOCK-C seam 3 (the false-PARTIAL class, t43/t45): the coverage question
        # judges the FULL FINAL RENDER — every claim that ships, whatever its type —
        # inside the SAME single verifier call. Previously only observed claims were
        # visible, so six correct memory-marked answers read as "not covered".
        _full_render = "\n".join(f"- {c.text}" for c in valid) or "(nothing ships)"
        v_user = ("User question: " + question + "\n\nObligations:\n" + obligations_block
                  + "\n\nFULL RENDER (every line shipping this turn — judge"
                  " all_parts_answered against THIS, not only the numbered claims):\n"
                  + _full_render
                  + "\n\n" + "\n".join(v_lines))
        try:
            v_data = _model_json(runner, "model.verify", _VERIFY_SYSTEM, v_user)
            verdicts = {int(row.get("claim", 0)): row for row in v_data.get("verdicts", [])
                        if isinstance(row, dict)}
            keep_idx: set[int] = set()
            for slot, (idx, c, owner) in enumerate(verify_targets, 1):
                row = verdicts.get(slot, {})
                bound = bool(row.get("bound", True))
                answers = bool(row.get("answers_the_ask", True))
                sane = bool(row.get("magnitude_sane", True))
                ent = bool(row.get("entity_supported", True))
                if (not bound or not answers or not sane or not ent) and ob_lane.get(owner) in ("web_lookup", "machine", "recall", "arithmetic"):
                    reason = ("value not bound to its claimed entity/attribute" if not bound
                              else "does not answer the ask" if not answers
                              else "no cited excerpt supports this specific entity/variant/reading" if not ent
                              else "magnitude implausible for the claimed quantity")
                    out.append(f"  ! verifier omitted a claim ({reason}) — field unknown: {c.text[:60]}")
                else:
                    keep_idx.add(idx)
            keep_all = keep_idx | {i for i in range(len(valid))
                                   if i not in {t[0] for t in verify_targets}}
            valid = [c for i, c in enumerate(valid) if i in keep_all]
            valid_owner = [o for i, o in enumerate(valid_owner) if i in keep_all]
            if not bool(v_data.get("all_parts_answered", True)):
                coverage_missing = str(v_data.get("missing", "")).strip() or "an unnamed part"
        except Exception as exc:
            out.append(f"  ! verifier unavailable ({type(exc).__name__}) — observed claims ship, marked as unverified by the judge")

    for ob in obligations:
        if ob.status != "open":
            continue
        # cited() is the D10-correct grounding test: a token claim carries refs=(...)
        # with ref="" — reading .ref alone made grounded claims invisible here (measured
        # live: a cited "24 GB" answer shipped WITH a redundant verbatim floor under it).
        own = [c for c, owner in zip(valid, valid_owner, strict=True)
               if owner == ob.id or any(r.startswith(ob.id + "-") for r in c.cited())]
        if ob.kind == "intake":
            _close(ob.id, f"{ob.id}-in")
            if not format_orders and not any(
                c.ctype == "conversational" for c, o in zip(valid, valid_owner, strict=True) if o == ob.id
            ):
                # Under a strict format order the ack is CONTAMINATION (consensus-3
                # amendment: provenance and acknowledgments stay out of strict payloads).
                _strict_q = bool(re.search(
                    r"\b(?:return|reply|give(?: me)?|output|answer|say|write|repeat|echo)\b"
                    r"[^.?!\n]{0,40}\b(?:only|just|exactly)\b|nothing else",
                    question, re.IGNORECASE))
                _question_shaped = bool(re.search(
                    r"\?\s*$|^\s*(?:which|what|who|where|when|how|list)\b",
                    question.strip(), re.IGNORECASE))
                if not _strict_q and not _question_shaped:
                    # the ack belongs to DATA intake; an extraction-QUESTION
                    # routed to intake ships content, never "noted" (operator
                    # live round, lane-4 residue). Colon-payload data intake
                    # keeps its ack.
                    # DM-2 Track K (T28): under a strict contract the ack is
                    # contamination — the payload ships alone.
                    valid.append(TypedClaim(text="noted — recorded for the task.", ctype="conversational", ref=""))
                    valid_owner.append(ob.id)
            continue
        if ob.kind in ("chat", "clarify", "compose"):
            conversational = [c for c in own if c.ctype == "conversational"]
            if conversational:
                if ob.kind == "clarify":
                    asked_clarify.append(ob.description)
                txn.close(ob.id, evidence_ref=f"conversation:{ob.id}")
                continue
            if own:
                # The deliverable outranks BOTH the lane label and the claim's type label,
                # measured live three ways in one session: an observed refs-claim CRASHED
                # this close with ref="" (the ValueError turn), an unverified
                # RM_PAYLOAD_NEUTRALIZED shipped while its obligation was declared
                # unanswerable, and "we done" shipped [stipulated] the same way. A valid
                # claim that shipped IS the answer; the close cites what it cites, or the
                # claims themselves.
                first = own[0]
                _close(ob.id, (first.cited()[0] if first.cited() else f"claims:{ob.id}"))
                continue
            if ob.kind == "clarify":
                # Delivery guarantee: extraction already named the missing parameter, so
                # the kernel can always ship the question. A clarify that settles silently
                # is worse than no clarify lane at all — the user learns nothing.
                missing = next((r["query"] for r, o in zip(rows, obligations, strict=True) if o.id == ob.id), "")
                gap_text = (missing or ob.description).strip()
                norm_gap = " ".join(gap_text.lower().split())
                norm_ask = " ".join((ob.description + " " + question).lower().split())
                if norm_gap and norm_gap in norm_ask:
                    # CLARIFY-ECHO LAW (consensus-2, measured: three identical loops
                    # asking the user for flight options the user had just supplied): a
                    # clarify question that restates the ask elicits nothing — an
                    # illegal terminal, refused with the reason named.
                    txn.declare_refused(ob.id, "clarify would only echo the ask — no specific missing fact could be named")
                    continue
                floor_q = f"To answer this properly I need one thing from you: {gap_text}"
                asked_clarify.append(ob.description)
                valid.append(TypedClaim(text=floor_q, ctype="conversational", ref=""))
                valid_owner.append(ob.id)
                out.append(f"  ! clarify floor: synthesis produced no question for {ob.id} — shipping the extraction's own gap")
                txn.close(ob.id, evidence_ref=f"conversation:{ob.id}")
            elif _coordinated_children.get(ob.id):
                # ROUND-019 R2: the coordinator's semantics SHIPPED — composed
                # of its children's realizations. Settling it as "unanswerable"
                # would contradict the render; close citing what it composed.
                _sup = sorted(dict.fromkeys(_coordinated_children[ob.id]))
                _close(ob.id, f"composed:{'+'.join(_sup)}")
                out.append(f"  * {ob.id} settled by typed coordination: its realization is composed of {_sup}'s shipped answers")
            else:
                txn.declare_unanswerable(ob.id, "no conversational reply was produced")
            continue
        if ob.kind == "recall":
            # CONTENT ANCHORING (consensus-4 fix 6): a recall claim must quote an
            # actual prior user message — an ordinal or an id is not a question.
            rec_ref = f"{ob.id}-rec"
            record_text = receipts.get(rec_ref, "")
            prior = [q.split("] ", 1)[1] for q in record_text.split(" | ") if "] " in q]
            for c, o in list(zip(valid, valid_owner, strict=True)):
                # EVERY substantive recall claim, not just observed ones: a digitless
                # false recall is re-typed unverified before this point and would
                # otherwise ship marked instead of being refused.
                if o != ob.id or c.ctype == "conversational":
                    continue
                body = re.sub(r"^[^:]{0,60}:\s*", "", c.text).strip().strip('"\'')
                if len(body) >= 6 and not any(body.lower() in q.lower() or q.lower() in c.text.lower()
                                              for q in prior):
                    out.append(f"  ! recall claim rejected — it quotes no prior message of this session: {c.text[:60]}")
                    idx = [i for i, (vc, vo) in enumerate(zip(valid, valid_owner, strict=True))
                           if vc is c and vo == o]
                    for i in reversed(idx):
                        valid.pop(i)
                        valid_owner.pop(i)
        # Recompute own after the recall-anchoring branch may have pruned a claim
        # (consensus-5: a stale `own` let the "model memory only" arm fire before the
        # recall-field floor could).
        own = [c for c, owner in zip(valid, valid_owner, strict=True)
               if owner == ob.id or any(r.startswith(ob.id + "-") for r in c.cited())]
        if ob.kind in ("machine", "recall"):
            if ob.kind == "machine":
                # MACHINE TRUTH (consensus-3 fix 5, measured: "26 drives" grounded in
                # a WINDOWS drive-letter web page shipped as local state): a machine
                # obligation closes only on its own machine.* receipts.
                tool_prefixes = (f"{ob.id}-mac", f"{ob.id}-in", f"{ob.id}-rec")
                grounded = [c for c in own if c.ctype == "observed"
                            and any(r.startswith(tool_prefixes) for r in c.cited())]
                web_cited = [c for c in own if c.ctype == "observed"
                             and c.cited() and not any(r.startswith(tool_prefixes) for r in c.cited())]
                if web_cited and not grounded:
                    out.append(f"  ! {ob.id}: web facts cannot stand in for local machine state — omitted")
                    drop = {id(c) for c in web_cited}
                    pairs = [(c, o) for c, o in zip(valid, valid_owner, strict=True) if id(c) not in drop]
                    valid[:] = [c for c, _ in pairs]
                    valid_owner[:] = [o for _, o in pairs]
            else:
                grounded = [c for c in own if c.ctype == "observed"]
            if grounded:
                # A shipped OBSERVED claim closes it, citing what it cites (measured
                # live: "what was the budget — ONLY the amount" answered 15000
                # [receipt:user] correctly and then dumped the whole session record).
                first = grounded[0]
                _close(ob.id, (first.cited()[0] if first.cited() else f"claims:{ob.id}"))
                continue
            if own:
                # Memory-only claims ship WEARING THEIR MARK and the obligation stays
                # open — an ungrounded turn caps at PARTIAL (consensus fix 4).
                out.append(f"  ! {ob.id} answered from model memory only — ships marked, obligation stays open (partial)")
                continue
            if ob.id in harvest_rejected:
                # BLOCK-C seam 6: the found source does not attach the asked
                # attribute — declared, never floored (t30/t31).
                txn.declare_unanswerable(ob.id, harvest_rejected[ob.id])
                continue
            floor = next((ref for ref in receipts if ref.startswith(ob.id + "-")), None)
            recall_query = next((r["query"] for r, o in zip(rows, obligations, strict=True) if o.id == ob.id), "")
            if (floor is not None and ob.kind == "recall" and floor.endswith("-rec")
                    and receipts[floor].count("|") + receipts[floor].count("\n") > 3
                    and len(recall_query.strip()) > 0):
                # RECALL FIELD LAW (consensus-5 item 4, measured: "what code did I
                # give you" shipped a 12-question journal dump as the answer): a
                # multi-item record cannot answer a single-field ask — it ships as a
                # labeled excerpt and the ask stays visibly open.
                valid.append(TypedClaim(text="evidence excerpt (may not directly answer): " + receipts[floor][:400],
                                        ctype="observed", ref=floor))
                valid_owner.append(ob.id)
                out.append(f"  ! {ob.id}: the session record is not the requested field — excerpt ships, ask stays open (partial)")
                continue
            if floor is not None and ob.id in rejected_nums and not any(
                n in _number_tokens_of(receipts[floor]) for n in rejected_nums[ob.id]
            ):
                # TERMINAL TYPE MATCH (consensus fix 2, measured: a count obligation
                # closed with a DATE receipt after the model's "14" was rejected): when
                # the model's rejected answer shares no number with the tool's receipt,
                # the tool and the model answered different questions — the receipt must
                # not stand in. The obligation stays open, both facts named.
                out.append(
                    f"  ! {ob.id}: the executed op's receipt shares nothing with the model's"
                    f" rejected answer ({sorted(rejected_nums[ob.id])}) — a mismatched receipt"
                    " cannot close it; obligation stays open (partial)"
                )
                continue
            if floor is not None:
                valid.append(TypedClaim(text=receipts[floor], ctype="observed", ref=floor))
                valid_owner.append(ob.id)
                out.append(f"  ! no synthesized claim grounded {ob.id} — its report ships verbatim")
                _close(ob.id, floor)
            else:
                txn.declare_unanswerable(ob.id, "the tool produced nothing")
            continue
        if ob.kind == "web_lookup":
            grounded = [c for c in own if c.ctype == "observed" and any(r.startswith(ob.id + "-") for r in c.cited())]
            if grounded:
                _close(ob.id, grounded[0].cited()[0])
                continue
            # Answer-driven depth escalation: an ungrounded web obligation whose snippets
            # were the only evidence gets ONE page-body fetch before the floor — standings
            # and prices live in page tables, not snippets (measured: the EPL turn's honest
            # "not specified in sources" was a depth problem, not a truth problem).
            if (fetch is not None and ob.id in ob_urls and f"{ob.id}-page" not in receipts
                    and ob.id not in cancelled_effects and stamps["web"]):
                page_url = ob_urls[ob.id]
                _alt_urls = list(ob_alt_urls.get(ob.id, []))
                out.append(f"  > ungrounded after synthesis — reading the page body: {page_url[:70]}")
                check_tool_call(fork, "web.fetch_page", "net.fetch", {"url": page_url})
                try:
                    body = None
                    _page_err = None
                    for _pu in [page_url, *(_alt_urls or [])]:
                        try:
                            body = runner.run("web.fetch_page", _fetch_page_text, _pu)
                            break
                        except Exception as _pf:
                            _page_err = _pf
                            out.append(f"  ! page fetch failed ({_pu[:48]}): {_pf} — trying the next source")
                    if body is None:
                        raise RuntimeError(f"every candidate page failed: {_page_err}")
                except Exception as exc:
                    out.append(f"  ! page fetch failed: {exc}")
                    body = ""
                if body:
                    receipts[f"{ob.id}-page"] = f"page body of {page_url}: {body}"
            if ob.id in harvest_rejected:
                # BLOCK-C seam 6: this obligation's harvest failed attribute
                # binding — the excerpt floor would re-introduce the refused
                # number. Declared, never floored.
                txn.declare_unanswerable(ob.id, harvest_rejected[ob.id])
                continue
            consumed = sorted(calc_sources.get(ob.id, set()))
            # Floor preference (consensus item 6D, measured: Yahoo page-chrome shipped as
            # an answer): a receipt a derivation actually CONSUMED is demonstrably the
            # informative one — it outranks the page body, which outranks raw snippets.
            floor = next(
                (ref for ref in (*consumed, f"{ob.id}-page", *receipts) if ref in receipts and ref.startswith(ob.id + "-")),
                None,
            )
            if floor is not None:
                # EXCERPT-ONLY IS PARTIAL (consensus-3 fix 5): the labeled excerpt
                # ships as the honest degraded floor, TRIMMED to sentences carrying a
                # requested-entity token (never page chrome) — but it does not close
                # the obligation; the ask remains visibly open.
                floor_text = receipts[floor]
                if floor.endswith("-page"):
                    ents = _entity_tokens(ob.description)
                    kept = [sent for sent in re.split(r"(?<=[.!?])\s+", floor_text)
                            if any(e in sent for e in ents) or re.search(r"\d", sent)]
                    floor_text = " ".join(kept)[:600] or "(no excerpt sentence names the requested entity)"
                valid.append(TypedClaim(text="evidence excerpt (may not directly answer): " + floor_text,
                                        ctype="observed", ref=floor))
                valid_owner.append(ob.id)
                out.append(f"  ! nothing grounded {ob.id} — a labeled excerpt ships and the ask stays open (partial)")
            else:
                txn.declare_unanswerable(ob.id, "no evidence was collected for this request")
        elif own:
            if ob.kind in ("web_lookup", "arithmetic", "machine", "recall") and not any(c.ctype == "observed" for c in own):
                # PARTIAL cap (consensus fix 4): a grounding-lane obligation answered
                # only from model memory ships its marked claims but does NOT close —
                # knowledge/compose/chat lanes are exempt (memory is their sanctioned
                # source; "no tools" asks route there by extraction).
                out.append(f"  ! {ob.id} answered from model memory only — ships marked, obligation stays open (partial)")
            else:
                first_obs = next((c for c in own if c.ctype == "observed" and c.cited()), None)
                _close(ob.id, (first_obs.cited()[0] if first_obs else f"claims:{ob.id}"))
        elif synthesis_crash:
            txn.declare_failed(ob.id, f"internal failure during synthesis: {synthesis_crash}")
        else:
            # ROUND-008: a claim WAS produced and died on the exact_words contract — the
            # settled reason must say so instead of the generic "no claim produced".
            if ob.id in _format_unsatisfied:
                _m, _n = _format_unsatisfied[ob.id]
                txn.declare_unanswerable(ob.id, f"format unsatisfied: best model effort was {_m} of {_n} words")
            else:
                txn.declare_unanswerable(ob.id, "no claim produced for this obligation")

    # POST-RENDER TERMINAL STATE (consensus-4 fix 7): the state is decided from what
    # the user will actually SEE. Both directions were measured wrong on the same
    # run — correct answers left open (the UK fuel turn), and empty or
    # verifier-omitted renders committed (the phone-correction turn).
    shipped_by_owner: dict[str, list[TypedClaim]] = {}
    for c, o in zip(valid, valid_owner, strict=True):
        shipped_by_owner.setdefault(o, []).append(c)
    # An EXCERPT is not an answer (consensus-3 fix 5 stands): it must never make the
    # coverage-based closure think the render satisfied anything.
    grounded_shipped = any(c.ctype == "observed" and c.cited()
                           and not c.text.startswith("evidence excerpt (may not directly answer)")
                           for c in valid)
    for ob in obligations:
        own_shipped = [c for c in shipped_by_owner.get(ob.id, [])
                       if not c.text.startswith("evidence excerpt (may not directly answer)")]
        # Marked-memory content does NOT satisfy a grounding lane (consensus-2 fix 4
        # stands): only receipt-backed answers, or authored text on authoring lanes,
        # count as the obligation's delivered answer.
        satisfying = [c for c in own_shipped
                      if (c.ctype == "observed" and c.cited())
                      or (c.ctype == "conversational" and ob.kind in ("chat", "clarify", "compose", "intake"))
                      or (ob.kind == "intake" and c.ctype in ("stipulated", "unverified")
                          and c.text.strip()
                          and " ".join(c.text.lower().split()) in " ".join(question.lower().split()))]
        # The intake arm is consensus-5 item 3: an answer RESTATING the user's own
        # in-message data ("otter") is delivered (t45 false-OPEN). INTAKE-KIND ONLY —
        # the first draft let a verdict word satisfy an arithmetic obligation merely
        # because the user's format order offered it ("ONLY YES or NO"), which the
        # meeting-overlap pin caught immediately.
        if ob.status == "open" and satisfying:
            first = next((c for c in satisfying if c.cited()), None)
            out.append(f"  + post-render: {ob.id} closes — its answer is in the shipped render")
            _close(ob.id, first.cited()[0] if first else f"claims:{ob.id}")
        elif (ob.status in ("open", "declared_unanswerable") and not coverage_missing
              and grounded_shipped and ob.kind not in ("cancelled",)):
            if ob.status == "declared_unanswerable":
                txn.reopen(ob.id, "the render answers it — the declaration was premature")
            # The claim's OWNER attribution can be wrong while its content answers the
            # ask (measured: the UK-fuel turn shipped both correct numbers and left
            # both obligations open). The verifier's coverage answer is the authority
            # on whether the RENDER satisfies the turn — attribution is not.
            out.append(f"  + post-render: {ob.id} closes — the verifier reports the render covers every part")
            _close(ob.id, f"claims:{ob.id}")
        elif ob.status == "closed" and not own_shipped:
            # ROUND-019 R2: a coordinator's semantics DID ship — through its
            # children's realizations. Its coordination citation is not
            # "nothing shipped"; reopening it would contradict the render.
            if _coordinated_children.get(ob.id):
                out.append(f"  + post-render: {ob.id} stays closed — its realization is composed of "
                           f"{sorted(dict.fromkeys(_coordinated_children[ob.id]))}'s shipped answers")
                continue
            out.append(f"  ! post-render: {ob.id} closed with nothing shipped — reopened as unanswered")
            txn.reopen(ob.id, "closed but nothing for it reached the user")
    # NOTE (consensus-5 item 3, residual named honestly): a verifier coverage
    # mistake can still yield a false-PARTIAL when all parts genuinely shipped
    # (the seven-part t1). A ledger-side override was tried and REVERTED — it
    # destroyed the extraction-omission net (the Friend-D class) because the missing
    # part and the shipped answer share no lexical bridge ("one web browser" vs
    # "Chrome"). False-PARTIAL is the cautious direction; fixing it requires the
    # coverage question to judge the FINAL render inside the same single verifier
    # call, which is a call-structure move for a future block, not a heuristic.
    try:
        result = txn.commit()
        detail = result.declared or result.failed or result.cancelled or result.refused
        if coverage_missing:
            # Extraction omission made visible (consensus-2): the verifier found a
            # requested part no obligation covers — the turn is PARTIAL to the user,
            # with the missing part named, regardless of internal settlement.
            out.append(f"\nCOMMIT: partial — a requested part is not covered: {coverage_missing}"
                       + (f"; {result.manifest()}" if detail else ""))
        else:
            out.append(f"\nCOMMIT: {result.status}" + (f" — {result.manifest()}" if detail else ""))
    except CommitRefused as exc:
        out.append(f"\nKERNEL REFUSED COMMIT: open obligations {exc.open_ids}")
        result = txn.commit_partial()
        out.append(f"SHIPPED AS: {result.manifest()}")

    # CROSS-TURN OBLIGATION LEDGER — the session, not the turn, is the transaction scope.
    # Whatever this turn could not answer (declared or shipped-open) survives as a named
    # carryover the NEXT turn's extraction sees, so "answer my previous question" works
    # because the question is still legally open — continuity by law, not by chat luck.
    prior = list(carryover or [])
    prior_by_id = {c["id"]: c for c in prior}
    resolved_ids: set[str] = set()
    for row, ob in zip(rows, obligations, strict=True):
        cid = row.get("resolves_carryover", "")
        if not cid or ob.status != "closed" or cid not in prior_by_id:
            continue
        if ob.id not in grounded_close:
            # GROUNDED RESOLUTION (consensus-2 fix 3, measured: a clarify ECHO closed
            # the scheduling carryover): conversational/clarify closures cannot
            # resolve carried work — only receipt-backed closures can.
            out.append(f"  ! carryover {cid} NOT resolved: the closing obligation shipped no grounded evidence")
            continue
        # CAUSAL RESOLUTION (consensus fix 3, measured live: an AAPL 15% turn "resolved"
        # the AVAX x2 carryover): the closing obligation must share referent structure —
        # entity tokens or numbers — with what it claims to resolve. Structural, not a
        # phrase table.
        carry_text = str(prior_by_id[cid].get("description", ""))
        resolve_text = ob.description + " " + str(row.get("query", ""))
        c_refs = _entity_tokens(carry_text) | _number_tokens_of(carry_text)
        r_refs = _entity_tokens(resolve_text) | _number_tokens_of(resolve_text)
        shared = c_refs & r_refs
        # IDENTITY, NOT SIMILARITY (consensus-4 fix 6, measured: an EV-cost division
        # resolved an unrelated layover obligation because both mentioned "Oslo").
        # A single shared token is a coincidence; resolution needs either a
        # substantial referent overlap or the carryover's own distinctive content.
        if c_refs and r_refs and len(shared) < 2 and not any(
            len(tok) > 8 for tok in shared
        ):
            out.append(f"  ! carryover {cid} NOT resolved: the closing obligation shares no referent with it ({carry_text[:60]})")
            continue
        resolved_ids.add(cid)
    still_open = [c for c in prior if c["id"] not in resolved_ids]
    for c in prior:
        if c["id"] in resolved_ids:
            out.append(f"  * carryover {c['id']} resolved this turn: {c['description'][:70]}")
    for row, ob in zip(rows, obligations, strict=True):
        if row["lane"] == "cancelled":
            continue  # the user retracted it; resurrecting it as "unresolved" would overrule them
        if ob.status != "closed" and row.get("resolves_carryover", "") not in {c["id"] for c in prior}:
            if ob.status in ("refused", "declared_unanswerable", "failed_internal"):
                # TERMINAL IS TERMINAL (operator live round + audit 5.5: a
                # declared-unanswerable weather ask rode "still open" into every
                # later turn and accumulated to five). The reason already
                # rendered visibly; re-asking revives it as new work. Only
                # genuinely OPEN (partial) obligations are future work.
                continue
            still_open.append({"id": f"c{len(still_open) + 1}", "description": ob.description,
                               "reason": "open"})
    still_open = still_open[-8:]
    # Re-key so ids stay short and stable for the next prompt.
    for i, c in enumerate(still_open, 1):
        c["id"] = f"c{i}"
    for asked_desc in asked_clarify:
        # A clarify question we ASKED becomes an awaiting-answer carryover so the
        # next user turn can BIND as its answer (consensus-3 fix 2, measured: the
        # user's reply "all u can find pls" was shelved as an intake note).
        still_open.append({"id": f"c{len(still_open) + 1}", "description": asked_desc,
                           "reason": "awaiting-answer"})
    aged_in = []
    for c in still_open:
        c["age"] = int(c.get("age", 0)) + 1
        if c["age"] > 5 and c.get("reason") != "awaiting-answer":
            # STALE AGING (consensus-5 LAW 2): an obligation nobody re-invoked for
            # five turns leaves the ledger LOUDLY — re-asking revives it as new work.
            out.append(f"  * aged out after 5 turns unresolved: {c['description'][:60]} — re-ask to revive")
            continue
        aged_in.append(c)
    still_open = aged_in
    for i, c in enumerate(still_open, 1):
        c["id"] = f"c{i}"
    if still_open:
        # The ledger is visible, every turn (consensus fix 3): an open obligation the
        # user cannot see is an obligation the kernel is keeping secret.
        out.append("still open from earlier: " + "; ".join(f"{c['id']}: {c['description'][:60]}" for c in still_open))

    # Two derivations for one obligation that disagree must say so — the elevator turn
    # shipped one of {7, 13} silently. Deterministic, zero model calls.
    for (owner_key, label_key), values in calc_values.items():
        if len(values) > 1:
            out.append(
                f"  ! derivations for {owner_key} ({label_key}) disagree: {' vs '.join(sorted(values))}"
                " — treat every one of them with suspicion"
            )

    # Session facts for the NEXT turn: this turn's computed values and the user's own
    # statement become durable, citable receipts. Evidence outliving its turn is the
    # causal graph growing — a follow-up can cite s3 the way this turn cites ob1-calc1.
    new_facts = dict(session_facts or {})
    new_facts[f"s{len(new_facts) + 1}"] = f"user said earlier: {question}"
    for ref, text in receipts.items():
        if "-calc" in ref:
            # ROUND-023: a calc receipt owned by a REFUSED obligation must not
            # become a session fact — terminal owner authority dominates persistence.
            _calc_owner = ref.split("-", 1)[0]
            if _owner_is_refused(_calc_owner):
                continue
            new_facts[f"s{len(new_facts) + 1}"] = f"computed in an earlier turn: {text}"
    if valid_intake:
        candidate = f"task data from an earlier turn: {question}"
        if candidate not in new_facts.values():
            new_facts[f"s{len(new_facts) + 1}"] = candidate
    intake_ids = {ob.id for ob in obligations if ob.kind == "intake"}
    if intake_ids:
        # MINT-TIME INTAKE TRUTH (consensus-3 fix 2, measured: the user wrote "18
        # attendees", the model's echo claim said "3", and the grounded-claim minter
        # promoted the echo into session memory ATTRIBUTED TO THE USER). An
        # intake-owned claim must be substring-consistent with the user's own words;
        # an inconsistent echo is dropped and the verbatim intake receipt is the fact.
        norm_user = " ".join(question.lower().split())
        kept_pairs = []
        for claim, owner in zip(valid, valid_owner, strict=True):
            if owner in intake_ids and claim.ctype != "conversational":
                norm_claim = " ".join(claim.text.lower().split())
                # TRANSFORMATIONS ARE LEGITIMATE (consensus-4 fix 5) but CORRUPTION
                # IS NOT (consensus-3): both laws hold, and near-identity separates
                # them deterministically. A claim that MIRRORS one of the user's own
                # lines while changing a value is corruption (measured: "18
                # attendees" shipped as "3"); a genuine transformation — a summary,
                # an extraction — is structurally different from every line it came
                # from (measured: this guard killed a ferry summary and a timetable).
                import difflib
                mirrors = max(
                    (difflib.SequenceMatcher(None, norm_claim, " ".join(line.lower().split())).ratio()
                     for line in question.splitlines() if line.strip()),
                    default=0.0,
                )
                if norm_claim and norm_claim not in norm_user and mirrors >= 0.85:
                    out.append(f"  ! intake echo dropped — not the user's words: {claim.text[:60]}")
                    continue
            kept_pairs.append((claim, owner))
        valid = [c for c, _ in kept_pairs]
        valid_owner = [o for _, o in kept_pairs]
    for claim, owner in zip(valid, valid_owner, strict=True):
        # GROUNDED shipped claims become session receipts (consensus fix 1): "the
        # Bitcoin price from Turn 1" must have a receipt to bind to next turn.
        # Unverified claims deliberately do NOT mint — the referent gap law depends
        # on ungrounded numbers having no home (the 0.50 -> 25 cascade).
        if owner in intake_ids:
            continue  # the verbatim intake receipt is the fact; echoes never mint
        if claim.ctype == "observed" and claim.cited():
            candidate = f"answered in an earlier turn: {claim.text}"
            if candidate not in new_facts.values():
                # The session-disagreement marker was REMOVED by consensus-2 (71 false
                # flags in one run — token overlap is not fact identity; rule 0.7:
                # replace, don't grandfather). Typed same-fact disagreement returns only
                # on the verifier tier, keyed by immutable claim identity.
                new_facts[f"s{len(new_facts) + 1}"] = candidate
    while len(new_facts) > 16:
        new_facts.pop(next(iter(new_facts)))

    for lit in sorted(demanded_literals):
        # DELIVERABLES BORN AT EXTRACTION (consensus-3 fix 3, completed after a live
        # probe: the model's claim was the fragment "preempted" and every rescue
        # missed): when the user DICTATED an exact output and no shipped claim
        # carries it, the kernel ships the user's own literal verbatim — this is the
        # user's text, not kernel prose.
        ordered = (any(lit in fo for fo in format_orders)
                   or re.search(r"(?:output|reply|say|type|print|write)[^.\n]{0,60}" + re.escape(lit),
                                question, re.IGNORECASE))
        if ordered and not any(lit in c.text for c in valid):
            out.append(f"  + demanded literal shipped from the user's own order: {lit[:50]}")
            valid.append(TypedClaim(text=lit, ctype="conversational", ref=""))
            valid_owner.append(obligations[0].id if obligations else "turn")
    # ROUND-002 CLAUSE 2 — BYTE-EXACT DOMINANCE.
    # Appending the literal is not owning the answer. Measured (round-002 Probe 1): with the
    # literal captured, a turn whose extraction had invented five obligations from prose the user
    # called "unrelated" still shipped `silver market / EMBER-X604 / Refused — capability gap…`.
    # The user asked for the payload AND NOTHING ELSE; sibling claims born of a mis-extraction are
    # not part of the answer.
    #
    # SCOPE (the council's floor, not a blanket suppression): dominance needs a captured
    # user-verbatim payload AND a single-purpose byte-exact contract. It is inert when the demanded
    # value is COMPUTED rather than echoed ("Return ONLY the final numeric total" captures no
    # payload), which is what keeps the arithmetic turns whole.
    #
    # HONEST-DECLINE FLOOR: a refusal belonging to the byte obligation ITSELF still ships. Only
    # spurious SIBLING claims are dropped. Dominance must never convert a genuine unservability
    # into a silent echo — that would trade this defect for a worse one.
    # Single-purpose = the user marked the payload as the WHOLE deliverable. Without this a
    # multi-ask turn ("What is 2+2? Also reply with exactly TOKEN-1.") would have its other
    # answers suppressed, so the marker is what keeps dominance from hijacking a real multi-ask.
    _single_purpose = bool(re.search(
        r"\bnothing else\b|\bnothing added\b|\bnothing more\b|\bnothing but\b"
        r"|\bno other (?:characters|content|text|words|output)\b|\bonly\s*:",
        question, re.IGNORECASE))
    # (applied below, once every claim including settled refusals has been assembled)
    # BLOCK-B CHIPS: typed operations and field recall are KERNEL-OWNED
    # deliverables — the op executes over the chips and its result IS the answer,
    # so the model's echo-prone prose can never displace it (t16/19/20/137 class).
    _machine_turn = any(r["lane"] in ("machine", "web_lookup") for r in rows)
    _setter_ack = False
    if ledger_mints and valid is not None:
        # SETTER ACK (operator live round, lane-2: the setter rendered a
        # truncated echo and once minted LIST CHIPS from its own junk): a
        # constraint-installing turn ships a deterministic ack; the constraint
        # applies from the NEXT answer (activation semantics unchanged).
        _n = ledger_mints[0].get("ttl", 1)
        _kinds = ", ".join(_describe_mint(e) for e in ledger_mints)
        valid = [TypedClaim(text=f"noted — {_kinds} applies to your next {_n} answer(s).",
                            ctype="conversational", ref="")]
        valid_owner = [obligations[0].id if obligations else "turn"]
        out.append("  * constraint setter: deterministic ack ships; model prose displaced")
        # ROUND-007 Root C: the ack is the ONLY visible output of a setter turn. Turn 006 spawned a
        # phantom arithmetic obligation from the frame count ("next THREE" -> "3-1"), it settled
        # REFUSED, and the refusal-append loop below — keyed on _rendered_obs, which holds only the
        # ack's owner — appended "Refused — cannot compute: operands ['3']" after the ack. A setter
        # turn has no legitimate collateral to render; this flag makes the ack terminal.
        _setter_ack = True
    _echo_payload = _repeat_contract(question)
    _list_noun_ok = True
    if _list_ops and _live_list:
        _lbl_toks = set(re.findall(r"[a-z]+", _live_list[0].get("label", "")))
        _q_toks = set(re.findall(r"[a-z]+", question.lower()))
        _naming_nouns = _q_toks & {"fruit", "fruits", "robot", "robots", "mountain",
                                   "mountains", "server", "servers", "name", "names",
                                   "item", "items", "entry", "entries", "list",
                                   "position", "positions", "one", "ones"}
        # BLOCK-C seam 2 identity: an op that NAMES a noun family must match the
        # live list's label family (t8: "delete B and F" after creating ROBOT
        # names must not mutate the FRUIT list). Purely positional asks
        # ("swap the second and fifth") carry no noun and bind to the live list.
        _specific = _naming_nouns - {"name", "names", "item", "items", "entry",
                                     "entries", "list", "position", "positions",
                                     "one", "ones"}
        # singular/plural are the same noun family ("fruit list" names the
        # 'fruits' chips): compare stems, not exact tokens.
        def _stem(w: str) -> str:
            return w[:-1] if w.endswith("s") else w
        if _specific and not ({_stem(w) for w in _specific}
                              & {_stem(w) for w in _lbl_toks}):
            _list_noun_ok = False
        # lettered ops (delete B and F / move G) additionally require the live
        # list to have been created WITH letters (>=6 items labeled A..): if the
        # live list is shorter than the highest letter referenced, it is the
        # wrong list.
        for _o in _list_ops:
            if _o["op"] in ("remove_labels", "move_front") and _o.get("labels" if _o["op"] == "remove_labels" else "label"):
                _letters = _o.get("labels") or [_o.get("label")]
                _maxi = max(ord(x) - ord("A") + 1 for x in _letters if x)
                if _maxi > len(_live_list):
                    _list_noun_ok = False
    if _echo_payload is not None and not _machine_turn:
        # BLOCK-C seam 7 (t47/t69): the repeat contract ships the user's own
        # quoted payload byte-exact from the kernel — model prose (which mangled
        # it live, twice) is displaced. Same doctrine as typed list ops: this is
        # mechanical enforcement of an explicit typed contract, not routing.
        receipts["echo"] = f"repeat contract, the user's own quoted payload: {_echo_payload}"
        evidence_lines.append(f"echo: {receipts['echo']}")
        valid = [TypedClaim(text=_echo_payload, ctype="observed", ref="echo")]
        valid_owner = [obligations[0].id if obligations else "turn"]
        out.append("  * repeat contract: the quoted payload ships byte-exact; model prose displaced")
    elif _list_ops and _live_list and not _machine_turn and _list_noun_ok:
        _items = [c["value"] for c in _live_list]
        _result, _op_notes = _apply_list_ops(list(_items), _list_ops)
        for _n in _op_notes:
            out.append(f"  ! list op: {_n}")
        _sel = any(o["op"] == "select" for o in _list_ops)
        _text = "\n".join(f"{i}. {v}" for i, v in enumerate(_result, 1))
        receipts["listop"] = ("typed list operation " +
                             ", ".join(o["op"] for o in _list_ops) +
                             f" over {len(_items)} chips -> " + _text.replace("\n", " · "))
        evidence_lines.append(f"listop: {receipts['listop']}")
        valid = [TypedClaim(text=_text, ctype="observed", ref="listop")]
        valid_owner = [obligations[0].id if obligations else "turn"]
        out.append(f"  * chips: op result ships ({len(_result)} item(s)); model prose displaced")
        if not _sel:
            _keep_label = _live_list[0].get("label", "list") if _live_list else "list"
            chips = ([c for c in chips if c.get("kind") != "list"]
                     + (_mint_list_chips(_text, label=_keep_label) or []))
    elif _list_ops and _live_list and not _machine_turn and not _list_noun_ok:
        out.append("  ! list op refused: the referenced list does not match the live"
                   f" '{_live_list[0].get('label','list')}' chips — no edit executed")
        valid = [TypedClaim(text=("That edit does not match the current list "
                                  f"({_live_list[0].get('label','list')}) — nothing was changed."),
                            ctype="conversational", ref="")]
        valid_owner = [obligations[0].id if obligations else "turn"]
    elif (_recall_hit is not None and not _machine_turn
          and not any(r.startswith(("ob",)) and ("-calc" in r or "-web" in r or "-page" in r)
                      for r in receipts)
          and not any(row["lane"] == "arithmetic" for row in rows)):
        # BLOCK-C seam 2 precedence: a turn that produced (or will produce) its own
        # derivations/effects for the ask NEVER yields to chip recall — fresh
        # results always beat stored state (t28: 714 displaced by stale 73).
        _ref = "chip-recall"
        receipts[_ref] = f"stored field chip value: {_recall_hit}"
        evidence_lines.append(f"{_ref}: {receipts[_ref]}")
        valid = [TypedClaim(text=_recall_hit, ctype="observed", ref=_ref)]
        valid_owner = [obligations[0].id if obligations else "turn"]
        out.append("  * chips: field recall ships the value, never the inventory")
    # BLOCK-B (t69-71: "say it is unavailable" answered with zero bytes): REFUSED
    # renders its reason as a visible typed sentence — silence is not refusal. The
    # nothing-was-running cancel renders too (the informative case). Slot-ordered
    # like any other claim; the empty-if-true contract below still overrides.
    _rendered_obs = set(valid_owner)
    _refusal_norms: list[str] = []
    for ob in obligations:
        if _setter_ack:
            # A setter turn ships the deterministic ack and nothing else (round-007 Root C):
            # no collateral refusal or cancellation may append to it.
            break
        if ob.id in _rendered_obs:
            continue
        if ob.status == "refused" and ob.reason:
            # BLOCK-C seam 8 (t71): two obligations refusing on the SAME
            # limitation render ONE sentence — "capability gap: disk
            # maintenance" + "... disk maintenance limitation" is one fact,
            # not two. Containment either way collapses; the first stands.
            _norm = " ".join(ob.reason.lower().split())
            if any(_norm in seen or seen in _norm for seen in _refusal_norms):
                out.append(f"  * duplicate refusal collapsed for {ob.id} — the limitation is already rendered")
                continue
            _refusal_norms.append(_norm)
            valid.append(TypedClaim(text=f"Refused — {ob.reason}", ctype="conversational", ref=""))
            valid_owner.append(ob.id)
        elif ob.status == "cancelled" and (ob.reason or "").startswith("nothing was running"):
            valid.append(TypedClaim(text=ob.reason, ctype="conversational", ref=""))
            valid_owner.append(ob.id)
    _err_like = [c for c in valid
                 if re.fullmatch(r'\s*\{\s*"error"\s*:.*\}\s*', c.text, re.DOTALL)]
    if _err_like:
        out.append(f"  ! render guard: {len(_err_like)} raw error object(s) dropped — a failure record is not an answer")
        _keepE = [(c, o) for c, o in zip(valid, valid_owner, strict=True)
                  if not re.fullmatch(r'\s*\{\s*"error"\s*:.*\}\s*', c.text, re.DOTALL)]
        valid = [c for c, _ in _keepE]
        valid_owner = [o for _, o in _keepE]
    _underrun_footer: tuple[int, int] | None = None   # ROUND-004: (shipped_words, required_words)
    _xw = next((int(e["arg"]) for e in active_ledger if e["kind"] == "exact_words"), None)
    if _xw is not None and valid:
        _fit = [(c, o) for c, o in zip(valid, valid_owner, strict=True)
                if "\n" not in c.text.strip() and len(c.text.split()) == _xw]
        _over = [(c, o) for c, o in zip(valid, valid_owner, strict=True)
                 if "\n" not in c.text.strip() and len(c.text.split()) > _xw]
        if not _fit and _over:
            _c, _o = _over[0]
            _trail = _c.text.rstrip()[-1] if _c.text.rstrip()[-1:] in ".!?" else ""
            _fit = [(TypedClaim(text=" ".join(_c.text.split()[:_xw]).rstrip(",;:") + _trail,
                                ctype=_c.ctype, ref=_c.ref, refs=_c.refs), _o)]
            out.append("  * exact-words render: overrun truncated to the model's own first words")
        if _fit:
            if len(_fit) < len(valid):
                out.append(f"  * exact-words render: {len(valid) - len(_fit)} non-conforming claim(s) dropped")
            valid = [c for c, _ in _fit]
            valid_owner = [o for _, o in _fit]
        else:
            # ROUND-008 (fail-closed exact_words, SECOND COMMIT PATH): this best-underrun
            # fallback could resurrect a claim the gate never vetted — the gate's word check
            # is conditioned on len(rows) == 1, so a multi-row turn's short claim reached
            # `valid` un-gated and shipped here (found independently by DeepSeek and Gemini).
            # Under a HARD exact_words contract the renderer now holds the same law as the
            # gate: no claim fits the count -> nothing commits, the seam-10 liveness terminal
            # ships the named failure. Overruns above are a compliant transform and stay.
            for _c, _o in zip(valid, valid_owner, strict=True):
                if "\n" not in _c.text.strip() and 0 < len(_c.text.split()) < _xw:
                    _format_unsatisfied[_o] = (len(_c.text.split()), _xw)
                    # ROUND-011 UX closure: the renderer-rejected underruns join the
                    # closest-attempt pool too (the gate never saw these — multi-row path).
                    _xw_rejected.setdefault(_o, []).append(_c.text)
            out.append("  ! exact-words render: no claim satisfies the count — the named failure ships")
            valid = []
            valid_owner = []
    if any(e["kind"] == "exact_words" for e in active_ledger) and len(valid) > 1:
        # DM-2 Track K (T15: two 3-word lines = six words under exactly-three):
        # the contract binds the ANSWER, so exactly one claim renders.
        out.append(f"  * exact-words contract: {len(valid) - 1} extra claim(s) suppressed — one answer, one count")
        valid = valid[:1]
        valid_owner = valid_owner[:1]
    _ENUM_ASK_RE = re.compile(r"which (?:identifiers|product names|names|entities)"
                              r"|identifiers or product names|list (?:the |all )?(?:identifiers|entities)",
                              re.IGNORECASE)
    _ID_TOKEN_RE = re.compile(r"\b(?:[A-Z]{2,}-\d[\w-]*|[A-Z]{2,}\d[\w-]*|\d[A-Z]{2,}\b|[A-Z][a-z]+\d\w*)\b")
    for _ob in obligations:
        if not _ENUM_ASK_RE.search(_ob.description):
            continue
        _cands = list(dict.fromkeys(_ID_TOKEN_RE.findall(question)))
        _idx = next((i for i, o in enumerate(valid_owner) if o == _ob.id), None)
        if not _cands or _idx is None:
            continue
        _missing = [c for c in _cands if c not in valid[_idx].text]
        if _missing:
            # ENUMERATIVE COVERAGE (operator live round, lane-4: EC2 dropped
            # while RX-731/2FA/Neo4j shipped): candidates are the user's own
            # identifier tokens; the completion is verbatim user text.
            _c = valid[_idx]
            valid[_idx] = TypedClaim(text=_c.text.rstrip(".") + ", " + ", ".join(_missing),
                                     ctype=_c.ctype, ref=_c.ref, refs=_c.refs)
            out.append(f"  + enumerative coverage: re-added from your message: {', '.join(_missing)}")
    _q_norm = " ".join(question.lower().split())
    _q_quoted_spans = [m.group(0).lower() for m in
                       re.finditer(r'"[^"]{2,}"|\'[^\']{2,}\'|`[^`]{2,}`', question)]

    def _is_ask_echo(c: TypedClaim) -> bool:
        t_norm = " ".join(c.text.lower().split())
        if len(t_norm) < 20 or t_norm not in _q_norm:
            return False
        if c.ref == "echo" or c.text.strip() in demanded_literals:
            return False              # repeat contracts / demanded payloads are legit
        if any(c.text.strip() and c.text.strip() in fo for fo in format_orders):
            return False              # a format-ordered deliverable is never an echo
        if any(t_norm in span for span in _q_quoted_spans):
            return False              # quoted payloads are data, not the ask
        # data-intake turns may restate the payload
        return not re.search(r"\b(?:store|record|remember|note)\b|=", question, re.IGNORECASE)

    _echoes = [c for c in valid if _is_ask_echo(c)]
    if _echoes:
        out.append(f"  ! render echo ban: {len(_echoes)} claim(s) restating the ask dropped"
                   " — a request is never its own answer (T19/T32/T41 class)")
        keep = [(c, o) for c, o in zip(valid, valid_owner, strict=True) if not _is_ask_echo(c)]
        valid = [c for c, _ in keep]
        valid_owner = [o for _, o in keep]
    if _empty_if_true_satisfied(question, receipts):
        # TIER 2: the empty-if-true contract holds — ship ZERO visible bytes as the
        # successful answer (an empty answer span, not an "unanswerable" failure). Every
        # synthesized claim is suppressed; the else-branch literal never ships.
        out.append("  * empty-if-true contract satisfied — the condition holds, shipping zero bytes")
        valid = [TypedClaim(text="", ctype="conversational", ref="")]
        valid_owner = [obligations[0].id if obligations else "turn"]
    if valid:
        # SLOT-ORDERED RENDER (consensus-5 item 5, measured: a numbered seven-part
        # answer shipped item 5 after items 6/7 because claims render in arrival
        # order): primary order is the obligation slot; intra-slot order preserved.
        def _slot(owner: str) -> int:
            return int(owner[2:]) if owner.startswith("ob") and owner[2:].isdigit() else 10_000
        pairs = sorted(zip(valid, valid_owner, strict=True), key=lambda co: _slot(co[1]))
        valid = [c for c, _ in pairs]
        valid_owner = [o for _, o in pairs]

        # GROUNDING INVARIANT (mixed-40 fresh S7 + reviews wf_8381a777/wf_9d184b83).
        # A calc grounds as an authoritative [receipt:] fact ONLY when it is
        # SELF-CONTAINED — computed from the user's own literals ("inputs from user")
        # or from THIS slot's own receipts. A calc drawing on CROSS-SLOT sources is
        # every live-value ratio and every register-blind mis-bind that pulls another
        # slot's number: the model routinely binds the right SOURCE but the wrong
        # OPERAND (S7 live: 0.9036/0.35035 = 2.579, neither the coin's price), which no
        # pre-mint gate reliably catches. Such a value ships FLAGGED, never grounded.
        # One pass at the render boundary closes the whole grounded-wrong class no
        # matter which path (synth token, injection, backstop) produced the claim —
        # replacing a fleet of leaky per-shape gates with a single structural rule.
        def _calc_self_contained(_ref: str, _own: str, _depth: int = 0) -> bool:
            if _depth > 8:
                return False  # cycle / pathological-depth guard
            # a machine/recall value COMPUTED for another slot is a foreign number
            # (review wf_13148344 #7: a mis-bound cross-slot -mac laundered a value)
            if _ref.endswith(("-mac", "-rec")) and _ref.split("-", 1)[0] != _own:
                return False
            if "-calc" not in _ref:
                return True   # this slot's web/page/user/own-machine receipts are fine
            # OWNER FIRST (review wf_13148344 #1/#4): a calc owned by a DIFFERENT slot
            # is foreign REGARDLESS of type — every extract calc carries "inputs from
            # user", so that exemption must never precede the ownership test.
            if _ref.split("-", 1)[0] != _own:
                return False
            _b = receipts.get(_ref, "")
            if "inputs from user" in _b:
                return True   # THIS slot's extract calc over the user's own literals
            _srcs = re.findall(r"\bob\d+-\w+", _b.split("sources:", 1)[1]) if "sources:" in _b else []
            for _s in _srcs:
                if not _s.startswith(_own + "-"):
                    return False  # a direct cross-slot source
                # TRANSITIVE (review wf_13148344 #6): a same-owner source calc that is
                # ITSELF not self-contained (ob3-calc2 -> ob3-calc1 -> ob1-web1) still
                # launders a cross-slot value one hop up. Follow the chain.
                if "-calc" in _s and not _calc_self_contained(_s, _own, _depth + 1):
                    return False
            return True
        for _i, (_c, _o) in enumerate(zip(list(valid), valid_owner, strict=True)):
            if _c.ctype == "observed":
                _bad = [r for r in _c.cited() if not _calc_self_contained(r, _o)]
                if _bad:
                    out.append(f"  * {_o} value demoted to unverified — its calc draws on cross-slot sources {_bad}; the operand binding cannot be trusted grounded")
                    valid[_i] = TypedClaim(text=_c.text, ctype="unverified", ref="")
                    continue
            # FC-C, PER-OWNER (mixed-40 S19 leak; scoping fix review wf_e771937c #2): a
            # value THIS obligation's harvest gate REJECTED, re-emerging UNMARKED on
            # this obligation's own line, is tagged unverified so no rejected value
            # ships as sourced fact. Scoped to the OWNER's rejected set — the old
            # turn-wide union false-branded an unrelated correct number in another
            # slot's line when the digits happened to collide.
            _cur = valid[_i]
            if (not _cur.cited() and _cur.ctype == "observed"
                    and (_number_tokens(_cur.text) & rejected_nums.get(_o, set()))):
                out.append(f"  ! reconciliation: {_o} re-emitted a harvest-rejected value unmarked — tagged unverified")
                valid[_i] = TypedClaim(text=_cur.text, ctype="unverified", ref="")

        out.append("")
        # BLOCK-B multipart backstop: a slot with NOTHING (open, no claim, no
        # rendered terminal) ships UNKNOWN rather than silently vanishing (A8.3).
        if len(obligations) >= 2:
            # ROUND-019 R2: a coordinated slot's semantics ship through typed
            # ownership — it is covered, never backstopped with UNKNOWN.
            _covered = set(valid_owner) | set(_coordinated_children.keys()) | set(
                child for siblings in _coordinated_children.values() for child in siblings
            )
            for _ob in obligations:
                if _ob.id in _covered:
                    continue
                # ARITHMETIC BOUND TO THE CALC RECEIPT, not the model's prose
                # (mixed-40 fresh S9/S10/S12, Flash-D #1): the model re-typed the
                # equation with wrong OPERATORS ("58 - 13 + 194 = 560") so the
                # equation-false gate correctly rejected its claim and the slot was
                # declared unanswerable — but the kernel already computed the answer
                # deterministically (ob2-calc = 560). An uncovered obligation that
                # OWNS a correct calc receipt ships THAT receipt (correct equation +
                # value), overriding a premature "unanswerable"; only a slot with no
                # calc falls back to UNKNOWN.
                # Reaching this body already means the slot shipped NOTHING (not in
                # _covered). "closed" is included because the post-render coverage loop
                # closes a slot to "closed" on a FALSE verifier all-covered verdict even
                # though nothing of its own reached the render (review wf_e771937c): a
                # computed value must still ship, never silently vanish under a wrong
                # coverage verdict. (refused/cancelled are settled deliberately and are
                # left alone.)
                _recoverable = ("open", "declared_unanswerable", "closed")
                _cr = next((r for r in receipts if r.startswith(_ob.id + "-calc")
                            and "computed locally" in receipts.get(r, "")
                            and _calc_self_contained(r, _ob.id)), None)
                if _cr and _ob.status in _recoverable:
                    valid.append(TypedClaim(text=receipts[_cr], ctype="observed", ref=_cr))
                    valid_owner.append(_ob.id)
                    continue
                # A cross-slot calc (a live-value ratio) exists but is not groundable
                # by the invariant — ship its VALUE flagged-unverified rather than
                # vanishing the slot (review wf_13148344 #5 / wf_e771937c: the correct
                # 2.70 ratio disappeared entirely). Flagged, never grounded, never lost.
                _cr_any = next((r for r in receipts if r.startswith(_ob.id + "-calc")
                                and "computed locally" in receipts.get(r, "")), None)
                if _cr_any and _ob.status in _recoverable:
                    valid.append(TypedClaim(text=receipts[_cr_any], ctype="unverified", ref=""))
                    valid_owner.append(_ob.id)
                elif _ob.status == "open":
                    valid.append(TypedClaim(text="UNKNOWN", ctype="conversational", ref=""))
                    valid_owner.append(_ob.id)
            _pairs2 = sorted(zip(valid, valid_owner, strict=True), key=lambda co: _slot(co[1]))
            valid = [c for c, _ in _pairs2]
            valid_owner = [o for _, o in _pairs2]
        # ROUND-002 CLAUSE 2 — BYTE-EXACT DOMINANCE. Applied LAST, immediately before render,
        # because every earlier placement was overtaken by a later claim source: placed before the
        # settled-refusal loop a sibling refusal survived; placed before the coverage-recovery loop
        # that loop re-added the excluded slot's calc receipt (measured — the turn shipped
        # `EMBER-X604 / computed locally: Bitcoin wallet: 2719 = 2719`). Dominance is a statement
        # about the FINAL bytes, so it belongs at the final boundary.
        #
        # SCOPE: needs a captured user-verbatim payload AND a single-purpose byte-exact contract.
        # Inert when the demanded value is COMPUTED rather than echoed, which is what keeps the
        # arithmetic turns whole.
        valid, valid_owner, _dom_notes = _byte_exact_dominance(
            valid, valid_owner, demanded_literals, _single_purpose)
        out.extend(_dom_notes)
        # FC-D (mixed-40 S35 crash): the final render must NEVER throw the turn.
        # render_typed_answer re-validates and raises EvidenceTypeError on any invalid
        # claim (e.g. an evidence excerpt truncated mid-number, whose partial digit is
        # not in the full receipt). Previously that propagated out of run_turn as a
        # hard crash with an EMPTY answer. A rejection that throws is not a rejection:
        # drop the offending claim(s) with a named note and render the survivors.
        try:
            rendered = render_typed_answer(valid, receipts)
        except EvidenceTypeError:
            _kept: list[TypedClaim] = []
            _kept_owner: list[str] = []
            for _c, _o in zip(valid, valid_owner, strict=True):
                try:
                    validate_claims([_c], receipts)
                except EvidenceTypeError as _rexc:
                    out.append(f"  ! render-guard dropped an invalid claim (graceful, no crash): {_rexc.reason}")
                    continue
                _kept.append(_c)
                _kept_owner.append(_o)
            valid, valid_owner = _kept, _kept_owner
            rendered = render_typed_answer(valid, receipts)
        # GOBLIN inv 9 (DERIVED TAINT): an answer that cites a receipt from an untrusted
        # origin (web/page/gap or a machine read) carries a taint marker so the model's
        # summary cannot launder the restriction away. Flag-gated (default OFF → the render
        # path is byte-identical until enabled). Advisory: it marks, never withholds.
        _taint_marks: tuple[str, ...] = ()
        if flag_enabled("derived_taint"):
            _cited_refs = re.findall(r"\[receipt:([^\]]+)\]", rendered)
            _taint_marks = tuple(dict.fromkeys(
                r for r in _cited_refs
                if any(m in r for m in _WORLD_REF_MARKS) or "-mac" in r))
        # (FC-C harvest-rejected reconciliation now runs PER-OWNER in the grounding
        # pass above, before render — the old turn-wide string union that ran here
        # false-branded an unrelated correct number across obligations, review
        # wf_e771937c #2.)
        # BLOCK-C seam 9 (final-byte contract dominance, t6/t18/t29/t31 class): the
        # strict signal is parsed from the QUESTION ITSELF, not only extraction's
        # format field — "return ONLY", "JUST the", "nothing else", "exactly" in
        # the ask strip ALL provenance decoration from the final bytes; citations
        # live in the record. This governs EVERY final render.
        strict = (any(re.search(r"\b(only|exactly|strictly|raw|nothing)\b", fo, re.IGNORECASE)
                      for fo in format_orders)
                  or bool(re.search(
                      r"\b(?:return|reply|give(?: me)?|output|answer|say|write|repeat|echo)\b[^.?!\n]{0,40}\b(?:only|just|exactly)\b"
                      r"|\b(?:only|just)\b[^.?!\n]{0,3}(?:the\s+)?\w+\s*(?:field|value|number|name|id|version|string|word)"
                      r"|nothing else|no other characters|no explanation",
                      question, re.IGNORECASE)))
        # ROUND-004: the shortfall is DECLARED TO THE READER, outside the answer span.
        # A marker inside the bytes would itself break the count it reports (a "[3/4 words]" tag
        # makes a 3-word answer 5 words); a marker only in the transcript leaves the reader unable
        # to tell a short answer from a compliant one — the adversarial seat's objection, and the
        # same class as the defect being repaired: bytes that do not satisfy the active contract
        # with the failure recorded where nobody looks. A footer AFTER \x02 is visible while
        # _extract_answer (which returns only the \x01..\x02 span) keeps the contract bytes clean.
        def _declare_shortfall() -> None:
            if _underrun_footer:
                _s, _k = _underrun_footer
                out.append(f"(format not met: {_s} of {_k} words — the model's own best effort; "
                           "the kernel does not pad an answer to reach a count)")

        if format_orders or strict:
            bare = re.sub(r" \[(?:receipt:[^\]]+|unverified - model memory|stipulated|memory:[^\]]+)\]", "", rendered)
            bare = _apply_ledger_text(bare, active_ledger)
            out.append("\x01" + bare + "\x02")
            _declare_shortfall()
            markers = re.findall(r"\[(receipt:[^\]]+)\]", rendered)
            if markers and not strict:
                # Under a strict contract NOTHING appends after the payload
                # (consensus-4 fix 1: the render validator is last in the chain).
                out.append("grounding: " + ", ".join(dict.fromkeys(markers)))
            # inv 9: a strict/format-order contract forbids appending to the shipped bytes,
            # so the derived-taint is recorded in the transcript, not shipped.
            if _taint_marks:
                out.append("derived-taint (recorded, not shipped under output contract): "
                           + ", ".join(_taint_marks))
        else:
            _body = _apply_ledger_text(rendered, active_ledger)
            if _taint_marks:
                # inv 9: mark the answer inline so an untrusted-derived answer is never
                # presented as if it were the runtime's own grounded truth.
                _body += "\n[derived-taint: untrusted origin — " + ", ".join(_taint_marks) + "]"
            out.append("\x01" + _body + "\x02")
            _declare_shortfall()
    elif obligations:
        # BLOCK-C seam 10 (constrained-render liveness, t41 class, Terra amendment
        # 2): an accepted answerable turn NEVER terminates in silence. A format
        # constraint may constrain a response; it may never convert model format
        # failure into answer_present=false. When every claim died before the
        # terminal render, the NAMED failure ships as visible bytes.
        _reasons = [ob.reason for ob in obligations if getattr(ob, "reason", "")]
        # ROUND-008: a claim that WAS produced but died on the exact_words contract is a
        # FORMAT_UNSATISFIED failure — the banner must name it truthfully instead of
        # reporting "no claim survived validation" (Ling post-R2 attack 2, upheld).
        _reasons += [f"format unsatisfied: best model effort was {_m} of {_n} words"
                     for _m, _n in _format_unsatisfied.values()]
        _line = ("Cannot answer this turn — "
                 + ("; ".join(dict.fromkeys(_reasons)) or "no claim survived validation"))
        _named = ", ".join(f"{e['kind']}({e['arg']})" for e in active_ledger)
        if _named:
            _line += f" (active format constraint: {_named})"
        out.append("  ! liveness: zero claims at terminal render — the named failure ships visibly")
        # ROUND-011 FINAL UX CLOSURE: the INTERNAL TRUTH stays FORMAT_UNSATISFIED — the
        # settle reason, the transcript, and an explicit receipt all carry it — but a
        # normal conversational user no longer receives the raw failure line. They get a
        # short useful message plus the CLOSEST MODEL-AUTHORED attempt, selected purely
        # mechanically (abs(candidate_word_count - N); tie -> earliest original attempt).
        # The displayed candidate is never invented, edited, reordered, merged, or
        # semantically ranked — it is the model's own text, verbatim. The no-candidate
        # case (nothing produced at all under an armed exact_words contract) gets the
        # same short message with NO attempt shown — never a fabricated one.
        _xw_active = any(e["kind"] == "exact_words" for e in active_ledger)
        _nothing_produced = (not _format_unsatisfied and _xw_active
                             and all((ob.reason or "").startswith(
                                 ("no claim produced", "no conversational reply"))
                                 for ob in obligations))
        if _format_unsatisfied or _nothing_produced:
            if _format_unsatisfied:
                for _ob_id, (_m, _n) in _format_unsatisfied.items():
                    receipts[_ob_id + "-format"] = (f"FORMAT_UNSATISFIED: best model effort "
                                                    f"{_m} of {_n} words")
                out.append("  ! format failure: FORMAT_UNSATISFIED — "
                           + "; ".join(f"{_ob_id} best model effort {_m} of {_n} words"
                                       for _ob_id, (_m, _n) in _format_unsatisfied.items()))
            else:
                out.append("  ! format failure: FORMAT_UNSATISFIED — no usable candidate "
                           "was produced under the exact-word contract")
            # STRICT-OUTPUT EXCEPTION (regression pin): when the ask itself demands bare
            # output — ONLY the answer, nothing else, no explanation, an exact/byte-exact
            # echo or literal — explanatory prose in the answer bytes would itself violate
            # the contract. The failed candidate stays uncommitted, the raw named failure
            # remains the shipped bytes, and the human-readable explanation lives in the
            # transcript/receipts (the typed failure surface), never in the answer span.
            _strict_out = (
                re.search(
                    r"\b(?:return|reply|give(?: me)?|output|answer|say|write|repeat|echo)\b"
                    r"[^.?!\n]{0,40}\b(?:only|just)\b"
                    r"|\b(?:only|just)\b[^.?!\n]{0,3}(?:the\s+)?\w+\s*"
                    r"(?:field|value|number|name|id|version|string|word)"
                    r"|nothing else|no other (?:characters|content|text|words|output)"
                    r"|no explanation",
                    question, re.IGNORECASE)
                or any(re.search(r"nothing else|no explanation|no other "
                                 r"(?:characters|content|text|words|output)"
                                 r"|\bonly the\b|\bjust the\b", fo, re.IGNORECASE)
                       for fo in format_orders)
                or bool(demanded_literals))
            if not _strict_out:
                if _format_unsatisfied:
                    _n = next(iter(_format_unsatisfied.values()))[1]
                    _first_ob = next(iter(_format_unsatisfied))
                else:
                    _n = int(next(e["arg"] for e in active_ledger
                                  if e["kind"] == "exact_words"))
                    _first_ob = None
                _cloud_ok = ("/" in _ARBITER_MODEL) and not _VETO_RE.search(question)
                _try = "Try a cloud model" if _cloud_ok else "Try another model"
                # closest attempt: mechanical selection only
                _cands = _xw_rejected.get(_first_ob, []) if _first_ob else []
                _closest = None
                if _cands:
                    _closest = min(
                        _cands,
                        key=lambda t: (abs(len(t.split()) - _n), _cands.index(t)))
                if _closest is not None:
                    _line = (f"I couldn\u2019t meet the exact {_n}-word requirement. "
                             f"{_try}, or use my closest attempt:\n{_closest}")
                else:
                    # never fabricate a candidate
                    _line = (f"I couldn\u2019t meet the exact {_n}-word requirement. "
                             f"{_try}.")
        out.append("\x01" + _line + "\x02")
    out.append("")
    out.append(f"receipts: {len(receipts)} · tape: {len(runner.journal.entries())} effects · carried-over unresolved: {len(still_open)}")
    # Evidence rides the RECORD (journal + review bus), hidden from the terminal by the
    # \x03..\x04 span (consensus item 6A: round 1 of the first review graded claims
    # against receipt bodies neither reviewer could see in the bus artifact).
    # BLOCK-C seam 1 postcondition (defense in depth): a torn stamp with a web
    # effect on the tape is an IMPOSSIBLE STATE — the turn fails loudly rather
    # than ship as if the veto held.
    if not stamps["web"]:
        _web_effs = [e.get("effect_id", "") for e in runner.journal.entries()
                     if isinstance(e, dict) and str(e.get("effect_id", "")).startswith("web.")]
        if _web_effs:
            out.append(f"  !! STAMP POSTCONDITION VIOLATED: web effects {_web_effs} under a"
                       " torn stamp — turn FAILED; a gate is missing and must be found")
            for _ob in obligations:
                if _ob.status == "open":
                    txn.declare_failed(_ob.id, "stamp postcondition violated: web effect under torn stamp")
    _final_answer = _extract_answer("\n".join(out))
    if _final_answer and not _list_ops and not ledger_mints:
        _new_list = _mint_list_chips(_final_answer, label=_list_label_from(question), question=question)
        if _new_list:
            chips = [c for c in chips if c.get("kind") != "list"] + _new_list
            out.append(f"  * chips: minted {len(_new_list)} list chips from the shipped answer")
    for _fc in _mint_field_chips(question):
        _prev = next((c for c in chips if c.get("kind") == "field" and c["label"] == _fc["label"]), None)
        if _prev:
            _prev.update(value=_fc["value"], revision=_prev.get("revision", 1) + 1)
        else:
            chips.append(_fc)
    new_facts[_CHIPS_KEY] = json.dumps(chips)
    if ledger_mints:
        new_facts[_LEDGER_KEY] = json.dumps(ledger_mints)
    else:
        _remaining = [dict(e, ttl=e["ttl"] - 1) for e in active_ledger if e["ttl"] - 1 > 0]
        new_facts[_LEDGER_KEY] = json.dumps(_remaining)
    out.append("\x03EVIDENCE (recorded for review):\n" + "\n".join(evidence_lines) + "\x04")
    # TIER 6 (harness reconciliation): stash a STRUCTURED per-turn terminal state on
    # the tape, so the journal row records terminal disposition without prose-parsing
    # the transcript. The tape is already threaded to the journal writer, so this adds
    # NO signature ripple to run_turn's ~90 callers (near-zero blast radius, rule 0.5).
    runner.journal._turn_terminal = [
        {"id": ob.id, "status": ob.status, "lane": row["lane"]}
        for row, ob in zip(rows, obligations, strict=True)
    ]
    return "\n".join(out), runner.journal, still_open, new_facts


_SESSION_LOG = pathlib.Path(os.environ.get("VOOL_HOME", "/tmp/vool-kernel-repl-home")) / "repl_sessions.jsonl"
# Consensus-5 LAW 8: the run id survives PROCESS RESTARTS when the driver owns it
# (env), so a resumed run's review can be reconciled against the journal.
_RUN_ID = os.environ.get("VOOL_RUN_ID") or f"run-{os.getpid()}-{int(time.time())}"


def _colorize(transcript: str) -> str:
    """Terminal rendering: the ANSWER block bright, diagnostics as-is, user text yellow.

    Sentinels (\\x01/\\x02) mark the answer in the transcript; the journal strips them so
    records stay plain text, and only the terminal paints."""
    transcript = re.sub("\x03.*?\x04", "", transcript, flags=re.DOTALL)
    return transcript.replace("\x01", "\033[1;92m").replace("\x02", "\033[0m")


def _plain(transcript: str) -> str:
    return (transcript.replace("\x01", "").replace("\x02", "")
            .replace("\x03", "").replace("\x04", ""))


_BUILD_SHA: str | None = None


def _build_sha() -> str:
    """The commit this PROCESS is running, resolved once at first use.

    Code on disk is not code running (a live session served a stale build for 15
    unrecorded turns) — so the identity is captured by the process itself and stamped
    on the banner AND on every journal record, making stale-build turns attributable
    after the fact.
    """
    global _BUILD_SHA
    if _BUILD_SHA is None:
        import subprocess
        here = pathlib.Path(__file__).resolve().parent.parent.parent
        try:
            _BUILD_SHA = subprocess.run(
                ["git", "-C", str(here), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or "unknown"
        except Exception:
            _BUILD_SHA = "unknown"
    return _BUILD_SHA


def _extract_answer(transcript: str) -> str | None:
    """The EXACT answer bytes from the \\x01..\\x02 span of the RAW transcript.

    TIER 6: the journal used to store only _plain(transcript) with the answer
    sentinels stripped, so a review rebuilt from the journal had no answer boundary
    and printed "(nothing shipped)" for every turn while the real bytes sat on tape —
    the digest lie the sabotage gauntlet exposed. Storing the span verbatim (NO strip:
    byte-exact contracts and silence both matter) lets review-open reconcile the
    digest against the raw run. Returns None when the turn rendered no answer span,
    which is distinct from an empty-but-present answer ("")."""
    if "\x01" not in transcript:
        return None
    return transcript.split("\x01", 1)[1].split("\x02", 1)[0]


class JournalPersistenceError(RuntimeError):
    """Canonical evidence could not be persisted. Raw-evidence persistence is
    ALL-OR-STOP (review-184040 round-5 P0-HOLD): a run that cannot append its
    canonical row must STOP ACCEPTING PROMPTS — silently continuing splits the
    prompt state from the raw journal and rebuilds the exact count/hash
    divergence the crash-row harness exists to prevent."""


def _journal_turn(question: str, transcript: str, tape: EffectJournal | None,
                  mode: str, turn_index: int | None = None,
                  crashed: BaseException | None = None) -> None:
    """Append the turn to a session journal — the REPL's own receipts.

    ALL-OR-STOP (round-5 P0-HOLD, replacing the old best-effort swallow): a failed
    canonical append RAISES JournalPersistenceError so the caller stops accepting
    prompts — the old `except: pass` silently split prompt state from the raw
    journal, rebuilding the count/hash divergence through the error path.
    Serialization hazards INSIDE the row (a tape that will not JSON, an odd
    terminal_state) degrade loudly INTO the row — the row itself must land with
    its exact answer fields; only a real persistence failure stops the run.
    """
    raw_answer = _extract_answer(transcript)
    answer_bytes = raw_answer.encode("utf-8") if raw_answer is not None else b""
    try:
        tape_field = json.loads(tape.to_json()) if tape is not None else None
    except Exception as ser_exc:                       # degrade INSIDE the row, loudly
        tape_field = {"tape_error": f"{type(ser_exc).__name__}: {ser_exc}"}
    try:
        terminal_field = getattr(tape, "_turn_terminal", None) if tape is not None else None
        json.dumps(terminal_field)                     # prove it serializes
    except Exception as ser_exc:
        terminal_field = {"terminal_state_error": f"{type(ser_exc).__name__}: {ser_exc}"}
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": mode,
        "build": _build_sha(),
        "run_id": _RUN_ID,
        "turn_index": turn_index,
        "envelope_lines": question.count("\n") + 1,
        "model": _ARBITER_MODEL,
        "question": question,
        "transcript": _plain(transcript),
        # TIER 6: exact answer bytes + length + hash + structured terminal state.
        "answer_present": raw_answer is not None,
        "answer": raw_answer,
        "answer_len": len(answer_bytes),
        "answer_sha256": hashlib.sha256(answer_bytes).hexdigest() if raw_answer is not None else None,
        "terminal_state": terminal_field,
        # CRASH ROW (operator-authorized harness slice, 2026-08-20): a crashed
        # turn journals like any other — evidence must never self-delete (the
        # 181-prompt run graded as 180 because the crashed turn left no row).
        "stamps": getattr(tape, "_turn_stamps", None) if tape is not None else None,
        "crashed": crashed is not None,
        "crash_exception": f"{type(crashed).__name__}: {crashed}" if crashed is not None else None,
        "tape": tape_field,
    }
    try:
        _SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SESSION_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as write_exc:
        raise JournalPersistenceError(
            f"canonical journal append failed for turn_index={turn_index}: "
            f"{type(write_exc).__name__}: {write_exc} — the run must stop accepting "
            "prompts (all-or-stop evidence persistence)") from write_exc


def _journal_crash(question: str, exc: BaseException, partial_tape: EffectJournal | None,
                   session_turns: list[tuple]) -> str:
    """Record a crashed turn ATOMICALLY in both stores and return the visible line.

    The turn loop's old exception path printed and continued — no journal row, no
    session_turns entry — so a crashed turn's evidence self-deleted and the review
    graded a 181-prompt run as 180 without knowing. Law 4: every accepted input
    produces an ordered raw row, crashes included. The partial tape is whatever the
    EffectRunner recorded before the throw; the visible bytes are exactly what the
    user saw."""
    crash_line = f"turn failed, named: {type(exc).__name__}: {exc}"
    envelope = {"raw": question, "lines": question.count("\n") + 1,
                "delimiter": "bracketed-paste" if "\n" in question else "line"}
    # JOURNAL FIRST (round-5 P0-HOLD): the canonical append happens BEFORE the
    # session_turns append, so a persistence failure raises with BOTH stores
    # unchanged — they can never diverge through this path.
    _journal_turn(question, crash_line, partial_tape, mode="record",
                  turn_index=len(session_turns) + 1, crashed=exc)
    session_turns.append((question, crash_line, time.strftime("%H:%M:%S"), envelope))
    return crash_line


def main() -> None:
    provider, fetch = _search_lane()
    lane = f"live web lane: {provider} (your key, from Keychain)" if provider else \
        "live web lane: OFF — lookups will be declared, not guessed"
    print(__doc__.split("\n\nCommands:")[0])
    judge = (
        f"{_ARBITER_MODEL} via OpenRouter (your key — YOUR WORDS LEAVE THE MACHINE on this judge)"
        if "/" in _ARBITER_MODEL
        else f"{_ARBITER_MODEL} via local Ollama (fully local judgment)"
    )
    print(f"\n{lane}\narbiter/synthesis: {judge} (recorded on the tape)")
    # Self-identification, the provenance discipline the app's /healthz taught us: a
    # long-running process keeps the code it launched with, and on 2026-08-19 an operator
    # session ran a pre-journaling REPL for 15 unrecorded turns because nothing on screen
    # said which build was serving. Code on disk is not code running — so say which one this is.
    print(f"build: {_build_sha()} · journal: {_SESSION_LOG} (every turn is recorded there)")
    print("commands: :replay  :done (aliases: we done, done — opens the Kimi/Codex/Fable consensus review)  :quit\n")
    last: tuple[str, EffectJournal, str, list[dict], dict[str, str]] | None = None
    history: list[tuple[str, str]] = []   # (question, rendered answer), newest last
    session_turns: list[tuple] = []  # (question, RAW transcript, ts) — the review artifact
    ledger: list[dict] = []               # unresolved obligations carried across turns
    facts: dict[str, str] = {}            # session facts: durable receipts across turns
    pager = PagedSession()                # Law 5 — paged session, not truncated (NIA-015)

    def build_context(question: str) -> str:
        """What the judgment model gets to see beyond the current message.

        Statelessness was measured live: "answer my previous question" web-searched the
        phrase and returned parliamentary procedure. History gives deixis a referent;
        the ledger gives unanswered obligations legal continuity. Since 2026-08-30 the
        assembly IS the paged-memory law (NIA-015): spine first (never dropped), hot
        window, sha-stamped cold pages recalled by anchor intersection — with a
        CoverageProof instead of the measured truncation defect (a last-4-turns
        window with answers cut at 400 chars).
        """
        assembly = pager.assemble(
            question, ledger_rows=ledger, hot_history=history, budget_chars=6000,
        )
        return assembly.text

    # THE REPL OWNS THE READ (consensus-3 fix 2, measured twice: macOS Python links
    # libedit, which silently ignores the GNU-readline bracketed-paste binding, and
    # two pasted messages shredded into 18 fragment turns across two sessions).
    # Mechanism with no readline dependency: after the first line arrives, poll
    # stdin briefly — a paste's remaining lines are already buffered and arrive
    # within the window; human typing is not. Multi-line pastes become ONE turn.
    import select

    def _read_turn() -> str:
        first = input("\033[1;33myou> ")
        lines = [first]
        try:
            while select.select([sys.stdin], [], [], 0.15)[0]:
                more = sys.stdin.readline()
                if not more:
                    break
                lines.append(more.rstrip("\n"))
        except Exception:
            pass
        return "\n".join(lines).strip()

    while True:
        try:
            question = _read_turn()  # user text in bold yellow (operator request)
        except (EOFError, KeyboardInterrupt):
            print("\033[0m")
            return
        finally:
            print("\033[0m", end="", flush=True)
        if not question:
            continue
        if question == ":quit":
            return
        if question == ":done" or question.lower() in ("we done", "done"):
            # Session-control, same plane as :quit — NOT an answer. Measured cost of
            # routing this through the model instead: the turn shipped "we done
            # [stipulated]" while the operator meant "start the review". The review
            # itself lives in core/kernel/consensus.py and never touches the laws.
            from core.kernel.consensus import run_review
            run_review(session_turns, _build_sha(), run_id=_RUN_ID, journal_path=_SESSION_LOG)
            continue
        if question == ":replay":
            if last is None:
                print("nothing to replay yet")
                continue
            prev, tape, prev_context, prev_ledger, prev_facts = last
            rep = EffectRunner(mode="replay", journal=EffectJournal.from_json(tape.to_json()))
            stub_active = fetch is not None

            def never(*a: object, **k: object) -> None:
                raise AssertionError("replay reached the model or the network")

            transcript, _, _, _ = run_turn(
                prev, rep, never if stub_active else None, provider,
                context=prev_context, carryover=prev_ledger, session_facts=prev_facts,
            )
            try:
                _journal_turn(prev, transcript, None, mode="replay")
            except JournalPersistenceError as jexc:
                print(f"\nEVIDENCE PERSISTENCE FAILED — REPL stops (all-or-stop): {jexc}\n")
                return
            print(f"[replay of {prev!r} — zero model calls, zero network]\n{_colorize(transcript)}\n")
            continue
        if question.startswith(":recall"):
            # Law 5 recall (NIA-015): byte-exact cold page, sha-verified at read time —
            # bit-rot is a refusal, never silently corrupted bytes.
            parts = question.split(maxsplit=1)
            try:
                print(f"\n{pager.recall(parts[1].strip() if len(parts) > 1 else '')}\n")
            except RecallRefused as rref:
                print(f"\n{rref}\n")
            continue
        runner = EffectRunner(mode="record")
        try:
            context = build_context(question)
        except ContextUndercovered as under:
            # Fail-closed is the repair loop (Law 5): refuse the turn, name the
            # uncovered spine rows — never ship a context missing open obligations.
            print(f"\n{under}\n  → resolve/close open work or raise the budget before continuing.\n")
            continue
        pre_ledger = [dict(c) for c in ledger]
        pre_facts = dict(facts)
        try:
            transcript, tape, ledger, facts = run_turn(
                question, runner, fetch, provider, context=context, carryover=ledger,
                session_facts=facts,
            )
        except Exception as exc:
            # CRASH ROW (operator-authorized harness slice): journal the crash
            # atomically — runner.journal holds the partial tape recorded before
            # the throw. Printing-and-continuing alone deleted the evidence.
            try:
                print(_journal_crash(question, exc, runner.journal, session_turns) + "\n")
            except JournalPersistenceError as jexc:
                # ALL-OR-STOP (round-5 P0-HOLD): a run that cannot persist its
                # canonical row accepts NO further prompts. Both stores are
                # unchanged (journal-first ordering), so counts stay equal.
                print(f"\nEVIDENCE PERSISTENCE FAILED — REPL stops (all-or-stop): {jexc}\n")
                return
            continue
        last = (question, tape, context, pre_ledger, pre_facts)
        answer_part = transcript.split("\n\n", 1)[-1]
        history.append((question, answer_part))
        # Law 5 admission (NIA-015): the FULL-verbatim turn (no [:400]) enters the
        # cold store, sha-stamped and anchored — recallable long after the hot
        # window has forgotten it. All-or-nothing: a failed admit changes nothing.
        pager.admit_turn(question, answer_part, turn_index=len(history))
        # RAW transcript (markers intact): the review bus parses answer/evidence spans
        # out of the markers to build FULL_REVIEW_CONTEXT.md for all three reviewers.
        # INPUT ENVELOPE (consensus-2 amendment 4): the raw physical input and its
        # boundary kind ride the record, so Law 4 can prove whether one user message
        # arrived whole or shredded (measured: two multiline pastes became 12 turns).
        envelope = {"raw": question, "lines": question.count("\n") + 1,
                    "delimiter": "bracketed-paste" if "\n" in question else "line"}
        session_turns.append((question, transcript, time.strftime("%H:%M:%S"), envelope))
        # BLOCK-B stamps ride the record out-of-band (never answer bytes).
        with contextlib.suppress(Exception):
            tape._turn_stamps = _compile_stamps(question)
        # turn_index is 1-based and equals this turn's position in session_turns (just
        # appended) — the identity review-open reconciles each answer against.
        try:
            _journal_turn(question, transcript, tape, mode="record", turn_index=len(session_turns))
        except JournalPersistenceError as jexc:
            # ALL-OR-STOP: roll the transcript back to the last persisted turn so
            # both stores agree, then stop accepting prompts.
            session_turns.pop()
            print(f"\nEVIDENCE PERSISTENCE FAILED — REPL stops (all-or-stop): {jexc}\n")
            return
        print(_colorize(transcript) + "\n")


if __name__ == "__main__":
    sys.exit(main())
