"""A workspace location selects where to act; it does not itself prompt a search."""
import pytest
from core.execution.planner import _code_search_query, plan_tool_workflow
from core.agent_runtime.builder.named_file_build import looks_like_named_file_build_request

DEMO_REQUEST = '''In this project, create a single-file HTML task board called index.html.

Include:
- TODO, DOING and DONE columns
- Add and delete tasks
- Buttons to move tasks between columns
- Live task counts
- localStorage persistence
- A clean dark interface with teal accents

Use vanilla HTML, CSS and JavaScript. No external libraries or network requests.

Create the file, then briefly report what you checked.'''

@pytest.mark.parametrize('prompt', [
    DEMO_REQUEST,
    'In this repo, write counter.py with a count_items function.',
    'Implement a settings panel using sessionStorage in this project.',
    'Explain how cache_key is computed in this codebase; use only the code pasted below.',
    'In this workspace, create a file called "welcome.txt" containing hello.',
])
def test_location_does_not_invent_a_search(prompt):
    assert _code_search_query(' '.join(prompt.split())) == ''
    plan = plan_tool_workflow(user_text=prompt, task_class='unknown', executed_steps=[],
                              source_context={'workspace':'/tmp/demo-workspace'})
    assert (plan.next_payload or {}).get('intent') != 'workspace.search_text'


def test_the_demo_keeps_its_named_file_build_contract():
    assert looks_like_named_file_build_request(DEMO_REQUEST, workspace_root='/tmp/demo-workspace')


@pytest.mark.parametrize('prompt, needle', [
    ('Find all references to localStorage in this project.', 'localStorage'),
    ('Hunt down cache_key in this repo.', 'cache_key'),
    ('Search this workspace for "welcome"', 'welcome'),
])
def test_explicit_searches_still_search(prompt, needle):
    plan = plan_tool_workflow(user_text=prompt, task_class='unknown', executed_steps=[],
                              source_context={'workspace':'/tmp/demo-workspace'})
    assert plan.next_payload['intent'] == 'workspace.search_text'
    assert plan.next_payload['arguments']['query'] == needle


# A lookup that names the repository is still a search. The first port of the creation repair above
# dropped every one of these: none carries a search verb, and the location was what admitted them.
@pytest.mark.parametrize('prompt, needle', [
    ('Which file in this workspace defines renderBoard?', 'renderBoard'),
    ('Find parse_config in this project.', 'parse_config'),
    ('What calls renderBoard in this codebase?', 'renderBoard'),
    ('Locate the definition of toKebabCase in this workspace.', 'toKebabCase'),
    ('Is renderBoard used anywhere in this project?', 'renderBoard'),
    ('How many places in this codebase call fetchUser?', 'fetchUser'),
])
def test_a_lookup_in_the_repository_still_searches(prompt, needle):
    plan = plan_tool_workflow(user_text=prompt, task_class='unknown', executed_steps=[],
                              source_context={'workspace':'/tmp/demo-workspace'})
    assert (plan.next_payload or {}).get('intent') == 'workspace.search_text', prompt
    assert plan.next_payload['arguments']['query'] == needle


# A lookup word inside an instruction to make or change files does not turn it into a search.
@pytest.mark.parametrize('prompt', [
    'Create a helper in this repo that finds where parse_config is defined.',
    'In this project, build a page that lets me find tasks by tag and saves them with localStorage.',
    'In this project, fix where renderBoard is called in app.js.',
])
def test_a_lookup_word_inside_an_instruction_is_not_a_search(prompt):
    assert _code_search_query(' '.join(prompt.split())) == ''
    plan = plan_tool_workflow(user_text=prompt, task_class='unknown', executed_steps=[],
                              source_context={'workspace':'/tmp/demo-workspace'})
    assert (plan.next_payload or {}).get('intent') != 'workspace.search_text'
