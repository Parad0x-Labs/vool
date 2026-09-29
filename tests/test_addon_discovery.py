"""Public search -> immutable inspection -> existing scan/install authority."""
import hashlib
import json
import urllib.error

import pytest

from core import addon_discovery as github
from core import addon_store, eyebrow_client
from core.web.api.addon_api import handle_addon_post
from tests.test_addon_store import SKILL, rig

REF = 'a' * 40
LICENSE = b'''MIT License\nCopyright (c) Example authors\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the "Software"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\nTHE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.\n'''


@pytest.fixture
def source(rig, monkeypatch):
    calls=[]
    tree=[{'path':'skills/example/SKILL.md','mode':'100644','type':'blob'},
          {'path':'LICENSE','mode':'100644','type':'blob'}]
    content={'skill':SKILL,'license':LICENSE}
    def api(path, **kw):
        calls.append(path)
        if '/search/repositories?' in path:
            return {'total_count':1,'items':[{'full_name':'example/skills','description':'Public skills','license':{'spdx_id':'MIT'},'stargazers_count':12}]}
        if '/commits/' in path:return {'sha':REF}
        if '/git/trees/' in path:return {'tree':tree,'truncated':False}
        return {'private':False,'default_branch':'main','license':{'spdx_id':'MIT'}}
    def raw(repo,ref,path):
        calls.append((repo,ref,path))
        return content['skill'] if path.endswith('SKILL.md') else content['license']
    monkeypatch.setattr(github,'_api',api)
    monkeypatch.setattr(github,'source_bytes',raw)
    return calls,tree,content


def test_discovery_inspection_scan_install_and_runtime_offer(source,rig,monkeypatch):
    from core import plugin_catalog, tool_offer_assembly
    matches=github.search('python skills')
    assert matches['repositories'][0]['name']=='example/skills'
    found=github.repository('https://github.com/example/skills')
    assert found['entries'][0]['ref']==REF
    # Public listing is never authorization to scan or install.
    assert not rig[2]
    inspected=github.inspect('example/skills',REF,'skills/example/SKILL.md')['entry']
    assert inspected['import_ready'] and inspected['license']=='MIT'
    assert len([r for r in addon_store.catalog()['entries'] if r['id']==inspected['id']])==1
    assert addon_store._entry(inspected['id'])==inspected
    review=addon_store.prepare(inspected['id'],approved=True)
    installed=addon_store.install_review(review['review_id'],accepted=True)
    pack=rig[0]/'addons/plugins'/installed['plugin_id']
    monkeypatch.setattr(plugin_catalog,'discovered_plugin_sources',lambda:((installed['plugin_id'],pack),))
    assert tool_offer_assembly.loaded_skills()[0].name=='example'
    assert (pack/'LICENSE.txt').read_bytes()==LICENSE
    assert len(rig[2])==1


@pytest.mark.parametrize('shape',['script','submodule','nested_license','apache_notice','symlink','long','licence','conflicting_licence','tool_mapping'])
def test_incompatible_sources_never_reach_scanner(source,rig,shape):
    calls,tree,content=source
    if shape=='script':tree.append({'path':'skills/example/helper.py','mode':'100644','type':'blob'})
    elif shape=='submodule':tree.append({'path':'skills/example/helper','mode':'160000','type':'commit'})
    elif shape=='nested_license':tree.append({'path':'skills/example/dependency/LICENSE','mode':'100644','type':'blob'})
    elif shape=='apache_notice':
        content['license']=b'Apache License Version 2.0, January 2004 END OF TERMS AND CONDITIONS'
        tree.append({'path':'NOTICE','mode':'100644','type':'blob'})
    elif shape=='symlink':tree[0]['mode']='120000'
    elif shape=='long':content['skill']=SKILL+b'x'*8001
    elif shape=='licence':content['license']=b'All rights reserved'
    elif shape=='conflicting_licence':content['skill']=SKILL.replace(b'description:',b'license: Apache-2.0\ndescription:')
    elif shape=='tool_mapping':content['skill']=SKILL.replace(b'description:',b'allowed-tools: [Read, Bash]\ndescription:')
    if shape=='symlink':
        with pytest.raises(eyebrow_client.AddonError):github.inspect('example/skills',REF,'skills/example/SKILL.md')
    else:
        row=github.inspect('example/skills',REF,'skills/example/SKILL.md')['entry']
        assert not row['import_ready']
        with pytest.raises(eyebrow_client.AddonError,match='compatibility'):
            addon_store.prepare(row['id'],approved=True)
    assert not rig[2]


def test_changed_download_and_forged_inspection_fail_closed(source,rig):
    row=github.inspect('example/skills',REF,'skills/example/SKILL.md')['entry']
    source[2]['skill']+=b'changed after inspection'
    with pytest.raises(eyebrow_client.AddonError,match='do not match'):
        addon_store.prepare(row['id'],approved=True)
    assert not rig[2]
    record=addon_store._root()/'discovered'/(row['id']+'.json')
    envelope=json.loads(record.read_text());envelope['value']['import_ready']=False
    record.write_text(json.dumps(envelope))
    with pytest.raises(eyebrow_client.AddonError):addon_store._entry(row['id'])


@pytest.mark.parametrize('value',['http://127.0.0.1/repo','https://github.com@evil.invalid/a/b','https://github.com/a/b?key=x','a/../b','https://github.com/a/b/tree/main','file:///private/key','a/..'])
def test_only_explicit_github_repositories_are_accepted(value):
    with pytest.raises(eyebrow_client.AddonError):github.repository_name(value)


def test_live_search_transport_is_fixed_origin_without_credentials(rig, monkeypatch):
    calls=[];github._CACHE.clear()
    def fetch(url,**kwargs):
        calls.append((url,kwargs));return b'{"items":[],"total_count":0}',{}
    monkeypatch.setattr(github,'fetch_bytes',fetch)
    from core import credential_store
    monkeypatch.setattr(credential_store,'get_credential',lambda *a:pytest.fail('Public discovery must not read keys'))
    github.search('git helpers & coding')
    assert calls[0][0].startswith('https://api.github.com/search/repositories?q=git+helpers+%26+coding')
    assert 'Authorization' not in calls[0][1]['headers']
    github.search('git helpers & coding')
    assert len(calls)==1
    github._CACHE.clear()


def test_discovery_api_fields_and_owner_boundary(source):
    headers={'content-type':'application/json','host':'localhost:1234','origin':'http://localhost:1234'}
    good={'action':'github_inspect','repository':'example/skills','ref':REF,'path':'skills/example/SKILL.md'}
    assert handle_addon_post({**good,'import_ready':True},headers,'127.0.0.1')[0]==400
    assert handle_addon_post(good,{**headers,'origin':'https://evil.invalid'},'127.0.0.1')[0]==403
    assert not source[0]
    code,result=handle_addon_post(good,headers,'127.0.0.1')
    assert code==200 and result['entry']['import_ready']


def test_incomplete_tree_is_not_silent_partial_catalogue(monkeypatch):
    monkeypatch.setattr(github,'_api',lambda path,**kw: {'tree':[],'truncated':True} if '/git/trees/' in path else {'private':False})
    with pytest.raises(eyebrow_client.AddonError,match='too large'):
        github.repository('example/skills',REF)


def test_public_source_and_search_cache_survive_memory_reset(rig, monkeypatch):
    github._CACHE.clear()
    calls=[]
    def fetch(url, **kwargs):
        calls.append(url)
        return (SKILL if 'raw.githubusercontent' in url else b'{"items":[],"total_count":0}'), {}
    monkeypatch.setattr(github, 'fetch_bytes', fetch)
    github.search('cached public skill')
    assert github.source_bytes('example/skills', REF, 'SKILL.md') == SKILL
    github._CACHE.clear()
    monkeypatch.setattr(github, 'fetch_bytes', lambda *a, **kw: pytest.fail('Should use saved public bytes'))
    github.search('cached public skill')
    assert github.source_bytes('example/skills', REF, 'SKILL.md') == SKILL
    assert len(calls) == 2


@pytest.mark.parametrize('status,headers,body,expected', [
    (403, {}, b'{"message":"Forbidden"}', 'github_access_denied'),
    (403, {'X-RateLimit-Remaining':'0'}, b'{}', 'github_rate_limited'),
    (403, {}, b'{"message":"API rate limit exceeded"}', 'github_rate_limited'),
    (429, {'Retry-After':'120'}, b'{}', 'github_rate_limited'),
])
def test_github_refusal_names_its_owner_and_cools_down(rig, monkeypatch, status, headers, body, expected):
    import io
    calls=[]
    def fetch(url, **kwargs):
        calls.append(url)
        raise urllib.error.HTTPError(url, status, 'fixture', headers, io.BytesIO(body))
    monkeypatch.setattr(github, 'fetch_bytes', fetch)
    with pytest.raises(eyebrow_client.AddonError) as error:
        github._request('https://api.github.com/example', 4096)
    assert error.value.code == expected
    assert 'Eyebrow has not scanned' in str(error.value)
    if expected == 'github_rate_limited':
        with pytest.raises(eyebrow_client.AddonError):
            github._request('https://api.github.com/another', 4096)
        assert len(calls) == 1
    assert not rig[2]


def test_expired_or_changed_public_cache_cannot_supply_source(rig, monkeypatch):
    identity='source:example/skills@'+REF+':SKILL.md'
    github._remember(identity, 'dW50cnVzdGVk', 600)
    path=github._cache_path(identity)
    envelope=json.loads(path.read_text())
    envelope['value']['value']='Y2hhbmdlZA=='
    path.write_text(json.dumps(envelope))
    calls=[]
    monkeypatch.setattr(github, 'fetch_bytes', lambda *a, **kw: (calls.append(a) or SKILL, {}))
    assert github.source_bytes('example/skills', REF, 'SKILL.md') == SKILL
    github._remember(identity, 'b2xk', -1)
    assert github.source_bytes('example/skills', REF, 'SKILL.md') == SKILL
    assert len(calls) == 2
