"""A model may not award itself a verification label. Only the runtime may, and only on evidence.

Measured 2026-08-05. Asked a multi-part car question that explicitly said *"separate verified facts
from unavailable data, and do not guess"*, the runtime returned a table with a **"Verified fact"**
column and filled every cell -- including four with no source behind them. One was internally
contradictory (`1.6L 1ZR-FAE / 2ZR-FAE` names a 1.8L/1797cc engine) and one appears to have mangled
Toyota's 22.65M cumulative figure and its 1.22M 2013-annual figure into an invented 1997 peak.

The fabrication is the model's. The gap is the runtime's: nothing stopped a model stamping its own
guesses "verified", and a polished table makes that worse rather than better -- the fluency is
exactly what makes the invented cells credible.

Why this needs no evidence lookup
---------------------------------
`grep -r "Verified fact" core/ tools/` returns nothing. The runtime has never awarded that label and
has no code that could. So its presence proves the model wrote it, which makes it unearned by
definition -- there is no case where a self-awarded label is legitimate, and therefore nothing to
check it against. That is what keeps this to pure post-processing: no extra model call, no browsing
dependency, no latency, and none of the orchestration failure modes that copying the audit lane's
proof-state machinery into every chat turn would reintroduce.

Scope, deliberately narrow
--------------------------
This downgrades the LABEL. It does not judge the claim, and it must not: deciding whether
`2ZR-FAE` is 1.8L requires authoritative data this layer does not have, and a second model's recall
is not verification. Protections (B) grounded-mode detection, (C) SUPPORTED/INFERRED/UNAVAILABLE/
CONFLICTING cell states, and (D) citation entailment are the layers that address the claim itself --
see VOOL-DELIVERY/RUNTIME_UPGRADE_LEDGER.md, "tiered truthfulness".

What is NOT touched: a person's own words quoted back, a source label the runtime attached itself
(`Source: [CoinGecko](...)`), and ordinary uses of "confirmed" or "official" in running prose --
"Toyota officially reported" is a sourcing statement about someone else, not a self-award.
"""

from __future__ import annotations

import re

# Only headings and standalone assertions are rewritten. The trailing-context requirement is what
# keeps "Toyota officially confirmed 50 million sales" -- a statement about what a manufacturer
# said -- from being caught alongside "Verified fact: white".
_SELF_AWARDED = (
    (re.compile(r"\bverified\s+facts?\b", re.IGNORECASE), "Model-provided"),
    (re.compile(r"\bconfirmed\s+facts?\b", re.IGNORECASE), "Model-provided"),
    (re.compile(r"\bfact[-\s]?checked\b", re.IGNORECASE), "Model-provided"),
    (re.compile(r"\bproven\s+facts?\b", re.IGNORECASE), "Model-provided"),
    (re.compile(r"\bauthoritative\s+(?:answer|fact|figure)s?\b", re.IGNORECASE), "Model-provided"),
    (re.compile(r"\bofficially\s+(?:verified|proven)\b", re.IGNORECASE), "Model-provided"),
)

# Appended once when anything was downgraded, so the reader learns the label changed rather than
# silently reading a relabelled table as if the runtime had endorsed it. Silent rewriting would be
# its own honesty problem.
_NOTE = (
    "\n\n_Note: this answer's confidence labels were set by the model, not verified by the runtime. "
    "Treat the figures as model-provided unless a source is attached._"
)


def downgrade_unearned_verification_labels(text: str) -> tuple[str, list[str]]:
    """Rewrite self-awarded verification labels. Returns (text, what was downgraded).

    The second element is the audit trail -- a decorator that removes meaning silently is the defect
    this runtime already has elsewhere (see `_decorate_chat_response` deleting a quote's price and
    source), and repeating it here would trade one honesty failure for another.
    """
    original = str(text or "")
    if not original.strip():
        return original, []
    downgraded: list[str] = []
    result = original
    for pattern, replacement in _SELF_AWARDED:
        found = pattern.findall(result)
        if not found:
            continue
        downgraded.extend(match if isinstance(match, str) else str(match) for match in found)
        result = pattern.sub(replacement, result)
    if not downgraded:
        return original, []
    return result + _NOTE, downgraded
