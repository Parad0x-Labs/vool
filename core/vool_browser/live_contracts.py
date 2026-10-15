"""Describe the supervised lane's actual operations at the existing catalog owner."""
from __future__ import annotations

from dataclasses import replace

PROPOSALS = frozenset({
    "vool-browser.session.open", "vool-browser.navigate", "vool-browser.click",
    "vool-browser.type", "vool-browser.download",
})
READS = frozenset({"vool-browser.inspect", "vool-browser.session.status"})
CLOSE = frozenset({"vool-browser.session.close", "vool-browser.cancel"})
SUPPORTED = PROPOSALS | READS | CLOSE


def apply_live_browser_contracts(contracts):
    from core.i18n import catalog_for
    from core.vool_browser.live import configured

    if not configured():
        return contracts
    result = []
    for contract in contracts:
        if not contract.intent.startswith("vool-browser."):
            result.append(contract)
        elif contract.intent not in SUPPORTED:
            result.append(replace(contract, supported=False, unsupported_reason=catalog_for("en").text("live.unsupported")))
        elif contract.intent in PROPOSALS:
            # A model proposal cannot touch the engine, disk or network. The
            # operator-only browser.decide command owns the actual effect.
            result.append(replace(contract, side_effect_class="read_only", approval_requirement="none", permission_actions=("read_files",)))
        else:
            result.append(contract)
    return result
