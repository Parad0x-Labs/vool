"""Real shipped skill bytes through scan, acceptance and the existing runtime loader."""
import copy
import io
import json
import tarfile
from pathlib import Path

import pytest

from core import addon_catalog, addon_discovery, addon_packages, addon_store
from core.eyebrow_client import AddonError
from tests.test_addon_store import rig


def test_all_ready_catalogue_files_are_complete_licensed_and_packaged():
    from core.plugin_skills import _FRONTMATTER_RE, parse_skill_content
    directory = addon_catalog._DIRECTORY
    assert directory['schema'] == 2
    ready = [e for e in addon_catalog.CATALOG if e.get('import_ready')]
    assert {e.get('ecosystem', e['publisher']) for e in ready} >= {'Hermes','OpenClaw','Anthropic'}
    assert all(not e.get('discovery_only') for e in addon_catalog.CATALOG)
    for entry in ready:
        assert entry['bundled_source']
        content = addon_packages.source(entry['sha256']).decode()
        skill = parse_skill_content(content, path=Path('addons')/entry['id']/'SKILL.md', plugin_id='discover-'+entry['id'])
        assert skill and skill.name == entry['skill_name']
        assert skill.body == _FRONTMATTER_RE.match(content).group(2).strip()
        licence = addon_packages.source(entry['license_sha256'])
        assert addon_discovery.license_name(licence) == entry['license']
    assert all(e.get('compatibility_reasons') for e in directory['entries'] if not e['import_ready'])
    assert all(e.get('license') in {'MIT','MIT-0','Apache-2.0'} for e in ready)


def test_real_shipped_skill_scan_install_offer_needs_no_github(rig, monkeypatch):
    from core import plugin_catalog, tool_offer_assembly
    entry = next(copy.deepcopy(e) for e in addon_catalog.CATALOG if e.get('ecosystem') == 'Hermes' and e.get('import_ready'))
    monkeypatch.setattr(addon_store, 'CATALOG', [entry])
    def unavailable(*a, **kw):
        pytest.fail('A bundled skill must not contact GitHub')
    monkeypatch.setattr(addon_store, 'fetch_bytes', unavailable)
    monkeypatch.setattr(addon_discovery, 'fetch_bytes', unavailable)
    from core import eyebrow_client
    from tests.test_addon_store import report
    def scan(endpoint, payload=None):
        rig[2].append((endpoint,payload))
        result=report(); result['artifacts'][0]['name']=entry['skill_name']
        return result,'18'
    monkeypatch.setattr(eyebrow_client, 'request_api', scan)
    addon_store.catalog()
    assert not rig[2]
    saved = addon_store.prepare(entry['id'], approved=True)
    assert rig[2][0][1]['source']['content'].encode() == addon_packages.source(entry['sha256'])
    installed = addon_store.install_review(saved['review_id'], accepted=True)
    pack = rig[0]/'addons/plugins'/installed['plugin_id']
    monkeypatch.setattr(plugin_catalog, 'discovered_plugin_sources', lambda: ((installed['plugin_id'],pack),))
    loaded = tool_offer_assembly.loaded_skills()
    assert len(loaded) == 1 and loaded[0].name == entry['skill_name']
    assert (pack/'LICENSE.txt').read_bytes() == addon_packages.source(entry['license_sha256'])
    assert len(rig[2]) == 1


def test_damaged_bundled_bytes_cannot_reach_scanner(rig, monkeypatch):
    entry=copy.deepcopy(addon_catalog.CATALOG[0])
    monkeypatch.setattr(addon_store,'CATALOG',[entry])
    monkeypatch.setattr(addon_packages,'_files',lambda: {entry['sha256']:'changed'})
    with pytest.raises(AddonError,match='integrity check'):
        addon_store.prepare(entry['id'],approved=True)
    assert not rig[2] and not rig[1]


def test_existing_exact_review_survives_source_becoming_bundled(rig):
    review=addon_store.prepare('example',approved=True)
    # A prior app version omitted default eligibility fields on its curated entries.
    path=addon_store._stage(review['review_id'])/'review.json'
    receipt=addon_store._read(path)
    receipt['entry'].pop('import_ready',None)
    receipt['entry'].pop('discovery_only',None)
    addon_store._write(path,receipt)
    addon_store.CATALOG[0]['bundled_source']=True
    assert addon_store.review_saved(review['review_id'])['can_install']
    addon_store.CATALOG[0]['import_ready']=False
    assert not addon_store.review_saved(review['review_id'])['can_install']
    assert len(rig[2])==1, 'An unchanged source does not require a replacement scan'


def test_rejected_catalogue_entries_never_scan_or_download(rig, monkeypatch):
    entries=[copy.deepcopy(e) for e in addon_catalog.CATALOG if e.get('import_ready') is False]
    monkeypatch.setattr(addon_store,'CATALOG',entries)
    for entry in entries:
        with pytest.raises(AddonError, match='compatibility'):
            addon_store.prepare(entry['id'],approved=True)
    assert not rig[2] and not rig[1]


@pytest.mark.parametrize('shape',['traversal','duplicate','link'])
def test_snapshot_builder_never_extracts_or_follows_archive_files(shape):
    from tools.build_addon_snapshot import archive_source
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:gz') as archive:
        item=tarfile.TarInfo('root/../outside' if shape=='traversal' else 'root/SKILL.md')
        if shape=='link':
            item.type=tarfile.SYMTYPE; item.linkname='/private/secret'
        else:item.size=4
        archive.addfile(item, io.BytesIO(b'test') if shape!='link' else None)
        if shape=='duplicate':archive.addfile(item,io.BytesIO(b'test'))
    if shape=='link':
        tree, files=archive_source(output.getvalue())
        assert tree[0]['mode']=='120000' and not files
    else:
        with pytest.raises((ValueError,AddonError)):
            archive_source(output.getvalue())
