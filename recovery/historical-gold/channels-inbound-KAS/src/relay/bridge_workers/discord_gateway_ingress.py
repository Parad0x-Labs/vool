from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import random
import re
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib import error, request
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from core.channel_gateway import process_channel_request
from core.discord_recent_observations import discord_message_ref, parse_discord_message_id
from core.external_ingress import ExternalIngressEventConflictError
from core.plugin_catalog import plugin_enabled, plugin_enablement_generation
from relay.bridge_workers.discord_command_parser import extract_discord_command_text
from relay.bridge_workers.discord_external_ingress import (
    DiscordExternalIngressAdapter,
    discord_account_ref,
    discord_conversation_ref,
    select_discord_reply_source_ref,
)
from relay.bridge_workers.discord_gateway_state import (
    GATEWAY_ENCODING,
    GATEWAY_VERSION,
    MAX_AUTHORIZED_CHANNELS,
    DiscordGatewayAuthorizedStage,
    DiscordGatewayCorruptStageError,
    DiscordGatewayOwnerBusyError,
    DiscordGatewayResume,
    DiscordGatewayStateError,
    DiscordGatewayStateStore,
    _DiscordGatewayOwnershipGuard,
)

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

GUILD_MESSAGES = 1 << 9
DIRECT_MESSAGES = 1 << 12
MESSAGE_CONTENT = 1 << 15
DISCORD_M5B_INTENTS = GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT

MAX_GATEWAY_FRAME_BYTES = 1_048_576
MAX_GATEWAY_JSON_DEPTH = 32
MIN_HEARTBEAT_INTERVAL_MS = 100
MAX_HEARTBEAT_INTERVAL_MS = 300_000
MIN_RECONNECT_DELAY_SECONDS = 0.25
MAX_RECONNECT_DELAY_SECONDS = 30.0
MAX_PROVIDER_WAIT_SECONDS = 7 * 24 * 60 * 60
STARTUP_ROLLBACK_TIMEOUT_SECONDS = 10.0

_SAFE_GATEWAY_HOST = "gateway.discord.gg"
_TERMINAL_CLOSE_CODES = frozenset({4004, 4010, 4011, 4012, 4013, 4014})
_NON_RESUMABLE_CLOSE_CODES = frozenset({4001, 4002, 4003, 4005, 4007, 4009})
_RESUMABLE_CLOSE_CODES = frozenset({4000, 4008})


class DiscordGatewayError(RuntimeError):
    pass


class DiscordGatewayProtocolError(DiscordGatewayError):
    pass


class _ReconnectError(DiscordGatewayError):
    def __init__(self, *, resume: bool, gap_status: str | None = None) -> None:
        super().__init__("Discord Gateway reconnect required")
        self.resume = resume
        self.gap_status = gap_status


class _DisabledError(DiscordGatewayError):
    pass


class _TerminalConfigurationError(DiscordGatewayError):
    pass


@dataclass(frozen=True)
class GatewayBotResult:
    status: str
    url: str | None = None
    remaining: int | None = None
    reset_after_ms: int | None = None
    max_concurrency: int | None = None
    retry_after: float | None = None


def _json_depth(value: Any, *, depth: int = 0) -> int:
    if depth > MAX_GATEWAY_JSON_DEPTH:
        raise DiscordGatewayProtocolError("Gateway payload nesting is invalid")
    if type(value) is dict:
        for item in value.values():
            _json_depth(item, depth=depth + 1)
    elif type(value) is list:
        for item in value:
            _json_depth(item, depth=depth + 1)
    return depth


def _parse_retry_after(header_value: Any, payload: Any) -> float | None:
    values: list[float] = []
    if type(header_value) is str and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", header_value):
        parsed = float(header_value)
        if math.isfinite(parsed):
            values.append(parsed)
    if type(payload) is dict and type(payload.get("retry_after")) in {int, float}:
        parsed = float(payload["retry_after"])
        if math.isfinite(parsed) and parsed >= 0:
            values.append(parsed)
    return max(values) if values else None


def _validate_gateway_url(value: Any) -> str:
    if type(value) is not str or not 1 <= len(value) <= 512:
        raise ValueError("Gateway URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "wss"
        or parsed.hostname != _SAFE_GATEWAY_HOST
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Gateway URL is invalid")
    return value


def _gateway_close_code(exc: BaseException) -> int | None:
    direct = getattr(exc, "code", None)
    if type(direct) is int:
        return direct
    for attribute in ("rcvd", "sent"):
        frame = getattr(exc, attribute, None)
        code = getattr(frame, "code", None)
        if type(code) is int:
            return code
    return None


def gateway_connect_url(base_url: Any) -> str:
    selected = _validate_gateway_url(base_url)
    parsed = urlsplit(selected)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["v"] = str(GATEWAY_VERSION)
    query["encoding"] = GATEWAY_ENCODING
    query.pop("compress", None)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", urlencode(query), ""))


def extract_gateway_command(content: Any, *, account_ref: Any) -> str | None:
    if type(content) is not str or len(content.encode("utf-8")) > 8_192:
        return None

    def mention_matches(raw_bot_id: str) -> bool:
        return discord_account_ref(raw_bot_id) == account_ref

    return extract_discord_command_text(
        content,
        mention_matches=mention_matches,
    ) or None


def _gateway_bot_result(payload: Any) -> GatewayBotResult:
    if type(payload) is not dict or set(payload) < {"url", "session_start_limit"}:
        return GatewayBotResult("invalid_response")
    limits = payload["session_start_limit"]
    if type(limits) is not dict:
        return GatewayBotResult("invalid_response")
    remaining = limits.get("remaining")
    reset_after = limits.get("reset_after")
    max_concurrency = limits.get("max_concurrency")
    if (
        type(remaining) is not int
        or remaining < 0
        or type(reset_after) is not int
        or reset_after < 0
        or reset_after > MAX_PROVIDER_WAIT_SECONDS * 1000
        or type(max_concurrency) is not int
        or max_concurrency < 1
    ):
        return GatewayBotResult("invalid_response")
    try:
        gateway_url = _validate_gateway_url(payload["url"])
    except ValueError:
        return GatewayBotResult("invalid_response")
    return GatewayBotResult(
        "success",
        gateway_url,
        remaining,
        reset_after,
        max_concurrency,
    )


def get_gateway_bot(token: str) -> GatewayBotResult:
    if type(token) is not str or not token:
        return GatewayBotResult("configuration_failure")
    req = request.Request("https://discord.com/api/v10/gateway/bot", method="GET")
    req.add_header("Authorization", f"Bot {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "VoolDiscordBridge/1.0")
    try:
        with request.urlopen(req, timeout=10) as response:
            raw = response.read(65_537)
            if len(raw) > 65_536:
                return GatewayBotResult("oversized_response")
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return GatewayBotResult("invalid_response")
            return _gateway_bot_result(payload)
    except error.HTTPError as exc:
        if exc.code in {401, 403}:
            return GatewayBotResult("configuration_failure")
        if exc.code != 429:
            return GatewayBotResult("http_failure")
        try:
            raw = exc.read(65_537)
            payload = json.loads(raw.decode("utf-8")) if len(raw) <= 65_536 else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        header = exc.headers.get("Retry-After") if exc.headers is not None else None
        delay = _parse_retry_after(header, payload)
        return GatewayBotResult("rate_limited", retry_after=max(delay or 1.0, 0.05))
    except (error.URLError, OSError):
        return GatewayBotResult("network_failure")


async def _default_connect(url: str, **kwargs: Any) -> Any:
    from websockets.asyncio.client import connect

    return await connect(url, **kwargs)


class DiscordGatewayIngress:
    """Discord conversational ingress.

    A transport thread receives Gateway frames and commits their classification to SQLite.
    A separate execution thread drains durable stages into M5A. A frame received immediately
    before process death may remain uncertain; Resume replay can recover it, otherwise a
    non-resumable gap is recorded. REST history is never used to reconstruct that gap.
    """

    def __init__(
        self,
        *,
        bot_token: str,
        configured_channels: Callable[[], set[str]],
        read_sources_by_channel: Callable[[], dict[str, list[dict[str, Any]]]],
        ensure_agent: Callable[[], Any],
        state_store: DiscordGatewayStateStore | None = None,
        connect_factory: Callable[..., Awaitable[Any]] = _default_connect,
        gateway_bot_fetcher: Callable[[str], GatewayBotResult] = get_gateway_bot,
        process_request: Callable[..., Any] = process_channel_request,
        enabled_reader: Callable[[str], bool] = plugin_enabled,
        generation_reader: Callable[[str], int | None] = plugin_enablement_generation,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if type(bot_token) is not str or not bot_token:
            raise ValueError("Discord bot token is required")
        self._bot_token = bot_token
        self._configured_channels = configured_channels
        self._read_sources_by_channel = read_sources_by_channel
        self._ensure_agent = ensure_agent
        self._store = state_store or DiscordGatewayStateStore()
        self._connect_factory = connect_factory
        self._gateway_bot_fetcher = gateway_bot_fetcher
        self._process_request = process_request
        self._enabled_reader = enabled_reader
        self._generation_reader = generation_reader
        self._random_value = random_value
        self._stop = threading.Event()
        self._stage_wake = threading.Event()
        self._channel_authority_sync_lock = threading.Lock()
        self._transport_thread: threading.Thread | None = None
        self._execution_thread: threading.Thread | None = None
        self._transport_thread_started = False
        self._execution_thread_started = False
        self._lifecycle_lock = threading.Lock()
        self._active_socket: Any = None
        self._active_socket_lock = threading.Lock()
        self._transport_loop: asyncio.AbstractEventLoop | None = None
        self._owner_guard: _DiscordGatewayOwnershipGuard | None = None

    @property
    def state_store(self) -> DiscordGatewayStateStore:
        return self._store

    def start(self) -> None:
        with self._lifecycle_lock:
            transport_alive = (
                self._transport_thread is not None and self._transport_thread.is_alive()
            )
            execution_alive = (
                self._execution_thread is not None and self._execution_thread.is_alive()
            )
            if transport_alive and execution_alive:
                return
            if transport_alive or execution_alive:
                raise DiscordGatewayError(
                    "Discord Gateway cannot restart while one worker remains active"
                )
            self._acquire_gateway_ownership()
            try:
                self._try_synchronize_channel_authority()
                self._stop.clear()
                self._create_worker_threads()
                self._start_worker_threads()
            except Exception:
                self._rollback_failed_start()
                raise

    def _create_worker_threads(self) -> None:
        self._transport_thread = None
        self._execution_thread = None
        self._transport_thread_started = False
        self._execution_thread_started = False
        self._transport_thread = threading.Thread(
            target=self._run_transport_thread,
            name="discord-gateway-transport",
            daemon=True,
        )
        self._execution_thread = threading.Thread(
            target=self._run_execution_thread,
            name="discord-gateway-execution",
            daemon=True,
        )

    def _start_worker_threads(self) -> None:
        execution = self._execution_thread
        transport = self._transport_thread
        if execution is None or transport is None:
            raise DiscordGatewayError("Discord Gateway workers are unavailable")
        try:
            execution.start()
        except Exception:
            self._execution_thread_started = execution.is_alive()
            raise
        self._execution_thread_started = True
        try:
            transport.start()
        except Exception:
            self._transport_thread_started = transport.is_alive()
            raise
        self._transport_thread_started = True

    def _rollback_failed_start(self) -> None:
        self.stop()
        deadline = time.monotonic() + STARTUP_ROLLBACK_TIMEOUT_SECONDS
        self._join_started_workers(deadline)
        self._finalize_stopped_workers()

    def stop(self) -> None:
        self._stop.set()
        self._stage_wake.set()
        with self._active_socket_lock:
            active = self._active_socket
        if active is not None:
            try:
                loop = self._transport_loop
                if loop is not None and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        active.close(code=1000, reason="bridge shutdown"), loop
                    )
            except Exception:
                pass

    def join(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        self._join_started_workers(deadline)
        with self._lifecycle_lock:
            self._finalize_stopped_workers()

    def _join_started_workers(self, deadline: float) -> None:
        workers = (
            (self._transport_thread, self._transport_thread_started),
            (self._execution_thread, self._execution_thread_started),
        )
        for thread, started in workers:
            if thread is not None and started:
                thread.join(max(0.0, deadline - time.monotonic()))

    def _finalize_stopped_workers(self) -> None:
        if self._transport_thread_started and self._transport_thread is not None:
            if not self._transport_thread.is_alive():
                self._transport_thread_started = False
                self._transport_thread = None
        elif not self._transport_thread_started:
            self._transport_thread = None
        if self._execution_thread_started and self._execution_thread is not None:
            if not self._execution_thread.is_alive():
                self._execution_thread_started = False
                self._execution_thread = None
        elif not self._execution_thread_started:
            self._execution_thread = None
        if not self._transport_thread_started and not self._execution_thread_started:
            self._release_gateway_ownership()

    def _acquire_gateway_ownership(self) -> None:
        guard = self._owner_guard
        if guard is not None and guard._authorizes(self._store._ownership_key):
            return
        try:
            self._owner_guard = self._store.acquire_gateway_ownership()
        except DiscordGatewayOwnerBusyError as exc:
            raise DiscordGatewayError("Discord Gateway owner is busy") from exc

    def _release_gateway_ownership(self) -> bool:
        guard = self._owner_guard
        self._owner_guard = None
        return bool(guard is not None and guard.release())

    def _has_gateway_ownership(self) -> bool:
        guard = self._owner_guard
        return bool(guard is not None and guard._authorizes(self._store._ownership_key))

    def _run_transport_thread(self) -> None:
        try:
            asyncio.run(self._transport_main())
        except Exception:
            print("[DiscordGateway] Transport stopped after a safe internal failure.")

    def _run_execution_thread(self) -> None:
        while not self._stop.is_set():
            progressed = self.drain_one_stage()
            if not progressed:
                self._stage_wake.wait(0.25)
                self._stage_wake.clear()

    def _current_authority(self) -> tuple[bool, int] | None:
        generation = self._generation_reader("discord")
        if generation is None or type(generation) is not int or generation < 0:
            return None
        return bool(self._enabled_reader("discord")), generation

    def _reply_source_ref(self, channel_id: str) -> str:
        sources = self._read_sources_by_channel().get(channel_id, [])
        return select_discord_reply_source_ref(sources)

    def _channel_authority_snapshot(self) -> dict[str, str]:
        configured = self._configured_channels()
        if type(configured) is not set or len(configured) > MAX_AUTHORIZED_CHANNELS:
            raise DiscordGatewayStateError("Discord channel authority is invalid")
        authority: dict[str, str] = {}
        for channel_id in configured:
            if type(channel_id) is not str or parse_discord_message_id(channel_id) is None:
                raise DiscordGatewayStateError("Discord channel authority is invalid")
            authority[discord_conversation_ref(channel_id)] = self._reply_source_ref(channel_id)
        return authority

    def synchronize_channel_authority(self) -> int:
        guard = self._owner_guard
        if guard is None or not self._has_gateway_ownership():
            raise DiscordGatewayOwnerBusyError("Discord Gateway ownership is unavailable")
        with self._channel_authority_sync_lock:
            try:
                snapshot = self._channel_authority_snapshot()
                return self._store.synchronize_channel_authority(snapshot, owner=guard)
            except Exception:
                raise DiscordGatewayStateError(
                    "Discord channel authority synchronization is unavailable"
                ) from None

    def _try_synchronize_channel_authority(self) -> bool:
        try:
            self.synchronize_channel_authority()
        except DiscordGatewayStateError:
            return False
        return True

    def classify_dispatch(self, *, sequence: int, event_name: str, data: Any) -> bool:
        if not self._has_gateway_ownership():
            raise DiscordGatewayError("Discord Gateway ownership is unavailable")
        authority = self._current_authority()
        if authority is None:
            raise DiscordGatewayStateError("Discord enablement authority is unavailable")
        enabled, generation = authority
        if not enabled:
            raise _DisabledError
        self._store.reconcile_authority(generation, enabled=True)
        if event_name == "READY":
            if type(data) is not dict:
                raise DiscordGatewayProtocolError("READY is invalid")
            user = data.get("user")
            if type(user) is not dict:
                raise DiscordGatewayProtocolError("READY user is invalid")
            bot_user_id = user.get("id")
            if type(bot_user_id) is not str or parse_discord_message_id(bot_user_id) is None:
                raise DiscordGatewayProtocolError("READY user is invalid")
            try:
                self._store.record_ready(
                    generation=generation,
                    sequence=sequence,
                    account_ref=discord_account_ref(bot_user_id),
                    session_id=data.get("session_id"),
                    resume_gateway_url=data.get("resume_gateway_url"),
                )
            except (ValueError, DiscordGatewayStateError) as exc:
                raise DiscordGatewayProtocolError("READY state is invalid") from exc
            return False
        if event_name != "MESSAGE_CREATE":
            self._store.advance_ignored(generation=generation, sequence=sequence)
            return False
        return self._classify_message_create(generation=generation, sequence=sequence, data=data)

    def _classify_message_create(self, *, generation: int, sequence: int, data: Any) -> bool:
        if type(data) is not dict:
            raise DiscordGatewayProtocolError("MESSAGE_CREATE is invalid")
        message_id = data.get("id")
        channel_id = data.get("channel_id")
        content = data.get("content")
        author = data.get("author")
        if (
            type(message_id) is not str
            or parse_discord_message_id(message_id) is None
            or type(channel_id) is not str
            or parse_discord_message_id(channel_id) is None
            or type(content) is not str
            or type(author) is not dict
            or type(author.get("id")) is not str
            or parse_discord_message_id(author["id"]) is None
            or type(author.get("bot", False)) is not bool
        ):
            raise DiscordGatewayProtocolError("MESSAGE_CREATE is invalid")
        state = self._store.read_state()
        if not state.session_valid or state.account_ref is None:
            raise DiscordGatewayStateError("MESSAGE_CREATE arrived before READY")
        if (
            channel_id not in self._configured_channels()
            or author.get("bot") is True
            or discord_account_ref(author["id"]) == state.account_ref
        ):
            self._store.advance_ignored(generation=generation, sequence=sequence)
            return False
        command = extract_gateway_command(content, account_ref=state.account_ref)
        if command is None:
            self._store.advance_ignored(generation=generation, sequence=sequence)
            return False
        try:
            inserted = self._store.stage_command(
                generation=generation,
                sequence=sequence,
                account_ref=state.account_ref,
                conversation_ref=discord_conversation_ref(channel_id),
                event_ref=discord_message_ref(channel_id, message_id),
                reply_source_ref=self._reply_source_ref(channel_id),
                raw_channel_id=channel_id,
                raw_message_id=message_id,
                raw_author_id=author["id"],
                command_text=command,
            )
        except Exception:
            self._store.invalidate_session(gap_status="staging_failure")
            raise _ReconnectError(resume=False, gap_status="staging_failure")
        self._stage_wake.set()
        return inserted

    def drain_one_stage(self) -> bool:
        guard = self._owner_guard
        if guard is None or not self._has_gateway_ownership():
            return False
        try:
            stage = self._store.pending_stage()
        except DiscordGatewayCorruptStageError as selected:
            return self._store.quarantine_corrupt_pending_stage(selected)
        except (DiscordGatewayStateError, ValueError):
            return False
        except Exception:
            return False
        if stage is None:
            return False
        authority = self._current_authority()
        if authority is None:
            return False
        enabled, generation = authority
        if not self._try_synchronize_channel_authority():
            return False
        try:
            authorization = self._store.authorize_stage_execution(
                stage,
                enabled=enabled,
                generation=generation,
                owner=guard,
            )
        except (DiscordGatewayStateError, sqlite3.OperationalError):
            return False
        if authorization.status in {"invalid", "revoked"}:
            return True
        authorized_stage = authorization.permit
        if (
            authorization.status != "authorized"
            or type(authorized_stage) is not DiscordGatewayAuthorizedStage
            or not authorized_stage.is_authorized()
        ):
            return False
        try:
            agent = self._ensure_agent()
            adapter = DiscordExternalIngressAdapter(
                account_ref=authorized_stage.account_ref,
                raw_channel_id=authorized_stage.raw_channel_id,
                raw_author_id=authorized_stage.raw_author_id,
                raw_message_id=authorized_stage.raw_message_id,
                reply_source_ref=authorized_stage.reply_source_ref,
            )
            channel_request = adapter.build_request(agent, authorized_stage.command_text)
            result = self._process_request(agent, channel_request)
            if bool(result.retryable):
                return False
        except ExternalIngressEventConflictError:
            pass
        except Exception:
            return False
        self._store.terminalize_stage(authorized_stage.event_ref, status="completed")
        return True

    async def _transport_main(self) -> None:
        self._transport_loop = asyncio.get_running_loop()
        reconnect_attempt = 0
        force_identify = False
        while not self._stop.is_set():
            authority = self._current_authority()
            if authority is None:
                await asyncio.sleep(0.25)
                continue
            enabled, generation = authority
            self._store.reconcile_authority(generation, enabled=enabled)
            if not enabled:
                await asyncio.sleep(0.25)
                continue
            resume = None if force_identify else self._safe_resume(generation)
            if resume is None:
                bootstrap = self._fresh_gateway_target()
                if bootstrap is None:
                    await asyncio.sleep(self._backoff(reconnect_attempt))
                    reconnect_attempt += 1
                    continue
                target_url = bootstrap
            else:
                target_url = resume.resume_gateway_url
            try:
                websocket = await self._connect_factory(
                    gateway_connect_url(target_url),
                    max_size=MAX_GATEWAY_FRAME_BYTES,
                    compression=None,
                    ping_interval=None,
                    close_timeout=5,
                )
                with self._active_socket_lock:
                    self._active_socket = websocket
                await self._connection_loop(websocket, generation=generation, resume=resume)
                reconnect_attempt = 0
                force_identify = False
            except _DisabledError:
                self._store.reconcile_authority(generation, enabled=False)
                force_identify = True
            except _TerminalConfigurationError:
                return
            except _ReconnectError as reconnect:
                if reconnect.gap_status is not None:
                    self._store.invalidate_session(gap_status=reconnect.gap_status)
                force_identify = not reconnect.resume
            except Exception as exc:
                code = _gateway_close_code(exc)
                if code in _TERMINAL_CLOSE_CODES:
                    self._store.invalidate_session(gap_status="invalid_state")
                    return
                if code in _NON_RESUMABLE_CLOSE_CODES or code not in _RESUMABLE_CLOSE_CODES:
                    self._store.invalidate_session(gap_status="non_resumable_session")
                    force_identify = True
                else:
                    force_identify = False
            finally:
                with self._active_socket_lock:
                    active = self._active_socket
                    self._active_socket = None
                if active is not None:
                    with contextlib.suppress(Exception):
                        await active.close(code=1000, reason="reconnect")
            if not self._stop.is_set():
                await asyncio.sleep(self._backoff(reconnect_attempt))
                reconnect_attempt += 1

    def _safe_resume(self, generation: int) -> DiscordGatewayResume | None:
        try:
            return self._store.resume_state(generation)
        except DiscordGatewayStateError:
            self._store.invalidate_session(gap_status="invalid_state")
            return None

    def _fresh_gateway_target(self) -> str | None:
        if not self._store.identify_ready():
            return None
        result = self._gateway_bot_fetcher(self._bot_token)
        if result.status == "configuration_failure":
            raise _TerminalConfigurationError
        if result.status == "rate_limited":
            self._store.set_identify_not_before(time.time() + max(result.retry_after or 1.0, 0.05))
            return None
        if result.status != "success" or result.url is None:
            return None
        if result.remaining == 0:
            reset_seconds = (result.reset_after_ms or 0) / 1000.0
            self._store.set_identify_not_before(time.time() + max(reset_seconds, 0.05))
            return None
        return result.url

    def _backoff(self, attempt: int) -> float:
        base = min(MAX_RECONNECT_DELAY_SECONDS, MIN_RECONNECT_DELAY_SECONDS * (2 ** min(attempt, 7)))
        return min(MAX_RECONNECT_DELAY_SECONDS, base + base * 0.25 * self._random_value())

    async def _connection_loop(
        self,
        websocket: Any,
        *,
        generation: int,
        resume: DiscordGatewayResume | None,
    ) -> None:
        hello = await self._receive_envelope(websocket)
        if hello["op"] != OP_HELLO or type(hello["d"]) is not dict:
            raise DiscordGatewayProtocolError("Gateway Hello is missing")
        heartbeat_interval = hello["d"].get("heartbeat_interval")
        if (
            type(heartbeat_interval) not in {int, float}
            or not math.isfinite(float(heartbeat_interval))
            or not MIN_HEARTBEAT_INTERVAL_MS
            <= float(heartbeat_interval)
            <= MAX_HEARTBEAT_INTERVAL_MS
        ):
            raise DiscordGatewayProtocolError("Gateway heartbeat interval is invalid")
        received_sequence = resume.durable_resume_seq if resume is not None else None
        awaiting_ack = False
        if resume is not None:
            await self._send(
                websocket,
                {
                    "op": OP_RESUME,
                    "d": {
                        "token": self._bot_token,
                        "session_id": resume.session_id,
                        "seq": resume.durable_resume_seq,
                    },
                },
            )
        else:
            await self._send(
                websocket,
                {
                    "op": OP_IDENTIFY,
                    "d": {
                        "token": self._bot_token,
                        "intents": DISCORD_M5B_INTENTS,
                        "properties": {
                            "os": os.name,
                            "browser": "vool",
                            "device": "vool",
                        },
                    },
                },
            )
        interval_seconds = float(heartbeat_interval) / 1000.0
        next_heartbeat = time.monotonic() + interval_seconds * self._random_value()
        while not self._stop.is_set():
            authority = self._current_authority()
            if authority is None or not authority[0] or authority[1] != generation:
                raise _DisabledError
            timeout = max(0.0, next_heartbeat - time.monotonic())
            try:
                envelope = await asyncio.wait_for(self._receive_envelope(websocket), timeout=timeout)
            except TimeoutError:
                if awaiting_ack:
                    raise _ReconnectError(resume=True)
                await self._send(websocket, {"op": OP_HEARTBEAT, "d": received_sequence})
                awaiting_ack = True
                next_heartbeat = time.monotonic() + interval_seconds
                continue
            opcode = envelope["op"]
            if opcode == OP_HEARTBEAT_ACK:
                awaiting_ack = False
                continue
            if opcode == OP_HEARTBEAT:
                await self._send(websocket, {"op": OP_HEARTBEAT, "d": received_sequence})
                awaiting_ack = True
                continue
            if opcode == OP_RECONNECT:
                raise _ReconnectError(resume=True)
            if opcode == OP_INVALID_SESSION:
                if type(envelope["d"]) is not bool:
                    raise DiscordGatewayProtocolError("Gateway Invalid Session is invalid")
                if envelope["d"] is True and self._safe_resume(generation) is not None:
                    raise _ReconnectError(resume=True)
                self._store.invalidate_session(gap_status="non_resumable_session")
                raise _ReconnectError(resume=False)
            if opcode != OP_DISPATCH:
                continue
            sequence = envelope["s"]
            event_name = envelope["t"]
            if type(sequence) is not int or sequence < 0 or type(event_name) is not str:
                raise DiscordGatewayProtocolError("Gateway Dispatch is invalid")
            received_sequence = sequence
            self.classify_dispatch(sequence=sequence, event_name=event_name, data=envelope["d"])

    async def _receive_envelope(self, websocket: Any) -> dict[str, Any]:
        raw = await websocket.recv()
        if type(raw) is not str or len(raw.encode("utf-8")) > MAX_GATEWAY_FRAME_BYTES:
            raise DiscordGatewayProtocolError("Gateway frame is invalid")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DiscordGatewayProtocolError("Gateway frame is invalid") from exc
        _json_depth(payload)
        if type(payload) is not dict or not set(payload).issubset({"op", "d", "s", "t"}):
            raise DiscordGatewayProtocolError("Gateway envelope is invalid")
        if type(payload.get("op")) is not int:
            raise DiscordGatewayProtocolError("Gateway opcode is invalid")
        payload.setdefault("d", None)
        payload.setdefault("s", None)
        payload.setdefault("t", None)
        return payload

    @staticmethod
    async def _send(websocket: Any, payload: dict[str, Any]) -> None:
        await websocket.send(json.dumps(payload, separators=(",", ":"), sort_keys=True))


__all__ = [
    "DIRECT_MESSAGES",
    "DISCORD_M5B_INTENTS",
    "GUILD_MESSAGES",
    "MAX_GATEWAY_FRAME_BYTES",
    "MESSAGE_CONTENT",
    "DiscordGatewayError",
    "DiscordGatewayIngress",
    "DiscordGatewayProtocolError",
    "GatewayBotResult",
    "extract_gateway_command",
    "gateway_connect_url",
    "get_gateway_bot",
]
