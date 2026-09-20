"""The app-language completion laws: what ships must be translated, and nothing joins the
UI silently in English only.

Guards in this file answer one concrete question each:

- every ``core.faults`` code (Worker C's lane) carries BOTH presentation keys —
  ``fault.<code>.message`` and ``fault.<code>.action`` — mirroring the authority
  byte-exactly in English, so a new fault code cannot ship without its localized seam;
- every ledger event type the activity panel can label has an ``activity.ledger.*`` key,
  so a new runtime event cannot render through an English-only fallback;
- every settings group/row/option string has its stable ``settings.group.*`` /
  ``settings.row.*`` key, so a new settings row cannot bypass the catalog;
- every ``usepod.receipt.*`` reference in the page source resolves to a catalog key;
- every shipped locale catalog is COMPLETE against the English source (a locale that
  lost keys serves English silently — the exact regression this stops) and records the
  current source hash;
- a translation byte-identical to English is only accepted for the documented technical
  allowlist (model names, units, PIN): copied English is not a completed translation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.i18n.catalog import CATALOGS_DIR, MessageCatalog, source_catalog_sha256

CHAT_PAGE = Path("core/vool_chat_page.py").read_text(encoding="utf-8")


def _en_messages() -> dict[str, str]:
    return json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]


# ---- Worker C's fault codes cannot ship without their localized presentation seam ------------


def test_every_fault_code_carries_both_presentation_keys_mirroring_the_authority() -> None:
    from core.faults.catalog import all_specs

    messages = _en_messages()
    for spec in all_specs():
        message_key = f"fault.{spec.code}.message"
        action_key = f"fault.{spec.code}.action"
        assert message_key in messages, (
            f"{message_key} missing from en.json — a new fault code must add its "
            "presentation keys in the same change (see core/i18n/ERROR_WORKER_CONTRACT.md)"
        )
        assert action_key in messages, f"{action_key} missing from en.json"
        assert messages[message_key] == spec.user_message, (
            f"{message_key} drifted from core.faults authority — regenerate the mirror"
        )
        assert messages[action_key] == spec.operator_action, (
            f"{action_key} drifted from core.faults authority — regenerate the mirror"
        )


# ---- runtime-owned activity labels cannot render English-only ---------------------------------


def test_every_ledger_event_type_has_a_catalog_key() -> None:
    map_block = re.search(r"const LEDGER_MAP = \{(.*?)\n\};", CHAT_PAGE, re.DOTALL)
    assert map_block, "LEDGER_MAP not found in the chat page"
    entries = re.findall(r"'?([A-Za-z][A-Za-z0-9_.]*)'?\s*:\s*\[[^\]]*'[^']*'\]", map_block.group(1))
    assert entries, "LEDGER_MAP parse found no entries — update the guard's parser"
    messages = _en_messages()
    missing = [t for t in entries if f"activity.ledger.{t}" not in messages]
    assert not missing, (
        f"ledger event types without activity.ledger.* keys (they would render "
        f"English-only in every locale): {missing}"
    )


def test_every_referenced_usepod_receipt_key_exists() -> None:
    referenced = set(re.findall(r"pageTF?\('(usepod\.receipt\.[a-z_]+)'", CHAT_PAGE))
    assert referenced, "no usepod.receipt references found — parser rot"
    messages = _en_messages()
    missing = sorted(referenced - set(messages))
    assert not missing, f"usepod receipt prose keys referenced but not cataloged: {missing}"


# ---- settings model strings are keyed at their stable ids --------------------------------------


def test_every_settings_model_string_has_its_catalog_key() -> None:
    from core.vool_settings_page import settings_groups

    messages = _en_messages()
    missing: list[str] = []
    for group in settings_groups():
        keys = [f"settings.group.{group['id']}.title"]
        if group.get("blurb"):
            keys.append(f"settings.group.{group['id']}.blurb")
        for row in group.get("rows", []):
            keys.append(f"settings.row.{row['id']}.label")
            for field in ("help", "placeholder", "effect"):
                if row.get(field):
                    keys.append(f"settings.row.{row['id']}.{field}")
            for option in row.get("options") or []:
                keys.append(f"settings.row.{row['id']}.option.{option['value']}")
        missing.extend(k for k in keys if k not in messages)
    assert not missing, (
        f"settings model strings without catalog keys (they would stay English in every "
        f"locale): {missing}"
    )


# ---- shipped locale catalogs are complete and current ------------------------------------------


def test_every_shipped_locale_catalog_is_complete_and_current() -> None:
    sha = source_catalog_sha256()
    en_keys = set(_en_messages())
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
        missing = sorted(en_keys - set(catalog.locale_keys))
        if missing:
            problems.append(f"{tag}: missing {len(missing)} keys, first: {missing[0]}")
        if catalog.diagnostic.rejected_keys:
            first = next(iter(catalog.diagnostic.rejected_keys.items()))
            problems.append(f"{tag}: rejected keys, first: {first}")
    assert not problems, "; ".join(problems)


#: Keys whose translation may legitimately equal the English source: closed vocabularies
#: (Auto / Local only model options), unit symbols (min), technical identifiers (PIN,
#: audio family names), and short function words that are true homographs across the
#: shipped languages (the French for "Conversation" IS "Conversation"; likewise
#: Tests/Standard/General/Model/Casual/Mode/Contacts/Protection/Notifications/Agents/
#: Actions/Sources/Verdict/Crypto/Humour/Pause/signature/transaction/route/minutes).
#: The technical-view field labels and fragment labels appended below join that class:
#: "diff" is the term of art in es/fr/lt/pl/pt/tr, French keeps phase/route/action/
#: permission/Source, German keeps Name/Cloud/Plugins, Spanish keeps error — forcing a
#: synthetic synonym there would make the UI worse, not more translated.
#: Copied English anywhere else is not a completed translation.
TECHNICALLY_IDENTICAL_KEYS = frozenset(
    {
        "activity.category.runtime",
        "activity.category.tests",
        "activity.rollup.actions",
        "activity.field.action",
        "activity.field.diff_summary",
        "activity.field.error_kind",
        "activity.field.permission_decision",
        "activity.field.phase",
        "activity.field.verify.route",
        "attach.audio_family",
        "bypass.expiry_15",
        "bypass.expiry_30",
        "bypass.expiry_60",
        "chat.log_aria",
        "contacts.protection",
        "contacts.title",
        "header.cloud_auto",
        "header.cloud_local",
        "header.model_auto_option",
        "header.model_label",
        "header.model_local_only_option",
        "header.model_popover_title",
        "header.update_chip",
        "header.update_pop_aria",
        "header.update_popover_title",
        "mode.auto",
        "mode.js.auto",
        "mode.js.manual",
        "mode.js.plan",
        "mode.manual",
        "mode.plan",
        "mode.popover_title",
        "model.js.auto",
        "model.js.local_only",
        "notif.pop_aria",
        "panel.tab_agents",
        "panel.tab_tests",
        "pay.minutes_short",
        "pay.row_reasoning",
        "permissions.reversible",
        "plugins.panel_plugins",
        "plugins.panel_skills",
        "plugins.title",
        "proof.head_actions",
        "proof.head_sources",
        "proof.model_row",
        "receipts.field_verdict",
        "receipts.no",
        "run.error_prefix",
        "run.total_word",
        "session.general",
        "settings.group.general.title",
        "settings.group.notifications.title",
        "settings.group.wallet.title",
        "settings.row.boundaries_mode.option.standard",
        "settings.row.communication_style.option.casual",
        "settings.row.humor_percent.label",
        "settings.row.model_pin.label",
        "setup.copy.pause",
        "sidebar.chats",
        "sidebar.contacts",
        "sidebar.plugins",
        "sidebar.skills",
        "update.version_line",
        "usepod.receipt.link_signature",
        "usepod.receipt.route",
        "usepod.receipt.transaction",
        "vcs.add.name",
        "vcs.add.source",
        "vx.toolbelt.cloud",
        "vx.toolbelt.plugins",
        "wallet.pin_short",
        "wallet.signature",
    }
)


# ---- a payment receipt carries identical VALUES in every locale ------------------------------


def test_receipt_prose_localizes_but_the_exact_values_never_change() -> None:
    """The same UsePod receipt fixture, resolved in every shipped locale: the prose is
    the locale's, while every amount, unit, identifier and signature is byte-identical
    everywhere — localization may never re-parse or round a charge."""
    from core.i18n.catalog import CATALOGS_DIR, catalog_for, clear_catalog_cache

    fixture = {
        "usepod.receipt.paid_chain_confirmed": {
            "outflow": "0.000420 USDC",
            "fee": "0.000001205 SOL",
        },
        "usepod.receipt.charged_upper_bound": {"amount": "1.250000 USDC"},
        "usepod.receipt.link_signature": {"id": "5Kd8Nh…"},
    }
    clear_catalog_cache()
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        tag = path.stem
        catalog = catalog_for(tag)
        for key, params in fixture.items():
            rendered = catalog.format(key, **params)
            for value in params.values():
                assert value in rendered, (
                    f"{tag}: {key} lost the exact value {value!r} in {rendered!r}"
                )
    # And the prose really is localized, not copied English.
    de = catalog_for("de").format("usepod.receipt.charged_upper_bound", amount="1.250000 USDC")
    assert "1.250000 USDC" in de and de != "up to 1.250000 USDC (usage at the approved ceiling — an upper bound, not the charge)"


# ---- copied English is not a translation -------------------------------------------------------


def test_copied_english_is_not_counted_as_translation_outside_the_allowlist() -> None:
    en = _en_messages()
    offenders: list[str] = []
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        tag = path.stem
        if tag == "en":
            continue
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        copied = sorted(
            key
            for key, value in messages.items()
            if key in en and value == en[key] and key not in TECHNICALLY_IDENTICAL_KEYS
        )
        if copied:
            offenders.append(f"{tag}: {len(copied)} untranslated-copies, first: {copied[0]}")
    assert not offenders, "; ".join(offenders)
