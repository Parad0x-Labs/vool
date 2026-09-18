"""ICU-lite message syntax parser/formatter.

Supported syntax (documented contract for the whole registry):
    {name}                                  variable substitution
    {count, plural, one {...} other {...}}  CLDR cardinal select; '#' inside a
                                            branch renders the formatted count;
                                            branches nest arbitrarily.

Anything else is literal text. Unknown keywords inside a placeholder fall back
to plain substitution so partial ICU strings never crash a render.
"""
from __future__ import annotations

from typing import Any, Callable

FormatNumber = Callable[[Any], str]


class MessageError(ValueError):
    pass


def tokenize_braces(text: str, start: int = 0) -> tuple[str, int]:
    """Read a balanced ``{...}`` block starting at ``start`` (must point at '{').

    Returns (inner_content, index_after_closing_brace).
    """
    if start >= len(text) or text[start] != "{":
        raise MessageError("expected '{' at position %d" % start)
    depth = 0
    quote = ""
    i = start
    while i < len(text):
        c = text[i]
        if quote:
            if c == "'":
                # ICU doubles quotes to escape a literal quote
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 1
                else:
                    quote = ""
            elif c == "\\" and i + 1 < len(text):
                i += 1
        elif c == "'":
            quote = "'"
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    raise MessageError("unbalanced braces in message: %r" % text[:80])


def parse_placeholder(inner: str) -> tuple[str, str | None, str | None]:
    """Split ``name``, ``name,type`` or ``name,type,branches``."""
    parts = _split_top_level(inner, ",")
    name = parts[0].strip()
    arg_type = parts[1].strip() if len(parts) > 1 else None
    rest = parts[2].strip() if len(parts) > 2 else None
    return name, arg_type, rest


def parse_plural_branches(spec: str) -> list[tuple[str, str]]:
    """Parse ``one {...} other {...}`` into [(category, template)]."""
    branches: list[tuple[str, str]] = []
    i = 0
    n = len(spec)
    while i < n:
        while i < n and spec[i].isspace():
            i += 1
        if i >= n:
            break
        j = i
        while j < n and (spec[j].isalnum() or spec[j] == "="):
            j += 1
        keyword = spec[i:j]
        while j < n and spec[j].isspace():
            j += 1
        if j >= n or spec[j] != "{":
            raise MessageError("plural branch %r must be followed by {...}" % keyword)
        inner, nxt = tokenize_braces(spec, j)
        branches.append((keyword, inner))
        i = nxt
    return branches


def _split_top_level(s: str, sep: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in s:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts


def format_message(
    template: str,
    args: dict[str, Any],
    *,
    plural_category: Callable[[Any], str],
    format_number: FormatNumber,
) -> str:
    """Render a template against ``args``.

    ``plural_category(value)`` -> 'zero'|'one'|'two'|'few'|'many'|'other'
    ``format_number(value)``   -> locale-formatted string (used for '#' and bare vars)
    """
    out: list[str] = []
    i = 0
    n = len(template)
    while i < n:
        c = template[i]
        if c == "{":
            inner, i = tokenize_braces(template, i)
            name, arg_type, rest = parse_placeholder(inner)
            value = args.get(name)
            shown = "" if value is None else str(value)
            if arg_type == "plural":
                out.append(_render_plural(rest or "", value, args,
                                          plural_category=plural_category,
                                          format_number=format_number))
            else:
                if isinstance(value, float) and value.is_integer():
                    shown = format_number(int(value))
                elif isinstance(value, (int, float)):
                    shown = format_number(value)
                out.append(shown)
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _render_plural(
    spec: str,
    value: Any,
    args: dict[str, Any],
    *,
    plural_category: Callable[[Any], str],
    format_number: FormatNumber,
) -> str:
    try:
        num = float(str(value).replace(",", "."))
    except ValueError:
        raise MessageError("plural argument is not numeric: %r" % (value,))
    exact = int(num) if num.is_integer() else None
    category = None
    for keyword, branch in parse_plural_branches(spec):
        if keyword.startswith("=") and exact is not None and keyword[1:] == str(exact):
            category = keyword
            break
    if category is None:
        category = plural_category(num)
    chosen = dict(parse_plural_branches(spec)).get(category, "")
    # '#' renders the locale-formatted count inside the branch
    return format_message(
        chosen.replace("#", "\x00"),
        args,
        plural_category=plural_category,
        format_number=format_number,
    ).replace("\x00", format_number(exact if exact is not None else num))
