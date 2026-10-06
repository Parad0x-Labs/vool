"""Every VOOL process entry point starts chats on the memory capsule path.

The capsule readers keep a default-off flag so a bare import assembles the legacy transcript
unless it asks. Before the entry-point default, only the source installers exported
VOOL_CONTEXT_CAPSULE_V2=1, so the macOS app and the Windows bundle, whose supervisors start
``apps.vool_api_server`` without those installers, served every chat on the legacy recall path.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.runtime_provider_defaults import apply_product_runtime_defaults

ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINTS = (
    ("apps/vool_api_server.py", "main"),
    ("apps/vool_cli.py", "main"),
    ("apps/vool_chat.py", "main"),
    ("core/agent_runtime/agent.py", "main"),
    ("core/agent_runtime/daemon.py", "main"),
)


def test_unset_flag_defaults_to_the_capsule():
    env: dict[str, str] = {}
    apply_product_runtime_defaults(env)
    assert env["VOOL_CONTEXT_CAPSULE_V2"] == "1"


@pytest.mark.parametrize("explicit", ["0", "false", "1", ""])
def test_an_explicit_value_always_wins(explicit):
    env = {"VOOL_CONTEXT_CAPSULE_V2": explicit}
    apply_product_runtime_defaults(env)
    assert env["VOOL_CONTEXT_CAPSULE_V2"] == explicit


def test_the_default_reaches_the_capsule_reader():
    from core.local_ollama_inventory import env_flag_enabled

    env: dict[str, str] = {}
    assert not env_flag_enabled(env, "VOOL_CONTEXT_CAPSULE_V2", default=False)
    apply_product_runtime_defaults(env)
    assert env_flag_enabled(env, "VOOL_CONTEXT_CAPSULE_V2", default=False)
    off = {"VOOL_CONTEXT_CAPSULE_V2": "0"}
    apply_product_runtime_defaults(off)
    assert not env_flag_enabled(off, "VOOL_CONTEXT_CAPSULE_V2", default=False)


@pytest.mark.parametrize("path,func", ENTRY_POINTS)
def test_each_entry_point_applies_the_default_before_serving(path, func):
    tree = ast.parse((ROOT / path).read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
    calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "apply_product_runtime_defaults"]
    assert calls, f"{path}:{func} never applies the product runtime defaults"
    first_call = min(c.lineno for c in calls)
    serving = [n.lineno for n in ast.walk(main) if isinstance(n, ast.Call)
               and getattr(n.func, "attr", getattr(n.func, "id", "")) in {"parse_args", "serve_forever", "run"}]
    assert not serving or first_call < min(serving), f"{path}:{func} serves before the default is applied"
