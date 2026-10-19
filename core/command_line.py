"""The one grammar for a sandbox command line that chains commands.

A chat turn can ask for `python3 -m pytest -q 2>&1 | tail -5` or `cd app && npm test`. Each part of
such a line must pass the same checks a single command passes (the whitelist, the hard markers, the
permission classifier), so the line is split here into its commands and nothing else. The grammar
is deliberately small: words, the operators `|`, `&&`, `||` and `;`, the exact redirection `2>&1`
at the end of a command, and one leading `cd <dir> &&`. Anything a shell would read as more than
that (other redirections, `&`, `$`, backticks, parentheses, braces, newlines, a backslash escape)
refuses the whole line.

The line that finally runs is rebuilt from the parsed words, each quoted with `shlex.quote`, so the
shell only ever sees the operators accepted here: no word is expanded, globbed or split again.

A line with none of the four operators and no `2>&1` is not a chain, and `parse_command_line`
returns None for it: the single-command path (argv, no shell) handles it exactly as before.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

CHAIN_OPERATORS = ("&&", "||", "|", ";")
STDERR_TO_STDOUT = "2>&1"

# Characters that start shell syntax beyond this grammar when they appear outside quotes.
_UNQUOTED_REFUSED = {
    ">": "an output redirection (`>`)",
    "<": "an input redirection (`<`)",
    "(": "a subshell or grouping (`(`)",
    ")": "a subshell or grouping (`)`)",
    "{": "a brace group (`{`)",
    "}": "a brace group (`}`)",
    "\\": "a backslash escape",
}


class CommandLineRefused(ValueError):
    """The line chains commands but uses shell syntax this grammar does not accept."""


@dataclass(frozen=True)
class CommandSegment:
    argv: tuple[str, ...]
    stderr_to_stdout: bool = False


@dataclass(frozen=True)
class CommandLine:
    segments: tuple[CommandSegment, ...]
    #: The operator after each segment but the last (len == len(segments) - 1).
    operators: tuple[str, ...]
    #: The directory of a leading `cd <dir> &&`, as written; the caller checks it stays in the workspace.
    cd: str | None = None

    def segment_texts(self) -> tuple[str, ...]:
        return tuple(shlex.join(segment.argv) for segment in self.segments)

    def shell_script(self) -> str:
        """The text handed to `/bin/sh -c`: every word quoted, only the accepted operators bare."""
        parts: list[str] = []
        if self.cd is not None:
            parts += ["cd", shlex.quote(self.cd), "&&"]
        for index, segment in enumerate(self.segments):
            parts.append(shlex.join(segment.argv))
            if segment.stderr_to_stdout:
                parts.append(STDERR_TO_STDOUT)
            if index < len(self.operators):
                parts.append(self.operators[index])
        return " ".join(parts)


@dataclass(frozen=True)
class _Token:
    text: str
    kind: str  # "word", "op", "redirect"


def _tokens(text: str) -> tuple[list[_Token], list[str]]:
    """Split `text` into words and operators. Returns the tokens and the hazards seen.

    A hazard is syntax this grammar refuses inside a chain. It is collected rather than raised so a
    line that turns out not to be a chain keeps today's single-command handling.
    """
    tokens: list[_Token] = []
    hazards: list[str] = []
    word: list[str] = []
    in_word = False
    index = 0
    length = len(text)

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            tokens.append(_Token("".join(word), "word"))
            word.clear()
            in_word = False

    while index < length:
        char = text[index]
        if char in "\n\r":
            hazards.append("a line break")
            index += 1
            continue
        if char in "$`":
            hazards.append("a variable or command substitution (`$` or a backtick)")
        if char == "'":
            closing = text.find("'", index + 1)
            if closing < 0:
                hazards.append("an unterminated quote")
                break
            quoted = text[index + 1:closing]
            if "$" in quoted or "`" in quoted:
                hazards.append("a variable or command substitution (`$` or a backtick)")
            if "\n" in quoted or "\r" in quoted:
                hazards.append("a line break")
            word.append(quoted)
            in_word = True
            index = closing + 1
            continue
        if char == '"':
            index += 1
            closed = False
            while index < length:
                inner = text[index]
                if inner == '"':
                    closed = True
                    index += 1
                    break
                if inner in "$`":
                    hazards.append("a variable or command substitution (`$` or a backtick)")
                if inner in "\n\r":
                    hazards.append("a line break")
                if inner == "\\":
                    hazards.append("a backslash escape")
                word.append(inner)
                index += 1
            if not closed:
                hazards.append("an unterminated quote")
                break
            in_word = True
            continue
        if char.isspace():
            end_word()
            index += 1
            continue
        if char in "|&;":
            pair = text[index:index + 2]
            if pair in {"&&", "||"}:
                end_word()
                tokens.append(_Token(pair, "op"))
                index += 2
                continue
            if char == "&":
                hazards.append("a background job (`&`)")
                end_word()
                index += 1
                continue
            end_word()
            tokens.append(_Token(char, "op"))
            index += 1
            continue
        if char == "2" and not in_word and text.startswith(STDERR_TO_STDOUT, index):
            after = text[index + len(STDERR_TO_STDOUT):index + len(STDERR_TO_STDOUT) + 1]
            if after == "" or after.isspace() or after in "|&;":
                tokens.append(_Token(STDERR_TO_STDOUT, "redirect"))
                index += len(STDERR_TO_STDOUT)
                continue
        if char in _UNQUOTED_REFUSED:
            hazards.append(_UNQUOTED_REFUSED[char])
        if char == "#" and not in_word:
            hazards.append("a comment (`#`)")
        word.append(char)
        in_word = True
        index += 1
    end_word()
    return tokens, hazards


def parse_command_line(text: str) -> CommandLine | None:
    """Parse a chained command line, or return None when `text` chains nothing.

    Raises `CommandLineRefused` for a chain that uses syntax outside the grammar, naming what was
    refused. Never returns a partial parse.
    """
    tokens, hazards = _tokens(str(text or ""))
    if not any(token.kind in {"op", "redirect"} for token in tokens):
        return None
    if hazards:
        raise CommandLineRefused(
            f"This command line chains commands and also uses {hazards[0]}, which is not allowed "
            "in a chained line. Chain plain commands with |, &&, || or ; (and 2>&1), or run them "
            "one at a time."
        )
    segments: list[CommandSegment] = []
    operators: list[str] = []
    current: list[str] = []
    redirected = False
    for token in tokens:
        if token.kind == "word":
            if redirected:
                raise CommandLineRefused("`2>&1` must come at the end of a command, before | or && or the end.")
            current.append(token.text)
            continue
        if token.kind == "redirect":
            if not current or redirected:
                raise CommandLineRefused("`2>&1` must follow a command.")
            redirected = True
            continue
        if not current:
            raise CommandLineRefused(f"`{token.text}` must sit between two commands.")
        segments.append(CommandSegment(tuple(current), redirected))
        operators.append(token.text)
        current, redirected = [], False
    if not current:
        raise CommandLineRefused("A command line cannot end with an operator.")
    segments.append(CommandSegment(tuple(current), redirected))

    cd: str | None = None
    first = segments[0].argv
    if first and first[0] == "cd":
        if len(first) != 2 or segments[0].stderr_to_stdout or len(segments) < 2 or operators[0] != "&&":
            raise CommandLineRefused("A leading `cd` takes exactly one folder and must be followed by `&&`.")
        cd = first[1]
        segments, operators = segments[1:], operators[1:]
    for segment in segments:
        if segment.argv[0] == "cd":
            raise CommandLineRefused("`cd` is allowed only once, at the start of the line, followed by `&&`.")
    return CommandLine(segments=tuple(segments), operators=tuple(operators), cd=cd)


__all__ = [
    "CHAIN_OPERATORS",
    "STDERR_TO_STDOUT",
    "CommandLine",
    "CommandLineRefused",
    "CommandSegment",
    "parse_command_line",
]
