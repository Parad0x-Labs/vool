"""The KAS law: an adapter translates, and VOOL keeps every authority.

These tests do not read the adapters' prose. They read their syntax trees, drive their real code
against a recorded transport, and check that the two forges -- whose wire shapes disagree about
almost everything -- hand the runtime the same typed vocabulary.
"""

from __future__ import annotations

import pytest

from core.kas.conformance import FORBIDDEN_MODULES, scan_adapters, scan_source
from core.kas.contract import ForgeAdapter, KasRequest, TransportDeniedError, TransportUnknownError
from core.kas.registry import forge_adapter, registered_adapters
from tests.repoops._forge_fixture import (
    RecordedForge,
    github_pull_request,
    gitlab_changes,
    gitlab_merge_request,
)

# --- the law itself --------------------------------------------------------------------


def test_every_shipped_adapter_holds_no_authority() -> None:
    findings = scan_adapters()
    assert not findings, "adapters holding a VOOL authority:\n" + "\n".join(str(f) for f in findings)


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("from core.mode_permission_policy import decide_tool_call\n", "permissions"),
        ("import urllib.request\n", "effects (egress)"),
        ("import requests\n", "effects (egress)"),
        ("import subprocess\n", "effects (process)"),
        ("from core.credential_store import get_credential\n", "privacy (credentials)"),
        ("from core.finalization import finalize_answer\n", "finality"),
        ("import os\n", "privacy (environment) / effects (process)"),
    ],
)
def test_the_scan_names_the_authority_an_adapter_would_duplicate(snippet: str, expected: str) -> None:
    findings = scan_source(snippet, path="probe.py")
    assert findings, f"the scan missed {snippet!r}"
    assert findings[0].authority == expected


def test_the_scan_catches_the_escape_hatches_that_would_make_the_import_list_a_lie() -> None:
    source = "def f():\n    mod = __import__('urllib.request')\n    return open('/etc/passwd')\n"
    constructs = {f.construct for f in scan_source(source, path="probe.py")}
    assert "__import__(...)" in constructs
    assert "open(...)" in constructs


def test_url_building_stays_legal_because_parse_is_not_egress() -> None:
    # An adapter must be able to build a URL. `urllib.parse` is string work; the egress half is
    # what is forbidden, and the scan has to tell them apart or authors route around it.
    assert not scan_source("from urllib.parse import quote\n", path="probe.py")
    assert scan_source("from urllib.request import urlopen\n", path="probe.py")
    assert "urllib.parse" not in FORBIDDEN_MODULES


def test_both_forges_are_registered_under_one_contract() -> None:
    adapters = registered_adapters()
    assert ("forge", "github") in adapters
    assert ("forge", "gitlab") in adapters
    for (kind, _provider), cls in adapters.items():
        if kind == "forge":
            assert issubclass(cls, ForgeAdapter)


# --- the transport is the only reach --------------------------------------------------


def test_an_adapter_that_sets_its_own_credential_header_is_refused() -> None:
    from core.kas.transport import build_transport

    send = build_transport(provider_id="github", allowed_hosts=("api.github.com",))
    request = KasRequest(
        method="GET",
        url="https://api.github.com/repos/o/r",
        purpose="probe",
        headers={"Authorization": "Bearer stolen"},
    )
    with pytest.raises(TransportDeniedError) as caught:
        send(request)
    assert caught.value.reason == "adapter_set_credential_header"


def test_a_request_to_an_unpinned_host_never_reaches_a_socket() -> None:
    from core.kas.transport import build_transport

    send = build_transport(provider_id="github", allowed_hosts=("api.github.com",))
    with pytest.raises(TransportDeniedError) as caught:
        send(KasRequest(method="GET", url="https://evil.example/repos/o/r", purpose="probe"))
    assert caught.value.reason == "host_not_pinned"


def test_a_file_url_is_not_an_egress_scheme() -> None:
    from core.kas.transport import build_transport

    send = build_transport(provider_id="github")
    with pytest.raises(TransportDeniedError) as caught:
        send(KasRequest(method="GET", url="file:///etc/passwd", purpose="probe"))
    assert caught.value.reason == "unsupported_scheme"


def test_an_unknown_credential_binding_is_refused_before_any_request() -> None:
    from core.kas.transport import build_transport

    send = build_transport(provider_id="github", allowed_hosts=("api.github.com",))
    with pytest.raises(TransportDeniedError) as caught:
        send(
            KasRequest(
                method="GET",
                url="https://api.github.com/repos/o/r",
                purpose="probe",
                auth="binding-that-does-not-exist",
            )
        )
    assert caught.value.reason == "unknown_credential_binding"


def test_the_adapter_names_a_binding_and_never_holds_a_secret() -> None:
    forge = RecordedForge().route("GET /repos/o/r", {"full_name": "o/r", "default_branch": "main"})
    adapter = forge_adapter("github", namespace="o/r", auth_binding="cb-1", transport_factory=forge.factory)
    adapter.describe_repository()
    call = forge.calls[-1]
    assert call["auth"] == "cb-1"
    assert not any(k.lower() == "authorization" for k in call["headers"])


# --- one contract, two wires ----------------------------------------------------------


def test_github_and_gitlab_produce_the_same_typed_pull_request() -> None:
    gh = RecordedForge().route("GET /pulls/7", github_pull_request("7", base_sha="a" * 40, head_sha="b" * 40))
    gl = RecordedForge().route(
        "GET /merge_requests/7", gitlab_merge_request("7", base_sha="a" * 40, head_sha="b" * 40)
    )
    github = forge_adapter("github", namespace="o/r", transport_factory=gh.factory).describe_pull_request("7")
    gitlab = forge_adapter("gitlab", namespace="g/p", transport_factory=gl.factory).describe_pull_request("7")

    for field in ("number", "state", "base_ref", "base_sha", "head_ref", "head_sha", "draft"):
        assert getattr(github, field) == getattr(gitlab, field), field
    # GitLab says "opened" on the wire; the shared vocabulary says "open". The runtime above must
    # never learn which forge it is talking to.
    assert github.state == "open"


def test_gitlab_structured_changes_become_the_same_unified_diff_shape() -> None:
    gl = RecordedForge().route("GET /merge_requests/7/changes", gitlab_changes())
    text = forge_adapter("gitlab", namespace="g/p", transport_factory=gl.factory).pull_request_diff("7")
    assert text.startswith("diff --git a/calc.py b/calc.py")
    assert "+++ b/calc.py" in text
    assert "+    return a + b" in text


def test_a_ci_mutation_is_declared_mutating_so_the_transport_can_treat_it_as_one() -> None:
    gh = RecordedForge().route("POST /actions/runs/99/rerun", {})
    forge_adapter("github", namespace="o/r", transport_factory=gh.factory).rerun_ci("99")
    assert gh.calls[-1]["mutating"] is True
    assert gh.calls[-1]["method"] == "POST"


def test_an_unproven_outcome_reaches_the_caller_as_unknown_not_as_failure() -> None:
    gh = RecordedForge().arm_unknown("/actions/runs/99/rerun")
    adapter = forge_adapter("github", namespace="o/r", transport_factory=gh.factory)
    with pytest.raises(TransportUnknownError):
        adapter.rerun_ci("99")


def test_an_unknown_provider_is_a_named_absence_not_a_crash() -> None:
    with pytest.raises(LookupError) as caught:
        forge_adapter("bitbucket", namespace="o/r")
    assert "github" in str(caught.value) and "gitlab" in str(caught.value)


def test_an_incomplete_forge_adapter_cannot_be_constructed() -> None:
    """The contract is enforced at construction, not at the first missing call.

    `ExternalAdapter` is deliberately not an ABC -- it has no abstract member of its own -- so the
    abstractness has to live on `ForgeAdapter`. If it ever moves, a half-written adapter constructs
    fine and fails later, in production, on whichever method the caller happened to reach first.
    """

    from core.kas.contract import AdapterConfig, ForgeAdapter

    class HalfAdapter(ForgeAdapter):
        provider_id = "half"

        def describe_repository(self):  # pragma: no cover - never constructed
            raise NotImplementedError

    with pytest.raises(TypeError) as caught:
        HalfAdapter(transport=lambda request: None, config=AdapterConfig(provider_id="half", base_url="https://x"))
    assert "abstract" in str(caught.value)
