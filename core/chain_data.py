"""Read-only blockchain lookups for any chat, through the operator's own chain-data keys.

One model-facing tool, ``web.chain_lookup``: a wallet's native balance, its token balances, its recent
transactions, a token's facts, a token's largest holders, or one transaction, on Solana, Ethereum,
Base, BNB Smart Chain or Robinhood Chain, served by Helius, Alchemy, Solscan or Etherscan
(``core/chain_providers.py``).

The tool is always offered, so a chain question is never answered from a model's memory:

* No key for any provider that serves the question: the answer says which keys would, and that
  nothing was looked up. Nothing goes online.
* A key is stored: exactly one provider is asked (the one named, else the first stored one in table
  order that serves the chain and the read). The key rides the placement that provider documents,
  through the one outbound door (``core.remote_fetch_policy``), so Local Only, the per-turn veto and
  the effect ledger govern it like every other network tool. A key carried in the URL is registered as
  an exact secret first and the request never follows a redirect.
* Every failure is named and fails closed: a rejected key, a plan or chain the key does not cover, a
  rate limit, a timeout, an outage, an unreachable provider, an answer in an unknown shape. No number
  is ever produced from a failed or partial answer, and no other provider is tried behind the
  operator's back (the answer names the other stored providers to try instead).

The tool's text is the answer (``renders_final_answer``): balances, amounts, addresses and
transaction ids are never paraphrased by a model. Nothing here signs, sends or spends; the wallet and
its payment endpoints are untouched.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from core import chain_providers as cp

INTENT = "web.chain_lookup"
SURFACE = "chain"
REQUEST_TIMEOUT_S = 15.0
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_LIMIT = 10
MAX_LIMIT = 25

_SOLANA_TOKEN_PROGRAMS = (
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
)

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SVM_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SVM_TX_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,90}$")
_EVM_TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

_OP_WORDS = {
    "balance": cp.OP_BALANCE, "native_balance": cp.OP_BALANCE,
    "tokens": cp.OP_TOKENS, "token_balances": cp.OP_TOKENS, "holdings": cp.OP_TOKENS, "portfolio": cp.OP_TOKENS,
    "history": cp.OP_HISTORY, "transactions": cp.OP_HISTORY, "activity": cp.OP_HISTORY, "transfers": cp.OP_HISTORY,
    "token_info": cp.OP_TOKEN_INFO, "token": cp.OP_TOKEN_INFO, "token_meta": cp.OP_TOKEN_INFO, "metadata": cp.OP_TOKEN_INFO,
    "holders": cp.OP_HOLDERS, "top_holders": cp.OP_HOLDERS, "token_holders": cp.OP_HOLDERS,
    "transaction": cp.OP_TRANSACTION, "tx": cp.OP_TRANSACTION, "tx_lookup": cp.OP_TRANSACTION,
}

_OP_PHRASES = {
    cp.OP_BALANCE: "native balances", cp.OP_TOKENS: "token balances", cp.OP_HISTORY: "transaction history",
    cp.OP_TOKEN_INFO: "token info", cp.OP_HOLDERS: "token holders", cp.OP_TRANSACTION: "transaction lookups",
}


class ChainDataError(Exception):
    """A lookup that produced no trustworthy answer. ``message`` is safe to show: no key, no body."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- the tool contract

def chain_contracts() -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    return [
        RuntimeToolContract(
            intent=INTENT,
            description=(
                "Look up public blockchain data with the operator's own chain-data API keys (Helius, Alchemy, Solscan, "
                "Etherscan): a wallet's native balance, its token balances, its recent transactions, a token's info, a "
                "token's largest holders, or one transaction, on Solana, Ethereum, Base, BNB Smart Chain or Robinhood "
                "Chain. Read-only: nothing is signed or sent. Use it for any question about an on-chain address, token "
                "or transaction instead of answering from memory; without a key it says which key to add."
            ),
            tool_surface=SURFACE,
            capability_id="web.chain_lookup",
            capability_claim="read public blockchain data through the operator's chain-data keys",
            supported=True,
            unsupported_reason="",
            input_schema={
                "query": "str: balance | tokens | history | token_info | holders | transaction",
                "chain": "str: solana | ethereum | base | bnb | robinhood (optional for a Solana address)",
                "address": "str: wallet address (balance, tokens, history)",
                "token": "str: token mint or contract address (token_info, holders)",
                "tx": "str: transaction signature or hash (transaction)",
                "provider": "str (optional): helius | alchemy | solscan | etherscan",
                "limit": f"int (optional, 1-{MAX_LIMIT}): how many rows for lists",
            },
            output_schema={"provider": "str", "chain": "str", "rows": "list"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="network_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("read_files", "use_network_access"),
            renders_final_answer=True,
        ),
    ]


def run_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None = None):
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult

    def result(ok: bool, status: str, text: str, **details: Any):
        observation = _tool_observation(intent=INTENT, tool_surface=SURFACE, ok=ok, status=status,
                                        provider=str(details.get("provider") or ""), chain=str(details.get("chain") or ""))
        return RuntimeExecutionResult(handled=True, ok=ok, status=status, response_text=text,
                                      details={**details, "observation": observation})

    if intent != INTENT:
        return result(False, "unsupported_intent", "That is not a chain lookup.")
    try:
        answer = lookup(arguments or {})
    except ChainDataError as exc:
        return result(False, exc.code, exc.message)
    return result(True, "ok", answer["text"], provider=answer["provider"], chain=answer["chain"], rows=answer["rows"])


# --------------------------------------------------------------------------- argument handling

def _op_of(args: dict[str, Any]) -> str:
    raw = str(args.get("query") or args.get("op") or args.get("operation") or "").strip().lower().replace(" ", "_").replace("-", "_")
    op = _OP_WORDS.get(raw, "")
    if op:
        return op
    # A request that names only the thing it is about still names the read.
    if args.get("tx"):
        return cp.OP_TRANSACTION
    if raw:
        raise ChainDataError("unknown_query", f"I can look up balance, tokens, history, token_info, holders or transaction; {raw!r} is none of those. Nothing was looked up.")
    raise ChainDataError("missing_query", "Say what to look up: balance, tokens, history, token_info, holders or transaction. Nothing was looked up.")


def _chain_of(args: dict[str, Any], subject: str) -> str:
    named = str(args.get("chain") or args.get("network") or "").strip()
    if named:
        chain = cp.resolve_chain(named)
        if not chain:
            raise ChainDataError("unknown_chain", f"VOOL's chain lookups cover Solana, Ethereum, Base, BNB Smart Chain and Robinhood Chain; {named[:40]!r} is not one of them. Nothing was looked up.")
        return chain
    if subject and (_SVM_ADDRESS_RE.fullmatch(subject) or _SVM_TX_RE.fullmatch(subject)) and not subject.startswith("0x"):
        return cp.CHAIN_SOLANA
    if subject.startswith("0x") and (_EVM_ADDRESS_RE.fullmatch(subject) or _EVM_TX_RE.fullmatch(subject)):
        raise ChainDataError("chain_needed", "That address or hash could be on Ethereum, Base, BNB Smart Chain or Robinhood Chain. Which chain is it on? Nothing was looked up.")
    raise ChainDataError("bad_subject", "That is not a Solana or EVM address, token or transaction id. Check it and ask again. Nothing was looked up.")


def _subject_of(op: str, args: dict[str, Any]) -> str:
    if op == cp.OP_TRANSACTION:
        value = args.get("tx") or args.get("signature") or args.get("hash") or ""
        what = "transaction signature or hash"
    elif op in (cp.OP_TOKEN_INFO, cp.OP_HOLDERS):
        value = args.get("token") or args.get("mint") or args.get("contract") or args.get("address") or ""
        what = "token mint or contract address"
    else:
        value = args.get("address") or args.get("wallet") or ""
        what = "wallet address"
    text = str(value or "").strip()
    if not text:
        raise ChainDataError("missing_subject", f"I need the {what} to look up. Nothing was looked up.")
    return text


def _validate_subject(op: str, chain: str, subject: str) -> str:
    evm = chain in cp.EVM_CHAINS
    if op == cp.OP_TRANSACTION:
        ok = bool(_EVM_TX_RE.fullmatch(subject)) if evm else bool(_SVM_TX_RE.fullmatch(subject))
        kind = "an EVM transaction hash (0x and 64 hex characters)" if evm else "a Solana transaction signature"
    else:
        ok = bool(_EVM_ADDRESS_RE.fullmatch(subject)) if evm else bool(_SVM_ADDRESS_RE.fullmatch(subject))
        kind = "an EVM address (0x and 40 hex characters)" if evm else "a Solana address"
    if not ok:
        raise ChainDataError("bad_subject", f"That is not {kind} for {cp.CHAIN_LABELS[chain]}. Check it and ask again. Nothing was looked up.")
    return subject.lower() if evm else subject


def _limit_of(args: dict[str, Any]) -> int:
    try:
        value = int(args.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        value = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, value))


def _stored(cfg: cp.ChainProviderConfig) -> bool:
    from core.credential_store import has_credential

    try:
        return bool(has_credential(cfg.credential_slot))
    except Exception:
        return False


def _names(cfgs) -> str:
    labels = [c.label for c in cfgs]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " or " + labels[-1]


def _choose_provider(args: dict[str, Any], chain: str, op: str) -> tuple[cp.ChainProviderConfig, tuple[cp.ChainProviderConfig, ...]]:
    serving = cp.providers_for(chain, op)
    chain_label = cp.CHAIN_LABELS[chain]
    if not serving:
        raise ChainDataError("not_offered", f"None of VOOL's chain-data providers offer {_OP_PHRASES[op]} on {chain_label}. Nothing was looked up.")
    named = str(args.get("provider") or "").strip()
    if named:
        cfg = cp.config_for(named)
        if cfg is None:
            raise ChainDataError("unknown_provider", f"VOOL's chain-data providers are Helius, Alchemy, Solscan and Etherscan; {named[:40]!r} is not one of them. Nothing was looked up.")
        if not cfg.serves(chain, op):
            raise ChainDataError("not_offered", f"{cfg.label} does not offer {_OP_PHRASES[op]} on {chain_label}. {_names(serving)} can. Nothing was looked up.")
        if not _stored(cfg):
            raise ChainDataError("key_missing", f"No {cfg.label} key is saved. Add one in Settings → API Keys, then ask again. Nothing was looked up.")
        others = tuple(c for c in serving if c is not cfg and _stored(c))
        return cfg, others
    stored = tuple(c for c in serving if _stored(c))
    if not stored:
        raise ChainDataError(
            "key_missing",
            f"To look up {_OP_PHRASES[op]} on {chain_label}, add a {_names(serving)} key in Settings → API Keys. "
            "Nothing was looked up, and VOOL did not go online.",
        )
    return stored[0], stored[1:]


# --------------------------------------------------------------------------- transport

def _read_key(cfg: cp.ChainProviderConfig) -> str:
    from core.credential_store import CredentialReadError, get_credential, strict_reads

    try:
        with strict_reads():
            key = get_credential(cfg.credential_slot)
    except CredentialReadError:
        raise ChainDataError("key_unreadable", f"VOOL could not read the saved {cfg.label} key from this machine's key store. Nothing was looked up.") from None
    key = str(key or "").strip()
    if not key:
        raise ChainDataError("key_missing", f"No {cfg.label} key is saved. Add one in Settings → API Keys, then ask again. Nothing was looked up.")
    return key


def _safe_provider_text(text: object, key: str) -> str:
    from core.secret_redaction import redact_secrets

    clean = str(text or "").replace(key, "[key]") if key else str(text or "")
    clean = " ".join(redact_secrets(clean).split())
    return clean[:160]


def _http_failure(cfg: cp.ChainProviderConfig, chain: str, status: int) -> ChainDataError:
    label = cfg.label
    chain_label = cp.CHAIN_LABELS.get(chain, chain)
    if status == 401:
        return ChainDataError("key_rejected", f"{label} rejected the saved key (HTTP 401). Replace it in Settings → API Keys. Nothing was looked up.")
    if status == 403:
        if cfg.provider_id == "alchemy":
            return ChainDataError("chain_not_enabled", f"Alchemy refused this request on {chain_label} (HTTP 403). Turn on {chain_label} for this key's app in the Alchemy dashboard, then ask again.")
        return ChainDataError("plan_refused", f"{label} accepted the key but refused this request (HTTP 403); your plan may not include it.")
    if status == 402:
        return ChainDataError("quota_exhausted", f"{label} reports no credit or quota left for this key (HTTP 402).")
    if status == 429:
        return ChainDataError("rate_limited", f"{label} is limiting requests right now (HTTP 429). Try again shortly. Nothing was looked up.")
    if status == 404:
        return ChainDataError("not_found", f"{label} found nothing for that on {chain_label} (HTTP 404).")
    if status == 408 or 500 <= status < 600:
        return ChainDataError("provider_down", f"{label} is having trouble right now (HTTP {status}). Try again later. Nothing was looked up.")
    return ChainDataError("provider_error", f"{label} gave an unexpected answer (HTTP {status}). Nothing was looked up.")


def _send(cfg: cp.ChainProviderConfig, chain: str, url: str, *, query: dict[str, str] | None = None,
          body: object = None) -> object:
    """One request to one provider, the key where it documents it. Returns parsed JSON or raises."""
    import urllib.error
    import urllib.request
    from urllib.parse import urlencode

    from core.credential_intelligence.verification import is_timeout_error
    from core.remote_fetch_policy import (
        CredentialRedirectRefusedError,
        RemoteFetchRefusedError,
        open_remote,
        open_remote_url,
    )

    key = _read_key(cfg)
    params = dict(query or {})
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    method = "POST" if data is not None else "GET"
    carried_in_url = cfg.auth_style == "query_token"
    if carried_in_url:
        from core.secret_redaction import register_exact_secret

        register_exact_secret(key)  # the URL that carries it is scrubbed from every ledger and error
        params[cfg.auth_name] = key
    full_url = f"{url}?{urlencode(params)}" if params else url
    try:
        if carried_in_url:
            response = open_remote(
                urllib.request.Request(full_url, data=data, headers=headers, method=method),
                timeout=REQUEST_TIMEOUT_S, no_proxy=True, provider_id=cfg.registry_id,
                keyed_or_keyless="keyed", redirect_policy="refuse",
            )
        else:
            sealed = "Authorization" if cfg.auth_style == "bearer" else cfg.auth_name
            headers[sealed] = f"Bearer {key}" if cfg.auth_style == "bearer" else key
            response = open_remote_url(
                full_url, data=data, headers=headers, method=method, timeout=REQUEST_TIMEOUT_S,
                no_proxy=True, provider_id=cfg.registry_id, keyed_or_keyless="keyed", credential_headers=(sealed,),
            )
    except RemoteFetchRefusedError:
        raise ChainDataError("refused", f"VOOL is not allowed to go online in this chat (for example Local Only is on), so {cfg.label} was not asked. Nothing was looked up.") from None
    except CredentialRedirectRefusedError:
        raise ChainDataError("redirected", f"{cfg.label} answered with a redirect to another site; VOOL did not send the key there. Nothing was looked up.") from None
    except urllib.error.HTTPError as exc:
        raise _http_failure(cfg, chain, int(exc.code or 0)) from None
    except Exception as exc:
        if is_timeout_error(exc):
            raise ChainDataError("timeout", f"{cfg.label} did not answer in time. Try again. Nothing was looked up.") from None
        raise ChainDataError("unreachable", f"Could not reach {cfg.label} from this machine. Nothing was looked up.") from None
    status = int(getattr(response, "status", None) or getattr(response, "code", None) or 0)
    if 300 <= status < 400:
        raise ChainDataError("redirected", f"{cfg.label} answered with a redirect; VOOL did not follow it with the key. Nothing was looked up.")
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1) or b""
    except Exception as exc:
        if is_timeout_error(exc):
            raise ChainDataError("timeout", f"{cfg.label} did not finish answering in time. Nothing was looked up.") from None
        raise ChainDataError("unreachable", f"The answer from {cfg.label} was cut off. Nothing was looked up.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ChainDataError("too_large", f"{cfg.label}'s answer was larger than VOOL accepts. Ask for fewer rows.")
    if not 200 <= status < 300:
        raise _http_failure(cfg, chain, status)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ChainDataError("bad_answer", f"{cfg.label} answered with something that is not an API response. Nothing was looked up.") from None


def _shape_error(cfg: cp.ChainProviderConfig) -> ChainDataError:
    return ChainDataError("bad_answer", f"{cfg.label} answered in a shape VOOL does not recognise, so no figures are shown.")


def _rpc_error(cfg: cp.ChainProviderConfig, chain: str, error: object, key: str = "") -> ChainDataError:
    code = error.get("code") if isinstance(error, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    text = str(message or "").lower()
    if code in (429, -32005) or "rate limit" in text or "too many requests" in text:
        return ChainDataError("rate_limited", f"{cfg.label} is limiting requests right now. Try again shortly. Nothing was looked up.")
    if code in (-32401, 401) or "api key" in text or "unauthorized" in text:
        return ChainDataError("key_rejected", f"{cfg.label} rejected the saved key. Replace it in Settings → API Keys. Nothing was looked up.")
    if "not enabled" in text or "not allowed" in text or code == 403:
        return _http_failure(cfg, chain, 403)
    detail = _safe_provider_text(message, key)
    return ChainDataError("provider_error", f"{cfg.label} could not answer that{': ' + detail if detail else ''}. Nothing was looked up.")


def _rpc_url(cfg: cp.ChainProviderConfig, chain: str) -> str:
    if cfg.provider_id == "helius":
        return cp.HELIUS_RPC_URL
    if cfg.provider_id == "alchemy":
        return cp.alchemy_url(chain)
    raise ChainDataError("not_offered", f"{cfg.label} has no JSON-RPC endpoint for {cp.CHAIN_LABELS[chain]}.")


def _rpc(cfg: cp.ChainProviderConfig, chain: str, method: str, params: object) -> object:
    payload = _send(cfg, chain, _rpc_url(cfg, chain), body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if not isinstance(payload, dict):
        raise _shape_error(cfg)
    if payload.get("error") is not None:
        raise _rpc_error(cfg, chain, payload["error"])
    if "result" not in payload:
        raise _shape_error(cfg)
    return payload["result"]


def _rpc_batch(cfg: cp.ChainProviderConfig, chain: str, calls: list[tuple[str, object]]) -> list[object]:
    """Several JSON-RPC calls in one request; every answer must be present and error-free."""
    if not calls:
        return []
    body = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
    payload = _send(cfg, chain, _rpc_url(cfg, chain), body=body)
    if not isinstance(payload, list):
        if isinstance(payload, dict) and payload.get("error") is not None:
            raise _rpc_error(cfg, chain, payload["error"])
        raise _shape_error(cfg)
    by_id = {item.get("id"): item for item in payload if isinstance(item, dict)}
    out: list[object] = []
    for i in range(len(calls)):
        item = by_id.get(i)
        if item is None or "result" not in item:
            if isinstance(item, dict) and item.get("error") is not None:
                raise _rpc_error(cfg, chain, item["error"])
            raise _shape_error(cfg)
        out.append(item["result"])
    return out


def _etherscan(cfg: cp.ChainProviderConfig, chain: str, params: dict[str, str], *, empty_ok: bool = False) -> object:
    payload = _send(cfg, chain, cp.ETHERSCAN_API_URL, query={"chainid": cp.EVM_CHAIN_IDS[chain], **params})
    if not isinstance(payload, dict):
        raise _shape_error(cfg)
    if params.get("module") == "proxy":
        if payload.get("error") is not None:
            raise _rpc_error(cfg, chain, payload["error"])
        if "result" not in payload:
            # the proxy module reports a plan or key problem in Etherscan's own envelope
            verdict = cp.etherscan_body_verdict(payload)
            if verdict:
                raise _etherscan_verdict(cfg, chain, verdict)
            raise _shape_error(cfg)
        return payload["result"]
    if str(payload.get("status")) == "1":
        return payload.get("result")
    message = str(payload.get("message") or "")
    if empty_ok and "no transactions found" in message.lower() and payload.get("result") in ([], None):
        return []
    verdict = cp.etherscan_body_verdict(payload)
    if verdict:
        raise _etherscan_verdict(cfg, chain, verdict)
    detail = _safe_provider_text(payload.get("result") if isinstance(payload.get("result"), str) else message, "")
    raise ChainDataError("provider_error", f"Etherscan could not answer that{': ' + detail if detail else ''}. Nothing was looked up.")


def _etherscan_verdict(cfg: cp.ChainProviderConfig, chain: str, verdict: str) -> ChainDataError:
    if verdict == "invalid":
        return _http_failure(cfg, chain, 401)
    if verdict == "rate_limited":
        return _http_failure(cfg, chain, 429)
    return ChainDataError("plan_refused", f"Your Etherscan plan does not cover {cp.CHAIN_LABELS[chain]} for this request. Nothing was looked up.")


def _solscan(cfg: cp.ChainProviderConfig, chain: str, path: str, params: dict[str, str]) -> object:
    payload = _send(cfg, chain, f"{cp.SOLSCAN_API_ORIGIN}/v2.0/{path}", query=params)
    if not isinstance(payload, dict):
        raise _shape_error(cfg)
    if payload.get("success") is False:
        errors = payload.get("errors") if isinstance(payload.get("errors"), dict) else {}
        message = str(errors.get("message") or payload.get("message") or "")
        if "unauthor" in message.lower() or "invalid token" in message.lower():
            raise _http_failure(cfg, chain, 401)
        detail = _safe_provider_text(message, "")
        raise ChainDataError("provider_error", f"Solscan could not answer that{': ' + detail if detail else ''}. Nothing was looked up.")
    if "data" not in payload:
        raise _shape_error(cfg)
    return payload["data"]


# --------------------------------------------------------------------------- formatting

def _amount(raw: object, decimals: int) -> str:
    try:
        units = Decimal(int(str(raw), 0)) if isinstance(raw, str) and raw.startswith("0x") else Decimal(str(raw))
        value = units.scaleb(-int(decimals))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("not an amount") from None
    text = format(value.normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".") or "0"


def _int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("not an integer")
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _when(ts: object) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return "time unknown"


def _explorer(chain: str, *, address: str = "", tx: str = "") -> str:
    from core.wallet.chains import REGISTRY

    for spec in REGISTRY.values():
        if spec.chain_key == chain and not spec.testnet:
            template = spec.explorer_tx_template if tx else spec.explorer_address_template
            return template.replace("{id}", tx).replace("{address}", address) if template else ""
    return ""


def _public(*values: str) -> None:
    """Addresses and signatures shown in the answer are public chain data, not secrets."""
    from core.secret_redaction import register_public_identifier

    for value in values:
        if value:
            register_public_identifier(value)


# --------------------------------------------------------------------------- the reads

def lookup(args: dict[str, Any]) -> dict[str, Any]:
    op = _op_of(args)
    subject = _subject_of(op, args)
    chain = _chain_of(args, subject)
    subject = _validate_subject(op, chain, subject)
    limit = _limit_of(args)
    cfg, others = _choose_provider(args, chain, op)
    try:
        if chain == cp.CHAIN_SOLANA:
            text, rows = _solana(cfg, op, subject, limit)
        elif cfg.provider_id == "etherscan":
            text, rows = _etherscan_read(cfg, chain, op, subject, limit)
        else:
            text, rows = _alchemy_evm(cfg, chain, op, subject, limit)
    except ChainDataError as exc:
        if others and exc.code not in ("bad_subject", "not_found"):
            exc.message += f" Your {_names(others)} key is also saved; ask again naming that provider to use it instead."
        raise
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        raise _shape_error(cfg) from None
    _public(subject)
    return {"text": f"{text}\nSource: {cfg.label}, {cp.CHAIN_LABELS[chain]}.", "provider": cfg.provider_id, "chain": chain, "rows": rows}


def _solana(cfg: cp.ChainProviderConfig, op: str, subject: str, limit: int) -> tuple[str, list[dict[str, Any]]]:
    chain = cp.CHAIN_SOLANA
    if cfg.provider_id == "solscan":
        return _solscan_read(cfg, op, subject, limit)
    link_a = _explorer(chain, address=subject)
    if op == cp.OP_BALANCE:
        result = _rpc(cfg, chain, "getBalance", [subject, {"commitment": "confirmed"}])
        lamports = _int(result["value"])
        sol = _amount(lamports, 9)
        return f"{subject} holds {sol} SOL.\n{link_a}", [{"address": subject, "lamports": lamports, "sol": sol}]
    if op == cp.OP_TOKENS:
        held: list[dict[str, Any]] = []
        for program in _SOLANA_TOKEN_PROGRAMS:
            result = _rpc(cfg, chain, "getTokenAccountsByOwner", [subject, {"programId": program}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
            for entry in result["value"]:
                info = entry["account"]["data"]["parsed"]["info"]
                amount = info["tokenAmount"]
                if int(amount["amount"]) <= 0:
                    continue
                held.append({"mint": info["mint"], "amount": _amount(amount["amount"], int(amount["decimals"])),
                             "raw": int(amount["amount"]), "decimals": int(amount["decimals"])})
        held.sort(key=lambda r: Decimal(r["amount"]), reverse=True)
        if not held:
            return f"{subject} holds no tokens with a balance (SOL itself is a separate balance).\n{link_a}", []
        shown = held[:limit]
        _public(*[r["mint"] for r in shown])
        lines = [f"- {r['amount']} of mint {r['mint']}" for r in shown]
        more = f"\n({len(held) - len(shown)} more not shown.)" if len(held) > len(shown) else ""
        return f"{subject} holds {len(held)} token(s) with a balance:\n" + "\n".join(lines) + more + f"\n{link_a}", shown
    if op == cp.OP_HISTORY:
        result = _rpc(cfg, chain, "getSignaturesForAddress", [subject, {"limit": limit, "commitment": "confirmed"}])
        rows = [{"signature": r["signature"], "slot": r.get("slot"), "time": _when(r.get("blockTime")),
                 "failed": r.get("err") is not None} for r in result]
        if not rows:
            return f"No transactions found for {subject}.\n{link_a}", []
        _public(*[r["signature"] for r in rows])
        lines = [f"- {r['time']}: {r['signature']}{' (failed)' if r['failed'] else ''}" for r in rows]
        return f"Latest {len(rows)} transaction(s) for {subject}, newest first:\n" + "\n".join(lines) + f"\n{link_a}", rows
    if op == cp.OP_TOKEN_INFO:
        supply = _rpc(cfg, chain, "getTokenSupply", [subject])["value"]
        decimals = int(supply["decimals"])
        total = _amount(supply["amount"], decimals)
        row: dict[str, Any] = {"mint": subject, "decimals": decimals, "supply": total}
        name_line = ""
        if cfg.provider_id == "helius":
            try:
                asset = _rpc(cfg, chain, "getAsset", {"id": subject})
            except ChainDataError:
                asset = None
            meta = ((asset or {}).get("content") or {}).get("metadata") or {} if isinstance(asset, dict) else {}
            price = (((asset or {}).get("token_info") or {}).get("price_info") or {}) if isinstance(asset, dict) else {}
            if meta.get("name") or meta.get("symbol"):
                row.update(name=str(meta.get("name") or "")[:80], symbol=str(meta.get("symbol") or "")[:20])
                name_line = f"{row['name']} ({row['symbol']})".strip() + "\n"
            if isinstance(price.get("price_per_token"), (int, float)):
                row["price"] = price["price_per_token"]
                row["price_currency"] = str(price.get("currency") or "USDC")[:10]
                name_line += f"Price: {row['price']} {row['price_currency']} (Helius price data can be up to 10 minutes old)\n"
        return f"{name_line}Token {subject}: total supply {total}, {decimals} decimals.\n{link_a}", [row]
    if op == cp.OP_HOLDERS:
        supply = _rpc(cfg, chain, "getTokenSupply", [subject])["value"]
        decimals = int(supply["decimals"])
        total = Decimal(str(supply["amount"]))
        result = _rpc(cfg, chain, "getTokenLargestAccounts", [subject, {"commitment": "confirmed"}])
        rows = []
        for entry in result["value"][:limit]:
            share = (Decimal(str(entry["amount"])) / total * 100) if total > 0 else Decimal(0)
            rows.append({"token_account": entry["address"], "amount": _amount(entry["amount"], decimals), "percent": f"{share:.2f}"})
        if not rows:
            return f"No holders found for token {subject}.\n{link_a}", []
        _public(*[r["token_account"] for r in rows])
        lines = [f"- {r['amount']} ({r['percent']}% of supply) in token account {r['token_account']}" for r in rows]
        return (f"Largest {len(rows)} token account(s) for {subject}. Each is a token account; one owner can hold several, "
                f"and exchanges or programs show up here too:\n" + "\n".join(lines) + f"\n{link_a}"), rows
    # transaction
    result = _rpc(cfg, chain, "getTransaction", [subject, {"encoding": "json", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}])
    link_t = _explorer(chain, tx=subject)
    if result is None:
        return f"Solana has no confirmed transaction {subject} (it may be too new, dropped, or on another network).\n{link_t}", []
    meta = result["meta"]
    keys = result["transaction"]["message"]["accountKeys"]
    payer = keys[0] if keys and isinstance(keys[0], str) else ""
    change = _amount(int(meta["postBalances"][0]) - int(meta["preBalances"][0]), 9) if payer else ""
    row = {"signature": subject, "slot": result.get("slot"), "time": _when(result.get("blockTime")),
           "fee_sol": _amount(meta["fee"], 9), "succeeded": meta.get("err") is None, "fee_payer": payer, "fee_payer_sol_change": change}
    _public(payer)
    state = "succeeded" if row["succeeded"] else "failed"
    return (f"Transaction {subject} {state} at {row['time']} (slot {row['slot']}). Fee {row['fee_sol']} SOL, paid by {payer}, "
            f"whose SOL balance changed by {change}.\n{link_t}"), [row]


def _solscan_read(cfg: cp.ChainProviderConfig, op: str, subject: str, limit: int) -> tuple[str, list[dict[str, Any]]]:
    chain = cp.CHAIN_SOLANA
    link_a = _explorer(chain, address=subject)
    if op == cp.OP_BALANCE:
        data = _solscan(cfg, chain, "account/detail", {"address": subject})
        lamports = _int(data["lamports"])
        sol = _amount(lamports, 9)
        return f"{subject} holds {sol} SOL.\n{link_a}", [{"address": subject, "lamports": lamports, "sol": sol}]
    if op == cp.OP_HISTORY:
        # Solscan accepts page sizes of 10, 20, 30 or 40.
        size = next(n for n in (10, 20, 30, 40) if n >= min(limit, 40))
        data = _solscan(cfg, chain, "account/transactions", {"address": subject, "limit": str(size)})
        rows = [{"signature": r["tx_hash"], "time": _when(r.get("block_time")), "status": str(r.get("status") or "")} for r in data[:limit]]
        if not rows:
            return f"No transactions found for {subject}.\n{link_a}", []
        _public(*[r["signature"] for r in rows])
        lines = [f"- {r['time']}: {r['signature']}{' (' + r['status'] + ')' if r['status'] and r['status'] != 'Success' else ''}" for r in rows]
        return f"Latest {len(rows)} transaction(s) for {subject}, newest first:\n" + "\n".join(lines) + f"\n{link_a}", rows
    if op == cp.OP_TOKEN_INFO:
        data = _solscan(cfg, chain, "token/meta", {"address": subject})
        if not isinstance(data, dict) or "decimals" not in data:
            raise _shape_error(cfg)
        row = {"mint": subject, "name": str(data.get("name") or "")[:80], "symbol": str(data.get("symbol") or "")[:20],
               "decimals": int(data["decimals"])}
        parts = [f"{row['name']} ({row['symbol']})".strip(), f"Token {subject}, {row['decimals']} decimals."]
        if data.get("supply") is not None:
            row["supply"] = _amount(data["supply"], row["decimals"])
            parts.append(f"Total supply {row['supply']}.")
        if isinstance(data.get("holder"), int):
            row["holders"] = data["holder"]
            parts.append(f"{data['holder']} holders.")
        if isinstance(data.get("price"), (int, float)):
            row["price_usd"] = data["price"]
            parts.append(f"Price {data['price']} USD.")
        if isinstance(data.get("market_cap"), (int, float)):
            row["market_cap_usd"] = data["market_cap"]
            parts.append(f"Market cap {data['market_cap']} USD.")
        return "\n".join(p for p in parts if p) + f"\n{link_a}", [row]
    if op == cp.OP_HOLDERS:
        size = next(n for n in (10, 20, 30, 40) if n >= min(limit, 40))
        data = _solscan(cfg, chain, "token/holders", {"address": subject, "page": "1", "page_size": str(size)})
        items = data["items"]
        rows = []
        for item in items[:limit]:
            decimals = int(item["decimals"])
            rows.append({"owner": item["owner"], "amount": _amount(item["amount"], decimals), "rank": item.get("rank")})
        if not rows:
            return f"No holders found for token {subject}.\n{link_a}", []
        _public(*[r["owner"] for r in rows])
        lines = [f"- {r['amount']} held by {r['owner']}" for r in rows]
        total = data.get("total")
        head = f"Top {len(rows)} holder(s) of {subject}" + (f" (of {total})" if isinstance(total, int) else "") + ":"
        return head + "\n" + "\n".join(lines) + f"\n{link_a}", rows
    # transaction
    data = _solscan(cfg, chain, "transaction/detail", {"tx": subject})
    link_t = _explorer(chain, tx=subject)
    if not isinstance(data, dict) or "status" not in data:
        raise _shape_error(cfg)
    signers = data.get("signer") if isinstance(data.get("signer"), list) else []
    payer = str(signers[0]) if signers else ""
    row = {"signature": subject, "slot": data.get("slot"), "time": _when(data.get("block_time")), "status": str(data["status"]),
           "fee_sol": _amount(data["fee"], 9) if data.get("fee") is not None else "", "fee_payer": payer}
    _public(payer)
    fee = f" Fee {row['fee_sol']} SOL." if row["fee_sol"] else ""
    by = f" Signed by {payer}." if payer else ""
    return f"Transaction {subject}: {row['status']} at {row['time']} (slot {row['slot']}).{fee}{by}\n{link_t}", [row]


def _alchemy_evm(cfg: cp.ChainProviderConfig, chain: str, op: str, subject: str, limit: int) -> tuple[str, list[dict[str, Any]]]:
    symbol, decimals = cp.NATIVE_ASSETS[chain]
    link_a = _explorer(chain, address=subject)
    if op == cp.OP_BALANCE:
        wei = _int(_rpc(cfg, chain, "eth_getBalance", [subject, "latest"]))
        value = _amount(wei, decimals)
        return f"{subject} holds {value} {symbol} on {cp.CHAIN_LABELS[chain]}.\n{link_a}", [{"address": subject, "wei": str(wei), symbol.lower(): value}]
    if op == cp.OP_TOKENS:
        result = _rpc(cfg, chain, "alchemy_getTokenBalances", [subject, "erc20"])
        nonzero = [(t["contractAddress"].lower(), _int(t["tokenBalance"])) for t in result["tokenBalances"]
                   if t.get("tokenBalance") and t.get("error") is None and _int(t["tokenBalance"]) > 0]
        if not nonzero:
            return f"{subject} holds no ERC-20 tokens with a balance on {cp.CHAIN_LABELS[chain]}.\n{link_a}", []
        shown = nonzero[:limit]
        metas = _rpc_batch(cfg, chain, [("alchemy_getTokenMetadata", [contract]) for contract, _ in shown])
        rows = []
        for (contract, raw), meta in zip(shown, metas, strict=True):
            dec = meta.get("decimals") if isinstance(meta, dict) else None
            rows.append({"contract": contract, "symbol": str((meta or {}).get("symbol") or "?")[:20],
                         "amount": _amount(raw, int(dec)) if isinstance(dec, int) else f"{raw} (raw units; decimals unknown)"})
        lines = [f"- {r['amount']} {r['symbol']} ({r['contract']})" for r in rows]
        more = f"\n({len(nonzero) - len(shown)} more not shown.)" if len(nonzero) > len(shown) else ""
        return (f"{subject} holds {len(nonzero)} ERC-20 token(s) with a balance on {cp.CHAIN_LABELS[chain]}. Token names come from "
                f"each contract and can be spoofed:\n" + "\n".join(lines) + more + f"\n{link_a}"), rows
    if op == cp.OP_HISTORY:
        base = {"category": ["external", "erc20"], "order": "desc", "maxCount": hex(limit), "withMetadata": True,
                "excludeZeroValue": False, "fromBlock": "0x0", "toBlock": "latest"}
        sent, received = _rpc_batch(cfg, chain, [("alchemy_getAssetTransfers", [{**base, "fromAddress": subject}]),
                                                 ("alchemy_getAssetTransfers", [{**base, "toAddress": subject}])])
        transfers = list(sent["transfers"]) + list(received["transfers"])
        transfers.sort(key=lambda t: _int(t["blockNum"]), reverse=True)
        rows = []
        for t in transfers[:limit]:
            outgoing = str(t.get("from") or "").lower() == subject
            rows.append({"hash": t["hash"], "direction": "sent" if outgoing else "received",
                         "counterparty": str((t.get("to") if outgoing else t.get("from")) or ""),
                         "value": t.get("value"), "asset": str(t.get("asset") or "")[:20],
                         "time": str(((t.get("metadata") or {}).get("blockTimestamp")) or "time unknown")})
        if not rows:
            return f"No transfers found for {subject} on {cp.CHAIN_LABELS[chain]}.\n{link_a}", []
        lines = [f"- {r['time']}: {r['direction']} {r['value'] if r['value'] is not None else '?'} {r['asset']} "
                 f"{'to' if r['direction'] == 'sent' else 'from'} {r['counterparty']} ({r['hash']})" for r in rows]
        return f"Latest {len(rows)} transfer(s) for {subject} on {cp.CHAIN_LABELS[chain]}, newest first:\n" + "\n".join(lines) + f"\n{link_a}", rows
    if op == cp.OP_TOKEN_INFO:
        meta = _rpc(cfg, chain, "alchemy_getTokenMetadata", [subject])
        if not isinstance(meta, dict) or meta.get("decimals") is None:
            return f"{subject} does not answer as an ERC-20 token on {cp.CHAIN_LABELS[chain]}.\n{link_a}", []
        row = {"contract": subject, "name": str(meta.get("name") or "")[:80], "symbol": str(meta.get("symbol") or "")[:20], "decimals": int(meta["decimals"])}
        return (f"{row['name']} ({row['symbol']}) on {cp.CHAIN_LABELS[chain]}: contract {subject}, {row['decimals']} decimals. "
                f"Names come from the contract itself and can be spoofed.\n{link_a}"), [row]
    # transaction
    tx, receipt = _rpc_batch(cfg, chain, [("eth_getTransactionByHash", [subject]), ("eth_getTransactionReceipt", [subject])])
    return _evm_tx_text(chain, subject, tx, receipt)


def _evm_tx_text(chain: str, subject: str, tx: object, receipt: object) -> tuple[str, list[dict[str, Any]]]:
    symbol, decimals = cp.NATIVE_ASSETS[chain]
    link_t = _explorer(chain, tx=subject)
    if tx is None:
        return f"{cp.CHAIN_LABELS[chain]} has no transaction {subject} (it may be on another chain).\n{link_t}", []
    if not isinstance(tx, dict):
        raise ValueError("not a transaction")
    status = "pending"
    if isinstance(receipt, dict) and receipt.get("status") is not None:
        status = "succeeded" if _int(receipt["status"]) == 1 else "failed"
    block = _int(tx["blockNumber"]) if tx.get("blockNumber") else None
    row = {"hash": subject, "from": str(tx.get("from") or ""), "to": str(tx.get("to") or "(contract creation)"),
           "value": _amount(_int(tx.get("value") or "0x0"), decimals), "block": block, "status": status}
    where = f" in block {block}" if block is not None else ""
    return (f"Transaction {subject} on {cp.CHAIN_LABELS[chain]} {status}{where}: {row['from']} sent {row['value']} {symbol} to {row['to']}.\n{link_t}"), [row]


def _etherscan_read(cfg: cp.ChainProviderConfig, chain: str, op: str, subject: str, limit: int) -> tuple[str, list[dict[str, Any]]]:
    symbol, decimals = cp.NATIVE_ASSETS[chain]
    link_a = _explorer(chain, address=subject)
    if op == cp.OP_BALANCE:
        wei = _int(_etherscan(cfg, chain, {"module": "account", "action": "balance", "address": subject, "tag": "latest"}))
        value = _amount(wei, decimals)
        return f"{subject} holds {value} {symbol} on {cp.CHAIN_LABELS[chain]}.\n{link_a}", [{"address": subject, "wei": str(wei), symbol.lower(): value}]
    if op == cp.OP_HISTORY:
        result = _etherscan(cfg, chain, {"module": "account", "action": "txlist", "address": subject, "startblock": "0",
                                         "endblock": "99999999", "page": "1", "offset": str(limit), "sort": "desc"}, empty_ok=True)
        rows = []
        for t in result[:limit]:
            outgoing = str(t["from"]).lower() == subject
            rows.append({"hash": t["hash"], "direction": "sent" if outgoing else "received", "counterparty": str((t["to"] if outgoing else t["from"]) or ""),
                         "value": _amount(t["value"], decimals), "time": _when(t.get("timeStamp")), "failed": str(t.get("isError")) == "1"})
        if not rows:
            return f"No transactions found for {subject} on {cp.CHAIN_LABELS[chain]}.\n{link_a}", []
        lines = [f"- {r['time']}: {r['direction']} {r['value']} {symbol} {'to' if r['direction'] == 'sent' else 'from'} {r['counterparty']}"
                 f"{' (failed)' if r['failed'] else ''} ({r['hash']})" for r in rows]
        return f"Latest {len(rows)} transaction(s) for {subject} on {cp.CHAIN_LABELS[chain]}, newest first:\n" + "\n".join(lines) + f"\n{link_a}", rows
    # transaction
    tx = _etherscan(cfg, chain, {"module": "proxy", "action": "eth_getTransactionByHash", "txhash": subject})
    receipt = _etherscan(cfg, chain, {"module": "proxy", "action": "eth_getTransactionReceipt", "txhash": subject}) if tx else None
    return _evm_tx_text(chain, subject, tx, receipt)


# --------------------------------------------------------------------------- Settings "Test"

_TEST_STATES = {
    "verified": "ok", "invalid": "unauthorized", "unauthorized": "unauthorized", "exhausted": "exhausted",
    "rate_limited": "rate_limited", "timeout": "timeout", "network_unavailable": "unreachable",
    "provider_unavailable": "provider_unavailable", "redirected": "redirected", "malformed_response": "malformed_response",
    "unexpected_schema": "unexpected_schema", "endpoint_not_found": "endpoint_not_found", "refused": "refused",
}


def probe_chain_provider(provider_id: str) -> dict[str, Any]:
    """The Settings Test button for a stored chain-data key: the same ONE verification read Save used,
    through the same verifier, so Save and Test can never disagree. No key material in the answer."""
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.verification import verify_provider_credential
    from core.credential_store import CredentialReadError, get_credential, strict_reads

    cfg = cp.config_for(provider_id)
    if cfg is None:
        return {"provider": str(provider_id or "")[:40], "state": "failed", "detail": "unknown provider", "http_status": None}
    base = {"provider": cfg.provider_id, "label": cfg.label}
    try:
        with strict_reads():
            key = get_credential(cfg.credential_slot)
    except CredentialReadError:
        return {**base, "state": "failed", "detail": "the saved key could not be read from this machine's key store", "http_status": None}
    if not str(key or "").strip():
        return {**base, "state": "no_key", "detail": "no key stored", "http_status": None}
    descriptor = default_registry().get(cfg.registry_id)
    outcome = verify_provider_credential(str(key), descriptor, scope="chain.connection_probe")
    state = _TEST_STATES.get(outcome.status, "unconfirmed")
    return {**base, "state": state, "detail": outcome.detail if state in ("refused", "unconfirmed") else state,
            "http_status": outcome.http_status}


__all__ = ["INTENT", "SURFACE", "ChainDataError", "chain_contracts", "lookup", "probe_chain_provider", "run_tool"]
