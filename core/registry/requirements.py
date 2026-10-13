"""Fixed list of what a skill can say it needs, plus the supported-languages rule.

Authors declare `needs` (ids from NEEDS) and `languages` in signed package metadata.
Provider labels come from VOOL's own provider tables, so the list names the same
services the app can actually be set up with.
"""
from __future__ import annotations

import re

from core.cloud_providers import PROVIDERS as CLOUD_PROVIDERS
from core.registry.codec import require
from core.search_providers import SEARCH_PROVIDERS

# id: (label, kind, provider labels). "provider" needs one of the listed services'
# keys in VOOL; "vool" uses something VOOL itself provides or the user allows.
NEEDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "search_web": ("Web search", "provider", tuple(cfg.label for cfg in SEARCH_PROVIDERS.values())),
    "llm_cloud": ("Cloud AI model", "provider", tuple(cfg.label for cfg in CLOUD_PROVIDERS.values())),
    # The chain-data services VOOL's provider-key setup accepts.
    "chain_data": ("Chain data", "provider", ("Helius", "Alchemy", "Solscan", "Etherscan")),
    "solana_rpc": ("Solana connection", "vool", ()),
    "wallet": ("VOOL wallet", "vool", ()),
    "browser": ("Browser", "vool", ()),
    "files": ("Your files", "vool", ()),
}

MAX_LANGUAGES = 30
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-(?:[A-Z]{2}|[0-9]{3}))?")

# Clear signs in SKILL.md text that a need is used. Advisory only: a notice, never a block.
_SIGNS = {
    "search_web": r"\bweb\.(?:search|research)\b|\bsearch (?:the )?(?:web|internet)\b|\bweb search\b",
    "wallet": r"\bwallet\.(?:propose|spend|read|status|simulate|payment_status)\b|"
              r"\b(?:your|the user'?s) (?:crypto )?wallet\b|\bsend (?:sol|eth|usdc|usdt)\b",
    "browser": r"\bweb\.browser\b|\bbrowser\.render\b|\b(?:open|use) (?:a|the) browser\b",
    "files": r"\bfiles?\.(?:read|write|list)\b|\b(?:local|your) files\b",
    "chain_data": r"\bweb\.chain_lookup\b|\b(?:helius|solscan|etherscan)\b",
    "solana_rpc": r"\bsolana rpc\b",
}


def check_languages(value: object) -> list[str]:
    require(isinstance(value, list) and (value == ["any"] or (
        0 < len(value) <= MAX_LANGUAGES and
        all(isinstance(code, str) and bool(_LANGUAGE.fullmatch(code)) and code != "any" for code in value) and
        len(set(value)) == len(value))),
        "languages must be 1 to 30 language codes such as en or pt-BR, or exactly [\"any\"]")
    return value


def check_needs(value: object) -> list[str]:
    require(isinstance(value, list) and all(isinstance(item, str) and item in NEEDS for item in value) and
            len(set(value)) == len(value),
            "needs must use ids from the registry requirements list")
    return value


def require_for_new_version(info: dict) -> None:
    """New uploads must say which languages they work in; earlier signed packages may not."""
    require(isinstance(info, dict) and "languages" in info,
            "languages required: list the language codes the skill works in, such as [\"en\"], or [\"any\"]")


def public_requirements() -> list[dict]:
    return [{"id": key, "label": label, "kind": kind, "providers": list(providers)}
            for key, (label, kind, providers) in NEEDS.items()]


def undeclared_needs(metadata: dict, files: dict[str, bytes]) -> list[dict]:
    """Needs a skill's instructions clearly use but its metadata leaves out."""
    declared = set(metadata.get("needs", []))
    text = "\n".join(data.decode("utf-8", "replace") for name, data in sorted(files.items())
                     if name.endswith("SKILL.md")).casefold()
    return [{"need": need, "label": NEEDS[need][0],
             "reason": f"SKILL.md looks like it uses {NEEDS[need][0].lower()}; add \"{need}\" to needs if it does"}
            for need, pattern in _SIGNS.items() if need not in declared and re.search(pattern, text)]
