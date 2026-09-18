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
import uuid
from dataclasses import dataclass

from core.agent_runtime.intent_claims import (
    FAMILY_DISK_USAGE,
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
from core.prompt_debug import dump_outbound_prompt
from core.provider_invocation_gateway import seal_direct_provider_invocation
from core.runtime_provider_defaults import _ollama_context_window_for_bundle_role

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
        tags = requests.get(f"{base}/api/tags", timeout=1.5).json().get("models") or []
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


def _arbiter_num_ctx(model: str) -> int:
    """The sized context this lane should load at, from the runtime's own sizing authority.

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
    """

    return _ollama_context_window_for_bundle_role("classifier", model_tag=str(model or ""))


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


def last_failure() -> str:
    """Why the most recent arbitrate() failed open ('' when it succeeded) — for the decision log."""
    return _LAST_FAILURE.get("reason", "")


def _breaker_open() -> bool:
    import time

    until = _BREAKER.get("open_until", 0.0)
    return time.monotonic() < until


def _trip_breaker() -> None:
    import time

    _BREAKER["open_until"] = time.monotonic() + _BREAKER_COOLOFF_S


def reset_breaker() -> None:
    """Test hook + successful-call reset."""
    _BREAKER.pop("open_until", None)


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
    if _breaker_open():
        _LAST_FAILURE["reason"] = "breaker_open"
        return None
    model = _resolve_model()
    if not model:
        _LAST_FAILURE["reason"] = "no_model"
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
            # model at its native context for the full thirty minutes (see _arbiter_num_ctx).
            "options": {
                "temperature": 0,
                "num_predict": 96,
                "num_ctx": _arbiter_num_ctx(model),
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
        permit = seal_direct_provider_invocation(
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
        response = requests.post(
            _ollama_chat_url(),
            json=permit.consume(),
            timeout=(1.5, _TIMEOUT_S),
        )
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
            _trip_breaker()  # saturated box: don't stack waits — cool off, fail open instantly
        return None
    reset_breaker()  # a served request proves the box is healthy again
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
                "options": {"num_predict": 1, "num_ctx": _arbiter_num_ctx(model)},
            }
            permit = seal_direct_provider_invocation(
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
            requests.post(
                _ollama_chat_url(),
                json=permit.consume(),
                timeout=(2.0, 60.0),
            )
        except Exception:
            pass

    import threading

    threading.Thread(target=_warm, name="intent-arbiter-prewarm", daemon=True).start()


__all__ = ["CHOICE_CHAT", "ArbiterDecision", "arbiter_enabled", "arbitrate", "last_failure", "prewarm_async"]
