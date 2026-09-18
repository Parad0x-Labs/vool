"""The WASM capability sandbox — plugin code with ZERO ambient authority.

Pass #9 proved the brokered surface absolute but left raw language authority alive
inside a native child. This module closes that with the one execution format where
ambient authority is not RESTRICTED but ABSENT:

**WebAssembly (via wasmtime), with WASI deliberately NOT linked.**

- A plugin is a wasm module whose only import is ``vool_broker.invoke`` — the
  EffectBroker's handle channel, bound at instantiation to the granted handles.
- Filesystem? There are no file opcodes in wasm; filesystem access exists ONLY as
  WASI imports — and we do not provide them. A module that declares
  ``wasi_snapshot_preview1.path_open`` FAILS INSTANTIATION. Not "denied":
  physically unlinkable.
- Network/process/environment: same category — no such host functions exist in the
  sandbox's linker.
- Memory isolation: the plugin sees only its own linear memory; the broker copies
  results in through its exported memory by explicit offset. It cannot address
  broker-side objects at all.

Falsification is built in: :meth:`WasmPluginSandbox.instantiate` on any module that
imports WASI raises ``LinkError``, and the tests pin exactly that.

Honest scope: this sandbox contains wasm BYTECODE. Native machine plugins do not fit;
they belong to the signed first-party tier (isolated process + OS sandbox when the
host provides one). That is the hybrid trust-tier product shape, not a weakening:
the LAW (zero ambient authority for store plugins) holds per tier.

Developer experience: third-party authors write Rust/Go/AssemblyScript/Python-
to-wasm targeting one import (``vool_broker.invoke(handle_id, args_json) -> reply``).
Hello-world is a ~20-line module or a one-function SDK call.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

import wasmtime

from core.kernel.broker import EffectBroker

__all__ = [
    "Budget",
    "ExecutionOutcome",
    "ResourceBudgetExceeded",
    "SandboxInstantiationError",
    "WasmPluginSandbox",
    "build_plugin_wat",
]


class ResourceBudgetExceeded(RuntimeError):
    """The plugin outspent its declared compute/call budget. Effect truth on the
    journal is UNCHANGED by this fact — they are different ledgers."""


@dataclass(frozen=True)
class Budget:
    """The compute/resource half of the two-budget law (mandate owns authority)."""

    fuel: int = 200_000                    # wasm instructions
    memory_bytes: int = 1_048_576         # linear-memory ceiling (1 MiB; Rust no_std needs >=16 pages)
    max_broker_calls: int = 16             # total handle invocations
    max_calls_per_handle: int = 8          # repeated-use bound
    reply_bytes: int = 4096


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str            # ok | trapped_fuel | trapped_resource | trapped_error | cancelled
    detail: str
    fuel_consumed: int
    broker_calls: int


def build_plugin_wat(handle_id: str, args_json: str) -> str:
    """The complete hello-world a third-party plugin author ships (wasm text form).
    Real SDKs emit this from Rust/Go/AssemblyScript/Python-to-wasm toolchains."""
    h = handle_id.replace('"', '\\"').replace("\n", "")
    a = args_json.replace('"', '\\"')
    return f'''(module
  (import "vool_broker" "invoke" (func $invoke (param i32 i32 i32 i32) (result i32)))
  (memory (export "memory") 1)
  (data (i32.const 0) "{h}")
  (data (i32.const 256) "{a}")
  (func (export "run") (result i32)
    (call $invoke (i32.const 0) (i32.const {len(handle_id)})
      (i32.const 256) (i32.const {len(args_json)}))))'''

_HOST_MODULE = "vool_broker"
_REPLY_OFFSET = 1024          # fixed scratch region in plugin linear memory


class SandboxInstantiationError(RuntimeError):
    """The module demanded authority the sandbox does not exist to give."""


class WasmPluginSandbox:
    """Instantiate an untrusted plugin with EXACTLY one import: the broker."""

    def __init__(self, broker: EffectBroker, budget: Budget | None = None,
                 cancelled: Callable[[], bool] | None = None) -> None:
        config = wasmtime.Config()
        config.consume_fuel = True          # compute budget enforcement
        # epoch_interruption left OFF: its default deadline (0) fires instantly;
        # fuel covers compute bounding for this slice.
        self.engine = wasmtime.Engine(config)
        self.broker = broker
        self.budget = budget or Budget()
        self.cancelled = cancelled or (lambda: False)

    def instantiate(self, wat_or_wasm: str | bytes):
        engine = self.engine
        try:
            if isinstance(wat_or_wasm, str):
                module = wasmtime.Module(engine, wat_or_wasm)
            else:
                module = wasmtime.Module(engine, wat_or_wasm)
        except wasmtime.WasmtimeError as exc:
            raise SandboxInstantiationError(f"module does not compile: {exc}") from exc

        # THE AMBIENT-AUTHORITY KILL: the linker knows ONLY our broker import.
        # No define_wasi. No filesystem. No clock. No environ. Nothing.
        linker = wasmtime.Linker(engine)
        broker = self.broker
        budget = self.budget
        holder: dict = {}
        store_holder: dict = {}
        state = {"calls": 0, "per_handle": {}, "cancelled_after_effect": False}

        def invoke(hptr: int, hlen: int, aptr: int, alen: int) -> int:
            if self.cancelled():
                raise _Cancelled()
            state["calls"] += 1
            if state["calls"] > budget.max_broker_calls:
                raise ResourceBudgetExceeded(
                    f"broker-call quota exhausted ({budget.max_broker_calls})")
            instance = holder["instance"]
            memory = _plugin_memory(store_holder["store"], instance)
            handle_id = bytes(memory.read(store_holder["store"], hptr, hptr + hlen)).decode()
            raw_args = bytes(memory.read(store_holder["store"], aptr, aptr + alen))
            try:
                args = json.loads(raw_args or "{}")
            except ValueError:
                args = {"__malformed_raw__": raw_args[:256].decode(errors="replace")}
            used = state["per_handle"].get(handle_id, 0)
            if used >= budget.max_calls_per_handle:
                raise ResourceBudgetExceeded(
                    f"handle {handle_id!r} used {used} times; "
                    f"per-handle bound is {budget.max_calls_per_handle}")
            state["per_handle"][handle_id] = used + 1
            try:
                result = broker.invoke(handle_id, args)
                reply = json.dumps({"ok": True, "result": result}).encode() + b"\x00"
            except Exception as exc:
                reply = json.dumps(
                    {"ok": False,
                     "error": f"{type(exc).__name__}: {exc}"}).encode() + b"\x00"
            reply = reply[:budget.reply_bytes]
            # The reply region never exceeds the GUEST's own linear memory: a
            # generous host-side reply cap must not crash small guests.
            avail = memory.data_len(store_holder["store"]) - _REPLY_OFFSET
            n = min(len(reply), budget.reply_bytes, max(avail, 0))
            memory.write(store_holder["store"], b"\x00" * n, _REPLY_OFFSET)
            memory.write(store_holder["store"], reply[:n], _REPLY_OFFSET)
            return len(reply)

        i32 = wasmtime.ValType.i32()
        func_type = wasmtime.FuncType([i32, i32, i32, i32], [i32])
        linker.define_func(_HOST_MODULE, "invoke", func_type, invoke)

        store = wasmtime.Store(engine)
        store.set_limits(memory_size=budget.memory_bytes)
        store.set_fuel(budget.fuel + 50_000)   # module init also burns fuel:
        # instantiation gets headroom ABOVE the plugin's own compute budget, so
        # a tiny budget cannot starve the loader while still bounding every run.

        def run_bounded(name: str = "run", *params: int) -> ExecutionOutcome:
            """Run ONE exported call under a FRESH compute budget (fuel arms here,
            after instantiation -- module setup is not the plugin's spend).
            A guest trap NEVER rewrites effect truth: receipts/journal live here."""
            store.set_fuel(budget.fuel)
            func = instance.exports(store)[name]
            start_fuel = budget.fuel
            _ = params
            try:
                func(store, *params)
                status, detail = "ok", ""
            except _Cancelled:
                status, detail = "cancelled", "cancellation honored at the boundary"
            except ResourceBudgetExceeded as exc:
                status, detail = "trapped_quota", str(exc)
            except wasmtime.Trap as exc:
                message = str(exc.trap_code) if exc.trap_code else str(exc)
                if "fuel" in message.lower():
                    status, detail = "trapped_fuel", message
                else:
                    status, detail = "trapped_resource", message
            except Exception as exc:  # guest-caught-nothing errors surface as traps
                status, detail = "trapped_error", f"{type(exc).__name__}: {exc}"
            remaining = store.get_fuel()
            consumed = int(start_fuel - remaining) if remaining is not None else 0
            return ExecutionOutcome(status=status, detail=detail,
                                    fuel_consumed=max(consumed, 0),
                                    broker_calls=state["calls"])

        try:
            instance = linker.instantiate(store, module)
            holder["instance"] = instance
            store_holder["store"] = store
        except wasmtime.WasmtimeError as exc:
            raise SandboxInstantiationError(
                f"module demanded authority the sandbox cannot give: {exc}") from exc
        globals_ = instance.exports(store)

        class _Bound:
            """Per-instance view: quotas/state die WITH the instance (tenants are
            isolated because each instantiate() mints fresh state dicts)."""

            @staticmethod
            def call(name: str = "run", *params: int) -> ExecutionOutcome:
                return run_bounded(name, *params)

            @staticmethod
            def call_with_request(handle_id: str, args_json: str,
                                  name: str = "run") -> ExecutionOutcome:
                """COMPILED-plugin entrypoint (Rust/Go/AS SDK contract): the HOST
                places the request in guest memory -- handle id @0, args @256 --
                then calls run(hlen, alen). The plugin never parses placement."""
                memory = _plugin_memory(store, instance)
                hb = handle_id.encode()
                ab = args_json.encode()
                memory.write(store, b"\x00" * 256, 0)
                memory.write(store, hb, 0)
                memory.write(store, ab[:512], 256)
                return run_bounded(name, len(hb), min(len(ab), 512))

            @staticmethod
            def memory_pages() -> int:
                return _plugin_memory(store, instance).size(store)

            @staticmethod
            def read_reply() -> str:
                memory = _plugin_memory(store, instance)
                raw = bytes(memory.read(store, _REPLY_OFFSET,
                                        _REPLY_OFFSET + budget.reply_bytes))
                end = raw.find(b"\x00")
                return (raw[:end] if end != -1 else raw).decode(errors="replace")

        return _Bound()


class _Cancelled(RuntimeError):
    pass


def _plugin_memory(store, instance):
    return instance.exports(store)["memory"]


def _plugin_memory(store, instance):
    return instance.exports(store)["memory"]


class WasmPluginInstance:
    def __init__(self, store, instance, exports, sandbox) -> None:
        self._store = store
        self._instance = instance
        self._exports = exports
        self._sandbox = sandbox

    def call(self, name: str = "run") -> object:
        func = self._exports[name]
        return func(self._store)

    def read_reply(self) -> str:
        memory = _plugin_memory(self._store, self._instance)
        length = int.from_bytes(
            bytes(memory.read(self._store, _REPLY_OFFSET - 8, _REPLY_OFFSET)), "little"
        ) if False else 0
        # reply length returned by invoke() is not persisted; scan for terminator
        raw = bytes(memory.read(self._store, _REPLY_OFFSET, _REPLY_OFFSET + 512))
        end = raw.find(b"\x00")
        return (raw[:end] if end != -1 else raw).decode(errors="replace")
