from __future__ import annotations

import pytest

from core.demo_source import (
    brief_from_github,
    brief_from_url,
    extract_brief_from_readme,
    fetch_readme,
    parse_github_url,
)

_HTML_DOCS = """<html><head><title>Widget Docs</title></head><body>
<h1>Widget</h1>
<p>The fastest widget for your workflow.</p>
<h2>Features</h2>
<ul><li>Instant sync - your data everywhere</li><li>Offline mode - works with no network</li></ul>
<h2>Installation</h2><p>pip install widget</p>
</body></html>"""

_README_FEATURES = """# VOOL

An honest local AI agent that owns its own keys. [docs](https://x.io)

![badge](https://img.shields.io/x)

## Features

- **Local-first** - runs models on your own machine, no cloud
- **Honest receipts**: signed, offline-verifiable proofs
- Own your wallet - a self-custody Solana wallet

## Installation

pip install vool
"""

_README_H2 = """# CoolTool

Does cool things for everyone.

## Fast

It is very fast indeed.

## Secure

End-to-end encrypted by default.

## Installation

pip install cooltool
"""


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://github.com/Parad0x-Labs/vool", ("Parad0x-Labs", "vool")),
        ("github.com/o/r/tree/main", ("o", "r")),
        ("git@github.com:o/r.git", ("o", "r")),
        ("o/r", ("o", "r")),
        ("https://example.com/x", None),
        ("", None),
    ],
)
def test_parse_github_url(url, expected) -> None:
    assert parse_github_url(url) == expected


def test_extract_brief_from_features_section() -> None:
    brief = extract_brief_from_readme(_README_FEATURES, repo_name="vool-local")
    assert brief.product_name == "VOOL"
    assert "honest local AI agent" in brief.tagline
    names = [f.name for f in brief.features]
    assert names == ["Local-first", "Honest receipts", "Own your wallet"]
    assert brief.features[0].blurb == "runs models on your own machine, no cloud"
    assert brief.features[1].blurb == "signed, offline-verifiable proofs"


def test_extract_brief_falls_back_to_h2_headings() -> None:
    brief = extract_brief_from_readme(_README_H2, repo_name="cooltool")
    assert brief.product_name == "CoolTool"
    names = [f.name for f in brief.features]
    assert names == ["Fast", "Secure"]           # Installation excluded as scaffolding
    assert brief.features[0].blurb == "It is very fast indeed."


def test_product_name_falls_back_to_repo_name() -> None:
    brief = extract_brief_from_readme("no heading here, just prose about things", repo_name="my-cool_repo")
    assert brief.product_name == "My Cool Repo"


def test_fetch_readme_uses_injected_fetcher() -> None:
    seen = {}

    def fetcher(url: str) -> str:
        seen["url"] = url
        return "# X\n\nhello"

    out = fetch_readme("owner", "repo", fetcher=fetcher)
    assert out == "# X\n\nhello"
    assert seen["url"] == "https://api.github.com/repos/owner/repo/readme"


def test_brief_from_github_end_to_end() -> None:
    brief = brief_from_github(
        "https://github.com/Parad0x-Labs/vool",
        fetcher=lambda _u: _README_FEATURES,
        presenter="woman",
        total_seconds=25,
    )
    assert brief is not None
    assert brief.product_name == "VOOL"
    assert brief.presenter == "woman" and brief.total_seconds == 25
    assert len(brief.features) == 3


def test_brief_from_github_handles_bad_url_and_fetch_failure() -> None:
    assert brief_from_github("not a github url") is None

    def boom(_u: str) -> str:
        raise RuntimeError("network down")

    assert brief_from_github("github.com/o/r", fetcher=boom) is None


def test_brief_from_url_reads_a_docs_page() -> None:
    brief = brief_from_url("https://widget.dev/docs", fetcher=lambda _u: _HTML_DOCS)
    assert brief is not None
    assert brief.product_name == "Widget"
    assert "fastest widget" in brief.tagline
    assert [f.name for f in brief.features] == ["Instant sync", "Offline mode"]


def test_brief_from_url_routes_github_to_readme() -> None:
    brief = brief_from_url("github.com/o/r", fetcher=lambda _u: _README_FEATURES)
    assert brief is not None and brief.product_name == "VOOL"


def test_brief_from_url_none_on_empty_or_failure() -> None:
    assert brief_from_url("https://x.dev", fetcher=lambda _u: "") is None

    def boom(_u: str) -> str:
        raise RuntimeError("down")

    assert brief_from_url("https://x.dev", fetcher=boom) is None


# --- regressions from the adversarial review ---

def test_tagline_skips_linked_badges() -> None:
    readme = ("# CoolProduct\n"
              "[![CI](https://img.shields.io/ci.svg)](https://ci.example.com/build)\n\n"
              "The real one-line description of the product.\n")
    brief = extract_brief_from_readme(readme, repo_name="cool")
    assert brief.tagline == "The real one-line description of the product."


def test_closed_script_dropped_unclosed_does_not_leak() -> None:
    closed = "<html><body><h1>Title</h1><script>var s='x'; doEvil();</script><p>Real paragraph here now.</p></body></html>"
    brief = brief_from_url("https://x.dev", fetcher=lambda _u: closed)
    assert brief is not None and "doEvil" not in brief.tagline
    assert "Real paragraph here now." in brief.tagline
    unclosed = "<h1>Title</h1><script>var s='x'; doEvil();"   # no </script>
    b2 = brief_from_url("https://x.dev", fetcher=lambda _u: unclosed)
    assert b2 is None or "doEvil" not in (b2.tagline or "")


def test_nested_bullets_not_flattened_into_features() -> None:
    readme = "# P\n\ndesc\n\n## Features\n- Top one\n  - nested a\n  - nested b\n- Top two\n"
    brief = extract_brief_from_readme(readme, repo_name="p")
    assert [f.name for f in brief.features] == ["Top one", "Top two"]


def test_separatorless_bullet_kept_whole() -> None:
    from core.demo_source import _split_bullet
    f = _split_bullet("- Runs entirely on your local machine with no cloud dependency whatsoever")
    assert f.blurb == "" and f.name.startswith("Runs entirely")


def test_colon_bullet_splits_before_a_later_dash() -> None:
    from core.demo_source import _split_bullet
    f = _split_bullet("- Auth: sign-in - and sign-out")
    assert f.name == "Auth" and "sign-in" in f.blurb


def test_lookalike_and_bad_github_hosts_rejected() -> None:
    assert parse_github_url("https://fakegithub.com/evil/repo") is None
    assert parse_github_url("https://mygithub.com/o/r") is None
    assert parse_github_url("https://www.github.com/o/r") == ("o", "r")
    assert parse_github_url("https://github.com/owner/.git") is None


def test_h3_subheadings_read_as_features() -> None:
    readme = ("# Widget\n\nA great widget for teams.\n\n"
              "## What makes Widget different\n"
              "### Fast rendering - GPU accelerated\n"
              "It draws in a millisecond.\n"
              "### Offline mode\n"
              "Works with no network.\n"
              "## Installation\n- pip install widget\n")
    brief = extract_brief_from_readme(readme, repo_name="widget")
    assert [f.name for f in brief.features] == ["Fast rendering", "Offline mode"]
    assert brief.features[0].blurb == "GPU accelerated"


def test_emoji_prefixed_meta_heading_still_excluded() -> None:
    readme = "# X\n\na short description here\n\n## ⚡ Installation\n- do this\n\n## Cool Stuff\nreally cool.\n"
    brief = extract_brief_from_readme(readme, repo_name="x")
    names = [f.name for f in brief.features]
    assert "Cool Stuff" in names and not any("nstall" in n for n in names)


def test_bounded_get_caps_response_size(monkeypatch) -> None:
    import io

    import core.demo_source as ds
    import core.remote_fetch_policy as policy

    class _FakeResp:
        def __init__(self):
            self.headers = {"Content-Type": "text/plain; charset=utf-8"}
            self._stream = io.BytesIO(b"x" * 20_000_000)   # 20 MB total if uncapped

        def read(self, n=-1):
            return self._stream.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(policy, "open_remote_url", lambda url, **k: _FakeResp())
    out = ds._bounded_get("http://x", {})
    assert len(out) <= ds._MAX_FETCH_BYTES
