from __future__ import annotations

import json
import os
import posixpath
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
    r"(?:(?:was|were|has\s+been|have\s+been)\s*)?"
    r"(?:deleted|created|edited|modified|written|saved|wiped|cleaned)|"
    r"(?:deleted|created|edited|modified|wrote|saved|wiped|cleaned)\s+"
    r"(?:the\s+)?(?:file|files|temp\.txt|workspace|wallet\s+key|wallet\s+keys)|"
    r"(?:tool|command)\s+(?:was\s+)?(?:executed|run)|"
    r"(?:executed|ran|used)\s+(?:the\s+)?(?:tool|command)|"
    r"(?:funds|sol|(?:crypto|wallet)\s+tokens?)\s+"
    r"(?:(?:were|was|have\s+been|has\s+been)\s*)?(?:sent|moved|transferred)|"
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
    r"|did\s+not\s+create\s+or\s+save\s+a\s+file"
    r"|nothing\s+was\s+generated\s+on\s+this\s+turn"
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
    r"scaffolds?|repos?|repositor(?:y|ies)|(?<![\w./-])[\w./-]+\.[A-Za-z]{2,5})"
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
# A completion claim about a DELIVERABLE FILE -- a document, deck, spreadsheet or PDF said to be
# made, saved, exported or attached. Measured live 2026-10-05 on main 9adff83: "give it to me as a
# Word document" was answered "Created a five-item packing checklist ... and saved it as a Word
# document." and "I want a PowerPoint file" with "Created a three-slide PowerPoint presentation
# ..." on turns where no tool ran -- and VOOL has no docx/pptx/xlsx writer at all. The build
# vocabulary above names files, scripts and tests, never a document, so both shipped. The object is
# a FORMAT (Word, PowerPoint, PDF, .xlsx ...) or a file-only noun (presentation, spreadsheet, slide
# deck, workbook); a bare "document" or "report" is left alone because an answer can BE one.
_DELIVERABLE_FORMAT = (
    r"(?:word|excel|power\s*point|keynote|google\s+(?:docs?|sheets?|slides?)|pdf|docx|xlsx|xls|pptx|"
    r"odt|ods|odp|rtf|epub)"
)
#: Text, data and archive files (measured live 2026-10-06, formats stack: "Created a comprehensive
#: moving-house checklist and saved it as a Markdown (.md) file for you." on a turn where no tool ran).
#: These name ordinary words too ("in plain text", "the JSON response"), so they count only as a
#: FILE: with a file noun, as a bare extension (".md"), or as a filename ("notes.md").
_DATA_FILE_FORMAT = r"(?:markdown|md|text|txt|plain[\s-]+text|csv|tsv|json|ya?ml|xml|html?|zip|tar(?:\.gz|ball)?|tgz)"
_DATA_FILE_EXTENSION = r"(?:md|markdown|txt|csv|tsv|json|ya?ml|xml|html?|zip|tar\.gz|tgz)"
_DATA_FILE_NOUN = r"(?:files?|documents?|docs?|archives?|exports?|versions?|copy|copies|bundles?)"
_DELIVERABLE_NOUN = (
    r"(?:documents?|docs?|files?|presentations?|decks?|slide\s*decks?|slides(?:hows?)?|spreadsheets?|"
    r"workbooks?|sheets?|reports?|exports?|versions?|copy|copies)"
)
#: A deliverable named as the SOURCE or SUBJECT ("a summary of the PowerPoint", "the PDF content")
#: is not a claim that one was produced.
_DELIVERABLE_NOT_PRODUCT = r"(?!\s+(?:content|contents|text|outline|summary|structure|layout|draft|notes|format)\b)"
_DELIVERABLE_OBJECT = (
    rf"(?:{_DELIVERABLE_FORMAT}\s+{_DELIVERABLE_NOUN}\b"
    # A bare format names the product only where the phrase ends ("saved it as a PDF.", "to Excel
    # and ..."); "the Excel formula" and "a PDF reader" are about something else.
    rf"|{_DELIVERABLE_FORMAT}\b(?=\s*(?:[.,;:!?)]|$)|\s+(?:and|so|which|that|for|with|in|to|now|here|"
    rf"is|are|was|were|has|have)\b)"
    rf"|(?:presentations?|(?:slide\s*)?decks?|spreadsheets?|workbooks?)\b"
    # "a Markdown (.md) file", "a CSV file", "a .txt file", "a .zip", "backup.zip"
    rf"|{_DATA_FILE_FORMAT}(?:\s*\(\.?{_DATA_FILE_EXTENSION}\))?\s+{_DATA_FILE_NOUN}\b|tarballs?\b)"
    rf"{_DELIVERABLE_NOT_PRODUCT}"
)
#: The object as a bare extension (".md") or a filename ("backup.zip"). Only after a production verb:
#: "Here's your config.yaml:" introduces content shown in the reply, not a file said to exist.
_DELIVERABLE_NAMED_FILE = rf"(?:(?<![\w.])\.{_DATA_FILE_EXTENSION}\b(?!\.\w)|(?<![\w~/./\\-])[\w~/./\\-]+(?:[ \t]+[\w~/./\\-]+){{0,5}}\.{_DATA_FILE_EXTENSION}\b(?!\.\w))"
_DELIVERABLE_VERB = (
    r"(?:created|generated|made|built|prepared|produced|saved|exported|converted|attached|wrote|written|"
    r"compiled|put\s+together|uploaded|rendered|packaged|zipped|compressed|archived|bundled)"
)
#: Words between the verb and the object that make the object a source rather than the product.
_DELIVERABLE_SOURCE_WORD = r"(?:of|from|about|on|with|by|based|describing|summari[sz]ing|explaining|using|for)"
_DELIVERABLE_CLAIM_RE = re.compile(
    rf"(?:"
    # "saved it as a Word document", "exported the budget to Excel", "created a three-slide PowerPoint presentation"
    # Not the adjective ("the attached notes.txt", "your exported data"): that names a file someone
    # else provided or the reply is talking about, not one this turn produced.
    rf"(?<!\bthe\s)(?<!\byour\s)(?<!\bmy\s)(?<!\bthis\s)(?<!\bthat\s)"
    rf"\b{_DELIVERABLE_VERB}(?:\s+(?!{_DELIVERABLE_SOURCE_WORD}\b)[\w'-]+){{0,6}}?\s+(?:{_DELIVERABLE_OBJECT}|{_DELIVERABLE_NAMED_FILE})"
    # "your Word document is ready", "the PDF has been generated"
    rf"|(?:\b{_DELIVERABLE_OBJECT}|{_DELIVERABLE_NAMED_FILE})\s+(?:is|are|was|were|has\s+(?:now\s+)?been|have\s+(?:now\s+)?been)\s+(?:(?:now|successfully)\s+)?"
    rf"(?:ready|created|generated|saved|exported|attached|available|done|complete|uploaded|made|prepared)"
    # "here is your PowerPoint file", "attached is the spreadsheet"
    rf"|\b(?:here(?:'s|\s+is|\s+are)|attached\s+(?:is|are))\s+(?:your|the)\s+(?:[\w-]+\s+){{0,2}}?{_DELIVERABLE_OBJECT}"
    rf")",
    re.IGNORECASE,
)
#: An offer, a plan, a refusal or the user's own action -- not a report that a file now exists.
_DELIVERABLE_NOT_DONE_RE = re.compile(
    r"\b(?:i'?ll|i\s+will|i\s+can|i\s+could|i\s+would|i'?d|let\s+me|want\s+me\s+to|shall\s+i|should\s+i|"
    r"if\s+you|you\s+can|you\s+could|you\s+(?:already\s+|just\s+)?(?:created|saved|exported|attached|uploaded|made)|"
    r"can'?t|cannot|couldn'?t|unable|not\s+able|didn'?t|did\s+not|haven'?t|have\s+not|hasn'?t|has\s+not|"
    r"isn'?t|is\s+not|wasn'?t|was\s+not|no\s+(?:file|document)|never)\b",
    re.IGNORECASE,
)

#: A PROMISE that a file is about to be made ("I will now generate the PDF with this summary.",
#: "I'll create this in an Excel file for you now."). Measured live the same day: the content came
#: back in the chat with that promise appended, on a turn where no tool ran and none was queued, so
#: the user waits for a file that never comes. An offer ("I can make it a PDF if you want") is honest
#: and stays.
_GENERIC_FILE_PROMISE_RE = re.compile(
    r"\b(?:i['’]?ll|i\s+will|let\s+me|i['’]?m\s+going\s+to|i\s+am\s+going\s+to)"
    r"\s+(?:now\s+)?(?:go\s+ahead\s+and\s+)?"
    r"(?:create|generate|make|build|prepare|produce|save|export|convert|compile|render|attach)"
    r"(?:\s+(?:the|a|an|one|both|all|each|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|those|these|your|new|other|\d+))*"
    r"\s+files?\b",
    re.IGNORECASE,
)
_DELIVERABLE_PROMISE_RE = re.compile(
    rf"(?:{_GENERIC_FILE_PROMISE_RE.pattern})|"
    rf"\b(?:(?:i['’]?ll|i\s+will|let\s+me|i['’]?m\s+going\s+to|i\s+am\s+going\s+to)\s+(?:now\s+)?(?:go\s+ahead\s+and\s+)?"
    rf"(?:create|generate|make|build|prepare|produce|save|export|convert|put\s+together|compile|render|attach|turn)"
    rf"|(?:now\s+)?(?:creating|generating|exporting|saving|converting|preparing|rendering)\s+(?:the|your|a|an|this)\b)"
    rf"[^.!?\n]*?(?:\b{_DELIVERABLE_OBJECT}|{_DELIVERABLE_NAMED_FILE})",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?:])\s+")
#: "If you want, I'll make it a PDF" waits for the user; it is an offer, not a promise of work underway.
_CONDITIONAL_OFFER_RE = re.compile(
    r"\b(?:if\s+you|if\s+that|would\s+you\s+like|do\s+you\s+want|want\s+me\s+to|shall\s+i|should\s+i|"
    r"just\s+(?:say|tell|let)|once\s+you|when\s+you)\b",
    re.IGNORECASE,
)
#: "I'll create the files next, then run the suite" lays out a plan for a later step; it does not
#: say a file is being made on this turn, so there is no waiting user to protect.
_DEFERRED_PLAN_RE = re.compile(r"\b(?:next|later|afterwards|after\s+(?:that|this))\b", re.IGNORECASE)
_UNBACKED_PROMISE_NOTE = "(I cannot verify that file from this turn's execution record.)"

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

_HONEST_NO_DELIVERABLE_RESPONSE = (
    "I cannot verify that file from this turn's execution record. "
    "I do not have matching evidence or a verified download to offer."
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


# A clause boundary. The dash separator's whitespace runs on both of its sides make the
# alternation's cost polynomial in a shape a static scan cannot rule out, so the splitter
# below walks the text once instead; the regex stays for the differential harness that
# pins the splitter to it.
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;:\n])\s+|,\s+|(?<![^\S\n])\s+[—-]\s+")
_SENTENCE_END_CHARS = frozenset(".!?;:\n")
_CLAUSE_DASH_CHARS = frozenset("—-")


def _split_clauses(text: str) -> list[str]:
    # Split on sentence AND clause boundaries (incl. ; : , —) so a trailing exempting clause
    # ("...; no need to do anything") cannot be glued to the claim it is supposed to (but does not)
    # negate.
    #
    # Exactly the separators the regex above finds: a maximal whitespace run after a sentence
    # ender; a comma with at least one whitespace after it (maximal run); and a maximal
    # whitespace run led by the text start or a non-newline-whitespace-free character (the
    # lookbehind), a single em dash or hyphen, and another maximal whitespace run.
    raw = str(text or "")
    parts: list[str] = []
    part_start = 0
    i = 0
    n = len(raw)
    while i < n:
        char = raw[i]
        end = -1
        if char.isspace() and i > 0 and raw[i - 1] in _SENTENCE_END_CHARS:
            end = i + 1
            while end < n and raw[end].isspace():
                end += 1
        elif char == ",":
            end = i + 1
            while end < n and raw[end].isspace():
                end += 1
            if end == i + 1:
                end = -1  # ",\s+" needs at least one whitespace after the comma
        elif char.isspace() and (i == 0 or not (raw[i - 1].isspace() and raw[i - 1] != "\n")):
            run_end = i + 1
            while run_end < n and raw[run_end].isspace():
                run_end += 1
            if (
                run_end < n
                and raw[run_end] in _CLAUSE_DASH_CHARS
                and run_end + 1 < n
                and raw[run_end + 1].isspace()
            ):
                end = run_end + 2
                while end < n and raw[end].isspace():
                    end += 1
        if end < 0:
            i += 1
            continue
        parts.append(raw[part_start:i])
        part_start = i = end
    parts.append(raw[part_start:])
    return parts


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


#: Verbs whose REQUESTED form asks this turn to write, change, remove, move or run something. Read
#: through `requested_action_clauses`, so a recall question ("how many times did I save ..."), a
#: past description or quoted text never counts as a request.
_REQUESTED_ACTION_VERB_RE = re.compile(
    r"(?:delete|remove|erase|wipe|clear|clean|purge|send|transfer|move|pay|create|make|write|save|"
    r"store|record|note|log|remember|add|put|edit|change|modify|update|rename|run|execute|install|"
    r"build|generate|copy|export|set\s+up)\b",
    re.IGNORECASE,
)

#: A stored-record noun followed by a bare storage participle, with no auxiliary: "the only note
#: saved", "the only file written to your log". Destructive and creative participles (deleted,
#: wiped, created, edited, ...) report an event even in this position and are never matched here.
_RECORD_STATE_PHRASE_RE = re.compile(r"(?:notes?|files?)\s+(?:saved|written)", re.IGNORECASE)

#: The participle phrase is the subject of a present-tense copula: "... note saved [to your log] is
#: the seed list". That is a description of what the record holds now, not a report of this turn.
_RECORD_STATE_PREDICATE_RE = re.compile(r"(?:\s+[A-Za-z'’-]+){0,6}?\s+(?:is|are)\b", re.IGNORECASE)


def request_asks_for_action(request_text: str) -> bool:
    """Whether the turn's request asks for something to be written, changed, removed or run."""

    from core.instructional_request import requested_action_clauses

    return bool(requested_action_clauses(str(request_text or ""), _REQUESTED_ACTION_VERB_RE))


def _describes_a_stored_record(sentence: str, match: re.Match[str]) -> bool:
    """The match is a noun phrase naming a stored record, the subject of a present copula."""

    return bool(
        _RECORD_STATE_PHRASE_RE.fullmatch(match.group(0))
        and _RECORD_STATE_PREDICATE_RE.match(sentence, match.end())
    )


def _file_claim_text(text: str) -> str:
    # Read displayed filenames, not Markdown's punctuation or link destination.
    text = re.sub(
        rf"\[([^\]\n]{{1,240}}\.{_DATA_FILE_EXTENSION})\]\([^\n)]*\)",
        r"\1", text, flags=re.I,
    )
    return re.sub(
        rf"(?:`|\*{{1,2}}|_{{1,2}}|[\"'])([^`*\"'\n]{{1,240}}\.{_DATA_FILE_EXTENSION})(?:`|\*{{1,2}}|_{{1,2}}|[\"'])",
        r"\1", text, flags=re.I,
    )


_FILE_COUNT_WORD_PATTERN = r"(?:one|both|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|dozen|\d+)"
_COMPLETED_FILE_COUNT_RE = re.compile(
    rf"\b(?:{_DELIVERABLE_VERB}\s+(?:(?:the|all)\s+)?(?:a\s+)?{_FILE_COUNT_WORD_PATTERN}\s+files?"
    rf"|{_FILE_COUNT_WORD_PATTERN}\s+files?\s+(?:are|were|have\s+been)\s+(?:saved|created|written|generated|ready))\b", re.I,
)
_FILE_LIST_HEADING_RE = re.compile(r"^\s*(?:files?\s+(?:created|saved|written|generated)|(?:created|saved|written|generated)\s+files?)\s*:\s*$", re.I)
_QUALIFIED_FILE_COMPLETION_START_RE = re.compile(
    rf"^\s*(?:(?:i|i've|i\s+have)\s+)?{_DELIVERABLE_VERB}\s+", re.I,
)


def _deliverable_claim_fragments(text: str) -> list[str]:
    normalized = _file_claim_text(text)
    fragments = []
    # A completion heading owns the immediately following list, not unrelated prose.
    listing: list[str] = []
    for line in [*normalized.splitlines(), ""]:
        if _FILE_LIST_HEADING_RE.match(line):
            if listing:
                fragments.append(", ".join(listing))
            listing = [line]
        elif listing and re.match(r"\s*(?:[-*+] |\d+[.)] )", line):
            listing.append(re.sub(r"^\s*(?:[-*+] |\d+[.)] )", "", line))
        elif listing:
            fragments.append(", ".join(listing))
            listing = []
    # Separate a disclosed failure from the successful clause it qualifies. Don't
    # split bare commas: they may join several outputs that all need evidence.
    for sentence in _split_sentences(normalized):
        for clause in re.split(r",\s*(?:but|however)\s+|;\s*", sentence, flags=re.I):
            match = _DELIVERABLE_CLAIM_RE.search(clause) or _COMPLETED_FILE_COUNT_RE.search(clause)
            if match is None:
                # Recognize a literal qualified target even when its directory is
                # too large for the bounded name parser. It must fail verification,
                # rather than disappear from the file-evidence obligation.
                start = _QUALIFIED_FILE_COMPLETION_START_RE.match(clause)
                filename = _SPECIFIC_DELIVERABLE_NAME_RE.search(clause) if start else None
                if start and filename:
                    target = clause[start.end():filename.end()]
                    before_directory = re.split(r"[/\\]", target, maxsplit=1)[0]
                    if ("/" in target or "\\" in target) and not re.search(
                            rf"\b{_DELIVERABLE_SOURCE_WORD}\b", before_directory, re.I):
                        match = start
            if match is None:
                continue
            if _DELIVERABLE_NOT_DONE_RE.search(clause[:match.end()]) or _NOT_A_SELF_CLAIM_RE.search(clause):
                continue
            fragments.append(clause)
    return fragments


def _build_claim_sentence(body: str) -> bool:
    """Whether some sentence reports this runtime's own finished build (files written, tests run)."""
    return any(
        _BUILD_CLAIM_RE.search(sentence)
        and not (_BUILD_CLAIM_NOT_SELF_RE.search(sentence) or _NOT_A_SELF_CLAIM_RE.search(sentence))
        for sentence in _split_sentences(body)
    )


def _only_a_file_count(fragment: str) -> bool:
    """A claim fragment that counts files ("created both files") and names no document or file.

    Inside a build report such a count is the build's own output: the build law, which reads the
    turn's execution record, decides it, as it did before deliverable claims existed. A named file
    or a document, deck, spreadsheet or PDF stays a deliverable claim."""
    return (
        _COMPLETED_FILE_COUNT_RE.search(fragment) is not None
        and _DELIVERABLE_CLAIM_RE.search(fragment) is None
        and _SPECIFIC_DELIVERABLE_NAME_RE.search(fragment) is None
    )


def completion_claim_kind(text: str, *, request_text: str | None = None) -> str:
    """What kind of finished work a reply claims: ``""``, ``"mutation"``, ``"build"`` or ``"persistence"``.

    One predicate for both consumers. The blocker uses it to decide whether a claim needs a
    receipt; `emit_turn_honesty_receipt` uses it to decide whether the turn made a claim at all.
    Splitting those two readings is what let a fabricated build be signed `no_action_claimed`.

    ``"mutation"`` wins ties: it is the older, narrower vocabulary (delete / wipe / send funds) and
    it carries the response the existing callers already expect.

    ``request_text`` is the turn's request. When it is given and asks for no action, a sentence
    that only DESCRIBES a stored record ("the only note saved is the seed list") is a memory answer,
    not a report that this turn saved anything. Measured on the original comparison: a correct
    "no record of that" recall answer was replaced by the no-execution notice because of exactly
    that phrase. Without ``request_text`` (or on any turn that requests an action) the law is
    unchanged, and a finite claim ("the files were deleted") is a claim on every turn -- the
    follow-up turn included.
    """

    body = str(text or "")
    if not body.strip():
        return ""
    if _HONEST_DENIAL_RE.search(body):
        # A denial of an action is not an action claim. Checked here as well as at the blocker,
        # because `emit_turn_honesty_receipt` calls this predicate directly and would otherwise
        # sign an honest denial as a claim needing a receipt.
        return ""
    describes_records = request_text is not None and not request_asks_for_action(request_text)
    for sentence in _split_sentences(body):
        mutation = _FALSE_ACTION_CLAIM_RE.search(sentence)
        while describes_records and mutation is not None and _describes_a_stored_record(sentence, mutation):
            mutation = _FALSE_ACTION_CLAIM_RE.search(sentence, mutation.end())
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
        return "deliverable" if _deliverable_claim_fragments(body) else "mutation"
    fragments = _deliverable_claim_fragments(body)
    if fragments and not (_build_claim_sentence(body) and all(map(_only_a_file_count, fragments))):
        return "deliverable"
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


#: Action words of a tool that CREATES or EXPORTS a file, read from the tool name's own segments
#: ("workspace.write_file" -> write, "file.deliver" -> deliver). A noun ("file", "pdf", "document")
#: is not an action: "workspace.read_file" and "pdf.extract_text" read a file and make nothing, so a
#: name that merely mentions a file can never back a claim that one was made.
_FILE_WRITE_ACTIONS = frozenset({
    "write", "save", "export", "generate", "render", "create", "convert", "attach", "deliver",
})
#: Segments that mark a tool as reading or inspecting, never producing, whatever else it names.
_FILE_READ_ACTIONS = frozenset({
    "read", "extract", "ocr", "list", "search", "inspect", "view", "open", "get", "fetch", "stat", "find",
    "grep", "glob", "preview", "parse", "summary", "summarize",
})
#: A receipt or fact that ran and failed, or was refused, is not a file.
_FAILED_STATUSES = frozenset({
    "failed", "failure", "error", "errored", "refused", "denied", "blocked", "rejected", "cancelled",
    "canceled", "timeout", "timed_out", "aborted", "skipped", "pending", "pending_approval",
})
#: Output formats a claim can name, grouped as the files that would satisfy it.
_DELIVERABLE_FAMILIES = (
    (re.compile(r"\b(?:word|docx?|google\s+docs?)\b", re.IGNORECASE), frozenset({"docx", "doc"})),
    (re.compile(r"\b(?:excel|xlsx|xls|spreadsheets?|workbooks?|google\s+sheets?)\b", re.IGNORECASE),
     frozenset({"xlsx", "xls", "csv", "tsv", "ods"})),
    (re.compile(r"\b(?:power\s*point|pptx|ppt|keynote|presentations?|(?:slide\s*)?decks?|slideshows?|google\s+slides?)\b",
                re.IGNORECASE), frozenset({"pptx", "ppt", "key", "odp"})),
    (re.compile(r"\bpdf\b", re.IGNORECASE), frozenset({"pdf"})),
    (re.compile(r"\bodt\b", re.IGNORECASE), frozenset({"odt"})),
    (re.compile(r"\bods\b", re.IGNORECASE), frozenset({"ods"})),
    (re.compile(r"\bodp\b", re.IGNORECASE), frozenset({"odp"})),
    (re.compile(r"\brtf\b", re.IGNORECASE), frozenset({"rtf"})),
    (re.compile(r"\bepub\b", re.IGNORECASE), frozenset({"epub"})),
    (re.compile(r"\bmarkdown\b|(?<![\w])\.?md\b", re.IGNORECASE), frozenset({"md", "markdown"})),
    (re.compile(r"\b(?:text|txt|plain[\s-]+text)\s+(?:files?|documents?)\b|\.txt\b", re.IGNORECASE), frozenset({"txt"})),
    (re.compile(r"\bcsv\b", re.IGNORECASE), frozenset({"csv"})),
    (re.compile(r"\btsv\b", re.IGNORECASE), frozenset({"tsv"})),
    (re.compile(r"\bjson\b", re.IGNORECASE), frozenset({"json"})),
    (re.compile(r"\bya?ml\b", re.IGNORECASE), frozenset({"yaml", "yml"})),
    (re.compile(r"\bxml\b", re.IGNORECASE), frozenset({"xml"})),
    (re.compile(r"\bhtml?\b", re.IGNORECASE), frozenset({"html", "htm"})),
    (re.compile(r"\bzip(?:ped)?\b", re.IGNORECASE), frozenset({"zip"})),
    (re.compile(r"\btar(?:\.gz|ball)?\b|\btgz\b", re.IGNORECASE), frozenset({"gz", "tgz", "tar"})),
)
_TOOL_SEGMENT_RE = re.compile(r"[a-z0-9]+")
_IDENTITY_KEYS = ("format", "filename", "file_name", "path", "file_path", "output_path", "target", "name", "media_type")


def _claimed_deliverable_families(matches: list[str]) -> list[frozenset[str]]:
    """The output formats the claimed (or promised) file would need, one family per format named."""
    families: list[frozenset[str]] = []
    for text in matches:
        for pattern, family in _DELIVERABLE_FAMILIES:
            if pattern.search(text) and family not in families:
                families.append(family)
    return families


def _receipt_succeeded(receipt: dict[str, Any]) -> bool:
    """Ran AND succeeded. ``executed`` alone is not success: a writer that ran and failed made nothing."""
    execution = receipt.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    if execution.get("ok") is False or receipt.get("ok") is False or receipt.get("success") is False:
        return False
    for value in (execution.get("status"), execution.get("outcome"), receipt.get("status"), receipt.get("outcome")):
        if str(value or "").strip().lower() in _FAILED_STATUSES:
            return False
    return _receipt_shows_execution(receipt)


def _is_file_writer(tool: str) -> bool:
    segments = set(_TOOL_SEGMENT_RE.findall(str(tool or "").lower()))
    return bool(segments & _FILE_WRITE_ACTIONS) and not (segments & _FILE_READ_ACTIONS)


def _output_formats(tool: str, *details: object) -> set[str]:
    """Every output format a writer's record names: a format field, a file extension, or a format in
    the tool's own name ("document.export_pdf")."""
    known = {fmt for _, family in _DELIVERABLE_FAMILIES for fmt in family}
    found = {segment for segment in _TOOL_SEGMENT_RE.findall(str(tool or "").lower()) if segment in known}

    def _scan(value: object, depth: int) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in _IDENTITY_KEYS and isinstance(inner, str):
                    text = inner.strip().lower()
                    tail = text.rsplit(".", 1)[-1] if "." in text else text
                    tail = tail.rsplit("/", 1)[-1]
                    if tail in known:
                        found.add(tail)
                elif depth < 3 and isinstance(inner, (dict, list)):
                    _scan(inner, depth + 1)
        elif isinstance(value, list) and depth < 3:
            for inner in value[:16]:
                _scan(inner, depth + 1)

    for detail in details:
        _scan(detail, 0)
    return found


def _turn_file_writes(*, session_id: str | None, source_context: dict[str, object] | None) -> list[set[str]]:
    """This turn's SUCCESSFUL file-creating executions, each as the output formats it is known to have
    made (empty when its record names none). Reads and failed or refused attempts are never here."""
    writes: list[set[str]] = []
    for receipt in list((source_context or {}).get("tool_receipts") or []):
        if not isinstance(receipt, dict) or not _receipt_succeeded(receipt):
            continue
        tool = str(receipt.get("tool_name") or receipt.get("tool") or receipt.get("intent") or "")
        if _is_file_writer(tool):
            writes.append(_output_formats(tool, receipt.get("observation"), receipt.get("details"),
                                          receipt.get("arguments"), receipt.get("execution")))
    try:
        from core.execution_truth import KIND_TOOL, facts_for_turn, resolve_turn_key

        turn_key = resolve_turn_key(dict(source_context or {}), None)
        for fact in facts_for_turn(turn_key, session_id=str(session_id or "").strip()):
            if not (fact.in_category(KIND_TOOL) and fact.ok) or str(fact.status or "").lower() in _FAILED_STATUSES:
                continue
            if _is_file_writer(fact.name):
                writes.append(_output_formats(fact.name, fact.detail))
    except Exception:
        pass
    return writes


_SPECIFIC_DELIVERABLE_NAME_RE = re.compile(
    rf"(?<![\w.])\.?[\w-]+(?:\.[\w-]+)*\.{_DATA_FILE_EXTENSION}\b(?!\.\w)",
    re.IGNORECASE,
)
_OUTPUT_FILE_KEYS = frozenset({"filename", "file_name", "path", "file_path", "output_path", "target"})
_FILE_COUNT_WORDS = {"one": 1, "both": 2, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                     "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "dozen": 12}


_PATH_CHAR_RE = re.compile(r"[\w~/.\\-]")


def _trailing_path_run(prefix: str) -> str:
    """The run of path characters that ends ``prefix``, found in one backward pass.

    A ``[...]+$`` search retries from every start inside a long run that a later character breaks,
    which is quadratic in the run's length; this walk reads each character once."""
    start = len(prefix)
    while start > 0 and _PATH_CHAR_RE.fullmatch(prefix[start - 1]):
        start -= 1
    return prefix[start:]


def _claimed_file_names(claims: list[str]) -> set[str] | None:
    names: set[str] = set()
    for text in claims:
        text = _file_claim_text(text)
        for match in _SPECIFIC_DELIVERABLE_NAME_RE.finditer(text):
            name = match.group(0)
            # A bounded preceding object span allows real filenames with spaces.
            # Do not accept an arbitrary suffix of a different written basename.
            prefix_start = max(0, match.start() - 240)
            prefix = text[prefix_start:match.start()]
            boundaries = list(re.finditer(rf"\b{_DELIVERABLE_VERB}\s+|\(\s*at\s+|[,;:]\s*|\b(?:and|into|as|to)\s+", prefix, re.I))
            # Once a directory begins, words within its components are literal.
            # A later separator is prose only after an earlier complete filename,
            # or in the runtime's explicit parenthesized location annotation.
            first_separator = re.search(r"[/\\]", prefix)
            if first_separator:
                boundaries = [boundary for boundary in boundaries
                              if boundary.start() < first_separator.start()
                              or boundary.group(0).lstrip().startswith("(")
                              or _SPECIFIC_DELIVERABLE_NAME_RE.search(prefix[:boundary.start()])]
            if boundaries:
                extra = prefix[boundaries[-1].end():]
                # "Created file ~/Desktop/a.txt": the noun names what the path is, not part of it.
                extra = re.sub(r"^(?:(?:the|a|an|your)\s+)?(?:(?:new\s+)?file\s+)?", "", extra, flags=re.I)
                if re.fullmatch(r"[\w ~/.-]{0,240}", extra):
                    name = extra + name
                elif "/" in extra or "\\" in extra:
                    return None  # A bounded parse cannot discard an unparsed directory.
            else:
                # Passive claims and completion lists may have no production verb.
                path_prefix = _trailing_path_run(prefix)
                if re.fullmatch(r"[\w ~/./\\-]{1,120}", prefix):
                    name = prefix + name
                elif path_prefix:
                    if (len(path_prefix) == len(prefix) and prefix_start > 0
                            and _PATH_CHAR_RE.fullmatch(text[prefix_start - 1])):
                        return None
                    if re.fullmatch(r"[\w ~/./\\-]+", prefix) and "/" in prefix and " " in prefix:
                        return None
                    name = path_prefix + name
            # "was created at /abs/dir/a.txt": the preposition that introduces a location is not part
            # of the path it introduces.
            name = name.strip()
            located = re.search(r"\b(?:at|in|into|to|under|inside)\s+(?=\S*[/\\])(\S+)$", name, flags=re.I)
            if " " in name and located:
                name = located.group(1)
            names.add(name.replace("\\", "/").strip())
    return names


def _file_claim_matches_output(claim: str, outputs: set[str], context: dict[str, object] | None) -> bool:
    """A named directory is part of the claim, not a disposable display prefix.

    A bare filename retains its display-name contract. Qualified paths require the
    same lexical path, or an absolute/relative mapping under the turn's existing
    workspace root. Do not infer a root from an unrelated output or the process cwd.
    """
    if "/" not in claim:
        return any(claim.casefold() == name.rsplit("/", 1)[-1].casefold() for name in outputs)
    root = str((context or {}).get("workspace_root") or (context or {}).get("workspace") or "")
    root = root.replace("\\", "/")

    def identity(name: str) -> str:
        normalized = posixpath.normpath(name.replace("\\", "/"))
        if posixpath.isabs(root) and not posixpath.isabs(normalized) and not normalized.startswith("~"):
            normalized = posixpath.normpath(posixpath.join(root, normalized))
        return normalized

    expected = identity(claim)
    return any(expected == identity(name) for name in outputs)


def _output_file_names(*details: object, identities_only: bool = False) -> set[str]:
    """Known output identities, retaining directories so two real paths count as two files."""
    names: set[str] = set()

    def scan(value: object, depth: int = 0) -> None:
        if isinstance(value, dict):
            if identities_only:
                for key in ("path", "file_path", "output_path", "target", "filename", "file_name", "name"):
                    inner = value.get(key)
                    if isinstance(inner, str) and inner and (key != "name" or "." in inner):
                        names.add(posixpath.normpath(inner.replace("\\", "/")))
                        # Several aliases in one observation describe one output, not several.
                        return
            for key, inner in value.items():
                if isinstance(inner, str) and (
                    key in _OUTPUT_FILE_KEYS or (key == "name" and "." in inner)
                ):
                    name = posixpath.normpath(inner.replace("\\", "/"))
                    if name:
                        names.add(name)
                elif depth < 3 and key not in {"arguments", "input", "content"}:
                    scan(inner, depth + 1)
        elif isinstance(value, list) and depth < 3:
            for item in value[:16]:
                scan(item, depth + 1)

    for detail in details:
        scan(detail)
    return names


def _turn_file_output_names(*, session_id: str | None, source_context: dict[str, object] | None,
                            identities_only: bool = False) -> set[str]:
    names: set[str] = set()
    for receipt in list((source_context or {}).get("tool_receipts") or []):
        if not isinstance(receipt, dict) or not _receipt_succeeded(receipt):
            continue
        tool = str(receipt.get("tool_name") or receipt.get("tool") or receipt.get("intent") or "")
        if _is_file_writer(tool):
            if identities_only:
                outputs = _output_file_names(receipt.get("observation"), identities_only=True)
                if not outputs:
                    outputs = _output_file_names(receipt.get("details"), identities_only=True)
                if not outputs and "write_file" in tool:
                    outputs = _output_file_names(receipt.get("arguments"), identities_only=True)
                names.update(outputs)
            else:
                names.update(_output_file_names(receipt.get("observation"), receipt.get("details")))
            if not identities_only and "write_file" in tool:
                names.update(_output_file_names(receipt.get("arguments")))
    try:
        from core.execution_truth import KIND_TOOL, facts_for_turn, resolve_turn_key

        turn_key = resolve_turn_key(dict(source_context or {}), None)
        for fact in facts_for_turn(turn_key, session_id=str(session_id or "").strip()):
            if fact.in_category(KIND_TOOL) and fact.ok and str(fact.status or "").lower() not in _FAILED_STATUSES:
                if _is_file_writer(fact.name):
                    names.update(_output_file_names(fact.detail, identities_only=identities_only))
    except Exception:
        pass
    return names


def _promised_file_count(claims: list[str]) -> int | None:
    required = 0
    for text in claims:
        match = _GENERIC_FILE_PROMISE_RE.search(text) or _COMPLETED_FILE_COUNT_RE.search(text)
        if match is None:
            continue
        body = match.group(0)
        count = re.search(
            rf"\b({_FILE_COUNT_WORD_PATTERN})(?:\s+(?:new|other|your|these|those)){{0,2}}\s+files?\b",
            body, re.IGNORECASE,
        )
        if count:
            token = count.group(1).lower()
            if token.isdigit():
                token = token.lstrip("0") or "0"
                # Such counts cannot be verified from bounded turn output metadata. Do not
                # feed arbitrary model text into an unbounded integer conversion.
                if len(token) > 18:
                    return None
                amount = int(token)
            else:
                amount = _FILE_COUNT_WORDS[token]
        else:
            amount = 2 if re.search(r"\bfiles\b", body, re.IGNORECASE) else 1
        required = max(required, amount)
    return required


def _turn_wrote_a_file(
    *,
    session_id: str | None,
    source_context: dict[str, object] | None,
    claimed: list[str] | None = None,
) -> bool:
    """Whether a tool that creates or exports a file SUCCEEDED on this turn, and made what was claimed.

    When the claim names formats (a Word document, a PDF), every one of them needs a successful
    writer whose record shows that format. A writer whose record names no output format cannot back
    a format-named claim: "Your Word document is ready" after a plain text write is still false.
    """
    writes = _turn_file_writes(session_id=session_id, source_context=source_context)
    if not writes:
        return False
    claims = list(claimed or [])
    families = _claimed_deliverable_families(claims)
    expected_names = _claimed_file_names(claims)
    if expected_names is None:
        return False
    minimum_count = _promised_file_count(claims)
    if minimum_count is None:
        return False
    if expected_names or minimum_count:
        identities = _turn_file_output_names(session_id=session_id, source_context=source_context,
                                             identities_only=True)
        if any(not _file_claim_matches_output(name, identities, source_context) for name in expected_names):
            return False
        if len(identities) < minimum_count:
            return False
    made = set().union(*writes)
    return all(family & made for family in families)


def _strip_unbacked_file_promises(
    output: dict[str, Any],
    *,
    session_id: str | None,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    """Drop sentences promising a file that no tool is making, keep the rest, and say so once.

    The answer's real content stays; only the promise of a document, deck, spreadsheet or PDF that
    this turn did not write is removed. A file-writing tool on this turn makes the promise true.
    """
    response = str(output.get("response") or "")
    if not response.strip() or not _DELIVERABLE_PROMISE_RE.search(response):
        return output
    promised = [sentence for sentence in _split_sentences(_file_claim_text(response)) if _DELIVERABLE_PROMISE_RE.search(sentence)]
    if _turn_wrote_a_file(session_id=session_id, source_context=source_context, claimed=promised):
        return output
    kept_lines: list[str] = []
    removed: list[str] = []
    for line in response.split("\n"):
        sentences = _SENTENCE_SPLIT_RE.split(line)
        survivors = []
        for sentence in sentences:
            if (_DELIVERABLE_PROMISE_RE.search(sentence) and not _CONDITIONAL_OFFER_RE.search(sentence)
                    and not _DEFERRED_PLAN_RE.search(sentence)):
                removed.append(sentence.strip())
                continue
            survivors.append(sentence)
        if removed and line.strip() and not "".join(survivors).strip():
            continue  # the whole line was the promise
        kept_lines.append(" ".join(part for part in survivors if part) if len(survivors) != len(sentences) else line)
    if not removed:
        return output
    body = "\n".join(kept_lines).strip()
    guarded = dict(output)
    guarded["response"] = f"{body}\n\n{_UNBACKED_PROMISE_NOTE}" if body else _HONEST_NO_DELIVERABLE_RESPONSE
    guarded["action_honesty_validator"] = {
        "applied": True,
        "reason": "unbacked_file_promise_removed",
        "removed": removed[:4],
    }
    return guarded


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


def _receipt_shows_execution(
    receipt: dict[str, Any],
    *,
    session_id: str = "",
    source_context: dict[str, object] | None = None,
) -> bool:
    execution = receipt.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    if "action_record" in execution:
        # The terminal-event producer stores the typed record in this wrapper.
        # Its success fields are authoritative only after the existing durable
        # receipt verifier has checked hashes and chat/project scope. An invalid
        # typed record must never fall back to a legacy success flag.
        context = source_context or {}
        clean_session = str(session_id or "").strip()
        record = execution.get("action_record")
        if (
            not clean_session
            or not isinstance(record, dict)
            or str(receipt.get("session_id") or "").strip() != clean_session
            or str(context.get("runtime_session_id") or context.get("session_id") or clean_session).strip()
            != clean_session
            or execution.get("executed") is False
        ):
            return False
        try:
            from core.active_context_capsule import _verified_action_record

            verified = _verified_action_record(
                str(receipt.get("receipt_key") or "").strip(),
                chat_id=str(context.get("chat_id") or clean_session).strip(),
                project_id=str(context.get("_trusted_project_id") or context.get("project_id") or "").strip(),
                runtime_session_id=clean_session,
            )
        except Exception:
            return False
        if verified is None or verified != record:
            return False
        result = verified["result"]
        failure = verified.get("failure")
        return (
            verified["action_type"] == "tool_executed"
            and result.get("outcome") == "succeeded"
            and result.get("ok") is True
            and result.get("executed") is not False
            and isinstance(failure, dict)
            and failure.get("failed") is False
        )
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
# A filename or path the sentence says was inspected. Narrow on purpose: a bare word is not a
# target, and a claim with no object ("I had a look") is not checkable. Only a token whose
# extension is one a file actually has counts as a claimed target. Driven live 2026-08-01: an
# audit report quoting `blob.startswith` was parsed as a claimed file `blob.starts`, no execution
# record existed for that "file", and the ENTIRE report was replaced with "I did not actually
# open `blob.starts`". Dotted CODE expressions are everywhere in a report that quotes the code it
# audited; per this gate's own contract — fail open in every ambiguous direction — an unknown
# extension is ambiguity, not a claim.
#
# Both the extension list and the candidate extraction live in
# `core/agent_runtime/file_target_contract.py`. The extraction is the shared linear scanner (the
# same candidates the historical pattern captured, without its quadratic suffix rescans), so this
# gate and the answer binder can never drift on what counts as a named file.
from core.agent_runtime.file_target_contract import REAL_FILE_EXTENSIONS as _REAL_FILE_EXTENSIONS
from core.agent_runtime.file_target_contract import iter_claimed_file_candidates


def _claimed_inspection_targets(response: str, user_input: str) -> list[str]:
    """Files the reply says it inspected. The user's own naming counts — that is the whole point.

    The answer binder deliberately EXEMPTS filenames the user typed, so that "did you find
    notes.md?" does not flag every reply. That exemption is right for claims about CONTENT and
    exactly wrong here: the file the user named is the file whose inspection is being claimed, and
    the one whose absence was the bug.
    """

    def _files(text: str) -> list[str]:
        found: list[str] = []
        for candidate in iter_claimed_file_candidates(str(text or "")):
            stem, _, extension = candidate.rpartition(".")
            if not stem or extension.lower() not in _REAL_FILE_EXTENSIONS:
                continue
            if candidate not in found:
                found.append(candidate)
        return found

    # A reply that names its own files claims exactly those: a file the user listed and the reply never
    # mentions is not claimed to have been inspected (measured on the live agent-team comparison, 2026-10-07:
    # a reply reporting on orders.py was replaced by "I did not actually open `pricing.py`" because the user
    # had named three files). A reply that names none ("the audit ran -- it inspected the whole workspace")
    # claims the files the user named.
    return _files(response) or _files(user_input)


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


def _location_answer_has_verified_past_receipt(
    output: dict[str, Any],
    *,
    session_id: str | None,
    source_context: dict[str, object] | None,
) -> bool:
    """Whether an answer about where an EARLIER write went is backed by that write's verified record.

    "Where did that file go?" is answered from the record of a write an earlier turn made, so this
    turn's own receipts never hold it, and the turn-scoped check blocked a true answer with "I did
    not create those files". Two shapes may read past turns: the receipt-location render itself, or
    an answer that names the very file a verified record wrote. Only typed action records that the
    durable verifier accepts count (hashes, chat and project scope); legacy success flags from earlier
    turns still authorize nothing, and an answer naming no recorded file is judged on this turn alone.
    """
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return False
    route = " ".join(str(output.get(key) or "") for key in ("route", "route_reason", "reason"))
    response = str(output.get("response") or "")
    try:
        from core.runtime_continuity import list_runtime_tool_receipts

        verified = [
            receipt
            for receipt in list_runtime_tool_receipts(clean_session, limit=8)
            if isinstance(receipt, dict)
            and isinstance(dict(receipt.get("execution") or {}).get("action_record"), dict)
            and _receipt_shows_execution(receipt, session_id=clean_session, source_context=source_context)
        ]
    except Exception:
        return False
    if not verified:
        return False
    if "action_receipt_location" in route:
        return True
    # A path the answer qualifies with a directory names that path, not any file of the same name:
    # "I saved other/a.md" is not backed by a record that wrote reports/a.md.
    qualified_claims = {
        claim for claim in (_claimed_file_names(_deliverable_claim_fragments(response)) or set()) if "/" in claim
    }
    for receipt in verified:
        written = str(dict(receipt.get("arguments") or {}).get("path") or "").strip()
        name = written.replace("\\", "/").rsplit("/", 1)[-1]
        if name and name in response:
            same_name = {claim for claim in qualified_claims if claim.rsplit("/", 1)[-1] == name}
            if same_name and not any(
                _file_claim_matches_output(claim, {written}, source_context) for claim in same_name
            ):
                continue
            return True
    return False


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


def _enforce_user_rule_words(
    output: dict[str, Any],
    *,
    session_id: str | None,
    source_context: dict[str, object] | None,
) -> dict[str, Any]:
    """Mask every word the user's rules in force here ban from replies (core.user_rules), and say
    which exception let a word through that a broader rule would have masked.

    The rules lane's own answer is exempt: it quotes the user's rules back to them.
    """
    response = str(output.get("response") or "")
    if not response.strip() or not str(session_id or "").strip():
        return output
    try:
        from core.user_rules import (
            RULE_NOTE_RE,
            override_note,
            review_reply,
            scope_keys_for,
            turn_answered_rules_command,
        )

        if turn_answered_rules_command(session_id, source_context):
            return output
        keys = scope_keys_for(source_context) if isinstance(source_context, dict) and (
            source_context.get("chat_id") or source_context.get("session_id")
        ) else scope_keys_for(None, chat_id=str(session_id))
        # The sealed reply is guarded again verify-only (K-08), so this pass must be idempotent:
        # a reply that already carries this guard's note and has nothing left to mask is final.
        existing = RULE_NOTE_RE.search(response)
        body = response[: existing.start()] if existing else response
        review = review_reply(body, chat_id=keys["chat"], project_id=keys["project"])
    except Exception:
        return output
    if existing is not None and not review.hits:
        return output
    if not review.hits and not review.overrides:
        return output
    guarded = dict(output)
    notes: list[str] = []
    if review.hits:
        rule_nos = sorted({hit.rule.rule_no for hit in review.hits})
        named = ("rule " if len(rule_nos) == 1 else "rules ") + ", ".join(str(n) for n in rule_nos)
        notes.append(f"Your {named} blocked {len(review.hits)} word{'s' if len(review.hits) != 1 else ''} here.")
    if review.overrides:
        notes.append(override_note(review.overrides))
    guarded["response"] = f"{review.text}\n\n(" + " ".join(notes) + ")"
    guarded["user_rule_validator"] = {
        "applied": True,
        "rules": sorted({hit.rule.rule_no for hit in review.hits}),
        "terms": sorted({hit.term for hit in review.hits}),
        "exceptions": sorted({lift.exception.rule_no for lift in review.overrides}),
    }
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

def _rules_lane_owns_forget(
    user_input: str, *, session_id: str | None, source_context: dict[str, object] | None
) -> bool:
    """"Forget the rule about X" is a rules command, not a memory forget: the rules lane answered it from
    the rules store and filed its own turn fact. Only a whole-turn rules removal that names no second
    forget qualifies; anything else still needs a verified memory forget outcome."""
    try:
        from core.user_rules import rules_command_owns_forget, turn_answered_rules_command

        return rules_command_owns_forget(user_input) and turn_answered_rules_command(session_id, source_context)
    except Exception:
        return False


def enforce_final_action_honesty(
    result: dict[str, Any],
    *,
    user_input: str,
    effective_input: str | None = None,
    session_id: str | None = None,
    source_context: dict[str, object] | None = None,
) -> dict[str, Any]:
    output = dict(result or {})
    from core.memory.command_result import requires_forget_outcome, verified_forget_outcome

    command_outcome = verified_forget_outcome(
        output, user_input=user_input, effective_input=effective_input,
        session_id=session_id, source_context=source_context,
    )
    if command_outcome is not None:
        original = str(output.get("response") or "")
        output["response"] = command_outcome.response
        output["_memory_command_outcome"] = command_outcome.outcome
        if original != command_outcome.response:
            output["action_honesty_validator"] = {
                "applied": True,
                "reason": "forget_reply_differed_from_execution",
                "claim_kind": "forget",
                "original_response_excerpt": original[:240],
            }
        return output
    if requires_forget_outcome(
        output, user_input=user_input, effective_input=effective_input,
        source_context=source_context,
    ) and not _rules_lane_owns_forget(user_input, session_id=session_id, source_context=source_context):
        output["response"] = (
            "Forget could not be verified on this turn. Please retry the forget."
        )
        output["action_honesty_validator"] = {
            "applied": True,
            "reason": "forget_execution_outcome_missing",
            "claim_kind": "forget",
            "original_response_excerpt": str(result.get("response") or "")[:240],
        }
        output["route_reason"] = "false_action_claim_blocked"
        return output
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
    output = _enforce_user_rule_words(output, session_id=session_id, source_context=source_context)
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
    claim_kind = _final_claim_kind(
        output, user_input=user_input, effective_input=effective_input,
        session_id=session_id, source_context=source_context,
    )
    # A short fabricated "in progress / one moment" reply with no task actually running this turn
    # (the "audit underway after it already completed" failure). Answer from runtime state, not prose.
    if (
        claim_kind == "progress"
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
    if not claim_kind:
        return _strip_unbacked_file_promises(output, session_id=session_id, source_context=source_context)
    # NOT gated on this turn's prompt. Whether "the files were deleted" is TRUE depends on whether a
    # tool actually ran -- not on what the user happened to type. The old gate required an action
    # verb in the CURRENT prompt, so the guard switched itself off on every follow-up: the identical
    # fabricated claim was blocked right after "delete the temp files" and passed one turn later on
    # "ok and what now?". The remaining gates below are the ones that decide truthfulness:
    # an explicit denial, mode=tool_executed, or a real executed receipt in this session.
    if claim_kind == "deliverable":
        # A file said to exist needs a tool that WROTE one on this turn. Any other receipt (a web
        # lookup, a memory read) or a tool-flavoured mode is not evidence that a document exists.
        claimed = _deliverable_claim_fragments(response)
        if _turn_wrote_a_file(session_id=session_id, source_context=source_context, claimed=claimed):
            return output
    elif (
        str(output.get("mode") or output.get("mode_override") or "").strip().lower() == "tool_executed"
        or _current_turn_has_executed_receipt(session_id=session_id, source_context=source_context)
    ):
        # A build report that counts or names its files ("created 3 files") is held to what this
        # turn wrote once the turn has file writes to compare it with: one written file does not
        # back three.
        claimed = _deliverable_claim_fragments(response) if claim_kind == "build" else []
        if not (claimed and _turn_file_writes(session_id=session_id, source_context=source_context)):
            return output
        if _turn_wrote_a_file(session_id=session_id, source_context=source_context, claimed=claimed):
            return output
        claim_kind = "deliverable"
    if _location_answer_has_verified_past_receipt(output, session_id=session_id, source_context=source_context):
        return output

    output["response"] = {
        "deliverable": _HONEST_NO_DELIVERABLE_RESPONSE,
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


def _final_claim_kind(
    result: dict[str, Any], *, user_input: str, effective_input: str | None = None,
    session_id: str | None = None, source_context: dict[str, object] | None = None,
) -> str:
    """Classify the exact delivered result identically for the guard and signed receipt."""
    from core.memory.command_result import requires_forget_outcome, verified_forget_outcome

    outcome = verified_forget_outcome(
        result, user_input=user_input, effective_input=effective_input,
        session_id=session_id, source_context=source_context,
    )
    if outcome is not None and str(result.get("response") or "") == outcome.response:
        return ""
    if requires_forget_outcome(
        result, user_input=user_input, effective_input=effective_input,
        source_context=source_context,
    ) and not _rules_lane_owns_forget(user_input, session_id=session_id, source_context=source_context):
        return "forget"
    response = str(result.get("response") or "").strip()
    if response and len(response) < 220 and _FALSE_PROGRESS_CLAIM_RE.search(response):
        return "progress"
    if _HONEST_DENIAL_RE.search(response):
        return ""
    return completion_claim_kind(response, request_text=f"{user_input or ''}\n{effective_input or ''}")


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
        if not isinstance(receipt, dict) or not _receipt_shows_execution(
            receipt, session_id=str(session_id or "").strip(), source_context=source_context
        ):
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
    effective_input: str | None = None,
    session_id: str | None = None,
    source_context: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Record a signed, offline-verifiable honesty receipt for a finalized turn.

    Binds the model's action claim, the ground-truth tool executions, and the honesty verdict
    into a hash-chained, Ed25519-signed receipt (see core.honesty_receipt). A failed
    write retries once and immediately reconstructs the same issued receipt.
    Never raises or changes the reply; disabled by VOOL_HONESTY_RECEIPTS=0.
    A telemetry stub is attached only after durable persistence is verified.
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
            rebuild_honesty_receipt,
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
        claim_kind = _final_claim_kind(
            result, user_input=user_input, effective_input=effective_input,
            session_id=session_id, source_context=source_context,
        )
        claimed = bool(claim_kind)
        from core.memory.command_result import verified_forget_outcome

        command_outcome = verified_forget_outcome(
            result, user_input=user_input, effective_input=effective_input,
            session_id=session_id, source_context=source_context,
        )
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
        elif claim_kind == "forget":
            verdict, claimed_actions, detail = (
                VERDICT_UNBACKED, ["memory_forget"],
                "forget reply has no verified outcome from this turn's memory executor",
            )
        elif command_outcome is not None:
            changed = command_outcome.status in {"applied", "partial"}
            verdict = VERDICT_CLEAN if changed else VERDICT_NO_CLAIM
            claimed_actions = ["memory_forget"] if changed else []
            detail = "verified memory forget outcome: " + command_outcome.status
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
        prior_pending = bool(result.get("receipt_pending"))
        result.pop("honesty_receipt", None)
        written = record_honesty_receipt(receipt)
        if written.status == "failed":
            result.setdefault("index_failures", []).append("honesty_receipt_write_error")
            result["receipt_pending"] = True
            written = record_honesty_receipt(receipt)
            result["receipt_rebuild_attempted"] = True
            rebuilt = rebuild_honesty_receipt(receipt)
            # A successful retry also receives a final durable re-read in rebuild.
            written = rebuilt
        result["receipt_write_status"] = written.status
        result["receipt_pending"] = prior_pending or written.status == "failed"
        if written.status in {"written", "already_exists"} and written.receipt is not None:
            result["honesty_receipt"] = {
                "receipt_id": receipt.receipt_id,
                "content_hash": receipt.content_hash,
                "verdict": receipt.verdict,
                "signed": bool(receipt.signature),
                "signer_peer_id": receipt.signer_peer_id,
            }
            if command_outcome is not None:
                result["honesty_receipt"]["memory_command_status"] = command_outcome.status
        else:
            result["receipt_pending_id"] = receipt.receipt_id
            result["receipt_failure_reason"] = written.reason
    except Exception as exc:
        result.pop("honesty_receipt", None)
        result["receipt_write_status"] = "failed"
        result["receipt_pending"] = True
        result["receipt_failure_reason"] = type(exc).__name__
    return result


__all__ = ["completion_claim_kind", "emit_turn_honesty_receipt", "enforce_final_action_honesty"]
