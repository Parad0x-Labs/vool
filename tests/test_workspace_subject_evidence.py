"""Subject questions collect local evidence through the agent turn entry."""
import json

import pytest

from apps.vool_agent import VoolAgent
from core import runtime_paths


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def enter_turn(text, workspace, *, events=None):
    agent = VoolAgent(backend_name="test-backend", device="subject-evidence")
    context = {"surface": "api", "_owner_local": True, "workspace": str(workspace),
               "workspace_binding": "project", "project_id": "subject-project",
               "runtime_session_id": "openclaw:4234567890abcdef1234",
               "requested_model": "unavailable:subject-evidence"}
    from core.runtime_task_events import (
        new_runtime_event_stream_id,
        register_runtime_event_sink,
        unregister_runtime_event_sink,
    )

    stream = new_runtime_event_stream_id() if events is not None else ""
    if stream:
        context["runtime_event_stream_id"] = stream
        register_runtime_event_sink(stream, events.append)
    try:
        result = agent.run_once(
            text, session_id_override="openclaw:4234567890abcdef1234", source_context=context,
        )
    finally:
        if stream:
            unregister_runtime_event_sink(stream)
    return result, context


@pytest.mark.parametrize("subject,extension,body", [
    ("kiln", "toml", '[firing]\npeak_celsius = 913\n'),
    ("rotor", "txt", '17:04 bearing seized at 740 rpm\n'),
])
def test_a_subject_without_a_filename_reaches_local_bytes(tmp_path, subject, extension, body):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / f"{subject}_records.{extension}").write_text(body)
    result, context = enter_turn(f"What does the {subject} record say?", workspace)
    observations = context.get("runtime_tool_observations", [])
    assert any(all(line in item.get("response_preview", "") for line in body.splitlines())
               for item in observations), observations
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    from core.prompt_normalizer import _runtime_tool_observation_message

    message = _runtime_tool_observation_message(context)
    assert message is not None
    model_rows = [json.loads(line[2:]) for line in message.content.splitlines() if line.startswith("- {")]
    assert any(all(line in row.get("response_preview", "") for line in body.splitlines())
               for row in model_rows), model_rows


def test_a_really_missing_file_keeps_the_missing_result(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "kiln_records.toml").write_text('[firing]\npeak_celsius = 913\n')
    result, _ = enter_turn("Read missing-notes.txt", workspace)
    response = str((result or {}).get("response", ""))
    assert "no file" in response.lower(), result
    assert "913" not in response


def test_an_outside_symlink_is_not_read(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-only-value")
    (workspace / "rotor_records.txt").symlink_to(outside)
    _, context = enter_turn("What does the rotor record say?", workspace)
    assert "outside-only-value" not in str(context.get("runtime_tool_observations"))


def test_unmatched_filenames_do_not_authorize_automatic_content_reads(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "asset-a.txt").write_text("Kiln peak temperature: 913 Celsius.")
    (workspace / "asset-b.txt").write_text("Rotor bearing seized at 740 rpm.")
    result, context = enter_turn("What is the kiln peak temperature?", workspace)
    assert result.get("model_execution", {}).get("source") in {
        "selected_model_blocked", "model_unavailable", "no_provider_available"
    }, result
    observations = context.get("runtime_tool_observations", [])
    assert not any(o.get("intent") == "workspace.read_file" for o in observations), context
    assert "913 Celsius" not in str(observations), context


def test_automatic_discovery_refuses_an_oversized_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "kiln_records.txt").write_text("unbounded-value " * 22000)
    _, context = enter_turn("What does the kiln record say?", workspace)
    reads = [o for o in context.get("runtime_tool_observations", [])
             if o.get("intent") == "workspace.read_file"]
    assert reads and reads[0]["status"] == "too_large", reads
    assert "unbounded-value" not in str(reads)


def test_a_matching_subject_does_not_read_unrelated_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for i in range(12):
        (workspace / f"asset-{i:02d}.txt").write_text(f"Unrelated fixture value: {i}.")
    (workspace / "kiln_records.txt").write_text("Kiln peak temperature: 913 Celsius.")
    _, context = enter_turn("What does the kiln record say?", workspace)
    paths = {o.get("path") for o in context.get("runtime_tool_observations", [])
             if o.get("intent") == "workspace.read_file"}
    assert paths == {"kiln_records.txt"}, paths


def test_automatic_read_activity_uses_the_same_bounded_evidence(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "kiln_records.txt").write_text("bounded receipt example " * 7000)
    events = []
    enter_turn("What does the kiln record say?", workspace, events=events)
    observations = []
    for event in events:
        details = event.get("details", event)
        observation = details.get("observation") if isinstance(details, dict) else None
        if isinstance(observation, dict) and observation.get("intent") == "workspace.read_file":
            observations.append(observation)
    assert observations, events
    from core.agent_runtime.workspace_question_evidence import MAX_FILE_CHARS

    for observation in observations:
        encoded_chars = len(json.dumps(observation, ensure_ascii=False))
        assert encoded_chars <= MAX_FILE_CHARS + 2048, encoded_chars
        assert observation.get("context_truncated") is True
        assert "lines" not in observation
