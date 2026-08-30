"""Binance spot and USDT-M futures price streams (no candle volume)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from pmtrader.streams.base import ReconnectingWebSocket

logger = logging.getLogger(__name__)

BINANCE_SPOT_URL = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
BINANCE_FUTURES_URL = "wss://fstream.binance.com/ws/btcusdt@aggTrade"
BINANCE_FUTURES_REST = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

PriceTickCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def fetch_spot_open_at(open_time_s: int) -> str | None:
    """1m candle open at the 5m window start — late-join PTB fallback."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                BINANCE_KLINES,
                params={
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "startTime": open_time_s * 1000,
                    "limit": 1,
                },
            )
            response.raise_for_status()
            rows = response.json()
            if isinstance(rows, list) and rows:
                return str(rows[0][1])
    except Exception:
        logger.exception("Binance 1m open fetch failed for %s", open_time_s)
    return None


class BinanceSpotStream:
    def __init__(self, on_price_tick: PriceTickCallback) -> None:
        self.on_price_tick = on_price_tick
        self.latest_price: str | None = None
        self._ws: ReconnectingWebSocket | None = None

    def start(self) -> None:
        if self._ws is not None:
            return
        self._ws = ReconnectingWebSocket(
            name="binance-spot",
            url=BINANCE_SPOT_URL,
            on_message=self._handle_message,
        )
        self._ws.start()

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None

    async def _handle_message(self, message: dict[str, Any]) -> None:
        data = message.get("data") or message
        if data.get("e") != "aggTrade":
            return
        price = str(data["p"])
        self.latest_price = price
        await self.on_price_tick(
            {
                "recv_ts_ms": int(time.time() * 1000),
                "exchange_ts_ms": int(data.get("T") or data.get("E") or 0),
                "source": "binance_spot",
                "price": price,
                "size": str(data["q"]),
            }
        )


class BinanceFuturesStream:
    def __init__(self, on_price_tick: PriceTickCallback) -> None:
        self.on_price_tick = on_price_tick
        self.latest_price: str | None = None
        self._ws: ReconnectingWebSocket | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._http = httpx.AsyncClient(timeout=10.0)
        self._last_ws_ms: int | None = None

    def start(self) -> None:
        if self._ws is not None:
            return
        self._stop.clear()
        self._ws = ReconnectingWebSocket(
            name="binance-futures",
            url=BINANCE_FUTURES_URL,
            on_message=self._handle_message,
        )
        self._ws.start()
        self._poll_task = asyncio.create_task(
            self._rest_fallback_loop(),
            name="binance-futures-rest",
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None
        await self._http.aclose()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        data = message.get("data") or message
        if data.get("e") != "aggTrade":
            return
        self._last_ws_ms = int(time.time() * 1000)
        await self._emit_price(str(data["p"]), str(data["q"]), int(data.get("T") or data.get("E") or 0))

    async def _rest_fallback_loop(self) -> None:
        warned = False
        while not self._stop.is_set():
            now_ms = int(time.time() * 1000)
            ws_recent = self._last_ws_ms is not None and now_ms - self._last_ws_ms < 15_000
            if not ws_recent:
                try:
                    response = await self._http.get(
                        BINANCE_FUTURES_REST,
                        params={"symbol": "BTCUSDT"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    await self._emit_price(
                        str(payload["price"]),
                        None,
                        int(payload.get("time") or now_ms),
                    )
                    if not warned:
                        logger.warning("Binance futures websocket unavailable; using REST fallback")
                        warned = True
                except Exception:
                    logger.exception("Binance futures REST fallback failed")
            await asyncio.sleep(1)

    async def _emit_price(self, price: str, size: str | None, exchange_ts_ms: int) -> None:
        self.latest_price = price
        await self.on_price_tick(
            {
                "recv_ts_ms": int(time.time() * 1000),
                "exchange_ts_ms": exchange_ts_ms,
                "source": "binance_futures",
                "price": price,
                "size": size,
            }
        )
