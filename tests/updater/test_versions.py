"""core.updater.versions — strict semver for channel/version decisions.

The old lane (core.self_update_check.parse_version) is best-effort display logic; the
signed-manifest lane needs real ordering rules: prerelease < release, numeric identifiers
compare numerically, and an unparsable version must NEVER silently become (0,0,0).
"""
from __future__ import annotations

import pytest

from core.updater.versions import Semver, parse_semver


class TestParse:
    def test_plain_triple(self):
        assert parse_semver("0.6.0") == Semver(0, 6, 0)

    def test_leading_v_is_tolerated(self):
        assert parse_semver("v1.2.3") == Semver(1, 2, 3)

    def test_prerelease_and_build_metadata(self):
        v = parse_semver("0.6.0-beta.3+build.12")
        assert v.prerelease == ("beta", 3)
        assert v.build == "build.12"
        assert v == parse_semver("0.6.0-beta.3")  # precedence ignores build metadata

    @pytest.mark.parametrize("bad", ["", "zero", "1", "1.2", "1.2.x", "1.2.3.4", " 1.2.3", "1.2.-3"])
    def test_invalid_versions_raise(self, bad: str):
        with pytest.raises(ValueError):
            parse_semver(bad)


class TestOrdering:
    def test_release_is_newer_than_prerelease_of_same_triple(self):
        assert parse_semver("0.6.0") > parse_semver("0.6.0-beta.9")

    def test_prerelease_numbering(self):
        assert parse_semver("0.6.0-beta.2") > parse_semver("0.6.0-beta.1")

    def test_numeric_prerelease_lower_than_alphanumeric(self):
        assert parse_semver("0.6.0-alpha.11") < parse_semver("0.6.0-alpha.2x")

    def test_higher_patch_wins(self):
        assert parse_semver("0.5.9") < parse_semver("0.6.0")

    def test_numeric_not_lexicographic(self):
        assert parse_semver("0.10.0") > parse_semver("0.9.0")

    def test_build_metadata_never_affects_ordering(self):
        assert parse_semver("1.0.0+b1") == parse_semver("1.0.0+b2")

    def test_sort_a_run_of_versions(self):
        ordered = ["0.4.0", "0.5.0-beta.1", "0.5.0-beta.2", "0.5.0", "0.6.0"]
        shuffled = [ordered[i] for i in (3, 0, 4, 1, 2)]
        assert sorted(shuffled, key=parse_semver) == ordered
