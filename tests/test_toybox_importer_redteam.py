"""Importer red-team invariants: impersonation refused, metadata inert, damage fails closed.

Contributor: sls_0x. A skin may not name itself "VOOL default": the Toybox status line
shows the active skin's name, so a lookalike name would read exactly like the restored
state. Skin metadata never reaches the generated CSS, and a damaged stored skin leaves
the app on VOOL's own look instead of a half-rendered one.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

SPOOF_NAMES = [
    "VOOL default",
    "vool default",
    "Vool Default",
    "VOOL  default",
    " VOOL default ",
    "VOOL default ",
]


def _manifest():
    from core.toybox import EXAMPLES

    return deepcopy(EXAMPLES[0])


@pytest.mark.parametrize("name", SPOOF_NAMES)
def test_a_skin_cannot_name_itself_the_default(name):
    from core.toybox import SkinError, validate

    manifest = _manifest()
    manifest["name"] = name
    with pytest.raises(SkinError):
        validate(json.dumps(manifest))


def test_metadata_never_reaches_the_css():
    from core.toybox import validate
    from core.toybox_fragment import skin_css

    plain = _manifest()
    hostile = _manifest()
    hostile["name"] = "url(//evil) ;} expression("
    hostile["author"] = "@import background:url(javascript:1)"
    hostile["description"] = ";} /b url( everything"
    hostile["license"] = ";} url( //evil"
    css_plain = skin_css(validate(json.dumps(plain))[0])
    css_hostile = skin_css(validate(json.dumps(hostile))[0])
    assert css_plain == css_hostile
    for marker in ("evil", "expression(", "javascript:", "@import"):
        assert marker not in css_hostile


def test_no_selection_and_damaged_selection_render_no_skin():
    from core.toybox_fragment import skin_css

    assert skin_css(None) == ""
