"""Development repro and controls for stale co-tenant destination clauses."""
from tests.test_overnight_source_structure import _recall, _store, source_env


def _shuttle(home):
    _store(home, 'shuttle-destination', 'The evening shuttle leaves at 18:05 from the south gate.',
           'Recorded.', 1730624400)
    _store(home, 'shuttle-destination',
           'Correction: the evening shuttle now leaves at 18:35, the 18:05 slot went to the maintenance fleet.',
           'Recorded.', 1730710800)


def test_current_shuttle_does_not_keep_old_time_as_unrelated_destination(source_env):
    _shuttle(source_env)
    capsule = _recall(source_env, 'shuttle-destination', 'When does the evening shuttle leave?')
    assert '18:35' in capsule and '18:05' not in capsule, capsule


def test_explicit_old_slot_question_keeps_its_new_destination(source_env):
    _shuttle(source_env)
    capsule = _recall(source_env, 'shuttle-destination', 'Who uses the 18:05 slot now?')
    assert '18:05 slot went to the maintenance fleet' in capsule, capsule


def test_named_destination_without_stale_quantity_survives(source_env):
    _store(source_env, 'parcel-destination', 'The attic parcel goes to the archive.', 'Recorded.', 1730624400)
    _store(source_env, 'parcel-destination',
           'Correction: the old instruction is withdrawn, the attic parcel goes to the museum instead.',
           'Recorded.', 1730710800)
    capsule = _recall(source_env, 'parcel-destination', 'Where does the attic parcel go?')
    assert 'attic parcel goes to the museum instead' in capsule, capsule


def test_trimmed_correction_receipts_are_exact_source_slices(source_env):
    from core.context_retrieval import get_last_retrieval_telemetry
    _shuttle(source_env)
    _recall(source_env, 'shuttle-destination', 'When does the evening shuttle leave?')
    body = 'Correction: the evening shuttle now leaves at 18:35, the 18:05 slot went to the maintenance fleet.'
    trimmed = [r for r in get_last_retrieval_telemetry()['evidence_refs']
               if r.get('delivered') and r.get('revision_source_trimmed')]
    assert trimmed
    for receipt in trimmed:
        span = receipt['span']
        assert body[span['start']:span['end']] == span['text'], receipt
