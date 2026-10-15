from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _counts(payload, capsys):
    workflow = ROOT / '.github/workflows/security-audit.yml'
    document = yaml.safe_load(workflow.read_text())
    step = next(row for row in document['jobs']['audit']['steps'] if 'pip_audit' in row.get('run', ''))
    script = step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]

    def recorded_json(path, **kwargs):
        assert str(path).endswith('.json')
        return io.StringIO(json.dumps(payload))

    exec(compile(script, str(workflow), 'exec'), {'open': recorded_json})
    return capsys.readouterr().out


def test_workflow_counts_the_recorded_pip_audit_fix_versions(capsys):
    # Vulnerability records from pip-audit's documented Flask JSON example:
    # https://github.com/pypa/pip-audit#examples
    payload = json.loads((ROOT / 'tests/fixtures/pip_audit_fix_versions.json').read_text())
    output = _counts(payload, capsys)
    assert output.count('vulnerabilities=2 fixable=2') == 2


@pytest.mark.parametrize('vulns,expected', [
    ([{'id': 'no-remediation', 'fix_versions': []}], 'vulnerabilities=1 fixable=0'),
    ([{'id': 'missing-remediation-field'}], 'vulnerabilities=1 fixable=0'),
    ([], 'vulnerabilities=0 fixable=0'),
])
def test_unfixed_or_missing_findings_do_not_become_fixable(capsys, vulns, expected):
    output = _counts({'dependencies': [{'name': 'example', 'version': '1', 'vulns': vulns}]}, capsys)
    assert output.count(expected) == 2
