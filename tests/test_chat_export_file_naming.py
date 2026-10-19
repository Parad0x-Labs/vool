"""A chat export is a file a person keeps: named after the chat, with a short human header.

Driven through the real ``/api/chat/export`` door (``dispatch_get``), with the transcript seeded
through the production writer. The file name is read back the way a client reads it, from the
``Content-Disposition`` header (``email.message`` decodes RFC 2231/5987 the same way the desktop
shell does), never from the helper that built it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from email.message import Message
from io import BytesIO

import pytest

from tests import test_chat_export_api as export
from tests import test_chat_visuals_browser as visuals
from tests.test_chat_export_copy_ui import HTML

_isolated_home = export._isolated_home
browser = visuals.browser

BIDI_CONTROLS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _name(response) -> str:
    """The name a browser saves under: ``filename*`` wins over ``filename`` (RFC 6266 4.3)."""
    disposition = response.headers["Content-Disposition"]
    disposition.encode("ascii")  # every byte of the header is plain ASCII on the wire
    match = re.search(r"filename\*=UTF-8''([^;\s]+)", disposition)
    assert match, disposition
    assert re.fullmatch(r"[A-Za-z0-9!#$&+\-.^_`|~%]+", match.group(1)), match.group(1)
    message = Message()
    message["content-disposition"] = "attachment; filename*=UTF-8''" + match.group(1)
    return message.get_filename()


def _ascii_name(response) -> str:
    match = re.search(r'filename="([^"]*)"', response.headers["Content-Disposition"])
    assert match, response.headers["Content-Disposition"]
    return match.group(1)


def _rename(title: str) -> None:
    from core.persistent_memory import set_session_meta

    set_session_meta(export.SESSION, title=title)


def _header(body: str) -> str:
    """The part of the file before the first message."""
    return re.split(r"\n---\n|\n={72}\n", body, maxsplit=1)[0]


@pytest.mark.parametrize("fmt", ["md", "txt", "pdf"])
def test_file_is_named_after_the_chat_and_the_day(fmt) -> None:
    export._seed(user="can you write a note about the fence", assistant="Here is the note.")
    _rename("Fence note")
    response = export._export(format=fmt)
    assert response.status == 200, response.body[:300]
    assert _name(response) == f"Fence note - VOOL chat - {_today()}.{fmt}"
    assert "openclaw" not in response.headers["Content-Disposition"]


def test_an_unrenamed_chat_is_named_after_its_first_message() -> None:
    export._seed(user="  what is a good name\nfor a cat  ", assistant="Miso.")
    export._seed(user="and for a dog", assistant="Biscuit.")
    response = export._export()
    assert _name(response) == f"what is a good name for a cat - VOOL chat - {_today()}.md"
    assert response.body.decode("utf-8").startswith("# what is a good name for a cat\n")


def test_an_untitled_chat_falls_back_to_vool_chat() -> None:
    export._seed(user="", assistant="Hello there.")
    response = export._export()
    assert response.status == 200
    assert _name(response) == f"VOOL chat - {_today()}.md"
    assert response.body.decode("utf-8").startswith("# VOOL chat\n")


@pytest.mark.parametrize("fmt", ["md", "txt"])
def test_header_is_short_and_says_nothing_technical(fmt) -> None:
    export._seed(user="fence question", assistant="fence answer")
    export._seed(user="second question", assistant="second answer")
    _rename("Fence note")
    body = export._export(format=fmt).body.decode("utf-8")
    header = _header(body)
    title_line = "# Fence note" if fmt == "md" else "Fence note"
    assert header.splitlines()[:3] == [title_line, "", f"Exported from VOOL on {_today()} · 4 messages"]
    assert "Not included: how VOOL worked things out and its activity log." in header
    for engine_word in ["Session", "openclaw", "log rows", "snapshot", "Format:", "Complete:", "turns",
                        "Artifact cards", "Source:", "persisted", "verbatim", "Timestamps:"]:
        assert engine_word not in header, engine_word
    # The messages themselves are all there after the header.
    assert "fence question" in body and "second answer" in body


def test_one_message_is_singular() -> None:
    export._seed(user="", assistant="only an answer")
    assert "· 1 message\n" in export._export().body.decode("utf-8")


def test_disclosures_that_matter_stay_in_plain_words() -> None:
    from core.finalization import AVAILABILITY_WITHHELD, set_availability
    from core.memory.files import conversation_log_path

    secret = "naming test: the withheld answer, never in the file"
    commit = export._admit_finalize(secret, request_id="req-naming-withheld")
    assert set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD, reason="naming-test")
    with export._request_scope("req-naming-withheld"):
        export._seed(user="ask for the secret", assistant=secret)
    with conversation_log_path().open("ab") as handle:
        handle.write(b"not json at all\n")
    export._seed(user="after", assistant="fine")
    body = export._export(timestamps="1").body.decode("utf-8")
    header = _header(body)
    assert "1 answer is not included because of the privacy policy." in header
    assert "1 saved part of this chat could not be read, so it is not in this file." in header
    assert "Times show when each answer finished." in header
    assert "Attached files" not in header  # this chat has none, so nothing is said about them
    assert secret not in body
    assert "WITHHELD" not in body and "ERASED" not in body
    assert "## Assistant — unavailable by privacy policy\n" in body
    # A time a person reads (local date and minute), never an id.
    assert re.search(r"## Assistant \(answered \d{4}-\d{2}-\d{2} \d{2}:\d{2}\)\n", body)
    assert "request " not in body


def test_attached_files_are_explained_only_when_the_chat_has_some() -> None:
    from core import chat_attachments

    record = chat_attachments.stage_attachment(
        session_id=export.SESSION, declared_name="fence-plan.txt", declared_type="text/plain", data=b"north 12 m"
    )
    chat_attachments.bind_to_turn(session_id=export.SESSION, turn_id="turn-naming-att", attachment_ids=[record["id"]])
    chat_attachments.record_delivery(
        session_id=export.SESSION, turn_id="turn-naming-att",
        receipts=[{"attachment_id": record["id"], "outcome": "read"}],
    )
    export._seed(user="here is the plan", assistant="got it", attachment_turn_id="turn-naming-att")
    listed = export._export().body.decode("utf-8")
    assert "Attached files are listed by name only." in _header(listed)
    assert "fence-plan.txt" in listed and "north 12 m" not in listed
    left_out = export._export(no_attachments="1").body.decode("utf-8")
    assert "Attached files are left out, as you chose." in _header(left_out)
    assert "fence-plan.txt" not in left_out


def test_german_ui_gets_a_german_file() -> None:
    export._seed(user="Zaun", assistant="Hier ist die Notiz.")
    _rename("Zaunnotiz")
    response = export._export(ui_locale="de")
    body = response.body.decode("utf-8")
    assert _name(response) == f"Zaunnotiz - VOOL-Chat - {_today()}.md"
    assert body.startswith(f"# Zaunnotiz\n\nAus VOOL exportiert am {_today()} · 2 Nachrichten\n")
    assert "## Nutzer" in body and "## Assistent" in body
    # The messages are the person's own words, untouched by the app language.
    assert "Hier ist die Notiz." in body


def test_unknown_ui_locale_is_english_not_a_guess() -> None:
    export._seed(user="hi", assistant="hello")
    body = export._export(ui_locale="xx-nope").body.decode("utf-8")
    assert "Exported from VOOL on" in body


@pytest.mark.parametrize("title,expected_stem", [
    ("ملاحظة السياج", "ملاحظة السياج"),
    ("גדר חדשה", "גדר חדשה"),
    ("Fence note 🏡", "Fence note 🏡"),
    ("👨\u200d👩\u200d👧 family plans", "👨\u200d👩\u200d👧 family plans"),
    ('Q3 "plan"; v2/final\\draft: a*b?c<d>e|f', "Q3 plan ; v2 final draft a b c d e f"),
    ("100% sure, 50%20off", "100% sure, 50%20off"),
    ("photo\u202egpj.exe", "photo gpj.exe"),
    ("..hidden name. ", "hidden name"),
    ("line one\nline two\ttab", "line one line two tab"),
    ("- rm -rf notes", "rm -rf notes"),
    ("Tom & Jerry's <3", "Tom & Jerry's 3"),
])
def test_titles_survive_the_header_exactly(title, expected_stem) -> None:
    export._seed(user="question", assistant="answer")
    _rename(title)
    response = export._export()
    name = _name(response)
    assert name == f"{expected_stem} - VOOL chat - {_today()}.md"
    assert not BIDI_CONTROLS.search(name)
    ascii_name = _ascii_name(response)
    assert ascii_name.isascii() and ascii_name.endswith(f"VOOL chat - {_today()}.md")
    assert not set(ascii_name) & set('"\\/%;')


def test_a_very_long_title_is_cut_at_a_word_and_stays_a_valid_name() -> None:
    export._seed(user="question", assistant="answer")
    _rename("garden " * 60 + "🌻" * 50)
    name = _name(export._export())
    stem = name.split(" - VOOL chat - ")[0]
    assert stem.endswith("garden…")
    assert len(name.encode("utf-8")) < 200


@pytest.mark.parametrize("title", ["🌻" * 200, "🇩🇪" * 100, "👍🏽" * 100, "ملاحظة" * 80])
def test_a_cut_never_splits_a_character(title) -> None:
    from core.web.api import chat_export_api

    stem = chat_export_api.filename_title(title)
    assert stem.endswith("…") and len(stem.encode("utf-8")) <= chat_export_api.MAX_FILENAME_TITLE_BYTES
    body = stem[:-1]
    unit = {"🌻" * 200: "🌻", "🇩🇪" * 100: "🇩🇪", "👍🏽" * 100: "👍🏽", "ملاحظة" * 80: "ملاحظة"}[title]
    if unit != "ملاحظة":
        assert body == unit * (len(body) // len(unit))


def test_single_answer_export_is_named_after_its_chat() -> None:
    with export._request_scope("chosen-answer"):
        export._seed(user="fence?", assistant="The fence is 2 m.")
    _rename("Fence note")
    response = export._export(format="pdf", request_id="chosen-answer")
    assert response.status == 200
    assert _name(response) == f"Fence note - VOOL answer - {_today()}.pdf"
    md = export._export(format="md", request_id="chosen-answer", ui_locale="de")
    assert _name(md) == f"Fence note - VOOL-Antwort - {_today()}.md"
    assert md.body.decode("utf-8") == "The fence is 2 m."


def test_single_answer_of_an_unrenamed_chat_never_carries_its_question() -> None:
    """The answer file leaves the question out, so its name, heading and PDF title must too."""
    from pypdf import PdfReader

    export._seed(user="FIRST-QUESTION-118 about my bank", assistant="Earlier answer.")
    with export._request_scope("answer-two"):
        export._seed(user="PRIVATE-QUESTION-452", assistant="The fence is 2 m.")
    for fmt in ("pdf", "md", "txt"):
        response = export._export(format=fmt, request_id="answer-two")
        assert response.status == 200, response.body[:300]
        assert _name(response) == f"VOOL answer - {_today()}.{fmt}"
        disposition = response.headers["Content-Disposition"]
        assert "QUESTION" not in disposition and "bank" not in disposition
    pdf = PdfReader(BytesIO(export._export(format="pdf", request_id="answer-two").body))
    assert "QUESTION" not in (pdf.metadata.title or "")
    assert "QUESTION" not in "\n".join(page.extract_text() for page in pdf.pages)
    # The whole chat still takes its name from its first message: that file holds it.
    assert _name(export._export()) == f"FIRST-QUESTION-118 about my bank - VOOL chat - {_today()}.md"


def test_an_emoji_in_the_chat_name_does_not_stop_the_pdf() -> None:
    from pypdf import PdfReader

    export._seed(user="fence?", assistant="The fence is 2 m.")
    _rename("Fence note 🏡")
    response = export._export(format="pdf")
    assert response.status == 200, response.body[:300]
    reader = PdfReader(BytesIO(response.body))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "Fence note" in text and "The fence is 2 m." in text
    assert reader.metadata.title == "Fence note 🏡"
    assert _name(response) == f"Fence note 🏡 - VOOL chat - {_today()}.pdf"


def test_title_punctuation_is_shown_not_interpreted() -> None:
    export._seed(user="question", assistant="answer")
    _rename("![x](https://example.invalid/a.png) *draft* #1")
    md = export._export().body.decode("utf-8")
    assert md.startswith("# \\!\\[x\\](https://example.invalid/a.png) \\*draft\\* \\#1\n")
    # Unescaped, the title would be an image, and an image refuses the whole PDF.
    assert export._export(format="pdf").status == 200


def test_plain_text_title_is_the_title_as_typed() -> None:
    export._seed(user="question", assistant="answer")
    _rename("Tom & Jerry <3 *draft*")
    body = export._export(format="txt").body.decode("utf-8")
    assert body.startswith("Tom & Jerry <3 *draft*\n\nExported from VOOL on ")


def test_preview_and_refusals_are_unchanged() -> None:
    export._seed(user="hi", assistant="hello")
    preview = json.loads(export._export(preview="1").body)
    assert preview["message_count"] == 2
    assert export._export(session="openclaw:ffffffffffffffffffff").status == 404


def _filename_helper_source() -> str:
    start = HTML.index("function downloadFilenameFrom(")
    end = HTML.index("async function mountProofChip(", start)
    return HTML[start:end]


def test_page_saves_under_the_servers_exact_name(browser) -> None:
    """The page reads the exact UTF-8 name first; the ASCII fallback only when it is absent."""
    page = browser.new_page()
    try:
        page.set_content("<!doctype html><body></body>")
        page.add_script_tag(content=_filename_helper_source())
        cases = [
            ("attachment; filename=\"VOOL chat - 2026-10-19.md\"; filename*=UTF-8''"
             "%D9%85%D9%84%D8%A7%D8%AD%D8%B8%D8%A9%20-%20VOOL%20chat%20-%202026-10-19.md",
             "ملاحظة - VOOL chat - 2026-10-19.md"),
            ("attachment; filename=\"100 sure - VOOL chat.md\"; filename*=UTF-8''100%25%20sure%20-%20VOOL%20chat.md",
             "100% sure - VOOL chat.md"),
            ('attachment; filename="Plain - VOOL chat.txt"', "Plain - VOOL chat.txt"),
            ("attachment; filename=\"x.md\"; filename*=UTF-8''%E0%A4%A", "x.md"),
            ("attachment; filename*=UTF-8''..%2F..%2Fetc%2Fpasswd", "passwd"),
            ("attachment; filename*=UTF-8''a%E2%80%AEdm.exe", "a dm.exe"),
            ("", "VOOL chat.md"),
            (None, "VOOL chat.md"),
        ]
        for header, expected in cases:
            got = page.evaluate("([h]) => downloadFilenameFrom(h, 'VOOL chat.md')", [header])
            assert got == expected, (header, got)
    finally:
        page.close()
