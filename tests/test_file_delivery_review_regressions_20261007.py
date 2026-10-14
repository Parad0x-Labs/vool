"""Request ownership, independent outputs, and archive integrity regressions."""
import pytest


@pytest.mark.parametrize('text', ['Create a CSV file of the planets in the data folder.', 'Save a CSV of my expenses in the src directory.', 'Make a PDF report and put it in the reports folder of my project.', 'Create a markdown file in the current directory listing the steps.', "Export the table as a CSV into my project's exports folder.", 'Write a zip of the scripts to disk in the build folder.'])
def test_named_workspace_destination_is_not_download_only(text):
    from core.deliverables import downloadable_request_without_workspace_target, requested_formats
    from core.tool_demand_signals import asks_for_a_file
    got = downloadable_request_without_workspace_target(text)
    assert got is False
WRITERS = [('workspace.apply_unified_diff', {'patch': '--- /dev/null\n+++ b/stray.txt\n@@ -0,0 +1 @@\n+hello\n'}), ('workspace.replace_in_file', {'path': 'existing.txt', 'old': 'a', 'new': 'b'}), ('workspace.ensure_directory', {'path': 'stray_dir'}), ('machine.download_file', {'url': 'https://example.invalid/x.zip', 'destination': 'fixture-downloads/x.zip'}), ('machine.ensure_directory', {'path': 'fixture-desktop/stray_dir'}), ('machine.move_path', {'source': 'fixture-desktop/a.txt', 'destination': 'fixture-desktop/b.txt'})]

@pytest.mark.parametrize('intent,arguments', WRITERS, ids=[w[0] for w in WRITERS])
def test_other_writers_refused_under_download_only(intent, arguments, monkeypatch):
    from core import tool_intent_executor
    from core.deliverables import DOWNLOAD_ONLY_KEY
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    reached = []

    def dispatch(*a, **k):
        reached.append(a[0] if a else k.get('intent'))
        raise RuntimeError('dispatch reached (probe stop)')
    monkeypatch.setattr(tool_intent_executor, 'execute_authorized_runtime_tool', dispatch)
    try:
        ex = tool_intent_executor._execute_tool_intent_impl({'intent': intent, 'arguments': arguments}, task_id='download-only', session_id='download-only', source_context={DOWNLOAD_ONLY_KEY: True, 'operating_mode': 'auto'}, hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False)))
        status = ex.status
    except RuntimeError as e:
        status = f'raised:{e}'
    assert status == "delivery_tool_required"
    assert not reached, f'{intent} reached dispatch under download-only ownership (status {status})'

@pytest.mark.parametrize('text,must_include', [('Make a CSV of the planets in a zip and a separate PDF summary.', {'zip', 'pdf'}), ('Zip them up and also give me a PDF summary.', {'zip', 'pdf'}), ('Put the HTML and CSS in a zip, and send a CSV of the colors too.', {'zip', 'csv'}), ('Give me a CSV of the data and a ZIP of the images.', {'csv', 'zip'}), ('Write an HTML page and a CSS file, then zip them together.', {'zip'})])
def test_requested_formats_keep_every_separate_output(text, must_include):
    from core.deliverables import requested_formats
    got = set(requested_formats(text))
    assert must_include <= got

@pytest.mark.parametrize('text,expected_enum', [('Make a CSV of the planets in a zip and a separate PDF summary.', {'zip', 'pdf'}), ('I need a spreadsheet of monthly subscriptions. Excel please.', {'xlsx'}), ('Write an HTML page and a CSS file, then zip them together.', {'zip'})])
def test_offered_schema_enum_matches_request(text, expected_enum):
    from core.tool_intent_executor import runtime_tool_specs as specs_fn
    from core.tool_offer_assembly import _scope_download_specs
    specs = [s for s in specs_fn() if s.get('intent') == 'file.deliver']
    scoped = _scope_download_specs(specs, text)
    enum = None
    for s in scoped:
        if s.get('intent') == 'file.deliver':
            enum = (s.get('json_schema') or {}).get('properties', {}).get('format', {}).get('enum')
    assert enum is not None and expected_enum <= set(enum)

@pytest.mark.parametrize('text,is_order', [('Explain how to make a CSV and then make one for my expenses.', True), ('Tell me how a PDF works and generate a sample PDF for me.', True), ('Show me how to export a CSV, and export one of these rows.', True), ('Teach me to make a CSV file.', False), ("What's the best way to make a PDF accessible?", False), ('How do I create an Excel spreadsheet? Create a short checklist for me.', False), ('Can you explain how to make a zip file?', False)])
def test_instructional_vs_imperative(text, is_order):
    from core.tool_demand_signals import asks_for_a_file
    got = asks_for_a_file(text)
    assert got is is_order

@pytest.mark.parametrize('filename,fmt,files,expect', [('site.zip', 'zip', [{'filename': 'index.html', 'content': '<p>x</p>'}, {'filename': 'styles.css', 'content': 'p{}'}], 'ok'), ('site.zip', 'zip', [{'filename': 'index.html', 'content': '<p>x</p>', 'format': 'txt'}], 'refused'), ('site.zip', 'zip', [{'filename': 'INDEX.HTML', 'content': '<p>x</p>'}], 'ok'), ('site.zip', 'zip', [{'filename': 'notes', 'content': 'x', 'format': 'md'}], 'ok'), ('site.zip', 'tar.gz', [{'filename': 'a.txt', 'content': 'x'}], 'refused_or_renamed'), ('site.tar.gz', 'zip', [{'filename': 'a.txt', 'content': 'x'}], 'refused_or_renamed'), ('site.zip', 'zip', [{'filename': 'a\\..\\..\\evil.txt', 'content': 'x'}], 'refused_or_contained'), ('site.zip', 'zip', [{'filename': 'a.txt', 'content': 'x'}, {'filename': 'A.TXT', 'content': 'y'}], 'ok')])
def test_archive_member_and_outer_integrity(filename, fmt, files, expect):
    import io
    import tarfile
    import zipfile

    from core import deliverables
    try:
        built = deliverables.build_file('', filename=filename, fmt=fmt, title=filename, verify=True, files=files) if 'files' in deliverables.build_file.__code__.co_varnames else None
        if built is None:
            pytest.skip('build_file has no files parameter; bundle entry point differs')
        data = built.data if hasattr(built, 'data') else getattr(built, 'content', b'')
        names = []
        if built.filename.endswith('.zip'):
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        elif built.filename.endswith(('.tar.gz', '.tgz')):
            names = tarfile.open(fileobj=io.BytesIO(data)).getnames()
        outcome = {'filename': built.filename, 'members': names}
    except deliverables.DeliverableRefused as e:
        outcome = {'refused': str(getattr(e, 'code', '')) or e.args[0] if e.args else 'refused', 'message': str(e)[:200]}
    if expect == 'ok':
        assert 'refused' not in outcome, outcome
    elif expect == 'refused':
        assert 'refused' in outcome, outcome
    elif expect == 'refused_or_renamed':
        assert 'refused' in outcome or outcome['filename'].endswith('.tar.gz' if fmt == 'tar.gz' else '.zip'), outcome
        if 'refused' not in outcome:
            assert not outcome['filename'].endswith(('.zip.tar.gz', '.tar.gz.zip')), outcome
    elif expect == 'refused_or_contained':
        assert 'refused' in outcome or all('..' not in n and (not n.startswith('/')) for n in outcome['members']), outcome

@pytest.mark.parametrize('text,policy,forbid_commands', [('Write a script and zip it up for me. Do not run the scripts.', 'allowed', True), ("Make a zip of my scripts but don't execute them.", 'allowed', True), ('Give me the files as a zip. Do not create files.', 'forbidden', True), ("Don't write any files, just explain what a CSV is.", 'forbidden', True), ("Package the app as a zip; don't open the app.", 'allowed', False)])
def test_execution_authority(text, policy, forbid_commands):
    from core.agent_runtime.intent_claims import turn_action_constraints
    c = turn_action_constraints(text)
    assert c.policy.value == policy and c.forbid_commands is forbid_commands


def test_workbook_fixture_is_byte_stable_across_wall_clock_dates(monkeypatch):
    import zipfile

    from tests.reader_fixtures import xlsx_workbook
    monkeypatch.setattr(zipfile.time, "localtime", lambda *_: (2024, 1, 1, 0, 0, 0, 0, 0, 0))
    before = xlsx_workbook()
    monkeypatch.setattr(zipfile.time, "localtime", lambda *_: (2026, 10, 7, 5, 0, 4, 0, 0, 0))
    assert xlsx_workbook() == before


def test_host_fixture_has_the_complete_snapshot_contract(monkeypatch):
    from core.system_resources import ResourceSnapshot
    from tests.provider_fixture_resources_20261007 import pin_fixture_host_memory
    pin_fixture_host_memory(monkeypatch)
    from core import system_resources
    value = system_resources.snapshot()
    assert isinstance(value, ResourceSnapshot)
    assert value.to_dict()["usable_gb"] == 60.0


@pytest.mark.parametrize("user_text", [
    "Give me a PDF summary and put the HTML and CSS in a zip.",
    "Put the HTML and CSS in a zip and give me a PDF summary.",
])
def test_outputs_on_either_side_of_bundle_remain_independent(user_text):
    from core.deliverables import requested_formats
    assert set(requested_formats(user_text)) == {"pdf", "zip"}


def test_project_discussion_is_not_a_workspace_destination():
    from core.deliverables import downloadable_request_without_workspace_target
    assert downloadable_request_without_workspace_target("Make a PDF to explain the project.")


@pytest.mark.parametrize("batch", [False, True])
def test_real_tool_loop_keeps_running_until_both_requested_outputs_exist(batch):
    from tests.test_tool_loop_liveness import _call, _drive
    csv = _call("file.deliver", filename="data.csv", content="a,b\n1,2\n", format="csv")
    pdf = _call("file.deliver", filename="report.pdf", content="# Report\n\nA summary.", format="pdf")
    scripts = [[csv, pdf]] if batch else [[csv], [pdf]]
    result, router, _ = _drive(scripts, extra_context={"operating_mode": "auto", "runtime_session_id": "openclaw:0123456789abcdef0123"},
                              user_input="Give me a PDF report and a CSV file of the data.")
    assert result["details"]["tool_steps"] == ["file.deliver", "file.deliver"], (result, router.calls)
    assert router.calls == (1 if batch else 2)
    assert "data.csv" in result["response"] and "report.pdf" in result["response"]
    assert result["details"]["loop_stop_reason"] == "tool_completed_turn"
