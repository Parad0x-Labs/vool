"""Independent REST/draft regressions. Synthetic HTTP-boundary responses, no sockets/accounts.

The original production draft, MIME builder, provider adapters and OAuth response/error
handling all run. Only urlopen and token acquisition are intercepted with synthetic data.
"""
import base64
import email
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

import pytest

from core import email_drafts, email_tools, runtime_paths
from core.email_providers.base import _OAuthClient

SESSION='openclaw:email-v2-independent'


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('VOOL_HOME',str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    email_drafts.reset_drafts()
    monkeypatch.setattr(_OAuthClient,'access_token',lambda self,**kwargs:'synthetic-access')
    yield
    runtime_paths.configure_runtime_home(None)


def configure(monkeypatch,provider):
    creds={'provider':provider,'from_addr':'review@example.test',
           '_oauth_handle':{'refresh_token':'synthetic-refresh','client_id':'synthetic-client',
                            'account_email':'review@example.test'}}
    monkeypatch.setattr(email_tools,'_load_account',lambda kind,account:dict(creds))
    return creds


def draft(body='Wednesday at 10:00 works.',subject='Delivery slot',reply_to=''):
    saved=email_drafts.save_draft(to='supplier@example.test',subject=subject,body=body,
        account='review-account',session_id=SESSION,in_reply_to=reply_to,
        references=[reply_to] if reply_to else [],kind='reply' if reply_to else 'compose')
    assert saved.ok,saved.message
    did=saved.draft['draft_id']
    assert email_drafts.approve_draft(did,session_id=SESSION).ok
    return did


class Reply:
    status=200
    def __init__(self,data=b'{}',failure=None):self.data=data;self.failure=failure
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self):
        if self.failure:raise self.failure
        return self.data


def with_profile(wire,address='review@example.test'):
    """Revision-5 fixture realism: the documented provider profile of the configured account.

    Sends, parent lookups and reconciliation reads now verify the mailbox with the provider's own
    profile, read with the exact bearer token that carries them. Wires written before that read
    existed model only the POST and parent GETs. This answers ONLY the two documented profile reads
    (Gmail users.getProfile, Graph GET /me) with the synthetic mailbox `configure` sets up, and passes
    every other request to the original wire unchanged, so its assertions still apply."""
    def profiled(req,**kwargs):
        path=urllib.parse.urlsplit(req.full_url).path
        if req.get_method()=='GET' and path.endswith('/gmail/v1/users/me/profile'):
            return Reply(json.dumps({'emailAddress':address}).encode())
        if req.get_method()=='GET' and path.endswith('/v1.0/me'):
            return Reply(json.dumps({'mail':address,'userPrincipalName':address}).encode())
        return wire(req,**kwargs)
    return profiled


@pytest.mark.parametrize('provider',['gmail','graph'])
@pytest.mark.parametrize('failure',['timeout-after-accept','malformed-success-json'])
def test_rest_accepted_send_cannot_be_redispatched_after_reply_failure(monkeypatch,provider,failure):
    configure(monkeypatch,provider)
    accepted=[]
    def wire(req,**kwargs):
        assert req.method=='POST'
        accepted.append(req.data)
        reply=(Reply(failure=TimeoutError('Synthetic reply-body timeout AFTER acceptance'))
               if failure=='timeout-after-accept' else Reply(b'{malformed-success-json'))
        reply.status=202 if provider=='graph' else 200
        return reply
    monkeypatch.setattr(urllib.request,'urlopen',with_profile(wire))
    did=draft(subject='Provider '+provider+' '+failure)
    first=email_drafts.send_draft(did,session_id=SESSION)
    second=email_drafts.send_draft(did,session_id=SESSION)
    assert len(accepted)==1,(first.status,second.status,len(accepted))
    assert email_drafts.get_draft(did,session_id=SESSION).draft['status'] in {'delivery_unknown','sending','sent','sent_confirmed'}


@pytest.mark.parametrize('body',[
    'The approved delivery details are '+ 'packaging '*30,
    'Ačiū už pagalbą. '+ '€'*100,
],ids=['quoted-printable-paragraph','base64-unicode'])
def test_graph_sends_decoded_approved_body(monkeypatch,body):
    configure(monkeypatch,'graph');sent=[]
    def wire(req,**kwargs):sent.append(json.loads(req.data));return Reply(b'')
    monkeypatch.setattr(urllib.request,'urlopen',with_profile(wire))
    result=email_drafts.send_draft(draft(body=body),session_id=SESSION)
    assert result.ok,result.message
    actual=sent[0]['message']['body']['content']
    assert actual.rstrip('\n')==body.rstrip('\n'),(actual,body)


@pytest.mark.parametrize('reply_to',['','<parent-42@example.test>'],ids=['compose','reply'])
def test_graph_send_uses_a_supported_header_representation(monkeypatch,reply_to):
    configure(monkeypatch,'graph')
    def strict_wire(req,**kwargs):
        if req.get_header('Content-type')=='application/json':
            body=json.loads(req.data)
            headers=body.get('message',{}).get('internetMessageHeaders',[])
            bad=[h['name'] for h in headers if not h['name'].lower().startswith('x-')]
            if bad:
                raise urllib.error.HTTPError(req.full_url,400,'InvalidInternetMessageHeader',{},
                    io.BytesIO(json.dumps({'error':{'code':'InvalidInternetMessageHeader','message':str(bad)}}).encode()))
        return Reply(b'')
    monkeypatch.setattr(urllib.request,'urlopen',with_profile(strict_wire))
    result=email_drafts.send_draft(draft(reply_to=reply_to),session_id=SESSION)
    assert result.status=='sent',result.message


@pytest.mark.parametrize('anchor,thread',[('<parent-42@example.test>','thread-original'),('<proposal-87@example.test>','thread-novel')])
def test_gmail_reply_preserves_resolved_provider_thread(monkeypatch,anchor,thread):
    configure(monkeypatch,'gmail');sent=[]
    def wire(req,**kwargs):
        if req.method=='POST':
            sent.append(json.loads(req.data))
            return Reply(json.dumps({'id':'sent-one','threadId':thread}).encode())
        if '/messages?' in req.full_url:
            return Reply(json.dumps({'messages':[{'id':'anchor-provider-id','threadId':thread}]}).encode())
        return Reply(json.dumps({'id':'anchor-provider-id','threadId':thread,'payload':{'headers':[
            {'name':'Message-ID','value':anchor},{'name':'Subject','value':'Delivery slot'}]}}).encode())
    monkeypatch.setattr(urllib.request,'urlopen',with_profile(wire))
    result=email_drafts.send_draft(draft(reply_to=anchor),session_id=SESSION)
    assert result.ok,result.message
    raw=base64.urlsafe_b64decode(sent[0]['raw']+'='*(-len(sent[0]['raw'])%4))
    assert email.message_from_bytes(raw)['In-Reply-To']==anchor
    assert sent[0].get('threadId')==thread,sent[0].keys()


@pytest.mark.parametrize('status',['sending','delivery_unknown'])
def test_pruning_never_discards_an_unresolved_send_reservation(status):
    rows={f'ed-{i}':{'status':'sent','updated_at':i+100} for i in range(email_drafts._MAX_DRAFTS)}
    rows['old-unresolved']={'status':status,'updated_at':1,'sent_message_id':'<durable@example.test>'}
    state={'drafts':rows}
    email_drafts._prune(state)
    assert 'old-unresolved' in state['drafts'],'Retention discarded the only unresolved send reservation'


@pytest.mark.parametrize('provider',['gmail','graph'])
def test_oauth_account_repoint_cannot_reuse_old_approval(monkeypatch,provider):
    creds=configure(monkeypatch,provider)
    did=draft()
    # Same outward From alias, different authenticated mailbox behind the credential slot.
    creds['_oauth_handle']={'refresh_token':'different-synthetic-refresh','client_id':'synthetic-client',
                            'account_email':'other-mailbox@example.test'}
    sent=[]
    def wire(req,**kwargs):sent.append(req.data);return Reply(b'{"id":"new-mailbox-send"}')
    monkeypatch.setattr(urllib.request,'urlopen',wire)
    result=email_drafts.send_draft(did,session_id=SESSION)
    assert not sent,(result.status,'An approval crossed to another authenticated mailbox')
    assert result.status=='needs_reapproval'


def test_control_simple_graph_body_and_gmail_raw_body_survive(monkeypatch):
    for provider in ('graph','gmail'):
        configure(monkeypatch,provider);sent=[]
        def wire(req,_sent=sent,**kwargs):_sent.append(json.loads(req.data));return Reply(b'{"id":"sent-control"}')
        monkeypatch.setattr(urllib.request,'urlopen',with_profile(wire))
        result=email_drafts.send_draft(draft(body='Short plain text.',subject=provider+' control'),session_id=SESSION)
        assert result.ok
        if provider=='graph':assert sent[0]['message']['body']['content'].strip()=='Short plain text.'
        else:
            raw=base64.urlsafe_b64decode(sent[0]['raw']+'='*(-len(sent[0]['raw'])%4))
            assert email.message_from_bytes(raw).get_payload(decode=True).decode().strip()=='Short plain text.'
