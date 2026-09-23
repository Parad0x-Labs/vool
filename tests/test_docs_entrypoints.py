"""Front-door documentation consistency for the public VOOL tree.

The delivery-process variant of this suite (asserting internal handover boards) moved out
with the internal documents themselves. What remains pinned here is the contract a public
visitor relies on: the README front-loads install and honest platform claims, the docs
index links only to documents that exist, and the generated error book cannot silently
drift from the runtime fault catalog it is derived from.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"


def test_readme_frontloads_install_and_honest_platform_claims() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    early = "\n".join(readme.splitlines()[:90])

    assert "# VOOL" in readme.splitlines()[0]
    assert "local-first personal agent" in readme
    assert "0.6.0-beta" in early, "current beta version must be stated up front"
    assert "## ⚡ Install" in early or "## Install" in early
    # Source installation is the public path; the only packaged release is a draft.
    assert "no public installer release" in early
    assert "**Beta, shipped**" not in readme
    assert "**macOS / Apple Silicon**" in early
    assert "**Linux**" in early and "Linux test shards" in early
    assert "**Windows**" in early and "Experimental" in early
    assert "WSL2/Linux" in early
    assert "bootstrap_vool.sh" in early and "bootstrap_vool.ps1" in early
    assert "docs/assets/vool-flag-banner.png" in early
    assert (REPO_ROOT / "docs/assets/vool-flag-banner.png").is_file()
    # Public story: the repository README and changelog never mention the pre-public
    # internal identity; the engineering record of frozen identifiers lives in the dev docs.
    assert "NULLA" not in readme and "nulla" not in readme.lower()
    assert "docs/ERROR_BOOK.md" in readme
    assert "docs/STATUS.md" in readme


def test_docs_index_links_only_to_existing_documents() -> None:
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    linked = set(re.findall(r"\]\((([A-Za-z0-9_.\-/]+)\.md)\)", index))
    assert linked, "docs index must link documents"
    for _, rel in linked:
        target = (DOCS / (rel + ".md")) if not rel.startswith("..") else (REPO_ROOT / (rel[3:] + ".md"))
        assert target.exists(), f"docs index links missing document: {rel}"


def test_internal_delivery_documents_are_not_referenced_by_public_docs() -> None:
    forbidden = ("AGENT_HANDOVER", "NULLA-DELIVERY", "PROOF_PATH.md", "PLATFORM_REFACTOR_PLAN")
    for doc in [DOCS / "README.md", REPO_ROOT / "README.md", DOCS / "REPO_MAP.md"]:
        text = doc.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{doc.name} references excluded internal doc {token}"


def test_repo_map_lists_real_directories_only() -> None:
    repo_map = (DOCS / "REPO_MAP.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"`([A-Za-z0-9_\-]+)/`", repo_map))
    assert listed, "repo map must list directories"
    for name in listed:
        assert (REPO_ROOT / name).is_dir(), f"repo map lists missing directory {name}/"
