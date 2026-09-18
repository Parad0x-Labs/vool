"""Mandatory final correction REDs — the four production defects, proven at the real seams.

1. RETURN CONTRACT — the production setter must return the re-read effective values it
   wrote (dict[str, Any]), agreeing with both the cache and the on-disk document, under
   single-key and composite writes.
2. LOCK CONTENTION IS TYPED THROUGH THE REAL PATH — holding the owning policy lock and
   invoking first_run.pact.boundary.set through the Command Registry/HTTP boundary yields
   a typed conflict with detail.error == "lock_unavailable" and ZERO mutations anywhere;
   releasing the lock lets the same request succeed. The harness never invents the code.
3. STALE CAS PRECEDES EFFECTS — a stale boundary request (web, memory, local_only_composite,
   via direct call, registry, and HTTP, plus a concurrent stale carrier) returns
   stale_revision with ZERO changes to default_policy.yaml, operator-profile pause state,
   wallet freeze state, pact state, and receipts/events.
4. CROSS-PLATFORM LOCKING — one fail-closed lock implementation used by all three
   authorities: POSIX multiprocess exclusion proven; modules importable with fcntl
   absent (Windows branch selected via msvcrt semantics); contention stays typed.
   Actual Windows packaged execution remains UNMEASURED here.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from core import first_run_pact, policy_engine
from core.first_run_pact import PactFault
from tests.first_run_pact_rig import pact_rig  # noqa: F401 — fixture

REPO = __file__.rsplit("/tests/", 1)[0]


# --- 1. the return contract -----------------------------------------------------------------


def _typed_error(payload: dict) -> str:
    """The error code from either honest shape: the registry envelope's fault block,
    or the legacy adapter's bare body. The harness invents neither."""
    fault = payload.get("fault") or {}
    return (fault.get("detail") or {}).get("error") or payload.get("error") or ""


def test_the_production_setter_returns_the_exact_effective_values_written(pact_rig):
    returned = policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    assert isinstance(returned, dict), "the setter must return dict[str, Any], not None"
    assert returned == {"system.local_only_mode": True}
    assert policy_engine.local_only_mode() is True


def test_returned_values_agree_with_the_cache_and_the_disk_document(pact_rig):
    import yaml

    from core.runtime_paths import active_config_home_dir

    returned = policy_engine.set_operator_policy_values({
        "system.local_only_mode": True,
        "network.outbound_enabled": False,
    })
    assert policy_engine.get("system.local_only_mode") is True  # cache
    assert policy_engine.get("network.outbound_enabled") is False
    doc = yaml.safe_load((active_config_home_dir() / "default_policy.yaml").read_text())
    assert doc["system"]["local_only_mode"] is True  # disk
    assert doc["network"]["outbound_enabled"] is False
    assert returned["system.local_only_mode"] == doc["system"]["local_only_mode"]
    assert returned["network.outbound_enabled"] == doc["network"]["outbound_enabled"]


def test_the_return_contract_holds_for_a_single_key_write(pact_rig):
    returned = policy_engine.set_operator_policy_values({"system.allow_web_fallback": False})
    assert returned == {"system.allow_web_fallback": False}
    assert policy_engine.allow_web_fallback() is False


def test_the_return_contract_holds_for_the_full_composite_write(pact_rig):
    returned = policy_engine.set_operator_policy_values({
        "system.local_only_mode": True,
        "system.allow_web_fallback": False,
        "network.outbound_enabled": False,
        "shards.default_share_scope": "local_only",
    })
    assert returned == {
        "system.local_only_mode": True,
        "system.allow_web_fallback": False,
        "network.outbound_enabled": False,
        "shards.default_share_scope": "local_only",
    }
    assert policy_engine.local_only_mode() is True


# --- 2. lock contention is typed through the real command/API path ---------------------------


def _hold_policy_lock(pact_rig):
    import fcntl

    from core.runtime_paths import active_config_home_dir

    lock_path = active_config_home_dir() / ".default_policy.yaml.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    return holder


def test_boundary_contention_through_the_registry_is_typed_lock_unavailable_with_zero_mutations(pact_rig):
    from core.command_registry.legacy import forward as _cr_forward

    pact_rig.walk_to("provider_choice")
    before = {
        "pact": (pact_rig.home / "data" / "first_run_pact_state.json").read_bytes(),
        "policy": (pact_rig.home / "config" / "default_policy.yaml").exists()
        and (pact_rig.home / "config" / "default_policy.yaml").read_bytes(),
    }

    holder = _hold_policy_lock(pact_rig)
    try:
        status, payload = _cr_forward("first_run.pact.boundary.set", {
            "key": "local_only_composite", "value": True,
            "expect_revision": pact_rig.pact()["revision"],
        })
        assert status == 409, payload
        assert _typed_error(payload) == "lock_unavailable", payload
    finally:
        import fcntl

        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    # ZERO mutations anywhere: policy untouched, pact untouched.
    assert (pact_rig.home / "data" / "first_run_pact_state.json").read_bytes() == before["pact"]
    if before["policy"]:
        assert (pact_rig.home / "config" / "default_policy.yaml").read_bytes() == before["policy"]
    assert policy_engine.local_only_mode() is False

    # The same request succeeds once the lock is released.
    snap = pact_rig.pact()
    status, payload = _cr_forward("first_run.pact.boundary.set", {
        "key": "local_only_composite", "value": True,
        "expect_revision": snap["revision"],
    })
    assert status == 200, payload
    assert payload["live"]["local_only_mode"] is True


def test_boundary_contention_through_the_http_boundary_is_typed_with_zero_mutations(pact_rig):
    pact_rig.walk_to("provider_choice")
    policy_file = pact_rig.home / "config" / "default_policy.yaml"
    policy_bytes = policy_file.read_bytes() if policy_file.exists() else None

    holder = _hold_policy_lock(pact_rig)
    try:
        status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
            "key": "local_only_composite", "value": True,
            "expect_revision": pact_rig.pact()["revision"],
        })
        assert status == 409, payload
        assert payload["error"] == "lock_unavailable", payload
    finally:
        import fcntl

        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    after_bytes = policy_file.read_bytes() if policy_file.exists() else None
    assert after_bytes == policy_bytes


# --- 3. stale CAS precedes any authority effect -----------------------------------------------


def _authority_snapshot(pact_rig):
    import yaml

    from core.runtime_paths import active_config_home_dir
    from core.wallet.limits import is_frozen

    policy_doc = None
    policy_file = active_config_home_dir() / "default_policy.yaml"
    if policy_file.exists():
        policy_doc = yaml.safe_load(policy_file.read_text())
    from core import operator_profile

    return {
        "policy_bytes": policy_file.read_bytes() if policy_file.exists() else None,
        "local_only": policy_engine.local_only_mode(),
        "web": policy_engine.allow_web_fallback(),
        "outbound": policy_engine.get("network.outbound_enabled"),
        "memory_paused": operator_profile.is_paused(operator_profile.OWNER_PRINCIPAL),
        "wallet_frozen": is_frozen(),
        "pact_bytes": (pact_rig.home / "data" / "first_run_pact_state.json").read_bytes(),
        "revision": json.loads((pact_rig.home / "data" / "first_run_pact_state.json").read_text())["revision"],
    }


@pytest.mark.parametrize("key,value", [
    ("local_only_composite", True),
    ("memory_paused", True),
    ("web_lookups", True),
])
def test_a_stale_boundary_request_returns_stale_revision_and_touches_nothing(pact_rig, key, value):
    from core.command_registry.legacy import forward as _cr_forward

    pact_rig.pact()
    before = _authority_snapshot(pact_rig)
    stale_revision = before["revision"] - 1

    status, payload = _cr_forward("first_run.pact.boundary.set", {
        "key": key, "value": value, "expect_revision": stale_revision,
    })
    assert status == 409 and _typed_error(payload) == "stale_revision", payload

    after = _authority_snapshot(pact_rig)
    assert after == before, f"a stale request mutated {key} authority state"


def test_a_stale_boundary_request_through_the_http_boundary_touches_nothing(pact_rig):
    pact_rig.pact()
    before = _authority_snapshot(pact_rig)
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
        "key": "local_only_composite", "value": True,
        "expect_revision": before["revision"] - 7,
    })
    assert status == 409 and payload["error"] == "stale_revision", payload
    assert _authority_snapshot(pact_rig) == before


def test_a_concurrent_stale_carrier_boundary_request_mutates_nothing(pact_rig, tmp_path):
    """A process holding a pre-winner revision attempts a boundary after another process
    advanced the pact: typed stale_revision, zero authority changes."""
    import os
    import subprocess
    import sys
    import time

    pact_rig.pact()
    before = _authority_snapshot(pact_rig)
    stale_revision = before["revision"] - 1

    driver = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, {repo!r})
        from core.command_registry.legacy import forward
        status, payload = forward("first_run.pact.boundary.set", {{
            "key": "local_only_composite", "value": True,
            "expect_revision": {rev!r},
        }})
        print(json.dumps({{"status": status, "error": (payload.get("fault", {{}}).get("detail", {{}}) or {{}}).get("error") or payload.get("error")}}))
    """).format(repo=REPO, rev=stale_revision)
    script = tmp_path / "stale_boundary_driver.py"
    script.write_text(driver)
    env = dict(os.environ)
    env["VOOL_HOME"] = str(pact_rig.home)
    proc = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-400:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == 409, result
    assert result["error"] == "stale_revision", result
    assert _authority_snapshot(pact_rig) == before


def test_a_failing_later_authority_effect_reports_a_truthful_partial_never_clean(pact_rig, monkeypatch):
    """If a boundary's FIRST authority write succeeds but a later one fails, the outcome
    must name what applied and what failed — never a clean success, never a clean
    refusal-after-effects."""
    pact_rig.pact()
    snap = pact_rig.pact()

    import core.first_run_pact as frp

    # The FIRST effect (the four policy keys) applies; the SECOND effect (the wallet
    # panic freeze) fails. The outcome must be a truthful boundary_partial.
    def failing_freeze(frozen):
        raise RuntimeError("simulated wallet-store failure")

    monkeypatch.setattr(frp, "_wallet_freeze_note", lambda: "not_frozen")
    monkeypatch.setattr("core.wallet_spend_policy_store.set_frozen_mirrored", failing_freeze)
    with pytest.raises(PactFault) as excinfo:
        first_run_pact.set_boundary("local_only_composite", True, expect_revision=snap["revision"])
    assert excinfo.value.code == "boundary_partial", excinfo.value
    detail = json.loads(excinfo.value.detail)
    assert detail["applied"] == ["policy_composite"], detail
    assert detail["failed"][0]["effect"] == "wallet_freeze", detail
    # live authority truth is projected in the outcome, honestly naming the split state
    assert detail["live"]["local_only_mode"] is True


# --- 4. cross-platform locking ----------------------------------------------------------------


POSIX_LOCK_DRIVER = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, {repo!r})
from core.cross_process_lock import PublicationLock, LockUnavailable
lock_path = sys.argv[1]
other_ready = sys.argv[2]
my_ready = sys.argv[3]
Path(my_ready).write_text("1")
deadline = time.time() + 20
while not Path(other_ready).exists() and time.time() < deadline:
    time.sleep(0.02)
try:
    with PublicationLock(lock_path):
        time.sleep(0.5)
    print(json.dumps({{"result": "acquired"}}))
except LockUnavailable:
    print(json.dumps({{"result": "refused", "code": "lock_unavailable"}}))
"""


def test_posix_multiprocess_exclusion_through_the_shared_lock(tmp_path):
    from core.cross_process_lock import LockUnavailable  # noqa: F401 — the typed code
    import subprocess
    import sys

    lock_path = tmp_path / "probe.lock"
    script = tmp_path / "driver.py"
    script.write_text(POSIX_LOCK_DRIVER.format(repo=REPO))
    env = dict(os.environ)
    procs = []
    for tag in ("a", "b"):
        ready = tmp_path / f"{tag}.ready"
        other = tmp_path / f"{'b' if tag == 'a' else 'a'}.ready"
        procs.append(subprocess.Popen(
            [sys.executable, str(script), str(lock_path), str(other), str(ready)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
    outs = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        if proc.returncode != 0:
            raise AssertionError(f"lock driver crashed: {err[-400:]}")
        outs.append(json.loads(out.strip().splitlines()[-1]))
    acquired = [o for o in outs if o["result"] == "acquired"]
    refused = [o for o in outs if o["result"] == "refused"]
    assert len(acquired) == 1, outs
    assert len(refused) == 1 and refused[0]["code"] == "lock_unavailable", outs


def test_the_lock_module_imports_without_fcntl_and_uses_msvcrt_semantics(monkeypatch, tmp_path):
    """Windows branch: with fcntl absent and msvcrt present, a fresh load of the shared
    lock module imports and locks through msvcrt.locking. (Actual Windows packaged
    execution stays UNMEASURED — this proves import + call-path selection only.)

    The probe loads a PRIVATE module object: reloading the cached module would leave
    diverged class identities (LockUnavailable) behind for other importers.
    """
    import importlib.util
    import os
    import sys
    import types

    fake_msvcrt = types.ModuleType("msvcrt")
    calls = {"locking": []}
    fake_msvcrt.LK_NBLCK = 2
    fake_msvcrt.LK_UNLCK = 0

    def fake_locking(fd, mode, nbytes):
        calls["locking"].append((fd, mode, nbytes))

    fake_msvcrt.locking = fake_locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.delitem(sys.modules, "fcntl", raising=False)

    real_import = __import__

    def guarded_import(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("fcntl is not available on Windows")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    spec = importlib.util.spec_from_file_location(
        "cpl_windows_probe", str(Path(REPO) / "core" / "cross_process_lock.py"))
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    assert "fcntl" not in vars(fresh), "the module must not keep a module-scope fcntl binding"
    # Force the Windows branch through the module's own constant (exactly what
    # os.name == "nt" produces on a real Windows host) — no global patching.
    fresh.IS_WINDOWS = True
    lock_file = os.path.join(str(tmp_path), "w.lock")
    with fresh.PublicationLock(lock_file):
        assert calls["locking"], "the Windows branch must lock through msvcrt.locking"


def test_boundary_policy_lock_contention_raises_the_typed_pact_fault(pact_rig, monkeypatch):
    """Module-level contract: set_boundary translates the owning-policy lock refusal
    into its OWN typed fault (lock_unavailable) — a raw LockUnavailable must never
    escape from the pact boundary seam."""
    from core.cross_process_lock import LockUnavailable
    from core import policy_engine as pe

    pact_rig.pact()
    snap = pact_rig.pact()

    def contended(values):
        raise pe.PolicyLockUnavailable("/x/.default_policy.yaml.lock", "held")

    monkeypatch.setattr(policy_engine, "set_operator_policy_values", contended)
    with pytest.raises(PactFault) as excinfo:
        first_run_pact.set_boundary("web_lookups", False, expect_revision=snap["revision"])
    assert excinfo.value.code == "lock_unavailable", excinfo.value


def test_the_authorities_use_the_shared_lock_implementation():
    """One lock authority: both pact and provider modules acquire via the shared module,
    not private fcntl imports."""
    import inspect

    from core import first_run, first_run_pact

    for module in (first_run, first_run_pact):
        src = inspect.getsource(module)
        assert "import fcntl" not in src, f"{module.__name__} still imports fcntl directly"
    from core import cross_process_lock

    assert cross_process_lock.PublicationLock is not None




# --- FINAL PASS 1: the locked writer result is forwarded under the lock ----------------------


def test_a_racing_publisher_between_lock_release_and_read_cannot_change_the_return(pact_rig, monkeypatch):
    """Deterministic interleave: a probe detects the moment writer A has RELEASED the
    publication lock but not yet read its return values — writer B publishes there.
    At the defect tip, A returns B's values; the contract requires A's own."""
    lock_path = pact_rig.home / "config" / ".default_policy.yaml.lock"

    real_get = policy_engine.get
    state = {"interleaved": False}

    def hooking_get(path, default=None):
        if not state["interleaved"] and lock_path.exists():
            import fcntl

            probe = open(lock_path, "w")
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # A has released the lock: writer B publishes NOW, before A reads.
                state["interleaved"] = True
                fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
                policy_engine._write_operator_values_locked({"system.local_only_mode": False})
            except OSError:
                pass  # A still holds the lock: no interleave window yet
            finally:
                probe.close()
        return real_get(path, default)

    monkeypatch.setattr(policy_engine, "get", hooking_get)
    returned = policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    # The window the hook probes for must NOT exist on the fixed shape: a post-release
    # re-read would let it fire AND corrupt the return. Both facts are pinned.
    assert not state["interleaved"], (
        "the setter re-reads after releasing the publication lock — a racing publisher "
        "got between publication and the return"
    )
    assert returned == {"system.local_only_mode": True}, (
        "the setter returned a racing publisher's value: %r" % (returned,)
    )


def test_the_helper_result_is_forwarded_byte_for_byte_under_the_lock(pact_rig, monkeypatch):
    real_helper = policy_engine._write_operator_values_locked
    recorded = {}

    def recording_helper(values):
        recorded["during_lock"] = dict(real_helper(values))
        return dict(recorded["during_lock"])

    monkeypatch.setattr(policy_engine, "_write_operator_values_locked", recording_helper)
    returned = policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    assert returned == recorded["during_lock"], "the public return must be the helper's locked result"


# --- FINAL PASS 2: lock-file OPEN failures are typed through every boundary ------------------


def _break_lock_file_open(pact_rig):
    """Make the lock-file OPEN itself fail with OSError (a directory in its place)."""
    from core.runtime_paths import active_config_home_dir

    lock_path = active_config_home_dir() / ".default_policy.yaml.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.mkdir(parents=True, exist_ok=True)  # open(..., "w") now raises IsADirectoryError
    return lock_path


def test_direct_policy_write_with_a_broken_lock_open_is_typed(pact_rig, monkeypatch):
    _break_lock_file_open(pact_rig)
    with pytest.raises(policy_engine.PolicyLockUnavailable) as excinfo:
        policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    assert excinfo.value.code == "lock_unavailable"


def test_registry_boundary_with_a_broken_lock_open_is_typed_never_500(pact_rig):
    from core.command_registry.legacy import forward as _cr_forward

    pact_rig.walk_to("provider_choice")
    _break_lock_file_open(pact_rig)
    status, payload = _cr_forward("first_run.pact.boundary.set", {
        "key": "local_only_composite", "value": True,
        "expect_revision": pact_rig.pact()["revision"],
    })
    assert status == 409, (status, payload)
    assert _typed_error(payload) == "lock_unavailable", payload


def test_http_boundary_with_a_broken_lock_open_is_typed_never_500(pact_rig):
    pact_rig.walk_to("provider_choice")
    _break_lock_file_open(pact_rig)
    status, payload = pact_rig.post("/api/onboarding/pact/boundary", {
        "key": "local_only_composite", "value": True,
        "expect_revision": pact_rig.pact()["revision"],
    })
    assert status == 409, (status, payload)
    assert payload.get("error") == "lock_unavailable", payload


# --- FINAL PASS 3: the command contract declares every real boundary fault -------------------


def test_every_boundary_fault_the_handler_can_produce_is_declared():
    """Census/contract pin: every fault code set_boundary can raise is declared on the
    first_run.pact.boundary.set CommandSpec AND carries remediation text."""
    import inspect
    import re

    from core.command_registry.groups.first_run import _FAULT_REMEDIATIONS
    from core.command_registry.registry import registry as get_registry

    spec = next(c for c in get_registry().commands() if c.command_id == "first_run.pact.boundary.set")
    declared = {fb.when for fb in spec.fault_bindings}

    source = inspect.getsource(first_run_pact.set_boundary)
    produced = set(re.findall(r'PactFault\(\s*"([a-z_]+)"', source))
    produced |= set(re.findall(r"PactFault\(\s*(FAULT_[A-Z_]+)", source))
    constants = {
        "FAULT_BOUNDARY_KEY_UNKNOWN": "boundary_key_unknown",
        "FAULT_INVALID_TRANSITION": "invalid_transition",
        "FAULT_STALE_REVISION": "stale_revision",
        "FAULT_LOCK_UNAVAILABLE": "lock_unavailable",
    }
    produced = {constants.get(code, code) for code in produced}
    produced |= {"boundary_partial", "authority_write_failed"}

    undeclared = produced - declared
    assert not undeclared, "undeclared boundary faults: %s" % sorted(undeclared)
    unremedied = {code for code in produced if code not in _FAULT_REMEDIATIONS}
    assert not unremedied, "boundary faults without remediation text: %s" % sorted(unremedied)


# --- FINAL PASS 4: pact publication failure after authority effects is truthful ---------------


@pytest.mark.parametrize("key", ["web_lookups", "memory_paused", "local_only_composite"])
def test_a_pact_publication_failure_after_effects_is_a_truthful_partial(pact_rig, monkeypatch, key):
    """A disk/publication failure AFTER the authority effects applied is reported as
    boundary_partial naming the applied authority effect and the failed publication,
    with live authority truth — never a raw 500, never a clean refusal."""
    import core.first_run_pact as frp

    pact_rig.pact()
    snap = pact_rig.pact()
    value = key != "memory_paused"  # web True / memory True / composite True

    def broken_publish(mutate):
        raise OSError("simulated disk failure during pact publication")

    monkeypatch.setattr(frp, "_publish", broken_publish)
    with pytest.raises(PactFault) as excinfo:
        first_run_pact.set_boundary(key, value, expect_revision=snap["revision"])
    assert excinfo.value.code == "boundary_partial", excinfo.value
    detail = json.loads(excinfo.value.detail)
    assert detail["failed"] and detail["failed"][-1]["effect"] == "pact_publication", detail
    assert detail["applied"], detail
    assert "live" in detail
