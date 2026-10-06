"""Development repro: metadata is provenance, never statement identity."""
from tests.test_overnight_source_structure import source_env, _store, _recall, TABLE_BODY, TABLE_ASK


def test_unrelated_correction_cannot_supersede_dated_roster(source_env):
    _store(source_env, 'roster-envelope',
           'Session date: 2024/11/03 (Sun) 09:00\nPlease draft the lantern workshop rotation.',
           TABLE_BODY, 1730624400)
    _store(source_env, 'roster-envelope',
           'Session date: 2024/11/04 (Mon) 09:00\nCorrection: our holiday cottage has no balcony; the balcony was at the lodge.',
           'The cottage correction is noted.', 1730710800)
    capsule = _recall(source_env, 'roster-envelope', TABLE_ASK)
    assert '| Tuesday | Tomas | Iris |' in capsule, capsule
    assert '06:00-12:00' in capsule and '12:00-18:00' in capsule, capsule


def test_same_subject_dated_correction_still_replaces_old_state(source_env):
    _store(source_env, 'same-subject-envelope',
           'Session date: 2024/11/03 (Sun) 09:00\nThe pottery class begins at 16:20.',
           'Recorded.', 1730624400)
    _store(source_env, 'same-subject-envelope',
           'Session date: 2024/11/04 (Mon) 09:00\nCorrection: the pottery class begins at 17:10.',
           'Recorded.', 1730710800)
    capsule = _recall(source_env, 'same-subject-envelope', 'When does the pottery class begin?')
    assert '17:10' in capsule and '16:20' not in capsule, capsule


def test_date_bearing_assertion_is_not_an_envelope(source_env):
    _store(source_env, 'dated-assertion',
           'On 2024-11-06, the reserve pavilion was painted violet.', 'Recorded.', 1730883600)
    capsule = _recall(source_env, 'dated-assertion', 'What color was the reserve pavilion painted?')
    assert 'violet' in capsule and '2024-11-06' in capsule, capsule
