"""Project evidence discovery never auto-reads secret files or ships secret-shaped content.

Drives the real agent turn entry (VoolAgent.run_once) in a project-bound workspace, exactly as the
subject-evidence feature is reached in production. Two laws are proven:

- a file the repo's own model-handoff rules would keep from a model (secret-named, excluded pattern,
  dotfile, ssh material), or whose name merely shares one general-vocabulary word with the question,
  is never auto-read as evidence;
- a file that IS read but whose content looks secret-shaped (mnemonic word runs, private-key blocks,
  token formats) has its content withheld: the read is audited, the bytes never reach the
  model-bound observation message.
"""
from __future__ import annotations

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


def enter_turn(text, workspace):
    agent = VoolAgent(backend_name="test-backend", device="aq076-privacy")
    context = {"surface": "api", "_owner_local": True, "workspace": str(workspace),
               "workspace_binding": "project", "project_id": "aq076-privacy-project",
               "runtime_session_id": "openclaw:4234567890abcdef7701",
               "requested_model": "unavailable:aq076-privacy"}
    result = agent.run_once(
        text, session_id_override="openclaw:4234567890abcdef7701", source_context=context,
    )
    return result, context


def _model_text(context) -> str:
    from core.prompt_normalizer import _runtime_tool_observation_message

    message = _runtime_tool_observation_message(context)
    return message.content if message is not None else ""


def _read_paths(context) -> set[str]:
    return {
        str(obs.get("path") or "")
        for obs in (context.get("runtime_tool_observations") or [])
        if obs.get("intent") == "workspace.read_file"
    }


def test_a_general_question_never_reads_secret_named_files(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "credentials.json").write_text('{"issued": "sk-ant-api03-probe-0000000000000000"}\n')
    (workspace / "api_notes.md").write_text("The notes talk about endpoints in general.\n")
    result, context = enter_turn("How are API credentials issued in general?", workspace)
    reads = _read_paths(context)
    assert not any("credentials" in path for path in reads), reads
    blob = json.dumps(context.get("runtime_tool_observations") or [], default=str) + "\n" + _model_text(context) \
        + "\n" + json.dumps(result, default=str)
    assert "sk-ant-api03-probe" not in blob


def test_camelcase_and_variant_spellings_are_still_refused(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "serverAccessTokens.json").write_text("token=ghp_ProbeProbeProbeProbeProbeProbeProbe\n")
    (workspace / "Recovery_Words.txt").write_text("media estate cradle foil velvet ribbon harbor pond drama citizen aisle pine kitten\n")
    result, context = enter_turn("How does a recovery phrase work in general?", workspace)
    reads = _read_paths(context)
    assert not any("serverAccessTokens" in path for path in reads), reads
    assert not any("Recovery" in path for path in reads), reads
    blob = json.dumps(context.get("runtime_tool_observations") or [], default=str) + "\n" + _model_text(context) \
        + "\n" + json.dumps(result, default=str)
    assert "ghp_ProbeProbe" not in blob and "velvet ribbon" not in blob


def test_dotfiles_and_ssh_material_are_never_autoread(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env.local").write_text("STAGING_TOKEN=tok_stg_77BFprobe22\n")
    (workspace / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1wcm9iZQ\n-----END OPENSSH PRIVATE KEY-----\n")
    result, context = enter_turn("What do the env settings say about staging thresholds?", workspace)
    reads = _read_paths(context)
    assert not any(".env" in path for path in reads), reads
    assert not any("id_ed25519" in path for path in reads), reads
    blob = json.dumps(context.get("runtime_tool_observations") or [], default=str) + "\n" + _model_text(context) \
        + "\n" + json.dumps(result, default=str)
    assert "tok_stg_77BFprobe22" not in blob and "OPENSSH PRIVATE KEY" not in blob


def test_one_general_vocabulary_word_is_not_a_subject(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "market_share_2024.csv").write_text("quarter,share\nQ1,0.3175\n")
    result, context = enter_turn("What is the market capitalisation of large companies?", workspace)
    reads = _read_paths(context)
    assert not any("market_share" in path for path in reads), reads
    assert "0.3175" not in json.dumps(context.get("runtime_tool_observations") or [], default=str)
    assert "0.3175" not in _model_text(context)


def test_excluded_patterns_apply_even_when_the_question_names_the_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "vault_notes.key").write_text("cipher material probe ZONE-QUELL-9317\n")
    (workspace / "todo.txt").write_text("ship the audit\n")
    result, context = enter_turn("What do the vault notes say?", workspace)
    reads = _read_paths(context)
    assert not any("vault_notes.key" in path for path in reads), reads
    blob = json.dumps(context.get("runtime_tool_observations") or [], default=str) + "\n" + _model_text(context) \
        + "\n" + json.dumps(result, default=str)
    assert "ZONE-QUELL-9317" not in blob


def test_a_real_subject_still_reads_its_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "greenhouse_watering_plan.md").write_text("Drip lines open at dawn for twenty minutes.\n")
    (workspace / "delivery_manifest.csv").write_text("crate,weight\nC-17,88.2\n")
    result, context = enter_turn("What does the greenhouse watering plan say about drip lines?", workspace)
    reads = _read_paths(context)
    assert any("greenhouse_watering_plan" in path for path in reads), reads
    assert not any("delivery_manifest" in path for path in reads), reads
    assert "Drip lines open at dawn" in _model_text(context)


def test_secret_shaped_content_is_withheld_not_sent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "site_journal.txt").write_text(
        "Pump maintenance due in May.\n"
        "media estate cradle foil velvet ribbon harbor pond drama citizen aisle pine kitten\n"
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEF\n-----END PRIVATE KEY-----\n"
    )
    result, context = enter_turn("What does the site journal say about the pump?", workspace)
    reads = _read_paths(context)
    assert any("site_journal" in path for path in reads), reads
    blob = json.dumps(context.get("runtime_tool_observations") or [], default=str) + "\n" + _model_text(context) \
        + "\n" + json.dumps(result, default=str)
    assert "velvet ribbon" not in blob
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEF" not in blob
    withheld = [obs for obs in (context.get("runtime_tool_observations") or [])
                if obs.get("intent") == "workspace.read_file" and obs.get("content_withheld")]
    assert withheld and "withheld" in str(withheld[0].get("response_preview")).lower(), withheld
