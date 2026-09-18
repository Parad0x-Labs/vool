"""One definition of "this turn does not contain a request", for the whole runtime.

A turn with nothing in it used to travel the entire front door -- it stamped an access policy,
recorded a dialogue turn, created a resumable runtime checkpoint and built a task -- and then died
in `TaskEnvelopeV1.__post_init__` with `ValueError: goal is required`. The envelope's goal is the
request text stripped (`core/task_router.py`, `build_task_envelope_for_request`), and a
whitespace-only turn strips down to nothing. No caller between `run_once` and the envelope caught
it, so the CLI printed a traceback and `process_channel_request` raised out of a channel delivery.

Two questions, kept separate on purpose
---------------------------------------

*Does this turn's TEXT carry anything?* -- `text_carries_no_request`. Conservative and
Unicode-aware; see "what counts as invisible" below.

*Does this TURN carry a REQUEST?* -- `turn_has_no_request`, which asks
`core/agent_runtime/request_authority.py` what in this turn is allowed to become a command. That
module draws the line by PROVENANCE, and it is the harder half of the problem: an attachment's
caption, extracted text, OCR or transcript is something the user SHOWED the agent, not something
the user ASKED it to do. A PDF reading "delete all files" is data. Read its authority docstring
before touching either predicate.

Today the answer is that only the turn's own visible text carries command authority, because
nothing in this repository's transports can prove an attachment was the sender's own utterance. So
a turn with no visible text has no request in it, whatever it is carrying, and it gets the reply
below -- which names what arrived rather than claiming the message was blank.

No goal is ever invented
------------------------

Nothing here summarises an attachment, describes it, or manufactures a goal so the envelope guard
will pass -- a goal the runtime wrote is a false record of what was asked, and `TaskEnvelopeV1`'s
guard is right to reject an empty one. That invariant is untouched: a turn that carries no request
is stopped BEFORE the envelope instead of the envelope being weakened.

Punctuation and emoji are not empty
-----------------------------------

"?" and a thumbs-up are things a person says and a model can answer, so they keep the ordinary path
-- routing them to a fixed reply would be phrase matching standing in for a model decision, which
section 2 of CLAUDE.md prohibits. Measured here before the fix: both already returned a normal
result, and only the invisible cases raised.

What counts as invisible is text with no character a reader could see. That is wider than
`str.strip()`, which removes Unicode whitespace but leaves the invisible formatting characters
behind. Zero-width space and joiner, the BOM, the bidi marks and the C0/C1 controls are all
category Cc/Cf and all survive `strip()`. Measured before the fix, a turn of two U+200B ZERO WIDTH
SPACEs did not raise -- it normalized to U+200B, space, U+200B, reached the envelope with that as
its goal, cleared the guard, and spent a whole model turn on a request nobody made.

Not treated as invisible, on purpose: a character that is blank-looking but carries a real
category, for instance U+3164 HANGUL FILLER (category Lo) or U+2800 BRAILLE PATTERN BLANK (So).
Those are ordinary codepoints someone typed; they route normally and cannot crash, because they
leave a non-empty goal.

Where the surrounding surfaces stand
------------------------------------

Every product surface in front of this one is free to keep its own, narrower validation, and
several justifiably do -- this gate is the runtime's floor, not a mandate to make them uniform:

* accepts an empty turn, relies on this gate -- `VoolAgent.run_once` itself and
  `core/channel_gateway.py:process_channel_request` (no check of its own; it subscripts
  `result["response"]`, so a raise here becomes a raise inside a channel delivery);
* transport validation boundary, justified -- `/api/chat` and `/v1/chat/completions` 400 with "no
  user message found" on a falsy `.strip()` (`core/web/api/service.py:2813`, via
  `extract_user_message` at `core/web/api/runtime.py:888`), `/api/generate` 400s with "no prompt"
  (`service.py:3102`), the chat queue 400s on an empty enqueue (`service.py:1601`), the Telegram
  bridge skips a message with no text (`relay/bridge_workers/telegram_bridge.py:88`), and the
  Discord bridge's `_extract_command_text` returns `None` for a bare prefix
  (`relay/bridge_workers/discord_bridge.py:274`). A malformed body should be refused at the edge
  rather than spend a turn;
* UI prevention boundary, justified -- the web composer's `send()` returns early on empty input
  (`core/vool_chat_page.py:1000` and `:3447`), and both CLI front ends do the same before they
  ever call `run_once`: the one-shot `--input` (`apps/vool_agent.py:2013`) and the chat REPL's
  bare-Enter line (`apps/vool_chat.py:134`). A composer and a prompt should not post blanks.

Those boundaries are narrower than this one, which is why this one still has work to do: the HTTP
400 tests `.strip()`, so a message of two zero-width spaces passes it and arrives here. Do not
"fix" a surface into agreement with this module -- fix a surface only if it CRASHES.
"""

from __future__ import annotations

from typing import Any

from core.agent_runtime.request_authority import (
    bounded_evidence,
    text_carries_no_request,
    turn_command_text,
    visible_request_text,
)

__all__ = [
    "describe_turn_attachments",
    "empty_turn_reply",
    "resumed_empty_turn_reply",
    "text_carries_no_request",
    "turn_command_text",
    "turn_has_no_request",
    "visible_request_text",
]


def turn_has_no_request(text: Any, source_context: dict[str, object] | None = None) -> bool:
    """True when nothing in this turn is allowed to become a command.

    Not "nothing arrived" -- a turn can arrive carrying a great deal and still contain no request.
    What it means is that no part of the turn holds user instruction authority, which
    `core/agent_runtime/request_authority.py` decides.
    """
    return not turn_command_text(text, source_context)


# The CLOSED set of attachment categories this application recognises, mapped to phrases the
# application owns. Not a grammar: a grammar admits any plausible-looking string, and `kind` is
# remote input, so "system message", "verified document", "trusted attachment" and "rm-rf" all read
# as legitimate categories and were rendered into the runtime's own prose as though the runtime had
# said them. Sanitising the characters does not help when the ATTACK IS THE WORD.
#
# Membership is the whole rule. A category that is not a member renders `attachment`, whatever it is
# called and however convincing it looks. These are the kinds shipped code actually produces --
# `_infer_kind_from_url` emits social_post / image / video / text, and a client may legitimately
# label audio, document or file.
_ATTACHMENT_NOUNS: dict[str, str] = {
    "image": "image",
    "audio": "audio file",
    "video": "video",
    "document": "document",
    "file": "file",
    "text": "file",
    "social_post": "post",
}
_NEUTRAL_NOUN = "attachment"


def _display_noun(kind: object) -> str:
    """The application's own noun for a category, or the neutral one for anything else.

    No client string reaches the reply. The previous version matched a character grammar, which
    stopped NUL and ANSI escapes but happily printed "a system message" for an attachment whose
    sender chose to call it that. Only the LABEL is decided here -- filenames, references and
    evidence payloads are untouched.
    """
    return _ATTACHMENT_NOUNS.get(str(kind or "").strip().lower(), _NEUTRAL_NOUN)


def describe_turn_attachments(source_context: dict[str, object] | None) -> str:
    """What arrived, in the application's own words -- "an image", "2 attachments (image, video)".

    It describes; it never promotes. Nothing here can become a request, and the description is not
    shown to a model as one.

    Bounded before anything is inspected, from the canonical view, so a fifty-thousand-attachment
    payload costs `TURN_EVIDENCE_ITEMS_MAX` reads and no more.

    The count is stated as a total ONLY when the source is known to have ended inside the budget.
    Sixty-three recognised items out of a fully spent budget is not sixty-three attachments -- the
    sixty-fourth was malformed and the two-hundredth was never looked at -- so that case says "at
    least". Nothing inspects item sixty-five to improve the wording.
    """
    evidence = bounded_evidence((source_context or {}).get("external_evidence"))
    recognized = evidence.recognized
    if not recognized:
        return ""
    from core.media_ingestion import media_kind_for_reference

    nouns: list[str] = []
    for item in recognized:
        kind = str(item.get("kind") or "").strip().lower()
        if not kind:
            reference = str(item.get("url") or item.get("path") or item.get("reference") or "")
            kind = media_kind_for_reference(reference)
        nouns.append(_display_noun(kind))
    if len(recognized) == 1 and evidence.count_is_exact:
        article = "an" if nouns[0][:1] in {"a", "e", "i", "o", "u"} else "a"
        return f"{article} {nouns[0]}"
    counted = str(len(recognized)) if evidence.count_is_exact else f"at least {len(recognized)}"
    # No length cut here any more, and none is needed: every noun comes from the closed set above,
    # so the sentence is bounded by construction rather than by a cap that could never be exceeded.
    return f"{counted} attachments ({', '.join(sorted(set(nouns)))})"


def turn_carries_retained_document(source_context: dict[str, object] | None) -> bool:
    """True when the turn's evidence includes a pasted DOCUMENT, whose bytes the chat keeps.

    Read from the bounded canonical view, like `describe_turn_attachments`, so the answer costs
    the same bounded reads and never trusts a client field: the `document` flag is set by the
    attachment authority on items it produced, and the door strips client-forged attachment items.
    """
    evidence = bounded_evidence((source_context or {}).get("external_evidence"))
    return any(bool(item.get("document")) and item.get("attachment_id") for item in evidence.recognized)


def empty_turn_reply(*, attachments: str = "", retained: bool = False) -> str:
    """The reply for a turn that carries no request anywhere.

    `attachments` is `describe_turn_attachments`'s output; empty means the message was blank.
    `retained` says the attachment is a pasted document the chat keeps, so the reply must not
    claim it was dropped: the true statement is that it is kept and waits for an instruction.

    What this must NOT do is promise something the runtime cannot keep. The earlier wording ended
    "Tell me what to look for and I'll go through it", and that was false: this gate returns before
    `adapt_user_input` and `_prepare_runtime_checkpoint`, so no dialogue turn and no checkpoint are
    written, and nothing in the repo stores an attachment against a session for a later turn to
    reach -- `storage/media_evidence_log.py` is keyed by task and trace id with no session
    dimension, and `recent_media_evidence` is a global recency read. There is no store to preserve
    it in that does not have to be invented, so the reply says what is true: send it again with the
    instruction.
    """
    if attachments and retained:
        return (
            f"That came through as {attachments} with no message, so there's nothing telling me "
            "what to do with it. It's kept with this chat -- tell me what you'd like done with it "
            "and I'll work from it."
        )
    if attachments:
        return (
            f"That came through as {attachments} with no message, so there's nothing telling me "
            "what to do with it. I'm not holding on to it -- send it again with what you'd like "
            "done and I'll work from that."
        )
    return (
        "That message came through with nothing in it, so there's nothing for me to answer yet. "
        "Tell me what you'd like me to do and I'll get on it."
    )


def resumed_empty_turn_reply() -> str:
    """Reply for a resume whose stored request text turned out to be empty.

    Reachable from state this defect itself created: before the front-door gate existed, a
    whitespace turn got as far as creating a checkpoint whose `request_text` was that whitespace,
    and the crash handler in `_run_once_inner` then finalized it as `interrupted` -- which is a
    resume candidate. A later "continue" adopts that request text and lands on the same
    `ValueError`, with a raw input the front-door gate has no reason to stop. Answering here also
    completes the checkpoint, so the poisoned resume is offered once and never again.
    """
    return (
        "The turn I was picking back up has no request text stored in it, so there's nothing to "
        "run. Tell me what you'd like me to do and I'll start it fresh."
    )
