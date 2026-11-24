"""Every optional module VOOL checks for at run time ships in the Mac and Linux packages.

The Mac and Linux packages install the lock's base dependencies plus a list of extras. The browser
window needs playwright, which sat in an extra neither package installed, so the packaged app said
"unsupported" for the browser while every source-checkout test passed. This reads the modules core
probes with importlib.util.find_spec and requires each to be shipped, or named below with a reason.
"""
from __future__ import annotations

import re
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]

#: Module -> the distribution that provides it, where the names differ.
DISTRIBUTION = {"solana_pay_kit": "solana-pay-kit"}

#: Probed modules that the packages leave out on purpose.
NOT_SHIPPED = {
    "solana_pay_kit": "the pay extra is locked in a branch of its own (pyproject.toml)",
    "nebula": "vendored source, not a package dependency",
}


def _probed() -> set[str]:
    found = set()
    for path in (ROOT / "core").rglob("*.py"):
        found.update(re.findall(r"find_spec\(\s*[\"']([A-Za-z_][\w]*)[\"']\s*\)", path.read_text(encoding="utf-8")))
    return found


def _names(requirements: list[str]) -> set[str]:
    return {re.split(r"[\s\[<>=!~;]", item.strip(), maxsplit=1)[0].lower().replace("_", "-") for item in requirements}


def _shipped(extras) -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = _names(project["dependencies"])
    for extra in extras:
        names |= _names(project["optional-dependencies"][extra])
    return names


def _mac_extras() -> tuple[str, ...]:
    from ops.macos_locked_requirements import EXTRAS

    return EXTRAS


def _linux_extras() -> tuple[str, ...]:
    script = (ROOT / "installer" / "bundle" / "build_linux_bundle.sh").read_text(encoding="utf-8")
    return tuple(re.findall(r"--extra (\w+)", script))


def test_core_probes_for_the_browser_driver():
    assert "playwright" in _probed()


def test_every_probed_module_ships_on_mac_and_linux():
    for platform, extras in (("macOS", _mac_extras()), ("Linux", _linux_extras())):
        shipped = _shipped(extras)
        missing = sorted(
            module for module in _probed() - set(NOT_SHIPPED)
            if DISTRIBUTION.get(module, module).lower().replace("_", "-") not in shipped
        )
        assert missing == [], f"{platform} package leaves out {missing}; add its extra or list it in NOT_SHIPPED"


def test_the_left_out_list_names_only_modules_core_still_probes():
    assert set(NOT_SHIPPED) <= _probed()
