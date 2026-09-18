from __future__ import annotations

import re
from typing import Any

from core.canonical_project_knowledge import (
    canonical_context_text,
    canonical_query_text,
)

NULL_REGISTRAR_V2_PROGRAM = "NXgQhepFpDCu935H1D4g34g59ZYbo1jR4tBCZWhV8Np"
_NULL_NAME_RE = re.compile(r"\b([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)\.null\b", re.IGNORECASE)

_WEB0_NULL_MARKERS = (
    ".null",
    "dot null",
    "web0",
    "null registrar",
    "null_registrar",
    "nulldomain",
    "nullpay",
    "null://",
)

_BUY_REGISTER_MARKERS = (
    "auction",
    "available",
    "availability",
    "bid",
    "buy",
    "claim",
    "get",
    "mint",
    "purchase",
    "register",
    "registration",
    "reserve",
    "resolve",
)

# Registration-specific words (used to catch a follow-up like "where do I send the
# registration fee" / "ok grab it and register" that no longer carries a .null token).
_REGISTRATION_MARKERS = (
    "register",
    "registration",
    "claim",
    "mint",
    "reserve",
)

# Fee / payment phrasing, and "you do it" execution phrasing.
_FEE_MARKERS = (
    "fee",
    "cost",
    "price",
    "how much",
    "send the",
    "where to send",
    "where do i send",
    "pay for",
    "pay the",
)
_EXEC_MARKERS = (
    "do it for me",
    "you do it",
    "you will do it",
    "you'll do it",
    "go ahead",
    "grab it",
    "buy it",
    "get it for me",
    "handle it",
)

WEB0_NULL_REGISTRATION_RESPONSE = (
    f"- Yes, but not as a normal ICANN/DNS purchase. In this stack, `.null` is a wallet-owned Web0 name on Solana `null_registrar v2` (`{NULL_REGISTRAR_V2_PROGRAM}`).\n"
    "- It costs a small amount of SOL — the on-chain registration fee plus the name account's rent (roughly ~0.01 SOL all-in right now; the exact amount is read live from the registrar config before you register). 1-3 character names are premium and are sold through the null-auction, not direct registration.\n"
    "- Register with `vool register <name>.null` — it previews the exact live cost and asks for explicit approval and a wallet prompt (Windows Hello) before spending; `vool resolve <name>.null` checks a name read-only. Any dial/x402 payment stays opt-in and needs explicit spend approval."
)


def _extract_null_name(text: str) -> str:
    match = _NULL_NAME_RE.search(str(text or ""))
    if not match:
        return ""
    return f"{match.group(1).lower()}.null"


def _looks_like_named_registration_request(text: str, name: str) -> bool:
    if not name:
        return False
    clean = " ".join(prose_only(str(text or "")).lower().split())
    return any(
        marker in clean
        for marker in (
            "buy",
            "claim",
            "get",
            "help me",
            "i want",
            "mint",
            "register",
            "registration",
            "reserve",
        )
    )


def _looks_like_registration_fee_or_exec(text: str) -> bool:
    """Catch a registration follow-up whose .null token was in a previous turn.

    Fires only when the message combines a registration word with either fee/payment
    phrasing or an "you do it" execution phrase, e.g. "ok lets grab it, tell me where to
    send the registration fee and you will do it for me". Without this, that turn carries
    no web0/.null marker, so the grounding responder is skipped and the tool loop returns
    the "I couldn't map that cleanly to a real action" dead-end.
    """
    clean = " ".join(prose_only(str(text or "")).lower().split())
    if not clean:
        return False
    if not _REGISTRATION_FOLLOWUP_RE.search(clean):
        return False
    return bool(_FEE_MARKER_RE.search(clean) or _EXEC_MARKER_RE.search(clean))


def _named_registration_response(name: str) -> str:
    return (
        f"- I can help you register `{name}`, but I will not sign, spend, or submit a wallet transaction automatically.\n"
        f"- First run `vool resolve {name}`. If it resolves, it is already owned; if it returns no record, the name is available. Then `vool register {name}` previews the exact live cost and, only with your explicit approval and a wallet prompt (Windows Hello), registers it against `null_registrar v2` (`{NULL_REGISTRAR_V2_PROGRAM}`).\n"
        "- It isn't free: registration costs a small amount of SOL (the on-chain fee + the name account's rent, ~0.01 SOL all-in right now), read live before you commit. 1-3 character names are premium (auction-only)."
    )


def _registration_fee_or_exec_response(name: str) -> str:
    resolve_target = name if name else "<name>.null"
    name_phrase = f"`{name}` " if name else ""
    return (
        f"- Registering a {name_phrase}`.null` name isn't free — it costs a small amount of SOL: the on-chain fee plus the name account's rent (~0.01 SOL all-in right now), paid from your own wallet and read live before you commit.\n"
        "- 1-3 character names are premium and are sold through the null-auction (floor-priced), not direct registration.\n"
        f"- I can register it with `vool register {resolve_target}` — it previews the exact cost first and asks for explicit approval and a wallet prompt (Windows Hello) before spending; I never sign automatically."
    )


# Both marker lists are matched as WHOLE TOKENS over PROSE, and each half of that mattered.
#
# Found by a QA build drive on 2026-07-30. "hey, could you knock together
# /Users/<me>/.vool_runtime/workspace/r2/roman.py? cheers" was answered, in 1s, with the `.null`
# name-registration blurb -- ICANN, SOL, the registrar program id -- for a request to write a file.
# Two independent bare-substring collisions, and it needed both:
#
#   ".null" matched inside the FILESYSTEM PATH ".vool_runtime"
#   "get"   matched inside the ordinary English word "to-get-her"
#
# `prose_only` already existed for exactly the first one and this matcher was not using it. The
# second is why word boundaries are here too: a path-stripped "knock together" still carries "get".
def _bounded_marker(marker: str) -> str:
    """One marker, guarded only on the edges where a guard means something.

    ``.null`` must NOT carry a leading guard -- it is how a name is written (`alice.null`), and
    demanding a non-word character before the dot loses every real question. It must carry a
    TRAILING one, because that is the edge `.vool_runtime` crosses.
    """

    body = re.escape(marker).replace(r"\ ", r"[\s_]")
    lead = r"(?<![\w-])" if marker[:1].isalnum() else ""
    tail = r"(?![\w-])" if marker[-1:].isalnum() else ""
    return lead + body + tail


_WEB0_NULL_MARKER_RE = re.compile(
    "|".join(_bounded_marker(m) for m in _WEB0_NULL_MARKERS), re.IGNORECASE
)
# The follow-up route obeys the same prose/token boundary as an explicit .null ask.
# Substrings in "preserve", "reclaimed", "coffee", paths or identifiers are not
# registration/payment intent. Keep ordinary verb inflections, without arbitrary suffixes.
_REGISTRATION_FOLLOWUP_RE = re.compile(
    r"(?<![\w-])(?:register(?:s|ed|ing)?|registrations?|claim(?:s|ed|ing)?|"
    r"mint(?:s|ed|ing)?|reserv(?:e[sd]?|ing))(?![\w-])", re.IGNORECASE,
)
_FEE_MARKER_RE = re.compile("|".join(_bounded_marker(m) for m in _FEE_MARKERS), re.IGNORECASE)
_EXEC_MARKER_RE = re.compile("|".join(_bounded_marker(m) for m in _EXEC_MARKERS), re.IGNORECASE)
# Word-initial, but open at the end: "registering", "buying" and "resolved" are the same ask, while
# "together" is not "get" and never was. A trailing "e" is dropped so the stem survives inflection.
_BUY_REGISTER_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m[:-1] if m.endswith("e") else m) for m in _BUY_REGISTER_MARKERS) + r")\w*",
    re.IGNORECASE,
)


def looks_like_web0_null_registration_question(text: str) -> bool:
    # A path is not prose. The name of a folder on this disk is not the operator asking to buy a name.
    clean = " ".join(prose_only(str(text or "")).lower().split())
    if not clean:
        return False
    if not _WEB0_NULL_MARKER_RE.search(clean):
        return False
    return bool(_BUY_REGISTER_MARKER_RE.search(clean))


_READING_MARKERS = (
    "read this passage",
    "read the passage",
    "read this and answer",
    "read the following",
    "passage:",
    "the passage",
    "answer the question that follows",
    "based on the passage",
    "according to the passage",
)


def _looks_like_reading_comprehension(text: str) -> bool:
    """A passage-plus-question turn is answered by reading it, never with a canned .null blurb."""
    low = " ".join(str(text or "").lower().split())
    if not low:
        return False
    if any(marker in low for marker in _READING_MARKERS):
        return True
    # A long quoted block is a passage to read, not a project question.
    for quote in ('"', "“", "”"):
        first, last = low.find(quote), low.rfind(quote)
        if 0 <= first < last and (last - first) > 120:
            return True
    return False


# Definition-style phrasing is used only to identify questions about deterministic
# runtime controls and safety behavior. Canonical project definitions are retrieved
# from repository documentation and composed by the selected model.
_DEFINE_LEAD = re.compile(
    r"\b(?:what(?:'s| is| are)|whats|who(?:'s| is| are| makes| made| built| created| owns)|"
    r"explain|define|describe|tell me about|what does|what did (?:we|you) say\b|"
    r"how (?:do|does|is|are))\b",
    re.IGNORECASE,
)

def prose_only(text: str) -> str:
    """Return prose with filesystem paths removed before topic matching."""
    return canonical_query_text(text)


def web0_boot_context(text: str) -> str:
    """Return source-backed canonical context for supported project terms."""
    return canonical_context_text(prose_only(text))


def _ecosystem_definition_response(
    text: str,
) -> dict[str, Any] | None:
    """Compatibility seam: project definitions are model-composed from sources."""
    del text
    return None


# An active-mission / recall / active-constraints query may NAME a .null domain as one of its
# fields (e.g. "Active mission: cap 0.05 SOL, domain alice.null, ... what is the current mission?").
# That intent must win over the .null registration/grounding responder, so this responder defers.
_ACTIVE_MISSION_QUERY_RE = re.compile(
    r"(?:"
    r"\b(?:current|active|remembered|the)\s+mission\b|"
    r"\b(?:current|active)\s+constraints\b|"
    r"\bspend[\s-]*cap\b|\bwallet\s+prefix\b|\bnever\s+auto[\s-]*spend\b|\bwindows\s+only\b|"
    r"\bsummari[sz]e\s+(?:the\s+|my\s+|current\s+)?mission\b|\brecall\s+(?:the\s+|my\s+)?mission\b|"
    r"\bwhat\s+did\s+i\s+(?:say|tell)\b|\bwhat\s+do\s+you\s+remember\b"
    r")",
    re.IGNORECASE,
)


def looks_like_active_mission_query(text: str) -> bool:
    """True when the message is asking to recall / state the active mission or its constraints
    (spend cap, wallet prefix, target OS, ...), which must not be hijacked by the .null responder."""
    return bool(_ACTIVE_MISSION_QUERY_RE.search(str(text or "")))


_NULL_NAME_RECALL_RE = re.compile(
    r"\bwhat\s+(?:project\s+)?(?:domain|name)\s+(?:did|do)\s+i\s+"
    r"(?:ask\s+for|ask\s+you\s+for|say|choose|request)\b",
    re.IGNORECASE,
)


def looks_like_null_name_recall(text: str) -> bool:
    return bool(_extract_null_name(text) and _NULL_NAME_RECALL_RE.search(str(text or "")))


def _null_name_recall_response(text: str) -> dict[str, Any] | None:
    if not looks_like_null_name_recall(text):
        return None
    name = _extract_null_name(text)
    return {
        "response": f"The domain you asked for is `{name}`.",
        "confidence": 1.0,
        "source": "web0_project_grounding",
        "deterministic": True,
        "intent": "web0_null_name_recall",
        "null_name": name,
    }


# Deterministic answers for VOOL's own safety controls and runtime truth. The model demonstrably
# contradicts these (it answered "/stopx402" with a generic x402 definition, and claimed live
# settlement "can be self-claimed"), so answer them from source rather than the model's priors. The
# `?`/definitional gate keeps a bare mention from triggering a canned control blurb, and the actual
# `/stopx402` freeze command is matched+executed earlier (anchored) in the brake frontdoor.
_STOPX402_QUERY_RE = re.compile(r"/stopx402|\bstop\s*x402\b", re.IGNORECASE)
_CLOUD_POLICY_QUERY_RE = re.compile(
    r"\bcloud\s+(?:burst|lane|policy|escalation|default|mode)\b|\bburst\s+(?:policy|to\s+cloud|mode)\b|\bbyok\b",
    re.IGNORECASE,
)
_SELF_CLAIM_QUERY_RE = re.compile(r"\bself[\s-]?claim", re.IGNORECASE)

_STOPX402_CONTROL_ANSWER = (
    "`/stopx402` instantly freezes the wallet's `.null`-registration lane, so no `.null` registration "
    "(a SOL spend) can go through until you `/startx402`. Separately, the USDC x402 spend lane is "
    "disabled in this build. The assistant keeps running throughout."
)
_CLOUD_POLICY_CONTROL_ANSWER = (
    "The cloud burst lane defaults to OFF and is fail-closed. Modes are `cloud off | ask | auto`; it "
    "spends your own cloud key (paid straight to the provider, no money through VOOL), capped rather "
    "than per-call consented. `ask` stays local until you set `auto`, and a saved key never authorizes a "
    "burst on its own."
)
_SELF_CLAIM_CONTROL_ANSWER = (
    "No — settlement cannot be self-claimed. Settlement is simulated today (stub/devnet), and a local "
    "on-chain receipt verifier gates real settlement so reputation cannot be self-claimed."
)


# Net-new grounding/honesty answers from KAS's beginner-testing audit (2026-07-11): the 7B model
# drifts these into generic crypto / generic software / generic AI-agent framing, and overclaims on
# two safety-relevant points (x402 auto-charging, memory permanence). Answer them from source.
_X402_AUTOCHARGE_QUERY_RE = re.compile(
    r"\bx402\b[^.\n]{0,40}?\b(?:auto|automatic|automatically|subscription|recurring|charge|charged|bill|billed)\b"
    r"|\b(?:auto[- ]?charge|automatic|automatically|subscription|recurring)\b[^.\n]{0,40}?\bx402\b",
    re.IGNORECASE,
)
_MEMORY_TRUST_QUERY_RE = re.compile(
    r"\bremember everything\b|\bnever forget\b|\bremembers? all\b|\bstore everything\b|"
    r"\bmemory\b[^.\n]{0,30}?\b(?:secure|securely|safe|encrypt)|\b(?:secure|securely|safely)\b[^.\n]{0,20}?\bremember\b",
    re.IGNORECASE,
)
_AGENT_LOOP_QUERY_RE = re.compile(
    r"\bagent(?:ic)?\s+loop\b|\btool[\s-]?(?:use\s+)?loop\b|\breasoning\s+loop\b|"
    r"\bhow\s+(?:does|do)\s+(?:the\s+|your\s+|vool'?s?\s+)?agent\s+(?:work|loop|run|iterate)\b",
    re.IGNORECASE,
)
_PROOF_EXEC_QUERY_RE = re.compile(
    r"\bproof[\s-]of[\s-]execution\b|\bexecution\s+proof\b|\bproof\s+that\s+(?:a\s+)?(?:tool|action|command|it)\s+(?:ran|executed)\b",
    re.IGNORECASE,
)
_WALLET_NATURE_QUERY_RE = re.compile(
    r"\bwhat\s+(?:kind|type|sort)\s+of\s+wallet\b|\bhow\s+(?:does|do)\s+(?:the\s+|your\s+|vool'?s?\s+)?wallet\s+work\b|"
    r"\bexplain\s+(?:the\s+|your\s+|vool'?s?\s+)?wallet\b|\bwhat\s+wallet\s+(?:does|do)\s+(?:vool|you|it)\s+use\b|"
    r"\btell me about\s+(?:the\s+|your\s+)?wallet\b|\bis\s+(?:the\s+|your\s+)?wallet\s+(?:a\s+)?(?:bitcoin|ethereum|multi)",
    re.IGNORECASE,
)

_X402_AUTOCHARGE_ANSWER = (
    "x402 does NOT auto-charge you. Every x402 payment is fail-closed: it needs your explicit per-call "
    "approval plus a spend cap before anything moves, and the USDC x402 spend lane is disabled in this "
    "build. There is no configurable automatic, recurring, or subscription charging — a saved key or a "
    "prior approval never authorizes a future charge on its own."
)
# "Who approves an x402 payment?" / "can x402 spend without my consent?" drifted into corporate
# approver / company-policy framing (flagged on current main). YOU are the approver; it's fail-closed.
_X402_CONSENT_QUERY_RE = re.compile(
    r"\bx402\b[^.\n]{0,50}?\b(?:consent|approv|authoriz|permission|without\s+my|need\s+my)\b"
    r"|\bwho\s+(?:approv|authoriz)[^.\n]{0,30}?\bx402\b"
    r"|\bx402\b[^.\n]{0,50}?\bspend\b[^.\n]{0,25}?\b(?:without|no\b)",
    re.IGNORECASE,
)
_X402_CONSENT_ANSWER = (
    "You approve every x402 payment — there is no organizational approver and no company policy to look "
    "up. It is fail-closed: nothing spends without your explicit per-call consent plus a spend cap, the "
    "USDC x402 spend lane is disabled in this build, and VOOL cannot spend on its own."
)
_MEMORY_TRUST_ANSWER = (
    "Be realistic about memory: VOOL's is best-effort, not perfect — it can miss or drift on recall, so "
    "it does not 'remember everything.' What it stores is local to your machine, and at rest it is NOT "
    "encrypted by default (opt-in passphrase / OS-keyring modes exist). Don't rely on it to hold secrets."
)
_AGENT_LOOP_ANSWER = (
    "VOOL's agent loop is: the local LLM plans -> calls a tool -> the runtime executes it -> the result "
    "is fed back -> the model iterates until the task is done or hits a gate (e.g. a spend/consent stop). "
    "It is not an 'AI stuck in a loop', and not a generic scheduled or periodic background task."
)
_PROOF_EXEC_ANSWER = (
    "Proof-of-execution in VOOL is a signed, offline-verifiable receipt that a specific tool or action "
    "actually ran (Ed25519), recorded on the append-only task/proof spine. It is local runtime provenance "
    "for what VOOL did — not a generic blockchain or zero-knowledge proof."
)
_WALLET_NATURE_ANSWER = (
    "VOOL uses a single local Solana wallet (an Ed25519 keypair) for `.null` registration — not a generic "
    "multi-chain or Bitcoin/Ethereum wallet. The model can't move money on its own: every SOL spend is "
    "gated by an OS consent prompt, and the USDC x402 spend lane is disabled in this build."
)
# "What might go outside the local machine?" drifted into "logs sent to a secure server managed by the
# provider" (false — flagged on current main). Nothing leaves by default; only explicit web fetch and
# the opt-in cloud burst do, on your action.
_LOCALITY_QUERY_RE = re.compile(
    r"\b(?:leave|leaves|leaving|go|goes|going|sent|send|sends|transmit|transmitted|upload|uploaded|"
    r"stored?|shared?)\b[^.\n]{0,40}?"
    r"\b(?:machine|device|computer|local|network|server|cloud|remote|online|internet)\b"
    r"|\b(?:outside|off)\s+(?:the|my|your)\s+(?:local\s+)?(?:machine|device|computer)\b"
    r"|\b(?:telemetry|phones?\s+home|send[s]?\s+(?:my\s+)?(?:data|logs|activity))\b",
    re.IGNORECASE,
)
_LOCALITY_ANSWER = (
    "By default nothing leaves your machine — VOOL runs locally and does not send your data, logs, or "
    "telemetry to a Parad0x or provider server. Only two things go out, and only on your action: web "
    "fetch/search/browser when you ask for live info, and the opt-in cloud burst (off by default) that "
    "calls your own cloud model with your own key. There is no background server collecting your activity."
)


def _vool_control_response(text: str) -> dict[str, Any] | None:
    low = " ".join(str(text or "").lower().split())
    if not low:
        return None
    is_question = (
        bool(_DEFINE_LEAD.search(low))
        or "?" in str(text or "")
        or "do not execute" in low
        or bool(re.match(r"(?:does|do|can|could|will|would|is|are|should|has|have)\b", low))
    )
    if not is_question:
        return None

    def _pack(answer: str, intent: str) -> dict[str, Any]:
        return {
            "response": answer,
            "confidence": 1.0,
            "source": "web0_project_grounding",
            "deterministic": True,
            "intent": intent,
        }

    if _STOPX402_QUERY_RE.search(low):
        return _pack(_STOPX402_CONTROL_ANSWER, "vool_stopx402_control")
    if _SELF_CLAIM_QUERY_RE.search(low):
        return _pack(_SELF_CLAIM_CONTROL_ANSWER, "vool_settlement_self_claim")
    if _X402_AUTOCHARGE_QUERY_RE.search(low):
        return _pack(_X402_AUTOCHARGE_ANSWER, "vool_x402_autocharge")
    if _X402_CONSENT_QUERY_RE.search(low):
        return _pack(_X402_CONSENT_ANSWER, "vool_x402_consent")
    if _CLOUD_POLICY_QUERY_RE.search(low):
        return _pack(_CLOUD_POLICY_CONTROL_ANSWER, "vool_cloud_burst_policy")
    if _MEMORY_TRUST_QUERY_RE.search(low):
        return _pack(_MEMORY_TRUST_ANSWER, "vool_memory_honesty")
    if _AGENT_LOOP_QUERY_RE.search(low):
        return _pack(_AGENT_LOOP_ANSWER, "vool_agent_loop")
    if _PROOF_EXEC_QUERY_RE.search(low):
        return _pack(_PROOF_EXEC_ANSWER, "vool_proof_of_execution")
    if _WALLET_NATURE_QUERY_RE.search(low):
        return _pack(_WALLET_NATURE_ANSWER, "vool_wallet_nature")
    if _LOCALITY_QUERY_RE.search(low):
        return _pack(_LOCALITY_ANSWER, "vool_data_locality")
    return None


def web0_null_project_response(text: str) -> dict[str, Any] | None:
    if _looks_like_reading_comprehension(text):
        return None
    # A mission-recall / active-constraints query outranks the .null responder even when it names a
    # .null domain as one of the mission fields — answer it as mission recall, not registration.
    if looks_like_active_mission_query(text):
        return None
    recalled_name = _null_name_recall_response(text)
    if recalled_name is not None:
        return recalled_name
    null_name = _extract_null_name(text)
    if looks_like_web0_null_registration_question(text):
        if _looks_like_named_registration_request(text, null_name):
            return {
                "response": _named_registration_response(null_name),
                "confidence": 1.0,
                "source": "web0_project_grounding",
                "deterministic": True,
                "intent": "web0_null_named_registration_workflow",
                "null_name": null_name,
            }
        return {
            "response": WEB0_NULL_REGISTRATION_RESPONSE,
            "confidence": 1.0,
            "source": "web0_project_grounding",
            "deterministic": True,
        }
    # Registration follow-up ("where's the fee" / "grab it and do it") whose .null token
    # lived in an earlier turn: answer it correctly instead of dead-ending in the tool loop.
    if _looks_like_registration_fee_or_exec(text):
        return {
            "response": _registration_fee_or_exec_response(null_name),
            "confidence": 1.0,
            "source": "web0_project_grounding",
            "deterministic": True,
            "intent": "web0_null_registration_fee_followup",
            "null_name": null_name,
        }
    # VOOL's own controls/runtime truth (/stopx402, cloud default, self-claim) — answered before the
    # generic ecosystem definition, so "what does /stopx402 do" is not swallowed by the "x402" match.
    control = _vool_control_response(text)
    if control is not None:
        return control
    # Project definitions are grounded with allowlisted repository passages in
    # bootstrap context, then worded by the selected model.
    return None
