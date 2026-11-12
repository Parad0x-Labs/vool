"""The deterministic reference and coverage checker is itself a gate: it must pass on this
tree, and its output must be stable across runs (same source, same report: no timestamps,
no environment-dependent ordering)."""
from __future__ import annotations

from tools.check_error_reference import main, run_checks


def test_the_error_reference_checks_pass_on_this_tree() -> None:
    problems, _report = run_checks()
    assert problems == [], "error reference drift: " + "; ".join(problems)


def test_the_checker_is_deterministic() -> None:
    first_problems, first_report = run_checks()
    second_problems, second_report = run_checks()
    assert first_problems == second_problems
    assert first_report == second_report, "the checker's report must be stable"


def test_the_command_exits_zero_and_says_so(capsys) -> None:
    assert main([]) == 0
    assert "error reference and coverage checks passed" in capsys.readouterr().out


def test_a_link_to_an_error_book_section_that_does_not_exist_is_refused(tmp_path, monkeypatch) -> None:
    """``#<doc_anchor>`` is the one link form: the docs site gives the heading that id and the
    named anchor gives it on GitHub. The heading's own GitHub id (underscores kept) does not land
    on the site, so a link spelled that way is refused like a misspelled one."""
    import tools.check_error_reference as checker

    (tmp_path / "reference").mkdir()
    (tmp_path / "ERROR_BOOK.md").write_text(
        '# Error Book\n\n| [`wallet_pin_invalid`](#wallet-pin-invalid) | PIN invalid |\n\n'
        '<a name="wallet-pin-invalid"></a>\n\n### wallet_pin_invalid\n\nSee [the next one](#wallet-gone).\n',
        encoding="utf-8",
    )
    page = tmp_path / "reference" / "errors.md"
    page.write_text(
        "[lands](../ERROR_BOOK.md#wallet-pin-invalid) [github only](../ERROR_BOOK.md#wallet_pin_invalid) "
        "[misspelled](../ERROR_BOOK.md#wallet-pin-invalide) [another page](other.md#anything) "
        "[the web](https://example.com/ERROR_BOOK.md#nowhere)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "DOCS", tmp_path)

    assert checker.error_book_anchors((tmp_path / "ERROR_BOOK.md").read_text(encoding="utf-8")) == {
        "error-book", "wallet-pin-invalid",
    }
    problems: list[str] = []
    checker.check_error_book_fragments(problems, [page])
    assert problems == [
        "links to Error Book sections that do not exist: ["
        "'ERROR_BOOK.md -> #wallet-gone', "
        "'reference/errors.md -> ../ERROR_BOOK.md#wallet_pin_invalid', "
        "'reference/errors.md -> ../ERROR_BOOK.md#wallet-pin-invalide']"
    ]


def test_a_code_link_must_land_on_its_own_section_with_the_catalog_title(tmp_path, monkeypatch) -> None:
    """A hand-written table that labels a link with one code but points at another, keeps a title
    the catalog has since changed, or names a code that does not exist, tells the reader one
    thing and opens another."""
    import tools.check_error_reference as checker
    from core.faults.catalog import get_spec

    (tmp_path / "reference").mkdir()
    book = "".join(
        f'<a name="{anchor}"></a>\n\n### {code}\n\n'
        for code, anchor in (("wallet_pin_invalid", "wallet-pin-invalid"), ("timeout", "timeout"))
    )
    (tmp_path / "ERROR_BOOK.md").write_text(book, encoding="utf-8")
    page = tmp_path / "reference" / "errors.md"
    page.write_text(
        "| Code | Title |\n| --- | --- |\n"
        f"| [`timeout`](../ERROR_BOOK.md#timeout) | {get_spec('timeout').title} |\n"
        "| [`wallet_pin_invalid`](../ERROR_BOOK.md#timeout) | PIN invalid |\n"
        "| [`wallet_pin_invalid`](../ERROR_BOOK.md#wallet-pin-invalid) | Wrong PIN |\n"
        "| [`wallet_pin_wrong`](../ERROR_BOOK.md#wallet-pin-invalid) | PIN invalid |\n"
        "Prose may name [`timeout`](../ERROR_BOOK.md#timeout) without a title.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "DOCS", tmp_path)

    problems: list[str] = []
    checker.check_error_book_fragments(problems, [page])
    assert problems == [
        "code links that disagree with the catalog: ["
        "'reference/errors.md: `wallet_pin_invalid` links to #timeout, not #wallet-pin-invalid', "
        f"\"reference/errors.md: `wallet_pin_invalid` is titled 'Wrong PIN', the catalog says {get_spec('wallet_pin_invalid').title!r}\", "
        "'reference/errors.md: `wallet_pin_wrong` is not a declared code']"
    ]
