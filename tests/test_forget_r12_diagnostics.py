"""Round 12 diagnostics lane: refusal diagnostics never serve withheld prose.

Fresh stories, independent of the round 7 review's wording and fields: a spa
gate keypad code withheld under structural field names the review did not
name, a barrier routed through a dependent second home, an initialized
authority that becomes unreadable or is lost, and a healthy forget whose
completed tokens keep diagnostics clean while another chat's rows survive.
"""
import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures('_hash_backend')

SECRET = 'OCEAN77'
BODY = 'The spa gate keypad code is OCEAN77 for the evening shift.'


def _home(tmp_path, monkeypatch):
    from core import runtime_paths

    home = _profile(tmp_path)
    monkeypatch.setattr(runtime_paths, '_VOOL_HOME_OVERRIDE', home)
    return home


def test_structural_field_names_do_not_admit_withheld_prose(tmp_path, monkeypatch):
    from core import context_retrieval as cr
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _home(tmp_path, monkeypatch)
    with VoolMemory(runtime_home=home):
        pass
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(
        memory_store_count=2,
        omission_reason=BODY,
        query_shape=BODY,
        estimate_method=BODY,
        evidence_compiler={
            'reason': 'coverage-short',
            'hop_reason': BODY,
            'assurance': BODY,
            'coverage': {'unseen_receipts': [BODY, 'receipt-9'], 'exhaustive': False},
        },
    )
    revocation.begin('spa-evening', SECRET, home)
    served = cr.get_last_retrieval_telemetry()
    assert SECRET not in str(served)
    assert served['memory_store_count'] == 2
    assert served['evidence_compiler']['reason'] == 'coverage-short'
    assert served['evidence_compiler']['coverage'] == {
        'unseen_receipts': ['receipt-9'], 'exhaustive': False}


def test_routed_dependent_home_barrier_withholds_diagnostics(tmp_path, monkeypatch):
    from core import context_retrieval as cr
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _home(tmp_path, monkeypatch)
    retreat = tmp_path / 'retreat-home'
    retreat.mkdir()
    with VoolMemory(runtime_home=home):
        pass
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(memory_store_count=1, memory_store_status=BODY)
    revocation.begin_group('spa-routing', SECRET, [home, retreat])
    served = cr.get_last_retrieval_telemetry()
    assert SECRET not in str(served)
    assert served['memory_store_count'] == 1
    assert served['memory_store_status'] == 'not_run'


@pytest.mark.parametrize('damage', ['corrupt', 'lost'])
def test_damaged_authority_withholds_every_diagnostic_string(tmp_path, monkeypatch, damage):
    from core import context_retrieval as cr
    from core.memory import revocation
    from core.vool_memory import VoolMemory

    home = _home(tmp_path, monkeypatch)
    with VoolMemory(runtime_home=home):
        pass
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(memory_store_count=1, omission_reason=BODY)
    revocation.begin('spa-damaged', SECRET, home)
    ledger = home / '.forget-authority' / 'ledger.json'
    if damage == 'corrupt':
        ledger.write_text('{"version": 2, "operations": sur', encoding='utf-8')
    else:
        ledger.unlink()
    served = cr.get_last_retrieval_telemetry()
    assert SECRET not in str(served)
    assert served['memory_store_count'] == 1
    assert not served.get('omission_reason')


def test_completed_forget_keeps_diagnostics_clean_and_other_rows_alive(tmp_path, monkeypatch):
    from core import context_retrieval as cr
    from core.memory.files import conversation_log_path, load_jsonl
    from core.persistent_memory import maybe_handle_memory_command

    home = _home(tmp_path, monkeypatch)
    _ingest(home, 'spa-main', [(1761000000, BODY)])
    _ingest(home, 'spa-bystander', [(1761000001, 'The morning shift starts at six.')])
    handled, reply = maybe_handle_memory_command(
        'forget OCEAN77', session_id='spa-main',
        source_context={'chat_id': 'spa-main', 'runtime_home': str(home),
                        'client_turn_id': 'r12-diag-positive'})
    assert handled and 'Forget applied' in reply, reply
    rows = load_jsonl(conversation_log_path())
    assert any(row.get('session_id') == 'spa-bystander' for row in rows)
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(memory_store_count=1, memory_store_status=BODY)
    after_forget = cr.get_last_retrieval_telemetry()
    assert SECRET not in str(after_forget)
    assert after_forget['memory_store_count'] == 1
    cr.reset_retrieval_telemetry()
    cr.update_retrieval_telemetry(memory_store_count=1, memory_store_status='stored',
                                  evidence_compiler={'reason': 'delivered'})
    clean = cr.get_last_retrieval_telemetry()
    assert clean['memory_store_status'] == 'stored'
    assert clean['evidence_compiler'] == {'reason': 'delivered'}
