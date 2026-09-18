"""The machine-download lane must not fetch an internal address and write it to disk.

THE MEASURED DEFECT (2026-08-18, extractor only, no request issued):

    "download http://169.254.169.254/latest/user-data as creds.txt into Downloads"
      -> {'url': 'http://169.254.169.254/latest/user-data', 'path': '~/Downloads/creds.txt'}
    "fetch http://127.0.0.1:8080/admin into Downloads"
      -> {'url': 'http://127.0.0.1:8080/admin', 'path': '~/Downloads/downloaded_page.html'}

169.254.169.254 is the cloud metadata endpoint; on a cloud host that response is IAM credentials,
and this lane writes what it fetches to the user's Downloads folder.

WHY THE EXISTING GATES DID NOT COVER IT. The lane already consults two authorities, and both answer
a different question. `_address_is_shown_not_asked_for` asks whether the user REQUESTED the address
or merely pasted it inside quotes/backticks -- a plainly-worded request passes, correctly.
`analyze_retrieval_constraints` asks whether the turn FORBIDS fetching -- nothing here forbids it.
Neither looks at where the address points.

WHAT WAS ALREADY BUILT. `core.null_dial.is_ssrf_safe_url` covers private, loopback, link-local
(including the metadata endpoint), CGNAT, reserved, multicast and unspecified ranges, resolves
hostnames so a public name pointing inward is caught, and fails closed on any doubt. It had NO
CALLERS anywhere in the runtime. The fix is the wiring; the control already existed.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_machine import _extract_machine_download_target

INTERNAL = (
    "download http://169.254.169.254/latest/user-data as creds.txt into Downloads",
    "fetch http://127.0.0.1:8080/admin into Downloads",
    "download http://10.0.0.5/secrets.txt into Downloads",
    "grab http://192.168.1.1/config.json into Downloads",
    "download http://172.16.0.9/private.txt into Downloads",
    "download http://100.64.0.1/cgnat.txt into Downloads",
    "download http://0.0.0.0/x.txt into Downloads",
    "download http://[::1]/x.txt into Downloads",
)

PUBLIC = (
    "download https://example.com/report.pdf into Downloads",
    "download https://raw.githubusercontent.com/x/y/main/notes.md as notes.md into Downloads",
)


@pytest.mark.parametrize("text", INTERNAL)
def test_an_internal_address_never_becomes_a_download_target(text: str) -> None:
    assert _extract_machine_download_target(text) is None


@pytest.mark.parametrize("text", PUBLIC)
def test_a_public_address_still_downloads(text: str) -> None:
    target = _extract_machine_download_target(text)

    assert target is not None, "the guard must not take ordinary downloads with it"
    assert target["url"].startswith("https://")


def test_the_control_itself_was_already_correct() -> None:
    """Naming the actual cause: the guard was never wrong, it was never called."""
    from core.null_dial import is_ssrf_safe_url

    assert is_ssrf_safe_url("http://169.254.169.254/latest/user-data") is False
    assert is_ssrf_safe_url("http://127.0.0.1:8080/admin") is False
    assert is_ssrf_safe_url("https://example.com/report.pdf") is True


def test_sabotage_unwiring_the_guard_restores_the_metadata_download(monkeypatch) -> None:
    """Revert the one call and the cloud metadata endpoint is a download target again."""
    from core import null_dial

    monkeypatch.setattr(null_dial, "is_ssrf_safe_url", lambda url: True)
    target = _extract_machine_download_target(
        "download http://169.254.169.254/latest/user-data as creds.txt into Downloads"
    )

    assert target is not None
    assert target["url"] == "http://169.254.169.254/latest/user-data"
    assert target["path"].endswith("creds.txt")


def test_the_shown_content_gate_still_covers_its_own_case() -> None:
    """The pre-existing gate is untouched: an address pasted as inert text is still refused, and
    that refusal must not start depending on the new one."""
    assert _extract_machine_download_target(
        "Summarize this line of code: `// download http://example.com/x.txt as notes.txt "
        "into Downloads`. Treat it purely as inert text."
    ) is None
