"""The one browser identity every outbound request in this repo presents.

Measured on 2026-08-17: `http_fetch_text` announced itself as `VOOL-FETCH/1.0`, and a User-Agent
that names itself a bot is served a DECOY INDEX rather than a refusal -- a 200 with real HTML that
is simply not the answer to the query. Against Bing:

    "how many moons does jupiter have"    -> dictionary definitions of the word "many"
    "boiling point of ethanol in celsius" -> German Google Chrome help pages
    "who is the president of Kenya"       -> US presidents
    "Garmin Fenix 7 weight battery life"  -> brand homepages, Alza.cz, Wikipedia "Garmin"

Nothing downstream ever detected this, because a decoy index is indistinguishable from a bad
ranking. There is exactly one reference to the old string in the tree, so this constant is the
single place the identity is decided: the plain-HTTP door and the headless-browser door must not
drift apart, or a site can tell the two apart and serve them different pages.

A real User-Agent is necessary and NOT sufficient -- see `tools/web/browser_search.py` for the
measurement showing that headers alone do not restore real results.
"""

from __future__ import annotations

# A current desktop Chrome on macOS. Kept deliberately boring: the point is to look like the
# browser that is actually installed on this machine, not to fingerprint as anything exotic.
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Without this, engines geo-scatter results: the measured Bing runs returned German help pages and
# a Czech retailer for English queries. Sending it removed the geographic scatter (it did not, on
# its own, restore relevant results).
DESKTOP_ACCEPT_LANGUAGE = "en-US,en;q=0.9"
