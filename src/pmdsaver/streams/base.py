"""Shared websocket reconnect helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ReconnectingWebSocket:
    def __init__(
        self,
        name: str,
        url: str,
        on_message: MessageHandler,
        *,
        ping_interval_s: float | None = None,
        ping_payload: str | None = None,
        subscribe: Callable[[Any], Awaitable[None]] | None = None,
        before_connect: Callable[[], Awaitable[None]] | None = None,
        idle_timeout_s: float | None = None,
        min_backoff_s: float = 1.0,
        max_backoff_s: float = 30.0,
    ) -> None:
        self.name = name
        self.url = url
        self.on_message = on_message
        self.ping_interval_s = ping_interval_s
        self.ping_payload = ping_payload
        self.subscribe = subscribe
        self.before_connect = before_connect
        self.idle_timeout_s = idle_timeout_s
        self.min_backoff_s = min_backoff_s
        self.max_backoff_s = max_backoff_s
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_data_mono: float = 0.0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_loop(), name=f"ws-{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    def mark_data(self) -> None:
        self._last_data_mono = time.monotonic()

    async def _run_loop(self) -> None:
        backoff = self.min_backoff_s
        while not self._stop.is_set():
            try:
                if self.before_connect is not None:
                    await self.before_connect()
                    if self._stop.is_set():
                        break
                async with websockets.connect(
                    self.url,
                    ping_interval=None,
                    max_size=8 * 1024 * 1024,
                ) as ws:
                    logger.info("%s connected", self.name)
                    backoff = self.min_backoff_s
                    self.mark_data()
                    if self.subscribe is not None:
                        await self.subscribe(ws)
                    extras: list[asyncio.Task[None]] = []
                    if self.ping_interval_s and self.ping_payload is not None:
                        extras.append(
                            asyncio.create_task(
                                self._heartbeat(ws),
                                name=f"ws-{self.name}-ping",
                            )
                        )
                    if self.idle_timeout_s:
                        extras.append(
                            asyncio.create_task(
                                self._idle_watch(ws),
                                name=f"ws-{self.name}-idle",
                            )
                        )
                    try:
                        async for raw in ws:
                            if self._stop.is_set():
                                break
                            await self._handle_raw(raw)
                    finally:
                        for task in extras:
                            task.cancel()
                        if extras:
                            await asyncio.gather(*extras, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    break
                logger.warning("%s disconnected: %s", self.name, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff_s)

    async def _heartbeat(self, ws: Any) -> None:
        # Official market channel: send text "PING" every 10s. First ping
        # immediately so the server does not drop us at the ~10s timeout.
        while not self._stop.is_set():
            await ws.send(self.ping_payload)
            await asyncio.sleep(self.ping_interval_s)

    async def _idle_watch(self, ws: Any) -> None:
        timeout = self.idle_timeout_s
        if timeout is None:
            return
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            idle_s = time.monotonic() - self._last_data_mono
            if idle_s >= timeout:
                logger.warning(
                    "%s silent for %.0fs after subscribe; reconnecting",
                    self.name,
                    idle_s,
                )
                await ws.close()
                return

    async def _handle_raw(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if raw in {"PONG", "pong"}:
            return
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("%s non-json frame: %s", self.name, raw[:120])
            return
        if isinstance(message, list):
            if not message:
                # Documented silent-freeze: server ACKs subscribe with [] then
                # never sends book data. Do not treat this as activity.
                logger.warning("%s empty subscribe ack []; waiting for book data", self.name)
                return
            self.mark_data()
            for item in message:
                if isinstance(item, dict):
                    await self.on_message(item)
            return
        if isinstance(message, dict):
            self.mark_data()
            await self.on_message(message)
