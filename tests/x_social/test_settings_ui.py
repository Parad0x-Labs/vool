from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_actions_approval import fake_x
from tests.x_social.support import resource


def test_rating_voice_pause_forget_on_the_served_settings_page(fake_x, vault_home):
    from core import x_feedback, x_voice_profile
    from core.runtime_execution_tools import execute_runtime_tool
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    action = execute_runtime_tool('x.action.propose', {'kind': 'quote', 'post': '555', 'text': 'A precise release note.'}, source_context={'runtime_session_id': 'settings-rating'}).details['action']
    runtime = RuntimeServices(display_name='Social fixture')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def answer(self, result):
            self.send_response(result.status); self.send_header('Content-Type', result.content_type); self.end_headers(); self.wfile.write(result.body)
        def do_GET(self):
            u=urlsplit(self.path)
            self.answer(dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime, model_name='fixture', client_host='127.0.0.1'))
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or '{}')
            self.answer(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers), runtime=runtime, model_name='fixture', client_host='127.0.0.1', workspace_root_provider=lambda: str(vault_home)))
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);resource('port',server.server_port,'started');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    manager,browser=launch_chromium()
    try:
        page=browser.new_page(viewport={'width':1100,'height':1050})
        errors=[];page.on('pageerror',lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{server.server_port}/settings#keys')
        card=page.locator('.x-action-card').filter(has_text='A precise release note.')
        card.get_by_label('Your rating',exact=True).select_option('9')
        card.get_by_label('Comment (optional)',exact=True).fill('Clear and concrete.')
        card.get_by_role('button',name='Save rating',exact=True).click()
        card.get_by_role('button',name='Use as a voice sample',exact=True).wait_for()
        assert x_voice_profile.inspect('global')['samples']==[]
        out=Path(os.environ['SOCIAL_SHOTS']);out.mkdir(parents=True,exist_ok=True)
        card.screenshot(path=str(out/'settings-rating.png'))
        card.get_by_role('button',name='Use as a voice sample',exact=True).click()
        card.locator('[data-voice-added="1"]').wait_for()
        assert len(x_voice_profile.inspect('global')['samples'])==1
        assert x_feedback.inspect('global')['entries'][0]['rating']==9
        assert fake_x.writes()==[]
        page.reload()
        page.get_by_role('button',name='Pause ratings',exact=True).click()
        page.get_by_role('button',name='Resume ratings',exact=True).wait_for()
        assert x_feedback.summary('global')==''
        page.get_by_role('button',name='Forget all ratings',exact=True).click()
        page.get_by_role('button',name='Forget rating',exact=True).wait_for(state='detached')
        assert x_feedback.inspect('global')['entries']==[]
        assert x_voice_profile.inspect('global')['samples']==[]
        assert not errors,errors
    finally:
        browser.close();manager.stop();server.shutdown();server.server_close();thread.join(3);resource('port',server.server_port,'closed')
