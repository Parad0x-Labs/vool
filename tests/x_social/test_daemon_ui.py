"""Real daemon/browser acceptance of the owner card, with a real tool-produced fixture turn.

Only X is replaced. No language model is run. This does not claim model-driven discovery.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.x_social.support import resource

SESSION='openclaw:0123456789abcdef0123'
REQUEST='social-card-request'
TEXT='Checkpoint resume makes interrupted work recoverable.'


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]


def test_real_daemon_chat_card_approves_once_and_ratings_feed_the_next_draft(vault_home,tmp_path,monkeypatch):
    from tests.served_browser import launch_chromium

    requests=[]
    class XHandler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def reply(self,code,body):
            raw=json.dumps(body).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            requests.append(('GET',self.path,None));p=urlsplit(self.path)
            post={'id':'555','author_id':'22','text':'Recovering interrupted builds.','created_at':datetime.now(timezone.utc).isoformat(),'public_metrics':{'like_count':7}}
            if p.path=='/2/users/personalized_trends':
                return self.reply(200,{'data':[{'trend_name':'checkpoint resume','post_count':20}]})
            if p.path=='/2/tweets/search/recent':
                return self.reply(200,{'data':[post],'includes':{'users':[{'id':'22','username':'builder','name':'Builder'}]}})
            if p.path=='/2/tweets':
                pid=parse_qs(p.query)['ids'][0]
                if pid=='999':post.update(id='999',author_id='1234567890',text=TEXT,public_metrics={'impression_count':101,'like_count':7,'reply_count':1})
                return self.reply(200,{'data':[post],'includes':{'users':[{'id':'22','username':'builder','name':'Builder'}]}})
            return self.reply(404,{'title':'Not Found Error'})
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or '{}')
            requests.append(('POST',self.path,body))
            return self.reply(201,{'data':{'id':'999','text':body.get('text')}})
    xserver=ThreadingHTTPServer(('127.0.0.1',0),XHandler);thread=threading.Thread(target=xserver.serve_forever,daemon=True);thread.start();resource('port',xserver.server_port,'started')
    from core import x_api
    monkeypatch.setattr(x_api,'API_ORIGIN',f'http://127.0.0.1:{xserver.server_port}')
    port=_free_port();resource('port',port,'started')
    root=Path(__file__).resolve().parents[2]
    boot=tmp_path/'vool_api_server_social_fixture.py'
    boot.write_text('''import sys,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom core.unattended_preflight import preflight\npreflight("social-daemon-test")\nfrom apps import vool_api_server as app\nfrom core import x_api\nx_api.API_ORIGIN=sys.argv[2]\nruntime=app._bootstrap(run_prewarm=False)\nfrom tests.test_x_api_reads import _connect\n_connect()\nfrom core.runtime_execution_tools import execute_runtime_tool\nfrom core.turn_contract import TurnRequest\nctx={"surface":"desktop","_owner_local":True,"runtime_session_id":sys.argv[4],"operating_mode":"auto","allow_remote_fetch":True}\nctx["turn_request"]=TurnRequest.from_ingress(user_text="Find checkpoint discussions and propose a quote on X.",source_context=ctx,request_id=sys.argv[5],turn_id="social-fixture-turn",session_id=sys.argv[4])\ntrends=execute_runtime_tool("x.account",{"kind":"trends"},source_context=ctx)\nsearch=execute_runtime_tool("x.search",{"query":"checkpoint resume","max_results":10},source_context=ctx)\nproposal=execute_runtime_tool("x.action.propose",{"kind":"quote","post":"555","text":sys.argv[6]},source_context=ctx)\nassert trends.ok and search.ok and proposal.ok,(trends,search,proposal)\nfrom core.persistent_memory import _write_conversation_event\n_write_conversation_event(session_id=sys.argv[4],user_input="Find checkpoint discussions and propose a quote on X.",assistant_output=proposal.response_text,source_context=ctx,request_id_override=sys.argv[5])\nPath(sys.argv[7]).write_text(json.dumps({"trends":trends.ok,"search":search.ok,"proposal":proposal.details}))\nimport threading,time\ndef capture_results():\n    from core import x_actions\n    deadline=time.monotonic()+60\n    while time.monotonic()<deadline:\n        status=x_actions.status_for_model(proposal.details["action"]["id"],source_context=ctx)\n        if status.status=="sent":\n            observed=execute_runtime_tool("x.results",{"posts":["999"]},source_context=ctx)\n            payload=json.loads(Path(sys.argv[7]).read_text())\n            payload.update(results_ok=observed.ok,results=observed.details,error=observed.response_text)\n            Path(sys.argv[7]).write_text(json.dumps(payload))\n            return\n        time.sleep(.05)\nthreading.Thread(target=capture_results,daemon=True).start()\nimport uvicorn\nuvicorn.run(app.create_app(runtime),host="127.0.0.1",port=int(sys.argv[3]),log_level="warning")\n''')
    evidence=tmp_path/'chain.json';log=(tmp_path/'daemon.log').open('w')
    env=dict(os.environ,VOOL_HOME=str(vault_home),VOOL_DAEMON_BIND_PORT=str(_free_port()),VOOL_DAEMON_HEALTH_PORT='0',VOOL_LOCAL_MODELS_ENABLED='0',VOOL_SKIP_PROVIDER_PREWARM='1',VOOL_REGISTER_INSTALLED_OLLAMA_MODELS='0')
    proc=subprocess.Popen([sys.executable,str(boot),str(root),f'http://127.0.0.1:{xserver.server_port}',str(port),SESSION,REQUEST,TEXT,str(evidence)],cwd=root,env=env,stdout=log,stderr=log)
    resource('process',proc.pid,'started')
    manager=browser=None
    try:
        deadline=time.monotonic()+90
        base=f'http://127.0.0.1:{port}'
        while time.monotonic()<deadline:
            assert proc.poll() is None,(tmp_path/'daemon.log').read_text()[-5000:]
            try:
                with urllib.request.urlopen(base+'/healthz',timeout=1) as r:
                    if r.status==200:break
            except Exception:
                time.sleep(.25)
        else:raise AssertionError('daemon did not become ready')
        assert not [r for r in requests if r[0]=='POST']
        manager,browser=launch_chromium();page=browser.new_page(viewport={'width':1180,'height':1000});errors=[];page.on('pageerror',lambda e: errors.append(str(e)))
        page.goto(base+'/chat#'+SESSION)
        card=page.locator('.vx-approval').filter(has_text=TEXT)
        card.wait_for(timeout=30000)
        shots=Path(os.environ['SOCIAL_SHOTS']);shots.mkdir(parents=True,exist_ok=True);card.screenshot(path=str(shots/'chat-approval.png'))
        assert page.request.post(base+'/api/x/activity',headers={'Origin':base},data={'action':'set_limits','monthly_actions':0}).ok
        for locale, expected in [('en','Your X send limit has been reached. Check your limits in Settings.'),('de','Dein X-Sendelimit ist erreicht. Prüfe deine Limits in den Einstellungen.')]:
            if locale!='en':
                page.goto(base+'/chat?ui_locale='+locale+'#'+SESSION)
                card=page.locator('.vx-approval').filter(has_text=TEXT)
                card.wait_for()
            with page.expect_response(lambda r:r.url.endswith('/api/x/activity') and r.request.method=='POST') as response:
                card.locator('.vx-approve').click()
            rejected=response.value.json()
            assert response.value.status==400 and rejected['state']=='usage_limit_reached',rejected
            page.wait_for_function("document.querySelector('.vx-approval .vx-approve') && !document.querySelector('.vx-approval .vx-approve').disabled")
            assert not [r for r in requests if r[0]=='POST']
            actual=card.locator('[role=status]').first.inner_text()
            (shots/('limit-'+locale+'.json')).write_text(json.dumps({'reply':rejected,'displayed':actual,'writes':0}))
            card.screenshot(path=str(shots/('chat-limit-'+locale+'.png')))
            assert actual==expected
        assert page.request.post(base+'/api/x/activity',headers={'Origin':base},data={'action':'set_limits','monthly_actions':None}).ok
        page.goto(base+'/chat?ui_locale=en#'+SESSION)
        card=page.locator('.vx-approval').filter(has_text=TEXT)
        card.get_by_role('button',name='Approve and send',exact=True).click()
        page.wait_for_function("document.querySelector('.vx-approval [role=status]').textContent === 'Sent'")
        assert len([r for r in requests if r[0]=='POST'])==1
        assert next(r for r in requests if r[0]=='POST')[2]=={'text':TEXT,'quote_tweet_id':'555'}
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            chain=json.loads(evidence.read_text())
            if 'results_ok' in chain:break
            time.sleep(.05)
        assert chain.get('results_ok'),chain
        assert chain['results']['posts'][0]['snapshots'][-1]['metrics']['views']==101
        assert chain['results']['posts'][0]['availability']['bookmarks']=='not available'
        card=page.locator('.vx-approval').filter(has_text=TEXT)
        card.get_by_label('Your rating',exact=True).select_option('10');card.get_by_label('Comment (optional)',exact=True).fill('Keep the checkpoint detail concrete.')
        card.get_by_role('button',name='Save rating',exact=True).click();card.get_by_role('button',name='Use as a voice sample',exact=True).wait_for()
        card.screenshot(path=str(shots/'chat-rating.png'))
        from core import x_feedback, x_voice_profile
        assert 'Keep the checkpoint detail' in x_voice_profile.voice_segment_for_turn('Write an X post about a build.')
        assert x_voice_profile.inspect('global')['samples']==[]
        assert x_feedback.forget('global')['ok']
        assert 'Keep the checkpoint detail' not in x_feedback.summary('global')
        assert not errors,errors
    finally:
        if browser:browser.close()
        if manager:manager.stop()
        proc.terminate()
        try:proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill();proc.wait(timeout=5)
        resource('process',proc.pid,'closed');log.close();resource('port',port,'closed');xserver.shutdown();xserver.server_close();thread.join(3);resource('port',xserver.server_port,'closed')
