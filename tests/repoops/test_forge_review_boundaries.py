"""Malformed or out-of-scope reads cannot become successful forge evidence."""
import pytest

from core.kas.contract import ForgeRefusedError
from tests.repoops.test_forge_listing_truth import _ScriptedWire, _github_adapter, _gitlab_adapter, _run


@pytest.mark.parametrize('factory', [_github_adapter, _gitlab_adapter])
@pytest.mark.parametrize('payload', [{}, [], b'not JSON', {'sha': None, 'id': None}])
def test_success_without_ref_identity_is_unknown_not_absent(factory, payload):
    with pytest.raises(ForgeRefusedError):
        factory(_ScriptedWire((200, payload, {}))).resolve_ref('feature/review')


@pytest.mark.parametrize('factory', [_github_adapter, _gitlab_adapter])
@pytest.mark.parametrize('payload', [{}, b'bad JSON', [None], {'workflow_runs': [None]}])
def test_malformed_listing_is_not_complete_empty_success(factory, payload):
    with pytest.raises(ForgeRefusedError):
        factory(_ScriptedWire((200, payload, {}))).ci_jobs('abc')


@pytest.mark.parametrize('next_url', [
    'https://other.invalid/repos/o/r/actions/runs?head_sha=abc&per_page=100&page=2',
    'https://api.github.com/repos/other/repo/actions/runs?head_sha=abc&per_page=100&page=2',
    'https://api.github.com/repos/o/r/actions/runs?head_sha=wrong&per_page=100&page=2',
    'https://api.github.com/repos/o/r/actions/runs?per_page=100&page=2',
])
def test_pagination_cannot_change_repository_or_commit_scope(next_url):
    wire = _ScriptedWire((200, {'workflow_runs': []}, {'Link': f'<{next_url}>; rel="next"'}),
                         (200, {'workflow_runs': [_run(2, 'wrong', 'success', 'wrong')]}, {}))
    with pytest.raises(ForgeRefusedError):
        _github_adapter(wire).ci_jobs('abc')
    assert len(wire.requests) == 1


def test_relative_pagination_preserves_scope_and_resolves_against_current_url():
    wire = _ScriptedWire((200, {'workflow_runs': []}, {'Link': '<?head_sha=abc&per_page=100&page=2>; rel="next"'}),
                         (200, {'workflow_runs': [_run(2, 'required', 'failure', 'abc')]}, {}))
    result = _github_adapter(wire).ci_jobs('abc')
    assert len(result.rows) == 1 and not result.truncated
    assert wire.requests[1].url == 'https://api.github.com/repos/o/r/actions/runs?head_sha=abc&per_page=100&page=2'


@pytest.mark.parametrize('factory', [_github_adapter, _gitlab_adapter])
def test_nested_ci_reads_share_one_total_request_bound(factory):
    if factory is _github_adapter:
        first = {'workflow_runs': [_run(i, str(i), 'success', 'abc') for i in range(20)]}
        subsequent = {'artifacts': []}
    else:
        first = [{'id': i + 1, 'sha': 'abc'} for i in range(20)]
        subsequent = []
    wire = _ScriptedWire((200, first, {}), *[(200, subsequent, {}) for _ in range(20)])
    result = factory(wire).ci_artifacts('abc') if factory is _github_adapter else factory(wire).ci_jobs('abc')
    assert len(wire.requests) <= 10
    assert result.pages_read == len(wire.requests)
    assert result.truncated


@pytest.mark.parametrize('factory', [_github_adapter, _gitlab_adapter])
@pytest.mark.parametrize('wrong_sha', ['', 'previous-head'])
def test_ci_rows_must_attest_the_requested_revision(factory, wrong_sha):
    if factory is _github_adapter:
        wire = _ScriptedWire((200, {'workflow_runs': [_run(1, 'unit', 'success', wrong_sha)]}, {}))
    else:
        wire = _ScriptedWire((200, [{'id': 1, 'sha': 'abc'}], {}),
                             (200, [{'id': 2, 'name': 'unit', 'status': 'success', 'commit': {'id': wrong_sha}}], {}))
    with pytest.raises(ForgeRefusedError):
        factory(wire).ci_jobs('abc')


def test_empty_ci_row_is_not_a_successful_job():
    with pytest.raises(ForgeRefusedError):
        _github_adapter(_ScriptedWire((200, {'workflow_runs': [{}]}, {}))).ci_jobs('abc')
