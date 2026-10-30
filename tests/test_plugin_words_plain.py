"""Plain-words laws for every plugin message a person can see.

Guards over core/i18n/catalogs:

- no developer vocabulary in any addons.*plugin* / keys.plugin* English value
  (the words a person reads on the Add-ons and Keys pages);
- a plugin_message_ key is the digest of its own English text, so a reworded
  sentence can never keep travelling under its old key;
- every catalog plugin_message_ entry is raised by real code, so reworded
  words actually reach a page instead of the generic failure;
- every plugin key is present in all 16 locale catalogs as a real translation
  (never a copy of the English) and stamped against the current English source.
"""
from __future__ import annotations

import hashlib
import json
import re

from core.i18n.catalog import CATALOGS_DIR, MessageCatalog, source_catalog_sha256

BANNED_WORDS = (
    "manifest", "mcp", "env", "sandbox", "resolve", "endpoint", "wildcard",
    "authority", "declarative", "runtime", "bytes", "commit", "repository",
    "token", "turn", "http",
)
_WORD = re.compile(r"\b(" + "|".join(BANNED_WORDS) + r")\b", re.IGNORECASE)

RAISING_MODULES = (
    "plugin_manager", "plugin_manifest", "plugin_package", "release_channel",
    "addon_store", "plugin_services", "plugin_scanners", "plugin_network",
    "plugin_providers", "plugin_processes", "addon_security", "plugin_lifecycle",
    "plugin_container", "plugin_migration", "plugin_executor", "plugin_tools",
    "plugin_store_fragment", "addon_store_fragment", "vool_settings_page",
    "mcp_client", "addon_discovery", "local_scan",
)
REPO_ROOT = CATALOGS_DIR.parent.parent.parent


def _en_messages() -> dict[str, str]:
    return json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]


def _plugin_keys(messages: dict[str, str]) -> list[str]:
    return [k for k in messages
            if (k.startswith("addons.") and "plugin" in k) or k.startswith("keys.plugin")]


def test_no_developer_words_in_plugin_catalog_english() -> None:
    messages = _en_messages()
    offenders: dict[str, list[str]] = {}
    for key in _plugin_keys(messages):
        hits = sorted({m.group(0).lower() for m in _WORD.finditer(messages[key])})
        if hits:
            offenders[key] = hits
    assert not offenders, f"developer words still shown to people: {offenders}"


def test_plugin_message_keys_digest_their_own_english() -> None:
    messages = _en_messages()
    broken = [
        key for key, value in messages.items()
        if key.startswith("addons.plugin_message_")
        and hashlib.sha256(value.encode()).hexdigest()[:12] != key[len("addons.plugin_message_"):]
    ]
    assert not broken, f"keys no longer digest their own text: {broken}"


def test_every_plugin_message_is_raised_by_real_code() -> None:
    """user_message() serves the generic failure for a key the code never
    derives; a catalog entry without a raiser is words nobody can see."""
    messages = _en_messages()
    sources = []
    for name in RAISING_MODULES:
        path = REPO_ROOT / "core" / f"{name}.py"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            # implicit string concatenation: "…part "\n            "rest…" raises one message
            text = re.sub(r'"\s*\n\s*"', "", text)
            sources.append(text)
    joined = "\n".join(sources)
    reachable = {
        "addons.plugin_message_" + hashlib.sha256(value.encode()).hexdigest()[:12]
        for value in messages.values()
        if value in joined
    }
    orphaned = sorted(k for k in messages
                     if k.startswith("addons.plugin_message_") and k not in reachable)
    assert not orphaned, (
        f"catalog messages no code raises (repo root {REPO_ROOT}, "
        f"{len(joined)} source chars read): {orphaned}")


def test_every_shipped_locale_translates_every_plugin_key_for_real() -> None:
    sha = source_catalog_sha256()
    english = _en_messages()
    problems: list[str] = []
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        tag = path.stem
        if tag == "en":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("source_catalog_sha256") != sha:
            problems.append(f"{tag}: stale source hash (regenerate against current en.json)")
            continue
        catalog = MessageCatalog(tag)
        own = data.get("messages", {})
        for key in _plugin_keys(english):
            if key not in catalog.locale_keys:
                problems.append(f"{tag}: {key} missing or rejected")
            elif own.get(key) == english[key]:
                problems.append(f"{tag}: {key} is a copy of the English")
    assert not problems, "; ".join(problems[:12])
