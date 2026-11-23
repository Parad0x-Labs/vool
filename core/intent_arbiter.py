"""Constrained intent arbiter: a real model tie-break for ambiguous routing — never free-form.

Keyword routing fails in two known ways: two families both claim one message (the "check Token
hunter folder on this machine" specs-hijack) and no family claims a message that clearly wants
a tool ("fint the oken hunter folder" — typo'd verb). Priority order guesses; this asks the
local model instead. Crucially it is a MULTIPLE-CHOICE question, not free-form tool calling:
small local models are unreliable at emitting arbitrary tool JSON (the original reason the
fast-paths exist) but are solid at picking one option from a short menu and copying out one
argument. The arbiter picks the family; the deterministic tools still produce the answer —
understanding chooses the route, scripts never invent the data.

Fail-open by design: flag off, model down, timeout, or unparseable output all return None and
dispatch proceeds exactly as it does today. The arbiter can therefore only ever improve a turn
that was already ambiguous — it cannot break an unambiguous one (those never reach it).
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from core.agent_runtime.intent_claims import (
    FAMILY_DISK_USAGE,
    FAMILY_FIND_FILE,
    FAMILY_FIND_FOLDER,
    FAMILY_FOLDER_OVERVIEW,
    FAMILY_HOST_STATE,
    FAMILY_IMAGE_GENERATION,
    FAMILY_LIST_DIRECTORY,
    FAMILY_LIST_PROCESSES,
    FAMILY_MACHINE_SPECS,
    FAMILY_READ_FILE,
    IntentClaim,
)
from core.disclosure.direct import (
    local_metadata_options,
    received_headers,
    refuse_redirected_metadata,
    request_options,
)
from core.prompt_debug import dump_outbound_prompt
from core.provider_invocation_gateway import seal_direct_provider_invocation
from core.runtime_provider_defaults import ollama_direct_runner_options

_FLAG_ENV = "VOOL_INTENT_ARBITER"
_MODEL_ENV = "VOOL_ARBITER_MODEL"


def _ollama_chat_url() -> str:
    """The chat endpoint through the runtime's ONE Ollama resolution; an explicit
    VOOL_OLLAMA_CHAT_URL still wins, as the launcher contract promises."""
    from core.ollama_endpoint import ollama_api_url

    return os.environ.get("VOOL_OLLAMA_CHAT_URL") or ollama_api_url("/api/chat")


# Read window must survive a COLD LOAD of the tiny model: measured ~8.6s to load qwen3:0.6b on a
# RAM-loaded 24GB box (300ms once resident; ~4s when queued behind a generation). 15s covers the
# worst case; keep_alive + the boot prewarm mean only the first arbitration after a long idle ever
# pays it, and the model lane the turn would otherwise take queues on the same Ollama anyway.
_TIMEOUT_S = float(os.environ.get("VOOL_ARBITER_TIMEOUT_S", "15.0") or 15.0)
_ARG_MAX = 80

CHOICE_CHAT = "chat"

# The menu the model picks from. Descriptions are written for a small model: short, concrete,
# one clear discriminator each. Only read-only intents are ever offered.
_MENU: dict[str, str] = {
    FAMILY_FIND_FILE: "the user wants a FILE (picture, document, pdf, video) found by its name on this computer (argument = the file name)",
    FAMILY_FIND_FOLDER: "the user wants a FOLDER located by name on this computer (argument = the folder name)",
    # "like Desktop or Downloads (argument = the place)" taught the tiny model to answer "Desktop" for
    # "/Users/me/Desktop/route-planner", so a request about one subfolder listed the whole Desktop.
    # The argument is whatever the user wrote, copied exactly -- the caller prefers a path it can see
    # in the raw text anyway, so this only has to stop the model from helpfully shortening one.
    FAMILY_LIST_DIRECTORY: "the user wants the CONTENTS of a folder listed (argument = the folder path or name EXACTLY as the user wrote it, full path if they gave one)",
    FAMILY_READ_FILE: "the user wants ONE FILE read/shown (argument = the file name or path)",
    FAMILY_DISK_USAGE: "the user asks about DISK SPACE / storage left",
    FAMILY_MACHINE_SPECS: "the user asks what HARDWARE this computer has (RAM, chip, GPU, screen)",
    # The two live-state options exist because MACHINE_SPECS used to absorb them: a static spec
    # sheet was offered as the answer to "what is eating my CPU" and "what is the uptime". The
    # discriminator is now/right now vs. what the machine is made of.
    FAMILY_LIST_PROCESSES: "the user asks what is RUNNING or what is using the CPU/memory RIGHT NOW",
    FAMILY_HOST_STATE: "the user asks about UPTIME, BATTERY level, or whether this is a laptop or a desktop",
    FAMILY_FOLDER_OVERVIEW: "the user asks what the CURRENT PROJECT/workspace is about",
    FAMILY_IMAGE_GENERATION: "the user wants an IMAGE generated (argument = the image description)",
    CHOICE_CHAT: "none of these — it is normal conversation for the assistant to answer",
}

#: The lane-registry coverage capabilities whose subjects this menu reads. Every option is a folder,
#: file, disk, process, host or workspace reading, served elsewhere by the workspace lanes and the
#: machine-fact lane. The front door does not arbitrate a turn that a registered lane of any other
#: domain already covers whole: no option here can serve it, and a pick would run a machine read in
#: its place (`turn_frontdoor._registered_owner_outside_the_menu`).
MENU_COVERAGE: tuple[str, ...] = ("workspace_read", "machine_fact")


@dataclass(frozen=True)
class ArbiterDecision:
    family: str
    argument: str = ""


def arbiter_enabled(env: dict[str, str] | None = None) -> bool:
    """Default ON (proven on the live gauntlet: 3/3 correct picks on gate-eligible cases,
    fail-open on every failure, and unambiguous messages never reach it). VOOL_INTENT_ARBITER=0
    turns it off; any explicit truthy/falsy value wins over the default."""
    value = str((env or os.environ).get(_FLAG_ENV) or "").strip().lower()
    if not value:
        return True
    return value in {"1", "true", "yes", "on"}


_MODEL_CACHE: dict[str, str] = {}


def _resolve_model() -> str:
    # OPERATOR KILL-SWITCH (2026-08-29): `config/local_models_disabled` in the data dir means this
    # machine runs NO local model work at all. The arbiter loads on demand, so the boot-prewarm
    # gate alone left it free to cold-load a multi-GB model mid-turn on a memory-tight host.
    # Returning "" is the arbiter's own documented "no local model available" path: callers fall
    # back to their deterministic routing rules instead of a model call. The canonical
    # LocalModelPolicy owns the decision now — the marker file and VOOL_LOCAL_MODELS_ENABLED are
    # one switch, and it binds even an explicit _MODEL_ENV pin: disabled means no local execution,
    # not "no local execution unless asked".
    if _local_models_disabled():
        return ""
    explicit = str(os.environ.get(_MODEL_ENV) or "").strip()
    if explicit:
        return explicit
    cached = _MODEL_CACHE.get("model")
    if cached is not None:
        return cached
    # ALWAYS the smallest capable tag, never "whatever is loaded": the live drive proved that a
    # resident 14b (loaded for the main lane) blows the arbiter's timeout, while a cold 0.6b loads
    # in ~1s and answers a multiple-choice prompt in ~300ms. Small + deterministic beats warm + big.
    try:
        import requests

        base = _ollama_chat_url().rsplit("/api/", 1)[0]
        _disclosure_metadata_url = f"{base}/api/tags"
        tags = refuse_redirected_metadata(
            requests.get(_disclosure_metadata_url, **local_metadata_options(_disclosure_metadata_url), timeout=1.5)
        ).json().get("models") or []
        names = [str(t.get("name") or "").strip() for t in tags if t.get("name")]
        pick = ""
        for preferred in ("qwen3:0.6b", "qwen3:1.7b", "qwen3:4b", "qwen3:8b"):
            if preferred in names:
                pick = preferred
                break
        if not pick and names:
            pick = names[0]
        _MODEL_CACHE["model"] = pick
        return pick
    except Exception:
        return ""


def _local_models_disabled() -> bool:
    """True when the canonical LocalModelPolicy has disabled every local-model load."""
    try:
        from core.local_model_policy import local_models_enabled

        return not local_models_enabled()
    except Exception:
        return False


def _arbiter_runner_options(model: str, role: str = "") -> dict[str, int]:
    """The sized context (and runner keys) this lane loads at, from the runtime's own authority.

    Both calls in this module post straight to Ollama rather than through the provider adapter --
    which is deliberate and documented below, but it also means the adapter's `num_ctx` stamping
    (`openai_compatible_adapter.py:906,978`) never reaches them. Ollama then loads the model at its
    NATIVE context, and the boot prewarm pins that instance for thirty minutes.

    Measured live 2026-09-09 on this 24 GiB host, `GET :11434/api/ps` while serving: `qwen3:0.6b`
    resident at **ctx=40960 / 4.99 GiB** -- as much as `qwen2.5:7b` at 4.97 GiB, for a
    classification prompt whose own output budget is 96 tokens. With `qwen3:8b` (6.10 GiB) and
    `qwen2.5:7b` also resident that is 16.06 GiB of the machine, which is the cumulative-residency
    exhaustion FINDINGS F12 measured from the other side.

    `classifier` is already `_ROLE_LIGHT` in `core.context_capsule_v2.role_fraction`, and this
    accessor already honours the documented `VOOL_OLLAMA_CONTEXT_WINDOW` override, the persisted
    hardware bucket and the model ceiling. No new sizing policy is introduced here: the lane is
    simply told to use the one that already exists.

    The thread count and GPU-layer setting come with the window, the same keys the provider
    adapter sends: Ollama reloads a resident model whose runner options change, so a lane that
    sized the window alone still reloaded the helper model on every alternation with the adapter.
    """

    # `role` is given when the model asked is not this lane's helper but one registered under another role (the
    # chat model, "general"): the call then names the runner that model is already loaded on.
    return ollama_direct_runner_options(str(model or ""), bundle_role=role or "classifier")


def _build_prompt(text: str, options: list[str]) -> str:
    lines = [
        "Route this user message. Pick EXACTLY ONE option key.",
        f'User message: "{" ".join(str(text or "").split())[:400]}"',
        "Options:",
    ]
    for key in options:
        lines.append(f'- "{key}": {_MENU[key]}')
    lines.append('Reply with ONLY this JSON, nothing else: {"choice": "<option key>", "argument": "<short argument or empty>"}')
    return "\n".join(lines)


def _candidate_options(claims: list[IntentClaim]) -> list[str]:
    families = [claim.family for claim in claims if claim.family in _MENU]
    if not families:  # near-miss: nothing claimed -> offer the full read-only menu
        families = [k for k in _MENU if k != CHOICE_CHAT]
    # de-dup preserving order, chat always offered so the model can decline
    seen: set[str] = set()
    ordered = [f for f in families if not (f in seen or seen.add(f))]
    ordered.append(CHOICE_CHAT)
    return ordered


def _claim_argument(claims: list[IntentClaim], family: str) -> str:
    for claim in claims:
        if claim.family == family and claim.argument:
            return claim.argument
    return ""


_LAST_FAILURE: dict[str, str] = {}
# Circuit breaker: on a SATURATED Ollama (a big model resident + heavy background load) even the
# tiny arbiter model can't answer inside the window. One timeout opens the breaker for a cool-off,
# so a struggling box pays the wait at most once per window — every other ambiguous turn fails
# open INSTANTLY to today's dispatch instead of stacking 15s delays.
_BREAKER: dict[str, float] = {}
_BREAKER_COOLOFF_S = float(os.environ.get("VOOL_ARBITER_COOLOFF_S", "300") or 300)
# The breaker is kept per model. One breaker for every model let a timeout of the helper tag, or of a background
# judgement queued behind a model load, silence the judge of the turn's own chat model for five minutes: on the
# installed 3dfb2f34 the tool-ask judge answered "None in 2 ms" for most of the gate after one slow call, and a
# reminder with a slip and a contact save were offered no tool (real-app gate, 2026-11-22).


def last_failure() -> str:
    """Why the most recent arbitrate() failed open ('' when it succeeded) — for the decision log."""
    return _LAST_FAILURE.get("reason", "")


def _breaker_open(model: str | None = None) -> bool:
    """Whether this model's breaker is open; with no model, whether any is."""
    import time

    now = time.monotonic()
    if model is None:
        return any(now < until for until in _BREAKER.values())
    return now < _BREAKER.get(str(model), 0.0)


def _trip_breaker(model: str = "") -> None:
    import time

    _BREAKER[str(model or "")] = time.monotonic() + _BREAKER_COOLOFF_S


def reset_breaker(model: str | None = None) -> None:
    """Test hook + successful-call reset: this model's breaker, or with no model every one."""
    if model is None:
        _BREAKER.clear()
    else:
        _BREAKER.pop(str(model), None)


def arbitrate(
    text: str,
    claims: list[IntentClaim],
    *,
    request_id: str = "",
    chat_id: str = "",
    project_id: str = "",
    context_manifest: dict[str, object] | None = None,
    source_context: dict[str, object] | None = None,
) -> ArbiterDecision | None:
    """Ask the local model to pick the family. None = fail-open (keep today's dispatch)."""
    _LAST_FAILURE["reason"] = ""
    if not arbiter_enabled():
        _LAST_FAILURE["reason"] = "disabled"
        return None
    model = _resolve_model()
    if not model:
        _LAST_FAILURE["reason"] = "no_model"
        return None
    if _breaker_open(model):
        _LAST_FAILURE["reason"] = "breaker_open"
        return None
    options = _candidate_options(claims)
    call_id = ""
    try:
        import requests

        arbiter_payload = {
            "model": model,
            "messages": [{"role": "user", "content": _build_prompt(text, options)}],
            "stream": False,
            "format": "json",
            "think": False,
            "keep_alive": "30m",  # keep the tiny arbiter model resident: only the FIRST arbitration pays a load
            # Sized, because keep_alive makes this residency LAST: an unsized load pins the
            # model at its native context for the full thirty minutes (see _arbiter_runner_options).
            "options": {
                "temperature": 0,
                "num_predict": 96,
                **_arbiter_runner_options(model),
            },
        }
        # This call posts straight to Ollama instead of going through the provider adapter, so it was
        # invisible to prompt capture -- the component that CHOOSES the route was the one thing the
        # debug lane could not show. Capture is opt-in and off by default, same as everywhere else.
        dump_outbound_prompt(
            arbiter_payload,
            lane="intent_arbiter",
            model=model,
            extra={"output_mode": "arbiter_choice", "options": list(options)},
        )
        manifest_context = dict(context_manifest or {})
        manifest_context.setdefault("chat_id", str(chat_id or ""))
        manifest_context.setdefault("project_id", str(project_id or ""))
        manifest_context.setdefault("items_included", [])
        manifest_context.setdefault("items_excluded", [])
        manifest_context.setdefault("capsule_version", "none")
        _disclosure_endpoint = _ollama_chat_url()
        permit = seal_direct_provider_invocation(
            destination_url=_disclosure_endpoint,
            provider_id="ollama",
            model_id=model,
            operation="intent_arbitration",
            payload=arbiter_payload,
            request_id=(
                str(request_id or "").strip()
                or f"intent-arbiter-{uuid.uuid4().hex}"
            ),
            context_manifest=manifest_context,
            max_output_tokens=96,
        )
        # This lane posts straight to the provider rather than through the adapter, so the router's
        # seam never sees it and the turn used to report a route decision that cost a model call as
        # having cost none. Recorded immediately before the post, under the same rule as the adapter
        # seam: entering the call is what counts, and a timeout below still counts.
        from core.turn_model_call_ledger import record_provider_call

        # Identity is known exactly here and is not a guess: this lane posts to `_ollama_chat_url()`, so
        # the provider is `ollama` and the lane is free-local whatever the router is doing elsewhere.
        call_id = record_provider_call(
            source_context,
            provider_id="ollama",
            model_id=str(model or ""),
            cost_class="free_local",
        )
        _disclosure_body = permit.consume()
        response = requests.post(
            _disclosure_endpoint,
            **request_options(_disclosure_body, _disclosure_endpoint),
            timeout=(1.5, _TIMEOUT_S),
        )
        received_headers(_disclosure_body)
        response.raise_for_status()
        content = str(((response.json().get("message") or {}).get("content")) or "")
    except Exception as exc:
        if call_id:
            from core.normalized_provider_result import classify_error_class
            from core.turn_model_call_ledger import record_provider_call_outcome

            error_class = classify_error_class(exc)
            record_provider_call_outcome(
                source_context,
                call_id,
                outcome="failed",
                error_class=error_class.value if error_class else "",
            )
        _LAST_FAILURE["reason"] = f"request:{type(exc).__name__}"
        if "Timeout" in type(exc).__name__:
            _trip_breaker(model)  # saturated box: don't stack waits — cool off, fail open instantly
        return None
    reset_breaker(model)  # a served request proves the model is answering again
    decision = _parse_decision(content, options=options, claims=claims)
    if decision is None:
        _LAST_FAILURE["reason"] = "parse"
    if call_id:
        from core.normalized_provider_result import ProviderErrorClass
        from core.turn_model_call_ledger import record_provider_call_outcome

        record_provider_call_outcome(
            source_context,
            call_id,
            outcome="completed" if decision is not None else "failed",
            error_class=(
                ""
                if decision is not None
                else ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE.value
            ),
        )
    return decision


def _parse_decision(content: str, *, options: list[str], claims: list[IntentClaim]) -> ArbiterDecision | None:
    """Strict parse of the model's JSON; anything off-menu or malformed fails open (None)."""
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", str(content or ""), re.DOTALL)
        if not match:
            return None
        try:
            raw = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    choice = str(raw.get("choice") or "").strip().lower()
    if choice not in options:
        return None
    if choice == CHOICE_CHAT:
        return ArbiterDecision(CHOICE_CHAT)
    argument = " ".join(str(raw.get("argument") or "").split()).strip(" .'\"")[:_ARG_MAX]
    if any(ord(ch) < 32 for ch in argument):
        argument = ""
    # Prefer the claim's own regex extraction (precise) over the model's argument (which small
    # models sometimes over-copy — live drive showed one echoing the whole message). The model's
    # argument is used only when no claim carried one, i.e. the near-miss case where the model's
    # extraction is all we have (and where it measured well: 'fint the oken hunter folder' ->
    # 'oken hunter').
    return ArbiterDecision(choice, _claim_argument(claims, choice) or argument)



# --- follow-up arbitration: retry / new / dismiss ------------------------------------------------------------------
# Runs ONLY when the chat's last actionable request is unfinished (refused/failed) and the next message is short and
# brings no task of its own (core.conversation_state.resolve). It exists because "is this a retry?" cannot be decided by
# listing retry wordings. Same lane as `arbitrate`: the smallest local model, sealed provider permit, the call ledger,
# the circuit breaker. Anything but a clean one-word decision is None, and None is never a retry (fail closed).
def _followup_prompt(request: str, reason: str, message: str) -> str:
    """A balanced yes/no: is the user asking to proceed with the earlier request, or saying the obstacle is gone?"""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    return "\n".join([
        "An assistant could not finish something a user asked. The user has now written a new message.",
        "Question: is the new message asking to proceed with the earlier request, or saying the obstacle is gone?",
        'Answer {"answer": "yes"} or {"answer": "no"}. A reaction, thanks, small talk, an unrelated question, or a '
        "different request is no.",
        "",
        'Earlier request: "show the spreadsheets in my documents folder" (not done: blocked)',
        'New message: "the block is lifted, run it" -> {"answer": "yes"}',
        'New message: "nice one" -> {"answer": "no"}',
        'New message: "ok it is switched on now, your turn" -> {"answer": "yes"}',
        'New message: "what is the capital of Peru" -> {"answer": "no"}',
        'New message: "alright, ready when you are" -> {"answer": "yes"}',
        'New message: "ugh, annoying" -> {"answer": "no"}',
        'New message: "go for it" -> {"answer": "yes"}',
        'New message: "translate this to French" -> {"answer": "no"}',
        "",
        f'Earlier request: "{flat(request, 300)}" (not done: {flat(reason or "it failed", 80)})',
        f'New message: "{flat(message, 200)}" ->',
        'Reply with ONLY JSON like {"answer": "no"}',
    ])


def arbitrate_followup(
    request: str,
    reason: str,
    message: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
    prompt: str = "",
    operation: str = "followup_arbitration",
    model: str = "",
    runner_role: str = "",
    call_role: str = "",
    parse: Callable[[str], str | None] | None = None,
    patient: bool = False,
    timeout_s: float | None = None,
) -> str | None:
    """``"yes"`` / ``"no"``, or None (disabled, no model, breaker open, error, unparseable).

    `patient` is for a judgement the turn cannot do without (the tool ask: without it a tool asked for in other
    words or another language is not offered at all). It is asked even while the model's breaker is open; the owner
    accepted a slower tool step over a missing tool (2026-11-22).

    `parse` reads the model's answer when the judgement is not a yes/no (`arbitrate_named_fact`); None is the
    strict yes/no reader.

    `prompt` and `operation` let another balanced yes/no judgement (`arbitrate_keep_ask`) go out through this one
    sealed site instead of opening a second one; without them this asks the follow-up question. `model` names the
    local model to ask instead of the helper tag, and `runner_role` the role that model is registered under, so the
    call names the runner already loaded and Ollama does not reload it."""
    _LAST_FAILURE["reason"] = ""
    if not arbiter_enabled():
        _LAST_FAILURE["reason"] = "disabled"
        return None
    model = str(model or "").strip() or _resolve_model()
    if not model:
        _LAST_FAILURE["reason"] = "no_model"
        return None
    if _breaker_open(model) and not patient:
        _LAST_FAILURE["reason"] = "breaker_open"
        return None
    call_id = ""
    try:
        import requests

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt or _followup_prompt(request, reason, message)}],
            "stream": False,
            "format": "json",
            "think": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_predict": 24, **_arbiter_runner_options(model, runner_role)},
        }
        permit = seal_direct_provider_invocation(
            destination_url=_ollama_chat_url(),
            provider_id="ollama",
            model_id=model,
            operation=operation,
            payload=payload,
            request_id=str(request_id or "").strip() or f"{operation}-{uuid.uuid4().hex}",
            context_manifest={"chat_id": str(chat_id or ""), "project_id": "", "items_included": [],
                              "items_excluded": [], "capsule_version": "none"},
            max_output_tokens=24,
        )
        from core.turn_model_call_ledger import record_provider_call

        call_id = record_provider_call(source_context, provider_id="ollama", model_id=str(model or ""), cost_class="free_local",
                                       call_role=call_role)
        response = requests.post(_ollama_chat_url(), json=permit.consume(), timeout=(1.5, timeout_s or _TIMEOUT_S))
        response.raise_for_status()
        content = str(((response.json().get("message") or {}).get("content")) or "")
    except Exception as exc:
        if call_id:
            from core.normalized_provider_result import classify_error_class
            from core.turn_model_call_ledger import record_provider_call_outcome

            error_class = classify_error_class(exc)
            record_provider_call_outcome(source_context, call_id, outcome="failed",
                                         error_class=error_class.value if error_class else "")
        _LAST_FAILURE["reason"] = f"request:{type(exc).__name__}"
        if "Timeout" in type(exc).__name__:
            _trip_breaker(model)
        return None
    reset_breaker(model)
    decision = (parse or parse_followup_decision)(content)
    if call_id:
        from core.normalized_provider_result import ProviderErrorClass
        from core.turn_model_call_ledger import record_provider_call_outcome

        record_provider_call_outcome(
            source_context, call_id, outcome="completed" if decision else "failed",
            error_class="" if decision else ProviderErrorClass.MALFORMED_PROVIDER_RESPONSE.value,
        )
    return decision


# ---- the judgements on a turn no local model answers --------------------------------------------------------
# Every judgement below asks the LOADED LOCAL model (`_keep_judge_model`). On a turn the owner's own cloud model
# answers, with local models off, there is none: the keep ask in other words, the fact said in passing, the
# restore, the question in other words and the same-thing gate each got "nobody judged" and fell back to what they
# did before they existed (measured on the real app on the cloud route, 2026-11-18). The runtime binds, for such a
# turn, a judge that sends the same one-question prompt to the model that answers the turn
# (`core.agent_runtime.turn_judge`): the owner's own choice, the owner's own key, the same reservation and caps
# as any other call to it. Nothing here knows which model that is.
_TURN_JUDGES: OrderedDict[str, Callable[[str], str]] = OrderedDict()
_TURN_JUDGES_KEPT = 64
_THIS_THREAD = threading.local()


def _turn_key(source_context: object) -> str:
    try:
        from core.memory.keep_receipt import _turn_id

        return _turn_id(source_context)
    except Exception:
        return ""


def bind_turn_judge(source_context: object, judge: Callable[[str], str] | None) -> None:
    """Set, or with None clear, the judge of this turn: a ``prompt -> text`` callable. Called once at the turn's
    start, for every turn, so a judge never outlives its turn on a thread."""
    _THIS_THREAD.judge = judge
    turn = _turn_key(source_context)
    if not turn:
        return
    if judge is None:
        _TURN_JUDGES.pop(turn, None)
        return
    _TURN_JUDGES[turn] = judge
    while len(_TURN_JUDGES) > _TURN_JUDGES_KEPT:
        _TURN_JUDGES.popitem(last=False)


def _judged_for_the_turn(
    prompt: str, source_context: object, parse: Callable[[str], str | None] | None = None
) -> str | None:
    """``"yes"`` / ``"no"`` from the judge bound for this turn, or None: no judge bound, a failed call, an
    unreadable answer. None is what "no local model" always was: the caller decides nothing on it."""
    judge = _TURN_JUDGES.get(_turn_key(source_context)) or getattr(_THIS_THREAD, "judge", None)
    if judge is None:
        _LAST_FAILURE["reason"] = "no_keep_judge_model"
        return None
    try:
        content = str(judge(prompt) or "")
    except Exception as exc:
        _LAST_FAILURE["reason"] = f"turn_judge:{type(exc).__name__}"
        return None
    decision = (parse or parse_followup_decision)(content)
    if decision is None:
        _LAST_FAILURE["reason"] = "turn_judge:unreadable"
    return decision


#: A judgement put to the turn's own model before it is loaded waits for the load (the turn needs the model loaded
#: anyway); a cold qwen3:8b load on the gate iMac took 10-20 s, more than the arbiter's usual window.
_LOAD_TIMEOUT_S = float(os.environ.get("VOOL_JUDGE_LOAD_TIMEOUT_S", "60") or 60)


def ask_local_model(
    prompt: str, model: str, *, runner_role: str = "general", source_context: dict[str, object] | None = None,
) -> str | None:
    """The raw answer of the named local Ollama model to one judgement prompt, through the arbiter's one sealed
    direct site, on the runner that model serves the chat on; None when it did not answer. The judge of a turn
    whose model is not loaded yet (`core.agent_runtime.turn_judge`)."""
    return arbitrate_followup(
        "", "", "", source_context=source_context, prompt=str(prompt or ""), operation="turn_judgement",
        model=str(model or ""), runner_role=runner_role, call_role=_judgement_role(), parse=lambda content: content,
        patient=True, timeout_s=_LOAD_TIMEOUT_S,
    )


def _keep_ask_prompt(lead: str, body: str) -> str:
    """A balanced yes/no: does the lead ask for the text behind it to be kept, or for something else?"""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    return "\n".join([
        "A person wrote to their assistant in two parts: a lead, then a text.",
        "Question: does the lead ask the assistant to keep the text for later (remember it, store it, put it on a "
        "list, a log or a record of theirs), or announce things that are to be kept?",
        'Answer {"answer": "yes"} or {"answer": "no"}. A lead that asks for anything else to be done with the text '
        "(translate, rewrite, check, explain, answer, reply, summarise, continue, search) is no. A lead that only "
        "introduces an opinion, a report, a question or a quote is no.",
        "",
        'Lead: "hang on to this for me" Text: "the meter key is on the hook by the door" -> {"answer": "yes"}',
        'Lead: "make this sound more formal" Text: "my flat is on the third floor" -> {"answer": "no"}',
        'Lead: "one for the packing list" Text: "bring the travel adapter" -> {"answer": "yes"}',
        'Lead: "here is what I think" Text: "the second option is cheaper" -> {"answer": "no"}',
        'Lead: "a couple of things for the recipe binder" Text: "the scones need 12 minutes" -> {"answer": "yes"}',
        'Lead: "put this into German" Text: "our dog sleeps in the kitchen" -> {"answer": "no"}',
        'Lead: "note to self" Text: "the car is due its service in April" -> {"answer": "yes"}',
        'Lead: "does this read well" Text: "my sister is moving to Leeds next year" -> {"answer": "no"}',
        'Lead: "quick question" Text: "the oven is making a clicking noise" -> {"answer": "no"}',
        'Lead: "don\'t let me lose this" Text: "the warranty runs until 2029" -> {"answer": "yes"}',
        "",
        f'Lead: "{flat(lead, 120)}" Text: "{flat(body, 300)}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_keep_ask(
    lead: str,
    body: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether "<lead>: <body>" asks for the body to be kept: ``"yes"`` / ``"no"``, or None when nothing judged it.

    A remember ask is said in any words ("hang on to this for me: ...", "one for the packing list: ..."), and the
    same shape carries every other task ("put this into German: ..."). Its meaning is not in a list of verbs, so the
    smallest local model is asked one balanced question -- the lane, the sealed site, the ledger and the breaker of
    `arbitrate_followup`. None is never a keep: the caller writes nothing on it."""
    if not str(lead or "").strip() or not str(body or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_keep_ask_prompt(lead, body), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_keep_ask_prompt(lead, body), operation="keep_ask_arbitration", model=model, runner_role="general",
        call_role=_judgement_role(),
    )


def _forget_ask_prompt(lead: str, order: str) -> str:
    """A balanced yes/no: do the words in front make this an order to the assistant to forget something it holds?"""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    return "\n".join([
        "A person wrote one sentence to their assistant. It is shown in two parts: the words in front, then the rest.",
        "Question: is the person asking the ASSISTANT to forget or erase something the assistant knows, was told or "
        "saved about them (to remove it from the assistant's memory)?",
        'Answer {"answer": "yes"} or {"answer": "no"}. A sentence about forgetting in general, about the person\'s '
        "own forgetting, about dropping a plan or an idea, about a device or a file, or one that asks how to forget "
        "something, is no.",
        "",
        'Front: "you can" Rest: "forget my gym locker number" -> {"answer": "yes"}',
        'Front: "you can" Rest: "forget about going to the beach, it is raining" -> {"answer": "no"}',
        'Front: "you may as well" Rest: "forget the wifi code I gave you" -> {"answer": "yes"}',
        'Front: "tell me how to" Rest: "forget a bad day at work" -> {"answer": "no"}',
        'Front: "from now on just" Rest: "forget my old address" -> {"answer": "yes"}',
        'Front: "how do I make my phone" Rest: "forget a bluetooth speaker" -> {"answer": "no"}',
        'Front: "it would be great if you could" Rest: "forget what I said about my boss" -> {"answer": "yes"}',
        'Front: "people say you should forgive and" Rest: "forget old grudges" -> {"answer": "no"}',
        'Front: "go ahead and" Rest: "erase my sister\'s phone number from what you know" -> {"answer": "yes"}',
        'Front: "what would it take to" Rest: "erase a hard drive safely" -> {"answer": "no"}',
        "",
        f'Front: "{flat(lead, 120)}" Rest: "{flat(order, 300)}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_forget_ask(
    lead: str,
    order: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether "<lead> <order>" orders the assistant to forget what the order names: ``"yes"`` / ``"no"``, or None
    when nothing judged it.

    A forget is said with any words in front of it ("you can forget my work parking bay.", "you may as well forget
    the wifi code"), and the same shape says many other things ("you can forget about the beach, it is raining").
    Its meaning is not in a list of lead-ins, so one balanced question is asked the way `arbitrate_keep_ask` asks
    its own. None is never a forget: the caller erases nothing on it."""
    if not str(lead or "").strip() or not str(order or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_forget_ask_prompt(lead, order), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_forget_ask_prompt(lead, order), operation="forget_ask_arbitration", model=model, runner_role="general",
        call_role=_judgement_role(),
    )


def _restore_prompt(message: str, current: str, earlier: str) -> str:
    """A balanced yes/no: does the message say the person is back to the earlier value of a saved fact?"""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    return "\n".join([
        "An assistant keeps facts a person told it. One fact was changed earlier; the old value is kept as history.",
        "Question: does the person's new message say that the change no longer holds, so the earlier value is "
        "the right one again?",
        'Answer {"answer": "yes"} or {"answer": "no"}. A new value, a remark about something else, a question '
        "alone, or thanks is no.",
        "",
        'Current: "my parking space is number 12 now" Earlier: "my parking space is number 5"',
        'Message: "they reversed the decision, the space I had before is mine again" -> {"answer": "yes"}',
        'Message: "the car needs a wash" -> {"answer": "no"}',
        'Message: "forget the switch, I am keeping what I had at the start" -> {"answer": "yes"}',
        'Message: "is the car park open on sundays" -> {"answer": "no"}',
        'Message: "the reshuffle was called off so nothing changed after all" -> {"answer": "yes"}',
        'Message: "I like the new space" -> {"answer": "no"}',
        "",
        f'Current: "{flat(current, 200)}" Earlier: "{flat(earlier, 200)}"',
        f'Message: "{flat(message, 300)}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_restore(
    message: str,
    current: str,
    earlier: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether the message says a saved fact is back to its earlier value: ``"yes"`` / ``"no"``, or None when
    nothing judged it. Asked only about a fact that has a stored history and a message that gives no new value;
    which value comes back is read from that history, never from this answer. The lane, the sealed site, the
    ledger role and the model of `arbitrate_keep_ask`. None restores nothing."""
    if not str(message or "").strip() or not str(current or "").strip() or not str(earlier or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_restore_prompt(message, current, earlier), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_restore_prompt(message, current, earlier), operation="restore_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


def _answered_prompt(question: str, reply: str) -> str:
    """A balanced yes/no: does a reply written after a file search give what was asked, or only report a miss?"""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    return "\n".join([
        "An assistant searched a person's own files to answer their question, then wrote a reply.",
        "Question: does the reply give what the person asked for?",
        'Answer {"answer": "yes"} when the reply states the thing asked for: a value, a name, a date, a place, or '
        "what a document says about it.",
        'Answer {"answer": "no"} when the reply only reports that it was not found, is not there, could not be '
        "checked, or asks where to look, and does not give the thing asked for.",
        "",
        'Asked: "when does my car insurance run out?" Reply: "Your policy document says cover ends on 14 March." '
        '-> {"answer": "yes"}',
        'Asked: "what is my boiler\'s model number?" Reply: "I went through your documents and none of them gives a '
        'boiler model. Want me to try another folder?" -> {"answer": "no"}',
        'Asked: "who is my landlord?" Reply: "The tenancy agreement names Harlow Lettings as the landlord." '
        '-> {"answer": "yes"}',
        'Asked: "how many guests are coming on Saturday?" Reply: "Nothing I opened says how many guests are '
        'coming." -> {"answer": "no"}',
        'Asked: "what time is the school play?" Reply: "The newsletter lists the school play at 6:30 pm on '
        'Thursday." -> {"answer": "yes"}',
        'Asked: "what is my bike frame size?" Reply: "That detail does not appear anywhere in what I can read on '
        'this computer." -> {"answer": "no"}',
        "",
        f'Asked: "{flat(question, 240)}" Reply: "{flat(reply, 600)}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_answered(
    question: str,
    reply: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a reply written after a file search gives what was asked: ``"yes"`` / ``"no"``, or None when
    nothing judged it.

    The file loop's model words a miss as it likes ("is not mentioned in the files", "I don't see any files
    containing ...", "none of them gives ..."), and a list of such wordings is never complete: measured on the real
    app, the same never-told question was handed on to memory in four runs and answered with the loop's miss in a
    fifth. So the reading of the reply is a judgement, asked of the loaded local model like the keep-ask and the
    restore (same lane, sealed site, ledger role). None changes nothing: the caller keeps its own reading."""
    if not str(question or "").strip() or not str(reply or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_answered_prompt(question, reply), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_answered_prompt(question, reply), operation="answered_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


#: The answers of the done-claim judgement (`arbitrate_done_claim`).
DONE_CLAIM_DONE = "done"
DONE_CLAIM_KEPT = "kept"
DONE_CLAIM_NONE = "none"
_DONE_CLAIM_ANSWERS = (DONE_CLAIM_DONE, DONE_CLAIM_KEPT, DONE_CLAIM_NONE)
_DONE_CLAIM_HEAD_CHARS = 700
_DONE_CLAIM_TAIL_CHARS = 300


def _done_claim_prompt(reply: str) -> str:
    """One question about a finished reply: does it report that the assistant has already done something?

    The reply alone is read, not the person's message: with the ask beside it ("put it on my calendar"), the
    judge read a calendar listing or a how-to in the reply as the ask carried out (measured on qwen3:8b, fresh
    replies in four languages: 43 of 48 with the ask, 47 of 48 without)."""
    body = " ".join(str(reply or "").split())
    if len(body) > _DONE_CLAIM_HEAD_CHARS + _DONE_CLAIM_TAIL_CHARS:
        # A done sentence sits at the start ("Done: ...") or at the end ("... I've put it on your list."), so both
        # ends are read when the middle has to go.
        body = body[:_DONE_CLAIM_HEAD_CHARS] + " ... " + body[-_DONE_CLAIM_TAIL_CHARS:]
    return "\n".join([
        "Below is a reply an assistant wrote. It may be in any language.",
        "Question: in this reply, does the assistant report that it ITSELF has ALREADY carried out an action that "
        "changes something outside the conversation (added, saved, created, set, scheduled, booked, sent, paid, moved, "
        "renamed, changed, cancelled or deleted something on a calendar, list, notes, reminders, alarms, contacts, "
        "files, messages, accounts)?",
        'Answer {"answer": "done"} only for such a report of the assistant\'s own finished action.',
        'Answer {"answer": "kept"} when the only such report is that it will remember, or has noted, a fact in its own '
        "memory.",
        'Answer {"answer": "none"} for everything else, including: information or an answer; what a calendar, list, '
        "folder or account already holds; instructions or steps for the reader to follow; a poem, story, list or "
        "other text written out in the reply; an offer, a plan or a question; a refusal or saying it cannot or did "
        "not do it.",
        "",
        'Reply: "Dodano mleko i jajka do listy zakupów." -> {"answer": "done"}',
        'Reply: "Morgen hast du zwei Einträge: 9 Uhr Zahnarzt und 15 Uhr Training." -> {"answer": "none"}',
        'Reply: "Le envié a Ana un mensaje diciendo que llegas tarde." -> {"answer": "done"}',
        'Reply: "Otwórz ustawienia routera, wpisz nowe hasło i kliknij Zapisz." -> {"answer": "none"}',
        'Reply: "Gražus vardas! Įsidėmėjau, kad jūsų dukra Mija." -> {"answer": "kept"}',
        'Reply: "Rain on the roof, a soft grey tune ..." -> {"answer": "none"}',
        'Reply: "C\'est fait, le fichier draft.txt a été supprimé." -> {"answer": "done"}',
        'Reply: "Ich kann keinen Wecker stellen, aber auf dem Handy geht das unter Uhr." -> {"answer": "none"}',
        'Reply: "歯医者の予約を金曜日に変更しました。" -> {"answer": "done"}',
        'Reply: "Shall I look up a taxi number near you?" -> {"answer": "none"}',
        "",
        f'Reply: "{body}" ->',
        'Reply with ONLY the JSON object: {"answer": "done"}, {"answer": "kept"} or {"answer": "none"}',
    ])


def _done_claim_reader(content: str) -> str | None:
    """Strict: one JSON object whose ``answer`` is exactly done / kept / none; anything else is None."""
    match = re.search(r"\{[^{}]*\}", str(content or ""), re.DOTALL)
    try:
        raw = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return None
    value = raw.get("answer") if isinstance(raw, dict) else None
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value in _DONE_CLAIM_ANSWERS else None


def arbitrate_done_claim(
    reply: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a reply says the assistant has already done something: ``"done"``, ``"kept"`` (only a fact noted in
    its own memory), ``"none"``, or None when nothing judged it.

    A done sentence is said in any language and any words ("Pridėjau į kalendorių", "Ich habe den Termin
    eingetragen", "Listo, lo apunté"); the honesty check's English verb lists read none of them, and such a reply,
    with no tool run behind it, was signed "no action claimed" and stamped "Record verified" (blind audit of
    58f60bbf3). Its meaning is not in a list of verbs, so it is asked of the model that judges the turn, like the
    keep ask and the answered reply (same lane, sealed site, ledger role). None decides nothing: the caller keeps
    its own reading."""
    if not str(reply or "").strip():
        return None
    prompt = _done_claim_prompt(reply)
    # Asked when the reply is finished, after every routing judgement of the turn: `last_failure` keeps naming the
    # routing call's own failure (the front door reports it), not this read of the reply.
    routing_failure = _LAST_FAILURE.get("reason", "")
    try:
        model = _keep_judge_model()
        if not model:
            return _judged_for_the_turn(prompt, source_context, _done_claim_reader)
        return arbitrate_followup(
            "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
            prompt=prompt, operation="done_claim_arbitration", model=model,
            runner_role="general", call_role=_judgement_role(), parse=_done_claim_reader,
        )
    finally:
        _LAST_FAILURE["reason"] = routing_failure


def _meaning_prompt(question: str, fact: str) -> str:
    """A balanced yes/no: does a saved fact, in whatever words, give what the question asks for?"""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    return "\n".join([
        "A person asked their assistant a question. The assistant holds one saved fact the person told it earlier.",
        "Question: does the saved fact give what the person asked for?",
        'Answer {"answer": "yes"} when the fact states the thing asked for, even in quite different words.',
        'Answer {"answer": "no"} when the fact is about a different thing or a different person, even if a word is '
        "shared, or when it does not hold what is asked.",
        "",
        'Asked: "what should I not drink before bed?" Saved fact: "coffee after 3 pm keeps me awake all night." '
        '-> {"answer": "yes"}',
        'Asked: "what is my bank\'s sort code?" Saved fact: "my bike lock code is 4471." -> {"answer": "no"}',
        'Asked: "who looks after my teeth?" Saved fact: "Dr Ferro is my dentist." -> {"answer": "yes"}',
        'Asked: "when is my sister\'s birthday?" Saved fact: "my brother\'s birthday is 2 June." -> {"answer": "no"}',
        'Asked: "where do we go on holiday?" Saved fact: "we rent the same cottage on Skye every August." '
        '-> {"answer": "yes"}',
        'Asked: "what is my running pace?" Saved fact: "my running shoes are size 44." -> {"answer": "no"}',
        "",
        f'Asked: "{flat(question, 240)}" Saved fact: "{flat(fact, 400)}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_meaning(
    question: str,
    fact: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a saved fact gives what a question asks for: ``"yes"`` / ``"no"``, or None when nothing judged it.

    An embed model puts a saved row NEAR a question; near is not an answer ("my punting pole is 12 feet" is near
    "what's my pole vault record?"). Before the recall lane treats a row that shares too few words with the
    question as the saved fact asked for, the loaded local model is asked this one yes/no, like the keep ask, the
    restore and the answered reading (same lane, sealed site, ledger role). None changes nothing: the row is not
    the answer and the lane says what it said before."""
    if not str(question or "").strip() or not str(fact or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_meaning_prompt(question, fact), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_meaning_prompt(question, fact), operation="meaning_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


def _same_thing_prompt(question: str, fact: str) -> str:
    """A balanced yes/no: is a saved fact about the SAME thing or person a question asks about?

    Asked of a saved fact the word search found for a question and that does not hold the question's subject, before
    it is shown to the answering model as a saved fact. The recall lane's question (`_meaning_prompt`: does the fact
    give what was asked for?) does not hold this line. Measured on the local model (the fix mission's probe,
    2026-11-18): it read yes for "what crust setting is my toaster on?" against "my bread maker is on crust setting
    3.", for a treadmill against an exercise bike and for a cross trainer against a rowing machine, 3 of 3: the
    same kind of setting on another machine. Asked about the THING, those three read no, the same things under
    other names read yes 4 of 4, and on 24 pairs sealed before the code was read a similar fact about another thing
    read no 11 of 12 (9 of 12 with the other question) with the fact asked for read yes 12 of 12."""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    return "\n".join([
        "A person asked their assistant a question. The assistant holds one saved fact the person told it earlier.",
        "Question: is the saved fact about the SAME thing or person the question asks about?",
        'Answer {"answer": "yes"} when it is the same thing or person, even under another name or in other words, '
        "or when the fact holds what is needed to work the answer out.",
        'Answer {"answer": "no"} when the fact is about a DIFFERENT thing or person of a similar kind: another '
        "machine, another room, another relative, another place. A shared setting, measure or word does not make it "
        "the same thing.",
        "",
        'Asked: "what speed is my blender on?" Saved fact: "my coffee grinder is on speed 2." -> {"answer": "no"}',
        'Asked: "what speed is the liquidiser on?" Saved fact: "my blender is on speed 5." -> {"answer": "yes"}',
        'Asked: "when is my sister\'s birthday?" Saved fact: "my brother\'s birthday is 2 June." -> {"answer": "no"}',
        'Asked: "who looks after my teeth?" Saved fact: "Dr Ferro is my dentist." -> {"answer": "yes"}',
        'Asked: "what\'s the door code for the garage?" Saved fact: "the door code for the shed is 4471." '
        '-> {"answer": "no"}',
        'Asked: "how many days is the Lisbon trip?" Saved fact: "we fly to Lisbon on the 3rd and back on the 9th." '
        '-> {"answer": "yes"}',
        'Asked: "what tog is the duvet in the spare room?" Saved fact: "the duvet in our room is 13.5 tog." '
        '-> {"answer": "no"}',
        'Asked: "where do we go on holiday?" Saved fact: "we rent the same cottage on Skye every August." '
        '-> {"answer": "yes"}',
        "",
        f'Asked: "{flat(question, 240)}" Saved fact: "{flat(fact, 400)}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_same_thing(
    question: str,
    fact: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a saved fact is about the same thing or person a question asks about: ``"yes"`` / ``"no"``, or None
    when nothing judged it. Same call path as the other judgements (one sealed site, the loaded local model, the
    judgement role on the turn's ledger). None decides nothing: the caller shows the fact marked."""
    if not str(question or "").strip() or not str(fact or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_same_thing_prompt(question, fact), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_same_thing_prompt(question, fact), operation="same_thing_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


#: Saved facts put to one naming judgement at most, each cut to this many characters.
NAMED_FACTS_ASKED = 40
_NAMED_FACT_CHARS = 160


def _named_fact_prompt(question: str, facts: list[str]) -> str:
    """One question over a numbered list: which saved fact, if any, is about the thing or person asked about?

    Asked only where no embed model can say which saved rows are near a question (a machine whose owner uses a
    cloud model and has no local one): there the rows that share no word with the question had no way to be found.
    It names a candidate and decides nothing: the fact it names is then put to the same yes/no judgements as a row
    an embed model finds near (`arbitrate_meaning`, `arbitrate_same_thing`)."""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    lines = [
        "A person asked their assistant a question. The assistant holds the saved facts below, which the person told "
        "it earlier.",
        "Question: which ONE saved fact is about the thing or person the question asks about, in whatever words?",
        'Answer {"answer": N} with the number of that fact.',
        'Answer {"answer": 0} when none of them is: a fact about a DIFFERENT thing or person of a similar kind '
        "(another machine, another relative, another place) is not it, and most questions are about nothing saved.",
        "",
        'Asked: "who looks after my teeth?" Saved facts: 1. "my bike lock code is 4471." 2. "Dr Ferro is my dentist." '
        '3. "we rent the same cottage on Skye every August." -> {"answer": 2}',
        'Asked: "when is my sister\'s birthday?" Saved facts: 1. "my brother\'s birthday is 2 June." '
        '2. "coffee after 3 pm keeps me awake all night." -> {"answer": 0}',
        'Asked: "what speed is the liquidiser on?" Saved facts: 1. "my coffee grinder is on speed 2." '
        '2. "my blender is on speed 5." -> {"answer": 2}',
        'Asked: "how tall is Mount Etna?" Saved facts: 1. "my running shoes are size 44." -> {"answer": 0}',
        "",
        f'Asked: "{flat(question, 240)}" Saved facts:',
    ]
    lines += [f'{number}. "{flat(fact, _NAMED_FACT_CHARS)}"' for number, fact in enumerate(facts, 1)]
    lines.append('Reply with ONLY the JSON object: {"answer": N}')
    return "\n".join(lines)


def _named_fact_reader(count: int) -> Callable[[str], str | None]:
    def read(content: str) -> str | None:
        """Strict: one JSON object whose ``answer`` is a whole number from 0 to the count; anything else is None."""
        match = re.search(r"\{[^{}]*\}", str(content or ""), re.DOTALL)
        try:
            raw = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            return None
        value = raw.get("answer") if isinstance(raw, dict) else None
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= count:
            return None
        return str(value)

    return read


def arbitrate_named_fact(
    question: str,
    facts: list[str],
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> int | None:
    """Which of ``facts`` is about the thing a question asks about: its place in the list from 1, 0 for none of
    them, or None when nothing judged it. Same call path as the other judgements (one sealed site, the loaded local
    model or the judge bound for the turn, the judgement role on the turn's ledger). At most `NAMED_FACTS_ASKED`
    facts are read; None and 0 change nothing for the caller."""
    asked = [str(fact or "") for fact in facts][:NAMED_FACTS_ASKED]
    if not str(question or "").strip() or not any(fact.strip() for fact in asked):
        return None
    prompt = _named_fact_prompt(question, asked)
    reader = _named_fact_reader(len(asked))
    model = _keep_judge_model()
    if not model:
        named = _judged_for_the_turn(prompt, source_context, reader)
    else:
        named = arbitrate_followup(
            "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
            prompt=prompt, operation="named_fact_arbitration", model=model,
            runner_role="general", call_role=_judgement_role(), parse=reader,
        )
    return int(named) if named is not None else None


def _forget_targets_prompt(message: str, held: list[str]) -> str:
    """One question over a numbered list: which of the things the person told the assistant does the message ask it
    to forget? Several when it names several ("everything about my tent"). The examples are in several languages: a
    forget is asked in the person's own words and language, and the English command readers know none of them."""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    lines = [
        "A person wrote to their assistant. The assistant holds the things below, which the person told it earlier.",
        "Question: which of them does the message ask the assistant to FORGET (erase, delete from its memory, stop "
        "remembering), in any language and any words?",
        'Answer {"answer": [N]} with the number of each one it asks to forget: every one about the thing it names '
        "when it asks to forget all of it.",
        'Answer {"answer": []} when it asks to forget none of these, or does not ask to forget anything: a question '
        "about them, a new fact, a reminder or a contact to delete is not a forget.",
        "",
        'Held: 1. "my bike lock code is 4471" 2. "Dr Ferro is my dentist" 3. "my bike is the blue one" '
        'Message: "forget everything about my bike" -> {"answer": [1, 3]}',
        'Held: 1. "mano šuo vardu Reksas" 2. "dirbu ligoninėje" Message: "pamiršk ką sakiau apie šunį" '
        '-> {"answer": [1]}',
        'Held: 1. "meine Schwester wohnt in Graz" 2. "ich trinke keinen Kaffee" '
        'Message: "lösch das mit dem Kaffee aus deinem Gedächtnis" -> {"answer": [2]}',
        'Held: 1. "my locker is number 41" Message: "what is my locker number?" -> {"answer": []}',
        'Held: 1. "Ana Ruiz, +1 555 0142, my plumber" Message: "delete the contact Ana Ruiz" -> {"answer": []}',
        'Held: 1. "el gato se llama Miso" Message: "olvida lo del gato, por favor" -> {"answer": [1]}',
        "",
        "Held: " + " ".join(f'{number}. "{flat(item, _NAMED_FACT_CHARS)}"' for number, item in enumerate(held, 1)),
        f'Message: "{flat(message, 400)}" ->',
        'Reply with ONLY the JSON object: {"answer": [N, ...]}',
    ]
    return "\n".join(lines)


def arbitrate_forget_targets(
    message: str,
    held: list[str],
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> tuple[int, ...] | None:
    """Which of ``held`` (what the person told the assistant: saved facts, learned lines, their own sentences of this
    chat) a message asks to forget: their places from 1 (empty for none), or None when nothing judged it. Same call
    path as the other judgements. It only names; the forget command then erases by those items' own words, with
    every store, fence and verification it has. None and () erase nothing."""
    asked = [str(item or "") for item in held][:NAMED_FACTS_ASKED]
    if not str(message or "").strip() or not any(item.strip() for item in asked):
        return None
    prompt = _forget_targets_prompt(message, asked)
    reader = _numbers_reader(len(asked))
    model = _keep_judge_model()
    if not model:
        named = _judged_for_the_turn(prompt, source_context, reader)
    else:
        named = arbitrate_followup(
            "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
            prompt=prompt, operation="forget_targets_arbitration", model=model,
            runner_role="general", call_role=_judgement_role(), parse=reader,
        )
    if named is None:
        return None
    return tuple(int(number) for number in str(named).split(",") if number.strip() and int(number) > 0)


def _recall_facts_prompt(question: str, held: list[str]) -> str:
    """One question over a numbered list: which of the things the person told the assistant answer what the message
    asks? Several when it asks several things. Examples in several languages, for the same reason as the forget one."""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    lines = [
        "A person wrote to their assistant. The assistant holds the things below, which the person told it earlier.",
        "Question: which of them answer what the message asks about the person's own life, people or things, in any "
        "language and any words?",
        'Answer {"answer": [N]} with the number of each one that answers it (several when it asks several things).',
        'Answer {"answer": []} when none of them answers it: one about a DIFFERENT thing or person of a similar kind '
        "does not, and a question about the world, the weather or a file is not about these.",
        "",
        'Held: 1. "my bike lock code is 4471" 2. "Dr Ferro is my dentist" Message: "who is my dentist again?" '
        '-> {"answer": [2]}',
        'Held: 1. "mano šuo vardu Reksas" 2. "Reksas bijo griaustinio" 3. "dirbu ligoninėje" '
        'Message: "kaip vadinasi mano šuo ir ko jis bijo?" -> {"answer": [1, 2]}',
        'Held: 1. "meine Schwester wohnt in Graz" Message: "wo wohnt mein Bruder?" -> {"answer": []}',
        'Held: 1. "my locker is number 41" Message: "what is the capital of Peru?" -> {"answer": []}',
        "",
        "Held: " + " ".join(f'{number}. "{flat(item, _NAMED_FACT_CHARS)}"' for number, item in enumerate(held, 1)),
        f'Message: "{flat(question, 400)}" ->',
        'Reply with ONLY the JSON object: {"answer": [N, ...]}',
    ]
    return "\n".join(lines)


def arbitrate_recall_facts(
    question: str,
    held: list[str],
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> tuple[int, ...] | None:
    """Which of ``held`` (saved facts the turn may read) answer what a message asks: their places from 1 (empty for
    none), or None when nothing judged it. Same call path as the other judgements. It only names: the memory lane
    shows the named facts in their own words, and serves nothing the serving gate withholds."""
    asked = [str(item or "") for item in held][:NAMED_FACTS_ASKED]
    if not str(question or "").strip() or not any(item.strip() for item in asked):
        return None
    prompt = _recall_facts_prompt(question, asked)
    reader = _numbers_reader(len(asked))
    model = _keep_judge_model()
    if not model:
        named = _judged_for_the_turn(prompt, source_context, reader)
    else:
        named = arbitrate_followup(
            "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
            prompt=prompt, operation="recall_facts_arbitration", model=model,
            runner_role="general", call_role=_judgement_role(), parse=reader,
        )
    if named is None:
        return None
    return tuple(int(number) for number in str(named).split(",") if number.strip() and int(number) > 0)


#: At most this many tools (the built-in ones asked about, then the added plugins) are put to one tool-ask judgement.
#: Eight cut the ninth added plugin off from any language but its manifest's (outside review of 261a0389, 2026-11-22).
PLUGIN_ASK_CHOICES = 32
_PLUGIN_ASK_CHARS = 360
#: With more tools than this in one question, each is described more briefly so the question stays one short read.
_PLUGIN_ASK_FULL_CHOICES = 8
_PLUGIN_ASK_BRIEF_CHARS = 180


def _plugin_ask_prompt(message: str, previous: str, plugins: list[str]) -> str:
    """One question over a numbered list: which of the listed tools does the message ask the assistant to use?

    The list is the tools' own names and descriptions (an added plugin's as its maker wrote it). The examples are
    deliberately about other tools and in several languages: a person asks in their own words and their own language,
    and a plugin is often added for exactly that language. More than one can be asked for in one message.

    "None of them" is a choice of its own, numbered 0 and listed first. Without it a small model reading a message no
    tool fits tended to name the first tool: "kiek bus 23 kart 4?" (23 times 4) was judged as the first built-in,
    Reminders, on qwen3:8b (memory-faults run on b43e6aced, 2026-11-22), and the person got an approval card to set a
    reminder instead of 92. The tools keep their numbers from 1."""
    flat = lambda text, n: " ".join(str(text or "").split())[:n]  # noqa: E731
    chars = _PLUGIN_ASK_CHARS if len(plugins) <= _PLUGIN_ASK_FULL_CHOICES else _PLUGIN_ASK_BRIEF_CHARS
    none = '0. "None of them: the message asks for no tool below."'
    lines = [
        "A person wrote to their assistant. The assistant has the tools below.",
        "Question: which of these tools does the message ask the assistant to USE, in any language and any words: to "
        "look something up with it, work something out with it, or do with it what the tool says it does?",
        'Answer {"answer": [N]} with the number of that tool, or several numbers when it asks for several.',
        'Answer {"answer": [0]} (none of them) when the message only talks about the same subject, asks for advice, an '
        "explanation, an opinion, a how-to or a sum the assistant can work out itself, asks for something else, or "
        "asks nothing a tool does. The order of the tools says nothing about which one is meant.",
        "A short follow-up (\"and now?\", \"same for the other one\") asks for the tool its previous message asked for.",
        "",
        f'Tools: {none} 1. "Word counter: counts the words and characters in a text." 2. "Flight board: shows the live '
        'status of a flight by its flight number." Message: "is LX 1440 delayed today?" -> {"answer": [2]}',
        f'Tools: {none} 1. "Alarm clock: rings at a time the person sets." 2. "Word counter: counts the words and '
        'characters in a text." Message: "wie viele Wörter hat: der Hund schläft im Garten" -> {"answer": [2]}',
        f'Tools: {none} 1. "Flight board: shows the live status of a flight by its flight number." '
        'Message: "how do airlines choose flight numbers?" -> {"answer": [0]}',
        f'Tools: {none} 1. "Alarm clock: rings at a time the person sets." 2. "Flight board: shows the live status of '
        'a flight by its flight number." Message: "¿mi vuelo IB3456 ya salió?" -> {"answer": [2]}',
        f'Tools: {none} 1. "Word counter: counts the words and characters in a text." 2. "Flight board: shows the live '
        'status of a flight by its flight number." Message: "skaičiuok žodžius šiame laiške ir pasakyk ar LX 1440 '
        'vėluoja" -> {"answer": [1, 2]}',
        f'Tools: {none} 1. "Alarm clock: rings at a time the person sets." 2. "Word counter: counts the words and '
        'characters in a text." Message: "combien font 18 fois 7 ?" -> {"answer": [0]}',
        f'Tools: {none} 1. "Word counter: counts the words and characters in a text." '
        'Message: "écris-moi un petit poème sur la mer" -> {"answer": [0]}',
        f'Tools: {none} 1. "Flight board: shows the live status of a flight by its flight number." '
        'Previous message: "status of flight LX 1440 please" Message: "and AF 220?" -> {"answer": [1]}',
        f'Tools: {none} 1. "Alarm clock: rings at a time the person sets." 2. "Flight board: shows the live status of '
        'a flight by its flight number." Message: "what should I pack for a long flight?" -> {"answer": [0]}',
        "",
        f"Tools: {none} " + " ".join(f'{number}. "{flat(plugin, chars)}"' for number, plugin in enumerate(plugins, 1)),
    ]
    if str(previous or "").strip():
        lines.append(f'Previous message: "{flat(previous, 240)}"')
    lines.append(f'Message: "{flat(message, 400)}" ->')
    lines.append('Reply with ONLY the JSON object: {"answer": [N, ...]}, or {"answer": [0]} for none of them')
    return "\n".join(lines)


def _numbers_reader(count: int) -> Callable[[str], str | None]:
    def read(content: str) -> str | None:
        """Strict: one JSON object whose ``answer`` is a list of whole numbers from 1 to the count (or one such number,
        0 for none); anything else is None. Returned as the numbers joined by commas ("0" for none)."""
        match = re.search(r"\{[^{}]*\}", str(content or ""), re.DOTALL)
        try:
            raw = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            return None
        value = raw.get("answer") if isinstance(raw, dict) else None
        values = value if isinstance(value, list) else [value]
        picked: list[int] = []
        for item in values:
            if isinstance(item, str) and item.strip().isdigit():
                item = int(item.strip())
            if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= count:
                return None
            if item and item not in picked:
                picked.append(item)
        # "0" for none: an empty string reads as a failed call on the turn's ledger.
        return ",".join(str(number) for number in picked) or "0"

    return read


def arbitrate_plugin_ask(
    message: str,
    plugins: list[str],
    *,
    previous: str = "",
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> tuple[int, ...] | None:
    """Which of the listed tools a message asks to use: their places in ``plugins`` from 1 (empty for none), or None
    when nothing judged it. ``plugins`` are the tools' descriptions (an added plugin's as its maker wrote it). Same call
    path as the other judgements (the loaded local model, or the judge bound for the turn; the judgement role on the
    turn's ledger). None and () change nothing for the caller."""
    asked = [str(plugin or "") for plugin in plugins][:PLUGIN_ASK_CHOICES]
    if not str(message or "").strip() or not any(plugin.strip() for plugin in asked):
        return None
    prompt = _plugin_ask_prompt(message, previous, asked)
    reader = _numbers_reader(len(asked))
    model = _keep_judge_model()
    if not model:
        named = _judged_for_the_turn(prompt, source_context, reader)
    else:
        named = arbitrate_followup(
            "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
            prompt=prompt, operation="plugin_ask_arbitration", model=model,
            runner_role="general", call_role=_judgement_role(), parse=reader, patient=True,
        )
    if named is None:
        return None
    return tuple(int(number) for number in str(named).split(",") if number.strip() and int(number) > 0)


def _lasting_fact_prompt(sentence: str) -> str:
    """A balanced yes/no: is a sentence said in passing a lasting fact about the person's private life?

    The question is about PRIVATE life on purpose. Asked whether a sentence is "a lasting fact about the person's
    life", the local model said yes to half of a set of plain work remarks ("the staging database is out of disk",
    "the client moved the deadline to Friday"; the fix mission's probe of 2026-11-17), and a row written from each
    of those would fill memory with the day's work."""
    flat = " ".join(str(sentence or "").split())[:400]
    return "\n".join([
        "A person said one sentence to their assistant in passing. They did not ask for it to be remembered.",
        "Question: does the sentence tell a lasting fact about the person's PRIVATE life?",
        'Answer {"answer": "yes"} only when it is about their home and belongings and where those are kept or go, '
        "their family, friends, neighbours or pets, their health and body, what they eat and drink, their habits and "
        "routines, or a standing arrangement in their personal life, and it will still be true next month. "
        'It counts even with no "I" or "my" in it.',
        'Answer {"answer": "no"} when it is about work or a project (code, builds, systems, files, documents, '
        "drafts, charts, clients, suppliers, deadlines, meetings), about this conversation, about the world at "
        "large or general knowledge, about today's mood, weather, traffic or a one-off event, or something imagined.",
        "",
        'Said: "the spare duvet lives in the top of the hall cupboard." -> {"answer": "yes"}',
        'Said: "the export job timed out twice on the staging box." -> {"answer": "no"}',
        'Said: "our neighbour Petra feeds the cat when we are away." -> {"answer": "yes"}',
        'Said: "that last paragraph reads much better than the first draft." -> {"answer": "no"}',
        'Said: "the bike with the red frame is Jonas\'s, mine is the grey one." -> {"answer": "yes"}',
        'Said: "the supplier pushed the delivery date to next week." -> {"answer": "no"}',
        'Said: "peppermint tea settles me after a long shift." -> {"answer": "yes"}',
        'Said: "bees can recognise human faces." -> {"answer": "no"}',
        'Said: "the gate at the allotment sticks unless you lift it." -> {"answer": "yes"}',
        'Said: "the report template looks fine now." -> {"answer": "no"}',
        'Said: "ugh, the wifi dropped twice during that call." -> {"answer": "no"}',
        'Said: "suppose the train were cancelled and we had to drive." -> {"answer": "no"}',
        "",
        f'Said: "{flat}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def _standing_state_prompt(sentence: str) -> str:
    """A balanced yes/no: does a sentence say how things ARE in the person's private life, not what HAPPENED?

    The second question of the keep step's judgement, asked only of a sentence the first one
    (`_lasting_fact_prompt`) read as a lasting private fact. Measured on the real app (2026-11-17), the first
    question alone said yes to single past events ("I lost one stirrup leather last week", "i dropped one rating
    point at the last tournament") and each wrote a memory row. This question holds that line and is weaker than
    the first on work remarks (the fix mission's probe: asked alone it read "the pipeline is green again" as a
    yes), so neither is asked alone: a sentence is kept only on a yes to both."""
    flat = " ".join(str(sentence or "").split())[:400]
    return "\n".join([
        "A person said one sentence to their assistant in passing. They did not ask for it to be remembered.",
        "Question: does the sentence say how things ARE in the person's PRIVATE life, in a way that will still hold "
        "next month?",
        'Answer {"answer": "yes"} only when it tells a standing state, habit, taste, routine or arrangement of '
        "their private life: where their things are kept or go, who the people and animals around them are and what "
        "they do, what suits or upsets their body, what they eat and drink, what they regularly do. "
        'It counts even with no "I" or "my" in it.',
        'Answer {"answer": "no"} when it tells something that HAPPENED: one event at one time, however personal '
        "(something lost, broken, bought, won, dropped, a visit, an accident, a day out).",
        'Answer {"answer": "no"} when it is about work or a project (code, builds, systems, files, documents, '
        "drafts, charts, clients, suppliers, deadlines, meetings), about this conversation, about the world at "
        "large or general knowledge, about today's mood, weather or traffic, or something imagined.",
        "",
        'Said: "the spare duvet lives in the top of the hall cupboard." -> {"answer": "yes"}',
        'Said: "I left my umbrella on the bus." -> {"answer": "no"}',
        'Said: "our neighbour Petra feeds the cat when we are away." -> {"answer": "yes"}',
        'Said: "the export job timed out twice on the staging box." -> {"answer": "no"}',
        'Said: "the bike with the red frame is Jonas\'s, mine is the grey one." -> {"answer": "yes"}',
        'Said: "we had the boiler serviced on Tuesday." -> {"answer": "no"}',
        'Said: "peppermint tea settles me after a long shift." -> {"answer": "yes"}',
        'Said: "that last paragraph reads much better than the first draft." -> {"answer": "no"}',
        'Said: "the gate at the allotment sticks unless you lift it." -> {"answer": "yes"}',
        'Said: "my sister came up for the bank holiday." -> {"answer": "no"}',
        'Said: "bees can recognise human faces." -> {"answer": "no"}',
        'Said: "the dog chewed through the hall rug." -> {"answer": "no"}',
        'Said: "the supplier pushed the delivery date to next week." -> {"answer": "no"}',
        'Said: "suppose the train were cancelled and we had to drive." -> {"answer": "no"}',
        "",
        f'Said: "{flat}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def _told_as_fact_prompt(sentence: str) -> str:
    """A balanced yes/no: is a sentence TELLING how something in the person's life is, or is it a request to the
    assistant or something the person is only thinking of?

    The third question of the keep step's judgement, asked only of a sentence the first two read as a lasting
    private fact that says how things are. Measured on the served cb32d1b6 (the before/after rerun of 2026-11-18 and
    a fresh set after it): "rename the soccer video so we know its nadia's", "I'm thinking of painting the box room
    sage green." and "maybe we'll swap the hallway carpet for oak boards one day." each read yes to both and wrote a
    memory row with 'Saved to memory: "..."'. Neither question asks what KIND of sentence it is: both take for
    granted that it states something. Putting that into the second question's wording was tried and measured
    (the fix mission's probe): the two rerun sentences still read yes. Asked by itself it reads them no.

    The first two questions read 11 of 12 fresh maybe / one-day / we-will-probably sentences as yes to both, and
    this question's first wording still read 5 of those 12 as yes ("we'll probably move the piano upstairs at some
    point."; on the real app that cut kept "maybe we'll swap the hallway carpet for oak boards one day."). This
    wording names what has not happened and is not settled. Measured on a set held out from its shaping: 12 of 12
    standing facts that sound like plans read yes, 12 of 12 requests and unsettled things read no; over the
    probe's sets, 72 of 72 lasting sentences read yes."""
    flat = " ".join(str(sentence or "").split())[:400]
    return "\n".join([
        "A person said one sentence to their assistant.",
        "Question: is the person TELLING the assistant how something in their life is?",
        'Answer {"answer": "yes"} when the sentence states a fact that already holds: how something is, where '
        "something is kept, who someone is, what suits them, or what they or others regularly do.",
        'Answer {"answer": "no"} when the sentence asks or tells the assistant to DO something (a task: change, move, '
        'name, sort, find, make or send something), with or without "please".',
        'Answer {"answer": "no"} when the sentence asks the assistant to TELL or REMIND the person of something, with '
        "or without a question mark: that is a question, not a fact.",
        'Answer {"answer": "no"} when the sentence is about something that has not happened and is not settled: '
        "something the person will probably, may, might or could do some time, is thinking of, would like, or has "
        "not decided.",
        "",
        'Said: "the spare duvet lives in the top of the hall cupboard." -> {"answer": "yes"}',
        'Said: "turn the scanned receipt the right way up for me." -> {"answer": "no"}',
        'Said: "our neighbour Petra feeds the cat when we are away." -> {"answer": "yes"}',
        'Said: "I\'m tempted to take up pottery in the autumn." -> {"answer": "no"}',
        'Said: "we go to my mother\'s for Christmas every year." -> {"answer": "yes"}',
        'Said: "copy the wedding pictures onto the stick so gran can have them." -> {"answer": "no"}',
        'Said: "peppermint tea settles me after a long shift." -> {"answer": "yes"}',
        'Said: "we\'ll probably get the drive resurfaced at some stage." -> {"answer": "no"}',
        'Said: "the gate at the allotment sticks unless you lift it." -> {"answer": "yes"}',
        'Said: "one day I\'ll learn to sail." -> {"answer": "no"}',
        'Said: "erinnere mich, wo der Ersatzschlüssel liegt" -> {"answer": "no"}',
        'Said: "my brother takes the dog on Thursdays." -> {"answer": "yes"}',
        'Said: "we could put bunk beds in the back room eventually." -> {"answer": "no"}',
        "",
        f'Said: "{flat}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_lasting_fact(
    sentence: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a sentence said in passing states a lasting fact about the person's own life: ``"yes"`` / ``"no"``,
    or None when nothing judged it.

    The keep step keeps what the person states about their life without being asked. Its readers know such a
    statement by shape ("my ... is ...", "I'm allergic to ..."), and people mention lasting things in any shape
    ("lavender oil on the pillow eases my hay fever"); the same shapes carry moods, remarks about the task and
    talk about the world. So for a sentence no shape lists, the loaded local model is asked this one yes/no, like
    the keep ask (same lane, sealed site, ledger role). None keeps nothing: no fact is written on a guess."""
    if not str(sentence or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_lasting_fact_prompt(sentence), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_lasting_fact_prompt(sentence), operation="lasting_fact_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


def arbitrate_standing_state(
    sentence: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a sentence says how things are in the person's private life rather than something that happened
    once: ``"yes"`` / ``"no"``, or None when nothing judged it. The keep step's second question
    (`_standing_state_prompt` says why there are two). None keeps nothing."""
    if not str(sentence or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_standing_state_prompt(sentence), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_standing_state_prompt(sentence), operation="standing_state_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


def _web_ask_prompt(message: str) -> str:
    """A balanced yes/no: does the message need the internet, or can it be answered from what anyone knows?

    Its own question because the tool judgement over a list reads almost any question as one for the web: on the app
    from source (qwen3:8b, 2026-11-22) "Kas yra fotosintezė?", "Was ist ein schwarzes Loch?" and "¿Quién escribió Don
    Quijote?" were each picked for the web there, and with Web lookups off such a pick refuses an ordinary question."""
    flat = " ".join(str(message or "").split())[:400]
    return "\n".join([
        "A person wrote one message to their assistant.",
        "Question: does answering it need the INTERNET: does it ask to search or look something up online or on a web "
        "page, or ask for something that changes and only the internet knows now (today's news, weather, prices, "
        "opening hours, timetables, scores)?",
        'Answer {"answer": "no"} when an encyclopedia, a textbook or general knowledge answers it (what something is, '
        "who wrote or made something, how something works, a capital, a definition, maths), or when it asks to write, "
        "translate or explain something.",
        "",
        'Message: "search online for the opening hours of the Louvre" -> {"answer": "yes"}',
        'Message: "who painted the Mona Lisa?" -> {"answer": "no"}',
        'Message: "kokie šiandien orai Klaipėdoje?" -> {"answer": "yes"}',
        'Message: "kas yra gravitacija?" -> {"answer": "no"}',
        'Message: "wie steht das Spiel Bayern gegen Dortmund gerade?" -> {"answer": "yes"}',
        'Message: "wie funktioniert ein Kühlschrank?" -> {"answer": "no"}',
        'Message: "busca en internet el precio del billete de tren a Madrid" -> {"answer": "yes"}',
        'Message: "¿cuál es la capital de Perú?" -> {"answer": "no"}',
        "",
        f'Message: "{flat}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_web_ask(
    message: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a message needs the internet: ``"yes"`` / ``"no"``, or None when nothing judged it. Asked only after the
    tool judgement picked the web (core.plugin_ask); None and "no" keep the web out of the turn."""
    if not str(message or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_web_ask_prompt(message), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_web_ask_prompt(message), operation="web_ask_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


def arbitrate_told_as_fact(
    sentence: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a sentence tells how something is, rather than asking the assistant for something or voicing a
    thought not settled: ``"yes"`` / ``"no"``, or None when nothing judged it. The keep step's third question
    (`_told_as_fact_prompt` says why it is its own question). None keeps nothing."""
    if not str(sentence or "").strip():
        return None
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(_told_as_fact_prompt(sentence), source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=_told_as_fact_prompt(sentence), operation="told_as_fact_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


def _memory_kind_prompt(message: str) -> str:
    """One question with four answers: does the message ask the assistant to KEEP something about the person, to TELL
    them something they told it, to FORGET something they told it, or none of these?

    Its own question, asked beside the turn's tool judgement (`core.plugin_ask._memory_kind_confirmed`). Among a dozen tools
    a small model read the three memory choices loosely, and widening one's wording made it eat a neighbour: on the Mac
    (qwen3:8b) "Atsimink šitą: mano palapinė yra oranžinė ..." ("remember this: ...") was judged a forget once the
    forget choice said "also when they only name what it was about", and nothing was saved. Here the three are side by
    side with their differences, and examples in several languages show each against the others."""
    flat = " ".join(str(message or "").split())[:400]
    return "\n".join([
        "A person wrote to their assistant.",
        "Question: what does the message ask the assistant to do with what it knows about the person's own life?",
        'Answer {"answer": "keep"} when the person TELLS the assistant something about themselves, their people or '
        "their things and wants it kept for later (remember it, note it, save it).",
        'Answer {"answer": "recall"} when the person wants to be TOLD something about their own life, people or things '
        "that they could only have told the assistant (what, where, who, when), or what they told it before.",
        'Answer {"answer": "forget"} when the person wants the assistant to FORGET, erase or delete something they told '
        "it, or to stop remembering it.",
        'Answer {"answer": "none"} for anything else: a question about the world, a sum, a task, advice, chat.',
        "Remembering and forgetting are opposites: a message that tells a fact to keep is never a forget.",
        "",
        'Message: "merk dir bitte: meine Schwester heißt Ilse und wohnt in Linz" -> {"answer": "keep"}',
        'Message: "wie heißt meine Schwester nochmal?" -> {"answer": "recall"}',
        'Message: "vergiss alles über meine Schwester" -> {"answer": "forget"}',
        'Message: "recuerda que mi coche es gris y duerme en el garaje" -> {"answer": "keep"}',
        'Message: "¿de qué color es mi coche?" -> {"answer": "recall"}',
        'Message: "borra lo que te dije de mi coche" -> {"answer": "forget"}',
        'Message: "olvida la información de mi bici" -> {"answer": "forget"}',
        'Message: "zapamiętaj: mój kot boi się odkurzacza" -> {"answer": "keep"}',
        'Message: "czego boi się mój kot?" -> {"answer": "recall"}',
        'Message: "nie pamiętaj już niczego o moim kocie" -> {"answer": "forget"}',
        'Message: "note that my dentist is Dr Ferro" -> {"answer": "keep"}',
        'Message: "who did i say my dentist was" -> {"answer": "recall"}',
        'Message: "dimentica quello che ti ho detto sul mio dentista" -> {"answer": "forget"}',
        'Message: "quanto é 17 vezes 3?" -> {"answer": "none"}',
        'Message: "what is the capital of Peru?" -> {"answer": "none"}',
        'Message: "écris un poème sur la mer" -> {"answer": "none"}',
        "",
        f'Message: "{flat}" ->',
        'Reply with ONLY the JSON object: {"answer": "keep"}, {"answer": "recall"}, {"answer": "forget"} or '
        '{"answer": "none"}',
    ])


def _forget_order_prompt(message: str) -> str:
    """A balanced yes/no: does the message ask the assistant to forget something the person told it?

    Asked only when the turn's two memory readings disagree about a forget (`core.plugin_ask._memory_kind_confirmed`):
    a forget cannot be undone, and on qwen3:8b one four-way answer read a Lithuanian forget with slips as a keep and
    another a recall (probe of bef26afe4). One question with two answers is the easier read for a small model."""
    flat = " ".join(str(message or "").split())[:400]
    return "\n".join([
        "A person wrote to their assistant.",
        "Question: does the message ask the assistant to FORGET, erase or delete something the person told it, or to "
        "stop remembering it?",
        'Answer {"answer": "yes"} when it does, in any language and any words, also with spelling slips.',
        'Answer {"answer": "no"} when it tells the assistant something, asks it something, or asks for anything else.',
        "",
        'Message: "vergiss bitte, was ich über meinen Bruder gesagt habe" -> {"answer": "yes"}',
        'Message: "merk dir: mein Bruder heißt Jonas" -> {"answer": "no"}',
        'Message: "olvida mi número de taquilla" -> {"answer": "yes"}',
        'Message: "¿cuál es mi número de taquilla?" -> {"answer": "no"}',
        'Message: "delete what i said about my brother" -> {"answer": "yes"}',
        'Message: "delete the file notes.txt" -> {"answer": "no"}',
        'Message: "napisz wiersz o morzu" -> {"answer": "no"}',
        "",
        f'Message: "{flat}" ->',
        'Reply with ONLY the JSON object: {"answer": "yes"} or {"answer": "no"}',
    ])


def arbitrate_forget_order(
    message: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """Whether a message asks the assistant to forget something: ``"yes"`` / ``"no"``, or None when nothing judged
    it. Same call path as the other judgements."""
    if not str(message or "").strip():
        return None
    prompt = _forget_order_prompt(message)
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(prompt, source_context)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=prompt, operation="forget_order_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(),
    )


MEMORY_KINDS = ("keep", "recall", "forget", "none")


def _memory_kind_reader(content: str) -> str | None:
    """Strict: one JSON object whose ``answer`` is one of MEMORY_KINDS; anything else is None."""
    match = re.search(r"\{[^{}]*\}", str(content or ""), re.DOTALL)
    try:
        raw = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        return None
    value = str(raw.get("answer") or "").strip().casefold() if isinstance(raw, dict) else ""
    return value if value in MEMORY_KINDS else None


def arbitrate_memory_kind(
    message: str,
    *,
    request_id: str = "",
    chat_id: str = "",
    source_context: dict[str, object] | None = None,
) -> str | None:
    """What a message asks of VOOL's memory: "keep", "recall", "forget" or "none"; None when nothing judged it. Same
    call path as the other judgements (`_memory_kind_prompt` says why it is its own question)."""
    if not str(message or "").strip():
        return None
    prompt = _memory_kind_prompt(message)
    model = _keep_judge_model()
    if not model:
        return _judged_for_the_turn(prompt, source_context, _memory_kind_reader)
    return arbitrate_followup(
        "", "", "", request_id=request_id, chat_id=chat_id, source_context=source_context,
        prompt=prompt, operation="memory_kind_arbitration", model=model,
        runner_role="general", call_role=_judgement_role(), parse=_memory_kind_reader,
    )


def _judgement_role() -> str:
    from core.turn_model_call_ledger import JUDGEMENT_ROLE

    return JUDGEMENT_ROLE


#: The smallest local model the keep judgement is asked of, in billions of parameters. Measured on this lane's own
#: probe (24 leads, half of them other tasks): qwen3:8b read 24 of 24, qwen3:4b read every keep ask and every task
#: that points at its text, and qwen3:0.6b -- the helper tag the rest of this module asks -- answered yes to all 24.
_KEEP_JUDGE_MIN_BILLIONS = 4.0


def _keep_judge_model() -> str:
    """The local model that judges a keep ask: the largest one Ollama already has loaded, or "".

    Loaded, because that is the model the chat is being answered by: asking it costs under a second and loads
    nothing (a cold multi-gigabyte load in the middle of a turn is the failure this module's helper tag exists to
    avoid). Largest and at least `_KEEP_JUDGE_MIN_BILLIONS`, because the helper tag cannot make this judgement.
    A tag that does not state its size is not asked. "" is "nobody judged": the caller keeps nothing."""
    if _local_models_disabled():
        return ""
    explicit = str(os.environ.get("VOOL_KEEP_JUDGE_MODEL") or "").strip()
    if explicit:
        return explicit
    try:
        from core.local_ollama_inventory import loaded_ollama_model_names

        sized: list[tuple[float, str]] = []
        for name in loaded_ollama_model_names():
            match = re.search(r":(\d+(?:\.\d+)?)b\b", str(name).lower())
            if match is not None and "embed" not in str(name).lower():
                sized.append((float(match.group(1)), str(name)))
        if not sized:
            return ""
        billions, name = max(sized)
        return name if billions >= _KEEP_JUDGE_MIN_BILLIONS else ""
    except Exception:
        return ""


def parse_followup_decision(content: str) -> str | None:
    """Strict: one JSON object whose ``answer`` is exactly yes / no; anything else is None."""
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{[^{}]*\}", str(content or ""), re.DOTALL)
        if not match:
            return None
        try:
            raw = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    value = raw.get("answer") if isinstance(raw, dict) else None
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if value in {"yes", "no"} else None


def prewarm_async() -> None:
    """Load the arbiter model in the background at boot (fail-soft, never blocks startup).

    A cold qwen3:0.6b load measured ~8.6s on a RAM-loaded box; prewarming at boot + keep_alive
    means a user's first ambiguous turn arbitrates in ~300ms instead of paying that load."""
    if not arbiter_enabled():
        return

    def _warm() -> None:
        try:
            import requests

            model = _resolve_model()
            if not model:
                return
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ok"}],
                "stream": False,
                "think": False,
                "keep_alive": "30m",
                "options": {"num_predict": 1, **_arbiter_runner_options(model)},
            }
            _disclosure_endpoint = _ollama_chat_url()
            permit = seal_direct_provider_invocation(
                destination_url=_disclosure_endpoint,
                control_only=True,
                privacy_class='public',
                provider_id="ollama",
                model_id=model,
                operation="intent_arbiter_prewarm",
                payload=payload,
                request_id=f"intent-arbiter-prewarm-{uuid.uuid4().hex}",
                context_manifest={
                    "chat_id": "",
                    "project_id": "",
                    "items_included": [],
                    "items_excluded": [],
                    "capsule_version": "none",
                },
                max_output_tokens=1,
            )
            _disclosure_body = permit.consume()
            requests.post(
                _disclosure_endpoint,
                **request_options(_disclosure_body, _disclosure_endpoint),
                timeout=(2.0, 60.0),
            )
            received_headers(_disclosure_body)
        except Exception:
            pass

    import threading

    threading.Thread(target=_warm, name="intent-arbiter-prewarm", daemon=True).start()


__all__ = ["CHOICE_CHAT", "ArbiterDecision", "arbiter_enabled", "arbitrate", "last_failure", "prewarm_async"]
