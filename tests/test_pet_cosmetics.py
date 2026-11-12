from __future__ import annotations

import array
import base64
import json
import math
import wave
from io import BytesIO

import pytest
from PIL import Image

from core import pet_cosmetics as c
from core.pet_world.model import YardError, starter
from storage import pet_cosmetics_store as store
from storage import pet_world_store
from storage.db import get_connection
from tests._pet_world_support import home as home


def sound():
    out = BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(array.array('h', (int(24000*math.sin(i/10)) for i in range(24000))).tobytes())
    return out.getvalue()


def pack():
    out = BytesIO()
    Image.new('RGBA', (48, 48), '#76bad5').save(out, 'PNG')
    return dict(schema='vool.pet.v1', version=1, id='cloud-cat', name='雲猫', author='sls_0x', license='CC0-1.0',
                description='A little cloud cat', atlas={'cell': 48, 'png': base64.b64encode(out.getvalue()).decode()},
                frames={s: {d: [0] for d in c.DIRECTIONS} for s in c.STATES},
                clips={'cheer': {'name': '応援', 'wav': base64.b64encode(sound()).decode()}},
                bindings={'success': {'expression': 'success', 'sound': 'cheer'}})


def test_import_export_assign_restart_and_reference_safe_removal(home):
    first = store.change('import', {'revision': 0, 'document': json.dumps(pack())})
    identifier = first['pets'][0]['id']
    assert identifier.startswith('custom-') and first['config']['sound'] is False
    exported = store.export(identifier)
    assert c.validate_pack(exported)[2] == identifier[7:]
    assert store.change('import', {'revision': 1, 'document': exported})['pets'] == first['pets']
    config = first['config']
    config.update(selected=identifier, sound=True)
    config['bindings'][identifier] = {'success': {'expression': 'success', 'sound': 'builtin:deep-ja-happy'}}
    assigned = store.change('configure', {'revision': 2, 'config': config})
    assert store.library()['config'] == assigned['config']
    with pytest.raises(YardError, match='conflict'):
        store.change('configure', {'revision': 2, 'config': config})
    yard = starter()
    yard['pets'][0]['id'] = identifier
    pet_world_store.save(json.dumps(yard))
    with pytest.raises(YardError, match='pet_in_yard'):
        store.change('remove', {'revision': 3, 'id': identifier, 'confirmed': True})
    assert store.library()['revision'] == 3
    yard = pet_world_store.load()['yard']
    yard['pets'] = []
    pet_world_store.save(json.dumps(yard))
    result = store.change('remove', {'revision': 3, 'id': identifier, 'confirmed': True})
    assert result['pets'] == [] and result['config']['selected'] == ''
    with get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM pet_cosmetic_assets_v1').fetchone()[0] == 0


def test_audio_normalization_idempotence_and_standalone_binding_removal(home):
    normalized, seconds = c.wav(sound())
    assert c.wav(normalized) == (normalized, seconds)
    result = store.change('sound', {'revision': 0, 'name': '反応', 'wav': base64.b64encode(sound()).decode()})
    sha = result['sounds'][0]['id']
    assert store.asset(sha) == ('audio/wav', normalized)
    config = result['config']
    config['bindings'] = {'spark': {'success': {'expression': 'happy', 'sound': sha}}}
    store.change('configure', {'revision': 1, 'config': config})
    result = store.change('remove', {'revision': 2, 'id': sha, 'confirmed': True})
    assert result['config']['bindings']['spark']['success']['sound'] == ''


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(script='alert(1)'),
    lambda p: p['atlas'].update(png='https://invalid.test/a.png'),
    lambda p: p['atlas'].update(cell=True),
    lambda p: p['frames']['idle']['front'].append(1),
    lambda p: p['frames'].pop('unknown'),
    lambda p: p['bindings'].update(unknown={'expression': 'success', 'sound': ''}),
    lambda p: p['clips']['cheer'].update(wav=base64.b64encode(b'RIFFbad').decode()),
    lambda p: p.update(name='<script>'),
])
def test_hostile_packs_keep_previous_library(home, mutate):
    initial = store.change('import', {'revision': 0, 'document': json.dumps(pack())})
    bad = pack()
    mutate(bad)
    with pytest.raises(YardError):
        store.change('import', {'revision': 1, 'document': json.dumps(bad)})
    assert store.library() == initial


def test_corrupt_asset_options_and_transaction_rollback(home):
    state = store.change('import', {'revision': 0, 'document': json.dumps(pack())})
    sha = state['pets'][0]['atlas']['sha256']
    with get_connection() as conn:
        conn.execute('UPDATE pet_cosmetic_assets_v1 SET data=? WHERE digest=?', (b'bad', sha))
        conn.commit()
    with pytest.raises(YardError, match='corrupt'):
        store.asset(sha)
    with pytest.raises(YardError, match='corrupt'):
        store.change('import', {'revision': 1, 'document': json.dumps(pack())})
    with get_connection() as conn:
        conn.execute("UPDATE pet_cosmetic_options_v1 SET document='broken'")
        conn.commit()
    broken = store.library()
    assert broken['read_only'] and broken['notice'] == 'corrupt'
    with pytest.raises(YardError, match='corrupt'):
        store.change('configure', {'revision': 1, 'config': c.default_config()})
    restored = store.change('reset', {'revision': 1, 'stamp': broken['stamp'], 'confirmed': True})
    assert not restored['read_only']
    with get_connection() as conn:
        assert conn.execute('SELECT document FROM pet_cosmetic_options_backups_v1').fetchone()[0] == 'broken'
        conn.execute("CREATE TRIGGER fail_options BEFORE UPDATE ON pet_cosmetic_options_v1 BEGIN SELECT RAISE(ABORT,'disk'); END")
        conn.commit()
    with pytest.raises(YardError, match='unavailable'):
        store.change('sound', {'revision': 2, 'name': 'clip', 'wav': base64.b64encode(sound()).decode()})
    assert store.library() == restored


def test_native_creator_real_cli_exports_importable_art_and_refuses_escape(home, tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from core.native_skill_library import load_native_library
    from core.pet_cosmetics_creator import build

    loaded = load_native_library(Path('skills'))
    assert not loaded.invalid
    skill = next(s for s in loaded.contracts if s.id == 'create-pet')
    assert skill.risk_class == 'workspace_write'
    source = dict(id='moon-moth', name='月蛾', author='sls_0x', license='CC0-1.0', description='An original moth',
                  palette={'body': '#b9bbf0ff', 'eye': '#242b40ff'}, base=[[10, 10, 28, 34, 'body']],
                  states={s: [[[15+i%6, 20, 4, 6, 'eye']]] for i, s in enumerate(c.STATES)}, bindings={})
    workspace = tmp_path/'workspace'
    workspace.mkdir(exist_ok=True)
    (workspace/'drawing.json').write_text(json.dumps(source))
    result = subprocess.run([sys.executable, '-m', 'core.pet_cosmetics_creator', '--workspace', str(workspace),
                             '--source', 'drawing.json', '--output', 'moth.voolpet'], capture_output=True, text=True, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    raw = (workspace/'moth.voolpet').read_text()
    assert store.change('import', {'revision': 0, 'document': raw})['pets'][0]['name'] == '月蛾'
    assert (workspace/'moth.png').is_file()
    from core.pet_cosmetics_creator import main
    with pytest.raises(YardError, match='exists'):
        main(['--workspace', str(workspace), '--source', 'drawing.json', '--output', 'moth.voolpet'])
    with pytest.raises(YardError):
        main(['--workspace', str(workspace), '--source', 'drawing.json', '--output', '../escaped.voolpet'])
    source['base'] = [[47, 0, 2, 2, 'body']]
    with pytest.raises(YardError):
        build(source)
