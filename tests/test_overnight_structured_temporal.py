"""Structured assistant history is state of the selected relation, not its prose."""
from tests.test_overnight_source_structure import TABLE_ASK, TABLE_BODY, _recall, _store, source_env


def test_unrelated_retraction_sharing_intro_cannot_withdraw_roster(source_env):
    _store(source_env, 'relation-state', 'Please draft the lantern workshop rotation.', TABLE_BODY,
           1730624400)
    _store(source_env, 'relation-state',
           'Correction: the lantern workshop pottery stock is now 14 plates; our previous stock sheet was wrong.',
           'The pottery stock correction is noted.', 1730710800)
    capsule = _recall(source_env, 'relation-state', TABLE_ASK)
    assert '| Tuesday | Tomas | Iris |' in capsule, capsule
    assert '06:00-12:00' in capsule and '12:00-18:00' in capsule, capsule


def test_historical_roster_versions_keep_their_own_dates(source_env):
    _store(source_env, 'relation-revision', 'Please draft the lantern workshop rotation.', TABLE_BODY,
           1730624400)
    changed = TABLE_BODY.replace('| Tuesday | Tomas | Iris |', '| Tuesday | Iris | Tomas |')
    _store(source_env, 'relation-revision', 'Update the lantern workshop rotation.', changed,
           1730710800)
    capsule = _recall(source_env, 'relation-revision', TABLE_ASK)
    # This question asks about prior history, not current state. Both
    # versions are admissible only with their own dates; an older version
    # must never acquire the newer source's attribution.
    assert 'assistant said (stated 2024-11-04): | Tuesday | Iris | Tomas |' in capsule, capsule
    assert 'assistant said (stated 2024-11-03): | Tuesday | Tomas | Iris |' in capsule, capsule
