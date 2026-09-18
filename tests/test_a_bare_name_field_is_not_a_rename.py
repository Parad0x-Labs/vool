"""A `name:` line in a spec is not a request to rename the user.

`classify_voolbook_intent` matched `(?:set\\s+)?(?:my\\s+)?(?:name|handle)\\s*[:=]` — both
qualifiers optional, so a BARE `name:` anywhere in a message routed the turn to the VoolBook
rename flow.

An independent tester, told nothing about this code, hit it with a skill specification and got back
"You need a VoolBook profile first." in 0.1s. The detail that makes it structural rather than
unlucky: SKILL.md frontmatter opens with `name:`, so the one document format this product authors
was guaranteed to trigger it — as is any pasted YAML, JSON field, config block, form, or database
column definition.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.voolbook import classify_voolbook_intent

A_REAL_RENAME = (
    "set my name: Alex",
    "my name: Alex",
    "set name: Alex",
    "my handle = zed",
    "set my handle: zed",
)

NOT_A_RENAME = (
    "here is the yaml:\nname: my-service\nport: 8080",
    "create a skill with this spec:\nname: unit-converter\ndescription: converts units",
    "the frontmatter is name: release-notes",
    "a json field name: value",
    "column name: created_at",
    "---\nname: bread-timer\ndescription: Use when the user asks about proofing times.\n---",
)


@pytest.mark.parametrize("text", A_REAL_RENAME)
def test_an_actual_rename_still_works(text: str) -> None:
    """The fix must not be bought by breaking the feature."""

    assert classify_voolbook_intent(text) == "rename", text


@pytest.mark.parametrize("text", NOT_A_RENAME)
def test_a_name_field_in_a_document_is_not_a_rename(text: str) -> None:
    assert classify_voolbook_intent(text) != "rename", text


def test_skill_frontmatter_specifically() -> None:
    """The product's own authored format, which this rule was guaranteed to hijack."""

    from core.skill_tools import render_skill_markdown

    document = render_skill_markdown(
        name="Release Notes",
        description="Use when the user asks for release notes.",
        body="Read CHANGELOG.md, then summarise by version.",
    )
    assert document.startswith("---\nname:")
    assert classify_voolbook_intent(document) != "rename"
