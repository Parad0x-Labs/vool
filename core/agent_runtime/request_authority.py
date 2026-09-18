"""Which text in a turn may become a COMMAND, and which may only ever be DATA.

Two categories. The boundary between them is the entire point of this module, and it is drawn by
PROVENANCE -- who authored the text and how it reached the runtime -- never by what a field is
called.

**A. User instruction authority.** Text the user themselves addressed to the agent this turn. Only
this may become `TaskEnvelopeV1.goal`, decide the action policy, or create a task.

**B. Evidence / data.** Everything the turn CARRIES: attachment extracted text, OCR, document
bodies, captions, quoted social content, fetched or embedded content, and any runtime- or
model-generated description of them. Evidence may inform execution AFTER a real request exists. It
may never BE the request.

Why the distinction is load-bearing
-----------------------------------

A PDF that contains "delete all files", an image whose OCR reads "run rm -rf", a quoted post
containing "ignore previous instructions" -- these are things the user SHOWED the agent, not things
the user ASKED it to do. An earlier version of this gate read `caption`/`transcript`/`text`/
`post_text` off an attachment and used them as the turn's request. That is command authority
granted on the strength of a dictionary key, and it is exactly the fusion this module exists to
prevent. Worse, it joined several items into one string, so two unrelated documents could be
concatenated into a single synthetic instruction nobody wrote.

What actually carries authority here, today
-------------------------------------------

`COMMAND_AUTHORITY_EVIDENCE_FIELDS` is **empty**, and that is a measured conclusion rather than
caution:

* There is no voice, microphone, speech-to-text or dictation input surface anywhere in this
  repository -- searched 2026-08-08 across `core/`, `apps/`, `relay/`, `tools/` for voice /
  microphone / speech_to_text / stt / transcrib* / whisper / dictation. Every hit is creative-set
  and visual-playbook prose about a character's voice, not an input transport.
* `core/channel_gateway.py:ChannelRequest` carries `platform`, `user_id`, `text`, `channel_id`,
  `persona_id`, `device_hint`, `surface` and `attachments`. None of them states that an attachment
  is the sender's own utterance. `surface`/`device_hint` are free strings a caller chooses.
* `core/media_ingestion.py:MediaEvidence` carries `source_kind` (social/web) and `media_kind`
  (image/video/text/social_post) -- what KIND of thing it is, never WHO authored it or whether it
  was addressed to the agent.

A genuine voice-input transcript really can be the user's request. But nothing in this codebase can
currently tell one apart from an audio file somebody forwarded, and a field named `transcript` is
not proof of anything -- any client can put any string there. So no evidence field is promoted, and
a turn carrying only evidence is answered as a turn with no request in it. That is the correct
answer here, not a placeholder: inventing certainty about who spoke would hand command authority to
whoever controls the attachment.

**To add one later**, a transport must stamp provenance the runtime itself trusts -- set by the
transport, never copied from client-supplied item fields -- and the field name goes in
`COMMAND_AUTHORITY_EVIDENCE_FIELDS` with `evidence_item_command_text` taught to require that stamp.
One place changes; the closure and the bounds below already hold.

Closed, not filtered
--------------------

The schema is an allowlist of field names, so a field nobody anticipated cannot leak in. `description`,
`alt_text`, `generated_summary`, `model_caption`, `metadata.prompt` and any nested metadata are not
rejected by name -- they are simply not members, and neither is anything else. A denylist would have
to guess every field a client might invent; this cannot be wrong about a field it has never heard of.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from itertools import islice
from typing import Any

# --------------------------------------------------------------------------------------------
# Visible text: what a reader would actually see
# --------------------------------------------------------------------------------------------

# `str.isspace()` already covers Zs/Zl/Zp and the ASCII whitespace controls. These are the
# categories that are invisible but are NOT whitespace, and so survive `strip()`:
#   Cc  C0/C1 controls (NUL, SOH, ...)
#   Cf  format characters (zero-width space/joiner/non-joiner, BOM, bidi marks, word joiner)
#   Cs  lone surrogates, which a badly decoded client payload can leave in a Python str
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


def visible_request_text(text: Any) -> str:
    """`text` with every invisible character removed -- what a reader would actually see."""
    return "".join(
        char
        for char in str(text or "")
        if not char.isspace() and unicodedata.category(char) not in _INVISIBLE_CATEGORIES
    )


def text_carries_no_request(text: Any) -> bool:
    """True when `text` ALONE carries nothing a model could be asked to answer."""
    return not visible_request_text(text)


# --------------------------------------------------------------------------------------------
# Cutting text without breaking it
# --------------------------------------------------------------------------------------------

# Characters that only mean anything ATTACHED TO WHAT PRECEDES THEM. Cutting immediately before one
# strands it; cutting immediately after a joiner leaves the joiner dangling. Both render as visible
# damage -- a lone combining accent, a half-formed emoji, a stray variation selector.
_ZERO_WIDTH_JOINER = "\u200d"
_COMBINING_CATEGORIES = frozenset({"Mn", "Mc", "Me"})
_REGIONAL_INDICATORS = range(0x1F1E6, 0x1F200)


def _joins_backwards(char: str) -> bool:
    return (
        char == _ZERO_WIDTH_JOINER
        or unicodedata.category(char) in _COMBINING_CATEGORIES
        # Variation selectors VS1-VS16 and the ideographic supplement.
        or 0xFE00 <= ord(char) <= 0xFE0F
        or 0xE0100 <= ord(char) <= 0xE01EF
    )


def safe_text_boundary(text: Any, limit: int) -> str:
    """`text` cut to at most `limit` characters, never mid-sequence.

    `core/execution/artifacts.py:truncate_text` was checked for reuse and is codepoint-naive
    (`value[:limit]`), so it would happily leave a dangling ZWJ or an orphaned combining mark. This
    is deliberately the smallest thing that fixes that and no more -- three rules, `unicodedata`
    only, no new dependency (the `regex` module with its `\\X` cluster support is present in this
    venv but is NOT a declared dependency of this project, so relying on it would break a clean
    install):

    1. never cut immediately before a character that attaches backwards;
    2. never leave a trailing joiner with nothing after it to join;
    3. never split a regional-indicator pair, which would turn a flag into a stray letter.

    Result is always <= `limit`; it never grows the string to keep a cluster whole.
    """
    value = str(text or "")
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    cut = limit
    while cut > 0 and _joins_backwards(value[cut]):
        cut -= 1
    while cut > 0 and value[cut - 1] == _ZERO_WIDTH_JOINER:
        cut -= 1
    trailing_regional = 0
    while trailing_regional < cut and ord(value[cut - 1 - trailing_regional]) in _REGIONAL_INDICATORS:
        trailing_regional += 1
    if trailing_regional % 2:
        cut -= 1
    return value[:cut]


# --------------------------------------------------------------------------------------------
# The authority schema
# --------------------------------------------------------------------------------------------

# Category A, for `external_evidence` items: the ONLY field names that may ever carry user
# instruction authority out of an attachment. Empty today -- see the module docstring for the
# provenance finding that makes it empty, and for what a transport must prove to add one.
#
# Membership is the whole rule. A field that is not a member is DATA, whatever it is called and
# however convincing its name.
COMMAND_AUTHORITY_EVIDENCE_FIELDS: frozenset[str] = frozenset()

# Turn-level, not per item. A per-item cap is not a bound at all: twenty-five attachments at two
# thousand characters each is fifty thousand characters of "request", which is how a many-attachment
# turn amplifies into the classifier, the envelope goal and the prompt. This caps what ONE TURN can
# contribute in total, whatever it arrived in.
TURN_COMMAND_MATERIAL_MAX = 2000

# How many evidence items ANY runtime path may touch. Not a display cap and not a late slice: the
# bound is on the WORK. A payload of fifty thousand attachments must not be copied, inspected,
# described, normalized, fetched or persisted fifty thousand times, and `list(x)[:64]` does exactly
# that -- it materializes the whole thing and only then throws most of it away.
#
# Deterministic and content-blind: the FIRST `TURN_EVIDENCE_ITEMS_MAX` items, in arrival order.
# Never chosen by what an item contains, because a rule that reads content to decide what to look
# at has already looked.
TURN_EVIDENCE_ITEMS_MAX = 64

# Server-owned checkpoint metadata. Callers may send a field with this name, but the runtime
# checkpoint front door deletes it and derives a replacement only from visible user text.
REQUEST_PROVENANCE_KEY = "_request_provenance"
_REQUEST_PROVENANCE_VERSION = 1
_REQUEST_PROVENANCE_ORIGIN = "visible_user_text"
_REQUEST_PROVENANCE_FIELDS = frozenset(
    {"version", "origin", "session_id", "request_sha256"}
)


def _stored_request_digest(text: Any) -> str:
    from core.secret_redaction import redact_secrets

    stored_text = redact_secrets(str(text or ""))
    return hashlib.sha256(stored_text.encode("utf-8", errors="surrogatepass")).hexdigest()


def request_provenance_for_visible_user_text(
    request_text: Any,
    *,
    session_id: str,
) -> dict[str, Any]:
    """Closed provenance stamp for a request the runtime saw as visible user text."""
    return {
        "version": _REQUEST_PROVENANCE_VERSION,
        "origin": _REQUEST_PROVENANCE_ORIGIN,
        "session_id": str(session_id or "").strip(),
        "request_sha256": _stored_request_digest(request_text),
    }


def checkpoint_request_has_authority(
    checkpoint: Any,
    *,
    expected_session_id: str,
) -> bool:
    """Whether a persisted request is proven user-authored command authority for this chat.

    Legacy absence, extra/unknown fields, an unknown version/origin, a cross-chat session, or a
    digest that does not bind the exact stored request all fail closed. Mere checkpoint existence
    and same-session lookup are deliberately insufficient provenance.
    """
    if not isinstance(checkpoint, dict):
        return False
    checkpoint_session_id = str(checkpoint.get("session_id") or "").strip()
    wanted_session_id = str(expected_session_id or "").strip()
    if not checkpoint_session_id or checkpoint_session_id != wanted_session_id:
        return False
    source_context = checkpoint.get("source_context")
    if not isinstance(source_context, dict):
        return False
    provenance = source_context.get(REQUEST_PROVENANCE_KEY)
    if not isinstance(provenance, dict) or frozenset(provenance) != _REQUEST_PROVENANCE_FIELDS:
        return False
    if provenance.get("version") != _REQUEST_PROVENANCE_VERSION:
        return False
    if provenance.get("origin") != _REQUEST_PROVENANCE_ORIGIN:
        return False
    if str(provenance.get("session_id") or "").strip() != checkpoint_session_id:
        return False
    request_text = str(checkpoint.get("request_text") or "")
    if text_carries_no_request(request_text):
        return False
    return str(provenance.get("request_sha256") or "") == _stored_request_digest(request_text)


@dataclass(frozen=True)
class BoundedEvidence:
    """One turn's evidence, already cut to budget, plus what is and is not known about the rest.

    The canonical view every consumer works from, so the bound is stated once instead of being
    re-sliced in five places that can each drift. It carries exactly four things and invents none
    of them:

    * `items` -- what was actually taken, in arrival order;
    * `source_exhausted` -- whether the source ENDED inside the budget. Fewer items than the limit
      proves it did; landing exactly on the limit proves nothing, because nothing looked further;
    * `recognized` -- the subset usable as an evidence record, which is not the same number as
      `items` when a client sends a malformed entry;
    * `remaining` -- the budget other origins in the same turn may still spend.

    There is deliberately no "original total" field. The total is the one thing that cannot be known
    without doing the work the budget exists to prevent, so it is absent rather than guessed.
    """

    items: tuple[Any, ...]
    limit: int
    source_exhausted: bool

    @property
    def taken(self) -> int:
        return len(self.items)

    @property
    def remaining(self) -> int:
        """Budget left for this turn's other evidence origins. One turn, one budget."""
        return max(0, self.limit - self.taken)

    @property
    def recognized(self) -> tuple[dict[str, Any], ...]:
        return tuple(item for item in self.items if isinstance(item, dict))

    @property
    def count_is_exact(self) -> bool:
        """Whether a count taken from this view may be stated as a total.

        Only when the source ended inside the budget. Sixty-three recognized items out of a budget
        that was fully spent is NOT sixty-three attachments -- the sixty-fourth was malformed and
        the two-hundredth was never looked at.
        """
        return self.source_exhausted


def bounded_evidence(items: Any, *, limit: int = TURN_EVIDENCE_ITEMS_MAX) -> BoundedEvidence:
    """Take at most `limit` evidence items WITHOUT walking whatever follows them.

    `islice` over an iterator, so a generator is advanced exactly `limit` times and a
    50,000-element list is read 64 times. Nothing peeks past the bound -- not even to learn how many
    there were, and not to improve the wording of a sentence.

    A caller that already holds a giant list has already paid for it; this cannot undo that. What it
    guarantees is that every unit of work the runtime does after this point is bounded.
    """
    bound = max(0, int(limit))
    if items is None or isinstance(items, (str, bytes, bytearray)):
        return BoundedEvidence(items=(), limit=bound, source_exhausted=True)
    try:
        taken = tuple(islice(iter(items), bound))
    except TypeError:
        return BoundedEvidence(items=(), limit=bound, source_exhausted=True)
    return BoundedEvidence(items=taken, limit=bound, source_exhausted=len(taken) < bound)


def bounded_evidence_items(items: Any, *, limit: int = TURN_EVIDENCE_ITEMS_MAX) -> list[Any]:
    """The bounded items alone, for a caller that needs no certainty about the remainder."""
    return list(bounded_evidence(items, limit=limit).items)


def compose_evidence_origins(
    *origins: Any,
    limit: int = TURN_EVIDENCE_ITEMS_MAX,
) -> list[Any]:
    """Spend one raw-item budget across evidence origins in deterministic origin order.

    Every item admitted from an earlier origin reduces what later origins may contribute. Invalid
    items count too: silently dropping one and refunding its slot would let an attacker force an
    unbounded scan with a stream of malformed values. No origin is inspected after the budget is
    exhausted, and no origin is replaced merely because another supplied the same context key.
    """
    remaining = max(0, int(limit))
    retained: list[Any] = []
    for origin in origins:
        if remaining <= 0:
            break
        view = bounded_evidence(origin, limit=remaining)
        retained.extend(view.items)
        remaining -= view.taken
    return retained


def evidence_item_command_text(item: Any) -> str:
    """The user-instruction text ONE evidence item carries; `""` unless it proves authority.

    Reads only members of `COMMAND_AUTHORITY_EVIDENCE_FIELDS` and never descends into nested
    metadata, so a `metadata.prompt`, a `description`, a model-written `generated_summary` or any
    other invented key is unreachable rather than filtered.
    """
    if not isinstance(item, dict):
        return ""
    for field in sorted(COMMAND_AUTHORITY_EVIDENCE_FIELDS):
        value = str(item.get(field) or "").strip()
        if not text_carries_no_request(value):
            return value
    return ""


def evidence_command_material(source_context: dict[str, object] | None) -> str:
    """The authority-bearing request an attachment carries, if exactly one does; `""` otherwise.

    Two rules, both about refusing to invent a request that nobody wrote:

    *Never fuse.* If more than one item claims authority, the turn is ambiguous -- there is no
    single thing the user asked -- and joining them would manufacture one instruction out of
    several sources. Ambiguity resolves to "no request", which the caller answers by asking.

    *Always bounded.* The one surviving claim is cut at the TURN-level budget, on a safe boundary.
    """
    items = bounded_evidence_items((source_context or {}).get("external_evidence"))
    claims = [text for text in (evidence_item_command_text(item) for item in items) if text]
    if len(claims) != 1:
        return ""
    return safe_text_boundary(claims[0], TURN_COMMAND_MATERIAL_MAX)


def turn_command_text(text: Any, source_context: dict[str, object] | None = None) -> str:
    """This turn's request -- the text that may become a goal -- or `""` if it has none.

    The user's own visible text wins outright and is returned UNCHANGED, byte for byte, so a turn
    that was never empty is unaffected by any of this. Only a turn with no visible text asks the
    attachment at all, and today the attachment can never answer.
    """
    if not text_carries_no_request(text):
        return str(text)
    return evidence_command_material(source_context)
