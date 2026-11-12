"""Reader refusals, in the words a person actually reads.

The confinement boundary keeps its own vocabulary for logs and capability reports; what the
chat shows never names decoders, sandboxes or parsers. Two raisers keep their technical text
because a released test pins it (``unconfined`` in the reader's message, the install hint in
the Linux lane); the chat seam replaces exactly that family with one plain, translated
sentence, and the technical detail goes to the log.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import core.artifact_readers as readers
from core import chat_attachments
from core.artifact_readers import _sandbox
from core.i18n.turn_locale import bind_turn_locale, reset_turn_locale
from tests.reader_fixtures import native_text_pdf

#: Words a person must never meet in a refusal. Substrings on purpose: "confine" also catches
#: "confined" and "unconfined", "decoder" also catches "decoders".
BANNED = ("decoder", "sandbox", "bubblewrap", "confine", "parser")

#: The one refusal family whose raiser text stays technical (pinned by released tests); the
#: person-facing replacement for it is asserted behaviorally below.
PINNED_TECHNICAL_CODES = {"confinement_unavailable"}

READERS_DIR = Path(readers.__file__).parent
CATALOGS_DIR = Path(readers.__file__).parent.parent / "i18n" / "catalogs"


def _shown_text(exc: BaseException) -> str:
    """Exactly what the chat composes for a person: the message and the remediation, joined."""
    return f"{getattr(exc, 'message', '')} {getattr(exc, 'remediation', '') or ''}".strip()


def test_a_security_blocked_upload_reads_one_plain_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_sandbox, "confinement_available", lambda: False)
    with pytest.raises(chat_attachments.AttachmentRefused) as caught:
        chat_attachments._extract_artifact(
            name="report.pdf", data=native_text_pdf(), media_type="application/pdf"
        )
    refusal = caught.value
    assert refusal.code == "confinement_unavailable"
    assert refusal.message == chat_attachments.ATTACH_READ_BLOCKED_EN
    for word in BANNED:
        assert word not in refusal.message.lower()
    assert "paste" in refusal.message.lower()


def test_the_blocked_file_sentence_follows_the_app_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_sandbox, "confinement_available", lambda: False)
    token = bind_turn_locale("lt")
    try:
        with pytest.raises(chat_attachments.AttachmentRefused) as caught:
            chat_attachments._extract_artifact(
                name="report.pdf", data=native_text_pdf(), media_type="application/pdf"
            )
    finally:
        reset_turn_locale(token)
    lithuanian = json.loads((CATALOGS_DIR / "lt.json").read_text())["messages"][
        chat_attachments.ATTACH_READ_BLOCKED_KEY
    ]
    assert caught.value.message == lithuanian
    assert caught.value.message != chat_attachments.ATTACH_READ_BLOCKED_EN


def test_a_missing_reader_refusal_stays_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.artifact_readers import documents

    monkeypatch.setattr(documents, "_import_xrd", lambda: None)
    with pytest.raises(readers.ReaderUnavailable) as caught:
        documents.read_xls(b"not a workbook", name="book.xls")
    shown = _shown_text(caught.value)
    for word in BANNED:
        assert word not in shown.lower()
    # The actionable fix survives the rewording: this exact string is pinned by a released test.
    assert "xlrd==2.0.2" in caught.value.remediation


def _reader_raise_sites() -> list[tuple[str, set[str], list[str]]]:
    """Every typed reader raise: (file, codes, constant strings in message and remediation)."""
    sites: list[tuple[str, set[str], list[str]]] = []
    for path in sorted(READERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"ReaderUnavailable", "ReaderRefused", "ReaderFailed"}
            ):
                continue
            codes: set[str] = set()
            texts: list[str] = []
            for keyword in node.keywords:
                if keyword.arg == "code" and isinstance(keyword.value, ast.Constant):
                    codes.add(str(keyword.value.value))
                elif keyword.arg in {"message", "remediation"}:
                    texts.extend(
                        str(part.value) for part in ast.walk(keyword.value) if isinstance(part, ast.Constant)
                    )
            texts.extend(
                str(part.value)
                for arg in node.args
                for part in ast.walk(arg)
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            sites.append((path.name, codes, texts))
    assert sites, "the scan found no reader raises at all"
    return sites


def test_every_reader_raise_shows_plain_words() -> None:
    offenders: list[str] = []
    for filename, codes, texts in _reader_raise_sites():
        if codes & PINNED_TECHNICAL_CODES:
            continue  # technical raiser; the chat seam replaces it (asserted behaviorally above)
        joined = " ".join(texts).lower()
        for word in BANNED:
            if word in joined:
                offenders.append(f"{filename}: {word!r} in {texts}")
    assert not offenders, "\n".join(offenders)


def test_the_registry_never_lists_a_decoder_by_name() -> None:
    """What a format says it needs reaches people in remediations; it must be plain there too."""
    tree = ast.parse((READERS_DIR / "__init__.py").read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ReaderSpec"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "requires":
                continue
            for part in ast.walk(keyword.value):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    for word in BANNED:
                        assert word not in part.value.lower(), (part.value, word)


def test_the_plain_blocked_sentence_exists_in_every_catalog() -> None:
    values: dict[str, str] = {}
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        key = chat_attachments.ATTACH_READ_BLOCKED_KEY
        assert key in data["messages"], f"{path.name} is missing {key}"
        values[path.stem] = str(data["messages"][key])
    for word in BANNED:
        assert word not in values["en"].lower()
    assert "paste" in values["en"].lower()
    for locale, value in values.items():
        if locale != "en":
            assert value != values["en"], f"{locale} carries a copy of the English text"
