"""Output presentation is not a missing retained attribute. Contributor: sls_0x."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BODY = ('The fictional weather-vane station assigned to the willow bench is station P-29. '
        'Its inspection label is silver crescent. These are the complete recorded values for this station.')
OTHER = ('The maple cupboard locker code is L-64. Its recorded colour label is pale amber. '
         'These are the complete recorded values for this cupboard.')
QUESTION = ('Return exactly one valid JSON object with the sole key "station" and its recorded '
            'string value for the willow bench. Do not include prose or Markdown.')
CASES = {
    'station': ('presentation-source', QUESTION),
    'label': ('presentation-source', QUESTION.replace('"station"', '"inspection_label"')),
    'different_entity': ('presentation-source', QUESTION.replace('"station"', '"locker"').replace('willow bench', 'maple cupboard')),
    'absent_facet': ('presentation-source', QUESTION.replace('"station"', '"water temperature"')),
    'wrong_entity': ('presentation-source', QUESTION.replace('willow bench', 'granite fountain')),
    'wrong_person': ('presentation-source', QUESTION.replace('its recorded', "Elena Marin's recorded")),
    'ungranted': ('presentation-empty', QUESTION),
}


def _child():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('writer', 'reader'), required=True)
    parser.add_argument('--home', required=True)
    parser.add_argument('--home-root', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--case', default='')
    args = parser.parse_args()
    home = Path(args.home).resolve()
    root = Path(args.home_root).resolve()
    assert home != root and home.is_relative_to(root)
    os.environ.update(VOOL_HOME=str(home), VOOL_REGISTER_INSTALLED_OLLAMA_MODELS='0')
    sys.path.insert(0, str(REPO))
    import urllib.request

    import requests
    def deny(*args, **kwargs):
        raise RuntimeError('Offline source-presentation proof: network disabled')
    requests.sessions.Session.request = deny
    urllib.request.urlopen = deny
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path
    configure_runtime_home(home)
    configure_default_db_path(home / 'data/vool_web0_v2.db')
    from storage.migrations import run_migrations
    run_migrations()
    from core import context_retrieval as cr
    from core import embedding_service
    embedding_service._best_embed_model = lambda: None
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.vool_memory import VoolMemory
    result = {'pid': os.getpid(), 'phase': args.phase, 'network_disabled': True,
              'model_accuracy': 'not_measured', 'provider_calls': 0}
    if args.phase == 'writer':
        for chat in ('presentation-source', 'presentation-empty'):
            ensure_chat_namespace(chat, grant_current_receipts=False)
        written = []
        for body in (BODY, OTHER):
            receipt = cr.store_turn('presentation-source', body, 'Recorded.',
                access_policy=resolve_memory_access_policy(chat_id='presentation-source'),
                source_context={'runtime_home': str(home), 'statement_at': 1754092800.0})
            assert receipt['status'] in ('stored', 'retained'), receipt
            written.append(receipt)
        result['ingestion'] = written
    else:
        chat, question = CASES[args.case]
        os.environ['VOOL_CAPSULE_DEBUG_DROPS'] = '1'
        cr._CAPSULE_DEBUG_DROPS.clear()
        messages = cr.inject_retrieved(chat, question, [{'role': 'user', 'content': question}],
            access_policy=resolve_memory_access_policy(chat_id=chat),
            source_context={'runtime_home': str(home), 'chat_id': chat},
            env={'VOOL_CONTEXT_CAPSULE_V2': '1'})
        capsule = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        result.update(case=args.case, question=question, capsule=capsule,
            telemetry=cr.get_last_retrieval_telemetry(), actual_drop_log=cr._CAPSULE_DEBUG_DROPS,
            retained=[{'id': row.occurrence_id, 'body': row.body, 'integrity': row.body_integrity,
                       'body_sha256': hashlib.sha256(row.body.encode()).hexdigest()}
                      for row, _ in VoolMemory(runtime_home=home, agent_id=cr._AGENT_ID).occurrence_search(
                          'willow bench', chat_scope='presentation-source', limit=10)])
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    _child()
    raise SystemExit(0)

import pytest

from tests._restart_child_env import scrub_child_env


@pytest.fixture(scope='module')
def restarted_presentation_sources(tmp_path_factory):
    suffix = uuid.uuid4().hex[:10]
    root = tmp_path_factory.mktemp('presentation-restart').resolve()
    profiles = root / 'profiles'
    profiles.mkdir()
    home = profiles / ('presentation-restart-' + suffix)
    home.mkdir(parents=True, exist_ok=False)
    work = home / 'proof'
    work.mkdir()
    artifact = Path(os.environ.get('VOOL_PRESENTATION_RESTART_ARTIFACT_DIR', str(root / 'artifacts'))) / suffix
    artifact.mkdir(parents=True, exist_ok=False)
    env = scrub_child_env(os.environ)
    env.update(VOOL_HOME=str(home), TMPDIR=str(work), TMP=str(work), TEMP=str(work), PYTHONDONTWRITEBYTECODE='1')
    policy = work / 'worker.sb'
    q = lambda p: json.dumps(str(p))
    policy.write_text('\n'.join(['(version 1)', '(allow default)', '(deny network*)', '(deny file-write*)',
        '(deny file-read* (subpath "/Users"))',
        '(allow file-read* (subpath ' + q(Path(sys.base_prefix).resolve()) + '))',
        '(allow file-read* (subpath ' + q(Path(sys.prefix).resolve()) + '))',
        '(allow file-read* (subpath ' + q(REPO) + '))',
        '(allow file-read* (subpath ' + q(home) + '))',
        '(allow file-write* (subpath ' + q(home) + '))',
        '(allow file-write* (literal "/dev/null"))', '(allow file-read-metadata)']) + '\n')
    runs = []
    outputs = {}
    for case in ('writer', *CASES):
        phase = 'writer' if case == 'writer' else 'reader'
        out = work / (case + '.json')
        native_python = os.environ.get('VOOL_PRESENTATION_RESTART_PYTHON', sys.executable)
        assert Path(native_python).is_file()
        cmd = ['/usr/bin/sandbox-exec', '-f', str(policy), native_python, '-B', str(Path(__file__).resolve()),
               '--phase', phase, '--home', str(home), '--home-root', str(profiles), '--out', str(out)]
        if phase == 'reader': cmd.extend(['--case', case])
        started = time.time()
        proc = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
        (artifact / (case + '.stdout.log')).write_text(proc.stdout)
        (artifact / (case + '.stderr.log')).write_text(proc.stderr)
        assert proc.returncode == 0, {'case': case, 'stderr': proc.stderr[-4000:], 'artifact': str(artifact)}
        value = json.loads(out.read_text())
        (artifact / (case + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        outputs[case] = value
        runs.append({'case': case, 'pid': value['pid'], 'exit_code': proc.returncode,
                     'started': started, 'finished': time.time()})
    assert len({r['pid'] for r in runs}) == len(runs)
    assert all(b['started'] >= a['finished'] for a, b in zip(runs, runs[1:]))
    (artifact / 'PROCESS-RECEIPT.json').write_text(json.dumps({'runs': runs, 'profile': str(home)}, indent=2) + '\n')
    return outputs


@pytest.mark.parametrize('case,body', [('station', BODY), ('label', BODY), ('different_entity', OTHER)])
def test_fresh_ingestion_exit_restart_delivers_real_content_despite_json_presentation(restarted_presentation_sources, case, body):
    result = restarted_presentation_sources[case]
    assert body in result['capsule'], result
    refs = result['telemetry']['evidence_refs']
    assert any(r.get('delivered') and r.get('source_integrity') == 'verified' and
               body == r.get('span', {}).get('text') for r in refs), refs


@pytest.mark.parametrize('case', ['absent_facet', 'wrong_entity', 'wrong_person', 'ungranted'])
def test_presentation_does_not_bypass_real_missing_facet_entity_actor_or_scope(restarted_presentation_sources, case):
    result = restarted_presentation_sources[case]
    assert 'P-29' not in result['capsule'], result
    assert 'L-64' not in result['capsule'], result


def test_valid_json_delivery_contract_still_accepts_correct_shape():
    from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract
    contract = parse_raw_output_contract(QUESTION)
    assert contract is not None and contract.json_object
    result = apply_raw_output_contract('{"station":"P-29"}', contract)
    assert result.compliant and not result.rejected and json.loads(result.text) == {'station': 'P-29'}


def test_malformed_final_still_rejected_by_same_json_contract():
    from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract
    contract = parse_raw_output_contract(QUESTION)
    result = apply_raw_output_contract('{"station":"P-29"', contract)
    assert result.rejected and not result.compliant and not result.text


@pytest.mark.parametrize('text', [
    '{"station":"P-29","format":"Return one valid JSON object"}',
    'What JSON key and string value did the willow bench register contain?',
    'What prose was recorded on the willow bench?',
    'Explain the quoted instruction "Return exactly one valid JSON object with the sole key station".',
    'Return exactly "Do not include prose or Markdown" and nothing else.',
    'The guide says return one valid JSON object with the sole key station. Explain that guide.',
])
def test_content_view_preserves_inline_data_substantive_format_terms_quotes_and_literals(text):
    from core import raw_output_contract as roc
    authority = getattr(roc, 'request_content_for_retrieval', None)
    assert callable(authority), 'The requested-content authority is absent'
    assert authority(text) == text


@pytest.mark.parametrize('text,retained', [
    (QUESTION, ['station', 'recorded', 'value', 'willow bench']),
    (QUESTION.replace('"station"', '"water temperature"'), ['water temperature', 'willow bench']),
    ('What is the willow bench station? Return one valid JSON object. No Markdown.', ['willow bench', 'station']),
])
def test_content_view_removes_only_bound_presentation_spans(text, retained):
    from core import raw_output_contract as roc
    authority = getattr(roc, 'request_content_for_retrieval', None)
    assert callable(authority), 'The requested-content authority is absent'
    view = authority(text)
    assert len(view) == len(text), 'Analysis spans must remain in original request coordinates'
    assert all(part in view for part in retained), view
    assert not re.search(r'\b(?:valid|json|key|sole|string|prose|markdown|include)\b', view, re.I), view
