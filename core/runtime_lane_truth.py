"""Which provider is connected, which lane served the turn — read, never guessed.

Measured on the installed build (commit 12128c2, 3/3 unpinned AND 3/3 pinned). Asked "Which cloud
provider is currently connected to this runtime, and is the connection working?", VOOL replied:

    "This runtime runs locally on your machine - there's no cloud provider connected. The
     'connection' is just the local execution environment (the sandbox/tooling available here),
     and it's working normally."

That message contradicted itself. Its own footer read ``cloud | nemotron-3-ultra-550b-a55b:free |
1,295 tok`` and ``GET /api/cloud/status`` returned ``{"provider":"openrouter","state":"ok",
"detail":"authorized","http_status":200}``. The turn was served BY the provider it denied having.

The cause was ownership, not phrasing. Nothing claimed the question, so it fell through to the
model, and the model answered from its own priors about where language models usually run. The
connected provider, the key's probe verdict and the lane that served a turn are facts this runtime
holds on disk; a replaceable reasoning engine is the last thing that should be asked for them.

So this module reads them:

* **connection** — ``core.cloud_connection_state.connection_status()``, the same call that backs
  ``GET /api/cloud/status``, so the chat answer and the header pill can never disagree.
* **selection** — ``core.cloud_escalation_policy.load_policy()`` for the operator's chosen model,
  escalation mode and provider, which is what ``/api/cloud/status`` layers on top.
* **last served lane** — ``core.usage_meter.last_served_call()``, the metered row for the last
  response a model actually produced.

**No key material, ever.** Nothing here reads a key's value: ``connection_status`` returns a state
and a digest-checked verdict, never the secret. Every rendered line is passed through
``core.secret_redaction.redact_secrets`` on the way out as a second, independent guard, so a
provider label or model id that ever carried a key shape is masked rather than printed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from core.secret_redaction import redact_secrets

# Which facet of the runtime the question is aimed at. All four are answered from the same
# snapshot; the facet only decides which line leads, so a near-miss between two of them still
# returns the truth rather than the wrong truth.
FACET_PROVIDER = "provider"   # "which cloud provider is connected?"
FACET_LANE = "lane"           # "am I on cloud or local right now?"
FACET_KEY = "key"             # "is my key working?"
FACET_MODEL = "model"         # "which model answered this?" / "did a cloud model answer that?"

# The question has to be about THIS runtime, not about cloud providers as a subject. Without this
# guard, "which cloud provider is cheapest?" and "how do I check if an api key is working in
# python?" would both be seized by the fast path and answered with an irrelevant status block.
_SELF_REFERENCE = re.compile(
    r"\b(?:you|your|you're|youre|u|i|i'm|im|me|my|we|our|this|that|it|its|it's|here|now|current|"
    r"currently|right now|runtime|vool|vool|session|turn|chat|reply|answer|response|machine|"
    r"connection|connections|connected|configured|active|set ?up|enabled|serving|answered|"
    r"answering|using|used)\b",
    re.IGNORECASE,
)

# A question about how to BUILD something that talks to a provider is a coding request, not a
# status request, however self-referential its wording. Checked before every facet.
_BUILD_INTENT = re.compile(
    r"\b(?:write|code|implement|build|create|add|generate|show me how|how do i|how to|example of|"
    r"snippet|function|script|library|sdk|tutorial|teach me|explain how)\b",
    re.IGNORECASE,
)

# "How is X connected?" asks for a MECHANISM; "is X connected?" asks for a STATE. Only the second is
# a fact this runtime holds. Without this, exempting the state patterns from the self-reference
# guard (below) also handed over "how is the api connected to the frontend?" — an architecture
# question about the user's own code, answered with a provider-status block.
_EXPLANATION_INTENT = re.compile(
    r"^\s*how\b|\bhow (?:is|are|was|were|do|does|did|can|could|would|should|might)\b",
    re.IGNORECASE,
)

# "is my key working?" means the cloud API key here — but this product also holds Solana keypairs,
# and answering a wallet question with a provider-status block would be a confident wrong answer of
# exactly the kind this module exists to remove. Named wallet subjects are left to their own path.
_WALLET_SUBJECT = re.compile(
    r"\b(?:wallet|keypair|key ?pair|private[- ]key|secret[- ]key|seed|mnemonic|solana|sol\b|signer|"
    r"pubkey|public[- ]key|address)\b",
    re.IGNORECASE,
)

# "what can you do locally on this machine right now?" is a capability INVENTORY question, and the
# grounded inventory path already owns it. Caught in the full suite, not by hand: the lane patterns
# below saw "locally ... right now" and seized a prompt three contract fixtures depend on.
_CAPABILITY_INVENTORY = re.compile(
    r"\bwhat (?:can|could|do) (?:you|u|i|we)\b"
    r"|\bwhat are you able to\b"
    r"|\bwhat (?:are|is) (?:your|you)\b[^.?!]{0,30}\b(?:powers|abilities|capabilities|skills|tools)\b"
    r"|\bwhat (?:tools|capabilities|powers|abilities)\b",
    re.IGNORECASE,
)

_PROVIDER_WORD = r"(?:cloud|provider|api|llm|inference|backend|endpoint)"

# People say "my own box" as often as "locally". Driving the built runtime with phrasings that were
# not in the fixtures found "so whats actually powering you right now, cloud or my own box?" still
# reaching the model, which answered "I'm running on your own machine" — the original defect intact
# under different words. Vocabulary, not structure, was the gap.
_LOCAL_WORD = (
    r"(?:local(?:ly)?|on[- ]device|on[- ]prem(?:ise)?s?|offline|"
    r"(?:on |in )?(?:my|this|your) (?:own )?(?:machine|box|computer|laptop|desktop|hardware|pc|mac|rig)|"
    r"my (?:own )?(?:side|end))"
)
_CLOUD_WORD = r"(?:cloud|remote(?:ly)?|api|server|off[- ]device|somewhere else|a data ?cent(?:er|re))"

# "powering" and "driving" ask the same question as "running" and were the specific verbs missed.
_SERVE_VERB = r"(?:running|runs|run|executing|hosted|served|serving|powering|powered|powers|driving|drives)"

# "Running" as a state of SUPPLY, not of serving: "running low on space on this machine" is a disk
# question, and it matched the serve-verb + locality pattern wholesale ("running" + "on this
# machine") -- measured live 2026-09-16, a largest-folders question was answered with the canned
# "Last model call: cloud -- ..." runtime-status text instead. A serve verb followed within two
# words by one of these particles is depletion idiom, never a lane question.
_DEPLETION_IDIOM_RE = re.compile(
    r"\b(?:running|runs?|executing|serving|driving|drives)\s+"
    r"(?:\w+\s+){0,1}(?:low|out|short|late|behind|hot|cold|dry|empty|slow|down|behind\s+schedule)\b",
    re.IGNORECASE,
)

# (facet, pattern, needs_self_reference).
#
# Most patterns describe a shape that a general-knowledge question shares — "which cloud provider is
# cheapest?" has the same skeleton as "which cloud provider is connected?" — so they must also prove
# the question is about THIS runtime. A few carry that proof in their own predicate: nobody asks the
# runtime "is the provider hooked up?" about a third party. Those are exempt, because requiring a
# pronoun on top of them is what let "is the provider hooked up and healthy?" reach the model.
_FACET_PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    # "am I on cloud or local", "are you running locally or in the cloud right now?",
    # "whats powering you, cloud or my own box?"
    (
        FACET_LANE,
        re.compile(
            rf"\b{_LOCAL_WORD}\b[^.?!]{{0,40}}\bor\b[^.?!]{{0,40}}\b{_CLOUD_WORD}\b"
            rf"|\b{_CLOUD_WORD}\b[^.?!]{{0,40}}\bor\b[^.?!]{{0,40}}\b{_LOCAL_WORD}\b",
            re.IGNORECASE,
        ),
        True,
    ),
    # "are you running locally right now?", "is this running in the cloud?", "am i local?"
    #
    # The second alternative requires an explicit subject frame. It used to accept any lane word
    # followed by "right now", which swallowed "what can you do locally on this machine right now?"
    # — a capability-inventory prompt owned elsewhere, and the one false positive the full suite
    # found. A bare adverb plus a time word is not a question about the lane.
    (
        FACET_LANE,
        re.compile(
            rf"\b{_SERVE_VERB}\b[^.?!]{{0,30}}\b(?:{_LOCAL_WORD}|in the cloud|on the cloud|remotely)\b"
            rf"|\b(?:are you|are we|am i|is (?:this|it|that)|is vool|is vool)\b[^.?!]{{0,20}}"
            rf"\b(?:{_LOCAL_WORD}|in the cloud|on the cloud|remote|cloud[- ]based)\b",
            re.IGNORECASE,
        ),
        True,
    ),
    # "did a cloud model answer that?", "which model answered this?", "what model is replying?"
    (
        FACET_MODEL,
        re.compile(
            r"\b(?:which|what|who|whose)\b[^.?!]{0,30}\bmodel\b[^.?!]{0,40}"
            r"\b(?:answer(?:ed|ing)?|repl(?:y|ied|ying)|respond(?:ed|ing)?|serv(?:ed|ing)|"
            r"generat(?:ed|ing)|wrote|produced|handl(?:ed|ing)|using|used|are you|is this|ran)\b"
            r"|\bdid\b[^.?!]{0,30}\b(?:cloud|local|remote|paid|free)?\s?model\b[^.?!]{0,30}"
            r"\b(?:answer|repl(?:y|ied)|respond|serve|handle|write|produce)\w*\b"
            r"|\bwhat model (?:are|is)\b",
            re.IGNORECASE,
        ),
        True,
    ),
    # "is my key working?", "does my openrouter api key work?", "is the key authorized?"
    (
        FACET_KEY,
        re.compile(
            r"\b(?:api[- ]?key|key|token|credential)s?\b[^.?!]{0,40}"
            r"\b(?:work(?:s|ing)?|valid|invalid|ok|okay|good|live|active|authori[sz]ed|accepted|"
            r"expired|set|configured|connected|fail(?:ed|ing)?)\b"
            r"|\b(?:work(?:s|ing)?|valid|authori[sz]ed|configured|expired)\b[^.?!]{0,30}"
            r"\b(?:api[- ]?key|key|token|credential)s?\b",
            re.IGNORECASE,
        ),
        True,
    ),
    # Ask for the provider currently serving this runtime. A provider noun plus a question word
    # is not enough: recommendations about an LLM runtime also contain both. Require the current
    # selection/serving relation; connectivity questions have their own state predicate below.
    (
        FACET_PROVIDER,
        re.compile(
            rf"\b(?:which|what|whose)\s+(?:cloud\s+|inference\s+)?{_PROVIDER_WORD}\b"
            r"[^.?!]{0,30}\b(?:am i on|are we on|are you on|is this on|"
            r"(?:are you|are we|am i|is this|is vool|is vool) (?:currently )?using|"
            r"(?:is|are) (?:currently )?(?:selected|configured|active|connected|serving)|"
            r"(?:powers|is powering|is serving) (?:you|this|vool|vool))\b",
            re.IGNORECASE,
        ),
        True,
    ),
    # "is the provider hooked up and healthy?", "is the connection working?", "are you connected?"
    #
    # Exempt from the self-reference guard: the predicate IS the self-reference. Found by driving
    # the built runtime — "is the provider hooked up and healthy?" contains no pronoun, failed the
    # guard, reached the model, and was answered in prose the model had no way to verify.
    (
        FACET_PROVIDER,
        re.compile(
            rf"\b{_PROVIDER_WORD}\b[^.?!]{{0,30}}\b(?:connect(?:ed|ion|ing)?|configured|active|"
            rf"in use|hooked up|wired|plugged|reachable|authori[sz]ed)\b"
            rf"|\bconnect(?:ed|ion|ing)?\b[^.?!]{{0,30}}\b(?:work(?:s|ing)?|ok|okay|up|live|alive|"
            rf"good|healthy|fine|down|broken|dead|fail(?:ed|ing)?)\b"
            rf"|\bam i connected\b|\bare you connected\b",
            re.IGNORECASE,
        ),
        False,
    ),
)

_STATE_PHRASE = {
    "ok": "connected and authorized",
    "failed": "NOT working",
    "untested": "a key is configured but no live probe has verified it yet",
    "no_key": "no cloud key is configured",
}


def runtime_lane_question(text: str) -> str:
    """The facet of runtime truth this text asks for, or ``""`` when it asks for none.

    Deliberately narrow. A question must name a runtime fact AND refer to this runtime, must not be
    a request to write code that talks to a provider, and must not belong to a family that another
    grounded path already owns.
    """
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned or len(cleaned) > 400:
        return ""
    if _BUILD_INTENT.search(cleaned) or _WALLET_SUBJECT.search(cleaned):
        return ""
    if _EXPLANATION_INTENT.search(cleaned):
        return ""
    if _CAPABILITY_INVENTORY.search(cleaned):
        return ""
    about_this_runtime = bool(_SELF_REFERENCE.search(cleaned))
    if _DEPLETION_IDIOM_RE.search(cleaned):
        # "running low on space on this machine" -- depletion idiom, not a serving question.
        # Checked before the facet scan because the lane pattern's own vocabulary ("running" +
        # "on this machine") is exactly what the idiom contains.
        return ""
    for facet, pattern, needs_self_reference in _FACET_PATTERNS:
        if needs_self_reference and not about_this_runtime:
            continue
        if pattern.search(cleaned):
            return facet
    return ""


def _utc(value: Any) -> str:
    """An epoch float or ISO string as ``YYYY-MM-DD HH:MM UTC``; ``""`` when there is nothing."""
    if value in (None, "", 0, 0.0):
        return ""
    try:
        if isinstance(value, (int, float)):
            stamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            stamp = stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except Exception:
        return ""
    return stamp.strftime("%Y-%m-%d %H:%M UTC")


def runtime_lane_snapshot() -> dict[str, Any]:
    """Everything the answer needs, read from the live runtime. Never raises, never returns a key.

    Each source is read independently and fails soft, so one unavailable source degrades a single
    line to "unknown" instead of forcing the whole answer back to the model that got it wrong.
    """
    snapshot: dict[str, Any] = {
        "provider": "",
        "provider_label": "",
        "state": "",
        "detail": "",
        "http_status": None,
        "checked_at": "",
        "selected_model": "",
        "escalation_mode": "",
        "free_cloud_enabled": None,
        "last_call": None,
    }

    try:
        # The same call GET /api/cloud/status serves, so the chat answer and the header pill are
        # one fact rather than two that can drift. Cache-only: a chat question must not fire a
        # network probe at the provider.
        from core.cloud_connection_state import connection_status

        status = connection_status()
        snapshot["provider"] = str(status.get("provider") or "")
        snapshot["provider_label"] = str(status.get("label") or "")
        snapshot["state"] = str(status.get("state") or "")
        snapshot["detail"] = str(status.get("detail") or "")
        snapshot["http_status"] = status.get("http_status")
        snapshot["checked_at"] = _utc(status.get("checked_at"))
    except Exception:
        pass

    try:
        from core.cloud_escalation_policy import load_policy

        policy = load_policy()
        snapshot["selected_model"] = str(getattr(policy, "model", "") or "")
        snapshot["escalation_mode"] = str(getattr(policy, "mode", "") or "")
        snapshot["free_cloud_enabled"] = bool(getattr(policy, "free_cloud_enabled", False))
    except Exception:
        pass

    try:
        from core.usage_meter import last_served_call

        snapshot["last_call"] = last_served_call()
    except Exception:
        pass

    return snapshot


def _provider_line(snapshot: dict[str, Any]) -> str:
    provider = snapshot.get("provider_label") or snapshot.get("provider") or ""
    state = str(snapshot.get("state") or "")
    if not provider or not state:
        return "Cloud provider: unknown — this runtime could not read its own connection state."
    if state == "no_key":
        return "Cloud provider: none connected — no cloud key is configured on this runtime."
    phrase = _STATE_PHRASE.get(state, state)
    line = f"Cloud provider: {provider} — {phrase}"
    bits = []
    detail = str(snapshot.get("detail") or "")
    if detail and state != "untested":
        bits.append(detail)
    http_status = snapshot.get("http_status")
    if isinstance(http_status, int):
        bits.append(f"HTTP {http_status}")
    checked = str(snapshot.get("checked_at") or "")
    if checked:
        bits.append(f"last checked {checked}")
    return line + (f" ({', '.join(bits)})" if bits else "") + "."


def _key_line(snapshot: dict[str, Any]) -> str:
    """Key STATUS only. The key's value is never read by this module and never printed."""
    state = str(snapshot.get("state") or "")
    provider = snapshot.get("provider_label") or snapshot.get("provider") or "the active provider"
    if state == "no_key":
        return f"Key: none stored for {provider}."
    if state == "ok":
        return f"Key: stored for {provider} and accepted by a live auth probe."
    if state == "failed":
        return f"Key: stored for {provider} but REJECTED — {snapshot.get('detail') or 'the last auth probe failed'}."
    if state == "untested":
        return f"Key: stored for {provider}, not yet verified by a live probe."
    return "Key: status unknown — this runtime could not read its own credential state."


def _selection_line(snapshot: dict[str, Any]) -> str:
    model = str(snapshot.get("selected_model") or "")
    mode = str(snapshot.get("escalation_mode") or "")
    bits = [f"cloud model {model}" if model else "no cloud model pinned (runtime default)"]
    if mode:
        bits.append(f"escalation mode {mode}")
    if snapshot.get("free_cloud_enabled") is True:
        bits.append("free cloud lane enabled")
    return "Selection: " + ", ".join(bits) + "."


def _last_call_line(snapshot: dict[str, Any]) -> str:
    call = snapshot.get("last_call")
    if not isinstance(call, dict) or not call:
        return "Last model call: none recorded on this runtime yet."
    lane = str(call.get("lane") or "")
    model = str(call.get("model_id") or "") or "unknown model"
    when = _utc(call.get("created_at"))
    tail = f" at {when}" if when else ""
    return f"Last model call: {lane} — {model}{tail}."


# This answer is produced by the runtime, not by a model. Saying so is the specific correction the
# defect needs: the wrong reply described the whole runtime as local because the MODEL that wrote
# it had no way to see the cloud lane it had just been served by.
_THIS_REPLY_LINE = "This reply: answered by VOOL's runtime directly from stored state — no model call."


def render_runtime_lane_answer(snapshot: dict[str, Any], *, facet: str = FACET_PROVIDER) -> str:
    """The grounded answer. The facet leads; the full state always follows, in a fixed order."""
    provider = _provider_line(snapshot)
    key = _key_line(snapshot)
    selection = _selection_line(snapshot)
    last_call = _last_call_line(snapshot)

    if facet == FACET_KEY:
        ordered = [key, provider, last_call, selection]
    elif facet == FACET_MODEL:
        ordered = [last_call, provider, selection, key]
    elif facet == FACET_LANE:
        ordered = [last_call, provider, key, selection]
    else:
        ordered = [provider, key, selection, last_call]

    body = "\n".join([*ordered, _THIS_REPLY_LINE])
    # Second, independent guard. Nothing above reads key material, so this is expected to be a
    # no-op — which is the point: if a provider label or model id ever carries a key shape, it is
    # masked here rather than printed to chat.
    return redact_secrets(body)


def maybe_runtime_lane_answer(text: str) -> str:
    """The rendered answer for a runtime-truth question, or ``""`` when the text is not one."""
    facet = runtime_lane_question(text)
    if not facet:
        return ""
    return render_runtime_lane_answer(runtime_lane_snapshot(), facet=facet)


__all__ = [
    "FACET_KEY",
    "FACET_LANE",
    "FACET_MODEL",
    "FACET_PROVIDER",
    "maybe_runtime_lane_answer",
    "render_runtime_lane_answer",
    "runtime_lane_question",
    "runtime_lane_snapshot",
]
