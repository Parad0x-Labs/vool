"""A forget of a number reads no model or provider name of a call record as the person's words (2026-10-20).

Measured on a served daemon at 11d4b9b, on the 0.7.3 train (dd563ce7, with #207's signature rule) and on v0.7.2
(29945fd); v0.7.1 (baf115c) applied it: with the model named "stub-private:12b", "Remember that my locker is 12."
then "Forget 12." answered "Forget could not be verified as fully applied: unclassified provider invocation
manifests, ..." every time, and the memory stayed. Verification read the provider call records' "model_id" and
"provider_id" ("...:12b") as words. Their signatures are left out by #207's rule, which these tests keep.
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
    """A provider call record row as provider_invocation_gateway stores it."""
    manifest = {"model_id": "mistral-nemo:12b", "provider_id": "ollama-local:mistral-nemo:12b", "operation": "chat",
                "signature": "qEJvV/aq20/tso6mMUu+wiiKkUavwU4XiuZ3kmwHKgf+426c/ElleXbpzKuRdyqWCIPQS5FdmPsfTKGmqzpeAw==",
                "provider_messages": [{"role": "user", "content_hash": "2b510ff279c5f149" * 4}]}
    row = {"manifest_id": "provider-manifest-0c4ce4c766504b88a8e055adc97355ea", "chat_id": "openclaw:" + "e5" * 10,
           "model_id": manifest["model_id"], "provider_id": manifest["provider_id"], "operation": "chat",
           "signature": manifest["signature"], "manifest_json": json.dumps(manifest),
           "created_at": "2026-10-20T22:52:28.444885+00:00"}
    row.update(fields)
    return row


RECEIPTS = "provider_invocation_manifests"


def test_a_call_records_model_name_and_signature_are_not_words_a_forget_waits_on():
    row = _receipt()

    assert len(row["signature"]) == 88, "probe is blind: not the runtime's signature shape"
    for number in ("12", "20", "5"):
        assert not carries(_unclassified_payload(row, RECEIPTS), number), number


def test_the_persons_words_still_hold_a_forget_open_wherever_a_model_is_named():
    # Words under any other field of a call record still count.
    assert carries(_unclassified_payload(_receipt(note="my locker is 12"), RECEIPTS), "12")
    # Only a call record's own top-level names are left out: a nested "model_id" or a "car_model" is read.
    nested = _receipt(manifest_json=json.dumps({"tool_args": {"model_id": "locker 12"}}))
    assert carries(_unclassified_payload(nested, RECEIPTS), "12")
    assert carries(_unclassified_payload(_receipt(car_model="Corolla 12"), RECEIPTS), "12")
    # Another table's model name may be the person's own words.
    assert carries(_unclassified_payload({"chat_id": "c", "model_id": "locker 12"}, "user_notes"), "12")


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
