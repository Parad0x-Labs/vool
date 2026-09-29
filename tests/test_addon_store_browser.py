"""Full served pages + real add-on dispatch/lifecycle; scanner/network are fixtures.

Unrelated chat polling endpoints are stubbed. No provider inference or owner profile.
"""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from tests.test_addon_discovery import source
from tests.test_addon_store import rig


@pytest.mark.parametrize('risk', [False, True, 'bundled', 'delayed-risk'])
def test_discover_scan_review_install_reopen_and_security(rig, source, monkeypatch, tmp_path, risk):
    from core import addon_store, credential_store
    from core.vool_chat_page import render_vool_chat_html
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    monkeypatch.setattr(credential_store, "credential_is_indexed", lambda name: True)
    bundled = risk == 'bundled'
    delayed_catalog = risk == 'delayed-risk'
    risk = risk is True or delayed_catalog
    release_catalog = threading.Event()
    if bundled:
        from core import addon_catalog, eyebrow_client
        from tests.test_addon_store import report
        actual = copy.deepcopy(addon_catalog.CATALOG[0])
        unsupported = copy.deepcopy(next(e for e in addon_catalog.CATALOG if e.get('import_ready') is False))
        unsupported['name'] = 'Unsupported example'
        monkeypatch.setattr(addon_store, 'CATALOG', [actual, unsupported])
        monkeypatch.setattr(addon_store, 'fetch_bytes', lambda *a, **kw: pytest.fail('Bundled workflow must not download'))
        def bundled_scan(endpoint, payload=None):
            rig[2].append((endpoint,payload))
            value = report(); value['artifacts'][0]['name'] = actual['skill_name']
            return value, '18'
        monkeypatch.setattr(eyebrow_client, 'request_api', bundled_scan)
    if risk:
        from core import eyebrow_client
        from tests.test_addon_store import report
        value = report()
        value['verdict'] = 'fail'
        value['findings'] = [
            {'ruleId':'PROMPT-INJECTION', 'severity':'high', 'message':'Fixture finding'},
            {'ruleId':'SENSITIVE-PATH-READ', 'severity':'high', 'message':'Sensitive path fixture'},
            {'ruleId':'FUTURE-RULE', 'severity':'medium', 'message':'Unknown rule fixture'},
        ]
        value['policy']['violations'] = [{'kind':'high-severity'}]
        def scan_reply(endpoint, payload=None):
            rig[2].append((endpoint,payload))
            return copy.deepcopy(value), '18'
        monkeypatch.setattr(eyebrow_client,'request_api',scan_reply)
    runtime = RuntimeServices(display_name="VOOL")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, code, data, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/chat":
                return self.send(200, render_vool_chat_html().encode(), "text/html")
            if path == "/settings":
                return self.send(200, render_vool_settings_html().encode(), "text/html")
            if path in {"/api/addons", "/api/plugins", "/api/intake/providers"}:
                result = dispatch_get(path=path, query={}, runtime=runtime, model_name="fixture", client_host="127.0.0.1")
                if delayed_catalog and path == '/api/plugins':
                    assert release_catalog.wait(15), 'browser never released the held catalogue'
                return self.send(result.status, result.body)
            return self.send(200, b'{}')
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b'{}')
            if self.path != "/api/addons":
                return self.send(200, b'{}')
            calls.append(body.get("action"))
            result = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=runtime,
                                   model_name="fixture", workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1")
            return self.send(result.status, result.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        if delayed_catalog:
            # Mark the browser frame after the add-on JSON is consumed, while the
            # installed-plugin response remains held by the served handler.
            page.add_init_script('''
                const actualFetch=window.fetch;
                window.fetch=async (...args)=>{
                    const response=await actualFetch(...args);
                    if(args[0]==='/api/addons' && (!args[1] || !args[1].method)){
                        const read=response.json.bind(response);
                        response.json=async ()=>{const value=await read();requestAnimationFrame(()=>{window.catalogConsumed=true;});return value;};
                    }
                    return response;
                };
            ''')
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator('#input').fill('Keep this unsent draft')
        page.locator('#homeToggle').click()
        assert page.locator('#homeOptions').get_by_role('button', name='Skills & Plugins', exact=True).count() == 1
        assert page.locator('#skillsBtn').count() == 0
        page.get_by_role('button', name='Skills & Plugins', exact=True).click()
        if delayed_catalog:
            page.wait_for_function('window.catalogConsumed === true')
            assert page.get_by_role('button', name='View add-on', exact=True).count() == 0
            assert page.locator('#pluginsBody').inner_text() == 'Loading…'
            assert not calls, 'partial hydration must not expose a review or installation decision'
            release_catalog.set()
        assert page.locator('#pluginsTitle').inner_text() == 'Skills & Plugins'
        page.locator('#addonNav').get_by_role('button', name='Browse', exact=True).click()
        page.get_by_role('button', name='View add-on').wait_for()
        if bundled:
            assert page.locator('#pluginsBody .addon-card').count() == 1
            page.get_by_role('button',name='All listed skills',exact=True).click()
            page.locator('.addon-card').filter(has_text='Unsupported example').get_by_role('button',name='View add-on').click()
            assert page.get_by_text('Not supported yet',exact=True).is_visible()
            assert page.get_by_role('button',name='Check compatibility',exact=True).count() == 0
            assert not calls and not rig[2]
            page.get_by_role('button',name='← Back to list',exact=True).click()
            page.get_by_role('button',name='Compatible with VOOL',exact=True).click()
        page.locator('#pluginsSearch').fill('FRONTEND_design')
        assert page.locator('#pluginsBody .addon-card').count() == 1
        page.get_by_role('button', name='Coding', exact=True).click()
        page.get_by_role('button', name='View add-on').click()
        page.get_by_role('button', name='Scan with Eyebrow' if bundled else 'Download and scan').click()
        assert not rig[2], 'choosing an entry must not silently consume a scan'
        page.get_by_role('button', name='Run one scan').click()
        page.get_by_role('heading',name='Eyebrow security report',exact=True).wait_for()
        assert page.get_by_role('heading',name='VOOL installation checks',exact=True).is_visible()
        if risk:
            # Plain-language help is local disclosure, never an installation decision.
            help_cards = page.locator('.addon-risk-explanation')
            assert help_cards.count() == 3
            assert page.locator('.addon-risk-explanation[open]').count() == 0
            before_help = calls.copy()
            for i, consequence in enumerate(('sending unwanted messages', 'access your accounts', 'no specific explanation')):
                card = help_cards.nth(i)
                card.get_by_text('What could go wrong?', exact=True).click()
                assert card.get_by_text('VOOL explanation · possible outcomes, not a prediction', exact=True).is_visible()
                assert consequence in card.inner_text()
                assert 'not proof of malicious intent' in card.inner_text()
                assert 'Existing permissions still apply' in card.inner_text()
                card.get_by_text('What could go wrong?', exact=True).click()
            assert calls == before_help, 'Reading risk help cannot scan, install or acknowledge risk'
            assert page.get_by_text('Eyebrow verdict: FAIL', exact=True).is_visible()
            page.get_by_text('Advanced: install despite findings',exact=True).click()
            assert not page.get_by_role('checkbox',name='I reviewed the Eyebrow findings and accept the risks of enabling this exact version.',exact=True).is_checked()
            assert not page.get_by_role('button',name='Accept risks and enable this version').is_enabled()
            page.screenshot(path=str(tmp_path/'eyebrow-attribution-risk-review.png'))
            page.get_by_role('checkbox',name='I reviewed the Eyebrow findings and accept the risks of enabling this exact version.',exact=True).check()
            assert page.get_by_role('button',name='Accept risks and enable this version').is_enabled()
            assert calls.count('install')==0
        assert calls.count('scan') == 1
        if bundled:
            # Local-check lookups are read-only saved-report reads; the first store
            # review still makes no compatibility, GitHub, install or local-scan call.
            assert set(calls) <= {'scan', 'local_report'}, 'First store review must not make compatibility or GitHub requests'
            assert calls.count('scan') == 1
            assert not rig[1], 'Bundled files need no download'
        assert not addon_store.catalog()['entries'][0]['installed']
        page.get_by_text('View scan report', exact=True).click()
        # Long reports must never scroll the only return action out of reach.
        page.set_viewport_size({'width': 620, 'height': 640})
        page.locator('#pluginsBody').evaluate('(el) => { el.scrollTop = el.scrollHeight; }')
        back = page.get_by_role('button', name='← Back to list', exact=True)
        assert back.evaluate('''el => {
            const r=el.getBoundingClientRect();
            const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
            return r.width>0 && r.height>0 && !!hit && (hit===el || el.contains(hit));
        }'''), 'Back must remain visible after the first scan, including at the bottom of a report'
        back.click()
        assert page.locator('#pluginsSearch').input_value() == 'FRONTEND_design'
        assert page.locator('#pluginsBody .addon-card').count() == 1
        page.get_by_text('Saved scans to review (1)', exact=True).click()
        page.get_by_role('button', name='Review scan', exact=True).click()
        assert calls.count('scan') == 1, 'returning to the list must keep the saved review'
        assert calls.count('install') == 0, 'navigation cannot accept a review'
        page.set_viewport_size({'width': 1280, 'height': 960})
        # A reload loses all JS state: the signed pending review must survive, without a second scan.
        page.reload(wait_until='domcontentloaded')
        page.evaluate('window.VoolPageActions.openSkills()')
        page.get_by_text('Saved scans to review (1)', exact=True).click()
        page.get_by_role('button', name='Review scan', exact=True).click()
        page.get_by_role('heading',name='Eyebrow security report',exact=True).wait_for()
        assert calls.count('scan') == 1
        page.locator('#input').fill('Keep this unsent draft')
        if risk:
            page.get_by_text('Advanced: install despite findings',exact=True).click()
            assert not page.get_by_role('button',name='Accept risks and enable this version').is_enabled()
            page.get_by_role('checkbox',name='I reviewed the Eyebrow findings and accept the risks of enabling this exact version.',exact=True).check()
            page.get_by_role('button',name='Accept risks and enable this version').click()
        else:
            page.get_by_role('button', name='Accept and enable').click()
        decision_label='Risks accepted by you' if risk else 'Checked by Eyebrow'
        page.get_by_text(decision_label, exact=True).wait_for()
        assert calls.count('install') == 1
        assert addon_store.catalog()['entries'][0]['enabled']
        page.locator('#pluginsClose').click()
        assert page.locator('#input').input_value() == 'Keep this unsent draft'
        page.evaluate('window.VoolPageActions.openSkills()')
        page.get_by_text(decision_label, exact=True).wait_for()
        page.screenshot(path=str(tmp_path / 'discover-installed.png'))
        page.locator('#addonNav').get_by_role('button', name='My add-ons', exact=True).click()
        page.get_by_role('button', name='Plugins', exact=True).click()
        assert page.locator('.addon-library-grid .plugin-card').count() >= 1
        page.locator('#addonNav').get_by_role('button', name='Browse', exact=True).click()
        page.locator('#pluginsSearch').fill('python skills')
        page.get_by_role('button', name='Search GitHub', exact=True).click()
        page.get_by_role('button', name='Explore skills', exact=True).click()
        page.get_by_role('button', name='View add-on', exact=True).click()
        page.get_by_role('button', name='Check compatibility', exact=True).click()
        page.get_by_role('button', name='Download and scan', exact=True).wait_for()
        assert calls.count('github_search') == 1
        assert calls.count('github_repository') == 1
        assert calls.count('github_inspect') == 1
        assert calls.count('scan') == 1, 'search and inspection must not spend a scan'
        assert calls.count('install') == 1, 'discovery cannot install anything'
        page.get_by_role('button', name='← Back to list', exact=True).click()
        page.get_by_role('button', name='← Back to directory', exact=True).click()
        page.locator('#pluginsSearch').fill('')
        page.set_viewport_size({'width': 620, 'height': 800})
        page.locator('#addonNav').get_by_role('button', name='Browse', exact=True).click()
        assert page.locator('#pluginsBody').evaluate('(el) => el.scrollWidth <= el.clientWidth')
        page.locator('#addonNav').get_by_role('button', name='Security', exact=True).click()
        page.get_by_role('button', name='Manage in API Keys', exact=True).wait_for()
        assert page.get_by_label('Eyebrow API key', exact=True).count() == 0
        assert calls.count('scan') == 1
        page.goto(f"http://127.0.0.1:{server.server_port}/settings#keys", wait_until="domcontentloaded")
        page.get_by_label('API key', exact=True).wait_for()
        assert page.get_by_label('Eyebrow API key', exact=True).count() == 0
        page.screenshot(path=str(tmp_path / 'security-settings.png'))
    finally:
        release_catalog.set()
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
