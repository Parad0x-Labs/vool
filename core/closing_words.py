"""Words that close a message politely or soften its end. Imports nothing, so any parser may read it."""

from __future__ import annotations

#: Words that close a message politely or soften it at its end ("at 8 pls", "at 8 thanks", "by 5 ok?"). After a
#: number they end the phrase the number belongs to, the way "please" does; a noun after a number makes it a count
#: or an address. One list, read by the time parser (core.operator.when) and anything else that asks the same.
CLOSING_COURTESY_WORDS = (
    "please", "pls", "plz", "thanks", "thank you", "thankyou", "thx", "tx", "ty", "tysm", "cheers",
    "ok", "okay", "then", "again", "sharp", "lol", "haha",
)

__all__ = ["CLOSING_COURTESY_WORDS"]
