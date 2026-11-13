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
    ``web_by_online_reading_only``  the web was seated only because the ask is a public fact
                            that changes or fresh information (``core.online_asks``), not
                            because the person asked for the web in so many words
    """

    explicit_intents: tuple[str, ...] = ()
    required_families: tuple[str, ...] = ()
    write_demand: bool = False
    web_by_online_reading_only: bool = False


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

# Typo-tolerant database nouns ("databse", "datbase", "tabel"): the people asking are not
# engineers, and a one-letter slip must not turn a database ask into a promise-only chat.
_DB_TABLE = r"(?:tables?|tabels?|tabl|tbl)"
_DB_NOUN = (
    r"\b(?:databases?|databses?|datbases?|dbs?|" + _DB_TABLE + r"|schemas?|columns?|rows?|"
    r"sqlite|postgres|mysql)\b"
)

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
    # -- project archaeology (file history across VOOL's own records) ---------
    # "Which task changed this file?" asks for a file's history: which turn, task or effect wrote
    # it and when. That is the archaeology trace (core/project_archaeology.py), not a code task's
    # report: with only `code.task.report` on the table the model passed the question itself as a
    # task id and answered "No code task `which task changed this file` exists in the journal"
    # (skills A/B on f4f048f7, qwen3:8b). A question head is required, so an instruction to
    # change a file never seats it.
    (
        "operator.command.archaeology.trace",
        "operator",
        re.compile(
            r"\b(?:which|what)\s+(?:of\s+(?:your|the|my|our|these|those)\s+)?(?:(?:code|coding|earlier|past|previous|last)\s+)?(?:tasks?|turns?|runs?|jobs?|chats?|sessions?|steps?|actions?)\b"
            r"[^.?!]{0,40}?\b(?:changed|modified|edited|touched|wrote|rewrote|overwrote|deleted|removed|moved|renamed|created|updated|broke)\b"
            r"[^.?!]{0,40}?(?:\bfiles?\b|\bfolders?\b|[\w./-]+\.[A-Za-z0-9]{1,6}\b)"
            r"|\bwho\s+(?:last\s+)?(?:changed|modified|edited|touched|wrote|overwrote|deleted|removed|moved|renamed)\b"
            r"[^.?!]{0,40}?(?:\bfiles?\b|[\w./-]+\.[A-Za-z0-9]{1,6}\b)"
            r"|\b(?:history|lineage)\s+of\s+(?:this|that|the|my|our)\s+(?:file|folder)\b"
            r"|\bwhat\s+happened\s+to\s+(?:this|that|the|my|our)\s+(?:file|folder)\b",
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
            r"\b(?:search|google|look\s+up|find)\b[^.?!]{0,60}?\b(?:web|online|internet|google|bing|duckduckgo|wikipedia)\b"
            # A named page with no URL: "open the Wikipedia page for the Daugava river and tell me
            # how long it is" seated nothing (battery v9, installed build) and the model, with no
            # tool, produced no usable answer. The search finds the page; the fetch reads it.
            r"|\b(?:open|read|check|visit|go\s+to|pull\s+up)\b[^.?!]{0,30}?\b(?:wikipedia|website|web\s*page|web\s*site)\b"
            # An answer asked for FROM a named web source is that source's content, not stable
            # knowledge: "according to wikipedia, when was the eiffel tower finished?" was answered
            # DIRECT with no lookup (run-modes test, 2026-11-11). "what is wikipedia?" stays out.
            r"|\b(?:according\s+to|per|as\s+per)\s+(?:the\s+)?wikipedia\b"
            r"|\bwikipedia(?:'s)?\s+(?:says?|said|states?|lists?|claims?|article|page|entry)\b"
            r"|\bwhat\s+(?:does|did)\s+(?:the\s+)?wikipedia\s+(?:say|list|state)\b"
            r"|\b(?:on|from|in)\s+wikipedia\b"
            # "wikipedia: what year did the titanic sink", "wiki: capital of mongolia" (a source
            # label leading the question) and "check wiki for ...", "what does the wiki say about
            # ..." -- "wiki" as people shorten Wikipedia (0.7.8 sweep block 2c: the first went to
            # memory recall, the second to background-task status). "what's a wiki" and "set up a
            # wiki for my team" name no source to read from and stay out.
            r"|^\s*wiki(?:pedia)?\s*:\s*\S"
            r"|\b(?:check|search|open|read|look\s+(?:it\s+)?up\s+(?:on|in))\s+(?:the\s+)?wiki\b(?!\s+(?:page\s+)?(?:for\s+)?(?:my|our|the\s+team|team))"
            r"|\b(?:on|from|in|per|according\s+to)\s+(?:the\s+)?wiki\b(?!\s*(?:page\s+)?(?:for|of)\s+(?:my|our))"
            r"|\bwhat\s+(?:does|did)\s+(?:the\s+)?wiki(?:pedia)?\s+(?:say|list|state)\b"
            # "look it up: …", "look this up", "check online", "search online": an explicit ask to
            # go and find out (0.7.8 sweep block 2: "look it up: what year did the berlin wall
            # fall" made no tool call).
            r"|(?:^|[.!?:]\s*|\b(?:please|pls|can\s+you|could\s+you|would\s+you|go\s+and|just)\s+)look\s+(?:it|this|that)\s+up\b"
            r"|(?:^|[.!?:]\s*|\b(?:please|pls|can\s+you|could\s+you|would\s+you|go\s+and|just)\s+)(?:check|search|look)\s+(?:it\s+)?(?:up\s+)?online\b",
            re.I,
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
            r"\b(?:show|list|describe|inspect|count|check|look\s+at|see|view|open|"
            r"what(?:'?s|\s+is|\s+are)\s+(?:in|inside)|how\s+many)\b"
            r"[^.?!]{0,60}?" + _DB_NOUN,
            re.I,
        ),
    ),
    (
        "database.query",
        "database",
        re.compile(
            r"\b(?:query|select|search|fetch|pull|read|find|get|give\s+me|tell\s+me)\b[^.?!]{0,60}?"
            + _DB_NOUN
            + r"|\b(?:from|in|of|on)\s+(?:the\s+|my\s+)?(?:\w+\s+)?" + _DB_TABLE + r"\b"
            r"|\bhow\s+many\b[^.?!]{0,40}?\b(?:are|r|is|do|does|did|have|has)\b[^.?!]{0,40}?" + _DB_NOUN,
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
    # -- Social content manager (L14): planning and managing content across platforms selects
    # the vool-social-content-manager skill. It seats no tool (the family is declared by that
    # skill alone), so a match adds planning guidance and never a publishing path. Verb or
    # noun-phrase anchored: a bare mention of "social media", "post" or one platform seats
    # nothing, and X-only drafting stays with the editorial rules above.
    (
        "social_content.calendar",
        "social_content",
        re.compile(
            r"\bcontent\s+calendars?\b"
            r"|\b(?:posting|posts?|publishing|social(?:\s+media)?)\s+(?:plans?|schedules?|calendars?)\b",
            re.I,
        ),
    ),
    (
        "social_content.campaign",
        "social_content",
        re.compile(
            # A planning verb and a campaign: "plan the campaign", "make a social content
            # campain". Campaigns that are not content work (a tabletop game, an election,
            # a military one) are refused inside the window, not after the fact.
            r"\b(?:plan|planning|make|run|running|manage|managing|build|create|set\s+up|organi[sz]e|"
            r"coordinate|map\s+out|track|start|launch|prepare|draft|outline|help\s+(?:me\s+)?(?:with|plan|run))\b"
            r"(?:(?!\b(?:d&d|dnd|dungeons?|tabletop|rpg|pathfinder|warhammer|game|gaming|"
            r"political|election|electoral|presidential|military|bombing|vaccination)\b)[^.?!]){0,60}?"
            r"\bcampai(?:gn|ng|n)s?\b"
            r"(?![^.?!]{0,40}?\b(?:d&d|dnd|tabletop|rpg|pathfinder|warhammer|game|gaming|"
            r"political|election|electoral|presidential|military)\b)",
            re.I,
        ),
    ),
    (
        "social_content.manage",
        "social_content",
        re.compile(
            r"\b(?:manage|managing|plan|run|organi[sz]e|coordinate)\b[^.?!]{0,40}?"
            r"\bsocial(?:\s+media)?\s+(?:content|posts|channels|accounts|presence)\b",
            re.I,
        ),
    ),
    (
        "social_content.adapt",
        "social_content",
        re.compile(
            r"\b(?:adapt|repurpose|cross-?post|rework|reshape)\b[^.?!]{0,60}?\bfor\s+"
            r"(?:linkedin|bluesky|mastodon|threads|telegram|discord|each\s+platform|every\s+platform|"
            r"(?:all\s+)?(?:our\s+|the\s+)?(?:other\s+)?platforms)\b",
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
    ("x.bookmarks", "x", re.compile(
        r"\b(?:my\s+)?(?:x|twitter)\s+bookmarks?\b|\bbookmarks?\s+(?:on|from)\s+(?:x|twitter)\b"
        r"|\b(?:bookmark|unbookmark)\s+(?:this|that|the)\s+(?:tweet|x\s+post)\b", re.I)),
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
    # One account: "look up the X account @bob", "who is @bob on X?", "@bob's X profile".
    ("x.user", "x", re.compile(r"\b(?:find|search\s+for|look\s+for)\s+(?:x|twitter)\s+accounts?\b"
                              r"|\baccounts?\s+on\s+(?:x|twitter)\s+(?:called|named|about)\b"
                              r"|\b(?:look\s+up|find|show\s+me|check|open)\s+(?:the\s+|this\s+|that\s+)?"
                              r"(?:x|twitter)\s+(?:account|profile|user|handle)\b"
                              r"|\bwho\s+is\s+@[A-Za-z0-9_]{1,15}\b[^.?!]{0,20}?\b(?:on|from)\s+(?:x|twitter)\b(?!\.)"
                              r"|(?<![\w@])@[A-Za-z0-9_]{1,15}(?:'s|’s)\s+(?:x|twitter)\s+(?:account|profile|bio)\b"
                              r"|\b(?:x|twitter)\s+(?:account|profile)\s+(?:of|for)\s+@[A-Za-z0-9_]{1,15}\b", re.I)),
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


# Words too ordinary to say which plugin a sentence is about, on top of every word the built-in
# tools already use to describe themselves (read at run time, so the list follows the catalog).
_PLUGIN_ORDINARY_WORDS = frozenset(
    ["plugin", "plugins", "addon", "addons", "example", "examples", "sample", "tool", "tools", "vool", "text", "texts", "supplied", "provided", "given", "through", "using", "bundled", "local", "connection", "server", "servers", "compute", "computes", "computed", "return", "returns", "returned", "result", "results", "value", "values", "input", "inputs", "output", "outputs", "string", "strings", "number", "numbers", "format", "formatted", "length", "separated", "please", "thing", "things", "which", "their", "there", "about", "would", "could", "should", "these", "those", "where", "while", "what", "when", "with", "from", "this", "that", "your", "into", "over"]
)
_PLUGIN_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PLUGIN_NEAR = 0.88           # one slipped or swapped letter in a word of seven or more
_PLUGIN_TELLING_LENGTH = 8    # a word this long (or carrying a digit) names its plugin on its own


def _plugin_word_forms(word: str) -> set[str]:
    """A word as people write it: with or without its hyphens, with or without a plural s."""
    forms = {word, word.replace("-", "")}
    forms |= {form[:-1] for form in tuple(forms) if len(form) > 5 and form.endswith("s")}
    return forms


_PluginWords = tuple[tuple[str, frozenset[str], frozenset[str], frozenset[str], frozenset[str]], ...]
_PLUGIN_SPOKEN_CACHE: tuple[tuple, _PluginWords] | None = None
#: A plain ask for a quantity. Grammar, not a topic: what is being counted comes from the plugin's own name.
_PLUGIN_QUANTITY_ASK = re.compile(r"\b(?:how\s+many|number\s+of)\s+([a-z0-9-]+)\b[^:?!.]{0,40}:\s*\S+\s+\S")


def _quantity_of(lowered: str, name_words: frozenset[str]) -> bool:
    """ "how many words are in: red green blue" -- the counted thing is a word of the plugin's name
    AND the material is handed over after a colon. "how many words should a cover letter have?"
    hands nothing over and stays an ordinary question."""
    match = _PLUGIN_QUANTITY_ASK.search(lowered)
    if not match:
        return False
    counted = match.group(1)
    return counted in name_words or (counted.endswith("s") and counted[:-1] in name_words)
_PLUGIN_DISPLAY: dict[str, str] = {}
_PLUGIN_REMOVED: dict[str, tuple[str, tuple]] = {}


def _plugin_spoken_vocabulary() -> _PluginWords:
    """Per added plugin: the names people call it by, and the words its own description uses.

    Read from the packages the person installed and reviewed (the lifecycle records and each
    package's own manifest), whether or not they can run right now -- a plugin that is turned off
    is still what the person is asking for, and they are owed the reason. Packs that ship inside
    VOOL keep their own hand-written rules above. A closed vocabulary, like the dotted intent
    tokens: nothing here reads open prose.
    """
    global _PLUGIN_SPOKEN_CACHE
    from pathlib import Path

    from core import plugin_lifecycle
    from core.plugin_tools import load_manifest
    from core.tool_registry import registered_tools, registry_epoch

    installed = [r for r in plugin_lifecycle.records() if r.stage != plugin_lifecycle.STAGE_UNINSTALLED and r.root]
    key = (registry_epoch(), tuple((r.plugin_id, r.root, r.verified_digest) for r in installed))
    if _PLUGIN_SPOKEN_CACHE is not None and _PLUGIN_SPOKEN_CACHE[0] == key:
        return _PLUGIN_SPOKEN_CACHE[1]
    built_in_words: set[str] = set()
    for contract in registered_tools():
        if str(contract.source or "").startswith("plugin:"):
            continue
        for word in _PLUGIN_WORD.findall(f"{contract.intent} {contract.description}".lower().replace("_", " ").replace(".", " ")):
            built_in_words |= _plugin_word_forms(word)
    rows: list[tuple[str, frozenset[str], frozenset[str], frozenset[str], frozenset[str]]] = []
    for record in installed:
        try:
            block = load_manifest(Path(record.root) / ".codex-plugin" / "plugin.json").vool
        except Exception:
            block = None
        tools = [tool for tool in (block or {}).get("tools") or () if isinstance(tool, dict)]
        if not block or not tools:
            continue
        display = str(block.get("name") or "")
        descriptions = [str(block.get("description") or ""), *(str(tool.get("description") or "") for tool in tools)]
        # A tool's own short name counts only when it is unmistakable ("sha256"); "create" or
        # "backup" are ordinary words that many requests use.
        leaves = [str(tool.get("intent") or "").rsplit(".", 1)[-1] for tool in tools]
        telling_leaves = [leaf for leaf in leaves if any(ch.isdigit() for ch in leaf)]
        names: set[str] = set()
        for label in (record.plugin_id, display, *telling_leaves):
            kept = [w for w in _PLUGIN_WORD.findall(label.lower().replace("_", " ")) for w in w.split("-")]
            fused = "".join(w for w in kept if w not in _PLUGIN_ORDINARY_WORDS)
            if len(fused) >= 6:
                names.add(fused)
        terms: set[str] = set()
        shared: set[str] = set()
        for word in _PLUGIN_WORD.findall(" ".join([display, *descriptions]).lower()):
            forms = _plugin_word_forms(word)
            if forms & _PLUGIN_ORDINARY_WORDS:
                continue
            # Words the built-in tools also use ("count", "words") say less: kept apart, and
            # two of them are needed where one of the plugin's own words is enough.
            digits = any(ch.isdigit() for form in forms for ch in form)   # "sha-256" is telling anywhere
            (shared if forms & built_in_words and not digits else terms).update(f for f in forms if len(f) >= 5)
        # The words of the plugin's own name ("Word count" -> word, count): with a plain quantity
        # ask ("how many words are in: ...") one of them is enough.
        name_words: set[str] = set()
        for word in _PLUGIN_WORD.findall(display.lower()):
            forms = _plugin_word_forms(word)
            if not forms & _PLUGIN_ORDINARY_WORDS:
                name_words |= {f for f in forms if len(f) >= 4}
        rows.append((record.plugin_id, frozenset(names), frozenset(terms), frozenset(shared - terms), frozenset(name_words)))
        _PLUGIN_DISPLAY[record.plugin_id] = display or record.plugin_id
    # A plugin that was here a moment ago and is now uninstalled is still what the person may ask
    # for. Its words are remembered for as long as the app runs, only to say that it was removed.
    present = {row[0] for row in rows}
    if _PLUGIN_SPOKEN_CACHE is not None:
        still_installed = {record.plugin_id for record in installed}
        for row in _PLUGIN_SPOKEN_CACHE[1]:
            # Gone from the lifecycle records, not merely unreadable: a plugin whose files
            # changed is still installed and has its own "review it again" notice.
            if row[0] not in present and row[0] not in still_installed:
                _PLUGIN_REMOVED[row[0]] = (_PLUGIN_DISPLAY.get(row[0], row[0]), row)
    for plugin_id in {record.plugin_id for record in installed}:
        _PLUGIN_REMOVED.pop(plugin_id, None)
    _PLUGIN_SPOKEN_CACHE = (key, tuple(rows))
    return _PLUGIN_SPOKEN_CACHE[1]


def _plugin_spoken(text: str) -> bool:
    return bool(plugins_named_in_words(text))


def plugins_named_in_words(text: str) -> tuple[str, ...]:
    """The added plugins the words name, or whose own thing the words ask for.

    People say "the word count plugin" or "fingerprint of this text"; nobody types a dotted
    tool name. This answers only WHETHER the plugin family is wanted -- which tool, if any, is
    still the model's choice among the tools the owner switched on, and running one is still the
    permission controller's decision. A name counts on its own. A describing word counts on its
    own when it is long or carries a digit ("fingerprint", "sha-256"); shorter ones, and words the
    built-in tools use too, need company ("count" + "words"), so "count the files here" stays
    where it was. A slipped letter in a long
    word ("fingerpint") still counts.
    """
    try:
        vocabulary = _plugin_spoken_vocabulary()
    except Exception:
        return ()
    return _plugins_matching(text, vocabulary)


def removed_plugins_named_in_words(text: str) -> tuple[str, ...]:
    """Display names of plugins uninstalled while the app has been running that the words NAME.

    Stricter than the offer: this decides a ready-made reply, so only the plugin's own name or a
    digit-bearing term of its ("sha-256") counts. Ordinary words it shared ("count", "words")
    do not, or every later question about words would be answered with "was removed".
    """
    try:
        _plugin_spoken_vocabulary()
    except Exception:
        return ()
    rows = {plugin_id: row for plugin_id, (_display, row) in _PLUGIN_REMOVED.items()}
    return tuple(_PLUGIN_REMOVED[plugin_id][0] for plugin_id in _plugins_matching(text, tuple(rows.values()), named_only=True))


def reset_plugin_spoken_memory() -> None:
    """Forget the remembered vocabulary (a different profile, or a test's own plugins)."""
    global _PLUGIN_SPOKEN_CACHE
    _PLUGIN_SPOKEN_CACHE = None
    _PLUGIN_DISPLAY.clear()
    _PLUGIN_REMOVED.clear()


def _plugins_matching(text: str, vocabulary, *, named_only: bool = False) -> tuple[str, ...]:
    if not vocabulary:
        return ()
    from difflib import SequenceMatcher

    words = _PLUGIN_WORD.findall(text.lower().replace("_", " "))[:160]
    pieces = [piece for word in words for piece in word.split("-")]
    said: set[str] = set()
    for word in words:
        said |= _plugin_word_forms(word)
    runs = set(said)
    for size in (2, 3):
        runs |= {"".join(pieces[i : i + size]) for i in range(len(pieces) - size + 1)}

    def near(target: str, candidates: set[str]) -> bool:
        if len(target) < 7:
            return False
        return any(
            abs(len(candidate) - len(target)) <= 2
            and candidate[0] == target[0]
            and SequenceMatcher(None, candidate, target).ratio() >= _PLUGIN_NEAR
            for candidate in candidates
        )

    lowered = text.lower()
    named: list[str] = []
    for plugin_id, names, terms, shared, name_words in vocabulary:
        hits = {term for term in terms if term in said or near(term, said)}
        by_name = any(name in runs or near(name, runs) for name in names)
        if named_only:
            if by_name or any(any(ch.isdigit() for ch in term) for term in hits):
                named.append(plugin_id)
            continue
        if (
            by_name
            or _quantity_of(lowered, name_words)
            or any(len(term) >= _PLUGIN_TELLING_LENGTH or any(ch.isdigit() for ch in term) for term in hits)
            or len({term.rstrip("s") for term in hits | {term for term in shared if term in said}}) >= 2
        ):
            named.append(plugin_id)
    return tuple(named)


#: "Do that once more, for this other thing." Closed grammar; names no topic.
_FOLLOW_UP_REPEAT = re.compile(
    r"(?i)\b(?:the\s+)?same(?:\s+thing)?(?:\s+again)?\s*(?:for|with|on|to|:)"
    r"|\bdo\s+(?:it|that|this)\s+(?:again|for|with|on)\b"
    r"|\bagain\s+(?:for|with|on)\b"
    r"|\b(?:another|one\s+more)\s+one\b"
)
#: "and characters?" -- a continuation that adds one thing to the request before it.
_FOLLOW_UP_OPENING = re.compile(r"(?i)^\s*(?:ok(?:ay)?[,.!]?\s+|great[,.!]?\s+|cool[,.!]?\s+|thanks?[,.!]?\s+)?(?:and|now|also|what\s+about|how\s+about)\b")
#: A turn that is only an acknowledgement; a later follow-up reaches back over it.
_FOLLOW_UP_ACK = re.compile(
    r"(?i)^\s*(?:ok(?:ay)?|thanks?|thank\s+you|thx|ty|great|nice|cool|perfect|got\s+it|good|cheers)"
    r"(?:[\s,.!]+(?:that\s+helps|thanks?|thank\s+you|a\s+lot|very\s+much|mate))*[\s.!]*$"
)
_FOLLOW_UP_WORDS = 14
_FOLLOW_UP_REACH = 4


def plugin_follow_up_targets(text: str, source_context) -> tuple[str, ...]:
    """The added plugins a short follow-up continues to ask; () when it is not such a follow-up.

    Measured in real chat (2026-11-12): after "count the words in ...", the follow-up "and
    characters?" was not offered the plugin, and the model asked what "characters" meant; "now do
    the same for ..." produced an invented result. Each turn's words were read alone, and a
    follow-up names nothing by itself.

    Deliberately narrow, because the turn before it does not own everything said next. The turn
    must ask for nothing of its own and be short, and it must be one of two shapes: a repeat
    ("same for ...", "do that for ...", "again with ..."), or a continuation ("and ...", "what
    about ...") that uses a word the earlier plugin's own name or description uses ("characters"
    after a word count). "and what is the capital of France?" is neither. The nearest earlier
    request in this chat -- stepping over acknowledgements and other such follow-ups, a few turns
    at most -- must have asked for an added plugin. This only widens what the model is OFFERED;
    whether a tool runs is still the model's call and the owner's permissions.
    """
    current = " ".join(str(text or "").split())
    if not current or not isinstance(source_context, dict):
        return ()
    words = _PLUGIN_WORD.findall(current.lower().replace("_", " "))
    if not 0 < len(words) <= _FOLLOW_UP_WORDS or _FOLLOW_UP_ACK.match(current):
        return ()
    repeat = bool(_FOLLOW_UP_REPEAT.search(current))
    if not repeat and not _FOLLOW_UP_OPENING.match(current):
        return ()
    signals = resolve_demand_signals(current)
    if signals.explicit_intents or signals.required_families:
        return ()
    earlier = [
        " ".join(str(message.get("content") or "").split())
        for message in list(source_context.get("conversation_history") or [])
        if isinstance(message, dict) and str(message.get("role") or "").strip().lower() == "user"
    ]
    if earlier and earlier[-1].casefold() == current.casefold():
        earlier.pop()   # the turn being read is usually the last message of its own history
    asked: tuple[str, ...] = ()
    for previous in reversed(earlier[-_FOLLOW_UP_REACH:]):
        if not previous or _FOLLOW_UP_ACK.match(previous):
            continue
        signals = resolve_demand_signals(previous)
        if "plugin" in signals.required_families:
            from core.plugin_tools import plugin_id_for_intent

            asked = tuple(dict.fromkeys(
                [p for p in (plugin_id_for_intent(i) for i in signals.explicit_intents) if p]
                or plugins_named_in_words(previous)
            ))
            break
        if signals.explicit_intents or signals.required_families:
            return ()
        if not (_FOLLOW_UP_REPEAT.search(previous) or _FOLLOW_UP_OPENING.match(previous)):
            return ()   # an ordinary turn in between: the plugin request is no longer the subject
    if not asked or repeat:
        return asked
    said: set[str] = set()
    for word in words:
        said |= {form.rstrip("s") for form in _plugin_word_forms(word)}
    try:
        vocabulary = _plugin_spoken_vocabulary()
    except Exception:
        return ()
    continued = [
        row[0] for row in vocabulary
        if row[0] in asked and said & {w.rstrip("s") for w in (row[2] | row[3] | row[4])}
    ]
    return tuple(continued)


def plugin_follow_up(text: str, source_context) -> bool:
    return bool(plugin_follow_up_targets(text, source_context))


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
    # Received-framing: "check the last 5 Kucoin emails i have received" — the mail noun and
    # RECEIVED within one clause, no possessive needed. Measured 2026-11-05/06 (operator's
    # live tests): this exact phrasing failed to seat email.read and the model answered a
    # capability denial instead of reading the inbox.
    rf"|\b{_MAIL_ITEM}\b[^.?!]{{0,60}}?\breceived\b"
    # "did paysera send me anything?", "has my bank written to me?", "did I get anything from X?":
    # a sender asked about by name with no mail noun at all. Measured 2026-11-11 on the installed
    # build: "did paysera send me anything?" reached a model with no tool and failed. Bounded
    # objects only (anything / something / a message / mail): "did mom send me money" stays out.
    r"|\b(?:did|has|have)\s+(?!i\b|you\b|we\b)[\w@&'.-]+(?:\s+[\w@&'.-]+){0,3}?\s+(?:send|sent|write|written|wrote)\s+(?:to\s+)?(?:me|us)\b"
    r"(?:\s+(?:anything|something|an?\s+(?:message|note|letter|update|reply)|mail|e-?mails?|back))?\s*(?:\?|$|\b(?:this|today|yesterday|recently|lately|since))"
    r"|\bdid\s+(?:i|we)\s+(?:get|receive)\s+(?:anything|something|any(?:thing)?\s+(?:mail|e-?mails?|messages?)|mail|an?\s+(?:message|e-?mail|reply))\s+from\b"
    # A received message picked out by recency: "reply to the latest kucoin email saying thanks",
    # "answer kucoin's last email". Measured 2026-11-11 (battery v9, installed build): no read was
    # seated, so the model asked the owner for KuCoin's address instead of reading the mail.
    rf"|\b(?:the|[\w-]+['\u2019]s)\s+(?:latest|last|newest|most\s+recent)\s+(?:(?!{_NOT_MAIL_CONTENT}\b)[\w@&'.-]+\s+){{0,3}}?{_MAIL_ITEM}\b(?!\s+{_NOT_MAIL_CONTENT}\b)"
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
#: Sender-first framing, CASE-SENSITIVE: "Kucoin emails with deposit notification" — a
#: capitalised NAME before the mail noun. The big anchor compiles case-insensitively, so a
#: capitalised-name requirement inside it would match "an email" — this pattern lives alone
#: so the capital letter actually means a name. Measured 2026-11-06: the operator's morning
#: ask failed to seat because the recognizer only knew sender-AFTER-noun ("emails from
#: Kucoin"). The instruction-authority gates in mailbox_retrieval_intents still decide first.
_SENDER_FIRST_ANCHOR_RE = re.compile(
    rf"\b[A-Z][\w'-]+\s+{_MAIL_ITEM}\b(?!\s+{_NOT_MAIL_CONTENT}\b)"
    # A possessive sender with up to two words before the mail noun: "Show me Ana's last email",
    # "Reply to Ana's dinner email". Measured 2026-11-10 on 0.7.8: neither seated email.read, so the
    # reply was drafted without ever reading Ana's message (to an invented address). Possessive
    # only: "Write Ana a nice email" is a compose request and stays out.
    rf"|\b[A-Z][\w-]*['\u2019]s\s+(?:[a-z][\w-]*\s+){{1,2}}?{_MAIL_ITEM}\b(?!\s+{_NOT_MAIL_CONTENT}\b)"
)


_REQUEST_FRAME_RE = re.compile(r"\?|\b(?:please|can\s+you|could\s+you|would\s+you|will\s+you)\b", re.IGNORECASE)
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

#: What a request about received mail seats: the read tool. The family fill seats the rest.
MAILBOX_RETRIEVAL_INTENTS: tuple[str, ...] = ("email.read",)


_STATES_AN_ADDRESS_RE = re.compile(r"\s*(?:address\s+)?(?:is|was|=|:)\s+(?:now\s+)?[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)


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
            sender_first = _SENDER_FIRST_ANCHOR_RE.search(sentence)
            if sender_first is None:
                continue
            anchor = sender_first
        example = _EXAMPLE_FRAME_RE.search(sentence)
        if example is not None and example.start() < anchor.start():
            continue
        # "my email is ada@example.test": the person states their address; nothing asks to read mail.
        if _STATES_AN_ADDRESS_RE.match(sentence[anchor.end():]):
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


#: A short follow-up that is nothing but a format order: "pdf please", "make it a pdf", "word
#: version?", "as excel". Measured 2026-11-11 on the installed build: after a packing list,
#: "pdf please" was answered "Would you like me to generate it now?" while "give me that in pdf"
#: delivered the file -- the reply asked instead of doing what was asked.
_ELLIPTICAL_FORMAT_ORDER_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|now|and|also|then)\s*[,]?\s*)?(?:(?:please|pls|plz)\s+)?"
    r"(?:(?:make|turn|put|convert|save|export|send|give\s+me)\s+(?:it|that|this|them|those)\s+(?:in(?:to)?|as|to)?\s*)?"
    r"(?:(?:a|an|as|in)\s+)?(?:\.)?(pdf|docx?|word(?:\s+doc(?:ument)?)?|excel|xlsx|csv|markdown|md|pptx|powerpoint|zip)"
    r"(?:\s+(?:version|file|copy|format|doc(?:ument)?))?\s*(?:,?\s*(?:please|pls|plz|too|instead|thanks))?\s*[.!?]*\s*$",
    re.IGNORECASE,
)


#: An edit of the last answer that orders the file again: "add sunscreen and a torch and make the pdf
#: again", "pls add sunscreen + torch, new pdf", "update it with ... and give me the pdf again". The
#: format order rides on the edit, so neither the elliptical order above nor the "as a PDF" shape saw
#: it, and the model claimed a file nothing made (0.7.8 sweep block 3, PDF-EDIT-01 p1-p3).
_FILE_AGAIN_ORDER_RE = re.compile(
    r"\b(?:(?:make|give\s+me|send\s+me|send|export|redo|regenerate|re-?make|create|generate|do)\s+"
    r"(?:me\s+)?(?:the|a|an|another|that|this)\s+(?:new\s+|fresh\s+|updated\s+)?"
    r"(?:pdf|docx?|word\s+(?:doc|document|file)|xlsx|excel\s+file|csv|pptx)\b(?:\s+(?:file|version|copy))?\s+(?:again|too)\b"
    r"|(?:^|[,;+&]|\band\b)\s*(?:a\s+|an\s+)?(?:new|fresh|updated)\s+(?:pdf|docx?|xlsx|csv|pptx)\b(?:\s+(?:file|version|copy))?\s*(?:pls|please)?\s*[.!]?\s*$"
    r"|\b(?:make|give\s+me|send\s+me|export|create|generate)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:new|fresh|updated)\s+(?:pdf|docx?|xlsx|csv|pptx)\b)",
    re.IGNORECASE,
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


# -- operator seats (owner-local turns only) -------------------------------------------------
# The words that ask VOOL's own note, agenda, reminder, Apple Notes and calendar tools to act.
# The CHAT lane parses these from typed words with its own recognizers (core.operator.parser);
# the MODEL lane must agree on what a request IS, so this reads the parser's recognizers
# verbatim wherever they carry their own prose vetoes (the save-note recognizer, the Apple Notes
# patterns). Where the parser's width exists for typed-turn UX rather than for seating -- the
# agenda's declarative branch, verbs that double as past-tense reports, the bare possessive --
# the seating variant below is the narrower one and the parser keeps its own (TASK S4 law).
#
# Seated only on owner-local turns: the caller (the requirements arm and the offer seam) reads
# core.request_trust.request_is_owner_local first, so a remote turn's words never widen an offer.

#: Words whose VERB may lead a seating ask: a segment boundary, a polite frame, or a connective
#: that starts a new clause. A verb behind any other word ("she read the note", "I find notes
#: boring", "the show notes were long") is a report about the world, not an ask to this runtime.
_OWNER_ASK_LEAD = (
    r"(?:^|[.!?;\n]\s*|,\s*"
    r"|\b(?:please|pls|can\s+you|could\s+you|would\s+you|will\s+you|help\s+me(?:\s+to)?|go\s+ahead\s+and"
    r"|try\s+to)\s+)"
)
#: The tighter lead for verbs that double as unmarked past tenses ("read", "found"): no plain
#: connectives, so "she opened and read the note out loud" stays a report.
_OWNER_ASK_LEAD_STRICT = (
    r"(?:^|[.!?;\n]\s*"
    r"|\b(?:please|pls|can\s+you|could\s+you|would\s+you|will\s+you|help\s+me(?:\s+to)?|go\s+ahead\s+and"
    r"|try\s+to)\s+)"
)

# R2 (round 2): a seat needs ask-shaped words AND the owner's own object. Ask shape is a
# property of the SEGMENT: a question head at its start, or a seating verb at a clause lead
# (the same `_OWNER_ASK_LEAD` the seat regexes anchor on). A declarative segment — a statement
# about the agenda ("my agenda is packed this week"), a report ("the show notes were longer
# than the episode"), a past-tense narration — never reaches a seat regex at all. This replaces
# the old whole-text statement veto, which cancelled the ask whenever the SAME text also held
# a statement: "list my reminders that are due today" and "save this as a note: the calendar
# is full on Monday" are asks whose objects merely contain declarative clauses.
_OWNER_QUESTION_HEAD_RE = re.compile(
    r"^\s*(?:so|ok|okay|alright|and|but|or|also)?[\s,]*"
    r"(?:what|where|when|which|who|whom|whose|how|why|am|is|are|was|were|do|does|did|"
    r"can|could|will|would|shall|should|may|might|any|have)\b",
    re.IGNORECASE,
)
#: Job 2c (N4): every verb a seat regex may put behind an ask lead, as ONE shared list. A seat
#: verb missing from the clause-lead list is dead code — the segment never counts as an ask, so
#: the seat can never fire ("review my notes and my schedule ...", "map out my week",
#: "gather my notes", "round up my notes", "structure my day", "lay out my week", "place a
#: booking on my calendar"). The wording test walks every seat regex against this list, so a
#: seat verb that cannot make its segment an ask can never slip in again.
_OWNER_CLAUSE_LEAD_VERBS: tuple[str, ...] = (
    "show", "list", "find", "save", "open", "read", "check", "give", "pull", "put", "book",
    "schedule", "add", "create", "make", "set", "arrange", "block", "plan", "organize",
    "organise", "tidy", "clean", "sort", "summarize", "summarise", "digest", "recap",
    "display", "remind", "note", "write", "export", "copy", "move", "delete", "remove",
    "search", "look", "walk", "run", "jot", "go", "review", "structure", "map", "lay",
    "place", "gather", "collect", "round",
    # Job 6: the reminder-ask clause leads ("i need a reminder to ...", "nudge me ...",
    # "cancel that reminder") — a segment opening with them must count as an ask or the
    # reminder seats are dead code, the same N4 trap as "review".
    # Job 6b (Fix 2): "ping me ..." is the same person-addressed ask as "nudge me ...".
    "need", "want", "nudge", "ping", "cancel",
)
#: The seating verbs, at any clause lead, make the segment an imperative ask. Job 6: a
#: first-person "i/we need/want" is the same imperative lead ("i need a reminder to ...") —
#: the subject sits between the ask lead and the verb, so it gets its own arm.
#: Job 6b (Fix 2): "don't let me forget ..." is the same first-person imperative lead, and it
#: carries its own ask in full ("don't let me forget to call mum on sunday"), so it gets its
#: own arm rather than a bare "let"/"forget" lead verb (a report can hold those too).
_OWNER_IMPERATIVE_CLAUSE_RE = re.compile(
    _OWNER_ASK_LEAD + r"(?:(?:i|we)\s+(?:need|want)|don'?t\s+let\s+(?:me|us)\s+forget"
    r"|(?:" + "|".join(_OWNER_CLAUSE_LEAD_VERBS) + r"))\b",
    re.IGNORECASE,
)

#: The owner's own object: a possessive over a seating noun, the first-person forms that are
#: about the owner's own time by construction ("am I free", "what am I doing on Thursday"),
#: Apple Notes and "note to self" (there is nobody else's on this machine), and the
#: first-person relative ("the reminders I set in this chat"). "The calendar" or "a note"
#: alone is the world's calendar, the world's note — no seat.
#: Job 2b: the gate was too strict and dropped real asks, so these owner-by-construction
#: shapes join it: the elliptical first-person inventory ("which reminders do I have set",
#: "what appointments do I have this week"), a question that OPENS with "any meetings/
#: appointments/..." ("any appointments today?" — an existential "are there any meetings in
#: London?" does not open a segment with it), a note carried by its title ("the note called
#: Venue plan" — titled notes on this machine are the owner's), and "time free" (the reverse
#: of "free time").
#: Job 2c (N2): ONE list of event nouns, shared by the own-object gate and the agenda seat,
#: so a noun the gate admits can never be missing from the seat (the "any calls today?" miss,
#: where "calls" sat in the gate's "any" shape but not in the seat's).
_OWNER_EVENT_NOUNS_ALT = r"meetings?|appointments?|events?|calls?"
#: Job 2c (N1): "have I got" / "have we got" is the same inventory ask as "do I have"
#: ("what reminders have I got set for tomorrow", "have I got time free tomorrow morning").
_OWNER_HAVE_INVENTORY = r"(?:do\s+(?:i|we)\s+have|have\s+(?:i|we)\s+got)"
#: Job 2c (W3): a segment that OPENS with "any meetings/appointments/events/calls" is the
#: owner's own only when nothing else in the segment names whose it is. Time words, "for
#: me/us" and "on my calendar" keep it the owner's ("any appointments today?"); a place, an
#: organisation, "available at ..." or "of the ..." make it the world's ("any events in Paris
#: tomorrow?"). The tail after the noun may therefore hold ONLY these words, up to the end of
#: the segment (segments join with " . " in the ask text).
_OWNER_ANY_EVENT_TAIL = (
    r"(?:\s+(?:for\s+(?:me|us)|on\s+(?:my|our)\s+(?:calendar|schedule|plate)"
    r"|on\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|in\s+the\s+(?:morning|afternoon|evening)"
    r"|at\s+all|left|still|already|booked|scheduled|set|due|pending|or|and|then"
    r"|today|tonight|tomorrow|yesterday|this\s+(?:morning|afternoon|evening|week|weekend|month)"
    r"|next\s+(?:week|weekend|month)|(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|morning|afternoon|evening|night|weekend|at\s+\d(?:[.:]\d+)?\s*(?:am|pm|a\.m\.|p\.m\.|o'?clock)?))*"
    r"(?=\s*\.\s|\s*$)"
)
_OWNER_OWN_OBJECT_RE = re.compile(
    r"\b(?:my|our)\s+(?:notes?|note|agenda|calendars?|calendar|schedule|reminders?|"
    r"(?:" + _OWNER_EVENT_NOUNS_ALT + r")|free\s+time|day|week|plate|disk|drive|storage|"
    r"computer|mac|machine|laptop|apple\s+notes?)\b"
    r"|\bapple\s+notes?\b"
    r"|\bnote\s+to\s+self\b"
    r"|\bnotes?\s+(?:titled|called|named)\b"
    r"|\breminders?\s+(?:that\s+)?(?:i|we)\s+(?:set|added|created|made)\b"
    r"|\bwhat\s+am\s+i\s+doing\b"
    r"|\bwhat\s+have\s+(?:i|we)\s+got\s+on\b"
    r"|\bwhat\s+do\s+(?:i|we)\s+have\s+on\b"
    r"|\b(?:what|which)\b[^.!?]{0,40}?\b" + _OWNER_HAVE_INVENTORY + r"\b(?!\s+to\b)"
    r"|(?:^|(?<=\. ))any\s+(?:" + _OWNER_EVENT_NOUNS_ALT + r")\b" + _OWNER_ANY_EVENT_TAIL +
    r"|\b(?:my|our)\s+next\s+(?:" + _OWNER_EVENT_NOUNS_ALT + r"|agenda|day|week)\b"
    r"|\b(?:am\s+i|are\s+we)\s+(?:free|available|busy|open)\b"
    r"|\bfree\s+(?:time|slots?|windows?)\b"
    r"|\btime\s+free\b"
    r"|\b" + _OWNER_HAVE_INVENTORY + r"\s+(?:anything|something|any\s+(?:" + _OWNER_EVENT_NOUNS_ALT
    + r"|conflicts?))\b"
    r"|\bis\s+(?:today|tonight|tomorrow|this\s+week|next\s+week|(?:mon|tues|wednes|thurs|fri|satur|sun)day)"
    r"(?:\s+(?:morning|afternoon|evening))?\s+(?:free|available|busy|open)\b",
    re.IGNORECASE,
)

#: Job 2c (N2): the time words the bare "any ..." agenda shape accepts — the same notion of a
#: time word as the own-object "any" tail, part-of-day words included ("any meetings this
#: afternoon?").
_OWNER_ANY_TIME_ALT = (
    r"today|tonight|tomorrow|this\s+weekend|this\s+week|next\s+weekend|next\s+week"
    r"|this\s+morning|this\s+afternoon|this\s+evening|in\s+the\s+(?:morning|afternoon|evening)"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day|morning|afternoon|evening"
)
_OWNER_AGENDA_SEAT_RE = re.compile(
    # R2: the agenda ask names the owner's own calendar ("my calendar"); "what's on the calendar
    # for the Olympics" is the world's event calendar, not the owner's. The show branch reads the
    # schedule/calendar nouns too, so a schedule-over-period ask seats the agenda (its view),
    # not availability.
    r"\bwhat(?:'s| is|s)\s+on\s+(?:my|our)\s+(?:calendar|schedule|plate)\b"
    r"|\bwhat\s+(?:have\s+)?(?:do\s+|did\s+|does\s+)?(?:i|you|we)\s+(?:have\s+|got\s+)on\b"
    r"[^.!?]{0,40}?\b(?:today|tonight|tomorrow|this week|next week|upcoming|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b"
    r"|\bwhat\s+am\s+i\s+doing\b[^.!?]{0,30}?\b(?:on\s+)?(?:today|tonight|tomorrow|this\s+week|next\s+week"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b"
    r"|\bwhat(?:'s| is| does)\s+(?:my|the|this|next)\s+week\b\s+look\s+like\b"
    r"|\bany\s+(?:" + _OWNER_EVENT_NOUNS_ALT + r")\b[^.!?]{0,30}?\b(?:on\s+)?(?:" + _OWNER_ANY_TIME_ALT + r")\b"
    r"|\b(?:what|which)\s+(?:" + _OWNER_EVENT_NOUNS_ALT + r")\s+" + _OWNER_HAVE_INVENTORY + r"\b"
    r"|\b(?:what|when)(?:'s| is)\s+(?:my|our)\s+next\s+(?:" + _OWNER_EVENT_NOUNS_ALT + r")\b"
    r"|" + _OWNER_ASK_LEAD + r"(?:show|read|open|check|give\s+me|walk\s+(?:me\s+)?through|run\s+through)\b"
    r"[^.!?]{0,60}?\b(?:my|the|this|today'?s|tomorrow'?s|this\s+week'?s|next\s+week'?s)\s+(?:day|agenda|schedule|calendar)\b"
    r"(?!\s+(?:app|account|settings?|sync|widget))"
    # Job 2b: a combined report over the owner's own time ("look over my notes and my
    # calendar and tell me what's coming up") seats the agenda beside the notes; without it
    # the turn got no tool list at all. The calendar noun stays possessive (the world's
    # calendar never seats), and an app/settings tail stays out.
    r"|" + _OWNER_ASK_LEAD + r"(?:look\s+(?:over|through)|review|go\s+through|check)\b"
    r"[^.!?]{0,60}?\b(?:my|our)\s+(?:calendar|schedule|agenda)\b(?!\s+(?:app|account|settings?|sync|widget))"
    # "plan my day" plans the owner's day; "plan my day trip to Rome" plans a trip — the day
    # noun is attributive there, and the trip (not the agenda) is the object.
    r"|" + _OWNER_ASK_LEAD + r"(?:plan|organise|organize|structure|map\s+out|sort\s+out|lay\s+out)\b"
    r"[^.!?]{0,20}?\b(?:my|the|our|today'?s)\s+day\b(?!\s+(?:trip|trips|tour|tours|outing|excursion|visit|out|off|break))"
    r"|\b(?:my|the|this)\s+(?:day|agenda)\b(?!\s+(?:trip|trips|tour|tours|outing|excursion|visit))"
    r"\s+(?:for|from|around|today|tomorrow|this\s+week|next\s+week)\b"
    r"|\bagenda\s+(?:for|from)\s+(?:today|tonight|tomorrow|this\s+week|next\s+week|the\s+week"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b"
    r"|\b(?:on|for)\s+(?:my|the)\s+calendar\b[^.!?]{0,40}?\b(?:this week|next week|today|tomorrow|tonight)\b",
    re.IGNORECASE,
)
_OWNER_LIST_REMINDERS_SEAT_RE = re.compile(
    r"\b(?:what|which|where)\b[^.!?]{0,40}?\breminders?\b"
    r"|" + _OWNER_ASK_LEAD + r"(?:show|list|read|check|give\s+me|pull\s+up)\b[^.!?]{0,40}?\breminders?\b"
    r"|\bmy\s+reminders?\b(?!\s+(?:app|account|settings?|notifications?|sound|alarm|history))",
    re.IGNORECASE,
)
#: Job 6: a first-person wh-word directly after "remind me" asks for recall or knowledge
#: ("remind me what I said about the May trip", "remind me why the sky is blue"), never for a
#: reminder to store. The chat parser's own veto covers the what-I-said family only, so the
#: seat adds the general wh guard; unsure is quiet.
_OWNER_RECALL_REMIND_RE = re.compile(
    r"\bremind\s+me\s+(?:what|when|where|who|how|why|which)\b", re.IGNORECASE
)
#: Job 6b (Fix 2): a reminder NOUN followed by another thing's word — an app, settings, a
#: sound, an email, a list, the history — is talk about reminders ("do you want reminders
#: turned on in settings?", "i need a reminder app recommendation"), never an ask to store
#: one; "a reminder of <a memory>" is a recollection, not a reminder to set. The veto reads a
#: short window past the noun so "reminders turned on in settings" is caught too.
_OWNER_REMINDER_TALK_TAIL = (
    r"(?![^.!?]{0,30}\b(?:of|app|apps|account|accounts|settings?|notifications?|sounds?|alarms?|"
    r"tones?|history|emails?|lists?|icon|badge|widget|recommendations?|reviews?|features?|tips)\b)"
)
#: Job 6b (Fix 2): the person-addressed reminder asks. Each arm addresses this runtime in the
#: first person, so it carries no possessive and is an ask by construction. "nudge/ping me"
#: and the Job 6b shapes ("don't let me forget to ...", "make sure I remember to ...") join
#: "remind me" here; the recall veto at the seat site still guards "remind me what I said".
_OWNER_SCHEDULE_REMINDER_SEAT_RE = re.compile(
    r"\bremind\s+me\b"
    r"|\b(?:nudge|ping)\s+(?:me|us)\b"
    r"|\bdon'?t\s+let\s+(?:me|us)\s+forget\b"
    r"|\bmake\s+sure\s+(?:i|we)\s+(?:don'?t\s+)?(?:remember|forget)\b",
    re.IGNORECASE,
)
#: Job 6b (Fix 2): the noun asks ("set me a reminder for 6pm, gym"), behind the talk-tail
#: veto. A reminder set on someone else's device or for someone else is not the owner's:
#: "\w+'s phone/device/..." (a person's device, never "my phone") and "for her/his/their"
#: after the noun step down. The first two arms mirror the parser's _REMINDER_HINT_RE so both
#: lanes read the same words; "need"/"want" are the shapes the typed parser misses and the
#: model tool exists for.
_OWNER_SET_A_REMINDER_SEAT_RE = re.compile(
    r"\b(?:set|create|schedule|add)\s+(?:me\s+)?(?:(?:a|an|the|my)\s+)?reminders?\b" + _OWNER_REMINDER_TALK_TAIL
    + r"|\b(?:need|want)\s+(?:(?:a|an|the|my)\s+)?reminders?\b" + _OWNER_REMINDER_TALK_TAIL,
    re.IGNORECASE,
)
#: Job 6b (Fix 2): a reminder on someone else's device or for someone else ("set a reminder
#: on my wife's phone for her yoga"). "my phone" is the owner's own and never matches; only a
#: person's possessive ('s) or a third-person possessive pronoun before the device noun does.
_OWNER_REMINDER_ON_ANOTHERS_DEVICE_RE = re.compile(
    r"\b\w+'s\s+(?:phone|mobile|device|laptop|computer|tablet|ipad|watch)\b"
    r"|\b(?:his|her|their)\s+(?:phone|mobile|device|laptop|computer|tablet|ipad|watch)\b",
    re.IGNORECASE,
)
_OWNER_REMINDER_FOR_ANOTHER_RE = re.compile(
    r"\breminders?\b[^.!?]{0,40}?\bfor\s+(?:her|his|their)\b",
    re.IGNORECASE,
)
#: Job 6: a cancel ask, narrower than the parser's own _CANCEL_REMINDER_RE (no "call off"/"scrub"):
#: only the words people say to this runtime about its own reminders.
#: Job 6b (Fix 3): the cancel verb may follow the reminder noun in the same clause ("never
#: mind the gym reminder, cancel it"), and settings talk about a reminder ("remove the
#: reminder from my phone's lock screen settings") is not a cancel: the reminder noun followed
#: by a place word that leads to a settings/screen/notification word steps down, and the same
#: talk-tail veto as the schedule seat applies to both arms.
_OWNER_REMINDER_SETTINGS_TALK_RE = re.compile(
    r"\breminders?\s+(?:from|on|in|inside|at)\b[^.!?]{0,50}?\b(?:settings?|app|lock\s*screen|"
    r"home\s*screen|notifications?|widget|icon|badge)\b",
    re.IGNORECASE,
)
_OWNER_CANCEL_REMINDER_SEAT_RE = re.compile(
    r"\b(?:cancel|delete|remove)\b[^\n!?.]*\breminders?\b" + _OWNER_REMINDER_TALK_TAIL
    + r"|\breminders?\b[^\n!?.]{0,60}?\b(?:cancel|delete|remove)\b" + _OWNER_REMINDER_TALK_TAIL,
    re.IGNORECASE,
)
_OWNER_AVAILABILITY_SEAT_RE = re.compile(
    # R2: "am I free to quit my job" asks about permission, not the calendar — the free-to
    # lookahead keeps it out. "any free will" is philosophy: a bare "free" needs the owner's
    # time behind it (free time/slot/window), not a noun it modifies.
    # Job 2b: "do I have time free on Friday" is the ask's own shape (the reverse word order of
    # "free time"); "do I have anything to worry about" carries an infinitive, not a schedule;
    # "is Sunday free parking" asks what a thing costs or permits, so the day-free idiom may
    # only end there or continue on a time word, never on the noun "free" modifies.
    r"\bam\s+i\s+(?:free|available|busy|open)\b(?!\s+to\b)"
    r"|\bare\s+we\s+(?:free|available|busy|open)\b(?!\s+to\b)"
    r"|\bis\s+(?:today|tonight|tomorrow|this\s+week|next\s+week|(?:mon|tues|wednes|thurs|fri|satur|sun)day)"
    r"(?:\s+(?:morning|afternoon|evening))?\s+free\b"
    r"(?=\s*(?:[?!,.]|$|\b(?:on|at|for|or|and|in|still|now|then|today|tonight|tomorrow|this|next"
    r"|morning|afternoon|evening)\b))"
    r"|\b" + _OWNER_HAVE_INVENTORY + r"\s+(?:anything|something|any\s+(?:" + _OWNER_EVENT_NOUNS_ALT
    + r"|conflicts?)"
    r"|(?:a|any)\s+free\s+(?:time|slots?|windows?))\b(?!\s+to\b)"
    r"|\b" + _OWNER_HAVE_INVENTORY + r"\s+(?:any\s+)?time\s+free\b"
    r"|\b(?:availability|available|free|busy|open)\b[^.!?]{0,60}?\b(?:slot|calendar|schedule|window)\b"
    r"|\b(?:slot|calendar|schedule)\b[^.!?]{0,60}?\b(?:availability|available|free|busy)\b"
    r"|" + _OWNER_ASK_LEAD + r"(?:check|find)\b[^.!?]{0,60}?\b(?:my|the)\s+(?:availability|free\s+time)\b",
    re.IGNORECASE,
)
#: R2: "make an event poster", "put a meeting summary in a table" — when a document or thing
#: noun follows the event noun, the event is attributive and the document is the object: not a
#: calendar write. The event noun must stay the head of its object phrase.
#: Job 2b: "a call to action" and "a meeting tone/topic" are the same shape — the thing named
#: after the event noun is the real object of the verb.
_EVENT_ATTRIBUTIVE_VETO = (
    r"(?!\s+(?:agenda|notes?|minutes|invite|room|link|points?|template|poster|summary|card|"
    r"graphic|banner|image|images|photo|photos|picture|pictures|illustration|flyer|draft|"
    r"report|memo|email|writeups?|write-ups?|recap|digest|outline|table|tables|chart|charts|"
    r"list|lists|program|playlist|badge|ticket|tickets|to\s+action|tone|topic|vibe))"
)
#: Job 2b: a question about whether the ask is possible, allowed or free of charge ("is it free
#: to schedule a meeting on Zoom?", "book a call with the Prime Minister? is that possible?")
#: wants an answer about the world first, never a seated booking tool.
_FEASIBILITY_ASK_RE = re.compile(
    r"\bis\s+(?:that|it|this)\s+(?:even\s+|actually\s+|really\s+)?(?:free|possible|feasible|allowed|ok|okay)\b"
    r"|\bcan\s+that\s+be\s+done\b"
    r"|\bwould\s+that\s+(?:work|be\s+(?:ok|okay|possible))\b",
    re.IGNORECASE,
)
_OWNER_PROPOSE_EVENT_SEAT_RE = re.compile(
    r"\b(?:schedule\s+a\s+meeting|schedule\s+meeting|schedule\s+an?\s+event|create\s+a?\s*calendar\s+event|create\s+a\s+meeting)\b"
    + _EVENT_ATTRIBUTIVE_VETO
    + r"|" + _OWNER_ASK_LEAD + r"(?:set\s+up|put|book|schedule|add|create|make|set|arrange|block|place)\b"
    r"[^.!?]{0,40}?\ban?\s+(?:appointment|meeting|event|call|sync)\b"
    + _EVENT_ATTRIBUTIVE_VETO
    + r"|" + _OWNER_ASK_LEAD + r"(?:put|book|schedule|add|create|make|set(?:\s+up)?|arrange|block|place)\b"
    r"[^.!?]{0,60}?\b(?:appointment|meeting|event|call|sync|lunch|dinner|visit|checkup|session|block)\b"
    + _EVENT_ATTRIBUTIVE_VETO
    + r"[^.!?]{0,60}?\b(?:on|onto|to|in(?:to)?|for)\s+(?:my|the|this)\s+calendar\b"
    + r"|" + _OWNER_ASK_LEAD + r"(?:put|book|schedule|add|create|make|set(?:\s+up)?|arrange|block|place)\b"
    r"[^.!?]{0,60}?\b(?:to|on|onto|in(?:to)?)\s+(?:my|the|this)\s+calendar\b"
    + r"|" + _OWNER_ASK_LEAD + r"(?:put|book|schedule|add|create|make|set(?:\s+up)?|arrange|block|place)\b"
    r"[^.!?]{0,60}?\b(?:appointment|meeting|event|call|sync)\b"
    + _EVENT_ATTRIBUTIVE_VETO
    + r"[^.!?]{0,60}?\b(?:today|tonight|tomorrow|next\s+week|this\s+week|next\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|on\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|for\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|(?:mon|tues|wednes|thurs|fri|satur|sun)day\s+at|at\s+\d|\d\s*(?:am|pm|a\.m\.|p\.m\.)|o'?clock)\b",
    re.IGNORECASE,
)
#: Job 2b: the ask whose material is already in the message ("summarize my notes on the French
#: revolution I pasted below: ...") reads words the model can already see — it is not a store
#: lookup, so the note-reading seats stand down for it. A save is untouched: storing the very
#: words someone pasted is the point of the ask.
#: Job 2c (W2): the veto belongs ONLY when the pasted words ARE the notes ("my notes on X I
#: pasted below:", "these notes below:", "I pasted my notes below"). Something else pasted
#: beside a real store lookup ("find my notes about the boiler, the error is below: E119")
#: keeps the lookup: the veto needs the notes noun attached to the pasted cue with no clause
#: boundary between them.
_NOTES_IN_MESSAGE_RE = re.compile(
    r"\bnotes?\b[^,.!?]{0,60}?\b(?:pasted|posted|copied|included|typed)\b[^,.!?]{0,20}?\b(?:below|above)\b"
    r"|\bnotes?\b[^,.!?]{0,25}?\b(?:below|above)\s*:"
    r"|\b(?:pasted|posted|copied|included|typed)\b[^,.!?]{0,30}?\bnotes?\b[^,.!?]{0,20}?\b(?:below|above)\b",
    re.IGNORECASE,
)
#: Job 2c (W1): the booking verbs, so a segment that OPENS with one ("book a call with the
#: Prime Minister?") reads as a booking clause of its own — as distinct from a polite request
#: ("can you book ...?"), whose verb sits behind a frame.
_BARE_BOOKING_LEAD_RE = re.compile(
    r"^(?:set\s+up|put|book|schedule|add|create|make|set|arrange|block|place)\b",
    re.IGNORECASE,
)


def _propose_event_asked_about(ask_text: str, segments: list[tuple[str, bool]]) -> bool:
    """Job 2c (W1): does the text ask ABOUT the booking instead of making it?

    The feasibility/price check ("is that possible?", "would that work?", "is it free to
    ...?") vetoes the booking seat only where the booking itself is asked about: (a) a check
    frame that wraps the verb — the check starts before the booking inside the same segment
    ("is it free to schedule a meeting on Zoom?"); or (b) a booking clause that is itself a
    bare question — the verb opens the segment and the segment ended on a question mark in
    the caller's own words — with a check after it ("book a call with the Prime Minister? is
    that possible?"). An order with a check attached ("book a dentist appointment tuesday at
    9, would that work?") and a polite request ("can you book ...?") are orders and keep the
    seat. `segments` carries the kept ask segments as (text, ended-on-a-question-mark) pairs,
    in ask_text order.
    """
    matches = list(_OWNER_PROPOSE_EVENT_SEAT_RE.finditer(ask_text))
    if not matches:
        return False
    checks = [m.start() for m in _FEASIBILITY_ASK_RE.finditer(ask_text)]
    if not checks:
        return False
    spans: list[tuple[int, int]] = []
    pos = 0
    for text, _ends_question in segments:
        spans.append((pos, pos + len(text)))
        pos += len(text) + 3  # the " . " join between kept segments

    def segment_of(offset: int) -> int:
        for index, (start, end) in enumerate(spans):
            if start <= offset < end:
                return index
        return len(spans) - 1

    order_found = False
    for match in matches:
        segment = segment_of(match.start())
        framed = any(
            check < match.start() and segment_of(check) == segment for check in checks
        )
        bare_question = (
            segments[segment][1]
            and _BARE_BOOKING_LEAD_RE.match(segments[segment][0])
            and any(segment_of(check) > segment for check in checks)
        )
        if not framed and not bare_question:
            order_found = True
    return not order_found


_OWNER_FIND_NOTES_SEAT_RE = re.compile(
    # The object must be the owner's OWN notes ("my notes"): a bare "notes" noun seats nothing,
    # because compound nouns about other things ("look up the latest SQLite release notes online")
    # are web/grounded asks, and a false seat there flipped the turn's whole toolset.
    _OWNER_ASK_LEAD_STRICT + r"(?:find|search(?:\s+for)?|look\s+(?:up|for)|list|show)\b[^.!?]{0,60}?\b(?:my|our)\s+notes?\b"
    r"|\bwhere\s+(?:are|is)\s+(?:my|our)\s+notes?\b"
    r"|" + _OWNER_ASK_LEAD + r"(?:tidy|clean|organize|organise|sort|go\s+through|gather|collect"
    r"|look\s+(?:over|through)|review)\b"
    r"[^.!?]{0,40}?\bmy\s+notes\b"
    r"|" + _OWNER_ASK_LEAD_STRICT + r"(?:summarize|summarise|digest|round\s+up|recap|go\s+over)\b"
    r"[^.!?]{0,40}?\bmy\s+notes\b",
    re.IGNORECASE,
)
_OWNER_SHOW_NOTE_SEAT_RE = re.compile(
    _OWNER_ASK_LEAD_STRICT + r"(?:show|read|open|display)\s+(?:the|my|that|this)\s+note\b"
    r"|\bwhat(?:'s| is|s)\s+in\s+(?:the|my|that)\s+note\b"
    r"|" + _OWNER_ASK_LEAD_STRICT + r"(?:show|read|open|display)\b[^.!?]{0,40}?\bnote\s+(?:titled|called|named)\b"
    # Job 2b: the titled note asked about by name ("what does the note called Venue plan say?").
    r"|\bwhat\s+(?:does|do|did)\s+(?:the|my|our|that|this)\s+notes?\s+(?:titled|called|named)\b",
    re.IGNORECASE,
)
_OWNER_APPLE_LIST_SEAT_RE = re.compile(
    _OWNER_ASK_LEAD + r"(?:list|show)\b[^.!?]{0,40}?\bapple\s+notes\b"
    # Job 2b: "what's in my Apple Notes?" asks for the list, in question form.
    r"|\bwhat(?:'s| is|s)\s+in\b[^.!?]{0,20}?\bapple\s+notes?\b",
    re.IGNORECASE,
)
_OWNER_APPLE_READ_SEAT_RE = re.compile(
    # Job 2b: "read my Apple Notes" is plural; the singular noun alone used to be the only shape.
    _OWNER_ASK_LEAD_STRICT + r"(?:read|show|open|display)\b[^.!?]{0,60}?\bapple\s+notes?\b",
    re.IGNORECASE,
)


_OWNER_DISK_ANALYSIS_SEAT_RE = re.compile(
    # R2: the disk ask names the disk (or a machine) somewhere — "where did the space go in my
    # sentence" is a question about words, not storage.
    r"\bwhere\s+did\s+(?:(?:all|the)\s+)?(?:my\s+)?(?:disk|drive|ssd|hd|startup[\s-]?disk)\s+space\s+go\b"
    r"|\bwhere\s+did\s+(?:(?:all\s+)?(?:my|the)\s+)?(?:space|storage)\s+go\b"
    r"[^.!?]{0,60}?\b(?:disk|drive|storage|ssd|mac|laptop|computer|machine)\b"
    r"|\bwhat(?:'s| is|s)\s+(?:taking|using|eating|occupying|filling)\s+up\b"
    r"[^.!?]{0,60}?\b(?:my\s+)?(?:disk|drive|storage|ssd|mac|laptop|computer|machine)\b"
    r"|\bwhat\s+(?:ate|consumed|used\s+up)\s+(?:my|the)\s+(?:disk|drive|storage|space)\b"
    r"|\bwhy\s+is\s+(?:my|the)\s+(?:disk|drive|storage)\s+(?:so\s+)?full\b",
    re.IGNORECASE,
)


#: Job 2b: the bare jot cue ("jot this down: buy milk", "jot down the wifi password"). Only at
#: a clause lead, so a report ("she jotted it down") never reaches it.
_OWNER_JOT_SAVE_RE = re.compile(
    _OWNER_ASK_LEAD + r"jot\s+(?:down\b|(?:(?:this|that|it|them)\s+)?down\b)",
    re.IGNORECASE,
)


def owner_local_operator_intents(text: str) -> tuple[str, ...]:
    """The operator tool intents the user's own words ask for, for seating on owner-local turns.

    Pure function of the text, like every signal here: no policy reads, no availability checks
    (seating filters those), no execution. The chat lane's own parser stays the authority for
    typed words; this recognizer agrees with it where the parser is precise and stays narrower
    where the parser's width is a typed-turn UX choice (see the block comment above).
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ()
    try:
        from core.instructional_request import asks_for_instructions_not_execution
        from core.retrieval_constraints import analyze_retrieval_constraints
        from core.turn_prohibitions import _strip_quoted_spans
    except Exception:
        return ()
    constraints = analyze_retrieval_constraints(_strip_quoted_spans(cleaned))
    if constraints.forbids_all_tools:
        return ()
    eligible = " ".join(constraints.eligible_text.split())
    if not eligible or asks_for_instructions_not_execution(eligible):
        return ()

    # R2: only ask-shaped segments seat (see the block comment above). Job 2c (W1): each kept
    # segment also remembers whether it ended on a question mark in the caller's own words —
    # a booking clause that is itself a bare question ("book a call ... ?") is asked about,
    # while a polite request ("can you book ... ?") is an order — so the propose seat's
    # feasibility veto can tell them apart. Joining with a full stop keeps every seat regex's
    # `[^.!?]` window inside one segment.
    kept_segments: list[tuple[str, bool]] = []
    pieces = re.split(r"([.!?;\n]+)", eligible)
    for index in range(0, len(pieces), 2):
        part = pieces[index]
        if not part.strip():
            continue
        if not (_OWNER_QUESTION_HEAD_RE.match(part) or _OWNER_IMPERATIVE_CLAUSE_RE.search(part)):
            continue
        delimiter = pieces[index + 1] if index + 1 < len(pieces) else ""
        kept_segments.append((part.strip(), "?" in delimiter))
    if not kept_segments:
        return ()
    ask_text = " . ".join(text for text, _ in kept_segments)
    own_object = _OWNER_OWN_OBJECT_RE.search(ask_text) is not None
    # The material-in-message veto (Job 2b) belongs to the reading seats only: a save of pasted
    # words is the ask, a lookup of them is not.
    notes_in_message = _NOTES_IN_MESSAGE_RE.search(ask_text) is not None

    def _seats(pattern: re.Pattern[str]) -> bool:
        return own_object and bool(pattern.search(ask_text))

    def _reads_notes(pattern: re.Pattern[str]) -> bool:
        return not notes_in_message and _seats(pattern)

    intents: list[str] = []
    if _seats(_OWNER_AGENDA_SEAT_RE):
        intents.append("operator.show_agenda")
    if not intents and _seats(_OWNER_AVAILABILITY_SEAT_RE):
        intents.append("operator.check_availability")
    if _seats(_OWNER_LIST_REMINDERS_SEAT_RE):
        intents.append("operator.list_reminders")
    # Job 6: the reminder acts. The recall veto is the chat parser's own, read over the WHOLE
    # request — the eligible text above has already had the recall remainder stripped
    # ("remind me what I said about the May trip" arrives here as "remind me what"), so the
    # veto on ask_text alone could never see the recall shape. If the veto itself cannot be
    # read, nothing seats — a false seat is worse than a miss.
    is_recall_ask = True
    try:
        from core.operator.parser import _asks_to_recall_not_to_be_reminded

        is_recall_ask = _asks_to_recall_not_to_be_reminded(cleaned)
    except Exception:
        pass
    if (
        not is_recall_ask
        and not _OWNER_RECALL_REMIND_RE.search(ask_text)
        and (
            _OWNER_SCHEDULE_REMINDER_SEAT_RE.search(ask_text)
            or (
                _OWNER_SET_A_REMINDER_SEAT_RE.search(ask_text)
                # A reminder on someone else's device or for someone else is not the owner's
                # (Job 6b, Fix 2); the person-addressed arms above are exempt ("remind me to
                # water her plants while she's away" is the owner's own reminder).
                and not _OWNER_REMINDER_ON_ANOTHERS_DEVICE_RE.search(ask_text)
                and not _OWNER_REMINDER_FOR_ANOTHER_RE.search(ask_text)
            )
        )
    ):
        intents.append("operator.schedule_reminder")
    if _OWNER_CANCEL_REMINDER_SEAT_RE.search(ask_text) and not _OWNER_REMINDER_SETTINGS_TALK_RE.search(ask_text):
        intents.append("operator.cancel_reminder")
    # The scheduling idioms are asks to act by construction ("schedule a meeting", "book a
    # dentist appointment Tuesday"): they carry no possessive, so the own-object rule is
    # carried by their own anchored verbs and the attributive veto instead. A feasibility or
    # price question around them is not the act itself — but only when it is the BOOKING the
    # check asks about (a frame before the verb, or a bare-question clause with a check after
    # it; see _propose_event_asked_about). An order with a check attached stays an order.
    if _OWNER_PROPOSE_EVENT_SEAT_RE.search(ask_text) and not _propose_event_asked_about(
        ask_text, kept_segments
    ):
        intents.append("operator.propose_calendar_event")
    if _reads_notes(_OWNER_FIND_NOTES_SEAT_RE):
        intents.append("operator.find_notes")
    if _reads_notes(_OWNER_SHOW_NOTE_SEAT_RE):
        intents.append("operator.show_note")
    if _reads_notes(_OWNER_APPLE_LIST_SEAT_RE):
        intents.append("operator.apple_note_list")
    if _reads_notes(_OWNER_APPLE_READ_SEAT_RE):
        intents.append("operator.apple_note_read")
    if own_object and _OWNER_DISK_ANALYSIS_SEAT_RE.search(ask_text):
        # A disk-analysis ask stands down from the direct capacity read (see
        # core.execution.constants._DISK_ANALYSIS_ASK_RE) and needs the reasoning lane with the
        # operator's own inspect tool seated. Read-only, and the words are disk-anchored.
        intents.append("operator.inspect_disk_usage")
    try:
        # The parser's own save-note recognizer, verbatim: it already separates keeping a note
        # from composing one for someone (a thank-you note), and "take note of" from a note.
        # A saving ask needs no possessive ("save this as a note: ...") — the recognizer IS
        # the precision, and the ask-shape gate above has already kept only imperative or
        # interrogative segments, so the note's own content can no longer veto the ask.
        from core.operator.parser import _SAVE_NOTE_RE

        if _SAVE_NOTE_RE.search(ask_text) is not None:
            intents.append("operator.save_note")
        elif _OWNER_JOT_SAVE_RE.search(ask_text) is not None:
            # Job 2b: the bare memorandum cue ("jot this down: buy milk") carries no note noun
            # for the parser's recognizer to hold onto; at a clause lead, with content to store,
            # the verb itself is the storing cue.
            intents.append("operator.save_note")
        elif re.match(r"\s*note\s+to\s+self\s*:(?!\d)", ask_text, re.IGNORECASE):
            # "note to self: buy milk" -- the memorandum header itself is the storing cue; the
            # parser has no record verb for it, and the colon keeps prose ("my note to self is
            # to slow down") out.
            intents.append("operator.save_note")
    except Exception:
        pass
    return tuple(intents[:MAX_EXPLICIT_INTENTS])


# -- the owner's own files (read by core.execution_requirements, owner-local turns only) --------
# Measured 2026-10-27 with the tool-seating offer probe: "where is my resume", "send day4.jpg to my
# phone", "copy day4.jpg to my desktop" and 13 more asks no deterministic lane owns reached a pinned
# model with NO tool list (classed as plain chat), so the model could only talk about the files.
# The words must both ASK and name the owner's own object: a bare noun ("a pdf", "files") is prose.
_FILE_DEMAND_ASK = re.compile(
    r"\b(?:find|where|locate|look(?:ing)?\s+for|search|show|list|open|read|see|view|copy|move|send|fetch|"
    r"get|give|grab|do\s+i\s+(?:still\s+)?have|is\s+there|are\s+there|how\s+many|how\s+big|which|what(?:'s|\s+is|\s+are)?|"
    r"access|check|clean(?:\s+up)?|tidy|organi[sz]e|sort|delete|remove|rename|summari[sz]e|need|help\s+me|free\s+up|"
    r"is\s+(?:my|the|that)|are\s+(?:my|the)|how\s+much|can'?t\s+find|cannot\s+find|lost)\b",
    re.I,
)
_FILE_DEMAND_OBJECT = re.compile(
    r"\b(?:my|our)\s+(?:[\w'\-]+\s+){0,3}?(?:files?|folders?|pdfs?|photos?|pictures?|pics|images?|screenshots?|"
    r"documents?|docs|resume|cv|invoices?|receipts?|downloads|desktop|videos?|movies|music|songs?|recordings?|"
    r"spreadsheets?|presentations?|stuff|boarding\s+pass(?:es)?|tickets?|passports?|statements?|contracts?|scans?|"
    r"csvs?|certificates?)\b"
    r"|\b(?:the|that)\s+(?:[\w\-]+\s+){0,2}?(?:invoice|receipt|resume|cv|contract|pdf|photo|picture|screenshot|file|"
    r"document|spreadsheet|presentation|video|recording)s?\s+(?:i\b|from\b|you\b|on\s+my\b|in\s+my\b|that\s+i\b)"
    r"|\b(?:find|where(?:'s|\s+is|\s+are))\s+(?:the|that)\s+(?:[\w\-]+\s+){0,2}?(?:invoice|receipt|resume|cv|contract)s?\b"
    r"|\b(?:apps?|applications?|programs?)\b[^.?!]{0,20}?\binstalled\b|\binstalled\s+(?:apps?|applications?|programs?)\b"
    r"|\b(?:photos?|pictures?|files?|videos?|documents?|pdfs?|songs?|screenshots?)\s+(?:do\s+i|i)\s+(?:still\s+)?have\b"
    r"|\bdo\s+i\s+(?:still\s+)?have\s+(?:the|that|a|any)\s+(?:[\w\-]+\s+){0,2}?(?:photos?|pictures?|files?|videos?|"
    r"documents?|pdfs?|songs?|screenshots?|recordings?|invoices?|receipts?)\b"
    r"|\bfree\s+up\s+(?:some\s+)?(?:disk\s+|storage\s+)?space\b"
    # Audit 2026-10-27: "i saved a pic of the cat on the sofa, find it?" and "i downloaded my bank thing as a
    # csv, how much did i spend" are the owner's own files ("give me a picture of a cat" is not: no saved/took).
    r"|\b(?:saved|took|taken|kept|have|had)\s+(?:a|the|that|some)\s+(?:pic|picture|photo|image|screenshot|video)s?\s+of\b"
    r"|\b(?:downloaded|saved|exported)\b[^.?!]{0,40}?\b(?:csv|pdf|spreadsheet|xlsx|excel\s+file)\b"
    r"|\b(?:in|on|from|to|into)\s+(?:the\s+|my\s+)?(?:downloads|desktop|documents|pictures)\b"
    r"|(?<![\w/.\-])[\w\-]+\.(?:txt|md|pdf|docx?|xlsx?|pptx?|csv|jpe?g|png|heic|gif|mov|mp4|mp3|zip|dmg|pages|numbers|key)\b",
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


#: What a plain database ask SEATS. The skill only teaches; the work needs the tool in the
#: offer, or the model can only promise. Read-only tools only (their contracts declare
#: side_effect_class read_only, approval none): `migrate.preview` mutates nothing, so a
#: schema-change ask can be previewed and explained, while apply/restore/backup/db.create
#: stay unseated -- a mutation still needs the user to name it (or the model to ask for
#: the family) and then crosses approval, effect budget and the permission controller.
_DATABASE_SEATS: dict[str, tuple[str, ...]] = {
    "database.inspect": ("vool-database.schema", "vool-database.connect"),
    "database.query": ("vool-database.query", "vool-database.schema", "vool-database.connect"),
    "database.mutate": ("vool-database.migrate.preview", "vool-database.schema"),
    "database.migrate": ("vool-database.migrate.preview", "vool-database.schema"),
}


_DATA_QUESTION = re.compile(
    r"\b(?:how\s+many|how\s+much|count|list|show|which|who|whom|what|whats|what's|total|sum|average|"
    r"oldest|newest|latest|earliest|largest|smallest|biggest|most|least|any|anything|does|do\s+i\s+have|"
    r"find|get|give\s+me|tell\s+me|number\s+of|is\s+there|are\s+there)\b",
    re.I,
)
_VOCAB_CACHE: dict[str, tuple[float, frozenset[str]]] = {}
# Column names that are ordinary English on their own ("what is your name", "what year is it"):
# they never make a sentence database work by themselves.
_GENERIC_COLUMNS = frozenset(
    {"id", "name", "type", "date", "time", "year", "day", "status", "text", "value", "key", "data", "note",
     "notes", "title", "code", "number", "count", "size", "state", "level", "kind", "group", "order", "end",
     "start", "user", "item", "list", "left", "right", "total", "price", "city", "country", "person"}
)


def _database_vocabulary() -> frozenset[str]:
    """Names of the disposable databases that exist and of their tables (lowercase).

    A closed vocabulary read from the person's own data, like the registered plugin intents:
    "how many books are there" names no database word, but `books` is a table here.
    """
    try:
        from core.plugin_tools import plugin_scratch_dir

        root = plugin_scratch_dir("vool-database") / "databases"
        files = sorted(p for p in root.glob("*.sqlite") if p.is_file())
    except Exception:
        return frozenset()
    words: set[str] = set()
    for path in files[:20]:
        try:
            stamp = path.stat().st_mtime
            key = str(path)
            cached = _VOCAB_CACHE.get(key)
            if cached is not None and cached[0] == stamp:
                words |= cached[1]
                continue
            import sqlite3

            found = {path.stem.lower()}
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
            try:
                tables = [
                    str(r[0]).lower()
                    for r in conn.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
                ]
                found |= set(tables)
                for table in tables[:20]:
                    for row in conn.execute('pragma table_info("%s")' % table.replace('"', "")):
                        column = str(row[1]).lower()
                        if column not in _GENERIC_COLUMNS:
                            found.add(column)
            finally:
                conn.close()
            _VOCAB_CACHE[key] = (stamp, frozenset(found))
            words |= found
        except Exception:
            continue
    return frozenset(w for w in words if len(w) >= 3)


def _names_the_persons_database(text: str) -> bool:
    """A data question that uses a name from the person's own databases/tables ("how many books are there")."""
    if not _DATA_QUESTION.search(text):
        return False
    vocab = _database_vocabulary()
    if not vocab:
        return False
    lowered = text.lower()
    stems: set[str] = set()
    for word in vocab:
        stems.add(word)
        if word.endswith("ies") and len(word) > 4:
            stems.add(word[:-3] + "y")
        if word.endswith("es") and len(word) > 4:
            stems.add(word[:-2])
        if word.endswith("s") and len(word) > 3:
            stems.add(word[:-1])
    return any(re.search(r"\b%s(?:e?s)?\b" % re.escape(stem), lowered) for stem in stems)


def _database_tool_intents(rule_intents: list[str]) -> list[str]:
    """Registered read-only database tools a plain database ask should seat, in priority order."""
    wanted: list[str] = []
    for rule_intent in rule_intents:
        for tool_intent in _DATABASE_SEATS.get(rule_intent, ()):
            if tool_intent not in wanted:
                wanted.append(tool_intent)
    if not wanted:
        return []
    try:
        from core.tool_registry import registered_tools

        registered = {
            str(c.intent) for c in registered_tools() if str(c.source or "").startswith("plugin:")
        }
    except Exception:
        return []
    return [t for t in wanted if t in registered]


def asks_for_database_work(text: str) -> bool:
    """Whether plain words ask for database work AND the database tools are registered here.

    The lane gates read this beside the wallet/email/web arms so a tools-less chat lane cannot
    keep a turn the offer would have armed (the model then only promised: "Let's connect to
    your database... shall I proceed?"). Registered-only: a runtime without the database
    plugin tools (the default) is unchanged.
    """
    try:
        return any(i.startswith("vool-database.") for i in resolve_demand_signals(text).explicit_intents)
    except Exception:
        return False


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
    database_rule_hits: list[str] = []
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
            if family == "database":
                # Family only: the rule name is not a tool, and as an "intent" it would
                # spend one of the few explicit slots the real tools below need.
                database_rule_hits.append(intent)
                if family not in families and len(families) < MAX_REQUIRED_FAMILIES:
                    families.append(family)
                continue
            _note(intent, family)
            if family == "workspace" and intent.startswith(
                ("workspace.write", "workspace.replace", "workspace.apply", "workspace.rollback")
            ):
                write_demand = True

    # "what is the biggest capacity" asks about the person's data; only a DEFINITION shape
    # ("what is a join") is talk about databases, so the vocabulary path reads the veto
    # without the bare "what is/are/does" alternatives.
    vocabulary_veto = bool(
        _DATABASE_CONTEXT_ONLY.search(
            re.sub(r"\bwhat(?:'s|\s+is|\s+are|\s+does)\b(?!\s+(?:a|an)\b)", "which", cleaned, flags=re.I)
        )
    )
    if not vocabulary_veto and "database.query" not in database_rule_hits and _names_the_persons_database(cleaned):
        database_rule_hits.append("database.query")
        if "database" not in families and len(families) < MAX_REQUIRED_FAMILIES:
            families.append("database")
    for tool_intent in _database_tool_intents(database_rule_hits):
        _note(tool_intent, "plugin")

    # Share the reader with front-door ownership: an anaphoric artifact order
    # must seat file.deliver too, not only classify as a file after seating.
    if (
        _separate_file_execution_clause(cleaned)
        or _ELLIPTICAL_FORMAT_ORDER_RE.match(cleaned)
        or _FILE_AGAIN_ORDER_RE.search(cleaned)
    ):
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
        # "nadias got a new number now, its ..." / "his number changed": an update to a saved person, read by the
        # same owner module (audit N6 K08/K09: the model answered "I'll update my records" with no Contacts tool).
        from core.contacts.requests import contact_detail_changed

        if contact_detail_changed(cleaned):
            _note("contacts.update", "contacts")
            write_demand = True
    except Exception:
        pass
    for intent in mailbox_retrieval_intents(cleaned):
        _note(intent, "email")

    for intent in outgoing_message_intents(cleaned):
        _note(intent, "email")

    for intent in _plugin_intents(cleaned):
        _note(intent, "plugin")
    # Named in plain words rather than by a dotted token: the family is wanted, no tool is
    # singled out. Only when nothing else already claimed the turn, so a plugin's vocabulary can
    # never pull a request away from the built-in tool its words asked for.
    if not intents and not families and _plugin_spoken(cleaned):
        families.append("plugin")

    for intent in _browser_lane_intents(cleaned):
        _note(intent, "web")

    if _MCP_FAMILY_PATTERN.search(cleaned) and "mcp" not in families and len(families) < MAX_REQUIRED_FAMILIES:
        families.append("mcp")

    # A public fact that changes or fresh information ("latest OpenClaw release notes", "is the
    # warsaw zoo open on mondays in winter?") needs the web as much as "search the web for ..." does.
    # Skills audit 2026-11-12: with Web lookups on, only the explicit wording seated a web tool. The
    # reading is `core.online_asks`, the clean loop's and the web-off note's too; with the web off
    # the seat is filtered as disabled and the front door says so before any model call.
    from core.online_asks import asks_for_online_facts

    web_by_online_reading_only = False
    if asks_for_online_facts(cleaned):
        asked_for_the_web = "web" in families
        _note("web.search", "web")
        web_by_online_reading_only = not asked_for_the_web and "web" in families

    return DemandSignals(
        explicit_intents=tuple(intents),
        required_families=tuple(families),
        write_demand=write_demand,
        web_by_online_reading_only=web_by_online_reading_only,
    )


__all__ = [
    "MAILBOX_RETRIEVAL_INTENTS",
    "MAX_EXPLICIT_INTENTS",
    "MAX_REQUIRED_FAMILIES",
    "DemandSignals",
    "asks_for_database_work",
    "mailbox_retrieval_intents",
    "owner_file_demand",
    "owner_file_intents",
    "plugin_follow_up",
    "plugin_follow_up_targets",
    "removed_plugins_named_in_words",
    "reset_plugin_spoken_memory",
    "resolve_demand_signals",
]


# -- owner live sources (tool-use root cause, 2026-10-29) ----------------------------------------
#
# The owner asks about something only THIS machine can answer: what is in their notes, on their
# calendar, in their folders, running on their computer, in their mailbox. Measured on the
# 2026-10-29 cloud re-audit (qwen3:8b, 8 boxes): 286 of ~470 turns ended on the tool-less
# `ordinary_plain_text_chat` route, and every R4 fake action ("your accountant thing is scheduled
# for the 3rd at 11", "Nothing suspicious is running ... based on the current system scan", "You
# don't have any videos on here yet") was one of them: the AI-first chat lane kept the turn because
# `requirements_for` saw no tool demand, so the model was never handed a tool and answered from its
# weights. The operator recognizer above is deliberately narrow (it owns the typed-turn UX of the
# chat parser); this one is the lane's question instead -- "does answering this need a live source
# on this machine?" -- and only ever SEATS a family. The model still decides, every write still
# goes through its approval gate, and nothing here executes.
#
# Precision comes from three conjoined reads, each cheap and none sufficient alone:
#   1. ask shape -- a question or an order, not a statement or a story;
#   2. the owner's own object -- a possessive or first-person inventory over a live-source noun
#      ("my notes", "have i got", "on this laptop", "in my downloads");
#   3. no advice / how-to / composition frame ("how do I make a note in the notes app", "tips for
#      keeping a calendar", "write a polite email to my landlord").
_LIVE_ASK_HEAD_RE = re.compile(
    r"^\s*(?:(?:hey|hi|so|ok|okay|alright|and|but|also|quick\s+one|please)[\s,!-]+)*"
    r"(?:(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+)?|please\s+|pls\s+)?"
    r"(?:what|whats|what's|where|when|which|who|how|is|are|am|was|were|do|does|did|have|has|any|"
    r"anything|show|list|find|pull|open|read|check|look|tell|give|search|put|pencil|book|schedule|"
    r"add|move|shift|push|reschedule|change|cancel|delete|remove|jot|pop|stick|save|note|keep|"
    r"write|make|set|count|scan|see)\b",
    re.IGNORECASE,
)
_LIVE_ADVICE_VETO_RE = re.compile(
    r"\bhow\s+(?:do|can|should|would)\s+(?:i|you|we|one)\b|\bhow\s+to\b|\btips?\b|\bideas?\b"
    r"|\bin\s+general\b|\bexplain\b|\bwhy\s+(?:do|does|is|are)\b|\bwhat\s+is\s+the\s+best\b"
    r"|\bbest\s+(?:way|app|apps)\b|\b(?:write|draft|compose)\s+(?:me\s+)?(?:a|an|the)\s+"
    r"(?:\w+\s+){0,3}(?:email|e-mail|mail|message|letter|note|poem|story)\b",
    re.IGNORECASE,
)
_LIVE_OWNER_RE = re.compile(
    r"\b(?:my|our|mine)\b|\b(?:i|we)\s+(?:have|got|own|saved|put|wrote|set|kept)\b"
    r"|\b(?:have|did|do|am|was)\s+(?:i|we)\b|\b(?:i|we)'?ve\s+got\b|\bon\s+here\b"
    r"|\b(?:this|the)\s+(?:computer|laptop|pc|mac|machine)\b|\bme\b",
    re.IGNORECASE,
)
_LIVE_NOTES_READ_RE = re.compile(
    r"\b(?:find|pull\s+up|open|show|read|look\s+(?:up|for|at)|search|any|have|got|where)\b"
    r"[^.!?]{0,40}?\bnotes?\b(?:[^.!?]{0,30}?\b(?:about|on|for|called|titled|named|re)\b)?",
    re.IGNORECASE,
)
_LIVE_NOTES_SAVE_RE = re.compile(
    r"\b(?:pop|put|add|stick|write|save|jot|keep|make|drop|leave)\b[^.!?]{0,30}?\b(?:a\s+)?note\b"
    r"|\b(?:in|to|into)\s+my\s+notes?\b(?:\s+app)?",
    re.IGNORECASE,
)
_LIVE_TIME_ANCHOR = (
    r"(?:\bon\s+the\s+\d{1,2}(?:st|nd|rd|th)?\b|\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b"
    r"|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b"
    r"|\btomorrow\b|\btonight\b|\bnext\s+week\b|\bthis\s+(?:week|weekend|morning|afternoon|evening)\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}\b)"
)
# The wording law (tests/test_wording_seats_need_an_ask_and_the_owners_own_object.py) keeps a set
# of sentences quiet that read like these asks. The notes and calendar sources below seat the same
# operator tools as the law, so its quiet sentences must not widen the offer through this door
# either: on the card chain 11 of them were offered the calendar or notes tools here (2026-11-12).
#   - "free to" / "anything to" is permission or an infinitive ("am I free to quit my job", "have I
#     got anything to declare at customs?"), unless a time word follows ("am I free to meet on
#     Friday?");
#   - a day that is free of something is a price or an entry ("is Sunday free parking in London?"):
#     the day-free idiom ends there or goes on with a time word, as in the law's availability seat;
#   - the calendar's app, settings or notes are not the owner's time ("check my calendar app, it
#     crashes on launch");
#   - an event noun used attributively ("add a call to action to my blog post for tomorrow") is
#     not a booking: the law's own list (_EVENT_ATTRIBUTIVE_VETO), read as a positive match;
#   - a question about what the assistant has seen ("have you seen my calendar notes from the
#     course?") is not an ask on the owner's notes or calendar.
_LIVE_TIME_LATER = r"[^.!?]{0,40}?" + _LIVE_TIME_ANCHOR
_LIVE_DAY_FREE_ENDS = (
    r"(?=\s*(?:[?!,.]|$|\b(?:on|at|for|or|and|in|still|now|then|today|tonight|tomorrow|this|next"
    r"|morning|afternoon|evening)\b))"
)
_LIVE_CALENDAR_THING_TAIL = r"(?!\s+(?:app|apps|application|account|settings?|sync|widget|notes?)\b)"
_LIVE_EVENT_ATTRIBUTIVE_RE = re.compile(
    r"\b(?:appointments?|meetings?|events?|calls?|syncs?)" + _EVENT_ATTRIBUTIVE_VETO.replace("(?!", "(?=", 1),
    re.IGNORECASE,
)
_LIVE_ABOUT_THE_ASSISTANT_RE = re.compile(
    r"^\s*(?:(?:hey|hi|so|ok|okay|alright|and|but|also)[\s,!-]+)*"
    r"(?:have|did)\s+(?:you|u)\s+(?:ever\s+|already\s+)?"
    r"(?:seen|see|heard|hear|read|watched|watch|noticed|notice)\b",
    re.IGNORECASE,
)
_LIVE_CALENDAR_READ_RE = re.compile(
    r"\b(?:my|our)\s+(?:calendar|diary|schedule|agenda)\b" + _LIVE_CALENDAR_THING_TAIL +
    r"|\b(?:have|got)\s+(?:i|we)\s+(?:got\s+)?anything\b(?:(?!\s+to\b)|\s+to\b" + _LIVE_TIME_LATER + r")"
    r"|\b(?:i|we)\s+(?:have|got)\s+anything\s+on\b"
    r"|\bis\s+(?:" + _LIVE_TIME_ANCHOR[3:-1] + r")(?:\s+(?:morning|afternoon|evening))?\s+"
    r"(?:clear|free|open|busy|available)\b" + _LIVE_DAY_FREE_ENDS +
    r"|\bam\s+i\s+(?:free|busy|around|available)\b(?:(?!\s+to\b)|\s+to\b" + _LIVE_TIME_LATER + r")",
    re.IGNORECASE,
)
_LIVE_CALENDAR_WRITE_RE = re.compile(
    r"\b(?:put|pencil(?:\s+in)?|book|schedule|add|move|shift|push|reschedule|change)\b"
    r"[^.!?]{0,50}?" + _LIVE_TIME_ANCHOR,
    re.IGNORECASE,
)
_LIVE_PROCESS_RE = re.compile(
    r"\b(?:programs?|apps?|processes|process|tasks?|stuff|anything)\b[^.!?]{0,40}?\b(?:running|open)\b"
    r"|\brunning\b[^.!?]{0,30}?\b(?:on\s+(?:this|my)|in\s+the\s+background|right\s+now)\b"
    r"|\b(?:hogging|eating|using|chewing|maxing)\b[^.!?]{0,20}?\b(?:memory|ram|cpu|processor)\b",
    re.IGNORECASE,
)
_LIVE_DISK_RE = re.compile(
    r"\b(?:free\s+)?(?:space|storage|room)\b[^.!?]{0,30}?\b(?:drive|disk|computer|laptop|mac|pc|ssd)\b"
    r"|\b(?:drive|disk|ssd)\b[^.!?]{0,20}?\b(?:full|space|left)\b|\bspecs?\b[^.!?]{0,30}?\b(?:computer|laptop|machine|mac|pc)\b",
    re.IGNORECASE,
)
_LIVE_FILES_RE = re.compile(
    r"\b(?:downloads|desktop|documents|pictures|photos|videos?|movies|music|songs|pdfs?|files?|"
    r"folders?|spreadsheets?|screenshots?|readme|recordings?)\b",
    re.IGNORECASE,
)
_LIVE_FILES_OWNER_RE = re.compile(
    r"\b(?:my|our)\s+(?:\w+\s+){0,2}(?:downloads|desktop|documents|pictures|photos|videos?|music|files?|"
    r"folders?|computer|laptop|drive|mac|pc)\b"
    r"|\bon\s+(?:here|this\s+(?:computer|laptop|pc|mac|machine)|my\s+(?:computer|laptop|pc|mac|desktop))\b"
    r"|\b(?:have|got)\s+(?:i|we)\b|\b(?:i|we)\s+(?:have|got)\b|\bdo\s+(?:i|we)\s+have\b",
    re.IGNORECASE,
)
_LIVE_MAIL_RE = re.compile(
    r"\b(?:inbox|mailbox|e-?mails?|mail)\b|\bemail(?:ed)?\s+me\b|\bmessaged\s+me\b",
    re.IGNORECASE,
)

#: The read-first seats per live source. The offer is still bounded by the seating cap; a write
#: intent here is only SEATED, its execution keeps every approval gate it already has.
OWNER_LIVE_SOURCE_SEATS: dict[str, tuple[str, ...]] = {
    "notes": ("operator.find_notes", "operator.show_note", "operator.save_note"),
    "calendar": ("operator.show_agenda", "operator.check_availability",
                 "operator.propose_calendar_event", "operator.update_calendar_event"),
    "processes": ("operator.inspect_processes", "machine.list_processes"),
    "disk": ("machine.disk_usage", "machine.inspect_specs", "operator.inspect_disk_usage"),
    "files": ("machine.find_file", "machine.list_directory", "machine.read_file", "machine.find_largest"),
    "mail": ("email.read",),
}
#: Each live source's toolset name (core.capability_graph._TOOLSET_HINT_TO_FAMILY).
OWNER_LIVE_SOURCE_TOOLSET: dict[str, str] = {
    "notes": "operator", "calendar": "operator", "processes": "operator",
    "disk": "filesystem", "files": "filesystem", "mail": "email",
}


def owner_live_sources(text: str) -> tuple[str, ...]:
    """Which of the owner's live sources ("notes", "calendar", "processes", "disk", "files",
    "mail") the words ask about, or () when answering needs none of them.

    Pure function of the text. Callers own the owner-local check: these sources are this
    machine's, and a remote session's words never seat them.
    """
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return ()
    try:
        from core.instructional_request import asks_for_instructions_not_execution
        from core.retrieval_constraints import analyze_retrieval_constraints
        from core.turn_prohibitions import _strip_quoted_spans
    except Exception:
        return ()
    constraints = analyze_retrieval_constraints(_strip_quoted_spans(cleaned))
    if constraints.forbids_all_tools:
        return ()
    eligible = " ".join(constraints.eligible_text.split())
    if not eligible or asks_for_instructions_not_execution(eligible):
        return ()
    sources: list[str] = []
    for segment in re.split(r"(?<=[.!?;])\s+|\n+", eligible):
        part = segment.strip()
        if not part or not _LIVE_ASK_HEAD_RE.match(part) or _LIVE_ADVICE_VETO_RE.search(part):
            continue
        owner = _LIVE_OWNER_RE.search(part) is not None
        # The notes and the calendar follow the wording law (see _LIVE_CALENDAR_READ_RE).
        about_the_assistant = _LIVE_ABOUT_THE_ASSISTANT_RE.match(part) is not None
        found: list[str] = []
        if owner and not about_the_assistant and (
            _LIVE_NOTES_SAVE_RE.search(part) or _LIVE_NOTES_READ_RE.search(part)
        ):
            found.append("notes")
        if not about_the_assistant and (
            _LIVE_CALENDAR_READ_RE.search(part)
            or (
                (owner or re.match(r"\s*(?:pencil|reschedule)\b", part, re.IGNORECASE))
                and _LIVE_CALENDAR_WRITE_RE.search(part)
                and not _LIVE_FILES_RE.search(part)
                and not _LIVE_EVENT_ATTRIBUTIVE_RE.search(part)
            )
        ):
            found.append("calendar")
        if (
            _LIVE_PROCESS_RE.search(part)
            and (owner or re.search(r"\b(?:background|right\s+now)\b", part, re.I))
            # "are you running on my mac or in the cloud" asks about VOOL itself, not a process list.
            and not re.search(r"\b(?:are|is)\s+(?:you|vool)\b|\byou\s+(?:running|run)\b", part, re.I)
        ):
            found.append("processes")
        if owner and _LIVE_DISK_RE.search(part):
            found.append("disk")
        if _LIVE_FILES_RE.search(part) and _LIVE_FILES_OWNER_RE.search(part) and "notes" not in found:
            found.append("files")
        if owner and _LIVE_MAIL_RE.search(part):
            found.append("mail")
        for source in found:
            if source not in sources:
                sources.append(source)
    if "files" not in sources and "notes" not in sources:
        from core.safe_folder_names import asks_about_a_root

        # The reads above are English. The files on one of the three roots, asked in any app
        # language ("die Dateien im Download-Ordner", "pliki na pulpicie", "桌面上的文件"), are the
        # same live source, read from the one folder table the tools resolve the folder by.
        if asks_about_a_root(eligible, need_noun=True):
            sources.append("files")
    return tuple(sources)


def owner_live_source_intents(text: str) -> tuple[str, ...]:
    """The tool seats for `owner_live_sources(text)`, in source order, deduplicated."""
    seats: list[str] = []
    for source in owner_live_sources(text):
        for intent in OWNER_LIVE_SOURCE_SEATS.get(source, ()):
            if intent not in seats:
                seats.append(intent)
    return tuple(seats)
