"""
core/null_register_chat.py
==========================
In-chat ``.null`` registration — register a name without leaving the chat.

SECURITY MODEL — read this before touching anything here.
--------------------------------------------------------
A chat message NEVER directly authorizes a spend. Registration is three factors,
and the last one is the only one that actually moves SOL:

  1. INTENT.  An explicit imperative "register <name>.null" (not a question) runs
     a read-only ``preview_registration`` and stages a short-TTL pending offer.
     No gate, no signing, no spend — just a quoted cost and a note that an offer
     was shown.

  2. DETERMINISTIC CONFIRMATION.  A *strict* "yes" (a full-string regex match,
     evaluated against the DB-staged offer from step 1 — never an LLM tool-arg)
     builds a ``SpendGate`` from TRUSTED, in-process constants. The spend cap is
     a fixed default in this module; nothing the user typed sets it. This only
     *triggers an attempt*.

  3. UN-SPOOFABLE OS AUTHORIZATION.  ``execute_registration`` fires a live
     OS-consent prompt (Windows Hello / native credential dialog) that names the
     exact name, amount, and wallet. It is fail-closed: declined OR unavailable
     OR erroring all resolve to "refused, spent nothing". A chat message — even a
     prompt-injected or mistaken "yes" — cannot satisfy this. A physical human at
     the machine must approve the exact spend they see on screen.

Consequence: the chat "yes" does not, and cannot, flip the real gate. Windows
Hello is the real gate. If the OS prompt can't be shown (e.g. VOOL is running
headless/in the background), the flow refuses and points the user at the CLI,
which runs the identical gated path from an interactive session.

Everything here is deterministic and side-effect-light except the single call to
``execute_registration`` on an explicit confirmation, which is itself gated by the
three factors above.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from core.null_register_execute import SpendGate

# Trusted, in-process spend cap for the CHAT path. NOT settable from chat. Covers
# a normal registration (~0.01 SOL) with headroom, and stays well under the signer's
# own hard ceiling (MAX_REGISTER_LAMPORTS_CEILING = 0.05 SOL). A name that costs more
# than this is redirected to the CLI (where the user can raise --max-spend explicitly).
CHAT_REGISTER_CAP_LAMPORTS = 30_000_000  # 0.03 SOL
_LAMPORTS_PER_SOL = 1_000_000_000

# Explicit imperative register command: it must START with the verb (optionally after
# "please"/"go ahead and"), so questions like "can I register foo.null?" or "how do I
# register foo.null" fall through to the read-only grounding responder instead of
# offering a spend. The name must carry the explicit ``.null`` suffix.
_REGISTER_CMD_RE = re.compile(
    r"^\s*(?:please\s+)?(?:go\s+ahead\s+and\s+)?"
    r"(?:register|claim|mint|reserve|buy|grab)\s+"
    r"([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)\.null\b",
    re.IGNORECASE,
)

# Confirmation regexes are OWNED here (not imported from the hive module) so a change
# to hive confirmation phrasing can never silently loosen this money gate. Both are
# FULL-STRING anchored: only a clean, unambiguous confirmation fires. "yes but wait"
# does not match — ambiguity resolves to no spend.
_CONFIRM_YES = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|ok(?:ay)?|confirm(?:ed)?|do\s+it|go(?:\s+ahead)?|proceed|sure|y)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_CONFIRM_NO = re.compile(
    r"^\s*(?:no|nope|nah|cancel|stop|don'?t|abort|never\s*mind|nevermind|n)\s*[.!]*\s*$",
    re.IGNORECASE,
)

_EXPLORER_TX = "https://explorer.solana.com/tx/{sig}"


def _detect_register_command(text: str) -> str:
    """Return the fully-qualified ``name.null`` for an explicit register command, else ""."""
    match = _REGISTER_CMD_RE.match(str(text or ""))
    if not match:
        return ""
    return f"{match.group(1).lower()}.null"


def _is_strict_yes(text: str) -> bool:
    return bool(_CONFIRM_YES.match(str(text or "")))


def _is_negative(text: str) -> bool:
    return bool(_CONFIRM_NO.match(str(text or "")))


def _short_pubkey(pubkey: str) -> str:
    clean = str(pubkey or "")
    if len(clean) <= 10:
        return clean
    return f"{clean[:4]}…{clean[-4:]}"


def _sol(lamports: int) -> str:
    return f"{(max(0, int(lamports)) / _LAMPORTS_PER_SOL):.4f}"


def _response(text: str, *, intent: str, success: bool = True) -> dict[str, Any]:
    return {
        "response": text,
        "confidence": 1.0,
        "source": "null_register_chat",
        "deterministic": True,
        "intent": intent,
        "success": success,
    }


def _safe_wallet(wallet_fn: Callable[[], Any]) -> Any:
    try:
        return wallet_fn()
    except Exception:
        return None


# --- offer (phase 1: preview + stage, NEVER spends) ------------------------------

def _offer_text(name: str, *, total_lamports: int, rent_lamports: int, fee_lamports: int, pubkey: str) -> str:
    return (
        f"I can register `{name}` for you right now.\n"
        f"- Cost: ~{_sol(total_lamports)} SOL (rent {rent_lamports} + fee {fee_lamports} lamports), "
        f"paid from your wallet {_short_pubkey(pubkey)} on Solana **mainnet**.\n"
        f"- To actually spend, you'll get a **Windows Hello** prompt on THIS machine naming the exact "
        f"amount — I can't move any SOL unless you approve it there. A chat message alone never spends.\n"
        f"Reply **yes** to proceed or **no** to cancel. "
        f"(Or run `vool register {name} --allow-spend --mainnet` in a terminal.)"
    )


def _offer_registration(
    name: str,
    session_id: str,
    *,
    preview_fn: Callable[..., Any],
    wallet_fn: Callable[[], Any],
    stage_fn: Callable[..., None],
    clear_fn: Callable[[str], None],
) -> dict[str, Any] | None:
    wallet = _safe_wallet(wallet_fn)
    if wallet is None or not getattr(wallet, "pubkey", ""):
        # No usable wallet -> we can't quote a real live cost. Fall through so the
        # read-only grounding responder gives its CLI guidance instead of guessing.
        return None

    try:
        outcome = preview_fn(name, wallet.pubkey)
    except Exception:
        return None  # let the grounding responder handle it read-only

    status = getattr(outcome, "status", "")
    message = str(getattr(outcome, "message", "") or "")
    if status == "refused":
        # Already registered, or a premium/auction-only (1-3 char) name.
        clear_fn(session_id)
        return _response(
            f"I can't directly register `{name}`: {message}\n"
            f"(1-3 character names are premium and sold through the null-auction, not direct registration.)",
            intent="null_register_unavailable",
            success=False,
        )
    plan = getattr(outcome, "plan", None)
    if status != "preview" or plan is None:
        clear_fn(session_id)
        return _response(
            f"I couldn't read the live registration cost for `{name}` on-chain right now"
            f"{(': ' + message) if message else ''}. "
            f"Try again in a moment, or run `vool register {name} --mainnet` to preview it in a terminal.",
            intent="null_register_preview_error",
            success=False,
        )

    total_lamports = int(getattr(plan, "total_lamports", 0) or 0)
    if total_lamports <= 0 or total_lamports > CHAT_REGISTER_CAP_LAMPORTS:
        # Too expensive for the fixed in-chat cap (or a nonsense zero) -> send to the
        # CLI where the user can set --max-spend explicitly and see the full preview.
        clear_fn(session_id)
        return _response(
            f"`{name}` would cost ~{_sol(total_lamports)} SOL, which is above the in-chat "
            f"registration cap of {_sol(CHAT_REGISTER_CAP_LAMPORTS)} SOL. "
            f"Register it from a terminal where you can set the limit explicitly:\n"
            f"  vool register {name} --allow-spend --max-spend {_sol(total_lamports)} --mainnet",
            intent="null_register_over_cap",
            success=False,
        )

    stage_fn(
        session_id,
        name=name,
        cost_lamports=total_lamports,
        owner_pubkey=str(wallet.pubkey),
    )
    return _response(
        _offer_text(
            name,
            total_lamports=total_lamports,
            rent_lamports=int(getattr(plan, "rent_lamports", 0) or 0),
            fee_lamports=int(getattr(plan, "sol_fee_lamports", 0) or 0),
            pubkey=str(wallet.pubkey),
        ),
        intent="null_register_offer",
    )


# --- confirm (phase 2: execute, gated by trusted cap + mandatory OS consent) -----

def _map_execute_outcome(name: str, outcome: Any) -> dict[str, Any]:
    status = getattr(outcome, "status", "")
    message = str(getattr(outcome, "message", "") or "")
    signature = str(getattr(outcome, "signature", "") or "")

    if status == "submitted" and signature:
        return _response(
            f"Registered `{name}` ✅\n"
            f"- Signature: `{signature}`\n"
            f"- Explorer: {_EXPLORER_TX.format(sig=signature)}",
            intent="null_register_submitted",
        )
    if status == "blocked":
        # Your own spend policy stopped it (panic freeze or a daily/weekly cap). Retrying
        # in a terminal won't help — the policy applies there too; adjust the policy instead.
        return _response(
            f"Your wallet spend policy blocked this — **nothing was spent** ({message}).\n"
            f"Adjust it from a terminal: `vool spend-policy` to view, then "
            f"`vool spend-unfreeze` or `vool spend-set-cap ...` as needed.",
            intent="null_register_blocked",
            success=False,
        )
    if status == "refused":
        # OS consent declined/unavailable, name taken in the meantime, or no wallet.
        return _response(
            f"I did **not** spend anything — `{name}` was not registered ({message}).\n"
            f"If you didn't see a Windows Hello prompt (common when VOOL runs in the background), "
            f"complete it from a terminal, which shows a reliable prompt:\n"
            f"  vool register {name} --allow-spend --mainnet",
            intent="null_register_refused",
            success=False,
        )
    if status == "action_required":
        return _response(
            f"I held off — the spend gate didn't clear for `{name}` ({message}), so nothing was spent. "
            f"Register it from a terminal with an explicit limit:\n"
            f"  vool register {name} --allow-spend --mainnet",
            intent="null_register_action_required",
            success=False,
        )
    # error / unknown
    return _response(
        f"Registration of `{name}` didn't go through ({message or 'unknown error'}) and nothing was spent. "
        f"You can retry from a terminal:\n"
        f"  vool register {name} --allow-spend --mainnet",
        intent="null_register_error",
        success=False,
    )


def _execute_confirmed(
    pending: dict[str, Any],
    session_id: str,
    *,
    execute_fn: Callable[..., Any],
    wallet_fn: Callable[[], Any],
    clear_fn: Callable[[str], None],
) -> dict[str, Any]:
    name = str(pending.get("name") or "")
    # A confirmation is single-use: clear the pending row FIRST so a failed attempt
    # (or a stray repeat "yes") can never replay it. The user must re-issue the
    # command to try again.
    clear_fn(session_id)

    if not name:
        return _response(
            "That confirmation didn't map to a pending registration. Start with "
            "`register <name>.null` first.",
            intent="null_register_no_pending",
            success=False,
        )

    wallet = _safe_wallet(wallet_fn)
    if wallet is None or not getattr(wallet, "pubkey", ""):
        return _response(
            f"I can't reach your wallet to sign, so `{name}` was not registered and nothing was spent.",
            intent="null_register_no_wallet",
            success=False,
        )

    # Gate built from TRUSTED in-process constants — never from the chat text. We are
    # here only because of an explicit register command + a strict deterministic "yes"
    # against a freshly-staged offer. This gate alone still cannot spend: the mandatory
    # OS-consent prompt inside execute_registration is the real, un-spoofable gate.
    gate = SpendGate(
        allow_spend=True,
        approve=True,
        wallet_present=True,
        max_spend_lamports=CHAT_REGISTER_CAP_LAMPORTS,
    )
    try:
        outcome = execute_fn(name, gate=gate, wallet=wallet)
    except Exception as exc:
        return _response(
            f"Registration of `{name}` hit an error ({exc}) and nothing was spent. "
            f"Retry from a terminal: `vool register {name} --allow-spend --mainnet`.",
            intent="null_register_error",
            success=False,
        )
    return _map_execute_outcome(name, outcome)


# --- public entrypoint -----------------------------------------------------------

def maybe_handle_null_registration(
    user_text: str,
    session_id: str,
    *,
    preview_fn: Callable[..., Any] | None = None,
    execute_fn: Callable[..., Any] | None = None,
    wallet_fn: Callable[[], Any] | None = None,
    stage_fn: Callable[..., None] | None = None,
    load_fn: Callable[..., Any] | None = None,
    clear_fn: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Deterministic in-chat ``.null`` registration handler.

    Returns a grounded-style response dict, or None to let the normal pipeline
    (grounding responder / agent loop) handle the message. Dependencies are
    injectable for tests; defaults bind the real preview/execute/wallet/store.
    """
    text = str(user_text or "")
    sid = str(session_id or "").strip()
    from core.web0_project_grounding import looks_like_null_name_recall

    if looks_like_null_name_recall(text):
        return None

    # Bind real dependencies lazily so importing this module stays cheap and tests
    # can inject fakes without a live wallet, RPC, or OS-consent prompt.
    if preview_fn is None:
        from core.null_register_execute import preview_registration as preview_fn
    if execute_fn is None:
        from core.null_register_execute import execute_registration as execute_fn
    if wallet_fn is None:
        from core.vool_wallet import get_or_create_wallet as wallet_fn
    if stage_fn is None:
        from storage.null_register_pending_store import stage_pending_registration as stage_fn
    if load_fn is None:
        from storage.null_register_pending_store import load_pending_registration as load_fn
    if clear_fn is None:
        from storage.null_register_pending_store import clear_pending_registration as clear_fn

    # (1) An explicit imperative "register foo.null" always (re)stages a fresh offer,
    #     superseding any older pending one.
    name = _detect_register_command(text)
    if name:
        return _offer_registration(
            name, sid, preview_fn=preview_fn, wallet_fn=wallet_fn, stage_fn=stage_fn, clear_fn=clear_fn
        )

    # (2) Otherwise, only a bare yes/no can act on a pending offer. Anything else is
    #     never handled here, so skip the store entirely for normal chat — this keeps
    #     the hot path free of a per-message DB read and shrinks the acting surface.
    if not sid:
        return None
    is_yes = _is_strict_yes(text)
    is_no = _is_negative(text)
    if not (is_yes or is_no):
        return None
    pending = load_fn(sid)
    if pending is None:
        return None
    if is_no:
        clear_fn(sid)
        return _response(
            f"Cancelled — I won't register `{pending.get('name') or 'that name'}`. Nothing was spent.",
            intent="null_register_cancelled",
        )
    # is_yes
    return _execute_confirmed(
        pending, sid, execute_fn=execute_fn, wallet_fn=wallet_fn, clear_fn=clear_fn
    )


__all__ = [
    "CHAT_REGISTER_CAP_LAMPORTS",
    "maybe_handle_null_registration",
]
