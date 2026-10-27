"""Deterministic demand signals for the tool-offer authority.

The offer seam (``capability_graph.model_visible_specs``) needs to know, BEFORE any model
is consulted, which tools the user's own words explicitly asked for and which capability
families those words require. That is the same deterministic-first law the rest of the
front door follows: shapes compute before model generation.

Design constraints, each carrying a census finding (a6c8e3c4):

- EXPLICIT beats RANKED. ``run the test suite`` classified ``debugging`` seats the
  workspace family, whose read-only-first ranking evicted ``workspace.run_tests`` from
  every offer. An intent named by the user's words is seated ahead of family ranking.
- MIXED demands name MULTIPLE families. ``read README.md and search the web`` used to
  collapse to whichever family the classifier picked first, starving the other half.
- WRITE demands seat a mutation tool. The offer is a PROPOSAL: the existing permission
  controller still decides execution. Nothing here widens any gate.

This module never executes anything, never reads policy (availability is decided at
seating time against live candidates), and is deliberately conservative: noun+verb
anchors, no fuzzy matching, no second task classifier. Unknown text yields no signals,
which keeps the previous single-family behaviour untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bounded so a single turn cannot flood its own offer with explicit seats.
MAX_EXPLICIT_INTENTS = 3
MAX_REQUIRED_FAMILIES = 3


@dataclass(frozen=True)
class DemandSignals:
    """What the user's words deterministically asked for.

    ``explicit_intents``    concrete tool intents named by the request
    ``required_families``   capability families the request needs (may be several)
    ``write_demand``        the request asks to change something (proposal only; the
                            permission controller still decides execution)
    """

    explicit_intents: tuple[str, ...] = ()
    required_families: tuple[str, ...] = ()
    write_demand: bool = False


# Market-watch evidence (used by the watch.* rules below). An address is an EVM address or a
# base58 Solana address; a ticker is upper case as typed (or $-prefixed); a condition is a price
# after above/below or a percent. The watch noun is "watch(es)" after a determiner or a subject,
# never the verb "watching" on its own.
_WATCH_ADDRESS = r"(?:\b0x[0-9a-fA-F]{40}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b)"
_WATCH_TICKER = r"(?:\$[A-Za-z][A-Za-z0-9]{1,9}\b|\b(?-i:[A-Z][A-Z0-9]{1,9})\b)"
_WATCH_CONDITION = (
    r"(?:\b(?:above|below|over|under|past)\s+\$?\d"
    r"|\b(?:goes?|gets?|is|trades?|moves?|rises?|drops?|falls?|dips?|pumps?|dumps?|crosses?|hits?|reaches?)"
    r"\s+(?:to\s+|by\s+)?\$?\d"
    r"|\d+(?:\.\d+)?\s*%)"
)
_WATCH_NOUN = (
    r"\b(?:the|this|that|my|all|these|those|its|price|wallet|token|coin)\s+watch(?:es)?\b"
    r"|\b(?-i:[A-Z][A-Z0-9]{1,9})\s+watch(?:es)?\b"
)
_WATCHING_SUBJECT = r"\bwatching\s+(?:this|that|the|my)\s+(?:wallet|token|coin|price)\b"


# Each rule: (intent, family, pattern). Patterns are noun+verb anchored phrases, matched
# case-insensitively against the whole message. Order is irrelevant to the result; the
# tuples exist so a single compiled pass covers everything.
#
# C18 FINAL CORRECTION — CONTEXT IS NOT AUTHORITY: a pasted command or test artifact ("python
# -m pytest -q", "test_calc.py") proves code CONTEXT only. The REPO_COMMAND_ANCHOR rule that
# seated `code.task.open` on such artifacts alone was REMOVED: it made "Explain this command:
# python -m pytest -q test_calc.py" and "Do not run this; what does ... mean?" authorize the
# code-task control plane. The control plane is seated by typed action demand (the verb-anchored
# rule below, or the bounded mutation+verification predicate); code context without action
# demand fails closed. No phrase regex replaced it — untranslated execution demands stay
# PARTIAL for the C12 owner rather than being authorized by artifacts.

# A repair directive can name the failing code check without naming its directory.
# Only imperative clauses qualify; advice, negation and authoring retain their gates below.
_CODE_REPAIR_ACTION_RE = re.compile(
    r"(?:^|[.;!?]\s*|\b(?:and|then)\s+)(?:please\s+)?"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:fix|repair|correct|resolve|patch)\b", re.IGNORECASE,
)
_CODE_CHECK_CONTEXT_RE = re.compile(
    r"\b(?:tests?|test\s+suite|unit\s+tests?|compiler|compilation|lint|build|assertion)\b",
    re.IGNORECASE,
)

def is_explicit_code_repair(text: str) -> bool:
    """A repair instruction with a code-check subject, shared by reach and tool seating."""
    from core.instructional_request import asks_for_instructions_not_execution

    return bool(
        _CODE_REPAIR_ACTION_RE.search(text)
        and _CODE_CHECK_CONTEXT_RE.search(text)
        and not asks_for_instructions_not_execution(text)
    )


# -- market data tools (core/market_data): every branch needs a market anchor in the SAME sentence --
# A token anchor (an EVM or Solana address, a $TICKER, "this/that token|coin|mint") or a collected
# dataset's id (md_<hex>). Trade words alone are ordinary English ("grab the candles from the store",
# "my thesis is due tomorrow", "delete the dataset I uploaded for the ML class"), so none of the
# market rules seats on them without the anchor.
_MARKET_TOKEN_ANCHOR = (
    r"\$[A-Za-z][A-Za-z0-9]{1,9}\b|\b0x[0-9a-fA-F]{40}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"
    r"|\b(?:this|that)\s+(?:token|coin|memecoin|mint)\b"
)
_MARKET_DATASET_ANCHOR = r"\bmd_[0-9a-f]{3,}\b|\bmarket\s+datasets?\b"


def _market_rule(branch: str, *, datasets: bool = False) -> re.Pattern[str]:
    """``branch`` and an anchor both inside one sentence (lookaheads from the sentence start)."""
    anchor = _MARKET_TOKEN_ANCHOR + ("|" + _MARKET_DATASET_ANCHOR if datasets else "")
    return re.compile(rf"(?:^|(?<=[.?!]))(?=[^.?!]*?(?:{anchor}))(?=[^.?!]*?(?:{branch}))", re.I)

#: the token a buy/sell activity question names: an address, a $TICKER, or "this/that token"
_FLOW_TOKEN = (r"(?:\$[A-Za-z][A-Za-z0-9]{1,9}\b|\b0x[0-9a-fA-F]{40}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"
               r"|\b(?:this|that|the|my)\s+(?:token|coin|memecoin)\b)")

_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # -- repository operations (RepoOps control plane) ------------------------
    # GitHub-management demands -- pull requests, issues, CI truth, the typed git vertical --
    # seat the RepoOps control plane (core/repoops) the same way a repair demand seats the
    # code-task plane. This puts the family on the table ONLY: every forge WRITE still sits
    # behind its operator authorization whatever the words say, and a PR number in an issue
    # body remains untrusted data. The pattern requires repository vocabulary together with
    # an action or truth noun; plain prose that merely mentions a PR does not seat anything.
    (
        "repo.session.open",
        "repo",
        re.compile(
            r"\b(?:pull\s+request|PR\b|merge\s+request)\b[^.?!]{0,80}?\b(?:open|opened|inspect|review|describe|prepare|check|checks?|CI\b|diff|branch|fail\w*|draft|comment|update|summar\w*|bind|session)\b"
            r"|\b(?:inspect|review|open|check|prepare|describe|summarize|comment\s+on|bind)\b[^.?!]{0,80}?\b(?:pull\s+request|PR\b|merge\s+request|repo(?:sitory)?\s+session)\b"
            r"|\b(?:CI\b|checks?|fail\w*|issue)\b[^.?!]{0,80}?\b(?:pull\s+request|PR\b|merge\s+request)\b"
            r"|\b(?:github|gitlab)\b[^.?!]{0,80}?\b(?:issue|pull\s+request|PR\b|CI\b|checks?|branch|repository|repo)\b"
            r"|\bwhich\s+(?:current\s+)?checks?\b[^.?!]{0,30}?\bfail\w*\b",
            re.I,
        ),
    ),
    # -- coding task control plane -------------------------------------------
    # A repair demand -- find/diagnose/fix WHY something in the tree fails -- seats the code-task
    # control plane (core/code_assistant), whose root-cause workflow the runtime then enforces.
    # The model still chooses; this only puts `code.task.open` on the table.
    (
        "code.task.open",
        "code",
        re.compile(
            r"\b(?:fix|repair|debug|diagnose|find\s+(?:out\s+)?why|figure\s+out\s+why|root[\s-]?cause)\b[^.?!]{0,80}?\b(?:fails?|failing|failure|broken|breaks?|bug|defect|regression|error|exception|traceback)\b",
            re.I,
        ),
    ),
    # -- wallet (propose/read only; approval is never a tool) ----------------
    # a decimal amount ("0.0005 SOL", "0.001 ETH") is not a sentence boundary; the native coins of every pilot row count.
    # The recipient may also be a bare address with no "to" ("pay 0x... 1 USDC on base"): then an exact amount must
    # stand before the coin, so an address and a coin name alone are not a payment
    ("wallet.propose", "wallet", re.compile(
        r"\b(?:pay|send|transfer|prepare|propose)\b(?:[^.?!]|\.(?=\d)){0,60}?\b(?:lamports?|sol|eth|bnb|usdc)\b(?:[^.?!]|\.(?=\d)){0,60}?\bto\b"
        r"|\b(?:pay|send|transfer)\b(?:[^.?!]|\.(?=\d)){0,60}?\b(?:0x[0-9a-f]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b(?:[^.?!]|\.(?=\d)){0,60}?\b\d[\d,]*(?:\.\d+)?\s*(?:lamports?|sol|eth|bnb|usdc)\b"
        r"|\b(?:pay|send|transfer)\b(?:[^.?!]|\.(?=\d)){0,60}?\b\d[\d,]*(?:\.\d+)?\s*(?:lamports?|sol|eth|bnb|usdc)\b(?:[^.?!]|\.(?=\d)){0,60}?\b(?:0x[0-9a-f]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b",
        re.I)),
    # -- contacts (the saved people and services; resolution itself happens inside each consumer) -----------
    # possession or the address-book name, never a bare mention: "contact card"/"Contacts are stored locally"
    # is prose about contacts, not a demand on them (the design-review boundary this base pins)
    ("contacts.search", "contacts", re.compile(r"\b(?:(?:my|our|the)\s+contacts?|address\s+book)\b(?![-\s]*(?:us|form|page|support|card)\b)", re.I)),
    ("contacts.resolve", "contacts", re.compile(r"\b(?:messaging|telegram|signal|whatsapp|slack|discord|matrix|teams)\s+(?:address|handle|id|username|identity|details)\b|\b(?:email\s+address|phone\s+number|wallet\s+address)\s+(?:for|of)\b", re.I)),
    # a possessive entry update ("Update Alex Chen's Solana devnet wallet with the one in the attached invoice",
    # "change Tom's phone number") is a Contacts write/lookup on one saved person, never a settings change
    ("contacts.update", "contacts", re.compile(r"\b(?:update|change|replace|edit)\b[^.?!]{0,60}?\w+'s\s+(?:\w+\s+){0,2}?(?:wallet|email|phone|handle|address)\b", re.I)),
    ("wallet.payment_status", "wallet", re.compile(r"\bpay-[0-9a-f]{20}\b|\b(?:status|state)\b[^.?!]{0,40}?\b(?:payment|proposal)\b", re.I)),
    ("wallet.simulate", "wallet", re.compile(r"\bsimulate\b[^.?!]{0,40}?\b(?:payment|proposal|transaction)\b", re.I)),
    ("x402.propose", "wallet", re.compile(r"\bx402\b[^.?!]{0,80}?\b(?:fetch|get|access|pay|paid|buy|resource|offer)\b|\b(?:fetch|access)\b[^.?!]{0,60}?\bpaid\b", re.I)),
    # pay-per-answer AI (core/x402_ai): the owner's own paid service, named as such; asking it only parks a payment
    ("ai.paid_ask", "wallet", re.compile(
        r"\b(?:ask|use|query|try)\b[^.?!]{0,30}?\b(?:paid|pay[\s-]?per[\s-]?answer)\s+(?:ai|model|llm|service|endpoint)\b"
        r"|\bask\b[^.?!]{0,60}?\b(?:with|over|via|through|using|paying\s+with)\s+x402\b", re.I)),
    ("wallet.status", "wallet", re.compile(r"\bwallet\b[^.?!]{0,30}?\b(?:status|balance|limits?)\b|\b(?:my|the)\s+wallet\b", re.I)),
    # -- Zcash private invoices (core/zcash; seated only when the lane is on, since the contracts exist only then) --
    # every pattern is anchored on the coin's own name, so no other money talk seats them
    ("zcash.invoice.create", "wallet", re.compile(r"\b(?:bill|invoice|charge|request)\b(?:[^.?!]|\.(?=\d)){0,60}?\b(?:zec|zcash|taz)\b|\b(?:zec|zcash)\s+(?:invoice|payment\s+request)\b", re.I)),
    ("zcash.invoice.unpaid", "wallet", re.compile(r"\b(?:owes?|owing|unpaid|outstanding|not\s+(?:yet\s+)?paid)\b[^.?!]{0,60}?\b(?:zec|zcash)\b|\b(?:zec|zcash)\b[^.?!]{0,60}?\b(?:owes?|owing|unpaid|outstanding|paid\s+yet)\b|\b(?:has|have)\b[^.?!]{0,30}?\bpaid\b[^.?!]{0,60}?\b(?:zec|zcash)\b", re.I)),
    ("zcash.payment.history", "wallet", re.compile(r"\b(?:pa(?:y|ys|id)|payments?|received|history|export)\b[^.?!]{0,60}?\b(?:zec|zcash)\b|\b(?:zec|zcash)\b[^.?!]{0,60}?\b(?:pa(?:y|ys|id)|payments?|received|history|export)\b", re.I)),
    # -- chain-data lookups (core/chain_data; read-only, through the operator's own provider keys) --
    # a provider's name, an on-chain read word near a chain or address word, or a raw EVM address/hash
    ("web.chain_lookup", "web", re.compile(
        r"\b(?:helius|alchemy|solscan|etherscan|basescan|bscscan)\b"
        r"|\b(?:balances?|holders?|holdings|token\s+info|transactions?|txs?|history|portfolio|supply)\b[^.?!]{0,60}?"
        r"\b(?:sol|solana|eth|ethereum|base|bnb|bsc|robinhood\s+chain|on[- ]?chain|address|token|mint|contract)\b"
        r"|\b(?:sol|solana|eth|ethereum|bnb|bsc|on[- ]?chain|token|mint)\b[^.?!]{0,30}?\b(?:balances?|holders?|holdings|transactions?|history|supply)\b"
        r"|\b0x[0-9a-fA-F]{40}\b|\b0x[0-9a-fA-F]{64}\b", re.I)),
    # -- token research (core/token_research; read-only facts, creator, links, early X posts) --
    # a research verb near a token word, a creator/deployer question, or a mint/freeze-rights question
    ("token.research", "web", re.compile(
        r"\b(?:research|dig\s+into|look\s+into|due\s+diligence|dd\s+on|analy[sz]e|check\s+out|tell\s+me\s+about)\b[^.?!]{0,60}?"
        r"(?:\b(?:token|coin|memecoin|mint|ca)\b|\$[A-Za-z][A-Za-z0-9]{1,9}\b|\b0x[0-9a-fA-F]{40}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b)"
        r"|\b(?:deployer|creator|dev)\b[^.?!]{0,60}?\b(?:token|coin|contract|mint|launch\w*|deploy\w*)\b"
        r"|\bwho\s+(?:made|created|deployed|launched|minted)\b[^.?!]{0,60}?"
        r"(?:\b(?:token|coin|contract|mint|this|it)\b|\b0x[0-9a-fA-F]{40}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b)"
        r"|\bwhat\s+else\b[^.?!]{0,40}?\b(?:launch|deploy|creat|made|ma[dk]e)\w*"
        r"|\b(?:mint|freeze)\s+authority\b", re.I)),
    # how calls did: "how did his calls do", "track record", "did that call pump"
    ("token.performance", "web", re.compile(
        r"\bhow\s+(?:did|has|have|do)\b[^.?!]{0,60}?\bcalls?\b[^.?!]{0,30}?\b(?:do|done|go|gone|perform\w*)\b"
        r"|\btrack\s+record\b[^.?!]{0,60}?\b(?:calls?|tokens?|coins?|picks?)\b"
        r"|\b(?:calls?|tokens?|coins?|picks?)\b[^.?!]{0,60}?\btrack\s+record\b"
        r"|\b(?:win\s*rate|hit\s*rate)\b[^.?!]{0,40}?\b(?:calls?|caller|picks?)\b", re.I)),
    # buy/sell activity: "is this token being bought or sold", "buy volume for $FDOG", "who is selling $WIF".
    # Every branch needs the token itself (an address, a $TICKER, or "this/that token") right where the
    # question names it, so a sentence that only shares a word ("token of love", "selling activity in my
    # store") never seats it.
    ("token.flow", "web", re.compile(
        _FLOW_TOKEN + r"[^.?!]{0,40}?\bbeing\s+(?:bought\s+or\s+sold|sold\s+or\s+bought)\b"
        r"|\b(?:buy|sell)(?:ing)?(?:\s*(?:and|or|/)\s*(?:buy|sell)(?:ing)?)?\s+(?:volume|pressure|flow|activity)\s+(?:on|for|of|in)\s+"
        + _FLOW_TOKEN + r"|\bwho(?:'s|\s+is)\s+(?:buying|selling)\s+" + _FLOW_TOKEN, re.I)),
    # new tokens: "new launches", "newest tokens", "latest memecoins on solana", "fresh mints"
    ("token.launches", "web", re.compile(
        r"\b(?:new(?:est)?|latest|fresh|recent(?:ly)?)\s+(?:token\s+|coin\s+|memecoin\s+)?(?:launch(?:es|ed)?|tokens|coins|memecoins|mints)\b"
        r"|\b(?:tokens|coins|memecoins)\s+(?:launched|created|minted)\s+(?:today|recently|just\s+now|in\s+the\s+last)\b", re.I)),
    # -- market watches (core/market_watch; watch a wallet or a price and get an alert) --
    # Seated only on crypto evidence: a wallet/token noun WITH an address or ticker, or an
    # alert shape whose subject is a token and whose condition is a price or a percent.
    # "track my package to this address", "notify me if the deploy goes red", "stop watching
    # TV" and "pause the alert sound" seat nothing.
    ("watch.add", "web", re.compile(
        rf"\b(?:watch|track|monitor|keep\s+(?:an?\s+)?eye\s+on)\b[^.?!]{{0,60}}?"
        rf"(?:\b(?:wallet|address)\b[^.?!]{{0,40}}?{_WATCH_ADDRESS}"
        rf"|\b(?:token|coin|mint|price)\b[^.?!]{{0,40}}?(?:{_WATCH_ADDRESS}|{_WATCH_TICKER})"
        rf"|(?:{_WATCH_ADDRESS}|{_WATCH_TICKER})[^.?!]{{0,30}}?\b(?:wallet|token|coin|price)\b)"
        rf"|\b(?:(?:alert|notify|warn|ping)\s+me|(?:tell|let)\s+me(?:\s+know)?)\b[^.?!]{{0,20}}?\b(?:when|if|once)\b"
        rf"[^.?!]{{0,40}}?(?:{_WATCH_ADDRESS}|{_WATCH_TICKER}|\b(?:token|coin)\b)[^.?!]{{0,40}}?{_WATCH_CONDITION}",
        re.I)),
    ("watch.list", "web", re.compile(
        r"\bwhat\s+am\s+i\s+watching\b"
        r"|\b(?:list|show|see|check)\b[^.?!]{0,40}?\b(?:my|the)\s+(?:watches|watch\s+list)\b"
        r"|\b(?:my|the)\s+(?:watches|watch\s+list)\b[^.?!]{0,40}?\b(?:list|show|what|open)\b", re.I)),
    ("watch.remove", "web", re.compile(
        rf"\b(?:stop|remove|delete|drop|cancel)\b[^.?!]{{0,60}}?(?:{_WATCH_NOUN}|{_WATCHING_SUBJECT})"
        rf"|\bunwatch\b", re.I)),
    ("watch.pause", "web", re.compile(
        rf"\b(?:pause|suspend|mute)\b[^.?!]{{0,60}}?(?:{_WATCH_NOUN}|{_WATCHING_SUBJECT})", re.I)),
    ("watch.resume", "web", re.compile(
        rf"\b(?:resume|unpause|unmute|restart)\b[^.?!]{{0,60}}?(?:{_WATCH_NOUN}|{_WATCHING_SUBJECT})", re.I)),
    # -- market data collection (core/market_data; trades collected locally, then analyzed in code) --
    # each branch also needs a market anchor in the same sentence (_market_rule above)
    # collect: a collecting verb near trade words, trades asked for a token, or a buy/sell-pressure question
    ("market.collect", "web", _market_rule(
        r"\b(?:collect|pull|gather|grab|download|fetch)\b[^.?!]{0,60}?"
        r"\b(?:trades?|swaps?|trading\s+(?:data|history|activity)|market\s+data|candles?)\b"
        r"|\b(?:trades?|swaps?)\b[^.?!]{0,40}?\b(?:for|of|on)\b"
        r"|\blast\s+(?:\d+\s*)?(?:hours?|days?|weeks?)\s+of\s+(?:\w+\s+){0,2}?trades?\b"
        r"|\b(?:being\s+)?bought\s+or\s+sold\b|\b(?:buy|sell)(?:ing)?\s+pressure\b")),
    # the stored datasets themselves: "list the market datasets", "what market datasets do you have"
    ("market.datasets", "web", _market_rule(
        r"\b(?:list|show)\b[^.?!]{0,40}?\bdatasets?\b"
        r"|\bdatasets?\b[^.?!]{0,40}?\b(?:do\s+you\s+(?:have|know)|are\s+(?:there|stored|saved)|have\s+you\s+collected)\b"
        r"|\bmy\s+(?:collected\s+)?(?:market\s+)?datasets?\b", datasets=True)),
    # deleting a stored dataset or its collected data
    ("market.dataset_delete", "web", _market_rule(
        r"\b(?:delete|remove|drop|clear|forget)\b[^.?!]{0,50}?\bdatasets?\b"
        r"|\b(?:delete|remove|drop|clear)\b[^.?!]{0,50}?\bcollected\s+(?:market\s+)?(?:data|trades?)\b"
        r"|\bdatasets?\b[^.?!]{0,30}?\b(?:deleted|removed|dropped)\b", datasets=True)),
    # "what patterns do you see in md_…", "analyze the trades for $BONK"
    ("market.analyze", "web", _market_rule(
        r"\b(?:what|any)\s+patterns?\b[^.?!]{0,60}?\b(?:do|did|can)\s+you\s+see\b"
        r"|\b(?:analy[sz]e|find\s+patterns?\s+in|look\s+for\s+patterns?\s+in)\b[^.?!]{0,60}?"
        r"\b(?:dataset|collected\s+(?:data|trades?)|market\s+data|trades?)\b"
        r"|\bpatterns?\s+in\s+the\s+(?:data|trades?|dataset|numbers?)\b", datasets=True)),
    # "check my thesis on $BONK: ...", "my theory about this token is that ..."
    ("market.thesis_check", "web", _market_rule(
        r"\b(?:check|test|verify|validate)\b[^.?!]{0,40}?\b(?:my|the|this|that|a)\s+thesis\b"
        r"|\bthesis\b[^.?!]{0,40}?\b(?:is|holds?|true|right|correct|wrong|supported|contradicted)\b"
        r"|\bmy\s+(?:theory|hypothesis|idea)\b[^.?!]{0,60}?\b(?:is|that)\b", datasets=True)),
    # -- workspace execution/validation -------------------------------------
    (
        "workspace.run_tests",
        "workspace",
        re.compile(
            r"\b(?:run|running|execute|executing|launch|launching)\b[^.?!]{0,40}?\b(?:unit\s+tests?|tests?\s+suite|test\s+suite|tests?)\b",
            re.I,
        ),
    ),
    (
        "workspace.run_lint",
        "workspace",
        re.compile(
            r"\b(?:run|running|execute|executing)\b[^.?!]{0,30}?\blint\b|\blint\s+(?:the\s+)?(?:code|repo|workspace|project)\b",
            re.I,
        ),
    ),
    (
        "workspace.run_formatter",
        "workspace",
        re.compile(
            r"\b(?:run|running|format|formatting)\b[^.?!]{0,30}?\b(?:formatter|prettier|black|ruff format)\b", re.I
        ),
    ),
    # -- a file handed to the user ---------------------------------------------
    # The answer wanted AS a file in a named format ("as a Word document", "export to CSV",
    # "give me a .xlsx", "zip these"). Seats the delivery tool beside the workspace writer below;
    # the tool descriptions tell the model which one a request means.
    (
        "file.deliver",
        "file",
        re.compile(
            # "as a Word document", "to Excel", "in a short one-page PDF", "as a .rar file". The
            # words between the preposition and the format may not be a determiner of material
            # already present ("in the attached pdf" asks about a file, it does not order one).
            r"\b(?:as|in|into|to)\s+(?:an?\.?\s+)?(?:(?!the\b|this\b|that\b|these\b|those\b|attached\b|uploaded\b|my\b|your\b|our\b)[\w-]+\s+){0,3}?"
            # "Word" alone is the program only when capitalized: "answer in exactly one word" and
            # "in a word" constrain the reply, they order no document.
            r"\.?\s?(?:(?-i:Word|WORD)|word\s+(?:docs?|documents?|files?)|docx|excel|xlsx|powerpoint|pptx|pdf|csv|tsv|zip|rar|7z|tar(?:\.gz|ball)?|spreadsheet|workbook|slide\s*deck)\b"
            # "a CSV file", "a JSON file", "a PowerPoint file", "a Word document": a NEW file of a
            # named type. Data formats need the noun, so "answer in JSON" stays a reply format.
            r"|\ban?\s+(?:(?!attached\b|uploaded\b)[\w-]+\s+){0,2}?\.?(?:word|docx|excel|xlsx|powerpoint|pptx|pdf|csv|tsv|json|yaml|xml|html|markdown|md|txt|text|zip|rar|7z)\s+"
            r"(?:file|document|doc|spreadsheet|workbook|deck|archive)\b"
            # "export ... .docx", "make me a slide deck", "give me a downloadable copy".
            r"|\b(?:export|download|give\s+me|send\s+me|make|create|generate|produce|build|prepare|convert|turn|save)\b[^.?!]{0,80}?"
            r"(?:\.(?:docx|xlsx|pptx|pdf|csv|tsv|json|zip|rar|7z)\b|\b(?:word\s+(?:doc|document|file)|excel\s+(?:file|sheet|workbook|spreadsheet)|powerpoint|slide\s*deck|downloadable\s+\w+)\b)"
            # "export the notes to word": a conversion verb makes a lowercase "word" the program.
            r"|\b(?:export|convert|save|turn)\b[^.?!]{0,80}?\b(?:to|into|as)\s+(?:ms\s+|microsoft\s+)?word\b"
            r"|\b(?:zip|compress)\s+(?:\w+\s+){0,2}?(?:up\s+)?(?:these|those|them|it|the\s+files|all\s+of\s+(?:it|them))\b"
            r"|\b(?:zip|compress)\s+(?:them|it|these|those)\s+up\b"
            # "...plus a usage note, and zip both files", "zip all of the files": the zip ordered by
            # quantity rather than a pronoun (measured live 2026-10-06: answered as chat prose).
            r"|\b(?:zip|compress)\s+(?:up\s+)?(?:both|all)\b(?:\s+(?:of\s+)?(?:the\s+)?(?:files?|them|together))?"
            # "Export a CSV of the five largest states", "make me a PDF of...": a verb ordering a NEW
            # file of a named type with no "file" noun (measured live: answered as chat prose).
            # Data formats that double as reply formats (json, yaml) stay out, as in the pattern above,
            # and "how to make a PDF ..." asks about the task instead of ordering it.
            r"|(?<!how to )\b(?:export|download|give\s+me|send\s+me|make\s+me|make|create|generate|produce|prepare)\s+an?\s+"
            r"(?:(?:sample|short|simple|brief|one-page)\s+){0,2}"
            r"(?:csv|tsv|pdf|xlsx|docx|pptx|excel|zip)\b(?!\s+(?:format|formatting|response|answer|reply)\b)"
            # A direct desire for a concrete artifact is also an order, not prose formatting.
            r"|\bi\s+(?:need|want|would\s+like)\s+an?\s+(?:short\s+|sample\s+)?(?:pdf|csv|tsv|zip|docx|xlsx|pptx)\b(?!\s+(?:format|formatting|response|answer|reply|syntax|explanation)\b)"
            r"|\b(?:zip|compress)\s+the\s+(?:csv|pdf|docx|xlsx|files?)\b"
            # These nouns name deliverable artifacts without needing an extra "file" noun.
            r"|\b(?:i\s+(?:need|want|would\s+like)|(?:make|create|build|generate|prepare)(?:\s+me)?)\s+"
            r"(?:an?\s+)?(?:(?!to\b|about\b|know\b|understand\b|learn\b|how\b)[\w-]+\s+){0,3}?"
            r"(?:spreadsheet|workbook|presentation|slide\s*deck)\b"
            # "I need a spreadsheet of my monthly subscriptions ... Excel please": a spreadsheet or
            # workbook is a file by its nature, so a turn that wants one orders a file (measured live
            # 2026-10-06: answered with a fabricated "working on it" and no file).
            r"|\b(?:i\s+(?:need|want|would\s+like)|give\s+me|send\s+me|make\s+me|make|create|build|put\s+together|set\s+up)"
            r"\s+(?:me\s+)?an?\s+(?:(?:excel|google|simple|small|quick|new|monthly|weekly|basic)\s+){0,2}(?:spreadsheet|workbook)s?\b",
            re.I,
        ),
    ),
    # -- workspace mutation --------------------------------------------------
    (
        "workspace.write_file",
        "workspace",
        re.compile(
            r"\b(?:write|create|save|overwrite|append)\b[^.?!]{0,60}?\b(?:file|\.md|\.txt|\.json|\.py|\.js|\.ts|\.yaml|\.yml|\.toml|\.csv|\.html|\.css)\b",
            re.I,
        ),
    ),
    (
        "workspace.replace_in_file",
        "workspace",
        re.compile(
            r"\b(?:edit|replace|substitute|patch)\b[^.?!]{0,40}?\b(?:in\s+(?:the\s+)?file|inside\s+(?:the\s+)?file|in\s+it)\b",
            re.I,
        ),
    ),
    ("workspace.apply_unified_diff", "workspace", re.compile(r"\bapply\b[^.?!]{0,20}?\b(?:unified\s+)?diff\b", re.I)),
    ("workspace.git_status", "workspace", re.compile(r"\bgit\s+status\b", re.I)),
    ("workspace.git_diff", "workspace", re.compile(r"\bgit\s+diff\b", re.I)),
    (
        "workspace.rollback_last_change",
        "workspace",
        re.compile(
            r"\b(?:rollback|roll\s+back|revert)\b[^.?!]{0,20}?\b(?:last\s+change|last\s+edit|that\s+change)\b", re.I
        ),
    ),
    # -- workspace reads ------------------------------------------------------
    (
        "workspace.read_file",
        "workspace",
        re.compile(r"\b(?:read|open|show|display|print|cat)\b[^.?!]{0,40}?\b[\w./-]+\.\w{1,6}\b", re.I),
    ),
    (
        "workspace.list_files",
        "workspace",
        re.compile(
            r"\b(?:list|show|what)\b[^.?!]{0,30}?\bfiles?\b[^.?!]{0,20}?\b(?:here|workspace|repo|repository|project|directory|folder)\b",
            re.I,
        ),
    ),
    (
        "workspace.search_text",
        "workspace",
        re.compile(
            r"\b(?:search|grep|find)\b[^.?!]{0,40}?\b(?:in\s+(?:the\s+)?(?:workspace|repo|repository|codebase|project)|across\s+(?:the\s+)?(?:workspace|repo))\b",
            re.I,
        ),
    ),
    # -- web -------------------------------------------------------------------
    (
        "web.search",
        "web",
        re.compile(
            r"\b(?:search|google|look\s+up|find)\b[^.?!]{0,60}?\b(?:web|online|internet|google|bing|duckduckgo)\b", re.I
        ),
    ),
    ("web.fetch", "web", re.compile(r"\b(?:fetch|open|load|check)\b[^.?!]{0,40}?https?://", re.I)),
    (
        "web.research",
        "web",
        re.compile(r"\bresearch\b[^.?!]{0,40}?\b(?:online|web|sources?|for)\b|\bdo\s+(?:some\s+)?research\b", re.I),
    ),
    # -- pdf -------------------------------------------------------------------
    (
        "pdf.extract_text",
        "pdf",
        re.compile(
            r"\b(?:extract|get|pull|read)\b[^.?!]{0,30}?\b(?:the\s+)?text\b[^.?!]{0,20}?\b\.?pdf\b|\bpdf\b[^.?!]{0,30}?\b(?:text|extract)",
            re.I,
        ),
    ),
    (
        "pdf.ocr",
        "pdf",
        re.compile(r"\bocr\b[^.?!]{0,30}?\b(?:pdf|scan|scanned)\b|\b(?:pdf|scan|scanned)\b[^.?!]{0,30}?\bocr\b", re.I),
    ),
    # -- machine / filesystem ---------------------------------------------------
    (
        "machine.list_directory",
        "filesystem",
        re.compile(
            r"\b(?:list|show|what'?s|how many)\b[^.?!]{0,40}?\b(?:downloads|desktop|documents|folder|directory|files?)\b",
            re.I,
        ),
    ),
    (
        "machine.disk_usage",
        "filesystem",
        re.compile(r"\b(?:disk|drive)\b[^.?!]{0,20}?\b(?:space|usage|free)\b|\bhow\s+much\s+(?:disk|space)\b", re.I),
    ),
    (
        "machine.list_processes",
        "filesystem",
        re.compile(r"\b(?:running|heaviest|top)\b[^.?!]{0,20}?\bprocesses\b|\blist\s+processes\b", re.I),
    ),
    (
        "machine.inspect_specs",
        "filesystem",
        re.compile(
            r"\b(?:machine|host|hardware)\b[^.?!]{0,20}?\b(?:specs?|specifications?)\b|\bwhat\s+(?:chip|hardware|ram)\b",
            re.I,
        ),
    ),
    # -- machine mutations (safe-local-dir writes; the permission gate still decides) --
    (
        "machine.write_file",
        "filesystem",
        re.compile(r"\b(?:write|save|create)\b[^.?!]{0,40}?\b(?:downloads|desktop|documents)\b", re.I),
    ),
    (
        "machine.ensure_directory",
        "filesystem",
        re.compile(
            r"\b(?:create|make|new)\b[^.?!]{0,20}?\b(?:folder|directory)\b[^.?!]{0,20}?\b(?:in\s+)?(?:downloads|desktop|documents)\b",
            re.I,
        ),
    ),
    (
        "machine.move_path",
        "filesystem",
        re.compile(
            r"\b(?:move|relocate)\b[^.?!]{0,60}?\b(?:to|into)\b[^.?!]{0,30}?\b(?:downloads|desktop|documents)\b", re.I
        ),
    ),
    # -- skill -------------------------------------------------------------------
    ("skill.list", "skill", re.compile(r"\b(?:list|show|what|which|audit)\b[^.?!]{0,30}?\bskills?\b", re.I)),
    (
        # Rolling back a SKILL is an activation-class change on the skill store; the rule
        # requires the skill noun so file/code revert phrasings stay in the workspace lane.
        "skill.rollback",
        "skill",
        re.compile(
            r"\b(?:roll\s?back|rollback|revert|restore|undo|previous\s+version|prior\s+version|"
            r"last\s+version|older\s+version)\b[^.?!]{0,60}?\bskills?\b|\bskills?\b[^.?!]{0,60}?"
            r"\b(?:roll\s?back|rollback|revert|restore|undo|previous\s+version|prior\s+version)\b",
            re.I,
        ),
    ),
    # -- email / media (policy may keep them unavailable; seating filters that) --
    # Neither email intent is a rule here. A request about mail the user received is recognized by what
    # it is about (mailbox_retrieval_intents below), and a request to send or compose a message by
    # outgoing_message_intents below -- both under instruction authority, so a how-question, a quoted
    # sentence, a reported example or a request for a KIND of text (a template) names no email tool.
    (
        "image.generate",
        "media",
        re.compile(r"\b(?:generate|create|make|draw)\b[^.?!]{0,30}?\b(?:an?\s+)?image\b", re.I),
    ),
    # -- operator -----------------------------------------------------------------
    ("operator.inspect_disk_usage", "operator", re.compile(r"\b(?:inspect|check|audit)\b[^.?!]{0,20}?\bdisk\b", re.I)),
    (
        "operator.inspect_processes",
        "operator",
        re.compile(r"\b(?:inspect|check|audit)\b[^.?!]{0,20}?\bprocesses\b", re.I),
    ),
    # -- database work (vool-database): ordinary phrasing selects the ONE database
    # skill. Verb+object anchored like every other rule here, and the family is
    # SELECTION-ONLY -- `database` is not a canonical capability family, so it seats
    # no tool. What arms a database tool is a typed action demand that still crosses
    # the approval, effect-budget, Blackbox-coverage and permission gates.
    #
    # These exist because the skill was unreachable by ANY ordinary phrasing: it
    # declares no task-families (so the 4-point task-class term can never fire) and
    # no rule emitted its capability family, so its score was structurally 0 and only
    # a literal `vool-database.<tool>` token could reach it.
    (
        "database.inspect",
        "database",
        re.compile(
            r"\b(?:show|list|describe|inspect|count|check|look\s+at|what(?:'s|\s+is)\s+in)\b"
            r"[^.?!]{0,60}?\b(?:databases?|db|tables?|schemas?|columns?|rows?|sqlite|postgres|mysql)\b",
            re.I,
        ),
    ),
    (
        "database.query",
        "database",
        re.compile(
            r"\b(?:query|select|search|fetch|pull|read)\b[^.?!]{0,60}?"
            r"\b(?:databases?|db|tables?|sqlite|postgres|mysql|rows?)\b"
            r"|\bfrom\s+(?:the\s+)?(?:\w+\s+)?tables?\b",
            re.I,
        ),
    ),
    (
        "database.mutate",
        "database",
        re.compile(
            r"\b(?:insert|update|delete|drop|alter|truncate|add|remove|rename)\b[^.?!]{0,60}?"
            r"\b(?:databases?|db|tables?|schemas?|columns?|rows?|records?|indexe?s?)\b",
            re.I,
        ),
    ),
    (
        "database.migrate",
        "database",
        re.compile(
            r"\b(?:migrate|migration|back\s?up|restore|dump)\b[^.?!]{0,60}?"
            r"\b(?:databases?|db|tables?|schemas?)\b"
            r"|\b(?:databases?|db|schemas?)\b[^.?!]{0,40}?\b(?:migrations?|back\s?ups?)\b",
            re.I,
        ),
    ),
    # -- X editorial studio (C20): direct X-writing requests select the editorial skill;
    # an ordinary mention of X/Twitter in prose must not. Every rule is verb+noun anchored
    # like the rest of this table, and the family is editorial-only (no tool seating).
    (
        "editorial.draft_article",
        "editorial",
        re.compile(
            r"\b(?:write|draft|create|start)\b[^.?!]{0,40}?\b(?:an?\s+)?(?:x|twitter)\s+articles?\b",
            re.I,
        ),
    ),
    (
        "editorial.draft_long_post",
        "editorial",
        re.compile(
            r"\b(?:write|draft|create|turn|convert|make)\b[^.?!]{0,40}?\b(?:premium\s+)?long[ -]?posts?\b",
            re.I,
        ),
    ),
    (
        "editorial.draft_post",
        "editorial",
        re.compile(
            r"\b(?:write|draft|compose|create|condense|compress|squeeze|shrink)\b"
            r"[^.?!]{0,60}?\b(?:single\s+|one\s+)?(?:tweets?|(?:x|twitter)\s+posts?)\b",
            re.I,
        ),
    ),
    (
        "editorial.to_thread",
        "editorial",
        re.compile(
            r"\b(?:turn|convert|break|split)\b[^.?!]{0,60}?\bthreads?\b"
            r"|\bas\s+a\s+thread\b|\bthread\s+this\b",
            re.I,
        ),
    ),
    (
        "editorial.format_x",
        "editorial",
        re.compile(
            r"\b(?:format|fit|adapt|trim|shape|squeeze|optimize|optimise|clean|write|draft)\b"
            r"[^.?!]{0,50}?\bfor\s+(?:x|twitter)\b",
            re.I,
        ),
    ),
    (
        "editorial.critique",
        "editorial",
        re.compile(
            r"\b(?:edit|critique|review|tighten|improve|punch\s+up)\b[^.?!]{0,40}?\b"
            r"(?:my|this|the)\s+(?:tweets?|threads?|(?:x|twitter)\s+(?:posts?|articles?|drafts?))\b",
            re.I,
        ),
    ),
    (
        "editorial.voice",
        "editorial",
        re.compile(
            r"\b(?:sounds?|reads?)\s+like\s+me\b|\bin\s+my\s+voice\b|\bwith\s+my\s+voice\b"
            r"|\bmake\s+(?:it|this)\b[^.?!]{0,20}?\bsounds?\b",
            re.I,
        ),
    ),
    # -- X reads and actions with the user's own keys (core/x_api.py, core/x_actions.py) ----
    # Verb+noun anchored like the rest of this table: searching X, who called a token, an
    # account's posts, or acting on X. A passing mention of X or Twitter seats nothing. An action
    # seat is a PROPOSAL only; nothing reaches X until the user approves it in Settings.
    (
        "x.token_callers",
        "x",
        re.compile(
            r"\b(?:who|which\s+(?:accounts?|people|influencers?|kols?))\b[^.?!]{0,40}?"
            r"\b(?:called|calls|shill(?:ed|s|ing)?|posted|tweeted|pushed|mentioned)\b[^.?!]{0,60}?"
            r"(?:\$[A-Za-z][A-Za-z0-9_]{0,14}\b|\b0x[a-fA-F0-9]{40}\b|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b"
            r"|\b(?:tokens?|coins?|memecoins?|ca|contract\s+address)\b)"
            r"|\b(?:callers?|kols?|shillers?)\b[^.?!]{0,30}?\b(?:of|for|behind)\b[^.?!]{0,40}?"
            r"(?:\$[A-Za-z]|\b0x[a-fA-F0-9]{6}|\b[1-9A-HJ-NP-Za-km-z]{32,44}\b|\b(?:tokens?|coins?)\b)",
            re.I,
        ),
    ),
    (
        "x.search",
        "x",
        re.compile(
            r"\b(?:search|find|look\s+up|look\s+for|check|scan|monitor|track|pull|show\s+me)\b[^.?!]{0,60}?"
            r"\b(?:on|in|from|across)\s+(?:x|twitter)\b(?!\.)"
            r"|\b(?:search|check|scan)\s+(?:x|twitter)\s+(?:for|about)\b"
            r"|\b(?:tweets?|(?:x|twitter)\s+posts?)\s+(?:about|mentioning|on|for|with)\s+"
            r"(?:\$[A-Za-z]|\b0x[a-fA-F0-9]{6}|[1-9A-HJ-NP-Za-km-z]{32,44}\b|(?:this|that|the)\s+(?:token|coin|ca)\b)",
            re.I,
        ),
    ),
    (
        "x.user_posts",
        "x",
        re.compile(
            r"(?<![\w@])@[A-Za-z0-9_]{1,15}(?:'s|’s)?\s+(?:(?:past|previous|old|recent|latest|last)\s+)?"
            r"(?:tweets|posts|timeline|calls|replies|mentions|likes|followers)\b"
            r"|\b(?:his|her|their|its)\s+(?:past|previous|old|earlier)\s+(?:calls|tweets|posts)\b",
            re.I,
        ),
    ),
    (
        "x.action.propose",
        "x",
        re.compile(
            r"\b(?:post|tweet|publish|reply|retweet|repost|quote)\b[^.?!]{0,60}?\b(?:on|to)\s+(?:x|twitter)\b(?!\.)"
            r"|\b(?:tweet|retweet|repost)\s+(?:this|that|it)\b"
            r"|\b(?:follow|unfollow)\s+@[A-Za-z0-9_]{1,15}\b"
            r"|\b(?:like|retweet|repost|reply\s+to|quote)\s+(?:this|that|the)\s+(?:tweet|post)\b"
            r"|\b(?:mute|unmute)\s+@[A-Za-z0-9_]{1,15}\b"
            r"|\b(?:dm|message)\s+@[A-Za-z0-9_]{1,15}\b"
            r"|\b(?:hide|unhide)\s+(?:this|that|the|a)\s+repl(?:y|ies)\b"
            r"|\b(?:create|make|start)\s+(?:an?\s+)?(?:x|twitter)\s+list\b"
            r"|\badd\s+@[A-Za-z0-9_]{1,15}\s+to\s+(?:my\s+)?(?:\w+\s+)?list\b",
            re.I,
        ),
    ),
    ("x.spaces", "x", re.compile(r"\b(?:x|twitter)\s+spaces?\b|\bspaces?\s+(?:on|from)\s+(?:x|twitter)\b"
                                r"|\b(?:live|scheduled|upcoming)\s+spaces?\b", re.I)),
    ("x.dms", "x", re.compile(r"\bmy\s+(?:x|twitter)\s+(?:dms?|direct\s+messages?|inbox)\b"
                             r"|\b(?:dms?|direct\s+messages?)\s+(?:on|from|in)\s+(?:x|twitter)\b", re.I)),
    ("x.account", "x", re.compile(r"\bwho\s+(?:have\s+)?i\s+(?:muted|blocked)\b|\bmy\s+(?:muted|blocked)\s+(?:accounts?|list)\b"
                                 r"|\bmy\s+(?:x|twitter)\s+trends\b|\b(?:x|twitter)\s+api\s+(?:usage|cap)\b", re.I)),
    # Your own X reads ("fetch my last 2 tweets", "check my mentions on X", "my X timeline") and
    # questions about what someone posted ("what did @bob post today?"). Reads only.
    (
        "x.user_posts",
        "x",
        re.compile(
            r"\bmy\s+(?:(?:last|latest|recent|newest|past|previous|old|top)\s+)?(?:\d{1,3}\s+|few\s+|couple\s+(?:of\s+)?)?"
            r"(?:tweets|(?:x|twitter)\s+(?:posts|mentions|likes|timeline|feed))\b"
            r"|\bmy\s+(?:(?:home\s+)?timeline|mentions|feed|likes)\s+(?:on|from)\s+(?:x|twitter)\b(?!\.)"
            r"|\bwho\s+(?:has\s+|have\s+)?(?:mentioned|tagged|replied\s+to)\s+me\s+on\s+(?:x|twitter)\b(?!\.)"
            r"|\bwhat\s+(?:did|has|have|does|is|was)\s+@[A-Za-z0-9_]{1,15}\s+(?:been\s+)?(?:tweet|post|say|said|share)"
            r"|\bwhat\s+(?:did|has|have|does|is|was)\s+[^.?!]{1,40}?\s+(?:been\s+)?"
            r"(?:tweet(?:ed|ing)?|post(?:ed|ing)?|said|say(?:ing)?)\b[^.?!]{0,40}?\b(?:on|to)\s+(?:x|twitter)\b(?!\.)",
            re.I,
        ),
    ),
    ("x.user", "x", re.compile(r"\b(?:find|search\s+for|look\s+for)\s+(?:x|twitter)\s+accounts?\b"
                              r"|\baccounts?\s+on\s+(?:x|twitter)\s+(?:called|named|about)\b", re.I)),
    # -- mcp (family-level: server tools are dynamic) ------------------------------
    # matched separately below via the mcp.<server>. prefix vocabulary
)

_MCP_FAMILY_PATTERN = re.compile(r"\b(?:mcp|model\s+context\s+protocol)\b|\bmcp\.[\w.-]+", re.I)

# Segment separator inside a spoken intent: nothing, a bare dot, or dot+spaces — the shapes
# prose normalization leaves between the words of one dotted token ("pack. tool", "pack.tool").
_SEGMENT_JOIN = r"\.?\s*"


def _spoken_variant_patterns(intent: str, expansions: dict[str, str]) -> re.Pattern[str] | None:
    """A pattern that also matches the intent as the normalizer's shorthand table renamed it.

    `normalize_user_text` rewrites shorthand WORDS (`db` -> `database`), which silently renames
    a dotted intent token inside ordinary prose (`vool-database.db.create` becomes
    `vool-database. database. create`) — the dot-fusion repairs the split, not the rename, and
    measured served: the seat was lost for exactly that shape. The variant is built ONLY from
    that table's own expansions, so the matcher undoes precisely what the normalizer did, over
    the CLOSED set of registered intents — it never generalizes beyond them.
    """

    inverse: dict[str, set[str]] = {}
    for shorthand, full in expansions.items():
        full_word = str(full).strip().lower()
        if re.fullmatch(r"[a-z0-9_-]+", full_word):
            inverse.setdefault(full_word, set()).add(str(shorthand).strip().lower())
    parts: list[str] = []
    for segment in str(intent).split("."):
        segment = segment.lower()
        if not re.fullmatch(r"[a-z0-9_-]+", segment):
            return None
        # Both directions of the rewrite table: what this segment normalizes INTO
        # ("db" -> "database" rewrote the token the user typed), and what normalizes
        # INTO it (an un-normalized shorthand beside a canonical segment).
        alts = {segment}
        forward = str(expansions.get(segment, "")).strip().lower()
        if re.fullmatch(r"[a-z0-9_-]+", forward):
            alts.add(forward)
        alts |= inverse.get(segment, set())
        ordered = sorted(alts, key=len, reverse=True)
        parts.append("(?:" + "|".join(re.escape(alt) for alt in ordered) + ")")
    try:
        return re.compile(r"\b" + _SEGMENT_JOIN.join(parts) + r"\b", re.I)
    except re.error:
        return None


def _plugin_intents(text: str) -> tuple[str, ...]:
    """Explicitly named plugin tool intents (``pack.tool`` tokens in the message).

    Plugin intents are dynamic, so they cannot live in the static rule table: the
    registered plugin contracts are the vocabulary. A plugin tool the user names by its
    intent token is the sharpest explicit request there is.
    """
    try:
        from core.tool_registry import registered_tools

        plugin_intents = [
            str(contract.intent) for contract in registered_tools() if str(contract.source or "").startswith("plugin:")
        ]
    except Exception:
        return ()
    if not plugin_intents:
        return ()
    lowered = text.lower()
    # Prose normalization inserts a sentence space after a period, which breaks a dotted
    # intent token in half (`pack.tool` -> `pack. tool`) before this seam runs -- measured
    # served: the seat was lost and the turn routed to a plain-text lane. Matching also
    # against a whitespace-fused variant is safe because the vocabulary is the CLOSED set
    # of registered plugin intents: an ordinary sentence boundary only fuses when a
    # registered intent is literally that dotted pair.
    fused = re.sub(r"(?<=[a-z0-9_-])\.\s+(?=[a-z0-9_-])", ".", lowered)
    matched = [intent for intent in plugin_intents if intent.lower() in lowered or intent.lower() in fused]
    if len(matched) < MAX_EXPLICIT_INTENTS:
        # The same normalizer also rewrites shorthand WORDS inside the token (`db` ->
        # `database`), which the fusion cannot undo. The spoken-variant patterns cover
        # exactly that rename, still over the closed registered vocabulary.
        try:
            from core.input_normalizer import shorthand_expansions

            expansions: dict[str, str] = shorthand_expansions()
        except Exception:
            expansions = {}
        for intent in plugin_intents:
            if intent in matched or len(matched) >= MAX_EXPLICIT_INTENTS:
                continue
            pattern = _spoken_variant_patterns(intent, expansions)
            if pattern is not None and (pattern.search(fused) or pattern.search(lowered)):
                matched.append(intent)
    return tuple(matched[:MAX_EXPLICIT_INTENTS])


def _browser_lane_intents(text: str) -> tuple[str, ...]:
    """Explicitly named vool-browser intents (the C06 native product browser).

    Same law as plugin intents, and the same prose hazard: the vocabulary is the
    closed set of the lane's registered contracts, and the fused-text match
    undoes the normalizer's sentence-space split of a dotted intent token. A
    user naming `vool-browser.navigate` in prose is the sharpest explicit
    request there is.
    """
    try:
        from core.tool_registry import registered_tools

        lane_intents = [
            str(contract.intent)
            for contract in registered_tools()
            if str(contract.intent or "").startswith("vool-browser.")
        ]
    except Exception:
        return ()
    if not lane_intents:
        return ()
    lowered = text.lower()
    fused = re.sub(r"(?<=[a-z0-9_-])\.\s+(?=[a-z0-9_-])", ".", lowered)
    matched = [
        intent
        for intent in lane_intents
        if intent.lower() in lowered or intent.lower() in fused
    ]
    return tuple(matched[:MAX_EXPLICIT_INTENTS])


#: CONTEXT IS NOT AUTHORITY, applied to the database family.
#:
#: The same law C18 established for pasted commands: talking ABOUT a database, asking
#: what one IS, comparing two of them, quoting a command, or explicitly forbidding an
#: action is CONTEXT. None of it may seat the database skill, because the skill teaches
#: how to do database work and this turn is not database work.
#:
#: A veto rather than a narrower pattern: the verbs above are the right verbs, and a
#: request can legitimately contain both ("delete the cancelled rows" vs "explain how
#: DELETE works"). What separates them is whether the message is asking for the work or
#: asking about it, and that is what this names.
_DATABASE_CONTEXT_ONLY = re.compile(
    r"\b(?:explain|describe\s+what|what\s+(?:is|are|does)|what's\s+the\s+difference"
    r"|difference\s+between|compare|comparison|meaning\s+of|how\s+does\b[^.?!]{0,30}\bwork"
    r"|tell\s+me\s+about|read\s+an?\s+article|article\s+about|blog\s+post\s+about"
    r"|do\s+not\s+(?:touch|change|modify|run|execute|alter|delete|drop)"
    r"|don't\s+(?:touch|change|modify|run|execute|alter|delete|drop)"
    r"|without\s+(?:touching|changing|modifying|running)"
    r"|just\s+(?:describe|explain|summari[sz]e)"
    r"|summari[sz]e\s+(?:it|this)\s+only)\b",
    re.I,
)


# -- mailbox retrieval ------------------------------------------------------------------------------
# Asking for mail the user RECEIVED is decided by what the request is about, never by which verb it
# uses: "pull up the most recent email from the kiln repair shop", "search my mail for the invoice",
# "has the supplier emailed me" and "check my email" ask the same thing. The closed verb list this
# replaces (read/check/list/summarize/review/show/open/look at/go through) missed the first three, and
# those turns stayed in the tools-less chat lane or were planned onto the web (revision 6, Gate A).
#
# The anchors are the closed grammar of received mail, never a sender, subject or phrase:
#   * a mailbox the user owns or shares: "my mail", "my work inbox", "our inbox", "the inbox for ...";
#   * a mailbox state: "new emails", "unread mail";
#   * a message named by who sent it: "the email from <sender>", "the message <sender> sent";
#   * a message addressed to the user: "what <sender> emailed me", "did <sender> email me",
#     "the email <sender> sent me";
#   * a mailbox noun as the object of a bare imperative: "open inbox", "pull up emails".
# Instruction authority decides first, from the modules that already own each question: quoted words
# are someone's text (core.turn_prohibitions), a negated or prohibited clause is removed
# (core.retrieval_constraints), a how-to or authoring request asks for TEXT
# (core.instructional_request), and a hypothetical frame supplies its own premises
# (core.stipulated_frame). Two grammatical readings are refused here: a mailbox reference introduced
# as an example ("for example ..."), and a first-person report of what happened ("I got an email from
# my landlord") that carries no request. The recognizer seats `email.read` and the email family; the
# model chooses what to call, and approvals, policy and provider binding decide everything after it.
_MAIL_ITEM = r"(?:e-?mails?|mail|messages?)"
_MAILBOX = r"(?:e-?mails?|mail|inbox(?:es)?|mailbox(?:es)?)"
#: What a mail noun names when it is not mail content: the account's configuration, or a kind of text.
_NOT_MAIL_CONTENT = (
    r"(?:address(?:es)?|signatures?|passwords?|settings?|clients?|apps?|providers?|servers?|domains?|"
    r"aliases?|templates?|marketing|campaigns?|etiquette|lists?|formats?)"
)
#: Words that end a sender phrase: auxiliaries and the first/second persons (mail WE sent is outgoing).
_NOT_A_SENDER_WORD = r"(?:i|we|you|was|were|is|are|be|been|being|has|have|had|get|gets|got|getting|to|that|which)"
_MAILBOX_ANCHOR_RE = re.compile(
    # The possessive window admits up to three interposed modifiers, measured on
    # "Check my unread orchard supplier mail" (unread + a two-word sender name): at two, a
    # sender-named mailbox read fell through to plain chat and the turn narrated an inbox
    # check nothing ran. _NOT_MAIL_CONTENT still excludes the non-content readings.
    rf"\b(?:my|our)\s+(?:(?!{_NOT_MAIL_CONTENT}\b)[\w-]+\s+){{0,3}}?{_MAILBOX}\b(?!\s+{_NOT_MAIL_CONTENT}\b)"
    r"|\bthe\s+(?:inbox|mailbox)\b(?=\s*(?:$|[.,;:!?]|(?:for|from|in|about|at|since|to|and|or)\b))"
    rf"|\b(?:new|unread)\s+{_MAIL_ITEM}\b(?!\s+{_NOT_MAIL_CONTENT}\b)"
    rf"|\b{_MAIL_ITEM}\s+(?:(?:that|which)\s+)?(?:from|sent\s+by)\s+"
    r"(?!(?:going|being|getting|landing|ending|reaching|hitting)\b)[\w@&'.-]"
    rf"|\b{_MAIL_ITEM}\s+(?:(?:that|which)\s+)?(?:(?!{_NOT_A_SENDER_WORD}\b)[\w@&'.-]+\s+){{1,4}}sent\b"
    r"|\be-?mailed\s+(?:(?:it|that|this|them|something|anything)\s+)?(?:(?:over|back)\s+)?(?:to\s+)?(?:me|us)\b"
    r"|\b(?:did|does|do|has|have|had|will|would)\s+(?:[\w@&'.-]+\s+){1,5}?e-?mail\s+(?:me|us)\b"
    rf"|\b{_MAIL_ITEM}\b[^.?!]{{0,60}}?\b(?:sent|forwarded|wrote|replied)\s+(?:(?:it|that|this|them)\s+)?"
    r"(?:(?:over|back)\s+)?(?:to\s+)?(?:me|us)\b"
    rf"|^\s*(?:please\s+)?[a-z]+(?:\s+(?:up|out|through|over))?\s+(?:the\s+)?(?:inbox|mailbox|e-?mails?|mail)\b"
    rf"(?!\s+{_NOT_MAIL_CONTENT}\b)",
    re.IGNORECASE,
)
_EXAMPLE_FRAME_RE = re.compile(
    r"\b(?:for\s+example|for\s+instance|e\.g\.|such\s+as|an?\s+example\s+(?:of|request|prompt|query|sentence))",
    re.IGNORECASE,
)
#: A first-person report ("I got an email from ...", "We received ...") -- unless its verb is one of
#: the closed class of request frames ("I need", "I'd like", "I was wondering").
_FIRST_PERSON_REPORT_RE = re.compile(
    r"^\s*(?:i|we)\s+(?!(?:need|want|would|wanna|must|have\s+to|has\s+to|should|can|could|"
    r"am\s+looking|are\s+looking|was\s+wondering|were\s+wondering|wonder)\b)[a-z]+\b",
    re.IGNORECASE,
)
_REQUEST_FRAME_RE = re.compile(r"\?|\b(?:please|can\s+you|could\s+you|would\s+you|will\s+you)\b", re.IGNORECASE)
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

#: What a request about received mail seats: the read tool. The family fill seats the rest.
MAILBOX_RETRIEVAL_INTENTS: tuple[str, ...] = ("email.read",)


def mailbox_retrieval_intents(text: str) -> tuple[str, ...]:
    """The email read seat for a request about mail the user received, or () when the text is not one.

    Pure function of the text, like every other signal here: no policy reads (a disabled email lane
    is filtered at seating) and no execution.
    """
    try:
        from core.instructional_request import asks_for_instructions_not_execution
        from core.retrieval_constraints import analyze_retrieval_constraints
        from core.turn_prohibitions import _strip_quoted_spans
    except Exception:
        return ()
    constraints = analyze_retrieval_constraints(_strip_quoted_spans(str(text or "")))
    if constraints.forbids_all_tools:
        return ()
    eligible = " ".join(constraints.eligible_text.split())
    if not eligible or asks_for_instructions_not_execution(eligible):
        return ()
    try:
        from core.stipulated_frame import has_stipulated_frame

        if has_stipulated_frame(eligible):
            return ()
    except Exception:
        pass
    for sentence in _SENTENCE_BREAK_RE.split(eligible):
        anchor = _MAILBOX_ANCHOR_RE.search(sentence)
        if anchor is None:
            continue
        example = _EXAMPLE_FRAME_RE.search(sentence)
        if example is not None and example.start() < anchor.start():
            continue
        if _FIRST_PERSON_REPORT_RE.match(sentence) and not _REQUEST_FRAME_RE.search(sentence):
            continue
        return MAILBOX_RETRIEVAL_INTENTS
    return ()


# An outgoing message: a verb of sending or composing with a message noun as its object. The same
# instruction authority as the mailbox recognizer decides first; then a message noun that names a KIND
# of text -- a sample, an example, a generic or boilerplate email, an email template or format -- is a
# request for text the user will use themselves, not for this runtime's mail tools. "Draft a reply
# saying Wednesday works" composes THIS mailbox's reply and keeps its seat; "write me a sample email
# template" does not. (The noun classes are shared with the mailbox recognizer's _NOT_MAIL_CONTENT.)
_MESSAGE_NOUN = r"(?:e-?mails?|repl(?:y|ies)|responses?)"
_OUTGOING_MESSAGE_RE = re.compile(
    rf"\b(?:send|write|draft|compose|forward|reply|respond|answer)\b[^.?!]{{0,30}}?\b(?:an?\s+)?{_MESSAGE_NOUN}\b",
    re.IGNORECASE,
)
_TEXT_KIND_BEFORE_RE = re.compile(
    rf"\b(?:sample|example|generic|boilerplate|mock|dummy|practice|reusable|standard|model|typical|stock)\s+"
    rf"(?:[\w-]+\s+){{0,3}}?{_MESSAGE_NOUN}\b",
    re.IGNORECASE,
)
_TEXT_KIND_AFTER_RE = re.compile(rf"\b{_MESSAGE_NOUN}\s+{_NOT_MAIL_CONTENT}\b", re.IGNORECASE)
_TEXT_KIND_OBJECT_RE = re.compile(
    rf"\b(?:templates?|formats?|outlines?|examples?|samples?|boilerplate|wording|phrasing)\s+(?:for|of)\b"
    rf"[^.?!]{{0,40}}?\b{_MESSAGE_NOUN}\b",
    re.IGNORECASE,
)

#: What an outgoing-message request seats: the send tool. The family fill seats the draft tools.
OUTGOING_MESSAGE_INTENTS: tuple[str, ...] = ("email.send",)


def outgoing_message_intents(text: str) -> tuple[str, ...]:
    """The email send seat for a request to send or compose a message, or () when the text is not one.

    Pure function of the text, like mailbox_retrieval_intents: no policy reads, no execution.
    """
    try:
        from core.instructional_request import asks_how_to
        from core.retrieval_constraints import analyze_retrieval_constraints
        from core.turn_prohibitions import _strip_quoted_spans
    except Exception:
        return ()
    constraints = analyze_retrieval_constraints(_strip_quoted_spans(str(text or "")))
    if constraints.forbids_all_tools:
        return ()
    eligible = " ".join(constraints.eligible_text.split())
    if not eligible:
        return ()
    for sentence in _SENTENCE_BREAK_RE.split(eligible):
        anchor = _OUTGOING_MESSAGE_RE.search(sentence)
        if anchor is None:
            continue
        if asks_how_to(sentence):
            continue
        example = _EXAMPLE_FRAME_RE.search(sentence)
        if example is not None and example.start() < anchor.start():
            continue
        if (_TEXT_KIND_BEFORE_RE.search(sentence) or _TEXT_KIND_AFTER_RE.search(sentence)
                or _TEXT_KIND_OBJECT_RE.search(sentence)):
            continue
        return OUTGOING_MESSAGE_INTENTS
    return ()


_QUESTION_HEAD_RE = re.compile(r"^\s*(?:what|which|why|how|is|are|was|were|does|do|did|should|when|where|who)\b", re.I)
_REQUEST_RE = re.compile(
    r"\b(?:give\s+me|send\s+me|get\s+me|make\s+me|export|download|create|generate|produce|build|prepare|convert|"
    r"turn|save|put|write|draft|zip|compress|i\s+(?:need|want|would\s+like)|can\s+you|could\s+you|would\s+you|please)\b",
    re.I,
)


def _separate_file_execution_clause(text: str) -> bool:
    raw = str(text or "")
    from core.instructional_request import asks_how_to

    # Read distinct action clauses before applying the instructional veto. A
    # question mark does not turn "make me one" into a tutorial, and the artifact
    # referred to by "one" may be named only in the preceding explanation.
    parts = re.split(r"([.!?;]\s+|,\s*(?:and\s+)?|\band\s+)", raw, flags=re.I)
    instruction = re.sub(r"^\s*(?:can|could|would)\s+you\s+(?:please\s+)?", "", raw, flags=re.I)
    explanatory = asks_how_to(instruction) or re.match(
        r"^\s*(?:(?:explain|describe|tell\s+me|show\s+me)\s+how|teach\s+me\s+(?:how|to))\b", instruction, re.I
    )
    format_context = bool(re.search(r"\b(?:pdfs?|csv|tsv|docx|xlsx|pptx|word\s+documents?|excel|zip|tarball|powerpoint|spreadsheet|workbook|presentation|markdown|json|ya?ml|files?|downloadable)\b", raw, re.I))
    if not format_context:
        return False
    for separator, clause in zip(parts[1::2], parts[2::2], strict=True):
        action = re.sub(r"^\s*(?:then\s+|also\s+)?(?:please\s+)?", "", clause, flags=re.I)
        if asks_how_to(action):
            continue
        if format_context and re.match(r"(?:make|create|generate|produce)\s+(?:me\s+(?:(?:a|an)\s+)?(?:sample\s+)?one|one)\b", action, re.I):
            # Bare 'make one' needs an execution connector; 'make me one' is
            # unambiguously addressed to this assistant.
            if re.search(r"\bme\b", action, re.I) or re.search(r"\b(?:then|also)\b", clause, re.I):
                return True
        if re.match(r"(?:export|create|make|generate|produce)\s+one\s+of\s+(?:these|those|the|my|your)\b", action, re.I):
            return True
        # An explanation can list parallel steps: 'how to export a CSV and
        # create a workbook'. A bare conjunction does not turn the second step
        # into an order. Preserve an independent sentence or an explicit
        # execution connector/address before seating its artifact demand.
        if explanatory and not (
            separator.lstrip().startswith((".", "!", "?", ";"))
            or re.match(r"\s*(?:then|also)\b", clause, re.I)
            or re.search(r"\b(?:for\s+me|(?:make|create|give|send)\s+me)\b", action, re.I)
        ):
            continue
        if re.match(r"(?:export|create|make|generate|produce|prepare|give\s+me|send\s+me|zip|compress)\b", action, re.I):
            if "file.deliver" in resolve_demand_signals(action).explicit_intents:
                return True
    return False


#: "Can you explain how to make a PDF accessible?" asks for an explanation in a polite request's
#: clothes; it starts with no question word, so the imperative reading below took it as an order.
_POLITE_HOW_RE = re.compile(
    r"^\s*(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"(?:explain|tell\s+me|show\s+me|teach\s+me|walk\s+me\s+through|describe|remind\s+me)\s+how\b",
    re.I,
)
#: ...unless the same turn also hands the result over as a file ("... and give it to me as a PDF").
_DELIVERY_ORDER_RE = re.compile(
    r"\b(?:give|send|hand)\s+(?:it|this|that|them|the\s+\w+)\s+(?:over\s+|back\s+)?(?:to\s+me\s+)?(?:as|in)\b"
    r"|\bas\s+an?\s+(?:downloadable\s+)?file\b|\b(?:export|save|put)\s+(?:it|this|that|them)\s+(?:as|to|in(?:to)?)\b",
    re.I,
)


def asks_for_a_file(text: str) -> bool:
    """Is this turn ordering a FILE (the `file.deliver` demand), not asking ABOUT a format?

    The demand pattern above seats the tool; this is the stricter reading the routing gates use
    to send a turn into the tool loop. A question about formats ("is a PDF better than a Word
    document?", "how do I export to Excel from Sheets?") names formats without ordering anything,
    and must stay an ordinary answer.
    """
    raw = str(text or "")
    from core.instructional_request import asks_how_to

    if _separate_file_execution_clause(raw):
        return True
    if "file.deliver" not in resolve_demand_signals(raw).explicit_intents:
        return False
    instruction = re.sub(r"^\s*(?:can|could|would)\s+you\s+(?:please\s+)?", "", raw, flags=re.I)
    explanatory = asks_how_to(instruction) or re.match(
        r"^\s*(?:(?:explain|describe|tell\s+me|show\s+me)\s+how|teach\s+me\s+(?:how|to))\b", instruction, re.I
    )
    # ...unless the same turn also hands the result over as a file ("... and give it to me as a PDF").
    if explanatory and not _DELIVERY_ORDER_RE.search(raw):
        return False

    if not _QUESTION_HEAD_RE.match(raw):
        # An imperative ("write a script and zip it up", "export this to CSV") orders the file
        # even when it also asks for authored text: the file is the deliverable.
        return True
    from core.instructional_request import asks_for_instructions_not_execution

    if asks_for_instructions_not_execution(raw):
        return False
    return bool(_REQUEST_RE.search(raw))


# An X action is proposed only when the words ask VOOL to act. A question about what someone
# posted ("what did elon post today on x"), a how-to ("how do I post on X?") or words inside
# quotes are not asks to act, even though they hold an action verb.
_X_QUOTED = re.compile(r"\"[^\"]{0,400}\"|“[^”]{0,400}”|‘[^’]{0,400}’")
_X_NOT_AN_ASK = re.compile(
    r"\bwhat\s+(?:did|has|have|does|is|was)\b[^.?!]{0,40}?\b(?:post(?:ed|ing)?|tweet(?:ed|ing)?|repost(?:ed)?|retweet(?:ed)?)\b"
    r"|\bhow\s+(?:do|can|should|would)\s+(?:i|you|we|one|people)\b"
    r"|\bhow\s+to\s+(?:post|tweet|reply|repost|retweet|quote|like|follow|mute|dm)\b",
    re.I,
)
_X_ASK_TO_ACT = re.compile(
    r"(?:^|[.!?:;]\s*|\b(?:please|pls|plz|can\s+you|could\s+you|would\s+you|will\s+you|go\s+ahead\s+and|now|then|and|also|"
    r"just|want\s+you\s+to|need\s+you\s+to|i'?d\s+like\s+you\s+to|help\s+me)\s+)"
    r"(?:post|tweet|publish|reply|retweet|repost|quote|like|follow|unfollow|mute|unmute|dm|message|hide|unhide|create|make|start|add)\b",
    re.I,
)


def _x_action_asked(cleaned: str) -> bool:
    words = _X_QUOTED.sub(" ", cleaned)
    if not any(p.search(words) for i, _f, p in _RULES if i == "x.action.propose"):
        return False
    return not _X_NOT_AN_ASK.search(words) or bool(_X_ASK_TO_ACT.search(words))


# -- the owner's own files (read by core.execution_requirements, owner-local turns only) --------
# Measured 2026-10-27 with the tool-seating offer probe: "where is my resume", "send day4.jpg to my
# phone", "copy day4.jpg to my desktop" and 13 more asks no deterministic lane owns reached a pinned
# model with NO tool list (classed as plain chat), so the model could only talk about the files.
# The words must both ASK and name the owner's own object: a bare noun ("a pdf", "files") is prose.
_FILE_DEMAND_ASK = re.compile(
    r"\b(?:find|where|locate|look(?:ing)?\s+for|search|show|list|open|read|see|view|copy|move|send|fetch|"
    r"get|give|grab|do\s+i\s+(?:still\s+)?have|is\s+there|are\s+there|how\s+many|how\s+big|which|what(?:'s|\s+is|\s+are)?|"
    r"access|check|clean(?:\s+up)?|tidy|organi[sz]e|sort|delete|remove|rename|summari[sz]e|need|help\s+me|free\s+up|"
    r"is\s+(?:my|the|that)|are\s+(?:my|the))\b",
    re.I,
)
_FILE_DEMAND_OBJECT = re.compile(
    r"\b(?:my|our)\s+(?:[\w'\-]+\s+){0,3}?(?:files?|folders?|pdfs?|photos?|pictures?|pics|images?|screenshots?|"
    r"documents?|docs|resume|cv|invoices?|receipts?|downloads|desktop|videos?|movies|music|songs?|recordings?|"
    r"spreadsheets?|presentations?|stuff)\b"
    r"|\b(?:the|that)\s+(?:[\w\-]+\s+){0,2}?(?:invoice|receipt|resume|cv|contract|pdf|photo|picture|screenshot|file|"
    r"document|spreadsheet|presentation|video|recording)s?\s+(?:i\b|from\b|you\b|on\s+my\b|in\s+my\b|that\s+i\b)"
    r"|\b(?:find|where(?:'s|\s+is|\s+are))\s+(?:the|that)\s+(?:[\w\-]+\s+){0,2}?(?:invoice|receipt|resume|cv|contract)s?\b"
    r"|\b(?:apps?|applications?|programs?)\b[^.?!]{0,20}?\binstalled\b|\binstalled\s+(?:apps?|applications?|programs?)\b"
    r"|\b(?:photos?|pictures?|files?|videos?|documents?|pdfs?|songs?|screenshots?)\s+(?:do\s+i|i)\s+(?:still\s+)?have\b"
    r"|\bdo\s+i\s+(?:still\s+)?have\s+(?:the|that|a|any)\s+(?:[\w\-]+\s+){0,2}?(?:photos?|pictures?|files?|videos?|"
    r"documents?|pdfs?|songs?|screenshots?|recordings?|invoices?|receipts?)\b"
    r"|\bfree\s+up\s+(?:some\s+)?(?:disk\s+|storage\s+)?space\b"
    r"|\b(?:in|on|from)\s+(?:the\s+)?(?:downloads|desktop|documents)\b"
    r"|(?<![\w/.])[\w\-]+\.(?:txt|md|pdf|docx?|xlsx?|pptx?|csv|jpe?g|png|heic|gif|mov|mp4|mp3|zip|dmg|pages|numbers|key)\b",
    re.I,
)
# "my mac" or "this laptop" alone is the machine, not its files ("is my laptop worth upgrading",
# "what is my mac address"): it counts only beside a file noun or a verb that works on files.
_FILE_DEMAND_DEVICE = re.compile(
    r"\b(?:(?:my|our)\s+(?:[\w'\-]+\s+){0,2}?|this\s+)(?:computer|mac|macbook|imac|laptop|pc|machine|disk|drive)\b(?!\s+address)",
    re.I,
)
_FILE_DEMAND_FILE_WORK = re.compile(
    r"\b(?:files?|folders?|pdfs?|photos?|pictures?|pics|images?|screenshots?|documents?|videos?|songs?|recordings?|"
    r"name|version|mac\s*os|os|specs?|model|ram|chip|processor|cpu|serial|apps?|installed|"
    r"access|space|storage|find|locate|search|copy|move|send|delete|remove|rename|clean(?:\s+up)?|tidy|organi[sz]e|saved?|stored?|downloaded)\b",
    re.I,
)
_FILE_DEMAND_PROSE = re.compile(
    r"\b(?:how\s+(?:do|can|should|would)\s+(?:i|you|one)|what\s+should|should\s+(?:i|my|we)\b|best\s+way|why\s+(?:is|does|do|are)|"
    r"tips?\s+(?:for|on)|advice|recommend|in\s+(?:python|linux|windows|excel|bash|terminal|javascript))\b",
    re.I,
)


def owner_file_intents(text: str) -> tuple[str, ...]:
    """The machine file tools an owner file ask needs seated ahead of family ranking (at most 3).

    The filesystem family's read-only-first ranking filled the round with list_directory,
    inspect_specs, disk_usage and find_folder, so "where is my resume" had no find_file and
    "search my documents for milk" no read_file; inside a project the workspace tools took the
    seats, so "which apps are installed" had no inspect_specs.
    """
    cleaned = " ".join(str(text or "").split())
    if re.search(r"\b(?:phone|iphone|android|mobile)\b", cleaned, re.I):
        return ("machine.find_file", "machine.send_to_phone", "machine.list_directory")
    if re.search(r"\b(?:free\s+up|space|big(?:gest)?|large(?:st)?|huge|heavy|taking\s+up)\b", cleaned, re.I):
        return ("machine.find_largest", "machine.disk_usage", "machine.find_file")
    if re.search(r"\b(?:apps?|applications?|programs?|installed|name|version|mac\s*os|specs?|model|ram|chip|processor|cpu|serial)\b", cleaned, re.I):
        return ("machine.inspect_specs", "machine.find_file", "machine.list_directory")
    return ("machine.find_file", "machine.read_file", "machine.list_directory")


def owner_file_demand(text: str) -> bool:
    """Whether the words ask for something done with or found among the owner's own files.

    Pure function of the text. The caller decides whose turn it is: only the owner's own session
    (or a phone the owner gave the computer grant) may be offered the machine file tools for it.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned or _FILE_DEMAND_PROSE.search(cleaned):
        return False
    if not _FILE_DEMAND_ASK.search(cleaned):
        return False
    if _FILE_DEMAND_OBJECT.search(cleaned):
        return True
    return bool(_FILE_DEMAND_DEVICE.search(cleaned) and _FILE_DEMAND_FILE_WORK.search(cleaned))


def resolve_demand_signals(text: str) -> DemandSignals:
    """Resolve the user's words into explicit intents and required families.

    Pure function of the text (plus registered plugin intents). No policy reads, no
    availability checks — an explicitly requested but disabled tool surfaces here and
    is then filtered at seating, where its disabled status is honest metadata.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return DemandSignals()

    intents: list[str] = []
    families: list[str] = []
    write_demand = False

    def _note(intent: str, family: str) -> None:
        if intent not in intents and len(intents) < MAX_EXPLICIT_INTENTS:
            intents.append(intent)
        if family not in families and len(families) < MAX_REQUIRED_FAMILIES:
            families.append(family)

    if is_explicit_code_repair(cleaned):
        _note("code.task.open", "code")

    from core.vool_browser.live_requests import requested as browser_requested

    if browser_requested(cleaned):
        _note("vool-browser.session.open", "web")
        _note("vool-browser.inspect", "web")

    database_is_context_only = bool(_DATABASE_CONTEXT_ONLY.search(cleaned))
    is_wallet_move = any(
        pattern.search(cleaned) for intent, _family, pattern in _RULES if intent == "wallet.propose"
    )
    for intent, family, pattern in _RULES:
        if pattern.search(cleaned):
            if family == "database" and database_is_context_only:
                # Discussed, not demanded. Recorded here rather than by narrowing the
                # pattern, so the reason a database turn did NOT seat the skill is a
                # named law instead of a regex that quietly failed to match.
                continue
            if intent == "x.action.propose" and not _x_action_asked(cleaned):
                continue
            if intent in {"web.chain_lookup", "token.research"} and is_wallet_move:
                # A send/pay turn belongs to VOOL's wallet alone (its caps, approval and
                # receipt): the destination address in the words is not a chain-data
                # question, so the read-only lookup is not offered beside the proposal.
                continue
            _note(intent, family)
            if family == "workspace" and intent.startswith(
                ("workspace.write", "workspace.replace", "workspace.apply", "workspace.rollback")
            ):
                write_demand = True

    # Share the reader with front-door ownership: an anaphoric artifact order
    # must seat file.deliver too, not only classify as a file after seating.
    if _separate_file_execution_clause(cleaned):
        _note("file.deliver", "file")

    # A save/store request whose object is a saved contact ("save Alex Chen as a contact",
    # "store Zoë Ng in my address book") is a Contacts demand even though the word-form rules above
    # recognize only possession/lookup shapes: the save-object shape has exactly one owner
    # (core.contacts.requests — the same reader the memory lane defers to), and reading it here
    # keeps the lane release, the tool offer and the memory yield on the same words instead of a
    # second list that can drift (the contacts.search tightening dropped this form, stranding the
    # save turn in the tools-less lane).
    try:
        from core.contacts.requests import storage_object_is_a_contact

        if storage_object_is_a_contact(cleaned):
            _note("contacts.save", "contacts")
            write_demand = True
    except Exception:
        pass
    for intent in mailbox_retrieval_intents(cleaned):
        _note(intent, "email")

    for intent in outgoing_message_intents(cleaned):
        _note(intent, "email")

    for intent in _plugin_intents(cleaned):
        _note(intent, "plugin")

    for intent in _browser_lane_intents(cleaned):
        _note(intent, "web")

    if _MCP_FAMILY_PATTERN.search(cleaned) and "mcp" not in families and len(families) < MAX_REQUIRED_FAMILIES:
        families.append("mcp")

    return DemandSignals(
        explicit_intents=tuple(intents),
        required_families=tuple(families),
        write_demand=write_demand,
    )


__all__ = [
    "MAILBOX_RETRIEVAL_INTENTS",
    "MAX_EXPLICIT_INTENTS",
    "MAX_REQUIRED_FAMILIES",
    "DemandSignals",
    "mailbox_retrieval_intents",
    "owner_file_demand",
    "owner_file_intents",
    "resolve_demand_signals",
]
