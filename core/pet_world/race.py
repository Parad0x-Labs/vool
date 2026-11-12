"""Pure local races. Play results have no route back into growth or execution."""
from __future__ import annotations

import math
from itertools import pairwise

from core.companion.lifeform.competition import race_outcome
from core.pet_world.catalog import SIZE
from core.pet_world.model import require, terrain

DEFAULT_STATS = dict(speed=30, focus=30, rigor=30, insight=30)


def stats_for(identifier, lifeform=None):
    result = dict(DEFAULT_STATS)
    if identifier != 'lifeform' or not lifeform or not lifeform.get('verified'):
        return result
    state = lifeform.get('state') or {}
    energy = state.get('energy_game')
    if type(energy) not in (int, float) or not math.isfinite(energy) or energy < 0:
        return result
    bonus = min(30, int(energy)//350)
    result = {key: value+bonus for key, value in result.items()}
    trait = {'MONOLITH': 'insight', 'FORGE': 'rigor', 'WISP': 'focus', 'DRIFT': 'speed'}.get(
        (state.get('genome') or {}).get('archetype'))
    if trait:
        result[trait] += 8
    return result


def track_for(yard):
    ground = terrain(yard)
    cells = {i for i, top in enumerate(ground['tops']) if top == 'track'}
    require(len(cells) >= 2 and all(ground['walkable'][i] for i in cells), 'track')
    neighbors = {}
    for cell in cells:
        x, y = cell % SIZE, cell // SIZE
        adjacent = {yy*SIZE+xx for xx, yy in ((x-1, y), (x+1, y), (x, y-1), (x, y+1))
                    if 0 <= xx < SIZE and 0 <= yy < SIZE and yy*SIZE+xx in cells}
        require(1 <= len(adjacent) <= 2 and all(abs(ground['heights'][cell]-ground['heights'][n]) <= 1 for n in adjacent), 'track')
        neighbors[cell] = adjacent
    ends = sorted(c for c in cells if len(neighbors[c]) == 1)
    require(len(ends) in (0, 2), 'track')
    current = ends[0] if ends else min(cells)
    path, visited = [], set()
    while current not in visited:
        visited.add(current)
        path.append(current)
        options = sorted(neighbors[current]-visited)
        if not options:
            break
        current = options[0]
    require(visited == cells, 'track')
    if not ends:
        path.append(path[0])
    segments = []
    for i, (first, second) in enumerate(pairwise(path)):
        slope = ground['heights'][first] != ground['heights'][second]
        turn = i > 0 and second-first != first-path[i-1]
        segments.append({'kind': 'hills' if slope else 'chicane' if turn else 'drag' if i%2 else 'straight', 'length': 1})
    return {'segments': segments}, [{'x': c % SIZE, 'y': c // SIZE} for c in path]


def race(yard, seed, lifeform=None):
    require(type(seed) is str and 0 < len(seed) <= 128 and seed.isascii() and seed.isprintable())
    require(bool(yard['pets']), 'pets')
    track, path = track_for(yard)
    participants = [{'lifeform_id': p['id'], 'stats': stats_for(p['id'], lifeform)}
                    for p in sorted(yard['pets'], key=lambda p: p['id'])]
    result = race_outcome(participants, track, seed)
    return {'finish_order': result['finish_order'], 'times': result['times'], 'race_id': result['race_id'],
            'path': path, 'notice': 'growth_unverified' if any(p['id'] == 'lifeform' for p in yard['pets'])
            and not (lifeform or {}).get('verified') else ''}
