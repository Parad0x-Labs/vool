"""Pay-per-answer AI over x402: ask a model at an endpoint the owner added, pay that one answer from a Crypto Pilot
wallet, and check that the model which answered is the model that was asked for.

The owner adds an endpoint in Settings (Crypto, Pay-per-answer AI): an OpenAI-compatible base URL, the one model to
ask there, and the most one answer may cost. Nothing is added by default and nothing here runs until the owner adds
one; crypto itself stays off until the owner turns it on.

One question, one payment:

1. ``ai.paid_ask`` sends the question (only the question: no chat history, no memory) once, as a chat completion for
   the named model. A free answer is used as it is. A 402 is read before anything is parked: only a USDC offer on Base
   that the wallet's x402 SDK lane pays (the ``exact`` EIP-3009 instrument), at or under the endpoint's own price limit
   and the wallet's x402 cap, becomes ONE proposal bound to exactly this request body
   (:func:`core.wallet.x402_sdk.park_challenge`). Nothing is signed here.
2. The owner approves it on the Crypto Pilot sheet with the wallet's own credential, under the wallet's limits; the
   receipt is the wallet's. This module holds no approval, signing or retry authority.
3. The approval delivers the answer once (the SDK lane's one-shot delivery). :func:`after_approval` takes it, reads the
   ``model`` the endpoint says answered, and posts the answer into the chat that asked, labelled with the verdict:

   * ``match``    -- the endpoint named the model that was paid for;
   * ``mismatch`` -- it named another model. The answer is still shown (it was paid for), clearly marked, and the
     endpoint is paused: nothing more is paid there until the owner turns it back on;
   * ``unnamed``  -- it named no model. Treated like a mismatch: shown marked, endpoint paused.

What the check is and is not: it compares the model the endpoint reports with the model the request asked and paid
for, and refuses to keep paying an endpoint that reports something else. It catches a relabelled or swapped model that
the endpoint admits to; it cannot prove what ran behind an endpoint that reports the requested name falsely. The reply
says that in one line, and the Settings card says it too.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

INTENT_ASK = "ai.paid_ask"
SURFACE = "wallet"
AUTHORITY = "core.x402_ai"
_FILE = "x402_ai.json"
MAX_ENDPOINTS = 12
MAX_QUESTION_CHARS = 8000
MAX_ANSWER_TOKENS = 2048
MAX_TRACKED_REQUESTS = 200
REQUEST_TIMEOUT_S = 60.0
#: Base mainnet and Base Sepolia: the rows the x402 SDK lane pays on
_LANE_NETWORKS = ("eip155:8453", "eip155:84532")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_LABEL_RE = re.compile(r"^[^\x00-\x1f<>]{1,40}$")
_DATED_SUFFIX = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})$")
_LOCK = threading.RLock()

VERDICT_MATCH = "match"
VERDICT_MISMATCH = "mismatch"
VERDICT_UNNAMED = "unnamed"
CHECK_LIMIT_NOTE = ("VOOL compares the model the service names with the one you paid for. It cannot see what really runs "
                    "behind the service.")


class PaidAskError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- the owner's endpoints

def _path():
    from core.runtime_paths import data_path

    return data_path(_FILE)


def _load() -> dict[str, Any]:
    path = _path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    endpoints = [e for e in raw.get("endpoints") or [] if isinstance(e, dict) and e.get("id")]
    requests = {k: v for k, v in (raw.get("requests") or {}).items() if isinstance(v, dict)}
    return {"endpoints": endpoints[:MAX_ENDPOINTS], "requests": requests}


def _save(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    requests = state.get("requests") or {}
    if len(requests) > MAX_TRACKED_REQUESTS:
        keep = sorted(requests.items(), key=lambda kv: float(kv[1].get("asked_at") or 0))[-MAX_TRACKED_REQUESTS:]
        requests = dict(keep)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"endpoints": state.get("endpoints") or [], "requests": requests}, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _public_view(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {k: endpoint.get(k) for k in ("id", "label", "base_url", "model", "max_price_minor", "on", "paused_reason", "last_verdict", "answers")}


def endpoints() -> list[dict[str, Any]]:
    with _LOCK:
        return [_public_view(e) for e in _load()["endpoints"]]


def _clean_base_url(value: Any) -> str:
    text = str(value or "").strip().rstrip("/")
    parts = urlsplit(text)
    from core.wallet import outbound

    # plain http only for a loopback service under the wallet's own loopback test switch (the hermetic x402 rigs)
    loopback_http = parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"} and outbound._loopback_switch()
    if (parts.scheme != "https" and not loopback_http) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise PaidAskError("endpoint_url_invalid", "The address must be a plain https:// address, like https://example.com/v1.")
    return text


def _price_minor(value: Any) -> int:
    """USDC has 6 decimals; the owner types dollars ("0.05") and the store keeps minor units."""
    text = str(value if value is not None else "").strip().lstrip("$")
    if not re.fullmatch(r"\d{1,4}(?:\.\d{1,6})?", text):
        raise PaidAskError("endpoint_price_invalid", "Type the most one answer may cost, in USDC, for example 0.05.")
    whole, _, frac = text.partition(".")
    minor = int(whole) * 1_000_000 + int((frac + "000000")[:6])
    if minor <= 0:
        raise PaidAskError("endpoint_price_invalid", "The price limit must be more than zero.")
    return minor


def add_endpoint(*, label: Any, base_url: Any, model: Any, max_price: Any) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not _LABEL_RE.fullmatch(clean_label):
        raise PaidAskError("endpoint_label_invalid", "Give the service a short name, up to 40 characters.")
    clean_model = str(model or "").strip()
    if not _MODEL_RE.fullmatch(clean_model):
        raise PaidAskError("endpoint_model_invalid", "Type the model name exactly as the service lists it.")
    entry = {"id": "ai-" + uuid.uuid4().hex[:12], "label": clean_label, "base_url": _clean_base_url(base_url), "model": clean_model,
             "max_price_minor": _price_minor(max_price), "on": True, "paused_reason": "", "last_verdict": "", "answers": 0}
    with _LOCK:
        state = _load()
        if len(state["endpoints"]) >= MAX_ENDPOINTS:
            raise PaidAskError("endpoint_limit", f"You can keep up to {MAX_ENDPOINTS} services. Remove one first.")
        if any(e["label"].casefold() == clean_label.casefold() for e in state["endpoints"]):
            raise PaidAskError("endpoint_label_taken", "You already have a service with that name.")
        state["endpoints"].append(entry)
        _save(state)
    return _public_view(entry)


def set_endpoint_on(endpoint_id: Any, on: bool) -> dict[str, Any]:
    with _LOCK:
        state = _load()
        for entry in state["endpoints"]:
            if entry["id"] == str(endpoint_id or ""):
                entry["on"] = bool(on)
                if on:
                    entry["paused_reason"] = ""
                _save(state)
                return _public_view(entry)
    raise PaidAskError("endpoint_not_found", "That service is not in your list.")


def remove_endpoint(endpoint_id: Any) -> None:
    with _LOCK:
        state = _load()
        kept = [e for e in state["endpoints"] if e["id"] != str(endpoint_id or "")]
        if len(kept) == len(state["endpoints"]):
            raise PaidAskError("endpoint_not_found", "That service is not in your list.")
        state["endpoints"] = kept
        _save(state)


def _find_endpoint(name: Any) -> dict[str, Any]:
    wanted = str(name or "").strip().casefold()
    listed = _load()["endpoints"]
    if not listed:
        raise PaidAskError("no_endpoint", "You have not added a pay-per-answer AI service yet. Add one in Settings → Crypto → Pay-per-answer AI.")
    if not wanted:
        if len(listed) == 1:
            return listed[0]
        raise PaidAskError("endpoint_ambiguous", "Which service should I ask? Yours: " + ", ".join(e["label"] for e in listed) + ".")
    for entry in listed:
        if wanted in {entry["id"].casefold(), entry["label"].casefold(), entry["model"].casefold()}:
            return entry
    raise PaidAskError("endpoint_not_found", "No service has that name. Yours: " + ", ".join(e["label"] for e in listed) + ".")


# --------------------------------------------------------------------------- the model check

def _norm(model: str) -> str:
    return str(model or "").strip().casefold()


def model_verdict(asked: str, answered: Any) -> str:
    """``match`` when the endpoint reports the model that was asked (and paid) for, allowing only the spellings that
    name the same model: letter case, a provider prefix (``org/name``) on either side, and a dated release suffix
    (``name-2025-01-31``) on the answer. Anything else is ``mismatch``; no name at all is ``unnamed``."""
    got = _norm(answered) if isinstance(answered, str) else ""
    if not got:
        return VERDICT_UNNAMED
    want = _norm(asked)
    candidates = {got, got.rsplit("/", 1)[-1], _DATED_SUFFIX.sub("", got), _DATED_SUFFIX.sub("", got.rsplit("/", 1)[-1])}
    wants = {want, want.rsplit("/", 1)[-1]}
    return VERDICT_MATCH if candidates & wants else VERDICT_MISMATCH


# --------------------------------------------------------------------------- asking

def _pilot_wallet_for(headers: dict[str, Any]) -> str:
    """A ready Crypto Pilot wallet on Base whose x402 SDK lane reads this 402, or ''."""
    from core.wallet import custody, x402_sdk

    for row in custody.list_wallets():
        wallet_id = str(row.get("wallet_id") or "")
        if wallet_id and x402_sdk.claims_challenge(headers, None, wallet_id=wallet_id):
            return wallet_id
    return ""


def _offer_amount(headers: dict[str, Any], wallet_id: str) -> tuple[int, str]:
    from core.wallet import x402_sdk

    spec = x402_sdk.pilot_wallet_row(wallet_id)
    payment_required = x402_sdk._decode_challenge(headers)
    entry = x402_sdk._entry_for(payment_required, spec.network) if (spec is not None and payment_required is not None) else None
    if entry is None:
        return 0, ""
    asset = x402_sdk.eip3009_asset(spec.network, str(getattr(entry, "asset", "") or ""))
    try:
        amount = int(str(getattr(entry, "amount", "") or "0"))
    except ValueError:
        amount = 0
    return amount, (asset.symbol if asset is not None else "")


def _usd(minor: int) -> str:
    return f"{minor / 1_000_000:.6f}".rstrip("0").rstrip(".") + " USDC"


def ask(endpoint_name: Any, question: Any, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.wallet import channels, config, custody, outbound

    custody.require_enabled(source_context=source_context)
    text = str(question or "").strip()
    if not text:
        raise PaidAskError("question_missing", "There is no question to send.")
    if len(text) > MAX_QUESTION_CHARS:
        raise PaidAskError("question_too_long", f"The question is longer than {MAX_QUESTION_CHARS} characters.")
    with _LOCK:
        endpoint = _find_endpoint(endpoint_name)
    if not endpoint.get("on"):
        why = " It was paused because " + endpoint["paused_reason"] if endpoint.get("paused_reason") else ""
        raise PaidAskError("endpoint_paused", f"{endpoint['label']} is paused.{why} Turn it back on in Settings → Crypto → Pay-per-answer AI.")
    if not channels.is_on(channels.CHANNEL_X402_SDK):
        raise PaidAskError("channel_off", "Paying with USDC on Base is turned off. Turn it on in Settings → Crypto → Payment channels.")
    request_id = uuid.uuid4().hex
    # ``user`` is the OpenAI field for an end-user id: a fresh random one per question makes each paid request its own
    # (the wallet binds one payment per exact request body), and says nothing about the owner
    payload = {"model": endpoint["model"], "messages": [{"role": "user", "content": text}], "stream": False,
               "max_tokens": MAX_ANSWER_TOKENS, "user": "vool-" + request_id[:16]}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    url = endpoint["base_url"] + "/chat/completions"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    answer = outbound.fetch(url, method="POST", headers=headers, body=body, timeout=REQUEST_TIMEOUT_S, source_context=source_context)
    status = int(answer.get("status") or 0)
    if status != 402:
        if status >= 400:
            raise PaidAskError("endpoint_error", f"{endpoint['label']} could not answer right now (error {status}). Nothing was paid. Try again later.")
        return {"status": "answered_free", **_finish(endpoint, answer.get("body") or b"", session_id=_session_of(source_context), paid=None)}
    response_headers = dict(answer.get("headers") or {})
    wallet_id = _pilot_wallet_for(response_headers)
    if not wallet_id:
        raise PaidAskError("offer_not_payable", f"{endpoint['label']} asked for a payment VOOL cannot make. VOOL pays for answers only in USDC on "
                           "Base, from a wallet on Base that is set up and ready. Nothing was paid.")
    amount, asset = _offer_amount(response_headers, wallet_id)
    if asset != "USDC" or amount <= 0:
        raise PaidAskError("offer_not_payable", f"{endpoint['label']} asked for a payment in something other than USDC on Base. Nothing was paid.")
    limit = min(int(endpoint["max_price_minor"]), config.x402_cap_minor())
    if amount > limit:
        raise PaidAskError("offer_over_limit", f"{endpoint['label']} wants {_usd(amount)} for this answer, more than your limit of {_usd(limit)}. "
                           "Nothing was paid.")
    from core.wallet import x402_sdk

    outcome = x402_sdk.park_challenge(answer, url=url, method="POST", headers=headers, body=body, wallet_id=wallet_id, source_context=source_context)
    proposal_id = str(getattr(outcome, "proposal_id", "") or "")
    if not proposal_id:
        raise PaidAskError("offer_not_payable", f"{endpoint['label']}'s payment offer could not be prepared. Nothing was paid.")
    with _LOCK:
        state = _load()
        state["requests"][proposal_id] = {"endpoint_id": endpoint["id"], "model": endpoint["model"], "session_id": _session_of(source_context),
                                          "asked_at": time.time(), "amount_minor": amount, "body_sha256": hashlib.sha256(body).hexdigest(),
                                          "state": "pending_approval"}
        _save(state)
    return {"status": "pending_approval", "proposal_id": proposal_id, "amount_minor": amount, "endpoint": endpoint["label"], "model": endpoint["model"],
            "text": (f"{endpoint['label']} will answer with {endpoint['model']} for {_usd(amount)}. Nothing is paid yet. Approve the payment "
                     f"in your wallet, and the answer will appear here.\nPayment ID: {proposal_id}")}


def _session_of(source_context: dict[str, Any] | None) -> str:
    return str((source_context or {}).get("session_id") or "")


def _parse_completion(raw: bytes) -> tuple[str, Any, dict[str, Any]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "", None, {}
    if not isinstance(data, dict):
        return "", None, {}
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return (content if isinstance(content, str) else ""), data.get("model"), usage


def _finish(endpoint: dict[str, Any], raw: bytes, *, session_id: str, paid: dict[str, Any] | None) -> dict[str, Any]:
    content, reported, usage = _parse_completion(raw)
    verdict = model_verdict(endpoint["model"], reported)
    reported_text = str(reported)[:128] if isinstance(reported, str) else ""
    paid_line = f"Paid {_usd(int(paid['amount_minor']))}. " if paid else "No payment was needed. "
    paused_line = (f"We paused {endpoint['label']}, so nothing more is paid there. You can turn it back on in "
                   "Settings → Crypto → Pay-per-answer AI.")
    if verdict == VERDICT_MATCH:
        head = f"Answer from {endpoint['model']} through {endpoint['label']}. {paid_line}The service named the model you paid for."
    elif verdict == VERDICT_MISMATCH:
        head = (f"Heads up: {endpoint['label']} says {reported_text} answered, not {endpoint['model']}, the model you paid for. {paid_line}"
                f"{paused_line}")
    else:
        head = (f"{endpoint['label']} did not say which model answered, so VOOL cannot check it against {endpoint['model']}. {paid_line}"
                f"{paused_line}")
    if not content:
        body_text = "The service sent an answer VOOL could not read."
    else:
        body_text = content.strip()
    with _LOCK:
        state = _load()
        for entry in state["endpoints"]:
            if entry["id"] == endpoint["id"]:
                entry["last_verdict"] = verdict
                entry["answers"] = int(entry.get("answers") or 0) + 1
                if verdict != VERDICT_MATCH:
                    entry["on"] = False
                    entry["paused_reason"] = (f"it reported {reported_text} instead of {endpoint['model']}" if verdict == VERDICT_MISMATCH
                                              else "it did not say which model answered")
        if paid:
            record = state["requests"].get(paid["proposal_id"])
            if record is not None:
                record.update(state="answered", verdict=verdict, reported_model=reported_text,
                              usage={k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(usage.get(k), int)})
        _save(state)
    text = f"{head}\n{CHECK_LIMIT_NOTE}\n\n{body_text}"
    posted = False
    if session_id and paid:
        from core.persistent_memory import append_assistant_artifact_event

        posted = append_assistant_artifact_event(session_id=session_id, text=text, artifact_kind="paid_ai_answer",
                                                 artifact={"proposal_id": paid["proposal_id"], "endpoint": endpoint["label"],
                                                           "model_asked": endpoint["model"], "model_reported": reported_text, "verdict": verdict})
    return {"verdict": verdict, "model_asked": endpoint["model"], "model_reported": reported_text, "text": text, "posted": posted}


def after_approval(proposal_id: str, approval_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Called by the wallet's approve door after a Crypto Pilot approval. For a payment this module parked, take the
    answer the approval delivered (once) and post it, checked, into the chat that asked. Any other payment: None."""
    with _LOCK:
        state = _load()
        record = state["requests"].get(str(proposal_id or ""))
        if record is None or record.get("state") != "pending_approval":
            return None
        endpoint = next((e for e in state["endpoints"] if e["id"] == record.get("endpoint_id")), None)
    delivery = (approval_result or {}).get("x402_sdk") or {}
    from core.wallet import x402_sdk

    raw = x402_sdk.delivered_body(proposal_id) if delivery.get("delivered") else None
    if raw is None or endpoint is None:
        reason = "the service was removed from your list" if endpoint is None else "the service did not send one"
        with _LOCK:
            state = _load()
            if proposal_id in state["requests"]:
                state["requests"][proposal_id]["state"] = "paid_unanswered"
                _save(state)
        # whether money moved is the wallet's to say (its card and receipt), never guessed here
        note = f"No answer arrived: {reason}. Check your wallet to see whether this payment went through."
        session_id = str(record.get("session_id") or "")
        if session_id:
            from core.persistent_memory import append_assistant_artifact_event

            append_assistant_artifact_event(session_id=session_id, text=note, artifact_kind="paid_ai_answer",
                                            artifact={"proposal_id": proposal_id, "verdict": "unanswered"})
        return {"verdict": "unanswered", "text": note}
    return _finish(endpoint, raw, session_id=str(record.get("session_id") or ""),
                   paid={"proposal_id": proposal_id, "amount_minor": int(record.get("amount_minor") or 0)})


# --------------------------------------------------------------------------- the chat tool

def contracts(*, wallet_tools_enabled: bool) -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    return [
        RuntimeToolContract(
            intent=INTENT_ASK,
            description=("Ask a pay-per-answer AI endpoint the owner added in Settings (Crypto, Pay-per-answer AI) one question, paid "
                         "in USDC on Base with x402. Sends only the question, never the chat history. It parks one payment at or under "
                         "the owner's price limit for the owner to approve in the wallet; it never approves or pays. After approval the "
                         "answer is posted in this chat with a check of which model answered."),
            tool_surface=SURFACE,
            capability_id="wallet.propose",
            capability_claim="ask a paid AI endpoint and propose its payment for owner approval",
            supported=wallet_tools_enabled,
            unsupported_reason="The wallet is switched off on this runtime (VOOL_WALLET_ENABLED).",
            input_schema={"question": "str: exactly what to ask", "endpoint": "str (optional): the endpoint's name when there is more than one"},
            output_schema={"proposal_id": "str", "status": "str"},
            side_effect_class="creative_state",
            permission_actions=("use_network_access",),
            approval_requirement="none",
            timeout_policy="network_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
        ),
    ]


def run_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None = None):
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult
    from core.wallet.security import WalletFault

    args = dict(arguments or {})
    try:
        out = ask(args.get("endpoint"), args.get("question"), source_context=source_context)
    except PaidAskError as exc:
        observation = _tool_observation(intent=intent, tool_surface=SURFACE, ok=False, status=exc.code, message=exc.message)
        return RuntimeExecutionResult(handled=True, ok=False, status=exc.code, response_text=exc.message, details={"observation": observation})
    except WalletFault as exc:
        message = getattr(exc, "user_message", "") or str(exc)
        observation = _tool_observation(intent=intent, tool_surface=SURFACE, ok=False, status=getattr(exc, "code", "wallet_fault"), message=message)
        return RuntimeExecutionResult(handled=True, ok=False, status=str(getattr(exc, "code", "wallet_fault")), response_text=message + " Nothing was paid.",
                                      details={"observation": observation})
    observation = _tool_observation(intent=intent, tool_surface=SURFACE, ok=True, status=out["status"], proposal_id=out.get("proposal_id", ""))
    return RuntimeExecutionResult(handled=True, ok=True, status=out["status"], response_text=out["text"], details={**out, "observation": observation})
