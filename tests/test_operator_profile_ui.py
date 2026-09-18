"""Operator Profile — the served chat page: the old field is gone, the new surfaces exist.

The page is a Python module rendering HTML + inline JS. The section test reads the served HTML;
the behaviour tests execute the page's own JS under node (the shared harness), so the chip and the
folded indicator are rendered by the real functions, not re-implemented here.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.chat_page_js_harness import DOM, HTML, script, slice_source
from tests.test_chat_visuals_browser import browser as browser


def test_old_address_field_is_removed_not_hidden():
    assert 'id="setName"' not in HTML
    assert "how VOOL addresses you" not in HTML
    assert "Your name or handle" not in HTML
    assert "What VOOL remembers about you" in HTML
    assert 'id="profileList"' in HTML
    for control in ("Edit", "Forget", "Move", "Pause memory", "Export", "Restore previous"):
        assert control in HTML, control


def test_settings_save_no_longer_posts_user_address():
    source = script()
    save_fn = slice_source(source, "async function savePrefs()", "\n}\n")
    assert "user_address" not in save_fn


_WALK = """
const buttonsOf = (el) => { const out = []; const walk = (n) => { if (!n) return; if (n.tagName === 'BUTTON') out.push(n.textContent); (n.children || []).forEach(walk); }; walk(el); return out; };
const findClass = (el, cls) => { let hit = null; const walk = (n) => { if (!n || hit) return; if (String(n.className || '').split(/\\s+/).indexOf(cls) >= 0) { hit = n; return; } (n.children || []).forEach(walk); }; walk(el); return hit; };
"""


def _run(program: str) -> dict:
    """Execute under node and return the FIRST JSON line that carries a ``text`` or ``summary``
    key (the harness prints its own boot report last)."""
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat page script")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(DOM + "\n" + _WALK + "\n" + program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=90)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr
    for line in result.stdout.strip().splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict) and ("text" in parsed or "summary" in parsed):
            return parsed
    raise AssertionError(f"no result line in:\n{result.stdout}")


def test_candidate_chip_renders_with_three_actions():
    source = script()
    fn = slice_source(source, "function renderProfileChip(", "\n}\n") + "\n}\n"
    program = fn + """
const host = document.createElement('div');
const chip = renderProfileChip(host, {candidate_id: 'opi-1', text: 'Remember: address you as Alex', actions: ['save','edit','only_this_chat']});
console.log(JSON.stringify({text: findClass(chip, 'pchip-text').textContent, buttons: buttonsOf(chip)}));
"""
    out = _run(program)
    assert out["text"] == "Remember: address you as Alex"
    assert out["buttons"] == ["Save", "Edit", "Only this chat"]


def test_used_preferences_indicator_is_folded_and_counts():
    source = script()
    fn = slice_source(source, "function renderProfileUsed(", "\n}\n") + "\n}\n"
    program = fn + """
const host = document.createElement('div');
const el = renderProfileUsed(host, [{label:'preferred name'},{label:'work inbox'},{label:'concise replies'}]);
console.log(JSON.stringify({summary: el.querySelector('summary').textContent, open: !!el.open}));
"""
    out = _run(program)
    assert out["summary"] == "Used 3 preferences: preferred name, work inbox, concise replies"
    assert out["open"] is False


def test_explicit_save_confirmation_is_non_blocking():
    source = script()
    fn = slice_source(source, "function renderProfileSaved(", "\n}\n") + "\n}\n"
    program = fn + """
const host = document.createElement('div');
const el = renderProfileSaved(host, [{report: 'Saved to your profile: preferred name -> Alex (global).', item_id: 'opi-1'}]);
console.log(JSON.stringify({text: el.textContent, cls: el.className, undo: buttonsOf(el)}));
"""
    out = _run(program)
    assert "Saved to your profile" in out["text"]
    assert "pconfirm" in out["cls"]
    assert out["undo"] == ["Undo"]
    assert not re.search(r"window\.confirm|alert\(", slice_source(script(), "function renderProfileSaved(", "\n}\n"))


def test_profile_frame_is_consumed_from_the_stream():
    source = script()
    assert "obj.vool_profile" in source
    assert "applyProfileFrame(" in source
    assert "'/api/profile/'" in source
    assert "profilePost(route" in source


@pytest.mark.parametrize('kind', ['candidate', 'item', 'scope'])
@pytest.mark.parametrize('cancel', [False, True])
def test_profile_text_entry_keeps_revision_and_cancel_authority(browser, kind, cancel):
    prompt_source = HTML[HTML.index('function requestTextInput('):HTML.index('async function nativePickFolder(')]
    item_source = HTML[HTML.index('function renderProfileItem('):HTML.index('function _explainCloudError(')]
    item = {'item_id': 'synthetic-item', 'status': 'candidate' if kind == 'candidate' else 'active',
            'label': 'Nickname', 'value_text': 'Initial <b>value</b>', 'revision': 7,
            'scope': 'global', 'actions': ['edit']}
    setup = """
      const displayedChat = 'chosen-chat'; window.calls = [];
      async function profilePost(route, body) {window.calls.push({route,body});return {ok:true,status:200,data:{}};}
      function profileStatus(){} function loadProfile(){}
      window.prompt = () => {throw new Error('unsupported native prompt');};
    """
    page = browser.new_page()
    try:
        page.set_content('<!doctype html><meta charset="utf-8"><body><script>' + setup + prompt_source + item_source
                         + 'document.body.appendChild(renderProfileItem(' + json.dumps(item) + ', {}));</script>')
        page.get_by_role('button', name='Move scope' if kind == 'scope' else 'Edit', exact=True).click()
        field = page.get_by_role('textbox')
        assert field.input_value() == ('global' if kind == 'scope' else item['value_text'])
        if cancel:
            field.press('Escape')
            assert page.evaluate('window.calls') == []
        else:
            value = 'chat' if kind == 'scope' else 'Šiauliai <img src=x onerror=alert(1)>'
            field.fill(value)
            page.get_by_role('button', name='OK', exact=True).click()
            calls = page.evaluate('JSON.parse(JSON.stringify(window.calls))')
            expected = {'candidate_id': 'synthetic-item', 'action': 'edit', 'value': value} if kind == 'candidate' else (
                {'item_id': 'synthetic-item', 'scope': 'chat', 'scope_key': 'chosen-chat', 'expected_revision': 7}
                if kind == 'scope' else {'item_id': 'synthetic-item', 'value': value, 'expected_revision': 7})
            assert calls == [{'route': 'candidate' if kind == 'candidate' else kind, 'body': expected}]
            assert page.locator('img').count() == 0
        assert page.get_by_role('dialog').count() == 0
    finally:
        page.close()
