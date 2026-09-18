"""A generated program is an artifact: byte-intact, or withheld whole -- never shredded.

Measured on the live daemon 2026-09-17 (an OpenRouter research turn with partly unreachable
search): the delivered Python script was adjudicated line by line as though each import,
declaration and assignment were an independently sourced factual claim. The published remnant
lost the imports, the class bodies and the URLs, and the notice listed 125 withheld and 98
uncertain per-line "statements" beside a skeleton that was no longer a runnable program.

The boundary this pins:

* fenced code lines are NOT claim territory -- program structure is generated artifact, not
  sourced prose;
* the ONE outward fact code carries that must be evidenced is an API endpoint URL, because the
  program will send bytes there -- a URL in code is an assertion, not a citation, and fencing a
  URL does not launder it;
* an artifact whose every endpoint is witnessed ships BYTE FOR BYTE; one with an unwitnessed
  endpoint is withheld WHOLE with an artifact-level notice, because a skeleton with lines
  removed is neither truthful nor usable.
"""
from __future__ import annotations

from core.claim_support import ENDPOINT_CLAIM_PREFIX, match_claims, segment_claims
from core.grounding_publication import compose_partial_truth

REQUEST = (
    "Research the current OpenRouter API/docs and build a Python script that fetches the "
    "currently available models and their pricing."
)

SCRIPT = "\n".join(
    [
        "```python",
        "import json",
        "import requests",
        "",
        "BASE_URL = \"https://openrouter.ai/api/v1/models\"",
        "",
        "def fetch_models():",
        "    response = requests.get(BASE_URL, timeout=30)",
        "    response.raise_for_status()",
        "    return response.json()[\"data\"]",
        "",
        "for model in fetch_models():",
        "    print(model[\"id\"], model.get(\"pricing\", {}))",
        "```",
    ]
)

WITNESSING_NOTES = [
    {
        "result_title": "OpenRouter models endpoint",
        "result_url": "https://openrouter.ai/docs",
        "origin_domain": "openrouter.ai",
        "summary": (
            "The catalog of currently available models with pricing is openrouter.ai/api/v1/models; "
            "no authentication is required to list models."
        ),
    },
    {
        "result_title": "OpenRouter quickstart",
        "result_url": "https://openrouter.ai/docs/quickstart",
        "origin_domain": "openrouter.ai",
        "summary": "Requests use Bearer authentication except the models listing at openrouter.ai/api/v1/models.",
    },
]

IRRELEVANT_NOTES = [
    {
        "result_title": "BGPsec - Wikipedia",
        "result_url": "https://en.wikipedia.org/wiki/BGPsec",
        "origin_domain": "en.wikipedia.org",
        "summary": "BGPsec allows receivers of UPDATE messages to verify the received AS path.",
    },
    {
        "result_title": "JSON Web Token - Wikipedia",
        "result_url": "https://en.wikipedia.org/wiki/JSON_Web_Token",
        "origin_domain": "en.wikipedia.org",
        "summary": "A JWT asserts claims between two parties as a signed token.",
    },
]


def test_code_lines_are_not_claim_territory() -> None:
    segments = segment_claims(SCRIPT)
    assert segments == [ENDPOINT_CLAIM_PREFIX + "https://openrouter.ai/api/v1/models"], segments
    # The incident's shredded "statements" are all gone as claim candidates.
    for forbidden in ("import json", "BASE_URL", "def fetch_models", "response.raise_for_status"):
        assert not any(forbidden in segment for segment in segments)


def test_a_prose_url_is_a_citation_not_an_endpoint_claim() -> None:
    segments = segment_claims("The listing is documented at https://openrouter.ai/api/v1/models.")
    assert not any(segment.startswith(ENDPOINT_CLAIM_PREFIX) for segment in segments), segments


def test_a_witnessed_artifact_publishes_byte_intact() -> None:
    answer = "No authentication is required to list models.\n\n" + SCRIPT
    claim_map = match_claims(answer=answer, notes=WITNESSING_NOTES, request_text=REQUEST)
    body, withheld, unverifiable = compose_partial_truth(answer, claim_map)
    assert SCRIPT in body, "a fully witnessed artifact must ship byte for byte"
    assert withheld == () and unverifiable == ()
    assert claim_map.coverage == "full"
    # Intact also means USABLE: the published program is syntactically valid Python.
    published_program = SCRIPT.splitlines()[1:-1]
    compile("\n".join(published_program), "<published-artifact>", "exec")


def test_an_unwitnessed_endpoint_withholds_the_artifact_whole() -> None:
    answer = "Authentication uses a Bearer key.\n\n" + SCRIPT
    # Keep the endpoint unwitnessed by pointing the note at a different path.
    partial_notes = [
        {
            "result_title": "OpenRouter docs",
            "result_url": "https://openrouter.ai/docs",
            "origin_domain": "openrouter.ai",
            "summary": "Authentication uses a Bearer key; see the quickstart for chat completions.",
        }
    ]
    claim_map = match_claims(answer=answer, notes=partial_notes, request_text=REQUEST)
    body, withheld, unverifiable = compose_partial_truth(answer, claim_map)
    assert "```" not in body and "BASE_URL" not in body, "the artifact must be gone WHOLE"
    assert not any(
        line.strip() and line in body for line in SCRIPT.splitlines() if line.strip().startswith(("import", "def", "BASE_URL"))
    ), "no line of the withheld artifact may survive"
    assert any(
        "https://openrouter.ai/api/v1/models" in entry and "withheld whole" in entry
        for entry in withheld
    ), withheld
    # The endpoint claim was adjudicated at all -- fencing did not launder it.
    endpoint_claims = [c for c in claim_map.claims if c.text.startswith(ENDPOINT_CLAIM_PREFIX)]
    assert endpoint_claims and endpoint_claims[0].status == "unsupported"
    assert claim_map.coverage == "partial"


def test_the_incident_script_against_irrelevant_sources_is_never_shredded() -> None:
    """Wrong sources, the exact incident shape: the artifact must not come back line-mangled,
    and the withheld/unverifiable lists must not enumerate program lines as 'statements'."""
    answer = "Here is the script.\n\n" + SCRIPT
    claim_map = match_claims(answer=answer, notes=IRRELEVANT_NOTES, request_text=REQUEST)
    body, withheld, unverifiable = compose_partial_truth(answer, claim_map)
    all_reported = " ".join([*withheld, *unverifiable])
    for forbidden in ("import json", "BASE_URL", "def fetch_models", "raise_for_status", "print(model"):
        assert forbidden not in all_reported, f"program line reported as a statement: {forbidden}"
    # And the artifact is not partially present either: no fence survived.
    assert "```" not in body


def test_an_artifact_with_no_outward_endpoints_ships_on_prose_support_alone() -> None:
    pure_code = "\n".join(
        [
            "```python",
            "def median(values):",
            "    ordered = sorted(values)",
            "    middle = len(ordered) // 2",
            "    return ordered[middle]",
            "```",
        ]
    )
    answer = "No authentication is required to list models.\n\n" + pure_code
    claim_map = match_claims(answer=answer, notes=WITNESSING_NOTES, request_text=REQUEST)
    body, withheld, unverifiable = compose_partial_truth(answer, claim_map)
    assert pure_code in body, "endpoint-free program structure must ship intact"
    assert withheld == () and unverifiable == ()
