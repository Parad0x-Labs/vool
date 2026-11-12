from __future__ import annotations

import json

import pytest

from core.toybox import validate
from core.toybox_art_b import artwork
from core.toybox_flagships_b import SKINS
from core.toybox_fragment import skin_css
from tests._toybox_v2_browser import served as served
from tests.test_toybox_flagships_a_browser import run_matrix


def test_second_collection_palettes_original_drawings_and_limits():
    from core.toybox_registry_v2 import manifests

    assert {s['id'] for s in SKINS} <= {s[0]['id'] for s in manifests()}
    for skin in SKINS:
        assert len(skin_css(validate(json.dumps(skin))[0]).encode()) <= 60_000
        for palette in skin['palettes'].values():
            drawings = artwork(skin['id'], palette)
            assert set(drawings) == {'empty', 'header', 'perch'}
            assert sum(len(s.encode()) for s in drawings.values()) < 160_000
            assert all(not any(word in s for word in ('<script', 'href=', '<text', '<image')) for s in drawings.values())


@pytest.mark.parametrize('identifier', [s['id'] for s in SKINS])
def test_second_collection_real_pages(served, identifier):
    run_matrix(served, next(s for s in SKINS if s['id'] == identifier))
