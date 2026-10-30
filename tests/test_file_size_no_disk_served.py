"""Served: a size-of-a-file ask never carries the machine's drive fill, in the reply or in what
reaches the provider; a disk ask still gets the drive numbers.

Drives the real ``/api/chat`` door through the served rig (one daemon, one scripted loopback
provider for the whole module -- startup is the slow part). The daemon sees a seeded fake HOME,
so a size ask resolves against real bytes on disk. A size ask may be answered before any model
turn (zero provider calls) or by the model lane; either way no drive-fill numbers may appear in
the reply or in any captured model-bound request. A disk ask must still come back with the
drive numbers. The gate case scripts a model that tries to call ``machine.disk_usage`` mid-turn
for a non-disk ask: the tool result handed back to the provider must be a plain-words refusal
with no drive numbers, so machine stats never reach the provider unasked.
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
GATE_PLAIN_ANSWER = "I can answer from the file itself, nothing more."

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


def _reply_of(out: dict) -> str:
    return str((out.get("message") or {}).get("content") or "")


def _tool_result_texts(provider) -> list[str]:
    with provider._lock:
        calls = list(provider.calls)
    texts: list[str] = []
    for call in calls:
        for message in call.get("messages") or []:
            if isinstance(message, dict) and str(message.get("role") or "") == "tool":
                texts.append(str(message.get("content") or ""))
    return texts


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

    provider = rig.CapturingProvider(default=NEUTRAL_REPLY)
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


# --- Case C: the gate. A scripted model tries machine.disk_usage mid-turn; the tool result the
# provider gets back must be a plain-words refusal with no drive numbers in it.

def _armed_probe(original, marker: str):
    """Wrap the rig's probe answer: for the marked turn, the model first tries a provider-native
    ``machine.disk_usage`` call and answers plainly on the next round. Certification shapes fall
    through to the rig's own probe untouched."""

    def probe(body: dict) -> dict | None:
        offered = rig._tool_names(body)
        if rig._PROBE_ECHO in offered or {rig._PROBE_ADD, rig._PROBE_NONCE} <= offered:
            return original(body)
        if not offered:
            # An auxiliary call (no tools offered) never carries the turn's tool loop.
            return original(body)
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        user_text = "\n".join(
            str(m.get("content") or "") for m in messages if str(m.get("role") or "") == "user"
        )
        if marker not in user_text:
            return original(body)
        if any(str(m.get("role") or "") == "tool" for m in messages):
            return {"role": "assistant", "content": GATE_PLAIN_ANSWER}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [rig._call("machine.disk_usage", {}, 1)],
        }

    return probe


def _run_gate_case(served, monkeypatch, ask: str, marker: str) -> None:
    daemon, provider = served
    monkeypatch.setattr(rig, "_probe_reply", _armed_probe(rig._probe_reply, marker))
    provider.reset()
    out = daemon.chat(ask, session_id=rig.canonical_session("size-gate-" + uuid.uuid4().hex[:8]))
    answer = _reply_of(out)
    assert answer, "the turn must complete with a reply"
    _assert_clean(answer, PAYLOAD_DISK_FILL_PATTERNS)
    tool_results = _tool_result_texts(provider)
    assert tool_results, "the scripted machine.disk_usage call never reached the tool loop"
    for tool_result in tool_results:
        assert tool_result.strip(), "the refusal must be plain words, not empty"
        _assert_clean(tool_result, PAYLOAD_DISK_FILL_PATTERNS)
    for payload in provider.payloads():
        _assert_clean(payload, PAYLOAD_DISK_FILL_PATTERNS)


def test_a_model_disk_call_on_a_size_ask_gets_a_number_free_refusal(served, monkeypatch):
    # A size ask with a second half the deterministic lane cannot answer alone, so the turn can
    # reach the model lane -- where the scripted model goes looking for the drive numbers.
    _run_gate_case(
        served,
        monkeypatch,
        "how big is the holiday-pictures.zip in downloads, and is that small enough to attach in gmail?",
        "attach in gmail",
    )


def test_a_model_disk_call_on_an_ordinary_ask_gets_a_number_free_refusal(served, monkeypatch):
    # The gate's law in its general form: ANY ask that is not a disk ask never pulls machine
    # stats to the provider. This ask is claimed by no fast path, so it always reaches the model.
    _run_gate_case(
        served,
        monkeypatch,
        "what should i pack for a weekend trip to the mountains?",
        "weekend trip",
    )
