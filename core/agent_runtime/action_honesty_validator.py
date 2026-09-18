from __future__ import annotations

import json
import os
import re
from contextlib import suppress
from typing import Any

from core.reply_control_sanitizer import is_bare_reply_control_token, reveal_reply_control_prefix

# A leaked OpenClaw NO_REPLY token reached direct chat and OpenClaw did not suppress it. We can't
# regenerate the intended answer here, but the user must never see the raw control token.
_NO_REPLY_FALLBACK = (
    "I don't have a clear answer for that in this session. Try rephrasing it, or ask something more "
    "specific and I'll take another pass."
)

_FALSE_ACTION_CLAIM_RE = re.compile(
    r"\b(?:"
    r"(?:file|files|temp\.txt|workspace|note|notes|wallet\s+key|wallet\s+keys)\s+"
    r"(?:was|were|has\s+been|have\s+been)?\s*"
    r"(?:deleted|created|edited|modified|written|saved|wiped|cleaned)|"
    r"(?:deleted|created|edited|modified|wrote|saved|wiped|cleaned)\s+"
    r"(?:the\s+)?(?:file|files|temp\.txt|workspace|wallet\s+key|wallet\s+keys)|"
    r"(?:tool|command)\s+(?:was\s+)?(?:executed|run)|"
    r"(?:executed|ran|used)\s+(?:the\s+)?(?:tool|command)|"
    r"(?:funds|sol|(?:crypto|wallet)\s+tokens?)\s+"
    r"(?:were|was|have\s+been|has\s+been)?\s*(?:sent|moved|transferred)|"
    r"(?:sent|moved|transferred)\s+(?:all\s+)?(?:funds|sol|(?:crypto|wallet)\s+tokens?)|"
    r"cleanup\s+(?:completed|finished|done)"
    r")\b",
    re.IGNORECASE,
)
_HONEST_DENIAL_RE = re.compile(
    r"\b(?:nothing\s+was\s+executed|no\s+tool\s+was\s+run|no\s+files?\s+were\s+deleted|no\s+funds\s+were\s+sent"
    # A denial of a WRITE names the thing it did not write, so it reads like the claim it refuses.
    # Without this the guard's own replacement classifies as a persistence claim on the next pass --
    # and `emit_turn_honesty_receipt` shares this predicate, so the denial would be RECORDED as a
    # claim. Caught by the family's own "a denial is not itself a claim" test.
    r"|did\s+not\s+write\s+anything\s+to\s+memory"
    r"|nothing\s+was\s+(?:stored|persisted|saved\s+to\s+memory)"
    r")\b",
    re.IGNORECASE,
)

_HONEST_NO_EXECUTION_RESPONSE = (
    "I cannot verify the claimed action from this turn's execution record. "
    "Missing evidence does not establish that nothing ran or that files were unchanged."
)

# A completion claim about BUILD work -- files created, a project scaffolded, tests run.
#
# `_FALSE_ACTION_CLAIM_RE` above matches a verb sitting directly beside a literal noun, and that
# shape is exactly how the fabrication of 2026-07-29 21:11:07Z passed every guard. Asked for two
# files under /tmp/vool_qa_build4, the reply was "Done. Created both files and ran the tests --
# all 5 pass." Nothing was written; three separate misses each sufficient on its own:
#
#   * "created BOTH files" -- a quantifier between verb and noun defeats `created\s+(?:the\s+)?files`
#   * "wrote fizz.py"      -- the object is a real FILENAME, and the object list held only "file"
#   * "ran the tests"      -- the execution arm accepted only "tool" and "command" as objects
#
# The turn was then signed `no_action_claimed`, because `emit_turn_honesty_receipt` asks the SAME
# regex whether a claim was made: the receipt agreed there was nothing to back. So the vocabulary
# below is shared by the blocker and the receipt -- a claim the guard cannot see is a claim the
# ledger records as absent.
_BUILD_OBJECT = (
    r"(?:files?|scripts?|tests?|folders?|director(?:y|ies)|packages?|projects?|crates?|modules?|"
    r"scaffolds?|repos?|repositor(?:y|ies)|[\w./-]+\.[A-Za-z]{2,5})"
)
# Determiners, quantifiers and counts between the verb and its object: "both files", "the two
# files", "all three scripts". Zero or more, so the bare "created files" still matches.
_BUILD_DETERMINER = r"(?:\s+(?:the|a|an|both|all|each|two|three|four|five|six|those|these|your|new|other|\d+))*"
_BUILD_VERB = r"(?:created|wrote|written|made|added|generated|scaffolded|set\s+up|saved|placed)"
_BUILD_CLAIM_RE = re.compile(
    rf"\b(?:"
    rf"{_BUILD_VERB}{_BUILD_DETERMINER}\s+{_BUILD_OBJECT}"
    rf"|{_BUILD_OBJECT}\s+(?:was|were|has\s+been|have\s+been)\s+"
    rf"(?:created|written|made|added|generated|saved|scaffolded)"
    rf"|(?:ran|executed|running)\s+(?:the\s+)?(?:tests?|test\s+suite|pytest|suite)"
    rf"|(?:tests?|suite)\s+(?:all\s+)?(?:pass(?:ed|es)?|ran|green)"
    rf"|all\s+\d+\s+(?:tests?\s+)?pass(?:ed|es)?"
    rf")\b",
    re.IGNORECASE,
)
# Framing that makes a build verb somebody else's action, or an offer, rather than a self-report of
# finished work. Checked on the CLAIM'S OWN SENTENCE, never the whole reply: "Created both files.
# You can run the tests yourself." must stay blocked, and a whole-response check would exempt it.
_BUILD_CLAIM_NOT_SELF_RE = re.compile(
    r"\byou\s+(?:already\s+|just\s+)?(?:created|wrote|made|added|ran|generated|saved)\b"
    r"|\b(?:would|could|should|will|can|might|shall|ll)\s+(?:have\s+)?"
    r"(?:create|write|make|add|run|generate|save|scaffold)\b",
    re.IGNORECASE,
)
#: What a persistence claim is ABOUT. Memory, preferences and standing rules are the things a user
#: asks to be remembered, and none of them appear in the file/tool/funds vocabularies above.
_PERSISTENCE_OBJECT = (
    r"(?:workspace\s+)?(?:memory|memories)"
    r"|(?:user\s+|your\s+|my\s+|our\s+|this\s+)?(?:preference|preferences|setting|settings)"
    r"|(?:formatting\s+|standing\s+|working\s+|workspace\s+)?(?:rule|rules|directive|directives)"
    r"|(?:instruction|instructions)\s+(?:for\s+this\s+(?:chat|session|workspace))"
)

#: Verbs that report the write as FINISHED. Future and conditional forms are excluded by the
#: not-self guard below, exactly as they are for build claims: "I'll store that" is an intention.
_PERSISTENCE_VERB = (
    r"(?:stored|saved|written|wrote|added|recorded|persisted|pinned|remembered|noted|"
    r"committed|locked\s+in|set)"
)

_PERSISTENCE_CLAIM_RE = re.compile(
    rf"\b(?:"
    # "stored it in workspace memory", "saved your preference", "added the rule to memory"
    rf"{_PERSISTENCE_VERB}(?:\s+(?:it|that|this|the|a|an|your|my|our|those|these))*"
    rf"\s+(?:\w+\s+){{0,3}}(?:to|in|into)?\s*(?:{_PERSISTENCE_OBJECT})"
    # "the rule was stored", "your preference has been saved"
    rf"|(?:{_PERSISTENCE_OBJECT})\s+(?:was|were|has\s+been|have\s+been|is|are)\s+"
    rf"(?:stored|saved|written|added|recorded|persisted|updated|pinned|remembered)"
    # The reproduction's shape: a progressive write announced as happening, then "Done."
    rf"|writing\s+(?:\w+\s+){{0,3}}(?:to|into)\s+(?:{_PERSISTENCE_OBJECT})"
    rf")\b",
    re.IGNORECASE,
)

_HONEST_NO_PERSISTENCE_RESPONSE = (
    "I did not write anything to memory -- no preference, rule or setting was stored on this turn, "
    "and nothing here will carry into a later chat. I can still follow it for the rest of this "
    "conversation if you repeat it, but I am not going to say it was saved when it was not."
)

_HONEST_NO_BUILD_RESPONSE = (
    "I did not create those files and I did not run any tests -- nothing was written to disk on "
    "this turn. Tell me the exact path you want them at and I'll say up front whether I can write "
    "there."
)
# A fabricated "in progress / working on it / one moment / still running" claim when NO task actually
# ran this turn -- the "audit in progress after it already completed" failure. Runtime state, not the
# model, decides whether something is running.
_FALSE_PROGRESS_CLAIM_RE = re.compile(
    r"\b(?:audit|analysis|scan|review|task|research|work)\s+(?:is\s+)?(?:in\s+progress|underway|running|ongoing)\b"
    r"|\bin\s+progress\b"
    r"|\b(?:one\s+moment|just\s+a\s+(?:sec|second|moment)|hold\s+on|please\s+wait|working\s+on\s+it)\b"
    r"|\bstill\s+(?:working|running|in\s+progress|analy[sz]ing|auditing)\b"
    r"|\bi'?ll\s+(?:now\s+)?(?:begin|start)\s+(?:the\s+)?(?:audit|analysis|inspect\w*|scan\w*|review\w*)\b",
    re.IGNORECASE,
)
_HONEST_NO_ACTIVE_TASK_RESPONSE = (
    "Nothing is running right now — there's no task in progress in the background. "
    "Tell me what you'd like me to do and I'll run it and show the steps as they happen."
)
_FORBIDDEN_TERM_RESPONSE = (
    "I can't include the forbidden term from the current mission, and I don't have an allowed exact answer to provide."
)

# Wallet-safety guard: the model must never tell a user to reveal a private key / seed phrase to chat,
# a field, or the assistant, nor claim it moved money / signed and sent a transaction. Both contradict
# VOOL's own docs (it can't move money on its own; spends are OS-consent-gated or disabled). A beginner
# x402 example once walked the user through "enter your seed phrase ... successfully signed and sent",
# which no existing guard caught.
#
# A SOLICITATION is a REVEAL verb (enter/paste/send/...) governing the user's OWN secret — verb, then
# "your/my", then the secret, in proximity. Custody verbs (back up / write down / store / keep / have)
# are excluded: telling a user to safeguard their own secret offline is good advice, not a solicitation.
# It is exempted only when a negation directly governs the reveal verb ("never enter your seed phrase")
# or the sentence is a structural third-party warning ("anyone who asks ...", "if a site tells you ...").
_SECRET = r"(?:private key|secret key|seed phrase|secret seed|mnemonic(?:\s+phrase)?|recovery phrase)"
# Verbs that hand the secret to another party ("reveal/share/send/give ... your seed phrase") — a
# solicitation on their own. Custody verbs (back up / write down / store / keep) are absent by design.
_WALLET_GIVE_VERB = (
    r"reveal(?:s|ing|ed)?|disclos(?:e|es|ing|ed)|shar(?:e|es|ing|ed)|send(?:s|ing)?|sent|"
    r"giv(?:e|es|ing)|gave|tell(?:s|ing)?|told|show(?:s|ing|ed)?|provid(?:e|es|ing|ed)"
)
# Verbs that put the secret into a field/tool ("enter/paste/type/export ..."). A solicitation only when
# directed at chat / the assistant, or with a phishing purpose — "enter your seed phrase into your
# hardware wallet" and "export your key to a backup file" are legitimate self-custody.
_WALLET_SUBMIT_VERB = (
    r"enter(?:s|ing|ed)?|past(?:e|es|ing|ed)|input(?:s|ting|ted)?|typ(?:e|es|ing|ed)|"
    r"submit(?:s|ting|ted)?|export(?:s|ing|ed)?|cop(?:y|ies|ying|ied)"
)
_WALLET_ALL_VERB = f"{_WALLET_GIVE_VERB}|{_WALLET_SUBMIT_VERB}"
_WALLET_GIVE_NEAR_SECRET_RE = re.compile(
    rf"\b(?:{_WALLET_GIVE_VERB})\b[^.\n]{{0,25}}?(?:\b(?:your|my|the|a|this)\s+)?(?:\w+\s+){{0,2}}?{_SECRET}\b",
    re.IGNORECASE,
)
_WALLET_SUBMIT_NEAR_SECRET_RE = re.compile(
    rf"\b(?:{_WALLET_SUBMIT_VERB})\b[^.\n]{{0,25}}?(?:\b(?:your|my|the|a|this)\s+)?(?:\w+\s+){{0,2}}?{_SECRET}\b",
    re.IGNORECASE,
)
# A submit destination: chat / a field / the assistant / a site. Honest, educational, and self-custody
# statements never carry this ("back up your seed phrase offline", "export your key to a file").
_WALLET_SUBMIT_DEST_RE = re.compile(
    r"\bhere\b|\bbelow\b|\binto\b|\bin\s+(?:the|this)\s+(?:box|field|dialog|form|chat|window|prompt|page|site|app|bar)\b|"
    r"\bto\s+(?:me|us)\b|\b(?:send|give|tell|show|paste)\s+(?:me|us)\b|\bwith\s+me\b|\bin\s+chat\b|"
    r"\bto\s+(?:the|this)\s+(?:site|website|app|chat|form|dialog|box|field|bot|assistant|page)\b",
    re.IGNORECASE,
)
# A phishing purpose that makes a submit-verb a solicitation even without an explicit field destination
# ("enter your private key to verify ownership / to restore access").
_WALLET_SUBMIT_PURPOSE_RE = re.compile(
    r"\bto\s+(?:verify|confirm|validate|restore|recover|unlock|prove|authenticate|continue|proceed|complete|activate)\b"
    r"|\b(?:verify|confirm|validate|restore|recover|unlock|authenticate)\b[^.\n]{0,20}?"
    r"\b(?:ownership|your\s+wallet|access|identity|account)\b",
    re.IGNORECASE,
)
# A negation directly governing a reveal/submit verb ("never enter/paste/share ...").
_WALLET_VERB_NEGATION_RE = re.compile(
    rf"\b(?:never|do not|don't|dont|cannot|can't|won't|will not|would not|wouldn't|must not|"
    rf"no need to|should not|shouldn't|without)\s+(?:ever\s+)?(?:{_WALLET_ALL_VERB})\b",
    re.IGNORECASE,
)
# A meta-negation about ASKING: "VOOL never asks/needs/tells you to enter ..." — the guard's own
# thesis. The negation governs the ask/need verb (not the reveal verb), with "you" downstream.
_WALLET_META_NEGATION_RE = re.compile(
    r"\b(?:never|not|does\s+not|doesn't|won't|will\s+not|do\s+not|don't|cannot|can't|no\s+need\s+to)\b"
    r"[^.\n]{0,20}?\b(?:ask|asks|asked|tell|tells|told|need|needs|require|requires|request|requests|"
    r"want|wants|prompt|prompts|make|makes)\b[^.\n]{0,15}?\byou\b",
    re.IGNORECASE,
)
# Structural third-party / conditional warning framing (describes an attacker, not a first-person ask).
_WALLET_THIRDPARTY_FRAME_RE = re.compile(
    r"\b(?:any|every|some|no)(?:one|body)\s+(?:who\s+|ever\s+)?(?:asks?|tells?|prompts?|requests?|wants?)\b|"
    r"\banyone who\b|\bshould\s+(?:a|an|any|some(?:one|body)?|the|vool)\b|\bwhenever\b|"
    r"\bif\s+(?:a|an|any(?:one|body)?|every(?:one|body)?|some(?:one|body)?|no(?:one|body)?|the)\b[^.\n]{0,40}?"
    r"\b(?:asks?|tells?|prompts?|requests?|wants?)\b",
    re.IGNORECASE,
)
# A HYPOTHETICAL ask: a modal verb governing an ask/tell verb directed at "you" ("a malicious plugin
# could ask you to enter ..."). The 2026-09-15 incident: an educational security review answer --
# "a malicious plugin could instruct the user to paste their seed phrase into chat, then read it" --
# was replaced wholesale by the wallet boilerplate because the description of an ATTACKER'S ask read
# to the matcher exactly like the assistant's own ask. A real solicitation never carries this shape:
# "please paste your seed phrase below" has no modal and no ask-verb before "you".
_WALLET_MODAL_ASK_FRAME_RE = re.compile(
    r"\b(?:could|can|may|might|would)\b[^.\n]{0,30}\b(?:ask|asks|told|tell|tells|instruct|instructs|"
    r"prompt|prompts|trick|tricks|lure|lures|convince|mislead)\b[^.\n]{0,30}?\byou\b",
    re.IGNORECASE,
)
# Possession of the secret by somebody OTHER than the reader ("their/his/her seed phrase", "the
# user's private key") inside the verb-to-secret span: third-person narrative, not an ask of the
# person reading the reply. A first-person solicitation hands over the READER'S secret -- "your".
_WALLET_THIRD_PARTY_POSSESSION_RE = re.compile(
    r"\b(?:their|his|her|its|the\s+user'?s?|a\s+user'?s?)\b", re.IGNORECASE
)
# False "money moved / payment complete" claims. A money/tx object is required (so "signed the report
# and sent it" is not flagged), "confirmed" is excluded (it collides with balance/detail lookups), and
# hypothetical framing ("once the tx is settled you get a receipt") is exempted below.
#
# The PASSIVE arm takes only past/perfect auxiliaries (was/were/has been/have been) -- a completed
# event this runtime could be reporting. Bare present "is/are" describes how a system behaves
# ("the payment is sent only after approval", "funds are moved without consent" -- the 2026-09-15
# incident's risk-review sentences), which is design prose, not a claim that a payment happened.
_MONEY = r"(?:transaction|payment|tx|funds?|sol\b|usdc|transfers?|the\s+spend)"
_WALLET_COMPLETION_VERB = (
    r"broadcast|submitted|settled|completed?|sent|processed|moved|transferred|delivered|cleared|"
    r"finalized|gone\s+through|went\s+through|on\s+(?:its|the)\s+way|left\s+your\s+wallet"
)
_WALLET_PAST_AUX = r"(?:has\s+been\s+|have\s+been\s+|had\s+been\s+|was\s+|were\s+|been\s+)"
_WALLET_FALSE_SIGN_RE = re.compile(
    rf"\b(?:successfully\s+)?signed\s+and\s+sent\b[^.\n]{{0,30}}?\b{_MONEY}"
    rf"|\bsign(?:ed)?\b[^.\n]{{0,40}}?\band\b[^.\n]{{0,40}}?\bsent\b[^.\n]{{0,30}}?\b{_MONEY}"
    rf"|\b{_MONEY}[^.\n]{{0,25}}?{_WALLET_PAST_AUX}?(?<!is )(?<!are )(?:{_WALLET_COMPLETION_VERB})\b"
    rf"|\b(?:{_WALLET_COMPLETION_VERB})\b\s+(?:the\s+|your\s+)?{_MONEY}"
    rf"|\bpayment\s+(?:is\s+)?complete\b",
    re.IGNORECASE,
)
_WALLET_HYPOTHETICAL_RE = re.compile(
    r"\b(?:once|when|after|whenever|if|as\s+soon\s+as|will\s+be|would\s+be)\b", re.IGNORECASE
)
# Framing that makes a matched completion somebody else's behaviour or a possibility, not this
# runtime's report: a modal ("funds could be moved"), or a named external actor ("the attacker
# sent the payment") before the claim. The incident's Risk-2 sentence -- "a compromised plugin
# could trigger the payment with no preview, so funds are moved without informed consent" --
# describes an abuse scenario; replacing that answer called the model's honest risk review a lie.
_WALLET_CLAIM_POSSIBILITY_RE = re.compile(
    r"\b(?:could|can|may|might|would)\b",
    re.IGNORECASE,
)
_WALLET_CLAIM_THIRDPARTY_ACTOR_RE = re.compile(
    r"\b(?:attacker|adversary|phisher|scammer|malicious|compromised|rogue|untrusted|third[- ]party|"
    r"someone|anyone|no\s+one|nobody|a\s+user|the\s+user|plugin|skill|extension)\b",
    re.IGNORECASE,
)
_WALLET_CLAIM_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|cannot|can't|won't|will not|did not|didn't|have not|haven't|has not|hasn't|n't)\b",
    re.IGNORECASE,
)
# The wallet-safety correction is COMPOSED, not static: the conduct law (never ask for, never
# handle, a secret) is constant, but the capability clause is read from the wallet's own config at
# publication time. The previous fixed paragraph asserted `.null` registration, Windows Hello
# prompts and a disabled "USDC x402 spend lane" -- product facts that stopped being true of the
# build it shipped from. A safety correction whose capability claims are stale is a new honesty
# defect, so nothing here may name a lane, an OS prompt or a chain the runtime did not report.
def _wallet_safety_correction_text(*, payment_claim: bool) -> str:
    try:
        from core.wallet import config as wallet_config

        wallet_on = bool(wallet_config.wallet_enabled())
        capability = (
            "Any wallet payment is previewed and needs your explicit approval before it can run; "
            "VOOL cannot move money on its own."
            if wallet_on
            else "Wallet payments are disabled in this session, so nothing can spend and no "
            "key is ever needed from you."
        )
    except Exception:
        capability = (
            "This session's wallet state could not be read here; regardless, nothing asks you for "
            "a private key or seed phrase."
        )
    lead = (
        "On wallet safety: VOOL never asks for, and never handles, your private key or seed "
        "phrase, and you should never paste one into chat or any field. " + capability
    )
    if payment_claim:
        return (
            lead
            + " No funds were sent and no payment was executed by this runtime on this turn -- "
            "there is no spend receipt behind that claim."
        )
    return lead


# Tool-name markers of a REAL wallet spend lane having run this turn (the wallet authority's own
# tool family is `wallet.*`). A payment-completion claim on a turn whose execution record shows a
# spend lane may be receipt-backed, so the claim is left to the evidence gates that own tool-backed
# claims; the replacement below is for claims with nothing behind them.
_WALLET_SPEND_TOOL_MARKERS = ("wallet", "spend", "x402", "settle", "transfer")


def _split_sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?\n])\s+", str(text or ""))


def _split_clauses(text: str) -> list[str]:
    # Split on sentence AND clause boundaries (incl. ; : , —) so a trailing exempting clause
    # ("...; no need to do anything") cannot be glued to the claim it is supposed to (but does not)
    # negate.
    return re.split(r"(?<=[.!?;:\n])\s+|,\s+|\s+[—-]\s+", str(text or ""))


def _solicits_wallet_secret(text: str) -> bool:
    for sentence in _split_sentences(text):
        match = _WALLET_GIVE_NEAR_SECRET_RE.search(sentence) or _WALLET_SUBMIT_NEAR_SECRET_RE.search(sentence)
        gives = bool(_WALLET_GIVE_NEAR_SECRET_RE.search(sentence))
        submits = bool(_WALLET_SUBMIT_NEAR_SECRET_RE.search(sentence)) and bool(
            _WALLET_SUBMIT_DEST_RE.search(sentence) or _WALLET_SUBMIT_PURPOSE_RE.search(sentence)
        )
        if not (gives or submits):
            continue
        if match is not None and _WALLET_THIRD_PARTY_POSSESSION_RE.search(match.group(0)):
            # "instruct the user to paste THEIR seed phrase" -- the secret belongs to a described
            # third party, so the sentence narrates somebody else's behaviour; it does not ask the
            # person reading this reply to hand anything over.
            continue
        if _WALLET_VERB_NEGATION_RE.search(sentence):
            continue  # "never enter your seed phrase" — a warning, not a solicitation
        if _WALLET_META_NEGATION_RE.search(sentence):
            continue  # "VOOL never asks you to enter your seed phrase into chat"
        if _WALLET_THIRDPARTY_FRAME_RE.search(sentence):
            continue  # "anyone who asks you to paste your seed phrase ...", "should a site ask ..."
        if _WALLET_MODAL_ASK_FRAME_RE.search(sentence):
            continue  # "a malicious plugin could ask you to enter ..." — hypothetical, not this assistant
        return True
    return False


def completion_claim_kind(text: str) -> str:
    """What kind of finished work a reply claims: ``""``, ``"mutation"``, ``"build"`` or ``"persistence"``.

    One predicate for both consumers. The blocker uses it to decide whether a claim needs a
    receipt; `emit_turn_honesty_receipt` uses it to decide whether the turn made a claim at all.
    Splitting those two readings is what let a fabricated build be signed `no_action_claimed`.

    ``"mutation"`` wins ties: it is the older, narrower vocabulary (delete / wipe / send funds) and
    it carries the response the existing callers already expect.
    """

    body = str(text or "")
    if not body.strip():
        return ""
    if _HONEST_DENIAL_RE.search(body):
        # A denial of an action is not an action claim. Checked here as well as at the blocker,
        # because `emit_turn_honesty_receipt` calls this predicate directly and would otherwise
        # sign an honest denial as a claim needing a receipt.
        return ""
    for sentence in _split_sentences(body):
        mutation = _FALSE_ACTION_CLAIM_RE.search(sentence)
        if mutation is None:
            continue
        # A past participle can describe an object's state rather than report an action by this
        # runtime: "recovery tools can restore deleted files".  The old whole-response regex read
        # that educational explanation as "I deleted files" and replaced every independent
        # sibling with a generic denial.  A modal governing the matched phrase is non-completion
        # unless the sentence explicitly frames it as this assistant's confirmation/report.
        prefix = sentence[: mutation.start()]
        modal_context = re.search(
            r"\b(?:can|could|may|might|would|should)\b[^.!?;:]*$",
            prefix,
            re.IGNORECASE,
        )
        self_report = re.search(
            r"\b(?:i|we)\s+(?:(?:can|could|will)\s+)?(?:confirm|report|verify|state)\b",
            prefix,
            re.IGNORECASE,
        )
        if modal_context and not self_report:
            continue
        return "mutation"
    for sentence in _split_sentences(body):
        if not _BUILD_CLAIM_RE.search(sentence):
            continue
        if _BUILD_CLAIM_NOT_SELF_RE.search(sentence) or _NOT_A_SELF_CLAIM_RE.search(sentence):
            continue  # an offer, a plan, or something the USER did -- not a report of work done
        return "build"
    for sentence in _split_sentences(body):
        # A claimed WRITE TO MEMORY. Measured live: asked to remember a formatting rule, the reply
        # was "Writing formatting rule to workspace memory... Done." on a turn whose work log
        # recorded ZERO actions. Nothing was stored, the user relied on it for eight further turns,
        # and every gate above passed it -- memory and preferences appear in neither the file/tool
        # vocabulary nor the build vocabulary. Same evidence gate as the other two kinds: a real
        # executed receipt or mode=tool_executed, or the claim does not ship.
        if not _PERSISTENCE_CLAIM_RE.search(sentence):
            continue
        if _BUILD_CLAIM_NOT_SELF_RE.search(sentence) or _NOT_A_SELF_CLAIM_RE.search(sentence):
            continue  # "I'll store that" is an intention, not a report of a finished write
        return "persistence"
    return ""


def _claims_false_payment(text: str) -> bool:
    for sentence in _split_sentences(text):
        sentence_match = _WALLET_FALSE_SIGN_RE.search(sentence)
        if sentence_match is None:
            continue
        # The sentence BEFORE the claim can carry its framing: a modal ("funds could be moved") or
        # a named external actor ("a compromised plugin ... so funds are moved"). The clause the
        # regex matched inherits that framing, so it is not this runtime reporting its own spend.
        prefix = sentence[: sentence_match.start()]
        narrated = bool(
            _WALLET_CLAIM_POSSIBILITY_RE.search(prefix)
            or _WALLET_CLAIM_THIRDPARTY_ACTOR_RE.search(prefix)
        )
        for clause in _split_clauses(sentence):
            match = _WALLET_FALSE_SIGN_RE.search(clause)
            if not match:
                continue
            if _WALLET_CLAIM_NEGATION_RE.search(clause[max(0, match.start() - 20) : match.start()]):
                continue  # a negation adjacent to the claim ("no funds were sent")
            if _WALLET_HYPOTHETICAL_RE.search(clause):
                continue  # "once the transaction is settled, you get a receipt" — generic protocol, not a claim
            if narrated:
                continue  # somebody else's behaviour or a possibility, not a report of this runtime's spend
            return True
    return False


def _turn_executed_a_spend(*, session_id: str | None, source_context: dict[str, object] | None) -> bool:
    """Whether this turn's execution record shows a wallet-spend lane actually ran.

    Shares the turn-scoped execution truth the signed receipts use, so a previous turn's spend can
    never authorize this turn's claim. Marker-matched on the tool name because the wallet
    authority owns that family (`wallet.*`, x402, settlement); a marker hit only ever SUPPRESSES a
    replacement, it never invents one.
    """
    for entry in _collect_executed_tools(session_id, source_context):
        tool = str(entry.get("tool") or "").lower()
        if any(marker in tool for marker in _WALLET_SPEND_TOOL_MARKERS):
            return True
    return False


def _enforce_wallet_secret_safety(
    output: dict[str, Any],
    *,
    session_id: str | None = None,
    source_context: dict[str, object] | None = None,
) -> dict[str, Any]:
    response = str(output.get("response") or "")
    if not response:
        return output
    solicits = _solicits_wallet_secret(response)
    payment = _claims_false_payment(response)
    if not (solicits or payment):
        return output
    if payment and not solicits and _turn_executed_a_spend(session_id=session_id, source_context=source_context):
        # A wallet lane genuinely executed this turn; the completion claim may be receipt-backed
        # and the tool-claim gates below own judging it. Replacing it here would call a real,
        # approved spend a fabrication.
        return output
    output["response"] = _wallet_safety_correction_text(payment_claim=payment)
    output["confidence"] = min(float(output.get("confidence") or 1.0), 0.9)
    output["action_honesty_validator"] = {
        "applied": True,
        "reason": "wallet_secret_safety",
        "trigger": "false_payment_claim" if (payment and not solicits) else "wallet_secret_solicitation",
        "original_response_excerpt": response[:240],
    }
    output["route"] = str(output.get("route") or "action_honesty_final_validator")
    output["route_reason"] = (
        "false_payment_claim_blocked" if (payment and not solicits) else "wallet_secret_solicitation_blocked"
    )
    return output


def _truthy_execution_status(value: object) -> bool:
    status = str(value or "").strip().lower()
    return status in {"ok", "success", "succeeded", "executed", "completed", "tool_executed"}


def _receipt_shows_execution(receipt: dict[str, Any]) -> bool:
    execution = receipt.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    if execution.get("executed") is True or execution.get("ok") is True:
        return True
    if _truthy_execution_status(execution.get("status") or execution.get("mode") or execution.get("outcome")):
        return True
    return _truthy_execution_status(receipt.get("status") or receipt.get("mode") or receipt.get("event_type"))


# Claims that WORK WAS PERFORMED on something. `_FALSE_ACTION_CLAIM_RE` above covers mutation
# (deleted/created/saved) and payments; it has no inspection vocabulary at all, so "the audit ran —
# it inspected the whole workspace" sailed through every guard while the named file was never
# opened. Reading is the most common thing this product claims to have done, and it was unguarded.
#
# PAST TENSE ONLY, and that distinction is load-bearing. "You should audit index.html — want me
# to?" is an OFFER; blocking it would make the assistant unable to propose work. Only a claim that
# the work is already DONE can be false about what ran.
_INSPECTION_CLAIM_RE = re.compile(
    r"\b(?:audited|inspected|analy[sz]ed|reviewed|scanned|examined|checked|"
    r"went\s+through|read\s+through|looked\s+(?:at|through|over)|"
    r"(?:audit|analysis|review|scan)\s+(?:ran|completed|finished|is\s+(?:done|complete)))\b",
    re.IGNORECASE,
)
# Framing that makes a past-tense verb hypothetical or second-person rather than a self-report.
_NOT_A_SELF_CLAIM_RE = re.compile(
    r"\b(?:you\s+(?:should|could|can|might|may|need\s+to|want\s+to)|"
    r"if\s+(?:you|we|i)\s+|would\s+have|could\s+have|should\s+have|"
    r"have\s+you|did\s+you|want\s+me\s+to|shall\s+i|i\s+(?:can|could|will|would|should)\s+)",
    re.IGNORECASE,
)
# A filename or path the sentence says was inspected. Narrow on purpose: a bare word is not a
# target, and a claim with no object ("I had a look") is not checkable.
_CLAIMED_TARGET_RE = re.compile(
    r"[`'\"]?((?:[\w\-.]+/)*[\w\-]+\.[A-Za-z][A-Za-z0-9]{0,5})[`'\"]?",
)
# Only a token whose extension is one a file actually has counts as a claimed target. Driven live
# 2026-08-01: an audit report quoting `blob.startswith` was parsed as a claimed file `blob.starts`,
# no execution record existed for that "file", and the ENTIRE report was replaced with "I did not
# actually open `blob.starts`". Dotted CODE expressions are everywhere in a report that quotes the
# code it audited; per this gate's own contract — fail open in every ambiguous direction — an
# unknown extension is ambiguity, not a claim.
#
# The list itself now lives in `core/agent_runtime/file_target_contract.py`. It was private here,
# and six days later `audit_target_in` re-decided the same question with a five-entry blocklist and
# parsed `workspace.write_file` as a file. A fix that lives inside one extractor is a fix that does
# not travel, so both extractors read one contract.
from core.agent_runtime.file_target_contract import REAL_FILE_EXTENSIONS as _REAL_FILE_EXTENSIONS


def _claimed_inspection_targets(response: str, user_input: str) -> list[str]:
    """Files the reply says it inspected. The user's own naming counts — that is the whole point.

    The answer binder deliberately EXEMPTS filenames the user typed, so that "did you find
    notes.md?" does not flag every reply. That exemption is right for claims about CONTENT and
    exactly wrong here: the file the user named is the file whose inspection is being claimed, and
    the one whose absence was the bug.
    """

    found: list[str] = []
    for text in (response, user_input):
        for match in _CLAIMED_TARGET_RE.finditer(str(text or "")):
            candidate = match.group(1)
            stem, _, extension = candidate.rpartition(".")
            if not stem or extension.lower() not in _REAL_FILE_EXTENSIONS:
                continue
            if candidate not in found:
                found.append(candidate)
    return found


def _enforce_inspection_claims(
    output: dict[str, Any],
    *,
    user_input: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Refuse a claim of having inspected something no tool actually opened.

    Referential, not existential. The check this replaces asked "did ANY tool run this session",
    which the audit satisfied by reading two unrelated files — so a reply claiming it had audited
    `app-landing/index.html` was waved through. Here the question is whether a record exists whose
    resolved target IS the thing named.

    Fails open in every ambiguous direction: no inspection verb, no named target, no records at
    all, or a target that WAS read — all pass untouched. It only fires when the reply names a file,
    claims to have inspected it, and the session shows a real execution that was not that file.
    """

    response = str(output.get("response") or "").strip()
    if not response or not _INSPECTION_CLAIM_RE.search(response):
        return output
    if _NOT_A_SELF_CLAIM_RE.search(response):
        return output  # an offer or a hypothetical, not a report of work done
    targets = _claimed_inspection_targets(response, user_input)
    if not targets:
        return output
    try:
        from core.execution_records import verify_claim
    except Exception:
        return output

    unsupported: list[dict[str, Any]] = []
    for target in targets:
        verdict = verify_claim(str(session_id or ""), target=target)
        if not verdict.get("any_execution"):
            # Nothing ran at all — the mutation path below already covers a bare "I did it", and
            # firing here would flag ordinary conversation about a filename.
            return output
        if not verdict.get("supported"):
            unsupported.append(verdict)
    if not unsupported:
        return output

    named = ", ".join(f"`{item['target']}`" for item in unsupported)
    ran = sorted({intent for item in unsupported for intent in item.get("executed") or ()})
    guarded = dict(output)
    guarded["response"] = (
        f"I did not actually open {named}, so I can't report on it. "
        + (f"What ran this turn: {', '.join(ran)}. " if ran else "")
        + "Ask me again and I'll read it directly."
    )
    guarded["confidence"] = min(float(guarded.get("confidence") or 1.0), 0.3)
    guarded["route_reason"] = "unsupported_inspection_claim"
    guarded["inspection_claim_validator"] = {
        "applied": True,
        "unsupported_targets": [item["target"] for item in unsupported],
        "executed_intents": ran,
        "original_excerpt": response[:240],
    }
    return guarded


def _current_turn_has_executed_receipt(
    *,
    session_id: str | None,
    source_context: dict[str, object] | None,
) -> bool:
    # Share the turn-scoped authority used by the signed honesty receipt. A
    # previous turn's receipt must not authorize this turn's completion claim.
    return bool(_collect_executed_tools(session_id, source_context))


def _active_mission_forbidden_terms(session_id: str | None) -> list[str]:
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return []
    try:
        from core.active_mission import current_active_mission_slots

        terms: list[str] = []
        for slot in current_active_mission_slots(clean_session):
            name = str(slot.get("slot_name") or "")
            value = str(slot.get("value") if slot.get("value") is not None else slot.get("value_raw") or "").strip()
            if name.startswith("forbidden_term:") and value:
                terms.append(value)
        return terms
    except Exception:
        return []


def _enforce_active_mission_forbidden_terms(
    output: dict[str, Any],
    *,
    session_id: str | None,
) -> dict[str, Any]:
    response = str(output.get("response") or "").strip()
    if not response:
        return output
    lowered = response.lower()
    matched = [
        term
        for term in _active_mission_forbidden_terms(session_id)
        if term and term.lower() in lowered
    ]
    if not matched:
        return output
    guarded = dict(output)
    guarded["response"] = _FORBIDDEN_TERM_RESPONSE
    guarded["confidence"] = min(float(guarded.get("confidence") or 1.0), 0.88)
    guarded["forbidden_term_validator"] = {
        "applied": True,
        "reason": "active_mission_forbidden_term",
        "blocked_count": len(matched),
        "original_response_excerpt": response[:240],
    }
    guarded["route_reason"] = "forbidden_term_blocked"
    return guarded


# Keys that mean "this JSON is a tool call, not an answer". Deliberately broad: the point is to
# catch shapes we have NOT seen, since a shape we already parse never reaches here.
_TOOL_CALL_KEYS = frozenset(
    {"intent", "action", "tool", "tool_name", "name", "function", "command", "operation"}
)


def _suppress_leaked_tool_call(output: dict[str, Any]) -> dict[str, Any]:
    """Never show the user a reply that is a tool call the runtime failed to recognise.

    Observed live 2026-07-28 on a free cloud model: asked to audit a file, it replied with the
    literal text `{"action": "read", "path": "config.py"}`, which was displayed as the assistant's
    answer. The repair parser was right to refuse it — "read" is not an offered tool and the shape
    is invented — but refusing to DISPATCH it is not the same as refusing to SHOW it.

    So this is containment rather than compatibility: it does not try to guess what the model
    meant. Whatever new shape a model invents next, a bare JSON object carrying a tool-call key and
    no prose is not an answer, and the user gets an honest line instead of machine output.

    Prose that merely contains JSON is untouched — a real answer explaining a config file may quote
    one. Only a reply that is *nothing but* the object qualifies.
    """

    response = str(output.get("response") or "").strip()
    if not response.startswith("{") or not response.endswith("}"):
        return output
    if len(response) > 600:  # a long object is far more likely quoted content than a call
        return output
    try:
        parsed = json.loads(response)
    except (TypeError, ValueError):
        return output
    if not isinstance(parsed, dict) or not (_TOOL_CALL_KEYS & {str(k).lower() for k in parsed}):
        return output

    guarded = dict(output)
    guarded["response"] = (
        "I started to call a tool and the request came back in a shape I could not run, so I "
        "stopped rather than show you machine output. Ask again and I'll retry."
    )
    guarded["leaked_tool_call_suppressed"] = {
        "applied": True,
        "keys": sorted(str(k) for k in parsed)[:8],
        "original_excerpt": response[:200],
    }
    guarded["route_reason"] = "leaked_tool_call_suppressed"
    guarded["confidence"] = min(float(guarded.get("confidence") or 1.0), 0.3)
    return guarded


def _apply_answer_binding(
    output: dict[str, Any],
    *,
    user_input: str,
    session_id: str | None,
) -> dict[str, Any]:
    """Attach the answer-to-tool binding verdict. Shadow mode by default: it does not act.

    Placed here because this function is on every return path a turn has, so a check here covers
    the front-door fast paths as well as the model lane. It only ever reads: with the enforce flag
    off it records a verdict and changes nothing, so the acting path can be turned on once the
    logged false-positive rate has been measured on real traffic rather than guessed at.

    Never raises. A verification layer that can break a turn is a worse bug than the one it catches.
    """

    from core.runtime_flags import flag_enabled

    if not (flag_enabled("answer_binder_shadow") or flag_enabled("answer_binder_enforce")):
        return output
    try:
        from core import answer_binder

        verdict = answer_binder.check(
            str(output.get("response") or ""),
            session_id=str(session_id or ""),
            user_input=str(user_input or ""),
        )
    except Exception:
        return output
    if not verdict.checked:
        return output

    guarded = dict(output)
    guarded["answer_binding"] = verdict.as_dict()
    if verdict.ok or not flag_enabled("answer_binder_enforce"):
        return guarded

    # Enforcing. The answer claimed something the tools did not produce, so fall back to what they
    # actually returned. A plain listing is a worse answer than good prose and a far better one
    # than a confident invention.
    try:
        from core import answer_binder as _binder

        rendered = _binder.deterministic_rendering(str(session_id or "")).strip()
    except Exception:
        rendered = ""
    if not rendered:
        return guarded
    guarded["response"] = rendered
    guarded["answer_binding"]["applied"] = True
    guarded["route_reason"] = "answer_binding_regrounded"
    guarded["confidence"] = min(float(guarded.get("confidence") or 1.0), 0.6)
    return guarded



def _bind_runtime_claims_to_evidence(
    output: dict[str, Any],
    *,
    user_input: str,
    session_id: str | None,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    """Hold the reply to the runtime's own execution trace, and repair the sentences that fight it.

    The gap this closes. Every other guard in this file asks whether a POSITIVE claim has a receipt
    behind it. None of them can see a DENIAL, so when Activity recorded an attempted fetch of
    `github.com/VOOL-ai/openclaw-skills` and the reply said "I never tried GitHub", the sentence
    passed untouched — the durable trace and the prose disagreed and nothing preferred the trace.
    The same blind spot let a reply assert a *provider-attested* model with no attestation source
    anywhere in the runtime.

    Deterministic repair rather than a block or a retry: the smallest architecture consistent with
    `_apply_answer_binding` above, which also rewrites rather than regenerating. One sentence is
    replaced with evidence-backed wording and the rest of the answer survives.

    Never raises, and refuses to judge whenever it cannot identify the turn — see
    `bind_response_to_evidence`.
    """

    response = str(output.get("response") or "").strip()
    if not response:
        return output
    try:
        from core.agent_runtime.evidence_claim_binder import (
            bind_response_to_evidence,
            question_covers_whole_conversation,
        )
        from core.runtime_evidence import collect_turn_evidence, remote_account_is_complete

        # This function runs TWICE on the /api/chat lane: once from `core.web.api.runtime` inside
        # `remote_fetch_policy_scope`, and again from `core.web.api.service` after that scope has
        # closed. The completeness of the turn's remote account is only READABLE inside the scope,
        # so the second pass saw "cannot prove a negative" where the first had proved one — and
        # rewrote a correct "no, I did not use the web" into "I cannot verify". Measured on a live
        # drive. The first pass therefore records what it proved, and every later pass reads that
        # back rather than re-deriving it from a vantage point that no longer has the answer.
        stamped = output.get("evidence_scope")
        remote_complete = (
            bool(stamped.get("remote_account_complete"))
            if isinstance(stamped, dict)
            else remote_account_is_complete()
        )
        evidence = collect_turn_evidence(
            session_id=session_id,
            source_context=source_context,
            remote_account_complete=remote_complete,
        )
        outcome = bind_response_to_evidence(
            response,
            evidence,
            whole_conversation=question_covers_whole_conversation(user_input),
        )
    except Exception:
        return output  # a verification layer that can break a turn is worse than the bug it catches
    output = dict(output)
    output["evidence_scope"] = {"remote_account_complete": remote_complete}
    if not outcome.checked or not outcome.applied:
        return output

    guarded = dict(output)
    guarded["response"] = outcome.response
    guarded["evidence_binding"] = {**outcome.as_dict(), "evidence": evidence.as_dict()}
    guarded["route_reason"] = (
        "evidence_contradiction_repaired" if outcome.contradicted else "unprovable_runtime_claim_repaired"
    )
    guarded["confidence"] = min(float(guarded.get("confidence") or 1.0), 0.5 if outcome.contradicted else 0.7)
    return guarded


def _verify_audit_claims_against_evidence(
    output: dict[str, Any], *, source_context: dict[str, object] | None
) -> dict[str, Any]:
    """Mark an audit statement the audit's own evidence contradicts.

    Measured on a real project 2026-07-29: a cloud-model audit asserted "the repository has zero
    tests" (48 test files present), "no external dependencies" (the file opens with
    `import zstandard`), and described a `decode()` method that does not exist. The report was well
    structured, which is what made it dangerous — an independent review scored it 8/10 on structure
    and 0/10 on verification.

    Runs only when a workspace audit actually collected evidence this turn; with nothing to check
    against, every claim would be "unverifiable" and flagging them all would be noise the operator
    learns to skip.
    """

    context = dict(source_context or {})
    evidence_blob = context.get("workspace_audit_evidence")
    response = str(output.get("response") or "")
    if not isinstance(evidence_blob, dict) or not response.strip():
        return output
    try:
        from core.agent_runtime.audit_claim_verifier import AuditEvidence, verify_audit_claims

        annotated, defects = verify_audit_claims(
            response,
            AuditEvidence(
                inspected_paths=tuple(evidence_blob.get("inspected_paths") or ()),
                all_paths=tuple(evidence_blob.get("all_paths") or ()),
                sources=dict(evidence_blob.get("sources") or {}),
                workspace_root=str(evidence_blob.get("workspace_root") or ""),
                incomplete_files=tuple(evidence_blob.get("incomplete_files") or ()),
            ),
        )
    except Exception:
        return output  # a verifier fault must never cost the operator their answer
    if not defects:
        return output
    output["response"] = annotated
    output["audit_claims_contradicted"] = [
        {"kind": d.kind, "claim": d.claim, "contradiction": d.contradiction} for d in defects
    ]
    # ...and write the verdict back to the stored answer. Setting this key and going no further is
    # what the previous shape did: the operator saw the annotation once, and the same answer stayed
    # in the candidate lane marked `valid` — a state written from a SHAPE check, so an audit whose
    # every load-bearing claim was false was stored valid at full trust and replayable from cache on
    # the next identical request.
    candidate_id = str(
        output.get("candidate_id")
        or (output.get("details") or {}).get("candidate_id")
        or context.get("candidate_id")
        or ""
    ).strip()
    if candidate_id:
        with suppress(Exception):  # the verdict is a record, never a reason to lose the answer
            from core.candidate_knowledge_lane import mark_candidate_claims_contradicted

            output["audit_verdict_recorded"] = mark_candidate_claims_contradicted(
                candidate_id, kinds=tuple(d.kind for d in defects)
            )
    return output

def enforce_final_action_honesty(
    result: dict[str, Any],
    *,
    user_input: str,
    effective_input: str | None = None,
    session_id: str | None = None,
    source_context: dict[str, object] | None = None,
) -> dict[str, Any]:
    output = dict(result or {})
    # Handle a leaked OpenClaw `NO_REPLY` silence token before any other finalization. If the whole
    # reply is just the token (bare, or trailed by an emoji/punctuation), replace it with an honest
    # fallback — OpenClaw does not reliably suppress it, so the user would otherwise see the raw token.
    # Otherwise, reveal a real answer hidden behind a NO_REPLY prefix line.
    _raw_response = str(output.get("response") or "")
    if is_bare_reply_control_token(_raw_response):
        output["response"] = _NO_REPLY_FALLBACK
        output["reply_control_sanitized"] = True
    else:
        revealed = reveal_reply_control_prefix(_raw_response)
        if revealed != _raw_response:
            output["response"] = revealed
            output["reply_control_sanitized"] = True
    output = _enforce_wallet_secret_safety(
        output, session_id=session_id, source_context=source_context
    )
    output = _enforce_active_mission_forbidden_terms(output, session_id=session_id)
    output = _suppress_leaked_tool_call(output)
    output = _enforce_inspection_claims(output, user_input=user_input, session_id=session_id)
    output = _apply_answer_binding(output, user_input=user_input, session_id=session_id)
    # Before the claim-shaped guards below, because those read `output["response"]` and must judge
    # the evidence-bound text rather than the wording the model shipped and the trace contradicts.
    output = _bind_runtime_claims_to_evidence(
        output, user_input=user_input, session_id=session_id, source_context=source_context
    )
    output = _verify_audit_claims_against_evidence(output, source_context=source_context)
    response = str(output.get("response") or "").strip()
    # A short fabricated "in progress / one moment" reply with no task actually running this turn
    # (the "audit underway after it already completed" failure). Answer from runtime state, not prose.
    if (
        response
        and len(response) < 220
        and _FALSE_PROGRESS_CLAIM_RE.search(response)
        and str(output.get("mode") or output.get("mode_override") or "").strip().lower() != "tool_executed"
        and not _current_turn_has_executed_receipt(session_id=session_id, source_context=source_context)
    ):
        output["response"] = _HONEST_NO_ACTIVE_TASK_RESPONSE
        output["confidence"] = min(float(output.get("confidence") or 1.0), 0.9)
        output["action_honesty_validator"] = {
            "applied": True,
            "reason": "fabricated_in_progress_no_active_task",
            "original_response_excerpt": response[:240],
        }
        output["route"] = str(output.get("route") or "action_honesty_final_validator")
        output["route_reason"] = "fabricated_progress_blocked"
        return output
    if _HONEST_DENIAL_RE.search(response):
        return output
    claim_kind = completion_claim_kind(response)
    if not claim_kind:
        return output
    # NOT gated on this turn's prompt. Whether "the files were deleted" is TRUE depends on whether a
    # tool actually ran -- not on what the user happened to type. The old gate required an action
    # verb in the CURRENT prompt, so the guard switched itself off on every follow-up: the identical
    # fabricated claim was blocked right after "delete the temp files" and passed one turn later on
    # "ok and what now?". The remaining gates below are the ones that decide truthfulness:
    # an explicit denial, mode=tool_executed, or a real executed receipt in this session.
    if str(output.get("mode") or output.get("mode_override") or "").strip().lower() == "tool_executed":
        return output
    if _current_turn_has_executed_receipt(session_id=session_id, source_context=source_context):
        return output

    output["response"] = {
        "build": _HONEST_NO_BUILD_RESPONSE,
        "persistence": _HONEST_NO_PERSISTENCE_RESPONSE,
    }.get(claim_kind, _HONEST_NO_EXECUTION_RESPONSE)
    output["confidence"] = min(float(output.get("confidence") or 1.0), 0.91)
    output["action_honesty_validator"] = {
        "applied": True,
        "reason": "no_executed_action_receipt",
        "claim_kind": claim_kind,
        "original_response_excerpt": response[:240],
    }
    output["route"] = str(output.get("route") or "action_honesty_final_validator")
    output["route_reason"] = "false_action_claim_blocked"
    output["web_calls"] = int(output.get("web_calls") or 0)
    return output


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_URL_REVIEW_INTENT_RE = re.compile(
    r"\b(?:check|review|read|look\s+at|open|score|rate|rank|analy[sz]e|summar(?:y|ize|ise)|evaluate|"
    r"assess|audit|any\s+good|is\s+it\s+(?:any\s+)?good|tell\s+me\s+(?:about|if)|what(?:'s|\s+is)\s+(?:on|in|at))\b",
    re.IGNORECASE,
)
# The refusal is right; the reason it gave was not. `web.fetch` works on this runtime -- driven
# directly it returns the page -- so "I can't browse arbitrary web pages in this build" denied a
# capability the product has, and sent the user off to paste files by hand. What is true is
# narrower and is what this now says: no fetch happened ON THIS TURN, so there is nothing to
# summarise. With the URL lane in the front door this should be reached only when a fetch was
# actually attempted and failed, or when the URL was mentioned rather than pointed at.
_UNFETCHED_URL_RESPONSE = (
    "You linked a URL and I did not open it on this turn, so I won't guess at what's on that page "
    "or score it. Ask me to fetch it and I'll read the page itself."
)
_UNFETCHED_URL_SENTINEL = "did not open it on this turn"
assert _UNFETCHED_URL_SENTINEL in _UNFETCHED_URL_RESPONSE.lower()


def response_describes_unfetched_url(*, user_input: str, response: str, fetch_attempts: int) -> bool:
    """Whether the model answered ABOUT a URL it never fetched. If a URL is named with a
    review/check/score intent and no web fetch happened this turn, any substantive answer is a
    fabrication -- honesty requires declining, not inventing the page's contents or a score."""
    if fetch_attempts > 0:
        return False
    text = str(user_input or "")
    if not _URL_RE.search(text) or not _URL_REVIEW_INTENT_RE.search(text):
        return False
    resp = str(response or "").strip()
    # The sentinel is a substring OF `_UNFETCHED_URL_RESPONSE`, so the guard recognises its own
    # output and does not judge its own decline a fabrication. It broke silently when the message
    # was reworded and the sentinel was not, which is why it is now derived from the message.
    return bool(resp) and _UNFETCHED_URL_SENTINEL not in resp.lower()


def enforce_url_grounding(result: dict[str, Any], *, user_input: str, fetch_attempts: int) -> dict[str, Any]:
    """Backstop: a URL named with review intent but never fetched must not yield an invented
    summary/score. Only touches model output -- deterministic/grounded replies are left alone."""
    output = dict(result or {})
    if output.get("deterministic") or output.get("response_control"):
        return output
    if not response_describes_unfetched_url(
        user_input=user_input, response=str(output.get("response") or ""), fetch_attempts=int(fetch_attempts or 0)
    ):
        return output
    original = str(output.get("response") or "")
    output["response"] = _UNFETCHED_URL_RESPONSE
    output["url_grounding_validator"] = {
        "applied": True,
        "reason": "url_named_but_not_fetched",
        "original_response_excerpt": original[:240],
    }
    output["route"] = "url_grounding_validator"
    output["route_reason"] = "unfetched_url_claim_blocked"
    return output


def _collect_executed_tools(
    session_id: str | None,
    source_context: dict[str, object] | None,
) -> list[dict[str, Any]]:
    """Ground-truth list of tools that actually ran on THIS turn, derived from the execution ledger.

    This is what the signed receipt puts its name to, so where it reads from decides whether the
    signature means anything. It used to ask `runtime_tool_receipts` -- a store only some execution
    paths write. The live-data lane writes none, so 128 real executions were invisible here and the
    ledger signed `executed_tools: []` over turns that had genuinely hit the network: 2,399 signed
    receipts, 2 of them non-empty, and zero on a day with 116 live-data executions. A receipt that
    reports no tools because it asked the wrong store is not a weaker receipt, it is a false one.

    It also read the last 8 receipts for the whole SESSION, so a turn could be signed as backed by a
    tool that ran several turns earlier -- evidence-shaped and attached to the wrong turn, which is
    worse than the empty list it was meant to avoid. `core.execution_truth` is turn-scoped and
    covers every path, so both failures go away at the source rather than being compensated for.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []

    def _add(receipt: object) -> None:
        if not isinstance(receipt, dict) or not _receipt_shows_execution(receipt):
            return
        tool = str(receipt.get("tool_name") or receipt.get("tool") or receipt.get("intent") or "")
        key = str(receipt.get("receipt_id") or receipt.get("receipt_key") or "")
        sig = (tool, key)
        if sig in seen:
            return
        seen.add(sig)
        out.append({"tool": tool, "status": str(receipt.get("status") or receipt.get("mode") or "executed"), "receipt_key": key})

    for receipt in list((source_context or {}).get("tool_receipts") or []):
        _add(receipt)
    clean = str(session_id or "").strip()
    try:
        from core.execution_truth import executed_tools, resolve_turn_key

        turn_key = resolve_turn_key(dict(source_context or {}), None)
        for entry in executed_tools(turn_key, session_id=clean):
            sig = (entry["tool"], entry["receipt_key"])
            if sig in seen:
                continue
            seen.add(sig)
            out.append(entry)
    except Exception:
        pass
    return out


def emit_turn_honesty_receipt(
    output: dict[str, Any],
    *,
    user_input: str = "",
    session_id: str | None = None,
    source_context: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Record a signed, offline-verifiable honesty receipt for a finalized turn.

    Binds the model's action claim, the ground-truth tool executions, and the honesty verdict
    into a hash-chained, Ed25519-signed receipt (see core.honesty_receipt). Best-effort and
    never raises; disabled by setting VOOL_HONESTY_RECEIPTS=0. Attaches a small telemetry stub
    to the returned result so a UI can surface + verify it.
    """
    result = dict(output or {})
    if str(os.environ.get("VOOL_HONESTY_RECEIPTS", "1")).strip() == "0":
        return result
    try:
        from core.honesty_receipt import (
            VERDICT_BLOCKED,
            VERDICT_CLEAN,
            VERDICT_CONTRADICTED,
            VERDICT_NO_CLAIM,
            VERDICT_UNBACKED,
            issue_honesty_receipt,
            latest_receipt_hash,
            list_honesty_receipts,
            record_honesty_receipt,
        )

        session = str(session_id or "").strip() or "default"
        response = str(result.get("response") or "")
        executed = _collect_executed_tools(session_id, source_context)
        blocked = bool((result.get("action_honesty_validator") or {}).get("applied"))
        evidence_binding = dict(result.get("evidence_binding") or {})
        contradicted = bool(evidence_binding.get("contradicted"))
        # Same predicate the blocker used. When these two disagree the ledger is worse than
        # useless: the fabricated build of 2026-07-29 was signed `no_action_claimed` because this
        # line asked a narrower question than the claim it was recording.
        claimed = bool(completion_claim_kind(response))
        mode_executed = str(result.get("mode") or result.get("mode_override") or "").strip().lower() == "tool_executed"
        if contradicted:
            # Ranked above `blocked` on purpose. A contradiction is a claim the trace DISPROVES,
            # which is strictly worse than a claim the trace merely fails to support, and the
            # ledger must not record the worse state under the milder name.
            verdict = VERDICT_CONTRADICTED
            claimed_actions = sorted(
                {
                    str(item.get("subject") or "runtime_fact")
                    for item in evidence_binding.get("findings") or []
                    if str(item.get("verdict") or "") == "contradicted"
                }
            ) or ["runtime_fact"]
            detail = "answer contradicted the durable execution trace; repaired to evidence-backed wording"
        elif blocked:
            verdict, claimed_actions, detail = VERDICT_BLOCKED, ["side_effecting_action"], "fabricated action claim blocked; no execution receipt"
        elif claimed and (executed or mode_executed):
            verdict, claimed_actions, detail = VERDICT_CLEAN, ["side_effecting_action"], "action claim backed by an executed tool receipt"
        elif claimed:
            # A claim, no execution, and no block. The ledger used to fold this into
            # `no_action_claimed`, so the one state worth auditing looked like the quietest one.
            verdict, claimed_actions, detail = VERDICT_UNBACKED, ["side_effecting_action"], "action claim recorded with no execution receipt behind it"
        else:
            verdict, claimed_actions, detail = VERDICT_NO_CLAIM, [], "no unbacked side-effecting action claim"

        receipt = issue_honesty_receipt(
            session_id=session,
            turn_index=len(list_honesty_receipts(session)),
            prompt_text=user_input,
            response_text=response,
            claimed_actions=claimed_actions,
            executed_tools=executed,
            verdict=verdict,
            verdict_detail=detail,
            prev_hash=latest_receipt_hash(session),
        )
        record_honesty_receipt(receipt)
        result["honesty_receipt"] = {
            "receipt_id": receipt.receipt_id,
            "content_hash": receipt.content_hash,
            "verdict": receipt.verdict,
            "signed": bool(receipt.signature),
            "signer_peer_id": receipt.signer_peer_id,
        }
    except Exception:
        pass
    return result


__all__ = ["completion_claim_kind", "emit_turn_honesty_receipt", "enforce_final_action_honesty"]
