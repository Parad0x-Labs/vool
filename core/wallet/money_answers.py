"""Money questions asked in ordinary chat, answered from the wallet's own records. READ ONLY.

"How much do I have across all my wallets?", "what did the agent spend yesterday, and on what?", "how much can it
still spend today?", "is a payment waiting for me?", "show my last payments": each answer is built here from the
wallet's own authorities, never from a model's memory and never from the web:

* balances: every wallet of the active network environment (the owner's Main / Everyday / Burner, the assistant's own
  wallet, the Skills & plugins wallet), its coin AND every registered token of its row, read through
  ``core.wallet.balances`` (identity proof first). A read that failed is said to be unknown, never zero, and a total
  leaves it out and says so.
* spending: the payments (``core.wallet.proposals``) that left an agent's wallet, by local calendar day, with what
  each one paid for (``core.wallet.purpose``: the service's own address and resource) and why it was paid without
  asking (the allowance decision log, ``core.wallet.allowances``). Paid means confirmed; in flight or failed is said.
* limits: each agent allowance's terms and what is used and left (``core.wallet.agent_settings``), the owner wallets'
  own caps (``core.wallet.limits``) and the freeze.
* waiting: every payment waiting for the owner's approval.
* history: the latest payments from every wallet, newest first.

Nothing here imports anything that approves, signs, sends, raises a limit or proposes a payment: a question about
money never starts a payment. Amounts are plain coin amounts ("0.6 USDC"), never atomic units.
"""
from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

TOPIC_BALANCES = "balances"
TOPIC_SPENDING = "spending"
TOPIC_LIMITS = "limits"
TOPIC_WAITING = "waiting"
TOPIC_HISTORY = "history"
TOPIC_ADDRESS = "address"
TOPICS = (TOPIC_BALANCES, TOPIC_SPENDING, TOPIC_LIMITS, TOPIC_WAITING, TOPIC_HISTORY, TOPIC_ADDRESS)

WHO_OWNER = "owner"
WHO_AGENT = "agent"
WHO_ADDONS = "addons"

SETTINGS_PLACE = "Settings, under Crypto"
#: where the tool loop hands the turn's own ask to the wallet.status tool (``core.agent_runtime.research_tool_loop_facade``)
ASK_TEXT_KEY = "wallet_ask_text"

# --------------------------------------------------------------------------------------------------------------------
# Reading the question
# --------------------------------------------------------------------------------------------------------------------

_COINS = r"(?:sol|solana|usdc|usdt|eth|ether|bnb|stablecoins?|tokens?|coins?|crypto)"
#: an agent of the owner's: the assistant (also "you" / "VOOL" as the one who pays), or the plugins' wallet
_AGENT_RE = re.compile(r"\b(?:agents?|assistant(?:'s)?|vool(?:'s)?|bot|ai)\b", re.I)
_YOU_PAID_RE = re.compile(r"\b(?:did|have|has)\s+you\b|\byou\s+(?:paid|spent|bought|spend|pay)\b|\byour\s+(?:own\s+)?(?:wallet|spending|allowance)\b", re.I)
_ADDONS_RE = re.compile(r"\b(?:skills?\s*(?:and|&|\+)\s*plugins?|plugins?|add-?ons?)\b", re.I)
_WALLET_RE = re.compile(r"\bwallets?\b", re.I)
_OWN_WALLET_RE = re.compile(
    r"\b(?:my|our)\s+(?:own\s+)?(?:\w+\s+){0,2}wallets?\b"
    r"|\b(?:all|each|every|both|any)\s+(?:of\s+)?(?:my\s+|our\s+|the\s+)?wallets?\b"
    r"|\b(?:the|this|that|its|your|his|their)\s+(?:own\s+)?(?:main|burner|everyday|agent(?:'s)?|assistant(?:'s)?|vool(?:'s)?|skills?\s*(?:and|&|\+)\s*plugins?|plugins?'?)"
    r"(?:'s)?\s+(?:own\s+)?wallets?\b"
    r"|\b(?:assistant|agent|vool)'s\s+(?:own\s+)?wallets?\b|\byour\s+(?:own\s+)?wallets?\b"
    r"|\bwallets?\s+(?:balance|balances|total|totals|holdings?)\b|\bburner\b",
    re.I)
_POSSESSED_COIN_RE = re.compile(
    rf"\b(?:my|our)\s+(?:own\s+)?{_COINS}\b"
    rf"|\bhow\s+much\s+(?:\w+\s+){{0,2}}?{_COINS}\b[^.?!]{{0,40}}?\b(?:do|did|have|got|is|are)\s+(?:i|we|sitting|left|there)\b"
    rf"|\bhow\s+much\b[^.?!]{{0,30}}?\b(?:do|have)\s+(?:i|we)\s+(?:have|got|hold|own)\b[^.?!]{{0,40}}?\b(?:on|in)\s+\w+"
    rf"|\b{_COINS}\b[^.?!]{{0,30}}\b(?:do|have)\s+(?:i|we)\s+(?:have|hold|own|got)\b"
    rf"|\b(?:total|altogether|all\s+together|in\s+total)\b[^.?!]{{0,30}}?\b(?:usdc|usdt|sol|eth|bnb)\b[^.?!]{{0,30}}?\b(?:across|everything|all|wallets?)\b"
    rf"|\b(?:usdc|usdt|sol|eth|bnb)\b[^.?!]{{0,30}}?\bacross\s+(?:everything|all|my)\b",
    re.I)
_SPEND_RE = re.compile(r"\b(?:spen[dt]|spending|paid|pay\s+for|payments?|bought|buy|charged|purchases?|costs?|receipts?)\b"
                       r"|\b(?:did|does|has|have|had|will|would)\b[^.?!]{0,30}\bpay\b", re.I)
_SPEND_NOT_MONEY_RE = re.compile(r"\bspen[dt]\b\s+(?:\w+\s+){0,2}?(?:time|hours?|minutes?|days?\s+on|effort|tokens?|energy|attention)\b", re.I)
_LIMIT_RE = re.compile(
    r"\b(?:limits?|caps?|allowances?|budgets?|ceilings?|max(?:imum)?|most)\b|\b(?:can|could|may|allowed\s+to)\s+(?:\w+\s+){0,2}?(?:still\s+)?(?:spend|pay)\b"
    r"|\b(?:per|a|each)\s+(?:day|week|month|hour|payment)\b|\bdaily\b|\bin\s+one\s+go\b"
    r"|\bleft\s+to\s+spend\b|\bhow\s+much\s+more\b|\bhow\s+many\s+(?:more\s+)?payments\b", re.I)
_WAITING_RE = re.compile(r"\b(?:waiting|pending|awaiting|stuck|needs?\s+(?:my|your)\s+(?:ok|okay|approval|signature)|to\s+(?:approve|sign)|approvals?|unapproved|not\s+(?:yet\s+)?approved)\b", re.I)
_HISTORY_RE = re.compile(
    r"\b(?:last|latest|recent|previous|past)\s+(?:\w+\s+){0,2}?(?:payments?|transactions?|transfers?|receipts?|purchases?|sends?)\b"
    r"|\b(?:payment|transaction|transfer|spending)\s+(?:history|log|list|record)\b|\breceipts?\b"
    r"|\bwhat\s+(?:did|have)\s+(?:i|we)\s+(?:pay|paid|send|sent|spend|spent)\b", re.I)
_PAYMENT_WORD_RE = re.compile(r"\b(?:payments?|transactions?|transfers?|receipts?|pay|paid|spend|spent|sends?|money|crypto|wallets?|usdc|sol|eth)\b", re.I)
_ADDRESS_RE = re.compile(r"\b(?:address|addresses|public\s+key|deposit\s+to|receive\s+(?:money|crypto|coins|usdc|sol))\b", re.I)
_BALANCE_RE = re.compile(r"\b(?:balances?|how\s+much|worth|holdings?|hold|have|left\s+(?:in|on)|in\s+(?:it|there)|total|broken\s+down|breakdown|what'?s\s+in|what\s+is\s+in)\b", re.I)
#: other people's money, cloud AI usage, cards and banks: not this wallet
_NOT_THIS_WALLET_RE = re.compile(
    r"\b(?:cloud|openrouter|api\s+key|tokens?\s+used|token\s+usage|byok|anthropic|openai|gpt|claude|model\s+usage|ai\s+usage|"
    r"credits?|debit\s+cards?|bank|paypal|venmo|revolut|apple\s+pay|google\s+wallet|apple\s+wallet|"
    r"invoice|salary|rent\s+due|my\s+(?:mom|dad|wife|husband|friend|boss)'?s?)\b", re.I)
#: a how-to, setup or safety question about wallets in general: not a reading of the owner's own wallets
_HOW_TO_RE = re.compile(
    r"\b(?:explain|in\s+general|set\s*up|setup|connect|install|import|recover|restore|back\s*up|backup|secure|protect|hack(?:ed)?|scam\w*|phish\w*|"
    r"lost|stolen|seed\s+phrase|recovery\s+phrase|private\s+key|best|recommend\w*|should\s+i\s+(?:use|get|buy))\b"
    r"|\bhow\s+(?:does|do)\b[^.?!]{0,60}\bwork\b|\bwhat\s+(?:is|are)\s+(?:a|an)\b", re.I)
#: material the words point at (a file, a note, a document): reading it is not a reading of the wallets
_MATERIAL_RE = re.compile(r"\b(?:files?|folders?|documents?|docs?|pdfs?|notes?|spreadsheets?|screenshots?|attachments?|words|emails?|messages?)\b|\.(?:txt|csv|pdf|md|json)\b", re.I)
#: the shape of asking: a question, or a request to show or tell (a statement about a wallet is not a question)
_ASKING_RE = re.compile(
    r"\?|^(?:ok(?:ay)?[,\s]+|so[,\s]+|and[,\s]+|hey[,\s]+|vool[,\s]+)*(?:how|what|whats|what's|which|is|are|do|does|did|has|have|can|could|show|give|list|tell|check|"
    r"any|was|were|where|when|who|total|sum|count|break|summar\w*|remind)\b|\bhow\s+(?:much|many)\b", re.I)
#: a read question, not an order: an order that changes money or settings belongs to the wallet's own doors
_ORDER_RE = re.compile(
    r"^(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+|go\s+ahead\s+and\s+)?(?:approve|decline|reject|cancel|pause|resume|stop|set|change|raise|lower|increase|"
    r"decrease|create|make|open|delete|remove|freeze|unfreeze|send|pay|transfer|top\s+up|fund|withdraw|move|swap|buy|sell|bridge|stake|"
    r"turn\s+(?:on|off)|enable|disable|rename|export|reveal|show\s+(?:me\s+)?(?:the\s+)?(?:seed|backup|private))\b", re.I)
_HYPOTHETICAL_SEND_RE = re.compile(
    r"\bif\s+i\s+(?:sent|send|paid|pay|transferred|transfer|moved|move)\s+(?P<amount>\d+(?:\.\d+)?)\s*(?P<coin>sol|usdc|usdt|eth|bnb)\b", re.I)

_PERIODS = (
    ("yesterday", re.compile(r"\byesterday\b", re.I)),
    ("last_day", re.compile(r"\b(?:last|past)\s+(?:day|24\s*h(?:ours?)?)\b", re.I)),
    ("today", re.compile(r"\btoday\b|\bso\s+far\s+today\b|\bthis\s+morning\b", re.I)),
    ("this_week", re.compile(r"\bthis\s+week\b", re.I)),
    ("last_week", re.compile(r"\b(?:last|past)\s+(?:week|7\s+days|seven\s+days)\b", re.I)),
    ("this_month", re.compile(r"\bthis\s+month\b", re.I)),
    ("last_month", re.compile(r"\b(?:last|past)\s+(?:month|30\s+days|thirty\s+days)\b", re.I)),
)

#: networks people name that VOOL keeps no wallet on (asked about, they are said to hold nothing VOOL can read)
_OTHER_CHAINS = {
    "polygon": "Polygon", "matic": "Polygon", "avalanche": "Avalanche", "avax": "Avalanche", "tron": "Tron", "bitcoin": "Bitcoin", "btc": "Bitcoin",
    "litecoin": "Litecoin", "dogecoin": "Dogecoin", "doge": "Dogecoin", "cardano": "Cardano", "ton": "TON", "sui": "Sui", "aptos": "Aptos", "near": "NEAR",
    "optimism": "Optimism", "zksync": "zkSync", "linea": "Linea", "scroll": "Scroll", "fantom": "Fantom", "cosmos": "Cosmos", "polkadot": "Polkadot",
    "xrp": "XRP Ledger", "ripple": "XRP Ledger", "stellar": "Stellar", "monero": "Monero", "zcash": "Zcash", "blast": "Blast", "mantle": "Mantle",
    "celo": "Celo", "gnosis": "Gnosis", "hyperliquid": "Hyperliquid", "berachain": "Berachain", "sonic": "Sonic", "unichain": "Unichain",
}
_CHAIN_WORDS = {"solana": "solana", "sol": "solana", "base": "base", "ethereum": "ethereum", "eth": "ethereum", "mainnet ethereum": "ethereum",
                "bnb": "bnb", "bsc": "bnb", "binance": "bnb", "arbitrum": "arbitrum", "arb": "arbitrum", "robinhood": "robinhood"}


@dataclass(frozen=True)
class MoneyAsk:
    """What a money question asks, read from its words."""

    topics: tuple[str, ...]
    who: tuple[str, ...] = ()           # WHO_OWNER / WHO_AGENT / WHO_ADDONS; empty: everyone
    wallet_names: tuple[str, ...] = ()  # owner wallet names the words name ("burner", "main", a custom name)
    chain_key: str = ""                 # a VOOL chain the words name ("solana", "base", ...)
    other_chain: str = ""               # a network VOOL keeps no wallet on ("Polygon")
    coin: str = ""                      # a coin the words name ("SOL", "USDC")
    period: str = ""                    # today / yesterday / last_day / this_week / last_week / this_month / last_month
    unasked_only: bool = False          # "without asking me": only what was paid with nobody asked
    hypothetical: tuple[str, str] = ()  # ("1", "SOL") of "if I sent 1 SOL ..."


def _clean(text: str) -> str:
    return " ".join(str(text or "").replace("’", "'").split())


#: "my balance" with no coin or wallet word: the wallet's only while crypto is on (with it off, "balance" is someone
#: else's word, the runtime's credits among them)
_MY_BALANCE_RE = re.compile(r"\b(?:my|our)\s+(?:(?:total|overall|current|crypto|coin|sol|usdc|usdt|eth|bnb|onchain|on-chain)\s+){0,2}balances?\b", re.I)


def is_money_question(text: str, *, wallet_on: bool = True) -> bool:
    """Whether the words ask about the owner's own wallets, an agent's spending, limits, waiting payments or payment
    history. A question, never an order (orders go to the wallet's own doors), and never cloud AI usage, a card or a
    bank. Pure function of the words."""
    cleaned = _clean(text)
    if not cleaned or len(cleaned) > 400:
        return False
    if _NOT_THIS_WALLET_RE.search(cleaned) or _HOW_TO_RE.search(cleaned) or _MATERIAL_RE.search(cleaned) or not _ASKING_RE.search(cleaned):
        return False
    first_clause = re.split(r"[.?!;]\s+", cleaned)[0]
    if _ORDER_RE.search(first_clause) and not _HYPOTHETICAL_SEND_RE.search(cleaned):
        return False
    if _OWN_WALLET_RE.search(cleaned):
        return True
    if _POSSESSED_COIN_RE.search(cleaned):
        return True
    if _MY_BALANCE_RE.search(cleaned) and (wallet_on or re.search(rf"\b{_COINS}\b", cleaned, re.I)):
        return True
    agentish = bool(_AGENT_RE.search(cleaned) or _YOU_PAID_RE.search(cleaned) or re.search(r"\bplugins?\b|\bskills?\b", cleaned, re.I))
    spendish = bool(_SPEND_RE.search(cleaned)) and not _SPEND_NOT_MONEY_RE.search(cleaned)
    if agentish and spendish:
        return True
    if agentish and _LIMIT_RE.search(cleaned) and re.search(r"\b(?:spend|spending|pay|payments?|allowance|wallet|money|usdc|sol)\b", cleaned, re.I):
        return True
    if _WAITING_RE.search(cleaned) and re.search(r"\b(?:payments?|transactions?|transfers?|sends?|wallet|crypto|usdc|sol|eth|money)\b", cleaned, re.I):
        return True
    return bool(_HISTORY_RE.search(cleaned) and _PAYMENT_WORD_RE.search(cleaned) and not re.search(r"\b(?:email|mail|message|chat|file|note)s?\b", cleaned, re.I))


def may_be_about_money(text: str) -> bool:
    """Whether the wallet's read tool belongs on the table for these words: a money question, or a question about
    what waits for the owner's approval (payments among other things; the model decides). Seats a read tool only."""
    if is_money_question(text):
        return True
    cleaned = _clean(text)
    return bool(cleaned and len(cleaned) <= 400 and _ASKING_RE.search(cleaned) and re.search(r"\b(?:waiting|pending|awaiting)\b[^.?!]{0,40}\b(?:approv\w*|sign\w*|ok)\b", cleaned, re.I)
                and not _MATERIAL_RE.search(cleaned) and not _NOT_THIS_WALLET_RE.search(cleaned))


def read_ask(text: str) -> MoneyAsk:
    """The topics, wallets, network, coin and period a money question names."""
    cleaned = _clean(text)
    lowered = cleaned.lower()
    topics: list[str] = []
    agentish = bool(_AGENT_RE.search(cleaned) or _YOU_PAID_RE.search(cleaned))
    addons = bool(_ADDONS_RE.search(cleaned))
    spendish = bool(_SPEND_RE.search(cleaned)) and not _SPEND_NOT_MONEY_RE.search(cleaned)
    wallet_named = bool(_WALLET_RE.search(cleaned))
    if _WAITING_RE.search(cleaned):
        topics.append(TOPIC_WAITING)
    if _LIMIT_RE.search(cleaned) and not re.search(r"\bhow\s+much\s+(?:is\s+)?left\s+(?:in|on)\b", lowered):
        topics.append(TOPIC_LIMITS)
    if (agentish or addons) and spendish and TOPIC_LIMITS not in topics and TOPIC_WAITING not in topics:
        topics.append(TOPIC_SPENDING)
    elif _HISTORY_RE.search(cleaned) and TOPIC_WAITING not in topics:
        topics.append(TOPIC_SPENDING if (agentish or addons) else TOPIC_HISTORY)
    if _ADDRESS_RE.search(cleaned) and wallet_named:
        topics.append(TOPIC_ADDRESS)
    if not topics or (_BALANCE_RE.search(cleaned) and wallet_named and TOPIC_SPENDING not in topics and TOPIC_LIMITS not in topics):
        if TOPIC_BALANCES not in topics:
            topics.insert(0, TOPIC_BALANCES)
    who: list[str] = []
    if addons:
        who.append(WHO_ADDONS)
    if agentish and not addons:
        who.append(WHO_AGENT)
    names = tuple(sorted({word for word in ("main", "burner", "everyday") if re.search(rf"\b{word}\b", lowered)}))
    if names and not who:
        who.append(WHO_OWNER)
    chain_key = ""
    for word, key in _CHAIN_WORDS.items():
        if re.search(rf"\b(?:on|in|across)\s+(?:the\s+)?{re.escape(word)}\b|\b{re.escape(word)}\s+(?:wallet|network|chain|side)\b", lowered):
            chain_key = key
            break
    other_chain = ""
    for word, name in _OTHER_CHAINS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            other_chain = name
            break
    coin = ""
    found = re.search(r"\b(sol|usdc|usdt|eth|bnb)\b", lowered)
    if found and not (found.group(1) in ("sol", "eth", "bnb") and chain_key and re.search(rf"\b(?:on|in)\s+(?:the\s+)?{found.group(1)}\b", lowered)):
        coin = found.group(1).upper()
    period = ""
    for name, pattern in _PERIODS:
        if pattern.search(cleaned):
            period = name
            break
    unasked = bool(re.search(r"\bwithout\s+(?:asking|checking\s+with|telling)\b|\bon\s+(?:its|your)\s+own\b|\bby\s+(?:itself|yourself)\b", lowered))
    hypo = _HYPOTHETICAL_SEND_RE.search(cleaned)
    return MoneyAsk(topics=tuple(topics), who=tuple(who), wallet_names=names, chain_key=chain_key, other_chain=other_chain, coin=coin, period=period,
                    unasked_only=unasked, hypothetical=(hypo.group("amount"), hypo.group("coin").upper()) if hypo else ())


# --------------------------------------------------------------------------------------------------------------------
# Reading the wallet
# --------------------------------------------------------------------------------------------------------------------


@dataclass
class _Wallet:
    wallet_id: str
    who: str
    name: str
    kind: str
    network: str
    network_name: str
    chain_key: str
    address: str
    holdings: list[dict[str, Any]] = field(default_factory=list)  # {"asset", "decimals", "minor": int|None, "human"}

    @property
    def title(self) -> str:
        return f"{self.name} ({self.network_name})"


def _assistant_name(agent_id: str) -> str:
    from core.wallet import addon_wallet, agent_pay

    if agent_id == addon_wallet.ADDONS:
        return "Skills & plugins wallet"
    if agent_id == agent_pay.ASSISTANT_AGENT:
        return "Agent wallet"
    return f"Agent wallet ({agent_id})"


def _wallets(active: str) -> list[_Wallet]:
    from core.wallet import addon_wallet, chains, custody

    found = []
    for entry in custody.list_wallets():
        try:
            spec = chains.resolve_network(str(entry.get("network") or ""))
        except Exception:
            continue
        if spec.environment != active or str(entry.get("setup_state") or "") in ("cancelled", "removed"):
            continue
        agent_id = str(entry.get("agent_id") or "")
        who = WHO_OWNER if not agent_id else (WHO_ADDONS if agent_id == addon_wallet.ADDONS else WHO_AGENT)
        name = str(entry.get("label") or "").strip() if who == WHO_OWNER else _assistant_name(agent_id)
        found.append(_Wallet(wallet_id=str(entry["wallet_id"]), who=who, name=name or "Wallet", kind=str(entry.get("kind") or ""), network=spec.network,
                             network_name=spec.display_name, chain_key=spec.chain_key, address=str(entry.get("public_key") or "")))
    order = {WHO_OWNER: 0, WHO_AGENT: 1, WHO_ADDONS: 2}
    kind_order = {"main": 0, "everyday": 1, "burner": 2}
    chain_order = {key: index for index, key in enumerate(chains.CHAIN_KEY_ORDER)}
    found.sort(key=lambda w: (order[w.who], kind_order.get(w.kind, 1), chain_order.get(w.chain_key, 99), w.name.lower()))
    return found


def _read_holdings(wallets: list[_Wallet], *, source_context: dict[str, Any] | None) -> None:
    """Fill each wallet's coin and every registered token of its row, read from the row's endpoint."""
    from concurrent.futures import ThreadPoolExecutor

    from core.wallet import balances, chains

    def read(wallet: _Wallet) -> list[dict[str, Any]]:
        spec = chains.resolve_network(wallet.network)
        native = chains.native_asset(spec.network)
        native_symbol = spec.native_display_symbol or native.symbol
        rows: list[dict[str, Any]] = []
        try:
            answer = balances.read_balance(wallet.wallet_id, source_context=source_context)
        except Exception:
            answer = {"state": "unavailable"}
        rows.append({"asset": native_symbol, "decimals": int(native.decimals), "minor": int(answer["balance_minor"]) if answer.get("state") == balances.STATE_READ else None})
        tokens = tuple(symbol for symbol, asset in spec.assets.items() if not asset.native)
        if tokens:
            try:
                token_answers = balances.read_token_balances(wallet.wallet_id, symbols=tokens, source_context=source_context)
            except Exception:
                token_answers = [{"asset": symbol, "state": "unavailable"} for symbol in tokens]
            for token_answer in token_answers:
                symbol = str(token_answer.get("asset") or "")
                if symbol not in spec.assets:
                    continue  # a coin this network does not register has no known decimals: never print its raw count
                decimals = int(spec.assets[symbol].decimals)
                rows.append({"asset": symbol, "decimals": decimals, "minor": int(token_answer["balance_minor"]) if token_answer.get("state") == balances.STATE_READ else None})
        return rows

    if not wallets:
        return
    with ThreadPoolExecutor(max_workers=min(6, len(wallets))) as pool:
        for wallet, rows in zip(wallets, pool.map(read, wallets), strict=True):
            wallet.holdings = rows


def _amount(minor: int, decimals: int, symbol: str) -> str:
    from core.wallet import amounts

    return f"{amounts.format_minor(int(minor), int(decimals))} {symbol}"


def _proposal_amount(proposal: Any) -> tuple[str, int, int]:
    """(symbol, decimals, minor) of a proposal, in the coin's own units; ('', 0, 0) when its asset is not registered."""
    from core.wallet import chains

    try:
        spec = chains.resolve_network(proposal.network)
        asset = chains.asset_for(spec.network, proposal.asset)
        symbol = asset.symbol
        if asset.native:
            symbol = spec.native_display_symbol or asset.symbol
        return symbol, int(asset.decimals), int(proposal.amount_minor)
    except Exception:
        return "", 0, 0


def _when(moment: datetime, now: datetime) -> str:
    day = moment.date()
    if day == now.date():
        label = "today"
    elif day == (now - timedelta(days=1)).date():
        label = "yesterday"
    else:
        label = f"{moment.strftime('%b')} {moment.day}"
    return f"{label} at {moment.strftime('%H:%M')}"


def _local(value: Any) -> datetime | None:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value)).astimezone()
        text = str(value or "").strip()
        if not text:
            return None
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone()
    except (ValueError, OSError, OverflowError):
        return None


def _window(period: str, now: datetime) -> tuple[datetime | None, datetime | None, str]:
    """(start, end, words) of a period in the owner's local time; (None, None, "") is all time."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return midnight, None, "today"
    if period == "yesterday":
        start = midnight - timedelta(days=1)
        return start, midnight, f"yesterday ({start.strftime('%b')} {start.day})"
    if period == "last_day":
        return now - timedelta(hours=24), None, "in the last 24 hours"
    if period == "this_week":
        return midnight - timedelta(days=now.weekday()), None, "this week"
    if period == "last_week":
        return now - timedelta(days=7), None, "in the last 7 days"
    if period == "this_month":
        return midnight.replace(day=1), None, "this month"
    if period == "last_month":
        return now - timedelta(days=30), None, "in the last 30 days"
    return None, None, ""


def _short(address: str) -> str:
    text = str(address or "")
    return text if len(text) <= 14 else f"{text[:6]}…{text[-4:]}"


def _paid_for(proposal: Any) -> str:
    """What a payment paid for, from the operation's typed record (never the memo of a payment the owner did not
    type). The service's own address, not a verified merchant identity."""
    from urllib.parse import urlsplit

    from core.wallet import proposals, purpose

    try:
        view = purpose.purpose_for(proposal)
    except Exception:
        view = {}
    if view.get("kind") == "service" or view.get("provider"):
        provider = str(view.get("provider") or "a service")
        resource = str(view.get("resource") or "")
        path = urlsplit(resource).path if resource else ""
        description = str(view.get("description") or "").strip()
        words = f"to {provider}"
        if description:
            words += f" for “{description[:80]}”"
        elif path and path != "/":
            words += f" for {path[:80]}"
        return words
    recipient = dict(getattr(proposal, "recipient", {}) or {})
    named = str(recipient.get("display_name") or recipient.get("label") or "").strip()
    words = f"to {named} ({_short(proposal.destination)})" if named else f"to {_short(proposal.destination)}"
    if str(getattr(proposal, "origin", "")) == proposals.ORIGIN_USER and str(getattr(proposal, "memo", "") or "").strip():
        words += f", note “{str(proposal.memo).strip()[:60]}”"
    return words


def _sum_line(rows: list[tuple[str, int, int]]) -> str:
    totals: dict[tuple[str, int], int] = {}
    for symbol, decimals, minor in rows:
        if symbol:
            totals[(symbol, decimals)] = totals.get((symbol, decimals), 0) + int(minor)
    return ", ".join(_amount(minor, decimals, symbol) for (symbol, decimals), minor in sorted(totals.items()))


# --------------------------------------------------------------------------------------------------------------------
# The answer
# --------------------------------------------------------------------------------------------------------------------


@dataclass
class MoneyAnswer:
    text: str
    topics: tuple[str, ...]
    state: str  # "answered" | "wallet_off" | "no_wallets"
    facts: dict[str, Any] = field(default_factory=dict)


def answer(text: str, *, source_context: dict[str, Any] | None = None, now: float | None = None, topics: tuple[str, ...] = ()) -> MoneyAnswer:
    """The answer to a money question, from the wallet's own records. Read only: nothing is proposed, paid or changed."""
    from core.wallet import chains, config, environment

    ask = read_ask(text)
    if topics:
        ask = MoneyAsk(**{**ask.__dict__, "topics": tuple(t for t in topics if t in TOPICS) or ask.topics})
    moment = datetime.fromtimestamp(float(now if now is not None else time.time())).astimezone()
    if not config.wallet_enabled():
        return MoneyAnswer(
            text=("Crypto is switched off in VOOL, so there are no wallets, balances or payments for me to read. Nothing was read and nothing was paid. "
                  f"You can turn it on in {SETTINGS_PLACE}."),
            topics=ask.topics, state="wallet_off")
    active = environment.active_environment().environment
    where = "Mainnet" if active == chains.ENVIRONMENT_MAINNET else "test networks"
    wallets = _wallets(active)
    if not wallets:
        other = "test networks" if active == chains.ENVIRONMENT_MAINNET else "Mainnet"
        try:
            elsewhere = any(chains.resolve_network(str(w.get("network") or "")).environment != active
                            for w in __import__("core.wallet.custody", fromlist=["list_wallets"]).list_wallets())
        except Exception:
            elsewhere = False
        lines = [f"You don't have a wallet on {where} yet, so there are no balances or payments to show. Nothing was read and nothing was paid."]
        if elsewhere:
            lines.append(f"You do have a wallet on {other}; VOOL is set to {where} right now ({SETTINGS_PLACE}).")
        else:
            lines.append(f"You can make one in {SETTINGS_PLACE}.")
        return MoneyAnswer(text=" ".join(lines), topics=ask.topics, state="no_wallets")
    sections: list[str] = []
    facts: dict[str, Any] = {"environment": active}
    if ask.other_chain and TOPIC_BALANCES in ask.topics:
        networks = ", ".join(dict.fromkeys(w.network_name for w in wallets))
        sections.append(f"VOOL has no wallet on {ask.other_chain}, so there is nothing of yours there that I can read. Your wallets are on {networks}.")
        facts["other_chain"] = ask.other_chain
        ask = MoneyAsk(**{**ask.__dict__, "topics": tuple(t for t in ask.topics if t != TOPIC_BALANCES)})
    for topic in ask.topics:
        if topic == TOPIC_BALANCES:
            sections.append(_balances_section(ask, wallets, where, facts, source_context=source_context))
        elif topic == TOPIC_SPENDING:
            sections.append(_spending_section(ask, wallets, moment, facts, source_context=source_context))
        elif topic == TOPIC_LIMITS:
            sections.append(_limits_section(ask, wallets, moment, facts))
        elif topic == TOPIC_WAITING:
            sections.append(_waiting_section(ask, wallets, moment, facts))
        elif topic == TOPIC_HISTORY:
            sections.append(_history_section(ask, wallets, moment, facts))
        elif topic == TOPIC_ADDRESS:
            sections.append(_address_section(ask, wallets))
    if ask.hypothetical and TOPIC_BALANCES in ask.topics:
        sections.append("Nothing was sent or prepared: this only reads your wallets.")
    body = "\n\n".join(section for section in sections if section)
    return MoneyAnswer(text=body or "I could not tell which part of your wallets you asked about.", topics=ask.topics, state="answered", facts=facts)


def _pick(ask: MoneyAsk, wallets: list[_Wallet]) -> list[_Wallet]:
    chosen = wallets
    if ask.who:
        chosen = [w for w in chosen if w.who in ask.who]
    if ask.wallet_names:
        chosen = [w for w in chosen if w.kind in ask.wallet_names or w.name.lower() in ask.wallet_names]
    if ask.chain_key:
        chosen = [w for w in chosen if w.chain_key == ask.chain_key]
    return chosen


def _balances_section(ask: MoneyAsk, wallets: list[_Wallet], where: str, facts: dict[str, Any], *, source_context: dict[str, Any] | None) -> str:
    chosen = _pick(ask, wallets)
    if not chosen:
        if ask.chain_key:
            names = ", ".join(dict.fromkeys(w.network_name for w in wallets))
            return f"You have no wallet on {ask.chain_key.capitalize()} on {where}. Your wallets are on {names}."
        if WHO_ADDONS in ask.who:
            return f"There is no Skills & plugins wallet yet, so plugins pay nothing on their own. You can make one in {SETTINGS_PLACE}, Agent spending."
        if WHO_AGENT in ask.who:
            return f"The assistant has no wallet of its own yet, so it pays nothing on its own: every payment asks you. You can give it one in {SETTINGS_PLACE}, Agent spending."
        if ask.wallet_names:
            named = " or ".join(name.capitalize() for name in ask.wallet_names)
            mine = ", ".join(w.title for w in wallets if w.who == WHO_OWNER)
            return f"You have no {named} wallet on {where}." + (f" Your wallets: {mine}." if mine else "")
        return "None of your wallets matches that."
    if ask.coin:
        # a coin question reads only the wallets whose network carries that coin ("how much SOL" is not asked of Base)
        from core.wallet import chains

        def carries(wallet: _Wallet) -> bool:
            spec = chains.resolve_network(wallet.network)
            symbols = {symbol.upper() for symbol in spec.assets} | {str(spec.native_display_symbol or "").upper()}
            return ask.coin in symbols

        carrying = [w for w in chosen if carries(w)]
        if not carrying:
            return f"None of your wallets{' there' if ask.chain_key else ''} is on a network that holds {ask.coin}."
        chosen = carrying
    _read_holdings(chosen, source_context=source_context)
    lines = [f"Your wallets on {where}:" if len(chosen) > 1 else f"On {where}:"]
    read_rows: list[tuple[str, int, int]] = []
    unknown: list[str] = []
    for wallet in chosen:
        held = [h for h in wallet.holdings if h["minor"] is not None and h["minor"] > 0 and (not ask.coin or h["asset"].upper() == ask.coin)]
        failed = [h for h in wallet.holdings if h["minor"] is None and (not ask.coin or h["asset"].upper() == ask.coin)]
        parts = [_amount(h["minor"], h["decimals"], h["asset"]) for h in held]
        if failed:
            parts.append("could not read " + ", ".join(h["asset"] for h in failed) + " just now (unknown, not zero)")
            unknown.append(wallet.title)
        if not parts:
            parts = [f"no {ask.coin}" if ask.coin else "empty"]
        lines.append(f"- {wallet.title}: {', '.join(parts)}")
        read_rows.extend((h["asset"], h["decimals"], h["minor"]) for h in held)
    total = _sum_line(read_rows)
    if len(chosen) > 1:
        lines.append(f"Together: {total}." if total else "Together: nothing that could be read.")
        if unknown:
            lines.append("The total leaves out what could not be read just now.")
    if ask.hypothetical:
        amount_text, coin = ask.hypothetical
        source = next((w for w in chosen if w.who == WHO_OWNER and w.kind == "main" and any(h["asset"].upper() == coin for h in w.holdings)), None)
        held = next((h for h in (source.holdings if source else []) if h["asset"].upper() == coin), None)
        if source is not None and held is not None and held["minor"] is not None:
            from core.wallet import amounts

            try:
                wanted = amounts.parse_human_amount(amount_text, decimals=held["decimals"], symbol=coin)
            except Exception:
                wanted = None
            if wanted is not None:
                left = held["minor"] - int(wanted)
                if left >= 0:
                    lines.append(f"After sending {amount_text} {coin}, {source.title} would have {_amount(left, held['decimals'], held['asset'])} left, before the network fee.")
                else:
                    lines.append(f"{source.title} holds less than {amount_text} {coin}, so that send would not go through.")
    facts["balances"] = [{"wallet": w.title, "who": w.who, "holdings": [{"asset": h["asset"], "read": h["minor"] is not None,
                                                                          "amount": _amount(h["minor"], h["decimals"], h["asset"]) if h["minor"] is not None else ""} for h in w.holdings]}
                         for w in chosen]
    return "\n".join(lines)


def _agent_payments(wallets: list[_Wallet], who: tuple[str, ...]) -> tuple[list[Any], dict[str, _Wallet]]:
    from core.wallet import proposals

    by_id = {w.wallet_id: w for w in wallets if w.who in (who or (WHO_AGENT, WHO_ADDONS))}
    rows = [p for p in proposals.list_proposals(limit=500) if p.wallet_id in by_id]
    return rows, by_id


def _decisions_by_proposal() -> dict[str, dict[str, Any]]:
    from core.wallet import allowances

    found: dict[str, dict[str, Any]] = {}
    try:
        for entry in allowances.decisions(limit=1000):
            if entry.get("proposal_id"):
                found.setdefault(str(entry["proposal_id"]), entry)
    except Exception:
        return {}
    return found


def _spending_section(ask: MoneyAsk, wallets: list[_Wallet], now: datetime, facts: dict[str, Any], *, source_context: dict[str, Any] | None) -> str:
    from core.wallet import agent_pay, allowances, proposals

    who = tuple(w for w in ask.who if w in (WHO_AGENT, WHO_ADDONS)) or (WHO_AGENT, WHO_ADDONS)
    agent_wallets = [w for w in wallets if w.who in who]
    subject = "Plugins" if who == (WHO_ADDONS,) else "The assistant"
    if not agent_wallets:
        if who == (WHO_ADDONS,):
            return f"There is no Skills & plugins wallet, so plugins have spent nothing on their own. You can make one in {SETTINGS_PLACE}, Agent spending."
        return f"The assistant has no wallet of its own, so it has spent nothing on its own: every payment asks you first. You can give it one in {SETTINGS_PLACE}, Agent spending."
    start, end, words = _window(ask.period, now)
    rows, by_id = _agent_payments(wallets, who)
    decided = _decisions_by_proposal()

    def inside(p: Any) -> bool:
        moment = _local(p.created_at)
        return moment is not None and (start is None or moment >= start) and (end is None or moment < end)

    in_window = [p for p in rows if inside(p)]
    paid = [p for p in in_window if p.state == proposals.STATE_CONFIRMED]
    if ask.unasked_only:
        paid = [p for p in paid if (decided.get(p.proposal_id) or {}).get("decision") == allowances.DECISION_PAY]
    moving = [p for p in in_window if p.state in (proposals.STATE_APPROVED, proposals.STATE_SIGNED, proposals.STATE_BROADCAST, proposals.STATE_AWAITING_SIGNATURE)]
    failed = [p for p in in_window if p.state == proposals.STATE_FAILED]
    lines: list[str] = []
    span = words or "so far"
    if paid:
        total = _sum_line([_proposal_amount(p) for p in paid])
        count = f"{len(paid)} payment" + ("s" if len(paid) != 1 else "")
        lead = f"{subject} spent {total} {span}, in {count}" + (" it made without asking you" if ask.unasked_only else "") + ":"
        lines.append(lead)
        for p in paid[:15]:
            symbol, decimals, minor = _proposal_amount(p)
            moment = _local(p.created_at)
            wallet = by_id.get(p.wallet_id)
            decision = decided.get(p.proposal_id) or {}
            how = "paid inside the allowance you set" if decision.get("decision") == allowances.DECISION_PAY else "you approved it"
            from_words = f" from the {wallet.name}" if wallet is not None and len(agent_wallets) > 1 else ""
            lines.append(f"- {_when(moment, now) if moment else 'at an unknown time'}: {_amount(minor, decimals, symbol)}{from_words} {_paid_for(p)} ({how})")
        if len(paid) > 15:
            lines.append(f"- and {len(paid) - 15} more")
    else:
        none_words = "nothing without asking you" if ask.unasked_only else "nothing"
        lines.append(f"{subject} spent {none_words} {span}.")
        earlier = [p for p in rows if p.state == proposals.STATE_CONFIRMED and not inside(p)]
        if words and earlier:
            last = earlier[0]
            symbol, decimals, minor = _proposal_amount(last)
            moment = _local(last.created_at)
            lines.append(f"Its last payment was {_when(moment, now) if moment else 'at an unknown time'}: {_amount(minor, decimals, symbol)} {_paid_for(last)}.")
    if moving:
        lines.append(f"Still on its way: {_sum_line([_proposal_amount(p) for p in moving])} ({len(moving)} payment{'s' if len(moving) != 1 else ''}, not confirmed yet).")
    if failed:
        lines.append(f"Did not go through: {len(failed)} payment{'s' if len(failed) != 1 else ''}; nothing left the wallet for {'them' if len(failed) != 1 else 'it'}.")
    # requests that were not paid on their own in the same window: asked the owner, or refused, and why
    try:
        decisions = [d for d in allowances.decisions(limit=500) if d.get("decision") != allowances.DECISION_PAY
                     and (d.get("agent_id") == agent_pay.ASSISTANT_AGENT if who == (WHO_AGENT,) else (allowances.is_addon_agent(str(d.get("agent_id") or "")) if who == (WHO_ADDONS,) else True))]
    except Exception:
        decisions = []
    held = [d for d in decisions if (lambda m: m is not None and (start is None or m >= start) and (end is None or m < end))(_local(d.get("created_at")))]
    if held:
        reasons = Counter(agent_pay.explain(str(d.get("reason") or "")) for d in held)
        lines.append(f"Not paid on its own {span}: {len(held)} request{'s' if len(held) != 1 else ''}. " + " ".join(f"{reason}" + (f" (x{n})" if n > 1 else "") for reason, n in reasons.most_common(3)))
    if not ask.unasked_only:
        left = _allowance_left_lines(agent_wallets, now, short=True)
        if left:
            lines.extend(left)
    facts["spending"] = {"period": ask.period or "all", "paid": [p.proposal_id for p in paid], "in_flight": [p.proposal_id for p in moving], "failed": [p.proposal_id for p in failed]}
    return "\n".join(lines)


#: an allowance's period is a ROLLING window (core.wallet.allowances: spends since now minus the window), said so
_ROLLING = {"hour": "per hour (rolling)", "day": "per 24 hours (rolling)", "week": "per 7 days (rolling)", "month": "per 30 days (rolling)"}


def _allowance_views(agent_wallets: list[_Wallet], now: datetime) -> list[tuple[_Wallet, dict[str, Any]]]:
    from core.wallet import allowances

    ids = {w.wallet_id: w for w in agent_wallets}
    found = []
    for view in allowances.list_allowances(now=now.timestamp()):
        if view.get("parent_id") or view.get("unreadable") or view.get("wallet_id") not in ids:
            continue
        found.append((ids[view["wallet_id"]], view))
    return found


def _allowance_left_lines(agent_wallets: list[_Wallet], now: datetime, *, short: bool) -> list[str]:
    from core.wallet import addon_wallet, agent_settings, chains

    lines = []
    for wallet, view in _allowance_views(agent_wallets, now):
        shown = agent_settings._allowance_shown(view, now=now.timestamp())
        try:
            asset = chains.asset_for(view["network"], view["asset"])
            decimals = int(asset.decimals)
        except Exception:
            continue
        coin = view["asset"]
        window_left = max(0, int(view["window_minor"]) - int(view["window_used_minor"]))
        total_left = max(0, int(view["total_minor"]) - int(view["total_used_minor"]))
        can_now = min(window_left, total_left)
        span = _ROLLING.get(shown["period"], f"per {shown['period']}")
        whose = ""
        if wallet.who == WHO_ADDONS:
            whose = f" for {addon_wallet.name_of(view['agent_id'])}"
        state = view.get("state")
        if state != "active":
            stopped = {"paused": "is paused", "revoked": "has ended", "expired": "has ended"}.get(str(state), f"is {state}")
            lines.append(f"Its allowance{whose} {stopped}, so it can't pay anything on its own right now.")
            continue
        if short:
            lines.append(f"Right now it can spend {_amount(can_now, decimals, coin)} more on its own{whose} before it has to ask you "
                         f"({_amount(window_left, decimals, coin)} of its {shown['period_amount']} {coin} {span} limit left, {_amount(total_left, decimals, coin)} of its {shown['total']} {coin} total).")
    return lines


def _limits_section(ask: MoneyAsk, wallets: list[_Wallet], now: datetime, facts: dict[str, Any]) -> str:
    from core.wallet import addon_wallet, agent_settings, chains, limits

    lines: list[str] = []
    try:
        frozen = bool(limits.is_frozen())
    except Exception:
        frozen = True
    if frozen:
        lines.append(f"Spending is frozen right now: nothing can be paid from any wallet until you unfreeze it in {SETTINGS_PLACE}.")
    want_agents = not ask.who or any(w in ask.who for w in (WHO_AGENT, WHO_ADDONS))
    want_owner = not ask.who or WHO_OWNER in ask.who
    if want_agents:
        agent_wallets = [w for w in wallets if w.who in ((WHO_AGENT, WHO_ADDONS) if not ask.who else tuple(x for x in ask.who if x != WHO_OWNER))]
        views = _allowance_views(agent_wallets, now)
        shown_any = False
        for wallet, view in views:
            shown = agent_settings._allowance_shown(view, now=now.timestamp())
            try:
                asset = chains.asset_for(view["network"], view["asset"])
                decimals = int(asset.decimals)
            except Exception:
                lines.append(f"{wallet.title}: its allowance could not be read, so it pays nothing on its own.")
                continue
            coin = view["asset"]
            period = shown["period"]
            whose = wallet.name if wallet.who == WHO_AGENT else f"{wallet.name}, {addon_wallet.name_of(view['agent_id'])}"
            window_left = max(0, int(view["window_minor"]) - int(view["window_used_minor"]))
            total_left = max(0, int(view["total_minor"]) - int(view["total_used_minor"]))
            payments_left = max(0, int(view["max_payments"]) - int(view["payments"]))
            state = str(view.get("state") or "")
            head = f"{whose} ({wallet.network_name}) can pay on its own, inside these limits you set:"
            if state != "active":
                head = f"{whose} ({wallet.network_name}): its allowance is {'paused' if state == 'paused' else 'ended'}, so it pays nothing on its own now. Its limits:"
            lines.append(head)
            lines.append(f"- Up to {shown['per_payment']} {coin} for one payment")
            span = _ROLLING.get(period, f"per {period}")
            lines.append(f"- {shown['period_amount']} {coin} {span}: {shown['period_used']} {coin} used, {_amount(window_left, decimals, coin)} left")
            lines.append(f"- {shown['total']} {coin} in total: {shown['total_used']} {coin} used, {_amount(total_left, decimals, coin)} left")
            lines.append(f"- {view['max_payments']} payments: {view['payments']} made, {payments_left} left")
            if shown.get("ask_above"):
                lines.append(f"- It asks you first for anything above {shown['ask_above']} {coin}")
            services = ", ".join(s["address"].split("://", 1)[-1] for s in shown.get("services") or [])
            if services:
                lines.append(f"- Only these services: {services}")
            lines.append(f"- {shown['days_left']} days left before the allowance ends")
            if state == "active":
                lines.append(f"Right now it can spend {_amount(min(window_left, total_left), decimals, coin)} more on its own before it has to ask you.")
            shown_any = True
        if not shown_any:
            if agent_wallets:
                lines.append(f"{'Plugins have' if ask.who == (WHO_ADDONS,) else 'The assistant has'} no spending allowance, so nothing is paid on its own: every payment asks you first. "
                             f"You can set one in {SETTINGS_PLACE}, Agent spending.")
            elif ask.who:
                lines.append(f"{'There is no Skills & plugins wallet' if ask.who == (WHO_ADDONS,) else 'The assistant has no wallet of its own'}, so nothing is paid on its own: "
                             f"every payment asks you first. You can set this up in {SETTINGS_PLACE}, Agent spending.")
        facts["allowances"] = [view["allowance_id"] for _wallet, view in views]
    if want_owner:
        owner = [w for w in _pick(MoneyAsk(topics=(), who=(WHO_OWNER,), wallet_names=ask.wallet_names, chain_key=ask.chain_key), wallets)]
        if owner:
            lines.append("Your own wallets never pay without your approval. Their caps per approval:")
            for wallet in owner:
                spec = chains.resolve_network(wallet.network)
                parts = []
                for symbol, asset in spec.assets.items():
                    if ask.coin and symbol.upper() != ask.coin:
                        continue
                    try:
                        cap = limits.load_limits(wallet.wallet_id, symbol)
                        used = limits.spent_today(wallet.wallet_id, symbol, chain=spec.network)
                    except Exception:
                        continue
                    shown_symbol = (spec.native_display_symbol or symbol) if asset.native else symbol
                    parts.append(f"{_amount(cap.per_tx_minor, asset.decimals, shown_symbol)} a payment, {_amount(cap.daily_minor, asset.decimals, shown_symbol)} a day "
                                 f"({_amount(used, asset.decimals, shown_symbol)} used today)")
                if parts:
                    lines.append(f"- {wallet.title}: " + "; ".join(parts))
    return "\n".join(lines) if lines else "There are no limits to show."


def _waiting_section(ask: MoneyAsk, wallets: list[_Wallet], now: datetime, facts: dict[str, Any]) -> str:
    from core.wallet import proposals

    by_id = {w.wallet_id: w for w in wallets}
    waiting = [p for p in proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL, limit=200) if p.wallet_id in by_id]
    if ask.who:
        waiting = [p for p in waiting if by_id[p.wallet_id].who in ask.who]
    facts["waiting"] = [p.proposal_id for p in waiting]
    if not waiting:
        return "Nothing is waiting for your approval. No payment is pending."
    count = f"{len(waiting)} payment{'s are' if len(waiting) != 1 else ' is'}"
    lines = [f"{count} waiting for your approval. Nothing is paid until you approve:"]
    for p in waiting[:10]:
        symbol, decimals, minor = _proposal_amount(p)
        wallet = by_id[p.wallet_id]
        moment = _local(p.created_at)
        lines.append(f"- {_amount(minor, decimals, symbol)} from {wallet.title} {_paid_for(p)}, asked {_when(moment, now) if moment else 'at an unknown time'}")
    lines.append("You can review each one on its card in chat, or decline it there.")
    return "\n".join(lines)


def _history_section(ask: MoneyAsk, wallets: list[_Wallet], now: datetime, facts: dict[str, Any]) -> str:
    from core.wallet import proposals

    by_id = {w.wallet_id: w for w in _pick(MoneyAsk(topics=(), who=ask.who, wallet_names=ask.wallet_names, chain_key=ask.chain_key), wallets)}
    start, end, words = _window(ask.period, now)
    done_states = (proposals.STATE_CONFIRMED, proposals.STATE_FAILED, proposals.STATE_BROADCAST, proposals.STATE_SIGNED, proposals.STATE_APPROVED)
    rows = []
    for p in proposals.list_proposals(limit=500):
        if p.wallet_id not in by_id or p.state not in done_states:
            continue
        moment = _local(p.created_at)
        if moment is None or (start is not None and moment < start) or (end is not None and moment >= end):
            continue
        rows.append((p, moment))
    facts["history"] = [p.proposal_id for p, _m in rows[:10]]
    if not rows:
        narrowed = bool(ask.who or ask.wallet_names or ask.chain_key)
        if narrowed and not by_id:
            return "None of your wallets matches that."
        scope = " or ".join(dict.fromkeys(w.title for w in by_id.values())) if narrowed else "your wallets"
        line = f"No payments {words} from {scope}." if words else f"No payments have been made from {scope} yet."
        waiting = sum(1 for p in proposals.list_proposals(limit=500) if p.wallet_id in by_id and p.state == proposals.STATE_PENDING_APPROVAL)
        if waiting:
            line += f" {waiting} payment{'s' if waiting != 1 else ''} from {'it' if len(by_id) == 1 else 'them'} {'is' if waiting == 1 else 'are'} waiting for your approval; nothing is paid until you approve."
        return line
    state_words = {proposals.STATE_CONFIRMED: "done", proposals.STATE_FAILED: "did not go through, nothing left the wallet"}
    lines = [f"Your latest payments{(' ' + words) if words else ''}, newest first:"]
    for p, moment in rows[:10]:
        symbol, decimals, minor = _proposal_amount(p)
        wallet = by_id[p.wallet_id]
        lines.append(f"- {_when(moment, now)}: {_amount(minor, decimals, symbol)} from {wallet.title} {_paid_for(p)} ({state_words.get(p.state, 'on its way, not confirmed yet')})")
    if len(rows) > 10:
        lines.append(f"- and {len(rows) - 10} more")
    return "\n".join(lines)


def _address_section(ask: MoneyAsk, wallets: list[_Wallet]) -> str:
    chosen = _pick(ask, wallets)
    if not chosen:
        return "None of your wallets matches that."
    lines = ["Addresses to receive at (public, safe to share):"]
    lines += [f"- {w.title}: {w.address}" for w in chosen if w.address]
    return "\n".join(lines)


__all__ = ["ASK_TEXT_KEY", "TOPICS", "MoneyAnswer", "MoneyAsk", "answer", "is_money_question", "may_be_about_money", "read_ask"]
