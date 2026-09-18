"""The ONE authority for the chat handle a client names.

A chat is addressed by many doors -- `/api/chat`, and every attachment door beside it (upload,
list, documents, preview, remove, queue). Each of them receives the handle from the client, in
whichever shape that client uses: the desktop page mints a canonical `openclaw:<20 hex>` id, while
an API client, a script or an integration commonly sends a plain string of its own.

Those doors MUST agree on which chat the handle names. When they did not, the split was silent and
total: `/api/chat` hashed a plain handle while the upload door stored it verbatim, so an upload
returned 200, the record was stamped with the raw handle, and the very next message carrying that
attachment id was refused `not_owned` -- an attachment that could be staged but never sent, with
nothing in the refusal naming the real reason.

So the rule lives here, once, and every door resolves through it:

* a canonical handle (`openclaw:` + 20 lowercase hex) is the identity itself and passes through
  verbatim -- this is what makes reopening a thread resume it instead of forking a new one;
* any other handle is folded into that shape by digest.

The rule is IDEMPOTENT by construction: folding a plain handle yields a canonical one, and folding
a canonical handle returns it unchanged. That is the property the doors rely on -- one that has
already resolved the handle and one that has not both land on the same identity, so no door can
reintroduce the split by forgetting to call this.

Distinct handles keep distinct identities (80 bits of SHA-256), so folding never lets one chat
reach another chat's attachments.
"""

from __future__ import annotations

import hashlib

#: The shape a VOOL-native chat id has everywhere: the prefix plus a 20-char lowercase-hex digest.
CANONICAL_PREFIX = "openclaw:"
CANONICAL_DIGEST_CHARS = 20

_HEX = frozenset("0123456789abcdef")


def is_canonical_chat_session_id(value: str) -> bool:
    """True when `value` is already the canonical id shape and is therefore its own identity."""
    text = str(value or "")
    if not text.startswith(CANONICAL_PREFIX):
        return False
    suffix = text[len(CANONICAL_PREFIX) :]
    return len(suffix) == CANONICAL_DIGEST_CHARS and all(char in _HEX for char in suffix)


def fold_chat_session_id(value: str) -> str:
    """Fold ANY handle into the canonical shape by digest -- a canonical one included.

    Used where passing a canonical handle through would be wrong: a remotely reachable API must
    not be able to select an existing desktop chat's namespace by presenting a guessed canonical
    id, so its handle is always folded rather than honoured.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:CANONICAL_DIGEST_CHARS]
    return f"{CANONICAL_PREFIX}{digest}"


def canonical_chat_session_id(value: str) -> str:
    """The identity of the chat this handle names. Idempotent; empty in, empty out."""
    text = str(value or "").strip()
    if not text:
        return ""
    if is_canonical_chat_session_id(text):
        return text
    return fold_chat_session_id(text)
