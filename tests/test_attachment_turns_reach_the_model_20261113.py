"""CI #683 macos reds, reproduced on any machine: two attachment turns never reached the model.

* "What is the total on the attached receipt?" was answered by the wallet's money answers ("Crypto is switched
  off ...") and the receipt never reached the model.
* "Describe the attached video; what is my screen resolution?" on a machine whose display cannot be read (a headless
  runner, this box): the display read failed and its refusal replaced the whole turn, so the video was never described.

Real served daemon, real lanes; the provider is the capturing stub.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._reader_served_rig import CapturingProvider, ServedDaemon, canonical_session
from tests.reader_fixtures import sample_video, text_image_png

CRYPTO_OFF = "Crypto is switched off"


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory):
    home = tmp_path_factory.mktemp("attachment-turns-home")
    with CapturingProvider() as provider, ServedDaemon(
        Path(home), provider=provider, vision=True, provider_name="reader-stub-vision", model="reader-drive:vision"
    ) as daemon:
        assert daemon.certify().get("state") == "verified"
        yield daemon, provider


def _ask(rig, *, name: str, data: bytes, declared_type: str, question: str, seed: str):
    daemon, provider = rig
    session = canonical_session(seed)
    uploaded = daemon.upload(session_id=session, name=name, data=data, declared_type=declared_type)
    assert uploaded["status"] == 201, uploaded
    provider.reset()
    reply = daemon.chat(question, session_id=session, attachments=[uploaded["attachment"]["id"]])
    message = reply.get("message") if isinstance(reply.get("message"), dict) else {}
    return str(message.get("content") or ""), provider


def test_a_question_about_an_attached_receipt_is_not_a_wallet_question(rig):
    png = text_image_png(["RECEIPT 55182", "TOTAL: 91.40"], width=1000, height=520, point=40)
    answer, provider = _ask(rig, name="receipt.png", data=png, declared_type="image/png",
                            question="What is the total on the attached receipt?", seed="attached-receipt-total")
    assert provider.calls, f"the model was never called: {answer}"
    assert CRYPTO_OFF not in answer
    assert provider.total_images() >= 1, "the receipt never reached the model"


def test_a_failed_display_read_beside_a_video_still_describes_the_video(rig):
    clip = sample_video(seconds=6, fps=5, with_audio=False)
    if not clip:
        pytest.skip("no video encoder available to build the fixture")
    from core.machine_diagnostics import display_info
    from core.runtime_execution_tools import _machine_display_details

    if display_info().get("supported") or _machine_display_details().get("current_resolution"):
        pytest.skip("this machine's display can be read; the failed-read case needs one that cannot")
    answer, provider = _ask(rig, name="clip.mp4", data=clip, declared_type="video/mp4",
                            question="Describe the attached video; what is my screen resolution?", seed="video-and-display")
    assert provider.calls, f"the model was never called: {answer}"
    payload = "\n".join(provider.payloads())
    assert "Describe the attached video" in payload
    assert "machine.display_inspect" in payload and "couldn't read the display configuration" in payload
    assert provider.total_images() >= 2, "the video frames never reached the model"
    assert "I won't guess a value" not in answer, "the display refusal replaced the whole turn"
