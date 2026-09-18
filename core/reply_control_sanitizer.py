from __future__ import annotations

import re

# `NO_REPLY` is OpenClaw's framework "stay silent" control token (its group-mention ack scope). It
# is not part of vool-local's own output vocabulary, but the model behind the OpenClaw persona
# sometimes emits it as literal content. In direct chat it then leaks two ways: as a bare dead
# reply, or as a prefix on its OWN line ahead of the real answer ("NO_REPLY\n\nThis is a good
# question! ..."). Once such a reply is logged, the token also re-enters later turns as history noise.
#
# Only a STANDALONE token (its own line, or the whole reply) is stripped — never an inline leading
# word — so a legitimate answer ABOUT the token ("NO_REPLY is OpenClaw's silence token") is left
# intact. The substring guard is case-insensitive to match the regex.
_STANDALONE_NO_REPLY_LINE_RE = re.compile(r"(?im)^[ \t>*_`\-]*NO_REPLY[ \t.:;!?\-]*(?:\r?\n|$)")
# The whole reply is just a leaked token: NO_REPLY followed only by whitespace/punctuation/emoji
# (no real answer). This is distinct from a sentence ABOUT the token ("NO_REPLY is OpenClaw's silence
# token", where a word follows) and from a prefix ("NO_REPLY\n\n<real answer>"). \W matches emoji too.
_BARE_NO_REPLY_RE = re.compile(r"^\s*no_reply\W*$", re.IGNORECASE)


def is_bare_reply_control_token(text: str) -> bool:
    """True when the entire reply is a leaked ``NO_REPLY`` token (optionally trailed by
    whitespace/punctuation/emoji) with no real answer — so it must be replaced, not shown raw."""
    return bool(_BARE_NO_REPLY_RE.match(str(text or "")))


def strip_reply_control_tokens(text: str) -> str:
    """Remove standalone ``NO_REPLY`` control-token lines, returning the remaining content.

    The result may be empty when the whole reply was just the token. Use this on vool-local's own
    internal history store so the token cannot re-feed as noise on later turns.
    """
    s = str(text or "")
    if "no_reply" not in s.lower():
        return s.strip()
    return _STANDALONE_NO_REPLY_LINE_RE.sub("", s).strip()


def reveal_reply_control_prefix(text: str) -> str:
    """Return the real answer hidden behind a standalone ``NO_REPLY`` prefix line.

    If the reply was ONLY the control token (no content follows), return the original unchanged: a
    bare silence token is OpenClaw's to suppress, and vool-local must neither fabricate content nor
    override an intended group-channel silence. Revealing a prefix is always safe — a turn that
    produced a real answer was never actually silent.
    """
    original = str(text or "")
    if "no_reply" not in original.lower():
        return original
    stripped = strip_reply_control_tokens(original)
    return stripped if stripped else original


__all__ = ["is_bare_reply_control_token", "reveal_reply_control_prefix", "strip_reply_control_tokens"]
