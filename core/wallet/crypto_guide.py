"""VOOL's built-in crypto guide: how wallets, Solana, Ethereum-style chains, stablecoins, fees, conversions and scams
work, in plain words, with links to the sources that publish them. Offline: nothing here fetches anything, so the
assistant can explain crypto without opening a browser, and the links are a fixed, vetted list (never a search result).

Each topic says what VOOL itself does where that matters (Solana Mainnet first, a PIN for every payment, caps,
receipts, Contacts), and only what VOOL has. Read-only: nothing here can approve, sign or move funds.

Links were checked on 13 Nov 2026. Sites that turn away automated visitors (solana.com, the block explorers) are the
projects' own well-known addresses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

AUTHORITY = "core.wallet.crypto_guide"

#: the only places the guide points to: each project's own site, or a public authority
SOURCES: dict[str, tuple[str, str]] = {
    "solana_docs": ("Solana documentation", "https://solana.com/docs"),
    "solana_learn": ("Solana: learn", "https://solana.com/learn"),
    "solana_fees": ("Solana documentation: fees", "https://solana.com/docs/core/fees"),
    "solana_accounts": ("Solana documentation: accounts", "https://solana.com/docs/core/accounts"),
    "solana_transactions": ("Solana documentation: transactions", "https://solana.com/docs/core/transactions"),
    "solana_staking": ("Solana: staking", "https://solana.com/staking"),
    "ethereum_wallets": ("ethereum.org: wallets", "https://ethereum.org/en/wallets/"),
    "ethereum_gas": ("ethereum.org: gas and fees", "https://ethereum.org/en/developers/docs/gas/"),
    "ethereum_networks": ("ethereum.org: networks", "https://ethereum.org/en/developers/docs/networks/"),
    "ethereum_bridges": ("ethereum.org: bridges", "https://ethereum.org/en/bridges/"),
    "ethereum_security": ("ethereum.org: staying safe", "https://ethereum.org/en/security/"),
    "ethereum_staking": ("ethereum.org: staking", "https://ethereum.org/en/staking/"),
    "base_docs": ("Base documentation", "https://docs.base.org"),
    "bnb_docs": ("BNB Chain documentation", "https://docs.bnbchain.org"),
    "arbitrum_docs": ("Arbitrum documentation", "https://docs.arbitrum.io"),
    "circle_usdc": ("Circle: USDC", "https://www.circle.com/usdc"),
    "circle_multichain": ("Circle: USDC on many chains", "https://www.circle.com/multi-chain-usdc"),
    "tether": ("Tether (USDT)", "https://tether.to/en/"),
    "jupiter_docs": ("Jupiter documentation", "https://dev.jup.ag/docs"),
    "solana_explorer": ("Solana Explorer", "https://explorer.solana.com"),
    "solscan": ("Solscan", "https://solscan.io"),
    "etherscan": ("Etherscan", "https://etherscan.io"),
    "basescan": ("Basescan", "https://basescan.org"),
    "bscscan": ("BscScan", "https://bscscan.com"),
    "arbiscan": ("Arbiscan", "https://arbiscan.io"),
    "x402": ("x402: payments over HTTP", "https://www.x402.org"),
    "ic3": ("FBI Internet Crime Complaint Center (IC3)", "https://www.ic3.gov"),
}


@dataclass(frozen=True)
class Topic:
    topic_id: str
    title: str
    keywords: tuple[str, ...]
    text: str
    sources: tuple[str, ...]


TOPICS: tuple[Topic, ...] = (
    Topic(
        "wallet", "What a crypto wallet is",
        ("wallet", "wallets", "self custody", "custody", "private key", "public key", "address", "keys", "non custodial", "how does a wallet work"),
        "A crypto wallet does not hold coins. The coins live on the blockchain; the wallet holds the secret key that lets you "
        "move them. Your address is public, like an account number: anyone can send to it and anyone can see its balance. "
        "The secret key (and the recovery phrase it comes from) is private: whoever has it controls the money. In VOOL the key "
        "stays sealed on your computer, every payment shows a card first, and nothing moves without your PIN.",
        ("ethereum_wallets", "solana_learn"),
    ),
    Topic(
        "recovery_phrase", "Recovery phrase (seed phrase) and backups",
        ("seed phrase", "recovery phrase", "secret phrase", "12 words", "24 words", "mnemonic", "backup", "back up", "lost my phrase", "restore"),
        "The recovery phrase (12 or 24 words) IS the wallet: it can rebuild every key on any device. Write it on paper, keep it "
        "offline and private, and never type it into a website, a chat or a 'support' form. Anyone who asks for it is stealing. "
        "If it is lost and the device breaks, nobody can recover the money, not VOOL and not the blockchain.",
        ("ethereum_security", "ethereum_wallets"),
    ),
    Topic(
        "solana", "What Solana is",
        ("solana", "sol", "what is solana", "lamport", "lamports", "slot", "validator", "validators", "spl"),
        "Solana is a public blockchain built for fast, cheap payments. Its own coin is SOL (1 SOL = 1,000,000,000 lamports), "
        "which pays the network fees. Blocks come about every 0.4 seconds and a normal payment costs a fraction of a cent. "
        "Other coins on Solana, like USDC, are tokens that each have a fixed mint address. Addresses are 32 to 44 letters and "
        "digits (base58). VOOL's wallet works on Solana Mainnet, the real network.",
        ("solana_learn", "solana_docs"),
    ),
    Topic(
        "solana_fees", "Fees on Solana",
        ("fee", "fees", "network fee", "solana fee", "priority fee", "how much does it cost", "transaction cost", "rent", "token account"),
        "A Solana payment pays a base fee of 5,000 lamports (0.000005 SOL) per signature, plus an optional priority fee "
        "when the network is busy. Sending a token to someone who has never held it also opens a token account for them, "
        "which costs a one-time deposit of about 0.00204 SOL (rent). VOOL shows the most the fee can be on the card before "
        "you approve, and the receipt keeps what was actually charged.",
        ("solana_fees", "solana_accounts"),
    ),
    Topic(
        "tokens_on_solana", "Tokens and token accounts on Solana",
        ("token", "tokens", "mint", "mint address", "token account", "associated token account", "ata", "spl token", "fake token", "same name token"),
        "On Solana every token is defined by its mint address, not its name: anyone can create a token called 'USDC', so only "
        "the official mint counts. Your wallet keeps each token in a separate token account tied to your address. VOOL only "
        "sends coins it knows by their official mint (SOL, USDC and USDT on Solana), so a look-alike token cannot slip in.",
        ("solana_accounts", "solana_docs"),
    ),
    Topic(
        "stablecoins", "USDC, USDT and stablecoins",
        ("usdc", "usdt", "tether", "circle", "stablecoin", "stablecoins", "stable coin", "dollar", "1 to 1", "peg", "depeg"),
        "A stablecoin is a token meant to stay worth one US dollar. USDC is issued by Circle and USDT by Tether; both say "
        "each coin is backed by reserves. USDC on Solana and USDC on Base are the same dollar but live on different "
        "networks: you can only send each on its own network. In VOOL you can send USDC and USDT on Solana from chat "
        "(\"send 10 USDC to Maya\"), and if you hold only SOL VOOL offers to convert first.",
        ("circle_usdc", "circle_multichain", "tether"),
    ),
    Topic(
        "evm", "Ethereum, Base, BNB Chain and Arbitrum",
        ("ethereum", "eth", "evm", "base", "bnb", "bnb chain", "bsc", "arbitrum", "layer 2", "l2", "gwei", "0x"),
        "Ethereum is the original smart-contract blockchain; its coin ETH pays the fees (gas). Base and Arbitrum are "
        "'layer 2' networks that settle on Ethereum and use ETH for gas at much lower cost; BNB Chain is a separate network "
        "that uses BNB. They all share the same address format (0x followed by 40 characters), so one address works on all of "
        "them, but the coins on each network are separate.",
        ("ethereum_networks", "base_docs", "bnb_docs", "arbitrum_docs"),
    ),
    Topic(
        "gas", "Gas fees on Ethereum-style chains",
        ("gas", "gas fee", "gas fees", "gas price", "gwei", "base fee", "tip", "eip 1559", "why is the fee so high", "out of gas"),
        "On Ethereum-style chains the fee is the gas the transaction uses times the gas price (a base fee that rises when the "
        "network is busy, plus a small tip). Prices are quoted in gwei, a billionth of an ETH. A plain transfer is cheap on "
        "Base and Arbitrum and can be costly on Ethereum itself. You always need a little of the network's own coin (ETH or "
        "BNB) to pay gas, even to send a token.",
        ("ethereum_gas",),
    ),
    Topic(
        "wrong_network", "Sending on the wrong network",
        ("wrong network", "wrong chain", "sent to wrong", "different chain", "which network", "cross chain", "lost coins", "exchange deposit"),
        "A coin sent on one network only arrives on that network. If you send USDC on Base to an exchange that only watches "
        "Solana, it does not show up there, and only the receiver can help. Always check which network the receiver "
        "supports. VOOL names the network on every payment card and refuses a payment on a network the address does not "
        "belong to.",
        ("ethereum_networks", "circle_multichain"),
    ),
    Topic(
        "swaps", "Converting one coin into another (swaps)",
        ("swap", "swaps", "convert", "conversion", "exchange", "dex", "jupiter", "slippage", "price impact", "minimum received", "aggregator"),
        "A swap trades one coin for another at a market price. On Solana, Jupiter searches many markets for the best route. "
        "Slippage is how far the price may move before the trade refuses; price impact is how much your own trade moves the "
        "price. VOOL converts SOL, USDC and USDT on Solana Mainnet through Jupiter: the card shows the most you pay and the "
        "least you get, VOOL checks the built transaction before signing (only your wallet signs, only known programs, no "
        "one else gets permission over your coins), and it needs your PIN.",
        ("jupiter_docs",),
    ),
    Topic(
        "confirmations", "Pending, confirmed and final",
        ("confirmed", "confirmation", "finalized", "final", "pending", "stuck", "how long", "did it arrive", "not arrived", "failed transaction"),
        "After you approve, the payment is sent to the network. On Solana it is usually confirmed within seconds and final "
        "(cannot be rolled back) a few seconds later. A transaction that fails still costs the small network fee but moves "
        "nothing else. VOOL marks a payment done only after the chain shows it landed, and the receipt keeps the "
        "transaction id so you can look it up.",
        ("solana_transactions", "solana_explorer"),
    ),
    Topic(
        "explorers", "Checking a payment yourself (block explorers)",
        ("explorer", "block explorer", "solscan", "etherscan", "basescan", "bscscan", "arbiscan", "transaction id", "tx hash", "signature", "look up", "verify"),
        "A block explorer is a public website that shows any address or transaction. Paste the transaction id from your VOOL "
        "receipt to see the amount, fee and time. Solana: Solana Explorer or Solscan. Ethereum: Etherscan. Base: Basescan. BNB "
        "Chain: BscScan. Arbitrum: Arbiscan.",
        ("solana_explorer", "solscan", "etherscan", "basescan", "bscscan", "arbiscan"),
    ),
    Topic(
        "approvals", "Token approvals and wallet drainers",
        ("approval", "approvals", "token approval", "approve", "unlimited approval", "delegate", "drainer", "drainers", "revoke", "permit", "signature request", "sign message"),
        "Some signatures do not send money; they give another program permission to take your tokens later (an approval or "
        "delegate). Drainer sites trick people into signing exactly that. Only sign what you understand. VOOL tells you what "
        "a signature does before you sign, never hands out a token delegate on a conversion, and pays services only within "
        "the caps you set.",
        ("ethereum_security",),
    ),
    Topic(
        "scams", "Common crypto scams",
        ("scam", "scams", "scammer", "fraud", "phishing", "airdrop", "fake support", "giveaway", "double your", "pig butchering", "romance",
         "investment", "recovery service", "address poisoning", "impersonat", "hacked", "stolen", "report"),
        "Most losses are tricks, not hacks: someone asks for your recovery phrase, a 'support agent' messages you first, a "
        "free airdrop asks you to connect and sign, an online friend steers you to an investment site, a 'recovery service' "
        "promises to get stolen coins back for a fee, or a look-alike address sneaks into your history. Crypto payments "
        "cannot be reversed. Stop, do not sign, and check with someone you trust. In the US you can report fraud to the "
        "FBI's IC3. VOOL's Stay safe cards in Settings walk through each one.",
        ("ic3", "ethereum_security"),
    ),
    Topic(
        "mainnet_testnet", "Mainnet, devnet and testnets",
        ("mainnet", "devnet", "testnet", "test network", "sepolia", "faucet", "fake money", "play money"),
        "Mainnet is the real network where coins have value. Devnet and testnets (like Solana Devnet or Base Sepolia) use free "
        "test coins that are worth nothing and exist for developers. Coins never move between them. VOOL's wallet uses "
        "mainnet.",
        ("ethereum_networks", "solana_docs"),
    ),
    Topic(
        "bridges", "Moving coins between networks (bridges)",
        ("bridge", "bridges", "bridging", "move usdc to base", "move to solana", "cctp", "wrapped"),
        "A bridge moves value from one network to another, usually by locking or burning coins on one side and issuing them "
        "on the other. Bridges have been the target of some of the largest hacks, so use well-known ones and move small "
        "amounts first. Circle moves USDC natively between networks. VOOL does not bridge coins today.",
        ("ethereum_bridges", "circle_multichain"),
    ),
    Topic(
        "staking", "Staking",
        ("stake", "staking", "liquid staking", "validator", "yield", "earn", "apy", "rewards"),
        "Staking lends your coins' weight to a validator that helps run the network, in return for rewards. On Solana you "
        "delegate SOL to a validator and can withdraw after a short cool-down; liquid staking gives you a token that stands "
        "for staked SOL. Rewards are not guaranteed and a promise of high fixed returns is a warning sign. VOOL does not "
        "stake for you today.",
        ("solana_staking", "ethereum_staking"),
    ),
    Topic(
        "x402", "Paying for online services from the wallet (x402)",
        ("x402", "402", "payment required", "pay per use", "paid api", "agent payments", "pay for a service", "allowance"),
        "x402 is an open way for a website or API to ask for a small stablecoin payment over the web (HTTP status 402, "
        "'Payment Required'). In VOOL a service that asks is never paid on its own say-so: the payment is parked for your "
        "approval, or paid from an allowance you set for the assistant or a plugin, within its caps, and every payment gets "
        "a receipt that says who asked and how the service was found.",
        ("x402",),
    ),
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join(_WORD_RE.findall(str(text or "").lower()))


def _score(topic: Topic, asked: str) -> int:
    padded = f" {asked} "
    score = 0
    for keyword in topic.keywords:
        key = _norm(keyword)
        if not key:
            continue
        if f" {key} " in padded or (key.endswith("impersonat") and key in asked):
            score += 3 if " " in key else 2
    return score


def find(question: str, *, limit: int = 2) -> list[Topic]:
    """The topics that answer this question best (none when nothing fits)."""
    asked = _norm(question)
    if not asked:
        return []
    ranked = sorted(((_score(topic, asked), index, topic) for index, topic in enumerate(TOPICS)), key=lambda row: (-row[0], row[1]))
    best = [topic for score, _index, topic in ranked if score > 0]
    return best[: max(1, int(limit))]


def topic(topic_id: str) -> Topic | None:
    return next((each for each in TOPICS if each.topic_id == str(topic_id or "").strip()), None)


def links(item: Topic) -> list[dict[str, str]]:
    return [{"name": SOURCES[key][0], "url": SOURCES[key][1]} for key in item.sources if key in SOURCES]


def answer(question: str = "", *, topic_id: str = "") -> dict[str, Any]:
    """The guide's answer: ``text`` (plain words with its sources), ``topics`` (ids) and ``links``."""
    chosen = [found] if (found := topic(topic_id)) else find(question)
    if not chosen:
        listing = "; ".join(each.title for each in TOPICS)
        return {"text": f"VOOL's crypto guide has no entry for that. It covers: {listing}.", "topics": [], "links": []}
    parts: list[str] = []
    every_link: list[dict[str, str]] = []
    for item in chosen:
        parts.append(f"{item.title}: {item.text}")
        every_link.extend(link for link in links(item) if link not in every_link)
    text = "\n\n".join(parts) + "\n\nRead more:\n" + "\n".join(f"- {link['name']}: {link['url']}" for link in every_link)
    return {"text": text, "topics": [item.topic_id for item in chosen], "links": every_link}


__all__ = ["SOURCES", "TOPICS", "Topic", "answer", "find", "links", "topic"]
