"""The chained command-line grammar (core.command_line).

A chained line runs through `/bin/sh -c`, so what the shell sees must be exactly the commands that
were checked one by one. These pin the two halves of that: everything outside the grammar refuses
the whole line, and the script handed to the shell is rebuilt from quoted words so nothing in a
word is expanded.
"""

from __future__ import annotations

import shlex
import subprocess

import pytest

from core.command_line import CommandLineRefused, parse_command_line


@pytest.mark.parametrize(
    "line",
    ["pytest -q", "grep 'a|b' notes.txt", "python3 -c 'import x; x.y()'", "echo hi > out.txt", "ls"],
)
def test_a_line_that_chains_nothing_is_left_to_the_single_command_path(line: str) -> None:
    assert parse_command_line(line) is None


def test_the_accepted_forms_split_into_their_commands() -> None:
    chain = parse_command_line("python3 -m pytest -q 2>&1 | tail -5")
    assert chain is not None
    assert chain.segment_texts() == ("python3 -m pytest -q", "tail -5")
    assert chain.operators == ("|",)
    assert chain.segments[0].stderr_to_stdout and not chain.segments[1].stderr_to_stdout

    chain = parse_command_line("cd app && npm test")
    assert chain is not None and chain.cd == "app" and chain.segment_texts() == ("npm test",)

    chain = parse_command_line('echo "a;b" ; ls || true && wc -l notes.txt')
    assert chain is not None
    assert [segment.argv for segment in chain.segments] == [("echo", "a;b"), ("ls",), ("true",), ("wc", "-l", "notes.txt")]
    assert chain.operators == (";", "||", "&&")


@pytest.mark.parametrize(
    ("line", "named"),
    [
        ("ls > out && pwd", "`>`"),
        ("cat < in | wc", "`<`"),
        ("sleep 1 & ls && pwd", "`&`"),
        ("echo $HOME | cat", "`$`"),
        ("echo '$HOME' && ls", "`$`"),
        ('echo "$(id)" && ls', "`$`"),
        ("echo `id` && ls", "backtick"),
        ("ls && (pwd)", "`(`"),
        ("ls && { pwd; }", "`{`"),
        ("ls\n| wc", "line break"),
        ("echo a\\;b && ls", "backslash"),
        ("ls # note && pwd", "`#`"),
        ("x 12>&1 | y", "`>`"),
    ],
)
def test_syntax_beyond_the_grammar_refuses_the_whole_line(line: str, named: str) -> None:
    with pytest.raises(CommandLineRefused) as refused:
        parse_command_line(line)
    assert named in str(refused.value)


@pytest.mark.parametrize(
    "line",
    ["ls &&", "&& ls", "ls ;", "ls ||| pwd", "a 2>&1 b | c", "2>&1 | ls", "cd .. ; ls", "cd a b && ls",
     "cd a 2>&1 && ls", "ls && cd a && pwd", "cd a && cd b && ls"],
)
def test_malformed_chains_and_misplaced_cd_are_refused(line: str) -> None:
    with pytest.raises(CommandLineRefused):
        parse_command_line(line)


def test_the_shell_script_quotes_every_word_so_nothing_is_expanded(tmp_path) -> None:
    (tmp_path / "a.py").write_text("")
    chain = parse_command_line("ls *.py | wc -l && echo 'a b'   c")
    assert chain is not None
    script = chain.shell_script()
    assert script == "ls '*.py' | wc -l && echo 'a b' c"
    # The shell runs exactly those words: the glob stays a literal name, so `ls` reports it missing.
    result = subprocess.run(["/bin/sh", "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert "a.py" not in result.stdout
    assert result.stdout.strip().endswith("a b c")
    # Re-splitting the script gives back the parsed words and operators, and nothing else.
    assert shlex.split(script) == ["ls", "*.py", "|", "wc", "-l", "&&", "echo", "a b", "c"]
