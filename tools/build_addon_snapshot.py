"""Release-maintainer command: precheck pinned public skills, bundle permitted bytes.

Run: python -m tools.build_addon_snapshot
No user credentials, installs, scans, extraction or source execution. Each repository
is downloaded once at its recorded commit using GitHub's public archive endpoint.
"""
from __future__ import annotations

import configparser
import hashlib
import io
import json
import tarfile
from pathlib import Path

from core.addon_discovery import _REF, inspect_source, repository_name, safe_path
from core.eyebrow_client import fetch_bytes


def archive_source(raw: bytes) -> tuple[list[dict], dict[str, bytes]]:
    tree, files = [], {}
    size = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        roots = set()
        seen = set()
        for count, item in enumerate(archive):
            if count >= 40000:
                raise ValueError('Archive entry limit exceeded')
            size += item.size
            if size > 384 * 1024 * 1024:
                raise ValueError('Archive expanded size limit exceeded')
            parts = item.name.rstrip('/').split('/', 1)
            roots.add(parts[0])
            if len(roots) != 1:
                raise ValueError('Multiple archive roots')
            if len(parts) == 1:
                if not item.isdir():
                    raise ValueError('Archive root is not a directory')
                continue
            path = safe_path(parts[1])
            if path in seen:
                raise ValueError('Duplicate archive path')
            seen.add(path)
            if item.isdir():
                continue
            tree.append({'path':path, 'type':'blob',
                         'mode':'100644' if item.isfile() else '120000'})
            if (item.isfile() and item.size <= 128 * 1024
                    and (Path(path).name.lower() in {'skill.md','license','license.txt','license.md'}
                         or path == '.gitmodules')):
                files[path] = archive.extractfile(item).read(128 * 1024 + 1)
        if '.gitmodules' in files:
            modules = configparser.ConfigParser(interpolation=None)
            modules.read_string(files['.gitmodules'].decode('utf-8'))
            for section in modules.sections():
                path = safe_path(modules[section]['path'])
                tree.append({'path':path,'type':'commit','mode':'160000'})
    return tree, files


def build(directory: dict, originals: list[dict], fetch=fetch_bytes) -> tuple[dict, dict]:
    entries, assets = [], {}
    def keep(content):
        digest = hashlib.sha256(content).hexdigest()
        assets[digest] = content.decode('utf-8')
    for source in directory['sources']:
        repo = repository_name(source['repository'])
        ref = source['ref']
        if not _REF.fullmatch(ref):
            raise ValueError('Unpinned source')
        print('Checking pinned public source:', repo, ref[:12], flush=True)
        # OpenClaw's full application archive exceeds 80 MiB. Fetch its one tree and
        # the selected skill files instead; this is release work, never a user's setup.
        if repo.lower() == 'openclaw/openclaw':
            root_raw, _ = fetch('https://api.github.com/repos/'+repo+'/git/trees/'+ref, limit=2 * 1024 * 1024)
            root = json.loads(root_raw)
            if root.get('truncated') or not isinstance(root.get('tree'),list):
                raise ValueError('Incomplete root tree')
            skill_root = next(i for i in root['tree'] if i['path']=='skills' and i['type']=='tree')
            raw, _ = fetch('https://api.github.com/repos/'+repo+'/git/trees/'+skill_root['sha']+'?recursive=1', limit=8 * 1024 * 1024)
            result = json.loads(raw)
            if result.get('truncated') or not isinstance(result.get('tree'),list) or len(result['tree']) > 40000:
                raise ValueError('Incomplete skills tree')
            tree = root['tree'] + [{**i,'path':'skills/'+safe_path(i['path'])} for i in result['tree']]
            files = {}
            def read_file(path, files=files, repo=repo, ref=ref):
                path = safe_path(path)
                if path not in files:
                    files[path], _ = fetch('https://raw.githubusercontent.com/'+repo+'/'+ref+'/'+path, limit=128 * 1024)
                return files[path]
        else:
            raw, _ = fetch('https://codeload.github.com/'+repo+'/tar.gz/'+ref, limit=80 * 1024 * 1024)
            tree, files = archive_source(raw)
            def read_file(path, files=files):
                return files[path]
        rows = [dict(e) for e in directory['entries'] if e['repository'] == source['repository'] and e['ref'] == ref]
        view = {'repository':repo,'ref':ref,'tree':tree,'entries':rows}
        for row in rows:
            result = inspect_source(view, row['skill_path'], read_file)
            entry = result['entry']
            entry['compatibility_reasons'] = result['reasons']
            if entry['import_ready']:
                keep(files[entry['skill_path']])
                keep(files[entry['license_path']])
                entry['bundled_source'] = True
            entries.append(entry)
    # These two established catalogue entries already pin their complete files by hash.
    for entry in originals:
        for filename, field in [('SKILL.md','sha256'), ('LICENSE.txt','license_sha256')]:
            url = entry['repository'].replace('https://github.com/','https://raw.githubusercontent.com/')+'/'+entry['ref']+'/'+entry['path']+'/'+filename
            content, _ = fetch(url, limit=128 * 1024)
            if hashlib.sha256(content).hexdigest() != entry[field]:
                raise ValueError('Original catalogue hash mismatch: '+entry['id'])
            keep(content)
    return {**directory, 'schema':2, 'entries':entries}, {'schema':1,'files':assets}


def main():
    from core.addon_catalog import CATALOG
    root = Path(__file__).resolve().parents[1] / 'core'
    directory = json.loads((root/'addon_directory.json').read_text())
    result, assets = build(directory, [e for e in CATALOG if e['id'].startswith('anthropic-')])
    # Build everything before replacing either output. No partial successful catalogue.
    for filename, value in [('addon_directory.json',result),('addon_packages.json',assets)]:
        (root/filename).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    print('Ready:', sum(e['import_ready'] for e in result['entries'])+2,
          'total:',len(result['entries'])+2,'unique bundled files:',len(assets['files']))


if __name__ == '__main__':
    main()
