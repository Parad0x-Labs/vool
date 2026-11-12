"""Inert, content-addressed world snapshots; a digest is not author authentication."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from core.pet_world.catalog import MAX_BYTES, SIZE
from core.pet_world.model import integer, keys, loads, plain_name, require, validate
from core.pet_world.standard import STANDARD_ID

WORLD_BYTES = MAX_BYTES+8192
DIGEST = re.compile('[a-f0-9]{64}')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def materialize(world, revision=0, preferences=None):
    yard = dict(schema='pet_yard_v1', version=1, yard_id='home', size=[SIZE, SIZE], revision=revision,
                preferences=deepcopy(preferences or {'motion': 'full'}))
    for key in ('name', 'columns', 'decorations', 'pets'):
        yard[key] = deepcopy(world[key])
    return validate(canonical(yard))[0]


def decode(raw):
    world = loads(raw, WORLD_BYTES)
    keys(world, ('schema', 'version', 'standard', 'name', 'description', 'columns', 'decorations', 'pets', 'digest'))
    require(world['schema'] == 'vool.world.v1' and integer(world['version'], 1, 1)
            and world['standard'] == STANDARD_ID)
    require(plain_name(world['description'], 300), 'invalid')
    require(type(world['digest']) is str and DIGEST.fullmatch(world['digest']), 'invalid')
    body = {k: v for k, v in world.items() if k != 'digest'}
    require(hashlib.sha256(canonical(body).encode()).hexdigest() == world['digest'], 'corrupt')
    yard = materialize(world)
    require(all(p['id'] != 'lifeform' for p in yard['pets']), 'growth_unverified')
    return world


def export(raw, description=''):
    yard, _ = validate(raw)
    require(plain_name(description, 300), 'invalid')
    world = dict(schema='vool.world.v1', version=1, standard=STANDARD_ID, description=description)
    for key in ('name', 'columns', 'decorations', 'pets'):
        world[key] = deepcopy(yard[key])
    world['pets'] = [p for p in world['pets'] if p['id'] != 'lifeform']
    world['digest'] = hashlib.sha256(canonical(world).encode()).hexdigest()
    raw = canonical(world)
    decode(raw)
    return raw
