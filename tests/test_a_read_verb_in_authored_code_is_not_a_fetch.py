"""A read verb inside code the user asked you to WRITE does not authorize a fetch.

"Write a bash script that uses `curl` to fetch `http://example.com`" has `write` as its imperative
and `a bash script` as its object. The `fetch` sits in a relative clause describing the artifact to
produce, so the address is a literal IN the code, not a destination for this turn.

The fetch lane matched on the verb alone, with no test of who owned it.

This was found as the direct consequence of a previous fix rather than by review. Declassifying
output-only codegen as a build (28606bc7) stopped the builder scaffolding a directory for that
prompt -- and this lane claimed the freed turn instead and fetched example.com, 558 characters of
HTML, `model_ran=False`. The failure mode moved from "writes junk to disk" to "makes an unrequested
network request"; the shared cause is a fast path reading a verb without asking whose it is.

The prohibition gate from `test_untrusted_content_cannot_authorize_a_fetch` cannot cover this: a
codegen request has no reason to say "do not fetch", so there is no negative clause to find.

Scope is a CODE-ARTIFACT object. "write a summary of <url>" and "generate a report from <url>" name
prose, genuinely need the page, and must keep reaching the lane.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_web import url_read_request

# Authoring requests. The URL is a literal in the artifact being written.
AUTHORED_CODE = {
    "bash_curl_pipe": (
        "Write a bash script that uses `curl` to fetch `http://example.com` and pipes it to "
        '`grep "title"`. Output ONLY valid bash code. Do NOT wrap it in markdown code fences. '
        "Start directly with `curl`."
    ),
    "python_download": "Write a python function that downloads https://example.com/data.json and parses it",
    "one_liner": "generate a one-liner that curls https://example.com",
    "makefile": "create a Makefile target that fetches https://example.com/assets.tar.gz",
    "script_to_read": "write me a script to read https://example.com and print the title",
}

# Genuine "go and read this page" asks. The lane exists for these.
GENUINE_READS = {
    "bare_fetch": "fetch https://example.com and tell me what it says",
    "rfc": "what does https://www.rfc-editor.org/rfc/rfc7231 say about the GET method?",
    "httpbin": "can you open https://httpbin.org/json and show me the content?",
    "summarise": "summarise https://example.com/report for me",
    "read_title": "read https://example.com and tell me the title",
    # Prose object, not a code artifact: must NOT be caught by the authoring test.
    "write_a_summary": "write a summary of https://example.com",
}


@pytest.mark.parametrize("name", sorted(AUTHORED_CODE))
def test_a_url_inside_code_being_authored_is_not_fetched(name: str) -> None:
    assert url_read_request(AUTHORED_CODE[name]) is None, (
        f"{name}: the runtime would fetch an address that is only a literal in the requested code"
    )


@pytest.mark.parametrize("name", sorted(GENUINE_READS))
def test_a_genuine_read_request_still_reaches_the_lane(name: str) -> None:
    request = url_read_request(GENUINE_READS[name])
    assert request is not None, f"{name}: a real 'read this page' ask stopped reaching the lane"
    assert request["url"].startswith("http")


# The cases this fix is actually load-bearing for: their read verb is one the lane recognises
# (` fetch `, ` read `), so only the ownership test stands between them and an outbound request.
#
# The other three are held by a gate that already existed -- `python_download`, `one_liner` and
# `makefile` match no read marker at all ("downloads", "curls", "fetches" are not ` fetch `). They
# belong in the must-not-fetch set above, but claiming the sabotage reclaims them would credit this
# change with blocking requests it never had to block. An earlier draft asserted all five and the
# sabotage caught it -- the third time that discipline has caught me overclaiming in this session.
# `bash_curl_pipe` was in this set until the shown-content gate landed. It writes its address as
# `` `http://example.com` ``, so `_address_is_shown_not_asked_for` now refuses it independently and
# removing ONLY the ownership test no longer reproduces its fetch. It is double-covered, not
# uncovered -- `test_the_shown_content_gate_is_what_now_holds_bash_curl_pipe` below pins that, so
# the shrinking of this set stays a recorded fact rather than a quietly relaxed assertion.
LOAD_BEARING = {"script_to_read"}


def test_removing_the_ownership_test_reproduces_the_fetch() -> None:
    """Anti-vacuity: disable the authoring test and require the affected prompts to fetch again."""
    import re

    from core.agent_runtime import fast_paths_web as mod

    original = mod._CODE_AUTHORING_RE
    # A pattern that cannot match, so ONLY the ownership test is removed and every other gate on the
    # path (the prohibition gate, the read markers, the download markers) stays exactly as it is.
    mod._CODE_AUTHORING_RE = re.compile(r"(?!x)x")
    try:
        refetched = {name for name, p in AUTHORED_CODE.items() if url_read_request(p) is not None}
    finally:
        mod._CODE_AUTHORING_RE = original

    assert refetched == LOAD_BEARING, (
        "SABOTAGE DID NOT BITE as measured: without the ownership test exactly the prompts whose "
        f"read verb the lane recognises must fetch again. Expected {sorted(LOAD_BEARING)}, got "
        f"{sorted(refetched)}"
    )
    # Gate restored.
    assert url_read_request(AUTHORED_CODE["bash_curl_pipe"]) is None


def test_the_shown_content_gate_is_what_now_holds_bash_curl_pipe() -> None:
    """`bash_curl_pipe` left LOAD_BEARING because a SECOND gate took it, not because it slipped.

    Disable both gates and it must fetch again. Disable either one alone and it must not. Without
    this, the shrunken LOAD_BEARING set above is indistinguishable from an assertion relaxed to make
    a suite go green.
    """
    import re

    from core.agent_runtime import fast_paths_web as mod

    prompt = AUTHORED_CODE["bash_curl_pipe"]
    never = re.compile(r"(?!x)x")
    real_authoring, real_shown = mod._CODE_AUTHORING_RE, mod._address_is_shown_not_asked_for
    try:
        mod._CODE_AUTHORING_RE = never
        assert url_read_request(prompt) is None, "the shown-content gate alone must still hold it"

        mod._CODE_AUTHORING_RE = real_authoring
        mod._address_is_shown_not_asked_for = lambda *_a, **_k: False  # type: ignore[assignment]
        assert url_read_request(prompt) is None, "the ownership test alone must still hold it"

        mod._CODE_AUTHORING_RE = never
        assert url_read_request(prompt) is not None, (
            "SABOTAGE DID NOT BITE: with BOTH gates disabled this prompt must fetch again, or "
            "neither gate is what holds it and both tests above are vacuous"
        )
    finally:
        mod._CODE_AUTHORING_RE = real_authoring
        mod._address_is_shown_not_asked_for = real_shown  # type: ignore[assignment]
