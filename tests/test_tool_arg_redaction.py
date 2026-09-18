"""Guard: tool-call arguments shown in the Activity timeline are secret-safe and useful.

The Activity panel shows WHAT a tool ran with (path, query) so a trace is legible, but arguments can
carry a key, a password, or file contents. `redact_tool_arguments` must mask secrets, summarize bulky
values, keep ordinary args readable, and never raise — it feeds a display string, never re-execution.
"""

from __future__ import annotations

from core.tool_arg_redaction import redact_tool_arguments


def test_ordinary_args_render_readably() -> None:
    assert redact_tool_arguments({"path": "README.md", "max_lines": 60}) == "path=README.md, max_lines=60"


def test_secret_named_keys_are_masked() -> None:
    for key in ("api_key", "password", "token", "authorization", "client_secret", "private_key", "mnemonic"):
        out = redact_tool_arguments({key: "sk-or-v1-SUPERSECRETVALUE1234567890", "path": "x"})
        assert "SUPERSECRET" not in out, (key, out)
        assert "[redacted]" in out, (key, out)
        assert "path=x" in out


def test_secret_value_is_masked_even_under_an_innocuous_key() -> None:
    # A key that slips a real secret into a plain field is still caught by the final redact_secrets pass.
    out = redact_tool_arguments({"note": "Bearer sk-ant-LEAK1234567890abcdef", "q": "hi"})
    assert "LEAK" not in out
    assert "q=hi" in out


def test_false_positive_guard_keeps_keyword_and_keys_readable() -> None:
    # "keyword"/"keys" merely contain "key" -> must NOT be redacted.
    out = redact_tool_arguments({"keyword": "solana", "keys": 3})
    assert out == "keyword=solana, keys=3"


def test_bulky_values_become_a_length_not_an_inline_dump() -> None:
    out = redact_tool_arguments({"content": "a" * 500, "path": "big.txt"})
    assert "content=(500 chars)" in out
    assert "aaaa" not in out


def test_long_values_truncate() -> None:
    out = redact_tool_arguments({"query": "x" * 300})
    assert out.endswith("…")
    assert len(out) < 200


def test_never_raises_on_bad_input() -> None:
    assert redact_tool_arguments(None) == ""
    assert redact_tool_arguments("not a dict") == ""
    assert redact_tool_arguments([1, 2, 3]) == ""
    assert redact_tool_arguments({}) == ""


def test_internal_plumbing_keys_are_dropped() -> None:
    out = redact_tool_arguments({"path": "a.txt", "idempotency_key": "abc", "source_context": {"x": 1}, "_private": 1})
    assert out == "path=a.txt"
