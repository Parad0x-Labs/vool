"""The locale-fixes laws (0.7.7 job locale-fixes-077-20261031, ported to main 411efdb2).

The translation check (locale-check-077-20261030) read the 0.7.7 words in all 16
locales against the English and produced 182 finding rows; that job applied 152 of
them as key-locale edits on its base. Ported here, only the rows whose key and
whose English this base carries are pinned (the port note above APPLIED lists what
stayed behind and why). Guards in this file answer one concrete question each:

- every applied row's locale value equals the fixed text that was handed over, so a
  later merge cannot silently drop a fix (APPLIED);
- the de keys this port touches read du like the rest of the de catalog: none of
  them still carries a Sie/Ihr address form. The capital "Sie" that remains is the
  pronoun "it" standing for a feminine noun ("die Menge ... Sie stoppt"), pinned to
  its exact sentence so the exception cannot hide a rewrite;
- every keys-pointer fix contains the locale's CURRENT settings.group.keys.title
  ("API Keys" again on this base — the findings were written against "Your keys");
- the English the retranslated keys translate is still the English this base ships;
  if English moves again the fixed texts need re-reading, so the guard fails loudly
  instead of letting drift hide.
"""

from __future__ import annotations

import json
import re

from core.i18n.catalog import CATALOGS_DIR

ADDR_FORM = re.compile(r"\b(Sie|Ihnen|Ihr|Ihre|Ihrem|Ihren|Ihrer)\b")

# capital "Sie" that is the pronoun for a feminine noun, not the address form
NON_ADDRESS_SIE = {"settings.row.daily_token_budget.help": "Sie stoppt keine Antworten", "phone.mobile.unknown": "Sie wurde möglicherweise bereits gesendet"}

# Ported to main 411efdb2 (delivery/port-078-cal-home-locale-20261101): the guards and their
# logic are unchanged from the 0.7.7 test; the pinned rows keep only the key-locale pairs this
# base carries — rows for keys main does not have (0.7.7-added keys) and rows whose text main
# already moved (main's own wording stands) are not pinned here. See the mission HANDBACK.md.
APPLIED = [["settings.models.go_keys", "de", "Zu „API-Schlüssel“"], ["settings.models.go_keys", "hi", "“API कुंजियाँ” पर जाएँ"], ["settings.models.local_none", "de", "Noch kein lokales Modell. Installiere eines, oder füge unter „API-Schlüssel“ einen Schlüssel hinzu, um ein Online-Modell zu nutzen."], ["settings.models.local_none", "hi", "अभी कोई स्थानीय मॉडल नहीं। एक इंस्टॉल करें, या ऑनलाइन मॉडल इस्तेमाल करने के लिए “API कुंजियाँ” में कुंजी जोड़ें।"], ["settings.models.need_key", "de", "Füge unter „API-Schlüssel“ einen Schlüssel hinzu, um hier ein Modell zu wählen."], ["settings.models.need_key", "hi", "यहाँ मॉडल चुनने के लिए “API कुंजियाँ” में कुंजी जोड़ें।"], ["setup.step.thinking.later", "lt", "Nustatymai → Modeliai ir tiekėjai, bei Nustatymai → API raktai"], ["setup.step.thinking.later", "pt", "Configurações → Modelos e provedores, e Configurações → Chaves de API"]]

DE_DU_KEYS = ["settings.row.daily_token_budget.help"]

POINTER_ROWS = [["settings.models.go_keys", "de"], ["settings.models.go_keys", "hi"], ["settings.models.local_none", "de"], ["settings.models.local_none", "hi"], ["settings.models.need_key", "de"], ["settings.models.need_key", "hi"], ["setup.step.thinking.later", "lt"], ["setup.step.thinking.later", "pt"]]

STALE_KEY_ENGLISH = {"settings.models.go_keys": "Go to API Keys", "settings.models.local_none": "No local model yet. Install one, or add a key in API Keys to use an online model.", "settings.models.need_key": "Add a key in API Keys to choose a model here.", "setup.step.thinking.later": "Settings → Models & Providers, and Settings → API Keys"}


def _messages(locale):
    return json.loads((CATALOGS_DIR / (locale + ".json")).read_text(encoding="utf-8"))["messages"]


def test_applied_rows_equal_the_handed_over_fixed_text():
    for key, locale, expected in APPLIED:
        got = _messages(locale).get(key)
        assert got == expected, (locale, key)


def test_de_new_keys_use_du_not_the_address_form():
    de = _messages("de")
    for key in DE_DU_KEYS:
        hits = ADDR_FORM.findall(de[key])
        if key in NON_ADDRESS_SIE and NON_ADDRESS_SIE[key] in de[key]:
            continue
        assert not hits, (key, hits)


def test_keys_pointers_use_the_current_group_title():
    for key, locale in POINTER_ROWS:
        msgs = _messages(locale)
        title = msgs["settings.group.keys.title"]
        assert title in msgs[key], (locale, key, title)


def test_the_retranslated_stale_keys_still_translate_this_english():
    en = _messages("en")
    for key, english in STALE_KEY_ENGLISH.items():
        assert en[key] == english, key
