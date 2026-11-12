"""Bounded yard documents, without scripts, dates, work records or disk paths."""
from __future__ import annotations

import json
import re

from core.pet_world.catalog import (
    BLOCKS,
    DECORATIONS,
    MAX_BYTES,
    MAX_DECORATIONS,
    MAX_HEIGHT,
    MAX_ROSTER,
    ROSTER,
    SIZE,
)

MAX_REVISION = 2**53 - 1
NAME_ASCII_ONLY = False
CUSTOM_ID = re.compile(r'custom-[a-f0-9]{64}')


class YardError(ValueError):
    def __init__(self, code='invalid', status=400):
        super().__init__(code)
        self.code, self.status = code, status


def require(condition, code='invalid', status=400):
    if not condition:
        raise YardError(code, status)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected))


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def plain_name(value, maximum=24):
    return (type(value) is str and len(value) <= maximum and value.isprintable()
            and '<' not in value and '>' not in value and (not NAME_ASCII_ONLY or value.isascii())) or value == ''


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def loads(raw, limit=MAX_BYTES):
    require(type(raw) is str)
    try:
        length = len(raw.encode('utf-8'))
    except UnicodeError as exc:
        raise YardError() from exc
    require(length <= limit, 'size', 413)
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: require(False))
    except (ValueError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, YardError):
            raise
        raise YardError() from exc


def footprint(item):
    width, depth = DECORATIONS[item['id']]['size']
    if item['rotation'] % 2:
        width, depth = depth, width
    return [(item['x']+x, item['y']+y) for y in range(depth) for x in range(width)]


def terrain(yard):
    heights = [sum(run[1] for run in c) for c in yard['columns']]
    tops = [c[-1][0] if c else '' for c in yard['columns']]
    blocked, bridges = set(), set()
    for item in yard['decorations']:
        cells = {y*SIZE+x for x, y in footprint(item)}
        if item['id'] == 'bridge':
            bridges |= cells
        elif DECORATIONS[item['id']]['blocking']:
            blocked |= cells
    walkable = [bool(heights[i]) and (tops[i] != 'water' or i in bridges) and i not in blocked for i in range(SIZE*SIZE)]
    return {'heights': heights, 'tops': tops, 'walkable': walkable}


def validate_column(column):
    require(type(column) is list and len(column) <= MAX_HEIGHT, 'height')
    total, previous = 0, None
    for run in column:
        require(type(run) is list and len(run) == 2 and type(run[0]) is str and run[0] in BLOCKS, 'block')
        require(integer(run[1], 1, MAX_HEIGHT) and run[0] != previous, 'height')
        previous = run[0]
        total += run[1]
    require(total <= MAX_HEIGHT, 'height')


def validate(raw):
    yard = loads(raw)
    keys(yard, ('schema', 'version', 'yard_id', 'name', 'size', 'columns', 'decorations', 'pets', 'revision', 'preferences'))
    require(yard['schema'] == 'pet_yard_v1' and integer(yard['version'], 1, 1)
            and yard['yard_id'] == 'home' and type(yard['size']) is list
            and len(yard['size']) == 2 and all(integer(n, SIZE, SIZE) for n in yard['size']))
    require(plain_name(yard['name']), 'name')
    require(integer(yard['revision'], 0, MAX_REVISION))
    require(type(yard['columns']) is list and len(yard['columns']) == SIZE*SIZE, 'grid')
    for column in yard['columns']:
        validate_column(column)
    require(type(yard['decorations']) is list and len(yard['decorations']) <= MAX_DECORATIONS, 'decor')
    occupied = set()
    for item in yard['decorations']:
        keys(item, ('id', 'x', 'y', 'rotation'))
        require(type(item['id']) is str and item['id'] in DECORATIONS, 'decor')
        require(integer(item['x'], 0, SIZE-1) and integer(item['y'], 0, SIZE-1)
                and integer(item['rotation'], 0, 3), 'decor')
        levels = set()
        for x, y in footprint(item):
            require(x < SIZE and y < SIZE and (x, y) not in occupied, 'decor')
            column = yard['columns'][y*SIZE+x]
            require(column and (column[-1][0] != 'water' or item['id'] == 'bridge'), 'decor')
            occupied.add((x, y))
            levels.add(sum(run[1] for run in column))
        require(len(levels) == 1, 'decor')
    require(type(yard['pets']) is list and len(yard['pets']) <= MAX_ROSTER+1, 'pets')
    ground, ids, roster = terrain(yard), set(), 0
    for pet in yard['pets']:
        keys(pet, ('id', 'x', 'y'))
        identifier = pet['id']
        require(type(identifier) is str and (identifier in (*ROSTER, 'lifeform') or CUSTOM_ID.fullmatch(identifier)), 'pets')
        require(identifier not in ids and integer(pet['x'], 0, SIZE-1) and integer(pet['y'], 0, SIZE-1), 'pets')
        require(ground['walkable'][pet['y']*SIZE+pet['x']], 'pet_ground')
        ids.add(identifier)
        roster += identifier != 'lifeform'
    require(roster <= MAX_ROSTER, 'pets')
    prefs = yard['preferences']
    keys(prefs, ('motion',))
    require(prefs['motion'] in ('full', 'reduced'))
    canonical = json.dumps(yard, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    require(len(canonical.encode('utf-8')) <= MAX_BYTES, 'size', 413)
    return yard, canonical


def starter():
    columns = []
    for y in range(SIZE):
        for x in range(SIZE):
            kind = 'water' if min(x, y, SIZE-1-x, SIZE-1-y) < 2 else 'grass'
            if y == 8 and 6 <= x <= 18:
                kind = 'track'
            if 9 <= x <= 16 and 13 <= y <= 15:
                kind = 'wood'
            columns.append([[kind, 1]])
    decorations = [dict(id=kind, x=x, y=y, rotation=0) for kind, x, y in (
        ('tree', 4, 4), ('tree', 19, 18), ('moon-gate', 10, 4), ('bamboo', 18, 5),
        ('lantern', 8, 5), ('wind-bell', 15, 5), ('pond', 5, 14), ('desk', 13, 13),
        ('bench', 10, 15), ('cushion', 16, 14), ('flowers', 5, 11), ('tea-table', 15, 16),
        ('paper-crane', 11, 14), ('stone-lion', 19, 10))]
    return {'schema': 'pet_yard_v1', 'version': 1, 'yard_id': 'home', 'name': '', 'size': [SIZE, SIZE],
            'columns': columns, 'decorations': decorations,
            'pets': [dict(id=identifier, x=x, y=11) for identifier, x in zip(
                ('spark', 'beetle', 'lumen-fox', 'dusk-lynx'), (5, 9, 13, 17), strict=True)],
            'revision': 0, 'preferences': {'motion': 'full'}}
