"""Public GitHub discovery; pinned inert content, no clone/install/credential reuse."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode, urlsplit

from core.eyebrow_client import AddonError, fetch_bytes

_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}\Z")
_REF = re.compile(r"[0-9a-f]{40}\Z")
_CACHE = OrderedDict()
_LOCK = threading.RLock()
_SLOTS = threading.BoundedSemaphore(2)
_ECOSYSTEMS = {'nousresearch/hermes-agent':'Hermes', 'openclaw/openclaw':'OpenClaw',
               'vercel-labs/agent-skills':'Vercel', 'obra/superpowers':'Superpowers'}


def repository_name(value: str) -> str:
    value = str(value).strip()
    if value.startswith('https://'):
        url = urlsplit(value)
        if url.netloc != 'github.com' or url.query or url.fragment:
            raise AddonError('github_source_invalid', 'Use a public github.com/owner/repository URL.')
        value = url.path.strip('/')
    if value.endswith('.git'):
        value = value[:-4]
    if not _REPO.fullmatch(value) or value.split('/')[1] in {'.', '..'}:
        raise AddonError('github_source_invalid', 'Enter owner/repository or its GitHub repository URL.')
    return value


def safe_path(value: str) -> str:
    if (not isinstance(value, str) or len(value) > 512 or value.startswith('/')
            or '\\' in value or any(part in {'', '.', '..'} for part in value.split('/'))
            or any(ord(c) < 32 for c in value)):
        raise AddonError('github_path_invalid', 'This source path is not a regular repository file.')
    return value


def _cache_path(identity: str):
    from core import addon_store
    return addon_store._root() / 'public-cache' / (hashlib.sha256(identity.encode()).hexdigest() + '.json')


def _cached(identity: str):
    from core import addon_store
    try:
        record = addon_store._read(_cache_path(identity))
        if record['identity'] == identity and record['expires'] > time.time():
            return record['value']
    except (AddonError, KeyError, TypeError, OSError):
        pass
    return None


def _remember(identity: str, value, seconds: float) -> None:
    from core import addon_store
    record = {'identity': identity, 'expires': time.time() + seconds, 'value': value}
    if len(json.dumps(record)) > 2 * 1024 * 1024:
        return  # Large trees still benefit from the bounded in-memory API cache.
    try:
        path = _cache_path(identity)
        with _LOCK:
            addon_store._write(path, record)
            files = sorted(path.parent.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            size = 0
            for i, file in enumerate(files):
                size += file.stat().st_size
                if i >= 128 or size > 32 * 1024 * 1024:
                    file.unlink(missing_ok=True)
    except OSError:
        pass  # A cache write is optional; a verified download is still usable.


def _rate_error(deadline: float) -> AddonError:
    minutes = max(1, int((deadline - time.time() + 59) // 60))
    return AddonError('github_rate_limited',
                      f'GitHub’s public download limit was reached. Try again in {minutes} minute(s). '
                      'Ready-to-scan store add-ons still work. Eyebrow has not scanned this request.', 429)


def _request(url: str, limit: int) -> bytes:
    cooldown = 'cooldown:' + urlsplit(url).hostname
    until = _cached(cooldown)
    if isinstance(until, (int, float)) and until > time.time():
        raise _rate_error(until)
    if not _SLOTS.acquire(blocking=False):
        raise AddonError('github_busy', 'Another catalogue lookup is running. Try again when it finishes.', 429)
    try:
        raw, _ = fetch_bytes(url, headers={'Accept': 'application/vnd.github+json'}, limit=limit)
        return raw
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        try:
            message = str(json.loads(exc.read(4096)).get('message', '')).lower()
        except (ValueError, AttributeError, OSError):
            message = ''
        limited = (exc.code == 429 or (exc.code == 403 and (
            headers.get('x-ratelimit-remaining') == '0' or 'retry-after' in headers
            or 'rate limit' in message or 'abuse detection' in message)))
        if limited:
            until = time.time() + 60
            try:
                retry = headers.get('retry-after')
                if retry:
                    until = max(until, time.time() + float(retry) if retry.isdigit()
                                else parsedate_to_datetime(retry).timestamp())
                if headers.get('x-ratelimit-remaining') == '0':
                    until = max(until, float(headers.get('x-ratelimit-reset', 0)))
            except (ValueError, TypeError, OverflowError):
                pass
            _remember(cooldown, until, until - time.time())
            raise _rate_error(until) from None
        if exc.code == 403:
            raise AddonError('github_access_denied', 'GitHub refused access to this source. '
                             'Choose a public source you can open in your browser. Eyebrow has not scanned it.', 403) from None
        if exc.code == 404:
            raise AddonError('github_not_found', 'That public repository or version was not found.', 404) from None
        raise AddonError('github_unavailable', 'GitHub could not complete the lookup. Nothing was installed.', 502) from None
    except (OSError, urllib.error.URLError):
        raise AddonError('github_unreachable', 'Could not reach GitHub. Retry when your connection is available.', 502) from None
    finally:
        _SLOTS.release()


def _api(path: str, limit: int = 2 * 1024 * 1024) -> dict:
    with _LOCK:
        cached = _CACHE.get(path)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
    saved = _cached('api:' + path)
    if isinstance(saved, dict):
        return saved
    try:
        result = json.loads(_request('https://api.github.com' + path, limit))
        if not isinstance(result, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise AddonError('github_invalid_reply', 'GitHub returned an unreadable catalogue response.', 502) from None
    with _LOCK:
        _CACHE[path] = (time.monotonic(), result)
        while len(_CACHE) > 32:
            _CACHE.popitem(last=False)
    _remember('api:' + path, result, 600)
    return result


def source_bytes(repository: str, ref: str, path: str) -> bytes:
    repo = repository_name(repository)
    if not _REF.fullmatch(ref):
        raise AddonError('github_version_invalid', 'Select an exact repository version before scanning.')
    path = safe_path(path)
    identity = 'source:' + repo.lower() + '@' + ref + ':' + path
    saved = _cached(identity)
    if isinstance(saved, str):
        try:
            return base64.b64decode(saved, validate=True)
        except ValueError:
            pass
    content = _request('https://raw.githubusercontent.com/' + repo + '/' + ref + '/' + quote(path, safe='/'), 128 * 1024)
    _remember(identity, base64.b64encode(content).decode(), 30 * 86400)
    return content


def category_for(path: str) -> str:
    parts = set(path.lower().split('/'))
    for names, category in [({'blockchain','finance','crypto'},'Crypto'), ({'research','arxiv','web'},'Research'),
                            ({'creative','design','web-design-guidelines','frontend-design'},'Design'),
                            ({'communication','social-media','email','slack','discord'},'Communication'),
                            ({'productivity','notes','writing','writing-guidelines'},'Productivity'),
                            ({'data-science','machine-learning'},'Data'), ({'devops','infrastructure'},'DevOps')]:
        if parts & names:
            return category
    return 'Coding'


def listing(repo: str, ref: str, path: str, *, ecosystem='', license_id='Unknown', description='') -> dict:
    folder = str(PurePosixPath(path).parent)
    if folder == '.':
        folder = ''
    slug = PurePosixPath(path).parent.name or repo.split('/')[-1]
    identity = hashlib.sha256((repo.lower()+'@'+ref+':'+path).encode()).hexdigest()[:24]
    return {'id':'github-'+identity, 'name':slug.replace('-', ' ').replace('_',' ').capitalize(),
            'description':description or 'Community skill from '+repo+'. Open it to check requirements and compatibility.',
            'publisher':repo.split('/')[0], 'ecosystem':ecosystem or _ECOSYSTEMS.get(repo.lower(),repo.split('/')[0]), 'category':category_for(path),
            'kind':'skill', 'repository':'https://github.com/'+repo, 'ref':ref, 'path':folder,
            'skill_path':path, 'skill_name':slug, 'license':license_id, 'discovery_only':True,
            'compatibility':'Check compatibility before scanning or installing.', 'requirements':'Not inspected yet.'}


def search(query: str) -> dict:
    query = str(query).strip()
    if not 2 <= len(query) <= 100 or any(ord(c) < 32 for c in query):
        raise AddonError('github_query_invalid', 'Enter 2–100 characters to search public GitHub repositories.')
    # Search terms are data. GitHub credentials are deliberately never read here.
    data = _api('/search/repositories?' + urlencode({'q':query+' fork:false archived:false', 'per_page':12, 'sort':'stars'}))
    rows = []
    for item in data.get('items', [])[:12]:
        if not isinstance(item, dict) or item.get('private') or item.get('archived'):
            continue
        try:
            repo = repository_name(item['full_name'])
        except (KeyError, AddonError):
            continue
        rows.append({'repository':repo, 'name':repo, 'description':str(item.get('description') or '')[:600],
                     'license':str((item.get('license') or {}).get('spdx_id') or 'Unknown'),
                     'url':'https://github.com/'+repo, 'stars':item.get('stargazers_count',0)})
    return {'ok':True, 'repositories':rows, 'query':query, 'total':data.get('total_count',len(rows)), 'scope':'Public repository search; listing is not a security or compatibility endorsement.'}


def repository(repository: str, ref: str = '') -> dict:
    repo = repository_name(repository)
    info = _api('/repos/'+repo)
    if info.get('private') or info.get('disabled') or info.get('archived'):
        raise AddonError('github_source_unavailable', 'Only active public repositories can be inspected.')
    if not ref:
        commit = _api('/repos/'+repo+'/commits/'+quote(info.get('default_branch','HEAD'),safe=''))
        ref = commit.get('sha','')
    if not _REF.fullmatch(ref):
        raise AddonError('github_version_invalid', 'GitHub did not provide an exact source revision.')
    result = _api('/repos/'+repo+'/git/trees/'+ref+'?recursive=1', limit=8 * 1024 * 1024)
    if result.get('truncated') or not isinstance(result.get('tree'), list) or len(result['tree']) > 30000:
        raise AddonError('github_tree_incomplete', 'This repository is too large to inspect completely. Choose a smaller skill repository.')
    rows = []
    for item in result['tree']:
        path = item.get('path','')
        if item.get('type') == 'blob' and item.get('mode') == '100644' and (path == 'SKILL.md' or path.endswith('/SKILL.md')):
            rows.append(listing(repo,ref,safe_path(path),license_id=(info.get('license') or {}).get('spdx_id') or 'Unknown'))
    return {'ok':True, 'repository':repo, 'ref':ref, 'entries':rows[:500], 'total':len(rows), 'tree':result['tree']}


def license_name(content: bytes) -> str:
    text = ' '.join(content.decode('utf-8',errors='replace').lower().split())
    if ('permission is hereby granted, free of charge, to any person obtaining a copy' in text
            and 'the software is provided "as is"' in text):
        if 'without attribution' in text:
            return 'MIT-0'
        if 'the above copyright notice and this permission notice shall be included' in text:
            return 'MIT'
    if 'apache license' in text and 'version 2.0, january 2004' in text and 'end of terms and conditions' in text:
        return 'Apache-2.0'
    return 'Unknown'


def inspect(repository_name_input: str, ref: str, path: str) -> dict:
    from core import addon_store
    source = repository(repository_name_input, ref)
    result = inspect_source(source, path, lambda p: source_bytes(source['repository'], source['ref'], p))
    row = result['entry']
    # Persist only the server-derived pinned entry. Callers never submit hashes or licences.
    addon_store._write(addon_store._root()/'discovered'/(row['id']+'.json'),row)
    return result


def inspect_source(source: dict, path: str, read_file) -> dict:
    """One compatibility law for public discovery and release-time catalogue snapshots."""
    import yaml

    from core.plugin_skills import _FRONTMATTER_RE, _MAX_BODY_CHARS
    path = safe_path(path)
    row = next((dict(r) for r in source['entries'] if r['skill_path'] == path), None)
    if not row:
        raise AddonError('github_skill_missing', 'That regular SKILL.md file was not found in this repository version.')
    content = read_file(path)
    try:
        text = content.decode('utf-8')
    except UnicodeError:
        raise AddonError('github_skill_encoding', 'The skill is not valid UTF-8 text.') from None
    match = _FRONTMATTER_RE.match(text)
    try:
        front = yaml.safe_load(match.group(1)) if match else {}
    except yaml.YAMLError:
        front = {}
    front = front if isinstance(front, dict) else {}
    reasons = []
    if not match or not isinstance(front.get('name'), str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}',front['name']):
        reasons.append('The skill needs a valid name and SKILL.md header before VOOL can load it.')
    if not isinstance(front.get('description'), str) or not front.get('description','').strip():
        reasons.append('The skill has no usable description for matching it to tasks.')
    if match and len(match.group(2).strip()) > _MAX_BODY_CHARS:
        reasons.append('This skill is larger than VOOL’s instruction limit. It needs adaptation; VOOL will not silently cut it short.')
    if front.get('allowed-tools') or front.get('allowed_tools'):
        reasons.append('This skill declares a host-specific tool list that needs a VOOL mapping.')
    folder = str(PurePosixPath(path).parent)
    files = {i['path']:i for i in source['tree'] if i.get('type') == 'blob'}
    prefix = '' if folder == '.' else folder+'/'
    extras = [i['path'] for i in source['tree'] if i.get('type') in {'blob','commit'}
              and i['path'].startswith(prefix) and i['path'] != path
              and not (str(PurePosixPath(i['path']).parent) == folder
                       and PurePosixPath(i['path']).name.lower() in {'license','license.txt','license.md'})]
    if extras:
        reasons.append('This skill includes supporting files or scripts. The current importer only installs complete standalone instruction skills.')
    licence = b''
    licence_path = ''
    for parent in [PurePosixPath(path).parent,*PurePosixPath(path).parent.parents]:
        found = [str(parent/name) for name in ('LICENSE','LICENSE.txt','LICENSE.md') if str(parent/name) in files]
        if found:
            licence_path = found[0]
            if len(found) != 1 or files[licence_path].get('mode') != '100644':
                reasons.append('The licence files need manual review.')
                break
            licence = read_file(licence_path)
            break
    licence_id = license_name(licence)
    if licence_id == 'Apache-2.0' and licence_path:
        parent = PurePosixPath(licence_path).parent
        if any(str(parent/name) in files for name in ('NOTICE','NOTICE.txt','NOTICE.md')):
            reasons.append('This licence includes an additional NOTICE file that needs a complete package import.')
    declared = str(front.get('license') or '').strip()
    if licence_id == 'Unknown' or (declared and declared.lower() not in ({'mit', 'mit license'} if licence_id == 'MIT' else {licence_id.lower()})):
        reasons.append('A supported permissive licence has not been established for this skill.')
    # Other-host declarations are shown, never transformed into grants or install commands.
    requirements = front.get('metadata') or front.get('required_environment_variables') or {}
    row.update(name=front.get('name') if isinstance(front.get('name'),str) else row['name'],
               description=front.get('description')[:1200] if isinstance(front.get('description'),str) else row['description'],
               skill_name=front.get('name') if isinstance(front.get('name'),str) else row['skill_name'],
               license=licence_id, license_path=licence_path, sha256=hashlib.sha256(content).hexdigest(),
               license_sha256=hashlib.sha256(licence).hexdigest(), discovery_only=False,
               import_ready=not reasons, compatibility=' '.join(reasons) if reasons else 'Standalone instruction format supported. Required external tools/accounts are not installed or verified.',
               requirements=json.dumps(requirements,ensure_ascii=False,default=str)[:2000] if requirements else 'Uses the existing model and permitted tools. No executable dependencies are installed.')
    return {'ok':True,'entry':row,'reasons':reasons}
