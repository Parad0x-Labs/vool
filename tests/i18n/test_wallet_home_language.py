"""The wallet home speaks the app's language (core/wallet_fragment.py).

That covers the shared credential fields and the one-time backup dialog the home's create flow opens.

Every text the wallet home draws goes through WT(key, english) or WTF(key, english,
params): the page bundle supplies the locale's text and the English argument is what
a page without a bundle shows. These laws keep the two halves joined:

- every key the fragment asks for exists in the English catalog, so no locale can
  fall back to a key the catalog never carried;
- the English the fragment carries is byte-for-byte the catalog's English, so the
  English page and the translations translate the same words;
- every wallet home key in the catalog is still used by the fragment (no orphans
  that translators would keep paying for);
- a WT text (no params) carries no {placeholder}, so nothing ever shows a raw brace;
- every locale carries every wallet home key with the same placeholders.
"""

from __future__ import annotations

import json
import re

from core.i18n.catalog import CATALOGS_DIR
from core.wallet_fragment import render_wallet_fragment

CALL = re.compile(r"\b(WTF?)\(\s*'([a-z0-9_.]+)'\s*,\s*'((?:[^'\\]|\\.)*)'")
PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
HOME_PREFIXES = ("wallet.home.", "wallet.reason.", "wallet.setup_state.", "wallet.cred.", "wallet.backup.")


def _catalog(locale: str) -> dict[str, str]:
    return json.loads((CATALOGS_DIR / f"{locale}.json").read_text(encoding="utf-8"))["messages"]


def _calls() -> list[tuple[str, str, str]]:
    calls = [(fn, key, re.sub(r"\\(.)", r"\1", text)) for fn, key, text in CALL.findall(render_wallet_fragment())]
    assert len(calls) > 150, "the wallet home should draw its texts through WT/WTF"
    return calls


def test_every_fragment_key_is_in_the_english_catalog_with_the_same_english():
    en = _catalog("en")
    missing = sorted({key for _, key, _ in _calls() if key not in en})
    assert missing == []
    drift = sorted({key for _, key, text in _calls() if en[key] != text})
    assert drift == [], "fragment English and catalog English differ"


def test_no_wallet_home_key_is_orphaned():
    used = {key for _, key, _ in _calls()}
    orphans = sorted(k for k in _catalog("en") if k.startswith(HOME_PREFIXES) and k not in used)
    assert orphans == []


def test_plain_wt_texts_carry_no_placeholder():
    en = _catalog("en")
    raw = sorted({key for fn, key, _ in _calls() if fn == "WT" and PLACEHOLDER.search(en[key])})
    assert raw == []


def test_every_locale_carries_every_wallet_home_key_with_the_same_placeholders():
    en = _catalog("en")
    home = [k for k in en if k.startswith(HOME_PREFIXES)]
    assert len(home) > 150
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        locale = path.stem
        if locale == "en":
            continue
        messages = _catalog(locale)
        for key in home:
            assert key in messages, f"{locale}: {key} missing"
            assert set(PLACEHOLDER.findall(messages[key])) == set(PLACEHOLDER.findall(en[key])), f"{locale}: {key}"
