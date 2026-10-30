"""Served: a size-of-a-file ask never carries the machine's drive fill, in the reply or in what
reaches the provider; a disk ask still gets the drive numbers.

Drives the real ``/api/chat`` door through the served rig (one daemon, one scripted loopback
provider for the whole module -- startup is the slow part). The daemon sees a seeded fake HOME,
so a size ask resolves against real bytes on disk. A size ask may be answered before any model
turn (zero provider calls) or by the model lane; either way no drive-fill numbers may appear in
the reply or in any captured model-bound request. A disk ask must still come back with the
drive numbers.

The mid-turn gate (a model that ITSELF calls ``machine.disk_usage`` on a non-disk ask must
receive a number-free refusal) is proven in tests/test_drive_stats_privacy_gate.py instead: on
this rig, chat-class and file-class turns carry no native tool definitions (measured
``tools=[]``; only certification probes and email-class tasks get them), so a scripted mid-turn
call cannot be driven through this door.
"""
from __future__ import annotations

import re
import uuid

import pytest

import tests._reader_served_rig as rig

pytestmark = [pytest.mark.served]

#: Drive-fill shapes that must never appear in a size-ask REPLY.
REPLY_DISK_FILL_PATTERNS = (
    re.compile(r"\d+(?:\.\d+)?\s*(?:GB|MB|TB|GiB|MiB|TiB)\s+free", re.IGNORECASE),
    re.compile(r"free\s+of", re.IGNORECASE),
    re.compile(r"%\s*(?:full|used)", re.IGNORECASE),
    re.compile(r"drive\s+space", re.IGNORECASE),
    re.compile(r"disk", re.IGNORECASE),
)

#: Drive-fill shapes that must never appear in a model-bound PAYLOAD or a tool result. Tool
#: contracts and descriptions may legitimately name the disk tools, so only the NUMBERS and the
#: serialized drive rows are barred here.
PAYLOAD_DISK_FILL_PATTERNS = (
    re.compile(r"\d+(?:\.\d+)?\s*(?:GB|MB|TB|GiB|MiB|TiB)\s+free", re.IGNORECASE),
    re.compile(r"free\s+of", re.IGNORECASE),
    re.compile(r"%\s*(?:full|used)", re.IGNORECASE),
    re.compile(r"\"(?:free_gb|total_gb|percent_used|drives)\"\s*:", re.IGNORECASE),
)

#: What the disk lane's answer looks like ("24.7 GB free of 460.4 GB"): the disk asks below
#: must still produce exactly this.
DRIVE_NUMBERS_RE = re.compile(r"\d+(?:\.\d+)?\s*GB\s+free\s+of", re.IGNORECASE)

ZIP_BYTES = 12 * 1024 * 1024
PDF_BYTES = 700 * 1024
VIDEO_BYTES = 5 * 1024 * 1024

NEUTRAL_REPLY = "Here is what I can say about that."
SIZE_ASKS = [
    "how big is the holiday-pictures.zip in my downloads?",
    "how many megabytes is the quarterly-report.pdf?",
    "whats the total size of the Taxes 2025 folder in my documents?",
    "how large is the wedding-video.mp4?",
]

# Both verified to route to the existing disk lane (machine.disk_usage) on this checkout.
DISK_ASKS = [
    "am i running low on storage on this machine?",
    "is the ssd nearly full yet?",
]


def _assert_clean(text: str, patterns: tuple) -> None:
    for pattern in patterns:
        assert not pattern.search(text), pattern.pattern


def _answer_auxiliary_probe(body: dict) -> str | None:
    """Answer the runtime's auxiliary ambiguity check so a stub-provider turn does not die at it.

    The scripted provider answers every plain request from its default text; the ambiguity check
    demands a JSON verdict, and with the default text it retries twice and ends the turn with
    "couldn't complete my check" -- measured on this rig before this hook: a DISK ask died there
    and never reached the deterministic disk lane. Replying ``not ambiguous`` is what a real model
    answers for these single-referent questions; the turn then continues into normal routing.
    """
    text = " ".join(
        str(m.get("content") or "") for m in (body.get("messages") or []) if isinstance(m, dict)
    )
    if "Reply with ONLY a JSON object" in text:
        return '{"ambiguous": false, "referents": [], "clarification": ""}'
    return None


def _reply_of(out: dict) -> str:
    return str((out.get("message") or {}).get("content") or "")



@pytest.fixture(scope="module")
def served(tmp_path_factory: pytest.TempPathFactory):
    """One daemon for the whole module. The user HOME the daemon sees is seeded BEFORE start so
    the machine tools resolve the named files there; the daemon's own state lives elsewhere."""
    user_home = tmp_path_factory.mktemp("size-user-home")
    for name in ("Desktop", "Downloads", "Documents"):
        (user_home / name).mkdir(parents=True)
    (user_home / "Downloads" / "holiday-pictures.zip").write_bytes(b"\0" * ZIP_BYTES)
    (user_home / "Downloads" / "quarterly-report.pdf").write_bytes(b"\0" * PDF_BYTES)
    (user_home / "Documents" / "wedding-video.mp4").write_bytes(b"\0" * VIDEO_BYTES)
    taxes = user_home / "Documents" / "Taxes 2025"
    taxes.mkdir()
    (taxes / "receipts.pdf").write_bytes(b"\0" * (512 * 1024))
    (taxes / "scan.png").write_bytes(b"\0" * (256 * 1024))
    daemon_home = tmp_path_factory.mktemp("size-daemon-home")

    provider = rig.CapturingProvider(default=NEUTRAL_REPLY, reply_fn=_answer_auxiliary_probe)
    provider.__enter__()
    daemon = rig.ServedDaemon(daemon_home, provider=provider, env_extra={"HOME": str(user_home)})
    try:
        daemon.register_provider()
        daemon.start()
        assert daemon.certify().get("state") == "verified", "scripted provider did not certify"
        yield daemon, provider
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


# --- Case A: a size ask is answered with the size only, and the provider never sees drive fill ---

@pytest.mark.parametrize("ask", SIZE_ASKS)
def test_a_size_ask_reply_and_payloads_carry_no_drive_numbers(served, ask):
    daemon, provider = served
    provider.reset()
    out = daemon.chat(ask, session_id=rig.canonical_session("size-clean-" + uuid.uuid4().hex[:8]))
    answer = _reply_of(out)
    assert answer, "the turn must produce a reply"
    _assert_clean(answer, REPLY_DISK_FILL_PATTERNS)
    # Zero payloads (answered before any model turn) passes; any payload there is must be clean.
    for payload in provider.payloads():
        _assert_clean(payload, PAYLOAD_DISK_FILL_PATTERNS)


# --- Case B: a disk ask still gets the drive numbers ---

@pytest.mark.parametrize("ask", DISK_ASKS)
def test_a_disk_ask_still_reports_the_drive_numbers(served, ask):
    daemon, provider = served
    provider.reset()
    out = daemon.chat(ask, session_id=rig.canonical_session("disk-numbers-" + uuid.uuid4().hex[:8]))
    answer = _reply_of(out)
    assert answer, "the turn must produce a reply"
    assert DRIVE_NUMBERS_RE.search(answer), "a disk ask must still be answered with the drive numbers"
