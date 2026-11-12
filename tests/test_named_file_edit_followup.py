"""Edits retain their constraints and read the actual file before generation."""
import pytest

from core.agent_runtime.demand_ownership import execution_unit_spans
from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request
from core.persistent_memory import maybe_handle_memory_command

EDIT = 'Add a “Clear completed” button. Keep the existing tasks and behavior intact. Change only index.html.'


@pytest.mark.parametrize('prompt', [EDIT, 'Update parser.py to accept empty input. Preserve its public API.',
    'Please modify settings.js to add a reset control. Keep its current defaults unchanged.'])
def test_edit_is_one_builder_request(prompt):
    assert looks_like_agentic_build_request(prompt)
    units = execution_unit_spans(prompt)
    assert len(units) == 1
    assert 'Keep' not in prompt or 'Keep' in units[0].text


@pytest.mark.parametrize('prompt', ['Should we change parser.py?', 'Explain how to modify settings.js.',
    'Read index.html.', 'Add a button in index.html, but do not edit any files.',
    "Add a footer to index.html, but don't change any files yet."])
def test_discussion_and_read_only_requests_do_not_become_edits(prompt):
    from core.agent_runtime.builder.named_file_build import looks_like_named_file_build_request
    assert not looks_like_agentic_build_request(prompt)
    # Nor may the named-file creation route write it: an opt-out governs both ways in.
    assert not looks_like_named_file_build_request(prompt)


def test_preserving_current_state_is_not_a_memory_write():
    assert maybe_handle_memory_command('Keep the existing tasks and behavior intact. Change only index.html.',
                                       session_id='edit-preservation') == (False, '')


def test_preservation_does_not_swallow_a_separate_action_or_explicit_memory():
    from core.agent_runtime.build_request_intent import is_preservation_constraint
    assert not is_preservation_constraint('Keep the layout intact and delete old.txt.')
    assert not is_preservation_constraint('Keep in mind my preference is unchanged.')
    assert maybe_handle_memory_command('Remember that my preferred editor is Vim.',
                                       session_id='edit-memory-control')[0]


@pytest.mark.parametrize('truncated', [False, True])
def test_existing_file_is_read_before_generation_and_incomplete_read_never_writes(tmp_path, truncated):
    from core.agent_runtime.builder import app_builder as ab
    from core.agent_runtime.builder import mutation_scope
    original = '<html>existing tasks and storage key</html>\n'
    (tmp_path/'index.html').write_text(original)
    calls = []
    def run(intent, arguments, **kwargs):
        calls.append(intent)
        if intent == 'workspace.read_file':
            return ab.ToolOutcome(ok=True, response_text=original,
                details={'truncated':truncated,'decode_lossy':False,'lines':[{'text':original}], 'verbatim':True})
        assert intent == 'workspace.write_file'
        (tmp_path/arguments['path']).write_text(arguments['content'])
        return ab.ToolOutcome(ok=True, response_text='written')
    def generate(prompt):
        assert calls == ['workspace.read_file']
        assert original in prompt
        assert EDIT in prompt
        return original+'<!-- clear button -->'
    report = ab.build_app_from_spec(request=EDIT,target_rel='',source_context={'workspace':str(tmp_path)},
        generate_fn=generate,run_tool_fn=run,scope=mutation_scope.resolve_mutation_scope(EDIT,workspace_root=str(tmp_path)))
    if truncated:
        assert calls == ['workspace.read_file']
        assert (tmp_path/'index.html').read_text() == original
        assert not report.files_written and report.error
    else:
        assert calls == ['workspace.read_file','workspace.write_file']
        assert 'clear button' in (tmp_path/'index.html').read_text()


def _edit_report(tmp_path, request, *, read, generate):
    from core.agent_runtime.builder import app_builder as ab
    from core.agent_runtime.builder import mutation_scope
    calls = []
    def run(intent, arguments, **kwargs):
        calls.append(intent)
        if intent == 'workspace.read_file':
            return read(arguments)
        assert intent == 'workspace.write_file'
        (tmp_path/arguments['path']).write_text(arguments['content'])
        return ab.ToolOutcome(ok=True, response_text='written')
    report = ab.build_app_from_spec(request=request, target_rel='', source_context={'workspace':str(tmp_path)},
        generate_fn=generate, run_tool_fn=run,
        scope=mutation_scope.resolve_mutation_scope(request, workspace_root=str(tmp_path)))
    return report, calls


def test_an_edit_of_a_file_that_is_not_there_yet_still_creates_it(tmp_path):
    """"Add test_parser.py ..." leads with an edit verb but names a new file: nothing to read, so
    the request alone is the brief, exactly as before edits were recognised."""
    from core.agent_runtime.builder import app_builder as ab
    request = 'Add test_parser.py that checks parse returns an empty list for empty input.'
    prompts = []
    def generate(prompt):
        prompts.append(prompt)
        return 'def test_empty():\n    assert True\n'
    report, calls = _edit_report(tmp_path, request, generate=generate,
        read=lambda arguments: ab.ToolOutcome(ok=False, status='not_found', response_text='There is no file at `test_parser.py`.'))
    assert calls[:2] == ['workspace.read_file', 'workspace.write_file'], calls
    assert prompts and '--- current' not in prompts[0]
    assert (tmp_path/'test_parser.py').exists() and report.files_written


def test_an_edit_too_large_for_the_local_window_is_refused_before_generation(tmp_path):
    """A model server that cuts an oversized prompt would have the model rewrite a file it only
    partly saw. The local lane declares its window; a file that cannot go in and come back whole
    inside it is not sent."""
    from core.agent_runtime.builder import app_builder as ab
    original = '<html>\n' + '<li>stored task</li>\n' * 1500 + '</html>\n'
    (tmp_path/'index.html').write_text(original)
    rows = [{'text': line} for line in original.splitlines()]
    def generate(prompt):
        raise AssertionError('an edit that cannot fit the window must not be generated')
    generate.context_tokens = 8192
    report, calls = _edit_report(tmp_path, EDIT, generate=generate,
        read=lambda arguments: ab.ToolOutcome(ok=True, response_text=original,
            details={'truncated': False, 'decode_lossy': False, 'lines': rows, 'verbatim': True}))
    assert calls == ['workspace.read_file']
    assert not report.handled and 'too large' in report.error
    assert (tmp_path/'index.html').read_text() == original
    # The same file fits a window that can hold it going in and coming back.
    edited = []
    def roomy(prompt):
        edited.append(prompt)
        return original.replace('</html>', '<button>Clear completed</button></html>')
    roomy.context_tokens = 65536
    report, calls = _edit_report(tmp_path, EDIT, generate=roomy,
        read=lambda arguments: ab.ToolOutcome(ok=True, response_text=original,
            details={'truncated': False, 'decode_lossy': False, 'lines': rows, 'verbatim': True}))
    assert calls == ['workspace.read_file', 'workspace.write_file'] and edited
    assert 'Clear completed' in (tmp_path/'index.html').read_text()
