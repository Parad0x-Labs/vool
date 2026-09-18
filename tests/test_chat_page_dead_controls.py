"""Guard: the /chat page must not claim a capability it does not have.

The Windows audit drove the page's real HTTP calls and found controls whose UI copy promised more
than the code delivers: thumbs that never leave the browser, a Projects sidebar with no server
route, a Stop button with no server-side cancel, paid-cloud strings that turn green on a saved key
even though no paid call can execute, and a Web0 header link wired to a flag nobody ever set.

Each test below pins the remediated wording or the removal, so the claim cannot come back.
"""

from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html
from tests.test_chat_visuals_browser import browser as browser

HTML = render_vool_chat_html()


# --------------------------------------------------------------------------- #
# Thumbs: no feedback route exists, so no rating control is offered
# --------------------------------------------------------------------------- #
def test_thumbs_rating_is_gone() -> None:
    # rateMsg wrote localStorage['vool_ratings'] and nothing in the repo ever read it back.
    assert "vool_ratings" not in HTML
    assert "function rateMsg" not in HTML
    assert "Good response" not in HTML
    assert "Bad response" not in HTML


def test_copy_action_survives() -> None:
    # Copy is a real control and must not be collateral damage of removing the thumbs.
    assert "msg-copy" in HTML
    assert "function copyText" in HTML


# --------------------------------------------------------------------------- #
# Projects: real grouping, but browser-local -- and it says so
# --------------------------------------------------------------------------- #
def test_chat_page_javascript_is_syntactically_valid() -> None:
    # The page is a large JS-in-Python blob; a stray syntax error silently breaks the WHOLE script
    # (empty sidebar, dead buttons). Node-check the emitted script so that can't ship. Skips where
    # node is unavailable (keeps CI-without-node green).
    import re
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        import pytest

        pytest.skip("node not available to syntax-check the page script")
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL), key=len, reverse=True)
    assert scripts, "no inline script found in the chat page"
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(scripts[0])
        path = handle.name
    result = subprocess.run([node, "--check", path], capture_output=True, text=True)
    assert result.returncode == 0, f"chat-page JS syntax error:\n{result.stderr}"


def test_projects_are_server_backed_and_isolating() -> None:
    # Projects were promoted from browser-only localStorage folders to a real server-backed feature:
    # a project is a folder, bindings persist, and a bound chat is isolated (files + memory).
    assert "createProjectFlow" in HTML  # server-backed create (pick a folder -> POST /api/projects)
    assert "/api/projects" in HTML and "refreshProjects" in HTML
    assert "Projects are saved in this browser only" not in HTML  # the old browser-only caveat is gone
    assert "stay isolated" in HTML  # the sidebar now states the isolation guarantee


# --------------------------------------------------------------------------- #
# Stop: truly cancels the in-flight turn server-side (turn_id-keyed), not just the display
# --------------------------------------------------------------------------- #
def test_stop_button_cancels_the_server_turn() -> None:
    assert ">Stop<" in HTML
    assert "/api/chat/cancel" in HTML          # the Stop button hits the server cancel endpoint
    assert "turn_id: run.turnId" in HTML        # keyed by the turn's own id
    # The old display-only copy is gone now that the turn is genuinely cancelled.
    assert "Stop showing" not in HTML
    assert "there is no server-side cancel" not in HTML


def test_stop_outcome_says_the_turn_was_cancelled() -> None:
    assert "cancelled the turn on your machine" in HTML
    assert "the turn still finished on your machine and counted toward usage" not in HTML


# --------------------------------------------------------------------------- #
# Paid cloud: explicit pins are real; Auto can never choose them
# --------------------------------------------------------------------------- #
def test_paid_copy_matches_the_owner_pick_reservation_path() -> None:
    assert "paid models spend your provider credits only when you explicitly select one" in HTML.lower()
    assert "spend caps" in HTML.lower()
    assert "paid models are gated" not in HTML.lower()
    assert "never charged" not in HTML.lower()


def test_no_fake_paid_presets_and_auto_is_free_only() -> None:
    assert 'data-model="openrouter-auto"' not in HTML
    assert 'data-model="openrouter-frontier"' not in HTML
    assert "Auto never selects a paid model" in HTML
    assert "m.free ? 'free · exact pin' : 'paid · exact pin'" in HTML


def test_settings_help_is_accurate_about_free_vs_paid_cloud() -> None:
    # Exact free/paid semantics and Auto's free-only rule are visible before a turn runs.
    assert "openrouter <code>:free</code> models cost nothing" in HTML.lower()
    assert "every turn runs locally" not in HTML.lower()
    assert "hard pin" in HTML.lower()
    assert "auto never selects paid" in HTML.lower()


# --------------------------------------------------------------------------- #
# Web0: the flag had no source of truth, so the permanently-hidden link is gone
# --------------------------------------------------------------------------- #
def test_dead_web0_flag_and_hidden_link_are_gone() -> None:
    assert "WEB0_ENABLED" not in HTML
    assert "__WEB0_ENABLED__" not in HTML
    assert 'id="web0Link"' not in HTML


def test_web0_and_trace_links_removed_from_settings() -> None:
    # Per product decision, the Diagnostics/trace + Web0 links are no longer shown in Settings (the
    # /trace and /web0 routes still exist server-side; they're just not surfaced here).
    assert 'href="/trace"' not in HTML
    assert 'href="/web0"' not in HTML
    assert "Diagnostics &amp; trace" not in HTML


def test_render_takes_no_unused_toggle() -> None:
    # render_vool_chat_html(web0_enabled=...) existed but every caller passed nothing.
    assert "web0_enabled" not in render_vool_chat_html.__code__.co_varnames


# Execute the shipped project flow with the bundled webview's absent text-prompt behavior.


def _project_flow_page(browser, picker):
    import json

    source = HTML[HTML.index('function _jget('):HTML.index('async function deleteProject(')]
    setup = '''
      window.posted = []; window.bound = []; window.legacyPrompts = 0;
      window.prompt = () => {window.legacyPrompts++; return null;};
      window.pywebview = PICKER;
      window.fetch = async (url, options) => {
        if (options && options.method === 'POST') window.posted.push(JSON.parse(options.body));
        return {ok:true,json:async()=>({project:{id:'created-project'},projects:[]})};
      };
      async function newChatInProject(id) {window.bound.push(['new',id]);}
      async function assignSession(sid,id) {window.bound.push([sid,id]);}
    '''.replace('PICKER', 'undefined' if picker is None else '{api:{pick_folder:async()=>('+json.dumps(picker)+')}}')
    page = browser.new_page()
    page.set_default_timeout(1500)
    page.set_content('<!doctype html><meta charset="utf-8"><button id="launch">Launch</button><script>'+setup+source+'</script>')
    assert page.evaluate('typeof createProjectFlow') == 'function'
    return page


def test_native_project_name_can_be_entered_without_window_prompt(browser):
    page = _project_flow_page(browser, {'ok': True, 'path': '/tmp/Harbor'})
    try:
        page.evaluate('() => { window.projectResult = createProjectFlow(); }')
        page.get_by_role('textbox', name='Project name:').fill('Harbor checks')
        page.get_by_role('button', name='OK', exact=True).click()
        assert page.evaluate('window.projectResult') == 'created-project'
        assert page.evaluate('window.posted') == [{'name': 'Harbor checks', 'root': '/tmp/Harbor'}]
        assert page.evaluate('window.bound') == [['new', 'created-project']]
        assert page.evaluate('window.legacyPrompts') == 0
    finally:
        page.close()


def test_browser_project_path_and_unicode_name_preserve_assignment(browser):
    page = _project_flow_page(browser, None)
    try:
        page.evaluate('() => { window.projectResult = createProjectFlow("existing-chat"); }')
        page.get_by_role('textbox', name='Project folder').fill('/tmp/Šiauliai')
        page.get_by_role('button', name='OK', exact=True).click()
        page.get_by_role('textbox', name='Project name:').fill('Orchard — 927')
        page.get_by_role('textbox', name='Project name:').press('Enter')
        assert page.evaluate('window.projectResult') == 'created-project'
        assert page.evaluate('window.posted') == [{'name': 'Orchard — 927', 'root': '/tmp/Šiauliai'}]
        assert page.evaluate('window.bound') == [['existing-chat', 'created-project']]
        assert page.evaluate('window.legacyPrompts') == 0
    finally:
        page.close()


def test_cancelled_native_folder_picker_does_not_start_a_second_prompt(browser):
    page = _project_flow_page(browser, {'ok': False, 'cancelled': True})
    try:
        assert page.evaluate('createProjectFlow()') is None
        assert page.evaluate('window.posted') == []
        assert page.evaluate('window.bound') == []
        assert page.evaluate('window.legacyPrompts') == 0
        assert page.get_by_role('dialog').count() == 0
    finally:
        page.close()


def test_cancelled_project_name_has_no_server_effect(browser):
    page = _project_flow_page(browser, {'ok': True, 'path': '/tmp/Harbor'})
    try:
        page.evaluate('() => { window.projectResult = createProjectFlow(); }')
        page.get_by_role('textbox', name='Project name:').press('Escape')
        assert page.evaluate('window.projectResult') is None
        assert page.evaluate('window.posted') == []
        assert page.evaluate('window.bound') == []
    finally:
        page.close()
