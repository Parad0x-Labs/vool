"""Separate production continuity controls; frozen 13-case corpus is untouched.

Real finalized-image writer and persisted grant/revoke owners exit before the
native API runtime reader starts. The reader transport is the already-frozen
deterministic offline fixture, not live model inference.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OWNER = Path(__file__).with_name("_memory_continuity_owner_driver.py")
READER = Path(__file__).with_name("_memory_evidence_contract_driver.py")
MAIN, FOREIGN = "allotment-log", "neighbor-notes"

BRIEF = "Generate an image of Captain Mica, a fictional pilot wearing a brass coat and cyan scarf. Keep a crescent badge on the left shoulder."
IMAGE_INPUT = {
    "main_chat": MAIN,
    "foreign_chat": FOREIGN,
    "turns": [
        {"user": BRIEF, "assistant": "The image brief is complete.", "stated": "2025-08-02"},
        {"user": "My ceramic glaze sample tray contains 31 test tiles.", "assistant": "Noted: 31 test tiles in the ceramic glaze sample tray.", "stated": "2025-08-03"},
        {"user": "The studio delivery rota is posted beside the loading door.", "assistant": "The studio delivery rota is beside the loading door.", "stated": "2025-08-04"},
    ],
}
GRANT_INPUT = {
    "main_chat": MAIN,
    "foreign_chat": FOREIGN,
    "turns": [
        {"chat": MAIN, "user": "My ceramic glaze sample tray contains 31 test tiles.", "assistant": ""},
        {"chat": FOREIGN, "user": "The Moonstone mural delivery label is Cobalt Orchid.", "assistant": ""},
    ],
}


def _locations(tmp_path, name):
    artifacts = Path(os.environ.get("VOOL_ACCEPTANCE_ARTIFACT_DIR", str(tmp_path))) / name
    profiles = Path(os.environ.get("VOOL_ACCEPTANCE_PROFILE_DIR", str(tmp_path))) / f"{name}-{uuid.uuid4().hex[:10]}"
    artifacts.mkdir(parents=True, exist_ok=True)
    profiles.mkdir(parents=True, exist_ok=True)
    return artifacts, profiles


def _invoke(script, home, out, arguments):
    repo = Path(os.environ.get("VOOL_ACCEPTANCE_REPO", str(REPO))).resolve()
    command = [sys.executable, str(script), "--repo-root", str(repo), "--home", str(home), "--out", str(out), *arguments]
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=180)
    out.with_suffix(".stdout.txt").write_text(result.stdout)
    out.with_suffix(".stderr.txt").write_text(result.stderr)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-4000:]
    return json.loads(out.read_text())


def _owner(home, artifact, payload, operation):
    source = artifact.with_suffix(".input.json")
    source.write_text(json.dumps(payload))
    return _invoke(OWNER, home, artifact, ["--input", str(source), "--operation", operation])


def _read(home, artifact, turn):
    turns = artifact.with_suffix(".turns.json")
    turns.write_text(json.dumps({"turns": [turn]}))
    doc = _invoke(READER, home, artifact, ["--turns-json", str(turns), "--skip-seed"])
    assert doc["network_blocked"] is True
    assert doc["embedding_backend"] in {"hash-bow:384", "not-observed"}
    record = doc["turns"][0]
    wire = "\n".join(str(message.get("content") or "") for call in record["request_calls"] if call.get("payload") for message in call["payload"].get("messages", []))
    capsule = "\n".join(str(message.get("content") or "") for call in record["request_calls"] if call.get("payload") for message in call["payload"].get("messages", []) if "<retrieved_context>" in str(message.get("content") or ""))
    return doc, record, wire, capsule


def test_creative_brief_retained_and_recalled_without_profile_adoption(tmp_path):
    artifacts, home = _locations(tmp_path, "creative-brief-native")
    written = _owner(home, artifacts / "writer.json", IMAGE_INPUT, "image-finalization")
    # Always capture the native reader, even if the retention seam fails.
    _, turn, wire, capsule = _read(home, artifacts / "reader.json", {
        "id": "creative-brief",
        "question": "What colors and badge did I ask for in Captain Mica's image brief?",
        "scripted": "You asked for Captain Mica to wear a brass coat and cyan scarf, with a crescent badge on the left shoulder.",
        "reader_require": ["Captain Mica", "brass coat", "cyan scarf", "crescent badge", "left shoulder"],
    })
    assert BRIEF in written["conversation_log"], "Original creative source must remain in its log carrier"
    assert all("Captain Mica" not in text for text in written["profile_facts"].values()), written["profile_facts"]
    assert not any(row.get("source_body") == BRIEF or "Captain Mica" in str(row.get("content") or "") for row in written["assertion_nodes"]), {"image_brief_became_user_assertion_node": written["assertion_nodes"]}
    assert any(row["role"] == "user" and row["chat_scope"] == MAIN and row["status"] == "active" and row["body"] == BRIEF for row in written["retained_sources"]), {"missing_semantic_source_retention": True, "written": written, "reader": turn}
    for fragment in ["Captain Mica", "brass coat", "cyan scarf", "crescent badge", "left shoulder"]:
        assert fragment in wire, {"missing_wire_fragment": fragment, "turn": turn}
        assert fragment in capsule, {"missing_semantic_capsule_fragment": fragment, "capsule": capsule}
        assert fragment in turn["delivered"], turn


def test_explicit_grant_revocation_survives_restart_without_deleting_sources(tmp_path):
    artifacts, home = _locations(tmp_path, "grant-revocation-native")
    granted = _owner(home, artifacts / "writer.json", GRANT_INPUT, "grant-seed")
    assert "chat:" + FOREIGN in granted["active_grants"], granted
    # Positive serving finalizes an assistant copy into the receiving chat.
    # Use a pre-answer branch for that diagnostic so this test measures import
    # revocation, not unspecified erasure of already disclosed target history.
    positive_home = home.parent / (home.name + "-positive-control")
    shutil.copytree(home, positive_home)
    _, positive, wire, _ = _read(positive_home, artifacts / "granted-reader.json", {
        "id": "grant-positive",
        "question": "Remind me: what label did I choose for the Moonstone mural delivery?",
        "scripted": "The Moonstone mural delivery label is Cobalt Orchid.",
    })
    assert "Cobalt Orchid" in wire and "Cobalt Orchid" in positive["delivered"], positive
    revoked = _owner(home, artifacts / "revoker.json", GRANT_INPUT, "revoke")
    assert revoked["revoked"] is True and "chat:" + FOREIGN not in revoked["active_grants"], revoked
    assert any(row["chat_scope"] == FOREIGN and row["status"] == "active" and "Cobalt Orchid" in row["body"] for row in revoked["retained_sources"]), "Revocation must not silently delete the source owner's history"
    _, denied, wire, _ = _read(home, artifacts / "revoked-reader.json", {
        "id": "grant-revoked",
        "question": "Remind me: what label did I choose for the Moonstone mural delivery?",
        "scripted": "I could not find that label in the history available to this chat.",
    })
    assert "Cobalt Orchid" not in wire and "Cobalt Orchid" not in denied["delivered"], denied
    _, sibling, wire, _ = _read(home, artifacts / "sibling-reader.json", {
        "id": "own-sibling",
        "question": "What did I tell you about my ceramic glaze sample tray?",
        "scripted": "Your ceramic glaze sample tray contains 31 test tiles.",
        "reader_require": ["31 test tiles"],
    })
    assert "31 test tiles" in wire and "31 test tiles" in sibling["delivered"], sibling
