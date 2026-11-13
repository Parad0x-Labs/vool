"""The cards for what comes with VOOL read in the person's language.

My add-ons printed the built-in cards' name, description and category, the "13 skills" count and
the "nothing added yet" line in the server's English, whatever language the app was set to. Those
words now come from the catalog, keyed by the plugin's id, with the server's English as the
fallback:

- the catalog's English is the server's English, so a reworded card cannot keep showing an old
  translation;
- every app language carries its own words for each key;
- the real page script, run under node with the real German and Arabic bundles, shows those words
  on the built-in cards and on the group name each built-in skill carries under Skills, and a
  plugin someone installed keeps its maker's own words, even one that reuses a built-in id.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core import plugin_catalog
from core.i18n.catalog import CATALOGS_DIR
from core.i18n.page_bundle import i18n_bootstrap_js
from core.vool_chat_page import render_vool_chat_html

ROOT = Path(__file__).resolve().parents[1]
KEYS = (
    "plugins.builtin.native_library.name",
    "plugins.builtin.native_library.description",
    "plugins.builtin.vool_database.name",
    "plugins.builtin.vool_database.description",
    "plugins.builtin.vool_database.category",
    "plugins.status.skill_count",
    "plugins.storage.missing",
)


def _messages(locale: str) -> dict[str, str]:
    return json.loads((CATALOGS_DIR / f"{locale}.json").read_text(encoding="utf-8"))["messages"]


def _database_interface() -> dict[str, str]:
    manifest = json.loads((ROOT / "plugins/vool-database/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    return manifest["interface"]


def test_the_catalog_english_is_the_cards_english() -> None:
    en = _messages("en")
    native = plugin_catalog._native_library_entry()
    assert native is not None
    assert en["plugins.builtin.native_library.name"] == native["name"]
    assert en["plugins.builtin.native_library.description"] == native["description"]
    assert en["plugins.status.built_in"] == native["category"]
    interface = _database_interface()
    assert en["plugins.builtin.vool_database.name"] == interface["displayName"]
    assert en["plugins.builtin.vool_database.description"] == interface["shortDescription"]
    assert en["plugins.builtin.vool_database.category"] == interface["category"]
    assert en["plugins.storage.missing"] == plugin_catalog._reason_for(plugin_catalog.STORAGE_MISSING, "", "", None)
    assert en["plugins.status.skill_count"] == "{n, plural, one {{n} skill} other {{n} skills}}"


def test_every_app_language_has_its_own_words() -> None:
    en = _messages("en")
    locales = sorted(path.stem for path in CATALOGS_DIR.glob("*.json") if path.stem != "en")
    assert len(locales) == 16
    for locale in locales:
        messages = _messages(locale)
        for key in KEYS:
            value = messages.get(key, "")
            assert value and value != en[key], (locale, key)
        assert "VOOL" in messages["plugins.builtin.vool_database.name"], locale
        assert "other {" in messages["plugins.status.skill_count"], locale


_DOM = """
function el(tag) {
  return { tag: tag, children: [], className: '', _text: '', attrs: {}, style: {},
    appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, setAttribute(k, v) { this.attrs[k] = v; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; } };
}
const body = el('div');
globalThis.window = globalThis;
globalThis.document = { createElement: el, getElementById: (id) => (id === 'pluginsBody' ? body : null),
  readyState: 'complete', querySelectorAll: () => [], addEventListener() {} };
globalThis._firstPartyBadge = function () { return el('span'); };
globalThis.togglePlugin = function () {};
"""

_READ = """
const cards = {};
let notice = '';
for (const node of body.children) {
  if (node.className.indexOf('plugin-storage-notice') !== -1) notice = node.children[0].textContent;
  if (node.className.indexOf('plugin-card') === -1) continue;
  const card = { name: '', category: '', from: '', description: '', status: '', toggle: '' };
  for (const part of node.children) {
    if (part.className === 'p-head') {
      for (const bit of part.children) {
        if (bit.className === 'p-name') card.name = bit.textContent;
        if (bit.className === 'p-cat') { card.category = bit.textContent; card.from = bit.title || ''; }
      }
    }
    if (part.className === 'p-desc') card.description = part.textContent;
    if (part.className === 'p-actions') {
      card.status = part.children[0].textContent;
      const toggle = part.children.find((c) => c.className.indexOf('p-toggle') !== -1);
      card.toggle = toggle ? toggle.attrs['aria-label'] : '';
    }
  }
  cards[card.name + (card.from ? ' / ' + card.description : '')] = card;
}
console.log(JSON.stringify({ cards: cards, notice: notice }));
"""

_NATIVE = {"id": "native-library", "name": "Built-in skills", "version": "1.0.0", "category": "Built in",
           "description": "The skills that come with VOOL. Turn any one of them on or off under Skills.",
           "first_party": True, "enabled": True, "skills": [{"id": f"s{i}"} for i in range(13)]}


def _database(**extra) -> dict:
    interface = _database_interface()
    entry = {"id": "vool-database", "name": interface["displayName"], "version": "0.1.0",
             "category": interface["category"], "description": interface["shortDescription"],
             "first_party": True, "enabled": True, "skills": []}
    entry.update(extra)
    return entry


def _page(locale: str, plugins: list[dict], tab: str = "plugins") -> dict:
    """Run the served page's own storage notice and renderPlugins (or renderSkills) with the real
    `locale` bundle."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat page script")
    html = render_vool_chat_html()
    pieces = [html[html.index("function pageT(key, fallback) {"):html.index("const queueEl = document.getElementById('queue');")],
              html[html.index("function _skillTitle(name) {"):html.index("async function togglePlugin(")]]
    if tab == "fresh-skills":
        call = ("renderSkills({ installed: false, storage: { state: 'missing' }, reason: "
                + json.dumps(plugin_catalog._reason_for(plugin_catalog.STORAGE_MISSING, "", "", None))
                + ", plugin_count: 0, skill_count: 0, plugins: " + json.dumps(plugins) + " }, '');\n")
    elif tab == "skills":
        call = ("renderSkills({ installed: true, storage: { state: 'accessible' }, plugin_count: 2, skill_count: "
                + str(sum(len(p["skills"]) for p in plugins)) + ", plugins: " + json.dumps(plugins) + " }, '');\n")
    else:
        call = ("renderPlugins({ installed: false, storage: { state: 'missing' }, reason: "
                + json.dumps(plugin_catalog._reason_for(plugin_catalog.STORAGE_MISSING, "", "", None))
                + ", plugin_count: 0, skill_count: 0, plugins: " + json.dumps(plugins) + " }, '');\n")
    program = _DOM + i18n_bootstrap_js(locale) + "\n" + "\n".join(pieces) + "\n" + call + _READ
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"the page script failed under node:\n{result.stderr[:1200]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("locale", ["de", "ar"])
def test_the_built_in_cards_read_in_the_app_language(locale: str) -> None:
    words = _messages(locale)
    seen = _page(locale, [_NATIVE, _database()])
    native = seen["cards"][words["plugins.builtin.native_library.name"]]
    assert native["description"] == words["plugins.builtin.native_library.description"]
    assert native["category"] == words["plugins.status.built_in"]
    assert words["plugins.status.built_in"] in native["status"] and "13" in native["status"]
    assert " skills" not in native["status"]
    database = seen["cards"][words["plugins.builtin.vool_database.name"]]
    assert database["description"] == words["plugins.builtin.vool_database.description"]
    assert database["category"] == words["plugins.builtin.vool_database.category"]
    assert database["toggle"].endswith(words["plugins.builtin.vool_database.name"])
    assert seen["notice"] == words["plugins.storage.missing"]
    # No English card words are left on either card.
    for english in ("Built-in skills", "VOOL Database", "The skills that come with VOOL", "Lets VOOL keep"):
        assert english not in json.dumps(seen, ensure_ascii=False)


def test_english_reads_exactly_as_the_server_wrote_it() -> None:
    seen = _page("en", [_NATIVE, _database()])
    assert seen["cards"]["Built-in skills"]["description"] == _NATIVE["description"]
    assert seen["cards"]["Built-in skills"]["status"].endswith(" · 13 skills")
    assert seen["cards"]["VOOL Database"]["category"] == "Data"
    assert seen["notice"] == plugin_catalog._reason_for(plugin_catalog.STORAGE_MISSING, "", "", None)


def test_a_plugin_someone_installed_keeps_its_makers_words() -> None:
    """Only a card that comes with VOOL is translated; an installed package that reuses a
    built-in id is not VOOL's, so it shows exactly what its maker wrote."""
    words = _messages("de")
    lookalike = _database(first_party=False, origin="installed", name="My Database",
                          description="Someone else's words.", category="Tools")
    seen = _page("de", [lookalike])
    card = seen["cards"]["My Database"]
    assert card["description"] == "Someone else's words." and card["category"] == "Tools"
    assert words["plugins.builtin.vool_database.name"] not in json.dumps(seen, ensure_ascii=False)


_SKILL = {"name": "vool-answer-presentation", "summary": "Picks the clearest shape for an answer.", "enabled": True}


@pytest.mark.parametrize("locale", ["de", "ar"])
def test_each_built_in_skill_names_its_group_in_the_app_language(locale: str) -> None:
    words = _messages(locale)
    native = dict(_NATIVE, skills=[_SKILL])
    mine = _database(first_party=False, origin="installed", name="My Database",
                     skills=[{"name": "my-lookup", "summary": "Looks things up.", "enabled": True}])
    seen = _page(locale, [native, mine], tab="skills")
    by_skill = {card["description"]: card for card in seen["cards"].values() if card["from"]}
    built_in = by_skill["Picks the clearest shape for an answer."]
    group = words["plugins.builtin.native_library.name"]
    assert built_in["category"] == group
    assert built_in["from"] == words["skills.from_plugin_title"].replace("{plugin}", group)
    # A package someone installed keeps its maker's own name, even under a built-in id.
    assert by_skill["Looks things up."]["category"] == "My Database"
    assert "Built-in skills" not in json.dumps(seen, ensure_ascii=False)


def test_a_fresh_install_lists_the_built_in_skills_under_skills() -> None:
    """The Built-in skills card says to turn single skills on or off under Skills, so before
    anything is added that tab lists them, under the "nothing added yet" note."""
    words = _messages("de")
    seen = _page("de", [dict(_NATIVE, skills=[_SKILL])], tab="fresh-skills")
    assert seen["notice"] == words["plugins.storage.missing"]
    by_skill = {card["description"]: card for card in seen["cards"].values() if card["from"]}
    card = by_skill["Picks the clearest shape for an answer."]
    assert card["category"] == words["plugins.builtin.native_library.name"]
    assert card["status"] == words["plugins.status.on"] and card["toggle"].startswith(words["plugins.disable"])
