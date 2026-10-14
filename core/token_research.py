"""Token research for any chat: one tool that gathers the public facts about a token in one go.

``token.research`` answers "tell me about this token" and "who made it, what else did they launch"
from the user's own keys, and hands the model the facts to analyse:

* Facts: name, symbol, supply and decimals (chain-data key).
* Holders: the largest holder accounts and how much of the supply the top 1 / top 10 hold.
* Creator: on Solana the fee payer of the token's first transaction, plus who can still mint
  more or freeze holders; on Ethereum, Base and BNB Smart Chain the contract's creator (Etherscan).
* Creator's other tokens: what else that address launched.
* Links: the website and social links the token's own metadata lists (anyone can write these;
  they are shown as listed, never as verified).
* X: who posted about it first, and how much (only when the user connected X; counts against
  their X read limit).

Every part is read-only and goes through the same doors as ``web.chain_lookup`` and the X tools,
so Local Only, the effect ledger and the user's limits apply. Each part either answers or says
plainly why it couldn't (no key, provider can't, not found); a part that fails never invents a
number and never stops the other parts. No key at all: nothing goes online, and the answer says
which key would help. There is no score or verdict: the tool reports facts, the model and the
user draw conclusions.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from core import chain_data as cd
from core import chain_providers as cp

INTENT = "token.research"
LAUNCHES_INTENT = "token.launches"
MAX_LAUNCHES = 50
SURFACE = "chain"
PARTS = ("facts", "holders", "creator", "creator_tokens", "links", "x")
MAX_SIGNATURE_PAGES = 3  # 3,000 transactions back, to find a token's first one
MAX_CREATED = 25
METADATA_MAX_BYTES = 64 * 1024
_LINK_KEYS = ("website", "twitter", "x", "telegram", "discord", "github", "medium")
_SHOWN_LINKS = frozenset({"website", "x", "telegram", "discord", "github", "medium"})
_SVM_TOKEN_PROGRAMS = set(cd._SOLANA_TOKEN_PROGRAMS)


class _PartUnavailable(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- the tool contract

def research_contracts(*, x_connected: bool = False) -> list[Any]:
    from core.runtime_tool_contracts import RuntimeToolContract

    actions = ("read_files", "use_network_access")
    if x_connected:
        actions += ("access_external_providers",)
    return [
        RuntimeToolContract(
            intent=INTENT,
            description=(
                "Research a token from public data with the user's own keys, in one call: its facts (name, symbol, "
                "supply), its largest holders and how concentrated they are, who created it and whether anyone can "
                "still mint or freeze it, what else that creator launched, the website and social links its metadata "
                "lists, and (when X is connected) who posted about it first. Solana, Ethereum, Base and BNB Smart "
                "Chain. Read-only. Use it for 'research this token', 'who deployed this', 'what else did the dev "
                "launch'. Give a contract address; a $TICKER or name alone only searches X for the addresses people "
                "post for it. Each part says plainly when it could not be read."
            ),
            tool_surface=SURFACE,
            capability_id="web.token_research",
            capability_claim="research a token's public on-chain facts, creator, links and early X posts",
            supported=True,
            unsupported_reason="",
            input_schema={
                "token": "str: token mint or contract address, or a $TICKER / name (X search only)",
                "chain": "str (optional): solana | ethereum | base | bnb (needed for a 0x address)",
                "parts": f"list[str] (optional): any of {', '.join(PARTS)}; default all",
                "creator": "str (optional): a creator address, to list only what it launched",
            },
            output_schema={"token": "str", "chain": "str", "parts": "dict"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="network_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=actions,
            renders_final_answer=False,
        ),
        RuntimeToolContract(
            intent=LAUNCHES_INTENT,
            description=(
                "List the newest tokens created on Solana, newest first, with the user's own Helius key: address, name, "
                "symbol and authority. Optionally only tokens with a given creator or authority address (for example one "
                "launch program's). Read-only, nothing is bought or traded. Follow up with token.research on any of them."
            ),
            tool_surface=SURFACE,
            capability_id="web.token_launches",
            capability_claim="list the newest Solana tokens through the user's Helius key",
            supported=True,
            unsupported_reason="",
            input_schema={
                "limit": f"int (optional, 1-{MAX_LAUNCHES}): how many, default 20",
                "authority": "str (optional): only tokens with this authority address",
                "creator": "str (optional): only tokens listing this creator address",
            },
            output_schema={"tokens": "list"},
            side_effect_class="read_only",
            approval_requirement="none",
            timeout_policy="network_default",
            retry_policy="none",
            artifact_emission="none",
            error_contract="returns_structured_error_result",
            permission_actions=("read_files", "use_network_access"),
            renders_final_answer=False,
        ),
    ]


def run_tool(intent: str, arguments: dict[str, Any] | None, source_context: dict[str, Any] | None = None):
    from core.execution.models import _tool_observation
    from core.runtime_execution_tools import RuntimeExecutionResult

    out = launches(arguments or {}) if intent == LAUNCHES_INTENT else research(arguments or {}, source_context=source_context)
    payload = {k: out.get(k) for k in ("token", "chain", "parts", "tokens") if out.get(k) is not None}
    observation = _tool_observation(intent=intent, tool_surface=SURFACE, ok=out["ok"], status=out["status"],
                                    message=out["text"], **payload)
    return RuntimeExecutionResult(handled=True, ok=out["ok"], status=out["status"], response_text=out["text"],
                                  details={**payload, "observation": observation})


# --------------------------------------------------------------------------- research

def _parts_of(args: dict[str, Any]) -> tuple[str, ...]:
    raw = args.get("parts")
    if raw in (None, "", []):
        return PARTS
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw))
    wanted = tuple(p for p in PARTS if p in {str(i).strip().lower() for i in items})
    if not wanted:
        raise cd.ChainDataError("bad_parts", f"parts can be any of {', '.join(PARTS)}. Nothing was looked up.")
    return wanted


def _x_connected() -> bool:
    try:
        from core import x_api

        return x_api.connected()
    except Exception:
        return False


def research(args: dict[str, Any], *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    token = str(args.get("token") or args.get("address") or args.get("mint") or "").strip()
    creator = str(args.get("creator") or args.get("deployer") or "").strip()
    try:
        parts = _parts_of(args)
        if creator and not token:
            return _creator_only(args, creator)
        if not token:
            raise cd.ChainDataError("missing_token", "Say which token: its contract address, or a $TICKER or name. Nothing was looked up.")
        from core.x_api import classify_token

        kind, value = classify_token(token)
        if kind in {"cashtag", "name"}:
            return _by_ticker(token, kind, value)
        chain = cd._chain_of(args, value)
        if chain not in (cp.CHAIN_SOLANA, *cp.EVM_CHAINS):
            raise cd.ChainDataError("unknown_chain", "That chain is not covered. Nothing was looked up.")
        address = cd._validate_subject(cp.OP_TOKEN_INFO, chain, value)
    except cd.ChainDataError as exc:
        return {"ok": False, "status": exc.code, "text": exc.message}
    found: dict[str, Any] = {}
    lines: list[str] = []
    readers = {
        "facts": lambda: _facts(chain, address),
        "holders": lambda: _holders(chain, address),
        "creator": lambda: _creator(chain, address),
        "links": lambda: _links(chain, address, web_on=_web_on(source_context)),
        "x": lambda: _x_part(address),
    }
    for part in parts:
        if part == "creator_tokens":
            who = (found.get("creator") or {}).get("address") if isinstance(found.get("creator"), dict) else ""
            if not who:
                if "creator" not in parts:
                    try:
                        who = _creator(chain, address)[1]["address"]
                    except (_PartUnavailable, cd.ChainDataError, KeyError, TypeError):
                        who = ""
                if not who:
                    found[part] = {"status": "unavailable", "message": "The creator is not known, so their other tokens can't be listed."}
                    lines.append("Creator's other tokens: not known (the creator could not be found).")
                    continue
            reader = lambda who=who: _created_by(chain, who, exclude=address)  # noqa: E731
        else:
            reader = readers[part]
        try:
            text, data = reader()
            found[part] = data
            lines.append(text)
        except _PartUnavailable as exc:
            found[part] = {"status": exc.status, "message": exc.message}
            lines.append(f"{_LABELS[part]}: {exc.message}")
        except cd.ChainDataError as exc:
            found[part] = {"status": exc.code, "message": exc.message}
            lines.append(f"{_LABELS[part]}: {exc.message}")
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, InvalidAnswer):
            found[part] = {"status": "bad_answer", "message": "The answer came back in a shape VOOL does not recognise."}
            lines.append(f"{_LABELS[part]}: the answer came back in a shape VOOL does not recognise, so nothing is shown.")
    read = [p for p in parts if isinstance(found.get(p), dict) and "status" not in found[p]]
    cd._public(address)
    head = f"Token research for {address} on {cp.CHAIN_LABELS[chain]} ({len(read)} of {len(parts)} parts read):"
    text = head + "\n" + "\n".join(f"- {line}" for line in lines)
    offline = {"key_missing", "unavailable", "x_not_connected", "not_offered"}
    if not read and all(str((found.get(p) or {}).get("status")) in offline for p in parts):
        text += "\nNothing was looked up on-chain and VOOL did not go online for it."
    return {"ok": bool(read), "status": "ok" if read else "nothing_read", "text": text,
            "token": address, "chain": chain, "parts": found}


_LABELS = {"facts": "Facts", "holders": "Holders", "creator": "Creator", "creator_tokens": "Creator's other tokens",
           "links": "Links", "x": "On X"}


class InvalidAnswer(Exception):
    pass


def _provider(chain: str, op: str, *, prefer: tuple[str, ...] = ()) -> cp.ChainProviderConfig:
    serving = [c for c in cp.providers_for(chain, op)]
    stored = [c for c in serving if cd._stored(c)]
    for pid in prefer:
        for cfg in stored:
            if cfg.provider_id == pid:
                return cfg
    if stored:
        return stored[0]
    if not serving:
        raise _PartUnavailable("not_offered", f"no provider VOOL supports offers this on {cp.CHAIN_LABELS[chain]}.")
    raise _PartUnavailable("key_missing", f"add a {cd._names(serving)} key in Settings > API Keys to read this.")


def _need(chain: str, provider_ids: tuple[str, ...], what: str) -> cp.ChainProviderConfig:
    for pid in provider_ids:
        cfg = cp.config_for(pid)
        if cfg is not None and chain in cfg.coverage and cd._stored(cfg):
            return cfg
    names = [cp.config_for(p).label for p in provider_ids if cp.config_for(p) is not None and chain in cp.config_for(p).coverage]
    if not names:
        raise _PartUnavailable("not_offered", f"VOOL can't read {what} on {cp.CHAIN_LABELS[chain]} yet.")
    raise _PartUnavailable("key_missing", f"reading {what} on {cp.CHAIN_LABELS[chain]} needs a {' or '.join(names)} key (Settings > API Keys).")


# --------------------------------------------------------------------------- facts and holders

def _facts(chain: str, address: str) -> tuple[str, dict[str, Any]]:
    _provider(chain, cp.OP_TOKEN_INFO)  # names the key to add when none serves
    answer = cd.lookup({"query": "token_info", "token": address, "chain": chain})
    if not answer.get("rows"):
        raise _PartUnavailable("not_a_token", answer["text"].split("\n")[0])
    row = dict(answer["rows"][0])
    name = " ".join(x for x in (str(row.get("name") or ""), f"({row['symbol']})" if row.get("symbol") else "") if x)
    supply = row.get("supply") or row.get("total_supply")
    bits = [name or "name not listed"]
    if supply is not None:
        bits.append(f"supply {supply}")
    if row.get("decimals") is not None:
        bits.append(f"{row['decimals']} decimals")
    if row.get("price") is not None:
        bits.append(f"price {row['price']} {row.get('price_currency') or ''}".strip())
    return "Facts: " + ", ".join(bits) + ".", {**row, "provider": answer.get("provider")}


def _holders(chain: str, address: str) -> tuple[str, dict[str, Any]]:
    _provider(chain, cp.OP_HOLDERS)
    answer = cd.lookup({"query": "holders", "token": address, "chain": chain, "limit": 20})
    rows = list(answer.get("rows") or [])
    if not rows:
        return "Holders: none found.", {"top": [], "provider": answer.get("provider")}
    shares = [Decimal(str(r.get("percent") or "0")) for r in rows]
    top1, top10 = shares[0], sum(shares[:10], Decimal(0))
    data = {"top": rows[:10], "top1_percent": f"{top1:.2f}", "top10_percent": f"{top10:.2f}",
            "accounts_read": len(rows), "provider": answer.get("provider")}
    note = ("these are token accounts; pools, exchanges and locks show up here too" if chain == cp.CHAIN_SOLANA
            else "pools, exchanges and locks show up here too")
    return (f"Holders: the largest holder has {top1:.2f}% of the supply and the top 10 have {top10:.2f}% ({note})."), data


# --------------------------------------------------------------------------- creator

def _creator(chain: str, address: str) -> tuple[str, dict[str, Any]]:
    if chain == cp.CHAIN_SOLANA:
        return _solana_creator(address)
    cfg = _need(chain, ("etherscan",), "who created a contract")
    if not cfg.serves(chain, cp.OP_HISTORY):
        raise _PartUnavailable("not_offered", f"VOOL can't read who created a contract on {cp.CHAIN_LABELS[chain]} yet.")
    result = cd._etherscan(cfg, chain, {"module": "contract", "action": "getcontractcreation", "contractaddresses": address})
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        raise _PartUnavailable("not_found", "The block explorer has no creation record for this contract.")
    first = result[0]
    who = str(first.get("contractCreator") or "").lower()
    tx = str(first.get("txHash") or "")
    if not cd._EVM_ADDRESS_RE.fullmatch(who):
        raise InvalidAnswer()
    data = {"address": who, "creation_tx": tx, "source": "contract creation record",
            "explorer": cd._explorer(chain, address=who)}
    if first.get("timestamp"):
        data["created_at"] = cd._when(first.get("timestamp"))
    cd._public(who, tx)
    when = f" at {data['created_at']}" if data.get("created_at") else ""
    return f"Creator: {who} created the contract{when} (transaction {tx}).", data


def _solana_creator(mint: str) -> tuple[str, dict[str, Any]]:
    cfg = _provider(cp.CHAIN_SOLANA, cp.OP_HISTORY, prefer=("helius", "alchemy"))
    if cfg.provider_id not in {"helius", "alchemy"}:
        raise _PartUnavailable("not_offered", "finding a Solana token's creator needs a Helius or Alchemy key (Settings > API Keys).")
    chain = cp.CHAIN_SOLANA
    account = cd._rpc(cfg, chain, "getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
    value = (account or {}).get("value") if isinstance(account, dict) else None
    if not value:
        raise _PartUnavailable("not_found", "Solana has no account at that address.")
    if value.get("owner") not in _SVM_TOKEN_PROGRAMS:
        raise _PartUnavailable("not_a_token", "that address is not a token mint (it may be a wallet or a pool).")
    info = value["data"]["parsed"]["info"]
    data: dict[str, Any] = {
        "mint_authority": info.get("mintAuthority") or None,
        "freeze_authority": info.get("freezeAuthority") or None,
        "token_program": "Token-2022" if value.get("owner") == cd._SOLANA_TOKEN_PROGRAMS[1] else "SPL Token",
    }
    rights = []
    rights.append("no one can mint more" if not data["mint_authority"] else f"{data['mint_authority']} can still mint more")
    rights.append("no one can freeze holders" if not data["freeze_authority"] else f"{data['freeze_authority']} can still freeze holders")
    oldest = None
    before = None
    reached_start = False
    for _ in range(MAX_SIGNATURE_PAGES):
        opts: dict[str, Any] = {"limit": 1000, "commitment": "confirmed"}
        if before:
            opts["before"] = before
        page = cd._rpc(cfg, chain, "getSignaturesForAddress", [mint, opts])
        if not isinstance(page, list):
            raise InvalidAnswer()
        if page:
            oldest = page[-1]
            before = oldest["signature"]
        if len(page) < 1000:
            reached_start = True
            break
    cd._public(*(v for v in (data["mint_authority"], data["freeze_authority"]) if v))
    if not reached_start or not oldest:
        data["address"] = None
        why = ("it has more than 3,000 transactions, so its first one is too far back to read here" if not reached_start
               else "no transactions were found for it")
        return f"Creator: not found ({why}). Right now {', and '.join(rights)}.", data
    tx = cd._rpc(cfg, chain, "getTransaction", [oldest["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0,
                                                                      "commitment": "confirmed"}])
    keys = (((tx or {}).get("transaction") or {}).get("message") or {}).get("accountKeys") if isinstance(tx, dict) else None
    payer = keys[0] if keys and isinstance(keys[0], str) else ""
    if not cd._SVM_ADDRESS_RE.fullmatch(payer):
        raise InvalidAnswer()
    data.update(address=payer, first_tx=oldest["signature"], created_at=cd._when(oldest.get("blockTime")),
                source="fee payer of the token's first transaction", explorer=cd._explorer(chain, address=payer))
    cd._public(payer, oldest["signature"])
    return (f"Creator: {payer} paid for the token's first transaction at {data['created_at']} "
            f"(launch tools can make this a launch program's account). Right now {', and '.join(rights)}."), data


def _created_by(chain: str, creator: str, *, exclude: str = "") -> tuple[str, dict[str, Any]]:
    if chain == cp.CHAIN_SOLANA:
        cfg = _need(chain, ("helius",), "what an address launched")
        found: dict[str, dict[str, Any]] = {}
        for method, params in (("getAssetsByAuthority", {"authorityAddress": creator, "page": 1, "limit": 100}),
                               ("getAssetsByCreator", {"creatorAddress": creator, "onlyVerified": False, "page": 1, "limit": 100})):
            result = cd._rpc(cfg, chain, method, params)
            for item in (result or {}).get("items") or [] if isinstance(result, dict) else []:
                if not isinstance(item, dict) or str(item.get("interface") or "") not in {"FungibleToken", "FungibleAsset"}:
                    continue
                aid = str(item.get("id") or "")
                if not cd._SVM_ADDRESS_RE.fullmatch(aid) or aid == exclude:
                    continue
                meta = ((item.get("content") or {}).get("metadata") or {})
                found.setdefault(aid, {"address": aid, "name": str(meta.get("name") or "")[:80],
                                       "symbol": str(meta.get("symbol") or "")[:20]})
        rows = list(found.values())[:MAX_CREATED]
        note = ("tokens whose on-chain metadata names this address as creator or authority; tokens launched through a "
                "launch program may not show up")
    else:
        cfg = _need(chain, ("etherscan",), "what an address launched")
        txs = cd._etherscan(cfg, chain, {"module": "account", "action": "txlist", "address": creator, "startblock": "0",
                                         "endblock": "99999999", "page": "1", "offset": "1000", "sort": "asc"}, empty_ok=True)
        if not isinstance(txs, list):
            raise InvalidAnswer()
        rows = []
        for t in txs:
            made = str(t.get("contractAddress") or "").lower()
            if t.get("to") in ("", None) and cd._EVM_ADDRESS_RE.fullmatch(made) and made != exclude:
                rows.append({"address": made, "created_at": cd._when(t.get("timeStamp")), "creation_tx": str(t.get("hash") or "")})
        rows = rows[:MAX_CREATED]
        note = f"contracts this address created in its first {len(txs)} transactions; not every contract is a token"
    cd._public(creator, *[r["address"] for r in rows])
    if not rows:
        return f"Creator's other tokens: none found for {creator} ({note}).", {"creator": creator, "tokens": [], "note": note}
    listed = "; ".join(f"{(r.get('name') or '') + ' ' if r.get('name') else ''}{r['address']}".strip() for r in rows[:10])
    more = f" and {len(rows) - 10} more" if len(rows) > 10 else ""
    text = f"Creator's other tokens: {len(rows)} found for {creator} ({note}): {listed}{more}."
    return text, {"creator": creator, "tokens": rows, "note": note}


def _creator_only(args: dict[str, Any], creator: str) -> dict[str, Any]:
    try:
        chain = cd._chain_of(args, creator)
        creator = cd._validate_subject(cp.OP_BALANCE, chain, creator)
        text, data = _created_by(chain, creator)
    except (_PartUnavailable, cd.ChainDataError) as exc:
        status = getattr(exc, "status", None) or getattr(exc, "code", "failed")
        return {"ok": False, "status": status, "text": f"Creator's other tokens: {exc.message}"}
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, InvalidAnswer):
        return {"ok": False, "status": "bad_answer", "text": "The answer came back in a shape VOOL does not recognise, so nothing is shown."}
    return {"ok": True, "status": "ok", "text": text, "token": "", "chain": chain, "parts": {"creator_tokens": data}}


# --------------------------------------------------------------------------- links (the token's own metadata)

def _web_on(source_context: dict[str, Any] | None) -> bool:
    try:
        from core.remote_fetch_policy import effective_web_available

        return bool(effective_web_available(source_context))
    except Exception:
        return False


def _links(chain: str, address: str, *, web_on: bool = False) -> tuple[str, dict[str, Any]]:
    if chain != cp.CHAIN_SOLANA:
        raise _PartUnavailable("not_offered", f"tokens on {cp.CHAIN_LABELS[chain]} keep no website or social links on-chain.")
    cfg = _need(chain, ("helius",), "a token's listed links")
    asset = cd._rpc(cfg, chain, "getAsset", {"id": address})
    if not isinstance(asset, dict):
        raise InvalidAnswer()
    content = asset.get("content") or {}
    links: dict[str, str] = {}
    for key, value in (content.get("links") or {}).items():
        _keep_link(links, "website" if key == "external_url" else str(key), value)
    uri = str(content.get("json_uri") or "")
    fetched = False
    skipped = ""
    # The metadata file lives wherever the token's creator chose, so it is read only with web
    # access on, and never from a private or local address.
    if uri.startswith("https://") and not web_on:
        skipped = "its metadata file was not read because web access is off"
    elif uri.startswith("https://") and not _public_url(uri):
        skipped = "its metadata file points at a private address, so it was not read"
    elif uri.startswith("https://"):
        meta = _metadata_json(uri)
        if meta is not None:
            fetched = True
            for source in (meta, meta.get("extensions") or {}, meta.get("properties") or {}):
                if isinstance(source, dict):
                    for key in (*_LINK_KEYS, "external_url"):
                        _keep_link(links, "website" if key == "external_url" else key, source.get(key))
    data = {"links": links, "metadata_uri": uri, "metadata_read": fetched,
            "note": "listed by whoever made the token; not verified"}
    tail = f" ({skipped})" if skipped else ""
    if skipped:
        data["metadata_skipped"] = skipped
    if not links:
        return f"Links: the token's metadata lists no website or social links{tail}.", data
    shown = ", ".join(f"{k} {v}" for k, v in links.items())
    return f"Links listed in the token's own metadata (anyone can write these; not verified): {shown}{tail}.", data


def _public_url(uri: str) -> bool:
    try:
        from core.null_dial import is_ssrf_safe_url

        return bool(is_ssrf_safe_url(uri))
    except Exception:
        return False


def _keep_link(links: dict[str, str], key: str, value: Any) -> None:
    key = "x" if key == "twitter" else key
    text = str(value or "").strip()
    if key in links or key not in _SHOWN_LINKS or not text:
        return
    if key == "telegram" and text.startswith("@"):
        text = "https://t.me/" + text[1:]
    if not re.fullmatch(r"https://[^\s<>\"']{3,300}", text):
        return
    links[key] = text


def _metadata_json(uri: str) -> dict[str, Any] | None:
    """The token's off-chain metadata file (public, no key sent). None when it can't be read."""
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote_url

    try:
        response = open_remote_url(uri, headers={"Accept": "application/json"}, method="GET", timeout=cd.REQUEST_TIMEOUT_S,
                                   no_proxy=True, provider_id="token.metadata", keyed_or_keyless="keyless")
        raw = response.read(METADATA_MAX_BYTES + 1) or b""
    except RemoteFetchRefusedError:
        return None
    except Exception:
        return None
    if len(raw) > METADATA_MAX_BYTES:
        return None
    try:
        meta = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


# --------------------------------------------------------------------------- new launches

def launches(args: dict[str, Any]) -> dict[str, Any]:
    """The newest fungible tokens on Solana (DAS ``searchAssets`` sorted by creation), newest first."""
    chain = cp.CHAIN_SOLANA
    try:
        limit = max(1, min(MAX_LAUNCHES, int(args.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    query: dict[str, Any] = {"interface": "FungibleToken", "page": 1, "limit": limit,
                             "sortBy": {"sortBy": "created", "sortDirection": "desc"}}
    try:
        for name, field in (("authority", "authorityAddress"), ("creator", "creatorAddress")):
            value = str(args.get(name) or "").strip()
            if value:
                query[field] = cd._validate_subject(cp.OP_BALANCE, chain, value)
        cfg = _need(chain, ("helius",), "the newest tokens")
        result = cd._rpc(cfg, chain, "searchAssets", query)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise InvalidAnswer()
    except (_PartUnavailable, cd.ChainDataError) as exc:
        status = getattr(exc, "status", None) or getattr(exc, "code", "failed")
        message = exc.message
        return {"ok": False, "status": status, "text": "New launches: " + message[0].lower() + message[1:]
                if status in {"key_missing", "not_offered"} else message}
    except InvalidAnswer:
        return {"ok": False, "status": "bad_answer", "text": "The Solana data service answered in a shape VOOL does not recognise, so no tokens are shown."}
    rows = []
    for item in result["items"]:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("id") or "")
        if not cd._SVM_ADDRESS_RE.fullmatch(aid):
            continue
        meta = ((item.get("content") or {}).get("metadata") or {})
        authorities = [str(a.get("address") or "") for a in item.get("authorities") or [] if isinstance(a, dict)]
        rows.append({"address": aid, "name": str(meta.get("name") or "")[:80], "symbol": str(meta.get("symbol") or "")[:20],
                     "authority": next((a for a in authorities if cd._SVM_ADDRESS_RE.fullmatch(a)), "")})
    cd._public(*[r["address"] for r in rows], *[r["authority"] for r in rows if r["authority"]])
    if not rows:
        return {"ok": True, "status": "ok", "text": "New launches: no tokens came back for that.", "tokens": []}
    lines = [f"- {(r['name'] + ' (' + r['symbol'] + ') ') if r['name'] else ''}{r['address']}" for r in rows]
    text = (f"The {len(rows)} newest Solana tokens, newest first (names are whatever the creator typed; "
            "anyone can launch a token):\n" + "\n".join(lines) + "\nSource: your Solana data key.")
    return {"ok": True, "status": "ok", "text": text, "tokens": rows, "chain": chain}


# --------------------------------------------------------------------------- X

def _x_part(address: str) -> tuple[str, dict[str, Any]]:
    if not _x_connected():
        raise _PartUnavailable("x_not_connected", "connect X in Settings > API Keys to see who posted about it.")
    from core import x_api

    out = x_api.token_callers(address, max_results=50)
    if not out.ok:
        raise _PartUnavailable(str(out.status), out.message)
    data = {k: out.data.get(k) for k in ("query", "posts_found", "earliest_post_at", "callers")}
    callers = list(data.get("callers") or [])
    data["callers"] = callers[:10]
    if not callers:
        return "On X: no recent posts mention it.", data
    first = callers[0]
    who = ", ".join("@" + str(c.get("username")) for c in callers[:5])
    return (f"On X: {data.get('posts_found')} recent post(s) from {len(callers)} account(s). First: @{first.get('username')} "
            f"at {first.get('first_call_at') or 'an unknown time'}. Accounts: {who}."), data


def _by_ticker(token: str, kind: str, value: str) -> dict[str, Any]:
    """A $TICKER or name has no single on-chain address: find the addresses people post for it on X."""
    if not _x_connected():
        return {"ok": False, "status": "address_needed",
                "text": (f"{token} is a ticker or name, and many tokens share those. Give its contract address to research it. "
                         "(With X connected, VOOL can also look for the addresses people post for it.) Nothing was looked up.")}
    from core import x_api

    out = x_api.search_posts(token=token, max_results=50)
    if not out.ok:
        return {"ok": False, "status": str(out.status), "text": out.message}
    counts: dict[str, int] = {}
    for post in out.data.get("posts") or []:
        for address in (post.get("tokens") or {}).get("addresses") or []:
            counts[address] = counts.get(address, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
    cd._public(*[a for a, _ in ranked])
    data = {"x": {"posts_read": len(out.data.get("posts") or []), "addresses": [{"address": a, "posts": n} for a, n in ranked]}}
    if not ranked:
        text = f"{token}: {data['x']['posts_read']} recent X post(s), but none posted a contract address. Give the address to research it."
    else:
        listed = "; ".join(f"{a} ({n} post{'s' if n != 1 else ''})" for a, n in ranked)
        text = (f"{token} is a ticker or name, so VOOL looked for the addresses posted with it on X: {listed}. "
                "Several tokens can share a ticker: check which one is meant, then research that address.")
    return {"ok": True, "status": "address_candidates", "text": text, "token": token, "chain": "", "parts": data}


__all__ = ["INTENT", "LAUNCHES_INTENT", "PARTS", "launches", "research", "research_contracts", "run_tool"]
