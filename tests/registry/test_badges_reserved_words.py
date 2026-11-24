"""No author can write a badge: the two badge labels, as VOOL shows them in each of its 17 languages, are refused
in a skill's title, summary and text and in a plugin's name, read from the language files when the check runs."""
import json

import pytest

from core import plugin_identity
from core.i18n.catalog import CATALOGS_DIR
from core.registry import checks
from tests.registry.test_skill_codex_checks import BODY, found, info, package

LABELS = {path.stem: json.loads(path.read_text(encoding="utf-8"))["messages"] for path in sorted(CATALOGS_DIR.glob("*.json"))}
KEYS = ("addons.badge_tested", "addons.badge_editors_choice")


def test_every_language_vool_ships_has_both_labels_and_both_help_lines():
    assert len(LABELS) == 17
    for locale, messages in LABELS.items():
        for key in (*KEYS, "addons.badge_tested_help", "addons.badge_editors_choice_help"):
            assert isinstance(messages.get(key), str) and messages[key].strip(), (locale, key)
        if locale != "en":
            assert all(messages[key] != LABELS["en"][key] for key in KEYS), locale  # a real translation, not a copy


@pytest.mark.parametrize("locale", sorted(LABELS))
@pytest.mark.parametrize("key", KEYS)
def test_a_skill_cannot_carry_a_badge_label_in_its_title_summary_or_text(tmp_path, locale, key):
    label = LABELS[locale][key]
    spellings = [label, label.upper(), "  ".join(label.split()), label.replace(" ", "-")]
    for number, spelling in enumerate(spellings):
        folder = tmp_path / f"case-{number}"
        folder.mkdir()
        findings, _ = found(folder, package(folder, metadata=info(summary="Notes into decisions. " + spelling)))
        assert ("reserved-summary", "registry.json") in {(item["rule"], item["path"]) for item in findings}, spelling
    folder = tmp_path / "title"
    folder.mkdir()
    findings, _ = found(folder, package(folder, metadata=info(title="Notes " + label)))
    assert "reserved-name" in {item["rule"] for item in findings}
    folder = tmp_path / "text"
    folder.mkdir()
    findings, _ = found(folder, package(folder, BODY + "\n" + label + "\n"))
    assert {item["rule"] for item in findings} == {"endorsed"}


@pytest.mark.parametrize("locale", sorted(LABELS))
@pytest.mark.parametrize("key", KEYS)
def test_a_plugin_cannot_carry_a_badge_label_in_its_name(locale, key):
    label = LABELS[locale][key]
    manifest = {"name": "acme-parcel", "version": "1.0.0", "vool": {"id": "acme-parcel", "name": label, "publisher": "Acme"}}
    assert plugin_identity.identity_refusal(manifest) != ""
    shown = {"name": "acme-parcel", "version": "1.0.0", "vool": {"id": "acme-parcel", "name": "Parcel desk", "publisher": "Acme"},
             "interface": {"displayName": "Parcel desk " + label}}
    assert plugin_identity.identity_refusal(shown) != ""


@pytest.mark.parametrize("title", ["Highlighted notes", "Audit" + "ed ledger helper", "Notes (highlighted)"])
def test_a_title_cannot_say_it_was_highlighted_or_looked_over(tmp_path, title):
    findings, _ = found(tmp_path, package(tmp_path, metadata=info(title=title)))
    assert [(item["rule"], item["path"]) for item in findings] == [("reserved-name", "registry.json")]


def test_tested_next_to_vools_name_is_refused_in_either_order(tmp_path):
    for number, summary in enumerate(["A tested VOOL way to plan a week", "Tested-VOOL planner for a week"]):
        folder = tmp_path / str(number)
        folder.mkdir()
        findings, _ = found(folder, package(folder, metadata=info(summary=summary)))
        assert [item["rule"] for item in findings] == ["reserved-summary"], summary


@pytest.mark.parametrize("summary", [
    "Turn meeting notes into decisions", "Pick a recipe by what is in the fridge", "Test your flashcards before an exam",
    "Tidy an editor's notes into a style sheet", "Choose a gift from three choices", "Redaktionsplan für die Woche erstellen",
    "Use it with VOOL to plan a week"])
def test_ordinary_summaries_still_pass(tmp_path, summary):
    assert found(tmp_path, package(tmp_path, metadata=info(summary=summary)))[0] == []
    assert checks.badge_label(summary) is None


@pytest.mark.parametrize("name", ["Parcel desk", "Editorial calendar", "Recipe picker", "Wochenplan", "Тест формул"])
def test_ordinary_plugin_names_still_pass(name):
    manifest = {"name": "acme-parcel", "version": "1.0.0", "vool": {"id": "acme-parcel", "name": name, "publisher": "Acme"}}
    assert plugin_identity.identity_refusal(manifest) == ""
