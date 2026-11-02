from __future__ import annotations

import json
from copy import deepcopy

import pytest

from core.toybox import EXAMPLES, SkinError, change, state, validate
from tests._toybox_v2_browser import served as served


def fixture_skin():
    manifest = deepcopy(EXAMPLES[0])
    manifest.update(id="stage-probe", name="Stage probe", renderer_contract="2.0", stage={"layout": "device"})
    return manifest


@pytest.mark.parametrize("field,value", [("layout", "outside"), ("controls", "<script>"), ("type", 1.2), ("css", "body{}"),
                                        ("frame", {"set": "bezel", "url": "https://invalid"}),
                                        ("reactive", {"profile": "meter", "accentFrom": "secret"})])
def test_unknown_stage_fields_refuse(field, value):
    manifest = fixture_skin()
    manifest["stage"][field] = value
    with pytest.raises(SkinError, match="stage"):
        validate(json.dumps(manifest))


def test_old_contract_cannot_request_stage():
    manifest = fixture_skin()
    manifest["renderer_contract"] = "1.2"
    with pytest.raises(SkinError):
        validate(json.dumps(manifest))


def test_preference_cas_and_tamper_preserve_evidence(served):
    from storage.db import get_connection

    raw = json.dumps(fixture_skin())
    first = change("apply", {"manifest": raw, "revision": 0, "confirmed": True, "motion": "calm", "layout_override": "standard"})
    assert first["motion"] == "calm" and first["layout_override"] == "standard"
    assert state()["motion"] == "calm"
    with pytest.raises(SkinError) as error:
        change("apply", {"manifest": raw, "revision": 0, "confirmed": True, "motion": "off"})
    assert error.value.status == 409
    assert state()["motion"] == "calm"
    with get_connection() as conn:
        conn.execute("UPDATE toybox_skin_objects SET manifest='{}'")
        conn.commit()
    assert state()["selected"] is None and state()["warning"] and state()["css"] == ""


def test_png_slot_bounds_and_canonical_reencoding():
    import base64
    from io import BytesIO

    from PIL import Image

    manifest = fixture_skin()
    for width in (1024, 1025):
        output = BytesIO()
        Image.new("RGB", (width, 64), "black").save(output, "PNG")
        manifest["assets"] = [{"path": "assets/top.png", "png": base64.b64encode(output.getvalue()).decode()}]
        manifest["stage"]["frame"] = {"set": "bezel", "art": {"top": "assets/top.png"}}
        if width == 1025:
            with pytest.raises(SkinError, match="slot"):
                validate(json.dumps(manifest))
        else:
            validated = validate(json.dumps(manifest))
            assert validated[0]["assets"][0]["png"]
