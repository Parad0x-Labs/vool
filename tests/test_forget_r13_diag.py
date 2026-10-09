"""Round 13 diagnostics lane: pending erasure withholds every non-code string.

Fresh stories, independent of the round 8 review's wording and fields: a west
greenhouse irrigation controller whose night-cycle PIN is withheld companion
prose under a FERRO11 barrier, a routed dependent home pair, and a swarm-store
registry audit over aliases, factories and annotated assignments. Codes,
counts and safe identifiers stay legible; sentence prose never does.
"""
from pathlib import Path

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')

TOKEN = 'FERRO11'
PIN_PROSE = 'The west irrigation controller pin is HALO76 for the night cycle.'
IRRIGATION_BODY = 'The west greenhouse irrigation pin is HALO76.'


def _home(tmp_path, monkeypatch):
    from core import runtime_paths

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    return home


def test_pending_erasure_withholds_companion_prose_from_diagnostic_slots(tmp_path, monkeypatch):
    from core import context_retrieval as cr
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _home(tmp_path, monkeypatch)
    with VoolMemory(runtime_home=home):
        pass
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(
        memory_store_count=4,
        evidence_compiler={'missing_before_hop': [PIN_PROSE], 'required': [PIN_PROSE],
                           'reason': 'coverage-short', 'receipts_in_chat': 2},
        recall_supplement={'skipped': {PIN_PROSE: 3, 'delivery-refused': 1}},
        whole_turn_lane_refused=[{'occurrence_id': '0f4a92c1-8b3d-4e2f-9a01-7c5d84e6f210',
                                  'reason': 'revalidation:missing', 'body': PIN_PROSE}],
        source_session_ids=['greenhouse-west'],
    )
    revocation.begin('greenhouse-west', TOKEN, home)
    served = cr.get_last_retrieval_telemetry()
    assert 'HALO76' not in str(served), served
    assert served['memory_store_count'] == 4
    assert served['evidence_compiler']['reason'] == 'coverage-short'
    assert served['recall_supplement']['skipped'] == {'delivery-refused': 1}
    assert served['whole_turn_lane_refused'] == [
        {'occurrence_id': '0f4a92c1-8b3d-4e2f-9a01-7c5d84e6f210', 'reason': 'revalidation:missing'}]
    assert served['source_session_ids'] == ['greenhouse-west']


def test_routed_home_barrier_withholds_companion_prose_from_code_lists(tmp_path, monkeypatch):
    from core import context_retrieval as cr
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _home(tmp_path, monkeypatch)
    retreat = tmp_path / 'retreat-east'
    retreat.mkdir()
    with VoolMemory(runtime_home=home):
        pass
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(
        memory_store_count=9,
        evidence_compiler={'reason_codes': [PIN_PROSE, 'facts:4'], 'omission_reason': PIN_PROSE})
    revocation.begin_group('greenhouse-east', TOKEN, [home, retreat])
    served = cr.get_last_retrieval_telemetry()
    assert 'HALO76' not in str(served), served
    assert served['evidence_compiler']['reason_codes'] == ['facts:4']
    assert served['memory_store_count'] == 9
    assert not served.get('omission_reason')


@pytest.mark.parametrize('assignment', [
    'public_recent_contexts = get_recent_contexts',
    'public_recent_contexts = functools.partial(get_recent_contexts)',
    'public_recent_contexts: object = get_recent_contexts',
])
def test_registry_rejects_public_callable_aliases_on_the_swarm_store(assignment):
    import core.memory.revocation as revocation
    from core.memory.reader_registry import _audit

    root = Path(revocation.__file__).resolve().parents[2]
    module = 'storage/swarm_memory.py'
    source = (root / module).read_text() + '\nimport functools\n' + assignment + '\n'
    errors = _audit(root, source_overrides={module: source})
    print('SWARM_ALIAS_AUDIT', assignment, errors)
    assert any('unclassified' in e and module in e for e in errors), errors


def test_healthy_forget_keeps_diagnostics_clean_and_bystander_rows_alive(tmp_path, monkeypatch):
    from core import context_retrieval as cr
    from core.memory.files import conversation_log_path, load_jsonl
    from core.persistent_memory import maybe_handle_memory_command

    home = _home(tmp_path, monkeypatch)
    _ingest(home, 'greenhouse-main', [(1761500000, IRRIGATION_BODY)])
    _ingest(home, 'greenhouse-bystander', [(1761500001, 'The morning shift starts at six.')])
    handled, reply = maybe_handle_memory_command(
        'forget HALO76', session_id='greenhouse-main',
        source_context={'chat_id': 'greenhouse-main', 'runtime_home': str(home),
                        'client_turn_id': 'r13-diag-positive'})
    assert handled and 'Forget applied' in reply, reply
    rows = load_jsonl(conversation_log_path())
    assert any(row.get('session_id') == 'greenhouse-bystander' for row in rows)
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(
        memory_store_count=2, memory_store_reason=IRRIGATION_BODY,
        evidence_compiler={'reason': 'delivered', 'reason_codes': ['facts:2']})
    served = cr.get_last_retrieval_telemetry()
    assert 'HALO76' not in str(served), served
    assert served['memory_store_count'] == 2
    assert served['evidence_compiler'] == {'reason': 'delivered', 'reason_codes': ['facts:2']}
