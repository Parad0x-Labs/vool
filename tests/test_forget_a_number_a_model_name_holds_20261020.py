"""A forget of a number reads no model name or signature as the person's words (2026-10-20).

Measured on a served daemon at 11d4b9b and on v0.7.2 (29945fd); v0.7.1 (baf115c) applied it: with the model named
"stub-private:12b", "Remember that my locker is 12." then "Forget 12." answered "Forget could not be verified as
fully applied: unclassified provider invocation manifests, ..." every time, and the memory stayed. Verification
read every chat-scoped table nobody classified, and the provider call receipts there hold the model name "...:12b"
and a random base64 signature: any number matched a signature about one time in ten, which is what made
tests/test_remember_again_after_forget_served.py ("Forget 20." on the 20th) flaky.
"""
from __future__ import annotations

import json
from pathlib import Path

import tests.test_private_chat_served_workflow as served_workflow
from core.memory.carriers import carries
from core.memory.run_history import _unclassified_payload
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED
from tests.test_private_chat_served_workflow import _Provider, _stream

REPO = Path(__file__).resolve().parents[1]
MODEL = "stub-private:12b"


def _receipt(**fields) -> dict:
    """A provider call receipt row as provider_invocation_gateway stores it."""
    manifest = {"model_id": "gemma3:12b", "provider_id": "ollama-local:gemma3:12b", "operation": "chat",
                "signature": "qEJvV/aqYj20tso6mMUu+wiiKkUavwU4XiuZ3kmwHKgf+426c/ElleXbpzKuRdyqWCIPQS5FdmPsfTKGmqzpeAw==",
                "provider_messages": [{"role": "user", "content_hash": "2b510ff279c5f149" * 4}]}
    row = {"manifest_id": "provider-manifest-0c4ce4c766504b88a8e055adc97355ea", "chat_id": "openclaw:" + "e5" * 10,
           "model_id": manifest["model_id"], "provider_id": manifest["provider_id"], "operation": "chat",
           "signature": manifest["signature"], "manifest_json": json.dumps(manifest),
           "created_at": "2026-10-20T22:52:28.444885+00:00"}
    row.update(fields)
    return row


def test_a_model_name_and_a_signature_are_not_words_a_forget_waits_on():
    row = _receipt()

    assert not carries(_unclassified_payload(row), "12")
    assert not carries(_unclassified_payload(row), "20")
    assert not carries(_unclassified_payload(row), "5")


def test_the_persons_words_in_a_table_nobody_classified_still_hold_a_forget_open():
    # Only the runtime's own names and signatures are left out: words under any other field still count.
    row = _receipt(note="my locker is 12", manifest_json=json.dumps({"model_id": "gemma3:12b", "reason": "locker 12"}))

    assert carries(_unclassified_payload(row), "12")
    assert carries(_unclassified_payload({"model_id": "gemma3:12b", "summary": "locker 12"}), "12")


def test_served_forget_of_the_number_in_the_models_name_is_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(served_workflow, "MODEL", MODEL)
    home = tmp_path / "home"
    provider = _Provider({MODEL: "Got it."})
    with provider:
        run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(MODEL, {})]))
        with ServedDaemon(home, env_extra={"VOOL_MODEL_LOAD_FLOOR_GB": "0"}) as daemon:
            assert served_workflow._certify(daemon).get("state") == "verified"
            chat = "openclaw:" + "e5" * 10

            assert "Locked in" in _stream(daemon, "Remember that my locker is 12.", chat)[0]
            forgot, _ = _stream(daemon, "Forget 12.", chat)
            assert forgot == "Forget applied. Removed 1 memory entry.", forgot

            # The chat is not left half-forgotten: the next fact is kept and the next forget applies.
            assert "Locked in" in _stream(daemon, "Remember that my gate code is 61.", chat)[0]
            again, _ = _stream(daemon, "Forget 61.", chat)
            assert again == "Forget applied. Removed 1 memory entry.", again
