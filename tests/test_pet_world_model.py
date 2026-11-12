from __future__ import annotations

import json

import pytest

from core.pet_world.model import YardError, starter, validate


def test_starter_and_a_different_eight_high_build_roundtrip():
    yard, canonical = validate(json.dumps(starter()))
    assert validate(canonical)[0] == yard
    yard['columns'][20*24+20] = [['stone', 7], ['grass', 1]]
    yard['name'] = '月の庭'
    assert validate(json.dumps(yard))[0] == yard
    yard['pets'] = []
    assert validate(json.dumps(yard))[0]['pets'] == []


@pytest.mark.parametrize('mutation', [
    lambda y: y.update(columns=y['columns']*2),
    lambda y: y['columns'].__setitem__(0, [['unknown', 1]]),
    lambda y: y['columns'].__setitem__(0, [['grass', 9]]),
    lambda y: y['columns'].__setitem__(0, [['grass', True]]),
    lambda y: y['columns'].__setitem__(0, [['grass', 1], ['grass', 1]]),
    lambda y: y.update(name='x'*25),
    lambda y: y.update(name='\u202ename'),
    lambda y: y['decorations'][0].update(rotation=4),
    lambda y: y['decorations'][0].update(x=24),
    lambda y: y['decorations'].append(dict(y['decorations'][0])),
    lambda y: y['pets'][0].update(x=0),
    lambda y: y['pets'].append(dict(y['pets'][0])),
    lambda y: y.update(revision=True),
    lambda y: y.update(revision=2**53),
    lambda y: y.update(script='run'),
    lambda y: y['preferences'].update(sound='true'),
])
def test_hostile_documents_refuse(mutation):
    yard = starter()
    mutation(yard)
    with pytest.raises(YardError):
        validate(json.dumps(yard))


@pytest.mark.parametrize('raw', ['{"schema":1,"schema":2}', '{"x":NaN}', '['*2000+']'*2000, '\ud800', ' '*262145])
def test_json_parser_refuses_ambiguous_deep_and_oversized_input(raw):
    with pytest.raises(YardError):
        validate(raw)
