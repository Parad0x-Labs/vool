from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from PIL import Image

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.disclosure.test_served_preview import CloudDaemon, _json, _provider_context, _resource


class NativeDaemon(CloudDaemon):
    def register_provider(self):
        super().register_provider()
        script='''
from core.model_registry import ModelRegistry
from storage.model_provider_manifest import get_provider_manifest
m=get_provider_manifest('disclosure-recording','disclosure-recording:scripted')
m.runtime_config.pop('api_path',None)
m.runtime_config['tool_dialect']='native'
m.metadata.update(runtime_family='ollama',deployment_class='cloud')
ModelRegistry().register_manifest(m)
'''
        result=subprocess.run([sys.executable,'-c',script],cwd=rig.REPO_ROOT,env=self.env(),capture_output=True,text=True,timeout=20)
        assert result.returncode==0,result.stderr


def native_provider():
    provider=rig.CapturingProvider(default='The selected image shows a blue square.')
    provider.raw=[]
    provider.metadata_requests=[]
    handler=provider._server.RequestHandlerClass
    original_get=handler.do_GET
    def get(self):
        if self.path.startswith(('/api/ps','/api/tags')):
            return self._send({'models':[{'name':'disclosure-recording:scripted','size':0,'size_vram':0}]})
        return original_get(self)
    def post(self):
        raw=self.rfile.read(int(self.headers.get('Content-Length') or 0))
        body=json.loads(raw)
        if self.path=='/api/pull':
            provider.metadata_requests.append(self.path)
            return self._send({'error':'Downloads are unavailable on this scripted provider.'},status=403)
        if self.path=='/api/show':
            return self._send({'model_info':{},'details':{'parameter_size':'0B'},'capabilities':['completion','vision','tools'],'template':'{{ .Messages }}'})
        assert self.path=='/api/chat',self.path
        provider.raw.append(raw)
        reply=rig._probe_reply(body)
        if reply is not None:
            for call in reply.get('tool_calls',[]):
                value=call['function'].get('arguments')
                if isinstance(value,str):call['function']['arguments']=json.loads(value)
        else:
            reply={'role':'assistant','content':'The selected image shows a blue square.'}
        data={'model':body['model'],'message':reply,'done':True,'done_reason':'stop','prompt_eval_count':40,'eval_count':8}
        if not body.get('stream'):
            return self._send(data)
        encoded=(json.dumps(data)+'\n').encode()
        self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    handler.do_GET,handler.do_POST=get,post
    return provider


def png(color):
    target=io.BytesIO();Image.new('RGB',(2,2),color).save(target,format='PNG');return target.getvalue()


def test_served_native_image_rules_and_individual_image_choices(tmp_path):
    provider=native_provider()
    daemon=NativeDaemon(tmp_path/'home',provider=provider,model='disclosure-recording:scripted',provider_name='disclosure-recording',vision=True)
    with _provider_context(provider):
        manager=browser=page=None
        uploads=[]
        try:
            daemon.start();_resource('daemon',daemon.process.pid,daemon.port,'running')
            cert=daemon.certify(timeout=60)
            assert cert.get('state')=='verified',{'certificate':cert,'log':daemon.log_tail()}
            provider.raw.clear()
            manager,browser=served_browser.launch_chromium();page=browser.new_page(viewport={'width':1280,'height':900})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('response',lambda response:uploads.append(response) if '/api/chat/attachments/upload' in response.url else None)
            page.goto(daemon.base_url+'/chat',wait_until='networkidle')
            folder=tmp_path/'project';folder.mkdir()
            project=_json(daemon.base_url+'/api/projects',{'name':'Workshop images','root':str(folder)})
            project_id=project.get('project',project)['id']
            page.evaluate('(pid) => newChatInProject(pid)',project_id)
            page.evaluate('(id) => pinLocalModel(id,null)',daemon.model)
            colors={'blue.png':png('blue'),'green.png':png('green'),'private-red.png':png('red')}
            page.locator('#attachInput').set_input_files([{'name':name,'mimeType':'image/png','buffer':data} for name,data in colors.items()])
            page.wait_for_function("() => { const chips=Array.from(document.querySelectorAll('#attachStrip .att-chip'));return chips.length===3 && chips.every(chip=>['ready','failed'].includes(chip.dataset.state)); }",timeout=330000)
            assert page.locator('#attachStrip .att-chip[data-state=failed]').count()==0,page.locator('#attachStrip').inner_text()
            rules=[]
            expected={}
            session=page.evaluate('() => window.VoolDisclosureActiveChat()')
            for name in colors:
                ident=page.locator('#attachStrip .att-chip[data-state=ready]').filter(has_text=name).get_attribute('data-id')
                with urlopen(daemon.base_url+'/api/chat/attachments/preview?'+urlencode({'session':session,'id':ident}),timeout=10) as response:
                    expected[name]=response.read()
                rules.append({'kind':'attachment','source':ident,'mode':'local' if name.startswith('private') else 'ask'})
            proposal=_json(daemon.base_url+'/api/disclosure/rules/propose',{'project_id':project_id,'rules':rules})
            _json(daemon.base_url+'/api/disclosure/rules/confirm',{'id':proposal['id'],'sha256':proposal['sha256']})
            page.locator('#input').fill('Describe the blue square in the attached images.')
            page.locator('#send').click()
            page.wait_for_function("() => document.getElementById('cloudDisclosure').open || Array.from(document.querySelectorAll('#vgActions .vg-confirm')).some(button=>button.offsetParent!==null)",timeout=45000)
            if page.locator('#vgActions .vg-confirm').is_visible():page.locator('#vgActions .vg-confirm').click()
            page.wait_for_selector('#cloudDisclosure[open]',timeout=45000)
            first=json.loads(page.locator('#cloudDisclosureBody').text_content())
            assert provider.raw==[]
            images=[value for m in first['messages'] for value in m.get('images',[])]
            blue,green,private=(base64.b64encode(expected[name]).decode() for name in colors)
            assert images==[blue,green]
            assert private not in page.locator('#cloudDisclosureBody').text_content()
            assert 'private-red.png' not in page.locator('#cloudDisclosureBody').text_content()
            assert page.locator('#cloudDisclosureParts img').count()==2
            page.wait_for_function("() => Array.from(document.querySelectorAll('#cloudDisclosureParts img')).every(image=>image.complete&&image.naturalWidth===2)")
            capture_shot(page,'native-image-preview')
            for index,message in enumerate(first['messages']):
                for n,value in enumerate(message.get('images',[])):
                    if value==green:page.locator('#cloudDisclosureParts input[value="i:'+str(index)+':'+str(n)+'"]').uncheck()
            page.locator('#cloudDisclosure [data-disclosure-action=parts]').click()
            page.wait_for_function('(value) => !document.getElementById("cloudDisclosureBody").textContent.includes(value)',arg=green)
            assert page.locator('#cloudDisclosureParts img').count()==1
            capture_shot(page,'native-image-selected-preview')
            shown=page.locator('#cloudDisclosureBody').text_content().encode()
            page.locator('#cloudDisclosure [data-disclosure-action=send]').click()
            page.locator('.msg.assistant').filter(has_text='blue square').last.wait_for(timeout=45000)
            page.wait_for_function("() => view.run && view.run.released && view.run.status==='completed'",timeout=45000)
            assert shown in provider.raw
            assert all(green.encode() not in raw and private.encode() not in raw for raw in provider.raw)
            assert any(blue.encode() in raw for raw in provider.raw)
            assert errors==[]
            evidence=os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
            if evidence:page.screenshot(path=str(Path(evidence).parent/'shots'/('native-images-'+os.environ.get('VOOL_DISCLOSURE_RUN_TAG','run')+'.png')),full_page=True)
        finally:
            try:
                evidence=os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
                if evidence and page:
                    tag=os.environ.get('VOOL_DISCLOSURE_RUN_TAG','run')
                    page.screenshot(path=str(Path(evidence).parent/'shots'/('native-images-final-'+tag+'.png')),full_page=True)
                    captured=[]
                    for response in uploads:
                        try:body=response.json()
                        except ValueError:body={'unparsed_response':response.text()[:1000]}
                        captured.append({'status':response.status,'body':body})
                    Path(evidence,'native-images-'+tag+'.json').write_text(json.dumps({'uploads':captured,'chips':page.locator('#attachStrip').inner_text()},indent=2))
            finally:
                try:
                    if browser:browser.close()
                finally:
                    try:
                        if manager:manager.stop()
                    finally:
                        if daemon.process:
                            daemon.stop();_resource('daemon',daemon.process.pid,daemon.port,'stopped')
            assert provider.metadata_requests==[]


def capture_shot(page,label):
    evidence=os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
    if evidence:
        page.screenshot(path=str(Path(evidence).parent/'shots'/(label+'-'+os.environ.get('VOOL_DISCLOSURE_RUN_TAG','run')+'.png')),full_page=True)
