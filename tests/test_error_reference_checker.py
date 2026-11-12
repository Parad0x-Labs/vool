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
        '# Error Book\n\n| [`wallet_locked`](#wallet-locked) | Wallet locked |\n\n'
        '<a name="wallet-locked"></a>\n\n### wallet_locked\n\nSee [the next one](#wallet-gone).\n',
        encoding="utf-8",
    )
    page = tmp_path / "reference" / "errors.md"
    page.write_text(
        "[lands](../ERROR_BOOK.md#wallet-locked) [github only](../ERROR_BOOK.md#wallet_locked) "
        "[misspelled](../ERROR_BOOK.md#wallet-lockd) [another page](other.md#anything) "
        "[the web](https://example.com/ERROR_BOOK.md#nowhere)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "DOCS", tmp_path)

    assert checker.error_book_anchors((tmp_path / "ERROR_BOOK.md").read_text(encoding="utf-8")) == {
        "error-book", "wallet-locked",
    }
    problems: list[str] = []
    checker.check_error_book_fragments(problems, [page])
    assert problems == [
        "links to Error Book sections that do not exist: ["
        "'ERROR_BOOK.md -> #wallet-gone', "
        "'reference/errors.md -> ../ERROR_BOOK.md#wallet_locked', "
        "'reference/errors.md -> ../ERROR_BOOK.md#wallet-lockd']"
    ]
