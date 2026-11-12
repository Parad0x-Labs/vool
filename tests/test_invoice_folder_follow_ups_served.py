"""Follow-ups on a folder VOOL just listed, through the real /api/chat door (red team R1-07, R1-09, R1-12).

Measured on the desktop app, 2026-11-12, with red's seeded home:
- "whats in my Invoices old folder in documents?" listed the four invoices, then "how many files are in
  that folder" said no path target was established, and "which is the biggest one there" said it had
  no access to file sizes;
- "what invoices do i have in the Invoices old folder (in documents)?" only found the folder, then "the
  second one - how much was that?" answered "$125" with no tool run (the real total is 120.00 EUR), and
  "no not that one, the one before it" said "Let me check that for you. One moment!" and ran nothing.

The home below holds the same four invoices as real PDFs with known totals and sizes. The scripted
model states a total only when the prompt carries that invoice's text, so a right answer proves the
file was read, and the default reply is the invented amount, so a pass cannot come from the model.
"""

from __future__ import annotations

import json
import uuid

import pytest

import tests._reader_served_rig as rig
from tests.test_memory_forget_served_p0 import _reply_text

pytestmark = pytest.mark.served

INVOICES = [  # name, total, padding bytes (sets the file size order)
    ("invoice_2023_014_roofer.pdf", "2,400.00", 200),
    ("invoice_2023_015_vet.pdf", "120.00", 50),
    ("invoice_2024_001_movers.pdf", "890.00", 100),
    ("invoice_2024_002_painter.pdf", "1,320.00", 900),
]
BIGGEST = "invoice_2024_002_painter.pdf"
DOWNLOADS = ["IMG_0001.jpg", "dog_park.jpeg", "beach-sunset.png", "Screenshot 2026-11-10 at 14.02.11.png"]


def _pdf(lines: list[str], padding: int) -> bytes:
    """A one-page PDF whose text a PDF reader can extract."""
    text = "BT /F1 12 Tf 60 800 Td " + " ".join(f"({line}) Tj 0 -18 Td" for line in lines) + " ET"
    text += "\n%" + "x" * padding
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(text) + text.encode() + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def _scripted_reply(body: dict) -> str | None:
    prompt = json.dumps(body)
    for name, total, _pad in INVOICES:
        if f"Total: {total} EUR" in prompt and name[8:16] in prompt:
            return f"That invoice totals {total} EUR."
    return None


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home_dir = tmp_path_factory.mktemp("invoices-home")
    for name in ("Desktop", "Downloads"):
        (home_dir / name).mkdir()
    for name in DOWNLOADS:
        (home_dir / "Downloads" / name).write_bytes(b"\x89PNG" if name.endswith(".png") else b"\xff\xd8\xff")
    folder = home_dir / "Documents" / "Invoices old"
    folder.mkdir(parents=True)
    for name, total, padding in INVOICES:
        (folder / name).write_bytes(_pdf([f"INVOICE {name[8:16]}", f"Total: {total} EUR"], padding))
    with rig.CapturingProvider(
        default="The second invoice in the folder was for $125. Let me check that for you. One moment!",
        reply_fn=_scripted_reply,
    ) as provider:
        daemon = rig.ServedDaemon(home_dir / "vool", provider=provider, env_extra={"HOME": str(home_dir)})
        daemon.start()
        assert daemon.certify(timeout=180).get("state") == "verified"
        try:
            yield daemon
        finally:
            daemon.stop()


def _chat(daemon, session: str, text: str) -> str:
    return _reply_text(daemon.chat(text, session_id=session))


def _session() -> str:
    return rig.canonical_session("inv-" + uuid.uuid4().hex[:8])


@pytest.mark.parametrize("asked", [
    "whats in my Invoices old folder in documents?",
    "what invoices do i have in the Invoices old folder (in documents)?",
    "show me the files inside documents/Invoices old",
])
def test_asking_what_is_in_a_named_folder_lists_its_files(served, asked):
    reply = _chat(served, _session(), asked)
    for name, _total, _pad in INVOICES:
        assert name in reply, (name, reply)
    assert "Found 1 folder" not in reply, reply


@pytest.mark.parametrize("count_ask", ["how many files are in that folder", "how many are there in it?"])
def test_a_count_of_the_folder_just_listed(served, count_ask):
    session = _session()
    _chat(served, session, "whats in my Invoices old folder in documents?")
    reply = _chat(served, session, count_ask)
    assert "4" in reply or "four" in reply.lower(), reply
    assert "did not establish" not in reply and "path target" not in reply, reply


@pytest.mark.parametrize("size_ask", ["which is the biggest one there", "whats the largest file in that folder"])
def test_the_biggest_file_of_the_folder_just_listed(served, size_ask):
    session = _session()
    _chat(served, session, "whats in my Invoices old folder in documents?")
    reply = _chat(served, session, size_ask)
    assert BIGGEST in reply, reply
    assert "don't have access to file sizes" not in reply, reply


def test_an_ordinal_pick_reads_that_invoice_and_never_invents_an_amount(served):
    session = _session()
    _chat(served, session, "what invoices do i have in the Invoices old folder (in documents)?")
    second = _chat(served, session, "the second one - how much was that?")
    assert "120.00" in second, second
    assert "$125" not in second, second
    before = _chat(served, session, "no not that one, the one before it")
    assert "2,400.00" in before, before
    assert "One moment" not in before and "$125" not in before, before


@pytest.mark.parametrize("asked, expected", [
    ("show only the png files in downloads", {"beach-sunset.png", "Screenshot 2026-11-10 at 14.02.11.png"}),
    ("gimme just the screen grabs from my downloads folder", {"Screenshot 2026-11-10 at 14.02.11.png"}),
    ("any jpgs in my downloads?", {"IMG_0001.jpg", "dog_park.jpeg"}),
])
def test_a_named_extension_or_screenshot_word_filters_to_exactly_those_files(served, asked, expected):
    # Red team round 3: "png" listed every picture, and "screen grabs" ended in "I wasn't able to turn
    # that into a completed action".
    reply = _chat(served, _session(), asked)
    shown = {name for name in DOWNLOADS if name in reply}
    assert shown == expected, reply


def test_how_to_answer_rides_the_count_and_is_not_answered_on_its_own(served):
    # Red team R1-03: "just the count" was sent to the model as a request of its own, and its reply
    # was printed (or withheld with a "Retry the turn" footer) under the right count.
    session = _session()
    reply = _chat(served, session, "how many pdf files are in my Documents folder? just the count")
    assert "`~/Documents`" in reply, reply
    for wrong in ("$125", "One moment", "Withheld", "Retry the turn", "Could not be answered"):
        assert wrong not in reply, (wrong, reply)
    again = _chat(served, session, "again")
    assert "`~/Documents`" in again and "$125" not in again, again


def test_a_private_chat_never_reaches_the_exact_read_lane_through_the_clean_loop(monkeypatch):
    """The clean loop hands an exact folder ask to the exact lane only for a turn it admitted; a
    private chat is never admitted, so its words reach neither the loop nor that lane from here."""
    import core.agent_runtime.fast_paths_machine as machine
    from core.clean_loop import hook, route
    from core.private_chat import PRIVATE_TURN_KEY

    monkeypatch.setenv(route.FLAG_ENV, "1")

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("the exact lane ran for a private turn")

    monkeypatch.setattr(machine, "maybe_answer_exact_read_first", _must_not_run)
    context = {"surface": "api", PRIVATE_TURN_KEY: True, "_conversation_slots": {"path": "~/Documents/Invoices old"}}

    class _Agent:
        memory_router = None

    assert hook.maybe_answer(
        _Agent(), text="how many files are in that folder", effective_input="how many files are in that folder",
        session_id="private-chat", source_context=context,
    ) is None
    assert context.get("clean_local_loop_route") != "exact_read_lane"
