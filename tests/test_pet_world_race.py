from __future__ import annotations

from copy import deepcopy

import pytest

from core.pet_world.model import YardError, starter
from core.pet_world.race import DEFAULT_STATS, race, stats_for


def test_same_participants_track_and_seed_reproduce_even_if_roster_order_changes():
    yard = starter()
    first = race(yard, 'home-round')
    yard['pets'].reverse()
    assert race(yard, 'home-round') == first
    assert len(first['path']) == 13
    assert set(first['finish_order']) == {p['id'] for p in yard['pets']}
    yard['pets'] = yard['pets'][:1]
    assert race(yard, 'time-trial')['finish_order'] == [yard['pets'][0]['id']]


def test_roster_stats_are_equal_and_unverified_lifeform_gets_the_same_defaults():
    assert stats_for('beetle') == stats_for('lumen-fox') == DEFAULT_STATS
    unverified = {'verified': False, 'state': {'energy_game': 100000}}
    assert stats_for('lifeform', unverified) == DEFAULT_STATS
    verified = {'verified': True, 'state': {'energy_game': 350, 'genome': {'archetype': 'FORGE'}}}
    snapshot = deepcopy(verified)
    assert stats_for('lifeform', verified)['rigor'] > stats_for('lifeform', verified)['speed']
    assert verified == snapshot
    yard = starter()
    yard['pets'].append({'id': 'lifeform', 'x': 12, 'y': 11})
    assert race(yard, 'seed', unverified)['notice'] == 'growth_unverified'


@pytest.mark.parametrize('bad', ['none', 'branch', 'gap', 'cliff', 'blocked'])
def test_unplayable_track_is_refused(bad):
    yard = starter()
    if bad == 'none':
        yard['columns'] = [[['grass', 1]] for _ in yard['columns']]
    if bad == 'branch':
        yard['columns'][7*24+10] = [['track', 1]]
    if bad == 'gap':
        yard['columns'][8*24+10] = [['grass', 1]]
    if bad == 'cliff':
        yard['columns'][8*24+10] = [['track', 8]]
    if bad == 'blocked':
        yard['decorations'].append({'id': 'tree', 'x': 10, 'y': 8, 'rotation': 0})
    with pytest.raises(YardError, match='track'):
        race(yard, 'seed')
