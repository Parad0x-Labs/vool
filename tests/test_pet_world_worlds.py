from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.pet_world import creator, worlds
from core.pet_world.model import YardError, starter


def recipe():
    return {'name': '星の庭', 'description': 'A place to meet and build.', 'base': [['grass', 1]],
            'fills': [{'x': 5, 'y': 5, 'width': 3, 'depth': 2, 'stack': [['water', 1]]}],
            'decorations': [{'id': 'tree', 'x': 12, 'y': 8, 'rotation': 0}],
            'pets': [{'id': 'lumen-fox', 'x': 10, 'y': 10}]}


def test_world_snapshots_are_deterministic_and_separate_local_growth_and_preferences():
    yard = starter()
    yard['pets'].append({'id': 'lifeform', 'x': 10, 'y': 10})
    yard['revision'] = 42
    raw = worlds.export(json.dumps(yard), 'Meet here.')
    assert raw == worlds.export(json.dumps(yard), 'Meet here.')
    world = worlds.decode(raw)
    assert world['standard'] == 'vool-yard.v1'
    assert 'revision' not in world and 'preferences' not in world
    assert all(p['id'] != 'lifeform' for p in world['pets'])
    local = worlds.materialize(world, revision=7, preferences={'motion': 'reduced'})
    assert local['revision'] == 7 and local['preferences']['motion'] == 'reduced'
    assert local['columns'] == yard['columns']
    world['name'] = 'Changed'
    with pytest.raises(YardError, match='corrupt'):
        worlds.decode(json.dumps(world))


def test_world_refusals_use_the_same_geometry_owner():
    source = recipe()
    raw = creator.build(source)
    for change in [lambda s: s['fills'][0].update(width=30),
                   lambda s: s['fills'][0].update(stack=[['foreign', 1]]),
                   lambda s: s['fills'][0].update(stack=[['cloud', 9]]),
                   lambda s: s['pets'][0].update(x=5, y=5),
                   lambda s: s.update(script='run'),
                   lambda s: s.update(description='<script>'),
                   lambda s: s['fills'][0].update(x=True)]:
        source = recipe()
        change(source)
        with pytest.raises(YardError):
            creator.build(source)
    with pytest.raises(YardError):
        worlds.decode(raw.replace('"version":1', '"version":1,"version":1'))
    with pytest.raises(YardError):
        worlds.decode(' '*(worlds.WORLD_BYTES+1))
    assert worlds.decode(raw)['name'] == '星の庭'


def test_native_world_creator_cli_preview_and_workspace_boundaries(tmp_path):
    from core.native_skill_library import load_native_library

    library = load_native_library(Path('skills'))
    assert not library.invalid
    assert next(s for s in library.contracts if s.id == 'create-world').risk_class == 'workspace_write'
    (tmp_path/'recipe.json').write_text(json.dumps(recipe()))
    command = [sys.executable, '-m', 'core.pet_world.creator', '--workspace', str(tmp_path),
               '--source', 'recipe.json', '--output', 'garden.voolworld']
    result = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert worlds.decode((tmp_path/'garden.voolworld').read_text())['name'] == '星の庭'
    from PIL import Image
    with Image.open(tmp_path/'garden.png') as image:
        assert image.size == (832, 640)
        assert len(image.getcolors(832*640)) > 4
    before = (tmp_path/'garden.voolworld').read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert (tmp_path/'garden.voolworld').read_bytes() == before
    with pytest.raises(YardError):
        creator.main(['--workspace', str(tmp_path), '--source', 'recipe.json', '--output', '../escape.voolworld'])
